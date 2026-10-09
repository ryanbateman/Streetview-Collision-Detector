# Streetview Collision Detector
A programmatic attempt to help you find yourself on Google Maps Streetview using your Google Maps Location history.

## What is this
This is a small Python package that is intended to help someone hoping to spot themselves on [Google Streetview](https://www.google.com/streetview/). It does this by looking at all the places where you've been (using your [Google Maps location history](https://support.google.com/maps/answer/3118687?hl=en)) and comparing this with the date of the Streetview photo for that place.

When it does find that you were somewhere in the same month as the Streetview photo for that place was taken, it lets you know about the 'collision' so that you can jump onto Streetview yourself and see if you can spot yourself.

![A screenshot showing the script out for a collision](assets/collision.png?raw=true "An example of a collision")

## Does it work?
Well, yes and no. Google Streetview photos only show the month they were taken, not the day, so it gives you a better chance to spot yourself, definitely, but it's still not precise. And you'll still need to wander around Streetview to find yourself. But this will at least help target your search somewhat.

The list is ranked by probability rather than by how long you stayed. For each place and month, the chance that a panorama caught you is coverage x visibility x proximity: how much of that month's daylight you spent there, how likely you were to be outdoors and in view, and how close the panorama sits to the place. Only the nearest panorama counts: the API gives just the month, so several panoramas at one place in one month are treated as one drive by one car, not as separate chances. (Google's own imagery and photospheres uploaded by Maps contributors are ranked separately, so a place can appear twice for one month; see the gotchas.) This is why your home drops down the list: you were indoors, and the Street View car does not drive at night. A park you strolled through twice now beats the flat you slept in for thirty nights.

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

Both formats also contribute walking, running and cycling activity segments: each point on such a path becomes a visit, because being on foot or on a bike is the likeliest way to end up in a photo. Other transport modes are ignored. The old export's activity segments are used this way; `Records.json` (the raw GPS pings) is still deliberately not read. The place visits and paths are enough, and the raw file is enormous.

Google's own labels are kept too: the semantic type (home or work) and whether a visit was a main stop or merely transitional. The likelihood model uses both.

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
On Linux or macOS the same command is `.venv/bin/python -m svcd.cli run-all --input takeout`; every `.venv\Scripts\python` below has a `.venv/bin/python` twin.
`--input` is either the Takeout folder (or its "Location History (Timeline)" or "Semantic Location History" subfolder), or the phone's `Timeline.json` / `location-history.json`. Open `output/map.html` and `output/candidates.html` when it finishes.

## The stages
Each stage reads and writes files, so you can rerun any of them on its own. Global options go before the stage: `--data-dir data`, `--output-dir output`, `--cache-dir cache`, `-v` (debug logging), `-q` (no progress bar).

- `ingest --input PATH` parses the export into `data/visits.jsonl`: place visits plus walking, running and cycling path points. Each path point covers at most 15 minutes, so a long walk with few recorded points does not credit hours to one spot.
- `lookup` dedupes visits into places (by placeId, else coordinates rounded to 5 decimals for stays and 4 decimals, about 10 m, for path points, whose keys start with `path:`) and asks the Street View metadata API about each place centre. Stays also get a ring of points around the centre so surrounding streets are covered; path points are sampled at the centre only. Options: `--ring-points 6`, `--ring-radius 40` (metres), `--concurrency 20`, `--limit N`, `--no-cache`. Writes `data/places.jsonl`, `data/samplePoints.jsonl` and appends to `data/panos.jsonl`. A rerun skips points already done, so it is resumable, and `--limit` lets you do it in slices. Responses are cached in `cache/streetview.sqlite` for 180 days. A REQUEST_DENIED or INVALID_REQUEST (usually a bad key) aborts immediately with the API's message.
- `match` writes two files. `data/collisions.jsonl` has one row per panorama: any visit overlapping that panorama's calendar month counts (a visit crossing midnight on the last day of a month counts for both), and each row carries its coverage, visibility, proximity and probability. `data/candidates.jsonl` has one row per place, month and imagery source ("google" or "user", classified from the credit line the API returns), ranked by the probability of its nearest panorama, with all its panoramas listed. The stage logs the best odds and the expected number of actual finds summed over all candidates, which is a sober reality check.
- `map` reads the candidates and writes `output/candidates.csv`, `output/candidates.html` (a sortable table) and `output/map.html`. The CSV gains `source` and `checkKey` columns and the table a source column. The map is a single standalone HTML file with a left side drawer titled "Streetview Finder", with the candidate count beneath. Below that is a paginated list (20 per page) of candidates in rank order. Each entry shows its month, its odds ("about 1 in N", or a percentage from 50 percent up), a "Show on map" link that zooms to the place and opens its popup, and a direct "Panorama" link (plus "+N more" when several panoramas qualify). The popup lists every panorama. Each candidate and each panorama link carries a "Google" or "User photo" badge, with the contributor's credit for user photos. Markers share one colour (grey once checked), and clicking one highlights its entry in the drawer. A `#rank-N` fragment on the URL selects an entry on load, so you can link to a rank. On narrow screens the drawer collapses.
  - **Filters.** The text filter sits in a collapsed "Filter" box (name or month, such as 2022-11). Imagery (All / Google only / User photos only) and Checked (All / Unchecked / Checked) are dropdowns. All three combine.
  - **Checked.** Every drawer entry and popup has a "Checked" checkbox, for keeping track of what you have already looked at on Streetview. Checked entries are muted and their markers turn grey. The header reads "N candidates, M checked". Space on a focused entry toggles it; Enter shows it on the map.
  - **Manage checked marks.** A collapsed section holds "Export checked" (downloads `svcd-checked.json`), "Import checked" (merges such a file back in) and "Clear checked" (asks before wiping anything). Checks are keyed by place, month and source, so they survive regenerating `map.html`.
  - **Sharing mode.** A checkbox in the drawer, off on every load. While on, unnamed places (the ones that would show coordinates or an ID) read "Unknown location" and user-photo contributor credits read "Contributor hidden", in entries, popups and marker tooltips. Use it before taking a screenshot. It does not touch the CSV or the HTML table.
