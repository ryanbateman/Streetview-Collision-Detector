import re

import pytest
from aioresponses import aioresponses

from svcd import cli
from svcd.models import PanoLookup, SamplePoint
from svcd.storage import readJsonl
from svcd.streetview import META_URL

META_PATTERN = re.compile(re.escape(META_URL) + r".*")


def okResponse(date: str = "2022-08") -> dict:
    return {
        "status": "OK", "pano_id": "FIXTUREPANO", "date": date,
        "location": {"lat": 52.5163, "lng": 13.3777}, "copyright": "test",
    }


def runCli(tmp_path, *stageArgs: str) -> int:
    return cli.main([
        "--data-dir", str(tmp_path / "data"), "--output-dir", str(tmp_path / "out"),
        "--cache-dir", str(tmp_path / "cache"), "-q", *stageArgs,
    ])


def test_run_all_end_to_end_on_fixtures(tmp_path, fixturesDir, monkeypatch):
    monkeypatch.setenv(cli.API_KEY_ENV, "test-key")
    with aioresponses() as mocked:
        mocked.get(META_PATTERN, payload=okResponse(), repeat=True)
        assert runCli(tmp_path, "run-all", "--input", str(fixturesDir / "semantic_fixture"),
                      "--no-cache", "--ring-points", "2") == 0

    data = tmp_path / "data"
    assert (data / "visits.jsonl").exists() and (data / "places.jsonl").exists()
    lookups = readJsonl(data / "panos.jsonl", PanoLookup)
    assert lookups and all(lookup.status == "OK" for lookup in lookups)
    collisions = (tmp_path / "out" / "collisions.csv").read_text(encoding="utf-8").splitlines()
    assert len(collisions) >= 2, "expected a header plus at least one collision for 2022-08"
    mapHtml = (tmp_path / "out" / "map.html").read_text(encoding="utf-8")
    assert "map_action=pano&pano=FIXTUREPANO" in mapHtml
    assert "FIXTUREPANO" in (tmp_path / "out" / "collisions.html").read_text(encoding="utf-8")


def test_lookup_resumes_and_skips_done_points(tmp_path, fixturesDir, monkeypatch):
    monkeypatch.setenv(cli.API_KEY_ENV, "test-key")
    runCli(tmp_path, "ingest", "--input", str(fixturesDir / "semantic_fixture"))
    with aioresponses() as mocked:
        mocked.get(META_PATTERN, payload=okResponse(), repeat=True)
        runCli(tmp_path, "lookup", "--no-cache", "--ring-points", "1", "--limit", "1")
        firstCount = len(readJsonl(tmp_path / "data" / "panos.jsonl", PanoLookup))
        assert firstCount == 1
        runCli(tmp_path, "lookup", "--no-cache", "--ring-points", "1")
        total = len(readJsonl(tmp_path / "data" / "panos.jsonl", PanoLookup))
        points = len(readJsonl(tmp_path / "data" / "samplePoints.jsonl", SamplePoint))
        assert total == points
        # A third run has nothing left to do and must not call the API again.
        callsBefore = sum(len(v) for v in mocked.requests.values())
        runCli(tmp_path, "lookup", "--no-cache", "--ring-points", "1")
        assert sum(len(v) for v in mocked.requests.values()) == callsBefore


def test_lookup_without_key_fails_fast(tmp_path, fixturesDir, monkeypatch):
    monkeypatch.delenv(cli.API_KEY_ENV, raising=False)
    runCli(tmp_path, "ingest", "--input", str(fixturesDir / "semantic_fixture"))
    with pytest.raises(SystemExit, match=cli.API_KEY_ENV):
        runCli(tmp_path, "lookup", "--no-cache")


def test_lookup_denied_key_aborts_with_message(tmp_path, fixturesDir, monkeypatch):
    monkeypatch.setenv(cli.API_KEY_ENV, "bad-key")
    runCli(tmp_path, "ingest", "--input", str(fixturesDir / "semantic_fixture"))
    with aioresponses() as mocked:
        mocked.get(META_PATTERN, payload={"status": "REQUEST_DENIED", "error_message": "The provided API key is invalid."}, repeat=True)
        with pytest.raises(SystemExit, match="refused"):
            runCli(tmp_path, "lookup", "--no-cache", "--ring-points", "0")


def test_pending_sample_points_matches_on_place_and_coordinates():
    points = [SamplePoint("a", 1.0, 2.0, 0), SamplePoint("a", 1.0001, 2.0, 1), SamplePoint("b", 1.0, 2.0, 0)]
    done = [PanoLookup("a", 1.0, 2.0, "OK", "p", "2020-01", 1.0, 2.0)]
    assert cli.pendingSamplePoints(points, done) == points[1:]


def test_pending_sample_points_retries_error_rows():
    points = [SamplePoint("a", 1.0, 2.0, 0), SamplePoint("a", 1.0001, 2.0, 1), SamplePoint("a", 1.0002, 2.0, 2)]
    done = [
        PanoLookup("a", 1.0, 2.0, "ERROR", None, None, None, None),
        PanoLookup("a", 1.0001, 2.0, "ZERO_RESULTS", None, None, None, None),
        PanoLookup("a", 1.0002, 2.0, "OK", "p", "2020-01", 1.0002, 2.0),
    ]
    assert cli.pendingSamplePoints(points, done) == [points[0]]


def test_verbose_mode_silences_cache_library_debug_logging(tmp_path, fixturesDir):
    import logging
    runCli(tmp_path, "-v", "ingest", "--input", str(fixturesDir / "semantic_fixture"))
    assert logging.getLogger("aiohttp_client_cache").level == logging.INFO
