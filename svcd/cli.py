"""Command line entry point: python -m svcd.cli <stage> [options].

Stages read and write files under --data-dir so each can be rerun on its own:
  ingest  input export            -> data/visits.jsonl
  lookup  visits                  -> data/places.jsonl, data/samplePoints.jsonl, data/panos.jsonl (append, resumable)
  match   visits + places + panos -> data/collisions.jsonl
  map     collisions              -> output/collisions.csv, output/collisions.html, output/map.html
  run-all all four in sequence
"""
import argparse
import asyncio
import logging as log
import os
import sys
from pathlib import Path
from typing import Sequence

import aiohttp

from svcd.ingest import ingestVisits
from svcd.match import findCollisions, rankCollisions
from svcd.models import Collision, PanoLookup, Place, SamplePoint, Visit
from svcd.places import buildPlaces, buildSamplePoints
from svcd.report import buildMap, writeTable
from svcd.storage import appendJsonl, readJsonl, writeJsonl
from svcd.streetview import StreetViewClient, StreetViewError, openCachedSession

API_KEY_ENV = "GMAPS_STATIC_API_KEY"


class StagePaths:
    def __init__(self, dataDir: Path, outputDir: Path, cacheDir: Path):
        self.visits = dataDir / "visits.jsonl"
        self.places = dataDir / "places.jsonl"
        self.samplePoints = dataDir / "samplePoints.jsonl"
        self.panos = dataDir / "panos.jsonl"
        self.collisions = dataDir / "collisions.jsonl"
        self.collisionsCsv = outputDir / "collisions.csv"
        self.collisionsHtml = outputDir / "collisions.html"
        self.map = outputDir / "map.html"
        self.cache = cacheDir / "streetview"


class BufferedAppender:
    """Collects lookups and appends them to a JSONL file every flushEvery results."""

    def __init__(self, path: Path, flushEvery: int = 100):
        self.path = path
        self.flushEvery = flushEvery
        self.buffer: list[PanoLookup] = []
        self.written = 0

    def __call__(self, lookup: PanoLookup) -> None:
        self.buffer.append(lookup)
        if len(self.buffer) >= self.flushEvery:
            self.flush()

    def flush(self) -> None:
        if self.buffer:
            self.written += appendJsonl(self.path, self.buffer)
            self.buffer.clear()


def runIngest(inputPath: Path, paths: StagePaths) -> int:
    visits = ingestVisits(inputPath)
    writeJsonl(paths.visits, visits)
    if visits:
        log.info(f"Ingested {len(visits)} visits from {visits[0].start.date()} to {visits[-1].end.date()}")
    else:
        log.warning("Ingested 0 visits")
    return len(visits)


DONE_STATUSES = {"OK", "ZERO_RESULTS", "NOT_FOUND"}


def pendingSamplePoints(points: list[SamplePoint], done: list[PanoLookup]) -> list[SamplePoint]:
    """Points without a definitive lookup yet. ERROR rows (quota, network) are retried on the next run."""
    doneKeys = {(lookup.placeKey, lookup.queryLat, lookup.queryLng)
                for lookup in done if lookup.status in DONE_STATUSES}
    return [point for point in points if (point.placeKey, point.lat, point.lng) not in doneKeys]


async def runLookupAsync(paths: StagePaths, apiKey: str, ringPoints: int, ringRadius: float,
                         concurrency: int, limit: int | None, useCache: bool, showProgress: bool) -> int:
    visits = readJsonl(paths.visits, Visit)
    if not visits:
        raise SystemExit(f"No visits in {paths.visits}; run ingest first")
    places = buildPlaces(visits)
    writeJsonl(paths.places, places)
    points = buildSamplePoints(places, ringCount=ringPoints, ringRadiusM=ringRadius)
    writeJsonl(paths.samplePoints, points)
    pending = pendingSamplePoints(points, readJsonl(paths.panos, PanoLookup))
    log.info(f"{len(places)} places, {len(points)} sample points, {len(pending)} still to look up")
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return 0

    appender = BufferedAppender(paths.panos)
    if useCache:
        session = openCachedSession(paths.cache)
    else:
        session = aiohttp.ClientSession()
    async with session:
        client = StreetViewClient(session, apiKey, concurrency=concurrency)
        try:
            await client.lookupAll(pending, onResult=appender, showProgress=showProgress)
        finally:
            appender.flush()
    log.info(f"Stored {appender.written} lookups in {paths.panos}")
    return appender.written


