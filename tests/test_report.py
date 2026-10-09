import csv
import json
import re

import pytest

from svcd.models import Candidate
from svcd.report import CSV_COLUMNS, _heatWeights, buildMap, oddsText, oneIn, writeTable

HEADER = [
    "rank", "placeKey", "name", "month", "lat", "lng", "kind", "probability", "oneIn", "coverage",
    "visibility", "visitCount", "dwellMinutes", "panoCount", "panoUrls", "source", "checkKey",
]


def panoUrl(panoId: str) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&pano={panoId}"


def makePano(panoId: str, probability: float, url: str | None = None, **extra) -> dict:
    """extra: optional source and copyright, which older pano dicts lack."""
    return {"panoId": panoId, "date": "2022-08", "url": url if url is not None else panoUrl(panoId),
            "lat": 52.5, "lng": 13.4, "distanceM": 12.6, "probability": probability, **extra}


def makeCandidate(rank: int, probability: float, name: str | None = "Cafe <Ost>", lat: float = 52.5,
                  lng: float = 13.4, panos: list[dict] | None = None, month: str = "2022-08",
                  source: str = "google", checkKey: str | None = None) -> Candidate:
    if panos is None:
        panos = [makePano(f"P{rank}a", probability / 2), makePano(f"P{rank}best", probability)]
    if checkKey is None:
        checkKey = f"key-{rank}|{month}|{source}"
    return Candidate(rank=rank, placeKey=f"key-{rank}", name=name, month=month, lat=lat, lng=lng, kind="place",
                     probability=probability, coverage=0.2, visibility=0.5, visitCount=2, dwellMinutes=90.4,
                     panos=panos, source=source, checkKey=checkKey)


def makeUserCandidate(rank: int, probability: float, credit: str = "Fixture Contributor 3", **kwargs) -> Candidate:
    panos = [makePano(f"U{rank}", probability, source="user", copyright=credit)]
    return makeCandidate(rank, probability, panos=panos, source="user", **kwargs)


def readCsv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def dataBlob(page: str) -> str:
    match = re.search(r'<script type="application/json" id="svcd-data">(.*?)</script>', page, re.S)
    assert match, "candidate JSON blob missing"
    return match.group(1)


# ---------------------------------------------------------------- oneIn

@pytest.mark.parametrize("probability, expected", [
    (0.0, "very unlikely"),
    (-0.2, "very unlikely"),
    (0.5, "50%"),
    (0.9, "90%"),
    (0.4999, "1 in 2"),
    (0.1, "1 in 10"),
    (1 / 12, "1 in 12"),
    (0.00083, "1 in 1,200"),
    (0.000012345, "1 in 81,000"),
])
def testOneIn(probability, expected):
    assert oneIn(probability) == expected


def testOddsText():
    assert oddsText(0.5) == "about 50%"
    assert oddsText(0.9) == "about 90%"
    assert oddsText(0.1) == "about 1 in 10"
    assert oddsText(0.0) == "very unlikely"


# ---------------------------------------------------------------- writeTable

def testWriteTableCsvAndHtml(tmp_path):
    candidates = [makeCandidate(2, 0.01), makeCandidate(1, 0.1, name=None)]
    csvPath, htmlPath = tmp_path / "out" / "c.csv", tmp_path / "out" / "c.html"
    writeTable(candidates, csvPath, htmlPath)

    rows = readCsv(csvPath)
    assert rows[0] == HEADER == CSV_COLUMNS
    assert [r[0] for r in rows[1:]] == ["1", "2"]  # ordered by rank
    first = dict(zip(HEADER, rows[1]))
    assert first["oneIn"] == oneIn(0.1)
    assert first["name"] == ""
    assert first["panoCount"] == "2"
    assert first["panoUrls"] == f"{panoUrl('P1best')} {panoUrl('P1a')}"  # best pano first, space-joined

    page = htmlPath.read_text(encoding="utf-8")
    for c in candidates:
        for p in c.panos:
            assert f'href="{p["url"].replace("&", "&amp;")}"' in page
    assert ">Open<" in page and 'target="_blank"' in page and 'rel="noopener"' in page
    assert "about 1 in 10" in page and "about 1 in 100" in page
    assert "Cafe &lt;Ost&gt;" in page  # names are escaped
    assert page.index("key-1") < page.index("Cafe &lt;Ost&gt;")  # ordered by rank
    assert ">90<" in page  # dwell rounded
    assert "daylight dwell (min)" in page


