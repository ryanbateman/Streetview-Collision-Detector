"""Async client for the Street View Static API metadata endpoint.

Metadata requests are free but rate limited. Transient failures (HTTP 429/5xx,
OVER_QUERY_LIMIT, UNKNOWN_ERROR, network errors) are retried with exponential
backoff; credential or request errors (REQUEST_DENIED, INVALID_REQUEST) raise
StreetViewError at once so a bad key cannot burn through thousands of requests.
"""
import asyncio
import logging as log
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import aiohttp
from aiohttp_client_cache import CachedSession, SQLiteBackend
from tqdm import tqdm

from svcd.models import PanoLookup, SamplePoint

META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
PANO_URL = "https://www.google.com/maps/@"
REQUEST_TIMEOUT_S = 30

NO_PANO_STATUSES = {"ZERO_RESULTS", "NOT_FOUND"}
RETRY_STATUSES = {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR"}
FATAL_STATUSES = {"REQUEST_DENIED", "INVALID_REQUEST"}
CACHEABLE_STATUSES = {"OK"} | NO_PANO_STATUSES


class StreetViewError(Exception):
    """The API rejected the request itself (bad key, bad parameters); retrying will not help."""


class _RetryableError(Exception):
    pass


def buildPanoUrl(panoId: str, lat: float, lng: float) -> str:
    """Google Maps URL that opens the given panorama."""
    params = {"api": "1", "map_action": "pano", "pano": panoId, "viewpoint": f"{lat},{lng}"}
    return f"{PANO_URL}?{urlencode(params, safe=',')}"


async def _isCacheable(response: aiohttp.ClientResponse) -> bool:
    """Cache only definitive answers; quota and auth errors arrive as HTTP 200 too."""
    try:
        payload = await response.json(content_type=None)
    except (ValueError, aiohttp.ClientError):
        return False
    return isinstance(payload, dict) and payload.get("status") in CACHEABLE_STATUSES


def openCachedSession(cachePath: Path | str, expireAfterSeconds: int = 180 * 24 * 3600) -> CachedSession:
    """Session backed by a SQLite response cache. The key param is excluded from cache keys
    so entries stay valid across API key rotation. Only OK / ZERO_RESULTS / NOT_FOUND
    responses are cached."""
    Path(cachePath).parent.mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(
        str(cachePath),
        expire_after=expireAfterSeconds,
        ignored_params=["key"],
        filter_fn=_isCacheable,
    )
    return CachedSession(cache=backend)


class StreetViewClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        apiKey: str,
        concurrency: int = 20,
        radiusM: int = 25,
        source: str = "outdoor",
        maxTries: int = 5,
        baseDelay: float = 0.5,
    ):
        self.session = session
        self._apiKey = apiKey
        self.concurrency = concurrency
        self.radiusM = radiusM
        self.source = source
        self.maxTries = maxTries
        self.baseDelay = baseDelay

    def __repr__(self) -> str:
        return f"StreetViewClient(concurrency={self.concurrency}, radiusM={self.radiusM}, source={self.source!r})"

    async def _fetch(self, point: SamplePoint) -> dict[str, Any]:
        """One request. Returns the JSON payload, raises _RetryableError or StreetViewError."""
        params = {
            "location": f"{point.lat},{point.lng}",
            "radius": str(self.radiusM),
            "source": self.source,
            "key": self._apiKey,
        }
        try:
            async with self.session.get(
                META_URL, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
            ) as response:
                httpStatus = response.status
                try:
                    payload = await response.json(content_type=None)
                except ValueError:
                    payload = None
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            # str(error) can include the request URL (and so the key); log the type only
            raise _RetryableError(type(error).__name__) from None

        if httpStatus == 429 or httpStatus >= 500:
            raise _RetryableError(f"HTTP {httpStatus}")
        if not isinstance(payload, dict):
            if httpStatus != 200:
                raise StreetViewError(f"HTTP {httpStatus} with no JSON body")
            raise _RetryableError("response body is not a JSON object")

        status = payload.get("status")
        if status in FATAL_STATUSES:
            raise StreetViewError(f"{status}: {payload.get('error_message', 'no error_message')}")
        if status in RETRY_STATUSES:
            raise _RetryableError(str(status))
        if httpStatus != 200:
            raise StreetViewError(f"HTTP {httpStatus}, status {status}")
        return payload

    async def lookup(self, point: SamplePoint) -> PanoLookup:
        """Query the metadata endpoint for one point, retrying transient failures."""
        reason = ""
        for attempt in range(self.maxTries):
            try:
                payload = await self._fetch(point)
            except _RetryableError as error:
                reason = str(error)
                if attempt + 1 < self.maxTries:
                    delay = self.baseDelay * 2 ** attempt
                    log.warning(f"Street View lookup failed ({reason}); retry {attempt + 1}/{self.maxTries - 1} in {delay:g}s")
                    log.debug(f"Failed point: {point.lat},{point.lng}")
                    await asyncio.sleep(delay)
                continue
            return self._toLookup(point, payload)

        log.warning(f"Street View lookup gave up after {self.maxTries} tries ({reason}); it will be retried on the next run")
        log.debug(f"Gave up on point: {point.lat},{point.lng}")
        return self._emptyLookup(point, "ERROR")

    def _toLookup(self, point: SamplePoint, payload: dict[str, Any]) -> PanoLookup:
        status = payload.get("status")
        log.debug(f"Street View {point.lat},{point.lng} -> {status} {payload.get('date')}")
        if status != "OK":
            if status not in NO_PANO_STATUSES:
                log.warning(f"Unexpected Street View status {status!r}")
            return self._emptyLookup(point, str(status))
        location = payload.get("location") or {}
        return PanoLookup(
            placeKey=point.placeKey,
            queryLat=point.lat,
            queryLng=point.lng,
            status="OK",
            panoId=payload.get("pano_id"),
            date=payload.get("date"),
            panoLat=location.get("lat"),
            panoLng=location.get("lng"),
            copyright=payload.get("copyright"),
        )

    @staticmethod
    def _emptyLookup(point: SamplePoint, status: str) -> PanoLookup:
        return PanoLookup(point.placeKey, point.lat, point.lng, status, None, None, None, None)

    async def lookupAll(
        self,
        points: Iterable[SamplePoint],
        onResult: Callable[[PanoLookup], None] | None = None,
        showProgress: bool = True,
    ) -> list[PanoLookup]:
        """Look up every point with at most `concurrency` in flight. Results keep input order;
        onResult is called as each one completes. A StreetViewError cancels the rest and propagates."""
        points = list(points)
        results: list[PanoLookup | None] = [None] * len(points)
        semaphore = asyncio.Semaphore(self.concurrency)
        progress = tqdm(total=len(points), desc="Street View metadata", unit="req", disable=not showProgress)

        aborted = False

        async def run(index: int, point: SamplePoint) -> None:
            nonlocal aborted
            async with semaphore:
                if aborted:
                    return
                try:
                    result = await self.lookup(point)
                except StreetViewError:
                    aborted = True
                    raise
            results[index] = result
            if onResult is not None:
                onResult(result)
            progress.update(1)

        tasks = [asyncio.create_task(run(i, p)) for i, p in enumerate(points)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            progress.close()
        return [r for r in results if r is not None]