def runLookup(paths: StagePaths, args: argparse.Namespace) -> int:
    apiKey = os.environ.get(API_KEY_ENV)
    if not apiKey:
        raise SystemExit(f"Set the {API_KEY_ENV} environment variable to a Google Maps Static API key")
    try:
        return asyncio.run(runLookupAsync(
            paths, apiKey, args.ring_points, args.ring_radius, args.concurrency,
            args.limit, not args.no_cache, not args.quiet))
    except StreetViewError as error:
        raise SystemExit(f"Street View API refused the request: {error}") from error


def runMatch(paths: StagePaths) -> int:
    visits = readJsonl(paths.visits, Visit)
    places = readJsonl(paths.places, Place)
    lookups = readJsonl(paths.panos, PanoLookup)
    if not lookups:
        raise SystemExit(f"No lookups in {paths.panos}; run lookup first")
    collisions = rankCollisions(findCollisions(visits, places, lookups))
    writeJsonl(paths.collisions, collisions)
    log.info(f"Found {len(collisions)} collisions across {len({c.placeKey for c in collisions})} places")
    return len(collisions)


def runMap(paths: StagePaths) -> int:
    collisions = readJsonl(paths.collisions, Collision)
    writeTable(collisions, paths.collisionsCsv, paths.collisionsHtml)
    buildMap(collisions, paths.map)
    log.info(f"Wrote {paths.collisionsCsv}, {paths.collisionsHtml} and {paths.map}")
    return len(collisions)


def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="svcd", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress bar")
    stages = parser.add_subparsers(dest="stage", required=True)

    def addInput(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--input", type=Path, required=True,
                         help="takeout folder (old format) or Timeline.json / location-history.json (phone export)")

    def addLookupOptions(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--ring-points", type=int, default=6, help="offset points around each place (default 6)")
        sub.add_argument("--ring-radius", type=float, default=40.0, help="ring radius in metres (default 40)")
        sub.add_argument("--concurrency", type=int, default=20, help="parallel requests (default 20)")
        sub.add_argument("--limit", type=int, default=None, help="look up at most N pending points this run")
        sub.add_argument("--no-cache", action="store_true", help="bypass the on-disk request cache")

    addInput(stages.add_parser("ingest", help="parse an export into visits.jsonl"))
    addLookupOptions(stages.add_parser("lookup", help="query Street View metadata for each place"))
    stages.add_parser("match", help="compute and rank collisions")
    stages.add_parser("map", help="write CSV, HTML table and folium map")
    runAll = stages.add_parser("run-all", help="ingest, lookup, match, map")
    addInput(runAll)
    addLookupOptions(runAll)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = buildParser().parse_args(argv)
    log.basicConfig(level=log.DEBUG if args.verbose else log.INFO, format="%(levelname)s %(message)s")
    # The cache library logs full request URLs at DEBUG, which would include the API key.
    log.getLogger("aiohttp_client_cache").setLevel(log.INFO)
    paths = StagePaths(args.data_dir, args.output_dir, args.cache_dir)

    if args.stage == "ingest":
        runIngest(args.input, paths)
    elif args.stage == "lookup":
        runLookup(paths, args)
    elif args.stage == "match":
        runMatch(paths)
    elif args.stage == "map":
        runMap(paths)
    elif args.stage == "run-all":
        runIngest(args.input, paths)
        runLookup(paths, args)
        runMatch(paths)
        runMap(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