def testWriteTableCsvHasSourceAndCheckKey(tmp_path):
    candidates = [makeCandidate(1, 0.2), makeUserCandidate(2, 0.1)]
    csvPath = tmp_path / "c.csv"
    writeTable(candidates, csvPath, tmp_path / "c.html")
    rows = readCsv(csvPath)
    assert "source" in rows[0] and "checkKey" in rows[0]
    byRank = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert byRank["1"]["source"] == "google"
    assert byRank["2"]["source"] == "user"
    assert byRank["1"]["checkKey"] == "key-1|2022-08|google"
    assert byRank["2"]["checkKey"] == "key-2|2022-08|user"


def testWriteTableDerivesMissingCheckKey(tmp_path):
    csvPath = tmp_path / "c.csv"
    writeTable([makeCandidate(1, 0.2, source="user", checkKey="")], csvPath, tmp_path / "c.html")
    assert dict(zip(HEADER, readCsv(csvPath)[1]))["checkKey"] == "key-1|2022-08|user"


def testWriteTableHtmlShowsSourceBadgesAndCredit(tmp_path):
    candidates = [makeCandidate(1, 0.2), makeUserCandidate(2, 0.1, credit="Fixture <Contributor> 3")]
    htmlPath = tmp_path / "c.html"
    writeTable(candidates, tmp_path / "c.csv", htmlPath)
    page = htmlPath.read_text(encoding="utf-8")
    assert "<th>source</th>" in page
    assert '<span class="badge google">Google</span>' in page
    assert '<span class="badge user">User photo</span>' in page
    assert '<span class="credit">Fixture &lt;Contributor&gt; 3</span>' in page  # credit line, escaped
    assert page.count('class="credit"') == 1  # Google panoramas carry no credit line


def testWriteTablePanoSourceFallsBackToCandidate(tmp_path):
    # pano dicts written before provenance existed have no source or copyright
    c = makeCandidate(1, 0.2, source="user", panos=[makePano("old", 0.2)])
    htmlPath = tmp_path / "c.html"
    writeTable([c], tmp_path / "c.csv", htmlPath)
    page = htmlPath.read_text(encoding="utf-8")
    assert page.count('<span class="badge user">User photo</span>') == 2  # source cell and the pano link
    assert 'class="credit"' not in page


def testWriteTableEmpty(tmp_path):
    csvPath, htmlPath = tmp_path / "c.csv", tmp_path / "c.html"
    writeTable([], csvPath, htmlPath)
    assert readCsv(csvPath) == [HEADER]
    assert "No candidates found" in htmlPath.read_text(encoding="utf-8")


def testWriteTableSkipsNonHttpsLinks(tmp_path):
    c = makeCandidate(1, 0.1, panos=[makePano("bad", 0.1, url="javascript:alert(1)")])
    htmlPath = tmp_path / "c.html"
    writeTable([c], tmp_path / "c.csv", htmlPath)
    assert "javascript:alert" not in htmlPath.read_text(encoding="utf-8")


# ---------------------------------------------------------------- buildMap

def testBuildMapEmbedsDataAndDrawer(tmp_path):
    candidates = [makeCandidate(rank, 0.3 / rank, name=f"Place {rank}", lat=52.5 + rank / 1000) for rank in (3, 1, 2)]
    path = tmp_path / "maps" / "map.html"
    buildMap(candidates, path)
    page = path.read_text(encoding="utf-8")

    records = json.loads(dataBlob(page))
    assert [r["rank"] for r in records] == [1, 2, 3]
    assert records[0]["oneIn"] == oneIn(0.3)
    assert records[0]["panos"][0]["panoId"] == "P1best"  # best pano first
    assert panoUrl("P1best") in page
    assert "zoomToShowLayer" in page
    assert "Show on map" in page and "Open panorama" in page and "Panorama" in page
    assert "PAGE_SIZE = 20" in page
    assert "MarkerCluster" in page and "heatLayer" in page
    assert 'id="svcd-drawer"' in page and 'id="svcd-filter"' in page
    assert "3 candidates" in page
    assert "#rank-" in page and "history.replaceState" in page
    assert "prefers-color-scheme: dark" in page
    assert "innerHTML" not in page.split('id="svcd-data"')[1]  # drawer JS never uses innerHTML
    assert "—" not in page  # no em-dashes


