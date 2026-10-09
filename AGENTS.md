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
# Windows shown; on Linux or macOS use python3 -m venv .venv, .venv/bin/pip, .venv/bin/python
# and export GMAPS_STATIC_API_KEY=... in place of every .venv\Scripts\... and set ... below.
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
.venv\Scripts\python -m pytest -q                              # All 235 tests (no network; tests/test_drawer_js.py needs node, else skipped)
.venv/bin/python -m pytest -q                                  # Same on Linux or macOS
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
- `lookup` appends to `data/panos.jsonl` and skips points already done, so reruns resume and `--limit` works in slices. An OK row without `copyright` (written before credits were stored) is looked up once more, normally from the cache; `match` prefers the credited row on a tie. REQUEST_DENIED and INVALID_REQUEST abort at once; HTTP 429/5xx and similar are retried with exponential backoff.
- Only the current panorama at each point is visible; the API exposes no historical imagery. Dates have month granularity.
- A visit crossing midnight on the last day of a month counts for both months.
- Ranking is a probability. Per panorama, `probability = coverage * visibility * proximity` (see `svcd/scoring.py`; all constants are module-level). Per place and month, only the nearest panorama counts, `coverage * visibility * max(proximity)`: dates have month granularity, so several panoramas in one month are treated as one drive. `scoring.combine` remains but is not used for candidates. `Collision.score` is kept only for backwards compatibility and equals the probability.
  - coverage: dwell in 07:00 to 19:00 solar time (estimated from longitude alone) over the month's daylight hours, with night dwell counted at 5 percent, capped at 1.
  - visibility prior: 0.9 for walking, running or cycling path places; 0.1 for home or work (semantic type, majority of visits); 0.7 for outdoor-word names (a word ending with a suffix keyword such as park or bahnhof, or equal to a whole-word keyword such as zoo or pier; an exclusion list drops Supermarkt, Parkhaus, Kindergarten and similar) or a majority of transitional visits; 0.5 when the median stay is under 15 minutes; 0.3 otherwise.
  - proximity: `exp(-distanceM / 40)` from place centre to panorama.
