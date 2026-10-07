import csv
import dataclasses

import pytest

from svcd.match import buildPanoUrl
from svcd.models import Collision
from svcd.report import buildMap, writeTable

FIELDS = [field.name for field in dataclasses.fields(Collision)]


def makeCollision(panoId: str, score: float, name: str | None = "Cafe <Ost>", lat: float = 52.5,
                  lng: float = 13.4) -> Collision:
    return Collision(placeKey=f"key-{panoId}", name=name, month="2022-08", panoId=panoId, panoDate="2022-08",
                     panoLat=lat, panoLng=lng, distanceM=12.6, visitCount=2, dwellMinutes=90.4, score=score,
                     url=buildPanoUrl(panoId, lat, lng))


def readCsv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def testWriteTableCsvAndHtml(tmp_path):
    collisions = [makeCollision("low", 1.0), makeCollision("high", 3.0, name=None)]
    csvPath, htmlPath = tmp_path / "out" / "c.csv", tmp_path / "out" / "c.html"
    writeTable(collisions, csvPath, htmlPath)

    rows = readCsv(csvPath)
    assert rows[0] == FIELDS
    assert [r[FIELDS.index("panoId")] for r in rows[1:]] == ["high", "low"]

    page = htmlPath.read_text(encoding="utf-8")
    for c in collisions:
        assert f'href="{c.url.replace("&", "&amp;")}"' in page
    assert "Open panorama" in page
    assert 'target="_blank"' in page
    assert "Cafe &lt;Ost&gt;" in page  # names are escaped
    assert "key-high" in page  # missing name falls back to placeKey
    assert page.index("key-high") < page.index("Cafe &lt;Ost&gt;")  # pre-sorted by score
    assert ">90<" in page and ">13<" in page  # dwell and distance rounded


def testWriteTableEmpty(tmp_path):
    csvPath, htmlPath = tmp_path / "c.csv", tmp_path / "c.html"
    writeTable([], csvPath, htmlPath)
    assert readCsv(csvPath) == [FIELDS]
    assert "No collisions" in htmlPath.read_text(encoding="utf-8")


def testBuildMapWritesMarkersAndHeatmap(tmp_path):
    path = tmp_path / "maps" / "heatmap.html"
    buildMap([makeCollision("a", 1.0), makeCollision("b", 2.0, lat=52.6)], path)
    page = path.read_text(encoding="utf-8")
    assert "map_action=pano&pano=" in page
    assert "MarkerCluster" in page
    assert "heatLayer" in page
    assert "Open panorama" in page


def testBuildMapEmpty(tmp_path):
    path = tmp_path / "empty.html"
    buildMap([], path)
    page = path.read_text(encoding="utf-8")
    assert "<html" in page.lower()
    assert "52.52" in page and "13.405" in page


def testBuildMapSingleCollision(tmp_path):
    path = tmp_path / "one.html"
    buildMap([makeCollision("solo", 2.5)], path)
    assert "map_action=pano&pano=solo" in path.read_text(encoding="utf-8")


def testBuildMapNeutralisesTemplateLiteralCharactersInNames(tmp_path):
    path = tmp_path / "map.html"
    buildMap([makeCollision("a", 1.0, name="Bar `${alert(1)}` & Grill")], path)
    page = path.read_text(encoding="utf-8")
    assert "`${alert(1)}`" not in page
    assert "&#96;&#36;{alert(1)}&#96;" in page


def testHeatWeightsKeepWeakestPointVisible():
    from svcd.report import _heatWeights
    weights = _heatWeights([makeCollision("a", -3.0), makeCollision("b", 0.0), makeCollision("c", 5.0)])
    assert min(weights) == pytest.approx(0.1) and max(weights) == pytest.approx(1.0)