- `run-all --input PATH` does all four, and accepts the lookup options.

### How visibility is guessed
Visibility is a prior, not a measurement: 0.9 for places that come from a walking, running or cycling path; 0.1 for home or work (from Google's semantic type); 0.7 for names with outdoor words (park, platz, strand, bahnhof, markt, zoo and so on; most match at the end of a word so German compounds like Volkspark count, while Supermarkt, Parkhaus and Kindergarten do not; the list is English and German only, so place names in other languages fall back to the default unless you extend it) or for transitional visits; 0.5 where your typical stay is under 15 minutes; 0.3 otherwise. Coverage uses 07:00 to 19:00 solar time, estimated from longitude alone, with night-time dwell counted at 5 percent. Proximity is `exp(-distance / 40 m)` from the place centre to the panorama. The constants live at the top of `svcd/scoring.py` if you disagree.

## Any gotchas?
A few.
- Only the *current* panorama at each point is checked. Google does not expose historical imagery through the API, so if a street was re-photographed after your visit, the older panorama is invisible to this tool.
- Panorama dates have month granularity. You still have to wander around Streetview yourself.
- The probabilities are rough priors, not calibrated. Treat the ranking as an ordering, and the "about 1 in N" figures as the right order of magnitude at best. The expected number of finds that `match` logs tells you how much to hope for; it is usually small.
- The map defaults to Esri's street tiles because OpenStreetMap's tile server refuses requests without a Referer header, which is what you get when you open `output/map.html` straight from disk. OpenStreetMap and Carto are still in the layer control; OpenStreetMap works if you serve the folder instead, for example `python -m http.server -d output 8000` and then open http://localhost:8000/map.html.
- User photospheres rank high and deserve suspicion. The metadata response includes a credit line: Google's own car, trekker and backpack imagery is credited to Google, while a photosphere uploaded by a Maps contributor is credited to that contributor. The tool stores the credit, classifies each panorama as Google or user, and ranks each place and month separately per source, so the odds are computed for each. In one test dataset, more than a third of the panoramas found were user photospheres. Contributors tend to photograph venue interiors and entrances, which are places you visited and were visible, so user photos can take over the top of the ranking and push the best Google car imagery well down the list. The Imagery filter is there for that reason. A user photosphere is a single still moment taken by a person, usually at or inside a venue, so the daylight car drive that the odds model assumes fits it less well. Treat its odds as rougher still.
- Checked state lives in your browser's localStorage (key `svcd-checked`), which is per browser and per origin. Open `map.html` in a different browser, or from a different path, and it starts empty. If you care about what you have ticked off, use "Export checked" and import it where needed.
- `cache/streetview.sqlite` contains your API key inside the stored request URLs. It is gitignored. Do not share it.
- `data/`, `output/`, `cache/` and `takeout/` are gitignored because they hold your personal location data. Keep it that way.
- And, uh, I'm not a Python developer. Or a developer at all. So, you know, it could all break. (PRs welcomed.)

## Tests
```
.venv\Scripts\python -m pytest -q
```
(`.venv/bin/python -m pytest -q` on Linux or macOS.)
235 tests, no network needed: aioresponses mocks the API, and a shim in `tests/conftest.py` works around an aioresponses/aiohttp 3.14 incompatibility. The fixtures are synthetic, as they should be.

[^1]: The deadly snake, not the [gaming peripheral](https://en.wikipedia.org/wiki/List_of_Razer_products), though I can personally vouch that they're okay as mice go. (Mouses? Meese? Mice? Yeah, mice, probably.)
[^2]: The programming language, not a small snake, though having allies in the animal world is never a bad idea, especially if you're the aforementioned hunter.
[^3]: The [version control system](https://git-scm.com/), not, say, Jacob Rees-Mogg, who can get in the sea as far as I'm concerned.