def testBuildMapDrawerLayout(tmp_path):
    path = tmp_path / "map.html"
    buildMap([makeCandidate(1, 0.3), makeUserCandidate(2, 0.2)], path)
    page = path.read_text(encoding="utf-8")
    drawer = page.split('id="svcd-drawer"')[1].split("</aside>")[0]
    assert '<h1 id="svcd-title">Streetview Finder</h1>' in drawer
    assert 'id="svcd-count" aria-live="polite">2 candidates</p>' in drawer
    # the name/month filter is collapsed by default and says when it is active
    assert '<details class="svcd-disclosure" id="svcd-filter-box">' in drawer
    assert '<summary id="svcd-filter-summary">Filter</summary>' in drawer and "Filter: active" in page
    assert drawer.index('id="svcd-filter-box"') < drawer.index('id="svcd-filter"') < drawer.index("</details>")
    assert "Manage checked marks" in drawer
    # the rank-third legend and colours are gone; one marker colour plus grey for checked
    for gone in ("top third", "middle third", "bottom third", "svcd-legend", "svcd-b0", "bucket"):
        assert gone not in page
    assert "svcd-pin-dot" in page and "svcd-grey" in page
    assert "Ranked by" not in drawer and "ranking" not in drawer.lower()
    # one font stack and tabular numerals; popup styles are scoped
    assert '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif' in page
    assert "tabular-nums" in page and ".leaflet-container .svcd-popup" in page
    assert "min in daylight" in page and "maxWidth: 340" in page


def testBuildMapReferencesFoliumObjects(tmp_path):
    path = tmp_path / "map.html"
    buildMap([makeCandidate(1, 0.2)], path)
    page = path.read_text(encoding="utf-8")
    mapName = re.search(r"var (map_[0-9a-f]+) = L\.map\(", page).group(1)
    clusterName = re.search(r"var (marker_cluster_[0-9a-f]+) = L\.markerClusterGroup\(", page).group(1)
    assert f"var map = {mapName};" in page
    assert f"var cluster = {clusterName};" in page
    assert "__SVCD_" not in page
    # the drawer script runs after the cluster group exists
    assert page.index(f"var {clusterName} =") < page.index(f"var cluster = {clusterName};")


def testBuildMapNeutralisesScriptAndTemplateLiteralCharacters(tmp_path):
    name = "Bar </script><script>alert(1)</script> `${alert(2)}` & Grill"
    path = tmp_path / "map.html"
    buildMap([makeCandidate(1, 0.2, name=name)], path)
    page = path.read_text(encoding="utf-8")
    blob = dataBlob(page)  # the regex stops at the first </script>, so the blob must not contain one
    assert "</script" not in blob and "`" not in blob and "${" not in blob
    assert json.loads(blob)[0]["name"] == name
    assert "`${alert(2)}`" not in page


def testBuildMapProvenanceAndCheckedState(tmp_path):
    candidates = [makeCandidate(1, 0.3), makeUserCandidate(2, 0.2), makeCandidate(3, 0.1, panos=[makePano("x", 0.1)])]
    path = tmp_path / "map.html"
    buildMap(candidates, path)
    page = path.read_text(encoding="utf-8")

    for text in ("svcd-checked", "Export checked", "Import checked", "Clear checked", "User photo", "Google"):
        assert text in page
    # three-way provenance dropdown and the checked dropdown, each with a visible label
    assert '<select id="svcd-source-filter"' in page and '<label for="svcd-source-filter">Imagery</label>' in page
    for value in ("all", "google", "user"):
        assert f'<option value="{value}"' in page
    assert "Google only" in page and "User photos only" in page
    assert '<select id="svcd-checked-filter"' in page and '<label for="svcd-checked-filter">Checked</label>' in page
    for value in ("unchecked", "checked"):
        assert f'<option value="{value}">' in page
    assert 'type="radio"' not in page
    # the export, import and clear buttons sit in a collapsed disclosure
    manage = page.split('id="svcd-manage"')[1].split("</details>")[0]
    assert "<summary>Manage checked marks</summary>" in manage and 'id="svcd-export"' in manage
    assert '<details class="svcd-disclosure svcd-manage" id="svcd-manage">' in page  # no open attribute
    assert "svcd-checked.json" in page and "localStorage" in page and "confirm(" in page

    records = {r["rank"]: r for r in json.loads(dataBlob(page))}
    assert records[1]["source"] == "google" and records[1]["checkKey"] == "key-1|2022-08|google"
    assert records[2]["source"] == "user" and records[2]["checkKey"] == "key-2|2022-08|user"
    assert records[2]["panos"][0]["source"] == "user"
    assert records[2]["panos"][0]["copyright"] == "Fixture Contributor 3"
    assert records[3]["panos"][0]["source"] == "google"  # falls back to the candidate's source
    assert records[3]["panos"][0]["copyright"] is None
    assert [records[r]["heat"] for r in (1, 2, 3)] == pytest.approx([1.0, 0.55, 0.1])

    # the drawer script can rebuild the heat layer folium created
    heatName = re.search(r"var (heat_map_[0-9a-f]+) = L\.heatLayer\(", page).group(1)
    assert f"var heat = {heatName};" in page
    assert "setLatLngs" in page


