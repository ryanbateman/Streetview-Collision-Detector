# AGENTS.md - AI Coding Agent Guidelines

This document provides guidelines for AI coding agents working in the Streetview-Collision-Detector codebase.

## Project Overview

A Python package (`svcd`) that helps users find themselves on Google Streetview. It compares Google location history (visits) with the capture month of Street View panoramas near each visited place. A "collision" is a (place, panorama, month) where the user visited the place during the month the panorama was photographed. Collisions are ranked by the probability that a panorama caught the user, grouped into place-and-month candidates, and written out as CSV, an HTML table and an interactive folium map with a side drawer.

The pipeline is staged, and each stage reads and writes files so it can be rerun alone: ingest, lookup, match, map.

## Tech Stack

- **Language**: Python 3.11 or newer
- **Async HTTP**: aiohttp with aiohttp_client_cache (SQLite backend), tqdm for progress
- **Data Processing**: pandas, stdlib json (the old `google_takeout_parser` and `ijson` are no longer used)
- **Visualization**: folium (marker cluster and heat layer)
- **Tests**: pytest, pytest-asyncio, aioresponses

Runtime dependencies are pinned in `requirements.txt`; `requirements-dev.txt` adds the test tools.

## Build/Run Commands

```bash
# Windows (Unix: python3 -m venv .venv, .venv/bin/pip, export GMAPS_STATIC_API_KEY=...)
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
set GMAPS_STATIC_API_KEY=your_key

# Run a stage, or everything
.venv\Scripts\python -m svcd.cli ingest --input PATH
.venv\Scripts\python -m svcd.cli lookup [--ring-points 6] [--ring-radius 40] [--concurrency 20] [--limit N] [--no-cache]
.venv\Scripts\python -m svcd.cli match
.venv\Scripts\python -m svcd.cli map
.venv\Scripts\python -m svcd.cli run-all --input PATH
```

Global options go before the stage: `--data-dir data`, `--output-dir output`, `--cache-dir cache`, `-v` (debug logging), `-q` (no progress bar).

`--input` is either a Takeout folder (or its "Location History (Timeline)" or "Semantic Location History" subfolder, old format) or a `Timeline.json` / `location-history.json` file from the phone's on-device Timeline export. Google moved Timeline on-device in 2024, so new Takeout exports have no Location History. The phone format has only been tested against synthetic fixtures. `Records.json` (raw GPS pings) is deliberately not read.

## Testing

```bash
.venv\Scripts\python -m pytest -q                              # All 199 tests (no network)
.venv\Scripts\python -m pytest tests/test_match.py             # One file
.venv\Scripts\python -m pytest tests/test_match.py::test_name  # One test
.venv\Scripts\python -m pytest -v                              # Verbose output
.venv\Scripts\python -m pytest -x                              # Stop on first failure
```

Tests never touch the network: aioresponses mocks the Street View API. A shim in `tests/conftest.py` works around an aioresponses/aiohttp 3.14 incompatibility; do not remove it without rerunning the suite.

## Linting/Formatting

No linting tools are currently configured. Recommended tools if adding:

```bash
pip install ruff black mypy
ruff check .
black .
mypy .
```

## Project Structure

```
svcd/
  cli.py          entry point, stage wiring, resumable lookups
  models.py       frozen dataclasses: Visit, Place, SamplePoint, PanoLookup, Collision, Candidate
  storage.py      JSONL/CSV read and write driven by dataclass type hints
  ingest/         detectFormat, ingestVisits; semanticHistory.py (old Takeout), timelineExport.py (phone export)
  places.py       placeKey, buildPlaces, ringPoints, buildSamplePoints, haversineM
  streetview.py   StreetViewClient (semaphore, retry with backoff), openCachedSession, buildPanoUrl
  match.py        monthsSpanned, dwellInMonth, findCollisions, rankCollisions, buildCandidates
  scoring.py      likelihood model: solar daylight, coverage, visibility, proximity, combine
  report.py       writeTable, buildMap
  templates/      drawer.html, drawer.css, drawer.js, inlined into map.html
tests/            pytest suite; tests/fixtures holds synthetic exports and API responses
```

