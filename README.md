# Streetview Collision Detector
A programmatic attempt to help you find yourself on Google Maps Streetview using your Google Maps Location history.

## What is this
This is a small Python package that is intended to help someone hoping to spot themselves on [Google Streetview](https://www.google.com/streetview/). It does this by looking at all the places where you've been (using your [Google Maps location history](https://support.google.com/maps/answer/3118687?hl=en)) and comparing this with the date of the Streetview photo for that place.

When it does find that you were somewhere in the same month as the Streetview photo for that place was taken, it lets you know about the 'collision' so that you can jump onto Streetview yourself and see if you can spot yourself.

![A screenshot showing the script out for a collision](assets/collision.png?raw=true "An example of a collision")

## Does it work?
Well, yes and no. Google Streetview photos only show the month they were taken, not the day, so it gives you a better chance to spot yourself, definitely, but it's still not precise. And you'll still need to wander around Streetview to find yourself. But this will at least help target your search somewhat.

## Okay. Is it difficult to get working?
Somewhat. You need a fair amount of technical expertise or be quite patient and Google things.

## So what do I need to know ahead of time?
Firstly, you will need to have had Location History (now called Timeline) enabled, with some history for this tool to look at. The quickest way to check is your [Google Maps Timeline](https://support.google.com/maps/answer/6258979?hl=en&co=GENIE.Platform%3DDesktop), which will confirm that Google has been intently tracking you, much as a deranged hunter would track a boomslang[^1] across the Africa veldt.

You'll also need a [Google Maps Static API key](https://developers.google.com/maps/documentation/maps-static/get-api-key), so you'll need to know how to navigate Google's Developer Console. The Street View metadata requests this tool makes are free of charge and consume no quota, according to Google's documentation.

You'll also need to know a little Python[^2] (3.11 or newer) and a little git[^3].

## Hardly seems worth it.
Fair.

## Getting your data
Google moved Timeline on to your phone in 2024, and new [Google Takeout](https://takeout.google.com/) exports, Google's truly excellent data exfiltration service, no longer contain Location History at all. Which leaves two routes:

- **Old Takeout export.** If you exported before the change, the "Semantic Location History" folder (year subfolders full of rich, meaty JSON) still works. Unzip it somewhere, for example `takeout/`.
- **Phone export.** Otherwise, on your phone go to Google Maps, your profile, Your Timeline, and export. You get a `Timeline.json` or `location-history.json`. This format is supported, but so far it has only been tested against synthetic fixtures. Expect rough edges.

`Records.json` (the raw GPS pings) is deliberately not read. The place visits are enough, and the raw file is enormous.

## How do I get started then?
Clone this repo (the one you're looking at right now) and set up an environment. On Windows:
```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
set GMAPS_STATIC_API_KEY=your_key
```
On Linux or macOS, use `python3 -m venv .venv`, `.venv/bin/pip` and `export GMAPS_STATIC_API_KEY=your_key`.

Then run the whole thing:
```
.venv\Scripts\python -m svcd.cli run-all --input takeout
```
`--input` is either the Takeout folder (or its "Location History (Timeline)" or "Semantic Location History" subfolder), or the phone's `Timeline.json` / `location-history.json`. Open `output/map.html` and `output/collisions.html` when it finishes.

## The stages
Each stage reads and writes files, so you can rerun any of them on its own. Global options go before the stage: `--data-dir data`, `--output-dir output`, `--cache-dir cache`, `-v` (debug logging), `-q` (no progress bar).

- `ingest --input PATH` parses the export into `data/visits.jsonl`.
- `lookup` dedupes visits into places (by placeId, else coordinates rounded to 5 decimals), samples each place centre plus a ring of points around it so surrounding streets are covered, and asks the Street View metadata API about each point. Options: `--ring-points 6`, `--ring-radius 40` (metres), `--concurrency 20`, `--limit N`, `--no-cache`. Writes `data/places.jsonl`, `data/samplePoints.jsonl` and appends to `data/panos.jsonl`. A rerun skips points already done, so it is resumable, and `--limit` lets you do it in slices. Responses are cached in `cache/streetview.sqlite` for 180 days. A REQUEST_DENIED or INVALID_REQUEST (usually a bad key) aborts immediately with the API's message.
- `match` writes `data/collisions.jsonl`: one collision per place, panorama and month, where any visit overlaps that calendar month (a visit crossing midnight on the last day of a month counts for both). Collisions are ranked by score: `log1p(dwell minutes) + 0.5 * log1p(visit count) - distance / 200`, so long, repeated visits close to the panorama come first.
- `map` writes `output/collisions.csv`, `output/collisions.html` (a sortable table with "Open panorama" links) and `output/map.html` (a folium map with a marker cluster and a heat layer weighted by score; popups link to the exact panorama by pano id).
- `run-all --input PATH` does all four, and accepts the lookup options.

## Any gotchas?
A few.
- Only the *current* panorama at each point is checked. Google does not expose historical imagery through the API, so if a street was re-photographed after your visit, the older panorama is invisible to this tool.
- Panorama dates have month granularity. You still have to wander around Streetview yourself.
- The map defaults to Esri's street tiles because OpenStreetMap's tile server refuses requests without a Referer header, which is what you get when you open `output/map.html` straight from disk. OpenStreetMap and Carto are still in the layer control; OpenStreetMap works if you serve the folder instead, for example `python -m http.server -d output 8000` and then open http://localhost:8000/map.html.
- `cache/streetview.sqlite` contains your API key inside the stored request URLs. It is gitignored. Do not share it.
- `data/`, `output/`, `cache/` and `takeout/` are gitignored because they hold your personal location data. Keep it that way.
- And, uh, I'm not a Python developer. Or a developer at all. So, you know, it could all break. (PRs welcomed.)

## Tests
```
.venv\Scripts\python -m pytest -q
```
104 tests, no network needed: aioresponses mocks the API, and a shim in `tests/conftest.py` works around an aioresponses/aiohttp 3.14 incompatibility. The fixtures are synthetic, as they should be.

[^1]: The deadly snake, not the [gaming peripheral](https://en.wikipedia.org/wiki/List_of_Razer_products), though I can personally vouch that they're okay as mice go. (Mouses? Meese? Mice? Yeah, mice, probably.)
[^2]: The programming language, not a small snake, though having allies in the animal world is never a bad idea, especially if you're the aforementioned hunter.
[^3]: The [version control system](https://git-scm.com/), not, say, Jacob Rees-Mogg, who can get in the sea as far as I'm concerned.