def testBuildMapHasSharingMode(tmp_path):
    path = tmp_path / "map.html"
    buildMap([makeCandidate(1, 0.3, name=None), makeUserCandidate(2, 0.2)], path)
    page = path.read_text(encoding="utf-8")
    for text in ("Sharing mode", 'id="svcd-sharing"', 'id="svcd-sharing-badge"', "Unknown location",
                 "Contributor hidden"):
        assert text in page
    # the info button and its popover explain the toggle
    assert 'aria-label="What sharing mode does"' in page
    assert 'role="note"' in page
    assert "Sharing mode hides details so you can share a screenshot." in page


def testBuildMapPopupHasOneSourceBadge(tmp_path):
    path = tmp_path / "map.html"
    buildMap([makeUserCandidate(1, 0.2)], path)
    page = path.read_text(encoding="utf-8")
    # the popup heading carries the badge; panorama rows no longer repeat it
    assert "head.appendChild(badge(c.source));" in page
    assert "badge(panoSource(" not in page


def testBuildMapEmptyHasNoHeatLayer(tmp_path):
    path = tmp_path / "map.html"
    buildMap([], path)
    page = path.read_text(encoding="utf-8")
    assert "var heat = null;" in page and "__SVCD_" not in page


def testBuildMapNeutralisesCopyright(tmp_path):
    credit = "Fixture </script><script>alert(1)</script> `${alert(2)}` Contributor"
    path = tmp_path / "map.html"
    buildMap([makeUserCandidate(1, 0.2, credit=credit)], path)
    page = path.read_text(encoding="utf-8")
    blob = dataBlob(page)
    assert "</script" not in blob and "`" not in blob and "${" not in blob
    assert json.loads(blob)[0]["panos"][0]["copyright"] == credit
    assert "`${alert(2)}`" not in page


def testBuildMapDropsNonHttpsPanoUrls(tmp_path):
    c = makeCandidate(1, 0.2, panos=[makePano("bad", 0.2, url="javascript:alert(1)"), makePano("ok", 0.1)])
    path = tmp_path / "map.html"
    buildMap([c], path)
    page = path.read_text(encoding="utf-8")
    assert "javascript:alert" not in page
    records = json.loads(dataBlob(page))
    assert records[0]["panos"][0]["url"] is None
    assert records[0]["panos"][1]["url"] == panoUrl("ok")
    assert 'url.startsWith("https://")' in page  # the JS checks again before building any link


def testBuildMapEmpty(tmp_path):
    path = tmp_path / "empty.html"
    buildMap([], path)
    page = path.read_text(encoding="utf-8")
    assert "<html" in page.lower()
    assert "52.52" in page and "13.405" in page
    assert json.loads(dataBlob(page)) == []
    assert "No candidates found" in page
    assert "heatLayer" not in page


def testBuildMapSingleCandidate(tmp_path):
    path = tmp_path / "one.html"
    buildMap([makeCandidate(1, 0.25, panos=[makePano("solo", 0.25)])], path)
    page = path.read_text(encoding="utf-8")
    assert "map_action=pano&pano=solo" in page
    assert "1 candidate<" in page


def testHeatWeightsKeepWeakestPointVisible():
    weights = _heatWeights([makeCandidate(1, 0.5), makeCandidate(2, 0.05), makeCandidate(3, 0.0)])
    assert min(weights) == pytest.approx(0.1) and max(weights) == pytest.approx(1.0)
    assert _heatWeights([makeCandidate(1, 0.2), makeCandidate(2, 0.2)]) == [1.0, 1.0]


def testBuildMapDefaultsToKeylessEsriTilesWithAlternatives(tmp_path):
    # Opened as a local file, the browser sends no Referer, and OSM's tile server then refuses the
    # request. The default basemap must work without a Referer or an API key.
    path = tmp_path / "map.html"
    buildMap([makeCandidate(1, 0.1)], path)
    page = path.read_text(encoding="utf-8")
    assert "server.arcgisonline.com" in page
    assert "basemaps.cartocdn.com" in page and "tile.openstreetmap.org" in page
    assert page.index("arcgisonline") < page.index("cartocdn") < page.index("tile.openstreetmap.org")
