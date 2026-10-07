import csv
import json
import re

import pytest

from svcd.models import Candidate
from svcd.report import CSV_COLUMNS, _heatWeights, buildMap, oddsText, oneIn, writeTable

HEADER = [
    "rank", "placeKey", "name", "month", "lat", "lng", "kind", "probability", "oneIn", "coverage",
    "visibility", "visitCount", "dwellMinutes", "panoCount", "panoUrls",
]


def panoUrl(panoId: str) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&pano={panoId}"


def makePano(panoId: str, probability: float, url: str | None = None) -> dict:
    return {"panoId": panoId, "date": "2022-08", "url": url if url is not None else panoUrl(panoId),
            "lat": 52.5, "lng": 13.4, "distanceM": 12.6, "probability": probability}


def makeCandidate(rank: int, probability: float, name: str | None = "Cafe <Ost>", lat: float = 52.5,
                  lng: float = 13.4, panos: list[dict] | None = None, month: str = "2022-08") -> Candidate:
    if panos is None:
        panos = [makePano(f"P{rank}a", probability / 2), makePano(f"P{rank}best", probability)]
    return Candidate(rank=rank, placeKey=f"key-{rank}", name=name, month=month, lat=lat, lng=lng, kind="place",
                     probability=probability, coverage=0.2, visibility=0.5, visitCount=2, dwellMinutes=90.4,
                     panos=panos)


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
