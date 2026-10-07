# AGENTS.md - AI Coding Agent Guidelines

This document provides guidelines for AI coding agents working in the Streetview-Collision-Detector codebase.

## Project Overview

A Python application that helps users find themselves on Google Streetview by comparing their Google Maps location history with Streetview photo dates. When a "collision" is found (user was at a location in the same month as the Streetview photo), the app generates an interactive heatmap.

## Tech Stack

- **Language**: Python 3.x
- **Async HTTP**: aiohttp with aiohttp_client_cache (SQLite backend)
- **Data Processing**: pandas, google_takeout_parser
- **Visualization**: folium (interactive maps)

## Build/Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set required environment variable
export GMAPS_STATIC_API_KEY={your_google_maps_api_key}

# Run the application
python main.py
```

## Testing

**Note**: No test suite exists yet. The .gitignore suggests pytest as the intended framework.

```bash
pytest                          # Run all tests
pytest tests/test_file.py       # Run a single test file
pytest tests/test_file.py::test_name  # Run a single test function
pytest -v                       # Verbose output
pytest -x                       # Stop on first failure
```

## Linting/Formatting

No linting tools are currently configured. Recommended tools if adding:

```bash
pip install ruff black mypy
ruff check .                    # Lint all files
black .                         # Format all files
mypy .                          # Type checking
```

## Project Structure

```
├── main.py              # Entry point - orchestrates collision detection & map generation
├── models.py            # Data model: StreetViewCollision class
├── get_metadata.py      # Google Streetview API interaction (async)
├── parse_takeout.py     # Parses Google Takeout location history
├── requirements.txt     # Python dependencies
└── takeout/             # User's Google Takeout data (gitignored)
```

## Code Style Guidelines

### Imports

Organize imports: standard library, third-party, then local modules.

```python
import os
import asyncio
from typing import List

from aiohttp import TCPConnector
import pandas as pd

from models import StreetViewCollision
```

### Type Hints

Use type hints for function signatures:

```python
async def checkAllVisitsForHits(placeVisits: List[PlaceVisit]) -> List[StreetViewCollision]:
    ...
```

### Naming Conventions

- **Functions**: camelCase (`checkAllVisitsForHits`, `getPlaceVisits`)
- **Variables**: camelCase (`placeVisits`, `api_key`)
- **Classes**: PascalCase (`StreetViewCollision`)
- **Constants**: snake_case or UPPER_CASE (`max_workers`)

### Async Patterns

Use async/await for HTTP requests with chunked processing:

```python
async def main():
    results = await checkAllVisitsForHits(placeVisits)

asyncio.run(main())
```

### Error Handling

Use try/except with logging:

```python
import logging as log

try:
    # operation
except AttributeError:
    log.error(placeVisit)
    return

if response.status != 200:
    log.error(f"Failed API call {response.status_code}")
```

### Logging

Use Python's logging module with `log` alias:

```python
import logging as log
log.basicConfig(level=log.INFO)
log.info("Starting search...")
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GMAPS_STATIC_API_KEY` | Google Maps Static API key | Yes |

## Important Patterns

### Request Caching

API requests are cached in SQLite:

```python
async with CachedSession(
    cache=SQLiteBackend('mapsRequestCache', expire_after=60*60*24),
    connector=tcp_connection
) as session:
    # cached requests
```

### Chunked Processing

Large datasets processed in chunks (default: 2000):

```python
for chunk in chunkedIterable(items, max_workers):
    results.extend(await processChunk(chunk))
```

## Files to Never Commit

- `takeout/` - Personal location history
- `*.sqlite` - Cache databases
- `output.pkl` - Processed results
- `heatmap*.html` - Generated maps
- `.env` - Environment variables