Gitignored working directories: `data/` (stage output as JSONL), `output/` (CSV and HTML), `cache/` (request cache), `takeout/` (user's export).

## Behaviour Worth Knowing

- Places are deduplicated by placeId, else by coordinates rounded to 5 decimals. Each place is sampled at its centre plus a ring of N points at R metres (defaults 6 and 40).
- `lookup` appends to `data/panos.jsonl` and skips points already done, so reruns resume and `--limit` works in slices. REQUEST_DENIED and INVALID_REQUEST abort at once; HTTP 429/5xx and similar are retried with exponential backoff.
- Only the current panorama at each point is visible; the API exposes no historical imagery. Dates have month granularity.
- A visit crossing midnight on the last day of a month counts for both months.
- Ranking is a probability. Per panorama, `probability = coverage * visibility * proximity` (see `svcd/scoring.py`; all constants are module-level). Per place and month, only the nearest panorama counts, `coverage * visibility * max(proximity)`: dates have month granularity, so several panoramas in one month are treated as one drive. `scoring.combine` remains but is not used for candidates. `Collision.score` is kept only for backwards compatibility and equals the probability.
  - coverage: dwell in 07:00 to 19:00 solar time (estimated from longitude alone) over the month's daylight hours, with night dwell counted at 5 percent, capped at 1.
  - visibility prior: 0.9 for walking, running or cycling path places; 0.1 for home or work (semantic type, majority of visits); 0.7 for outdoor-word names (a word ending with a suffix keyword such as park or bahnhof, or equal to a whole-word keyword such as zoo or pier; an exclusion list drops Supermarkt, Parkhaus, Kindergarten and similar) or a majority of transitional visits; 0.5 when the median stay is under 15 minutes; 0.3 otherwise.
  - proximity: `exp(-distanceM / 40)` from place centre to panorama.
- Ingest also reads walking, running and cycling activity segments from both formats, one `Visit` per path point (`kind` walking or cycling), each window capped at `PATH_POINT_MAX_MINUTES` (15) and clipped to its segment; other transport modes are ignored. `semanticType` (HOME/WORK) and `importance` are kept on `Visit`.
- Path points are deduplicated by coordinates rounded to 4 decimals (about 10 m; stays use 5) under keys like `path:52.5163,13.3777`, so a path key never equals a stay key. A path place is centred on its key coordinates, not the mean of its points. Path places are sampled at the centre only; stays still get the ring.
- `match` writes `data/collisions.jsonl` (one row per panorama) and `data/candidates.jsonl` (one row per place and month, ranked, panoramas listed as dicts) and logs the best odds and the expected number of finds (sum of candidate probabilities). `map` reads only the candidates and writes `output/candidates.csv`, `output/candidates.html` and `output/map.html`. The old collisions.csv and collisions.html are gone.
- `map.html` is a single standalone file: the drawer markup, CSS and JS in `svcd/templates/` are inlined at build time. The drawer lists candidates 20 per page with a filter, odds, "Show on map" and panorama links; markers are coloured by rank third; a `#rank-N` fragment selects an entry on load.
- Metadata requests are free and consume no quota. Responses are cached for 180 days in `cache/streetview.sqlite`.

## Code Style Guidelines

### Imports

Organize imports: standard library, third-party, then local modules.

```python
import asyncio
import logging as log
from pathlib import Path

import aiohttp

from svcd.models import Visit
from svcd.storage import readJsonl
```

### Type Hints

Use type hints for function signatures:

```python
def findCollisions(visits: list[Visit], places: list[Place], lookups: list[PanoLookup]) -> list[Collision]:
    ...
```

### Naming Conventions

- **Functions**: camelCase (`findCollisions`, `buildSamplePoints`)
- **Variables**: camelCase (`placeVisits`, `apiKey`)
- **Classes**: PascalCase (`StreetViewClient`)
- **Constants**: UPPER_CASE (`API_KEY_ENV`)
- **Records**: frozen dataclasses in `svcd/models.py`; fields are camelCase so JSONL keys match

### Logging and Errors

Use Python's logging module with the `log` alias, and log failures rather than printing:

```python
import logging as log
log.info("Starting search...")
log.error(f"Failed API call {response.status}")
```

Unrecoverable user errors (missing key, missing output from an earlier stage) raise `SystemExit` with a plain message.

### Async Patterns

HTTP work is async with a semaphore bounding concurrency; the CLI wraps it in `asyncio.run`. Results stream to disk in buffered appends so interrupted runs lose little.

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GMAPS_STATIC_API_KEY` | Google Maps Static API key | Yes, for `lookup` and `run-all` |

## Privacy Rules

- Test fixtures must be synthetic. Never copy real coordinates, placeIds, names or addresses from `takeout/` into tests, fixtures, docs or reports.
- Never print or log the API key or personal location values. Note that `cache/streetview.sqlite` stores request URLs, which contain the key.

## Files to Never Commit

- `takeout/` - Personal location history
- `data/` - Stage output derived from location history
- `output/` - Generated CSV and HTML maps
- `cache/` - Request cache (contains the API key)
- `.tmp/` - Scratch files
- `*.sqlite` - Cache databases
- `.env` - Environment variables