- Ingest also reads walking, running and cycling activity segments from both formats, one `Visit` per path point (`kind` walking or cycling), each window capped at `PATH_POINT_MAX_MINUTES` (15) and clipped to its segment; other transport modes are ignored. `semanticType` (HOME/WORK) and `importance` are kept on `Visit`.
- Path points are deduplicated by coordinates rounded to 4 decimals (about 10 m; stays use 5) under keys like `path:52.5163,13.3777`, so a path key never equals a stay key. A path place is centred on its key coordinates, not the mean of its points. Path places are sampled at the centre only; stays still get the ring.
- `match` writes `data/collisions.jsonl` (one row per panorama) and `data/candidates.jsonl` (one row per place and month, ranked, panoramas listed as dicts) and logs the best odds and the expected number of finds (sum of candidate probabilities). `map` reads only the candidates and writes `output/candidates.csv`, `output/candidates.html` and `output/map.html`. The old collisions.csv and collisions.html are gone.
- `map.html` is a single standalone file: the drawer markup, CSS and JS in `svcd/templates/` are inlined at build time. The drawer lists candidates 20 per page with a filter, odds, "Show on map" and panorama links; the heading is "Streetview Finder" with the count beneath; the text filter and "Manage checked marks" (export, import, clear) are collapsed `<details>`, Imagery and Checked are `<select>`s; markers share one colour, grey once checked; a `#rank-N` fragment selects an entry on load.
- Provenance: the metadata response's credit line is stored as `PanoLookup.copyright`. `models.panoSource(copyright)` classifies it: any credit containing "google" (case-insensitive), or a missing, empty or blank credit, is `SOURCE_GOOGLE`; anything else is `SOURCE_USER` (a photosphere from a Maps contributor). `Collision.source` carries it. Candidates are built per (placeKey, month, source), so Google imagery and user photospheres at one place and month are separate `Candidate`s with their own probability (nearest panorama of that source), and ranks are global across both. `Candidate.checkKey` is `f"{placeKey}|{month}|{source}"`. Each `panos` dict also carries `source` and `copyright`. User photospheres are single moments at venues, so the car-drive assumptions in the odds fit them worse; do not read their probabilities as comparable in quality to Google ones. CSV has `source` and `checkKey` columns and the HTML table a source column; badges use `report.SOURCE_LABELS` ("Google", "User photo") and the drawer JS repeats those words.
- Checked state is purely client-side, in the drawer JS. It is stored in the browser's localStorage under `svcd-checked` as a JSON object keyed by `checkKey` (value is the ISO timestamp of when it was checked; unchecking deletes the key), so it survives regenerating `map.html` but is per browser and per origin (a different browser or file path starts empty). "Export checked" downloads `svcd-checked.json`; "Import checked" merges a file in; "Clear checked" asks for confirmation. Every write re-reads the store first and a `storage` listener reloads it, so several open tabs do not overwrite each other. An unreadable stored value is copied to `svcd-checked.bak` and the drawer starts empty; an object with some non-string values keeps the valid entries. Pure helpers are exposed on `window.__svcd` for `tests/test_drawer_js.py`. The drawer has Imagery (All / Google only / User photos only) and Checked (All / Unchecked / Checked) filters that compose with the text filter; checked entries are muted and their markers grey; the header shows "N candidates, M checked"; Space on a focused entry toggles it and Enter shows it on the map. The Python side never reads or writes this state.
- Sharing mode is a drawer checkbox (with an info button and popover explaining it), off on every load and never stored. While on, a candidate without a name (its place key would show: `path:` keys, bare coordinates, placeIds) reads "Unknown location" in entries, popups and marker tooltips, and every contributor credit reads "Contributor hidden" (credits containing "google" are kept); a "Sharing mode" badge shows in the header. The text filter still matches real names. Helpers `displayName` and `displayCredit` are on `window.__svcd`. The HTML table and CSV are not masked.
- Metadata requests are free and consume no quota. Responses are cached for 180 days in `cache/streetview.sqlite`.

## Adapting to another dataset

Everything person-specific is in the input export and a few constants. Use a separate `--data-dir`, `--output-dir` and `--cache-dir` per person as subfolders of the ignored ones (`data/alice`, `output/alice`, `cache/alice`); `lookup` appends to `panos.jsonl`, so reusing a directory mixes two people.

### Input
- The export goes anywhere readable and is passed as `--input`. `detectFormat` in `svcd/ingest/__init__.py` returns `"semantic"` for a directory that is, or contains within two levels, a folder named `Semantic Location History`, and `"timeline"` for a `.json` file containing a top-level `"semanticSegments"` key (found by scanning the file, not parsing it). Anything else raises `ValueError` with a hint. `Records.json` and `Settings.json` are rejected on purpose.
- If an export is laid out differently, extend `findSemanticRoot` (`svcd/ingest/semanticHistory.py`: folder name `SEMANTIC_DIR_NAME`, search depth) or the parsers in `semanticHistory.py` / `timelineExport.py` (field names, E7 or `geo:` coordinate formats, durations).
- A new format needs a parser that yields `Visit` records, a branch in `detectFormat` (extend its `Literal`) and a branch in `ingestVisits`, which sorts by start. Add synthetic fixtures and tests beside `tests/test_ingest.py`.
- `Visit` contract (`svcd/models.py`): `placeId` and `name` and `address` may be `None`; `lat`, `lng` are degrees; `start`, `end` are timezone-aware UTC datetimes with `end >= start`; `source` names the format; `kind` is `place`, `walking` or `cycling` (path points: one per point, window capped at `PATH_POINT_MAX_MINUTES`); `semanticType` is `HOME`, `WORK` or `None`; `importance` is `MAIN`, `TRANSITIONAL` or `None`. A parser that cannot fill the last two leaves them `None` and scoring degrades gracefully.
- The real export never enters `tests/`, docs or reports. Write synthetic fixtures with invented coordinates, names and ids.

