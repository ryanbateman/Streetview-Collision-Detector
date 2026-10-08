import asyncio
import json
import re
from pathlib import Path

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses

from svcd.models import SOURCE_GOOGLE, SOURCE_USER, PanoLookup, SamplePoint, panoSource
from svcd.streetview import META_URL, StreetViewClient, StreetViewError, buildPanoUrl, openCachedSession

URL_PATTERN = re.compile(r"^" + re.escape(META_URL) + r"(\?.*)?$")
POINT = SamplePoint("ChIJtest", 52.5163, 13.3777, 0)


def loadFixture(fixturesDir: Path, name: str) -> dict:
    return json.loads((fixturesDir / f"metadata_{name}.json").read_text(encoding="utf-8"))


def requestCount(mocked: aioresponses) -> int:
    return sum(len(calls) for calls in mocked.requests.values())


async def runLookup(point: SamplePoint = POINT, **clientKwargs) -> PanoLookup:
    async with aiohttp.ClientSession() as session:
        client = StreetViewClient(session, "test-key", baseDelay=0, **clientKwargs)
        return await client.lookup(point)


async def test_ok_response_parsed(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "ok"))
        result = await runLookup()
    assert result == PanoLookup(
        placeKey="ChIJtest", queryLat=52.5163, queryLng=13.3777, status="OK",
        panoId="TESTPANO_ok-123", date="2022-08", panoLat=52.51628, panoLng=13.37771,
        copyright="\u00a9 Google",
    )
    assert panoSource(result.copyright) == SOURCE_GOOGLE


async def test_user_photosphere_keeps_contributor_credit(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "user"))
        result = await runLookup()
    assert result.status == "OK"
    assert result.panoId == "TESTPANO_user-456"
    assert result.copyright == "\u00a9 Jane Contributor"
    assert "google" not in result.copyright.casefold()
    assert panoSource(result.copyright) == SOURCE_USER


async def test_missing_copyright_is_none(fixturesDir):
    payload = loadFixture(fixturesDir, "ok")
    del payload["copyright"]
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=payload)
        result = await runLookup()
    assert result.status == "OK"
    assert result.copyright is None


async def test_ok_without_date(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "nodate"))
        result = await runLookup()
    assert result.status == "OK"
    assert result.panoId == "TESTPANO_nodate"
    assert result.date is None
    assert (result.panoLat, result.panoLng) == (52.5163, 13.3777)


async def test_zero_results(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "zero"))
        result = await runLookup()
    assert result == PanoLookup("ChIJtest", 52.5163, 13.3777, "ZERO_RESULTS", None, None, None, None)


async def test_over_query_limit_retried_then_ok(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "overlimit"))
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "overlimit"))
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "ok"))
        result = await runLookup()
        assert requestCount(mocked) == 3
    assert result.status == "OK"
    assert result.date == "2022-08"


async def test_http_500_retried_then_ok(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, status=500, body="Internal error")
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "ok"))
        result = await runLookup()
        assert requestCount(mocked) == 2
    assert result.status == "OK"


async def test_client_error_retried_then_ok(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, exception=aiohttp.ClientConnectionError("boom"))
        mocked.get(URL_PATTERN, exception=asyncio.TimeoutError())
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "ok"))
        result = await runLookup()
    assert result.status == "OK"


async def test_five_failures_give_error(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, status=503, repeat=True)
        result = await runLookup()
        assert requestCount(mocked) == 5
    assert result == PanoLookup("ChIJtest", 52.5163, 13.3777, "ERROR", None, None, None, None)


async def test_backoff_delays(fixturesDir, monkeypatch):
    delays = []

    async def fakeSleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("svcd.streetview.asyncio.sleep", fakeSleep)
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, status=429, repeat=True)
        async with aiohttp.ClientSession() as session:
            result = await StreetViewClient(session, "test-key").lookup(POINT)
    assert result.status == "ERROR"
    assert delays == [0.5, 1, 2, 4]


async def test_request_denied_raises_immediately(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "denied"), repeat=True)
        with pytest.raises(StreetViewError, match="The provided API key is invalid."):
            await runLookup()
        assert requestCount(mocked) == 1


async def test_request_params(fixturesDir):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "zero"))
        await runLookup(radiusM=50, source="default")
        (method, url), = mocked.requests.keys()
    assert method == "GET"
    assert str(url.with_query(None)) == META_URL
    assert url.query["location"] == "52.5163,13.3777"
    assert url.query["radius"] == "50"
    assert url.query["source"] == "default"
    assert url.query["key"] == "test-key"


async def test_lookupAll_order_callbacks_and_concurrency():
    points = [SamplePoint(f"p{i}", 50 + i / 1000, 10.0, 0) for i in range(30)]
    inFlight = 0
    maxInFlight = 0

    async def respond(url, **kwargs):
        nonlocal inFlight, maxInFlight
        inFlight += 1
        maxInFlight = max(maxInFlight, inFlight)
        lat = float(url.query["location"].split(",")[0])
        # Later points answer faster so completion order differs from input order.
        await asyncio.sleep(0.002 * (60 - round((lat - 50) * 1000)) / 10)
        inFlight -= 1
        return CallbackResult(payload={"status": "OK", "pano_id": f"pano-{lat}", "date": "2020-01",
                                       "location": {"lat": lat, "lng": 10.0}})

    seen: list[PanoLookup] = []
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, callback=respond, repeat=True)
        async with aiohttp.ClientSession() as session:
            client = StreetViewClient(session, "test-key", concurrency=4, baseDelay=0)
            results = await client.lookupAll(points, onResult=seen.append, showProgress=False)

    assert [r.placeKey for r in results] == [p.placeKey for p in points]
    assert [r.queryLat for r in results] == [p.lat for p in points]
    assert all(r.status == "OK" for r in results)
    assert len(seen) == 30 and set(seen) == set(results)
    assert [r.placeKey for r in seen] != [p.placeKey for p in points]
    assert 1 < maxInFlight <= 4


async def test_lookupAll_aborts_on_denied(fixturesDir):
    points = [SamplePoint(f"p{i}", 50 + i / 1000, 10.0, 0) for i in range(50)]
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "denied"), repeat=True)
        async with aiohttp.ClientSession() as session:
            client = StreetViewClient(session, "test-key", concurrency=2, baseDelay=0)
            with pytest.raises(StreetViewError):
                await client.lookupAll(points, showProgress=False)
        assert requestCount(mocked) <= 2


async def test_lookupAll_empty():
    async with aiohttp.ClientSession() as session:
        client = StreetViewClient(session, "test-key")
        assert await client.lookupAll([], showProgress=False) == []


def test_buildPanoUrl():
    assert buildPanoUrl("AbC-123_x", 52.5163, 13.3777) == (
        "https://www.google.com/maps/@?api=1&map_action=pano&pano=AbC-123_x&viewpoint=52.5163,13.3777"
    )


async def test_cached_session_skips_transient_errors_and_ignores_key(fixturesDir, tmp_path):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "overlimit"))
        mocked.get(URL_PATTERN, payload=loadFixture(fixturesDir, "ok"))
        async with openCachedSession(tmp_path / "cache" / "meta") as session:
            first = await StreetViewClient(session, "key-one", baseDelay=0).lookup(POINT)
            second = await StreetViewClient(session, "key-two", baseDelay=0).lookup(POINT)
        assert requestCount(mocked) == 2
    assert first.status == "OK"
    assert second == first