### Assumptions tied to the dataset
- `svcd/scoring.py`: `OUTDOOR_SUFFIX_KEYWORDS` (matches words equal to or ending with the keyword, for compounds), `OUTDOOR_WORD_KEYWORDS` (whole words only, for short keywords that would otherwise hit unrelated words) and `OUTDOOR_EXCLUDED_WORDS` (indoor places that end in a suffix keyword) are English and German. Add the other language's park, square, market, station and beach words, then exclusions found by reading the top of the ranking for indoor false positives. Names are matched case-insensitively.
- `DAYLIGHT_START`, `DAYLIGHT_END` (07:00 to 19:00 solar), `NIGHT_WEIGHT` (0.05), the `VISIBILITY_*` priors and `BRIEF_VISIT`. Solar time is UTC plus longitude / 15 hours; there is no timezone database, so local-time rules (daylight saving, polar latitudes) are approximate everywhere. The car does not drive at night, but where Street View crews work different hours the window is the first thing to move.
- `PROXIMITY_SCALE_M` (40) tracks the ring radius; if `--ring-radius` changes substantially, change both. `PATH_POINT_MAX_MINUTES` in `svcd/models.py` (15) bounds what a sparse walking or cycling point is worth; raise it for denser or slower exports.
- Ring sampling (`--ring-points 6`, `--ring-radius 40`) trades coverage of surrounding streets against lookups: each stay costs 1 + N requests. Rural or dense urban data may want different values.
- `DEFAULT_CENTRE` in `svcd/report.py` (Berlin) is used only when there are no candidates; otherwise the map centres on the mean of the candidates. Tile layers are in `buildMap`.

### Clean run
1. Install, set `GMAPS_STATIC_API_KEY`, confirm `data/`, `output/`, `cache/` and `takeout/` are gitignored (only those exact names are).
2. `run-all --input PATH` with the per-person directories. Expect tens of thousands of lookups to finish in a few minutes at the default concurrency of 20, and to be served from cache for 180 days on reruns.
3. Use `--limit N` to try a slice first. Reruns skip points with a definitive status (`OK`, `ZERO_RESULTS`, `NOT_FOUND`) and retry `ERROR` rows (quota, network, 429/5xx after retries). `REQUEST_DENIED` and `INVALID_REQUEST` abort at once, usually a bad or restricted key.
4. Read the `match` log: the count of Google and user collisions, the best odds, and the expected number of finds, which is the sum of candidate probabilities, that is how many real sightings the model predicts across the whole list. It is a sum of rough priors, so read it as an order of magnitude, and expect it to be small.
5. Sanity-check the top ranks: indoor venues near the top mean the keyword lists need work; mostly user photospheres means look at the Imagery filter.

### Verifying without personal data
- `tests/test_cli.py::test_run_all_end_to_end_on_fixtures` runs all four stages against the synthetic `tests/fixtures/semantic_fixture` with aioresponses; copy its approach for a new parser or format.
- To check the map UI without any real data, write about 45 synthetic `Candidate` records (invented coordinates around any city, a mix of named, unnamed, `path:` keyed and user-source candidates, varied probabilities) with `storage.writeJsonl` into a scratch `--data-dir` under `.tmp/`, run `map` with `--data-dir` and `--output-dir` pointing there, and open the result. Keep the generator out of the repository or commit it with invented values only.

### Never commit, and what to redact
- Never commit `takeout/`, `data/`, `output/`, `cache/`, `.tmp/`, `*.sqlite` (request URLs contain the key) or `.env`. Do not paste coordinates, names, placeIds or addresses from them into issues, logs or reports.
- Before sharing a screenshot of `map.html`, turn on Sharing mode: it masks unnamed places and contributor credits. It does not mask named venues, the map position itself, the URL, `candidates.csv` or `candidates.html`; crop or blur those, or share no real map at all.

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
