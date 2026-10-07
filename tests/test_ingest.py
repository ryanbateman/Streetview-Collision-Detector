import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from svcd.ingest import detectFormat, ingestVisits
from svcd.ingest.semanticHistory import parseSemanticHistory
from svcd.ingest.timelineExport import parseLatLng, parseTimelineExport
from svcd.models import Visit


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


SEMANTIC_EXPECTED = [
    Visit("ChIJfixture001", "Brandenburg Gate", "Pariser Platz, 10117 Berlin, Germany",
          52.5163, 13.3777, utc(2022, 8, 5, 9, 0), utc(2022, 8, 5, 11, 30), "semantic"),
    Visit("ChIJfixture002", None, None,
          52.5208, 13.4094, utc(2022, 8, 12, 14, 0), utc(2022, 8, 12, 15, 15), "semantic"),
    Visit("ChIJfixture005", "Reichstag Building", "Platz der Republik 1, 11011 Berlin, Germany",
          52.5186, 13.3762, utc(2022, 8, 31, 22, 0), utc(2022, 9, 1, 1, 30), "semantic"),
    Visit("ChIJfixture006", "Southern Hemisphere Fixture", "Fixture Street 1, Sydney, Australia",
          -33.9, 151.2, utc(2022, 9, 10, 8, 0), utc(2022, 9, 10, 9, 45), "semantic"),
]

TIMELINE_EXPECTED = [
    Visit("ChIJfixture101", None, None, 52.5163, 13.3777,
          utc(2025, 3, 1, 9, 0), utc(2025, 3, 1, 11, 30), "timeline"),
    Visit("ChIJfixture102", None, None, 52.5208, 13.4094,
          utc(2025, 3, 30, 0, 30), utc(2025, 3, 30, 2, 0), "timeline"),
    Visit("ChIJfixture103", None, None, 40.7580, -73.9855,
          utc(2025, 4, 2, 12, 0), utc(2025, 4, 2, 13, 30), "timeline"),
]


@pytest.fixture
def semanticDir(fixturesDir: Path) -> Path:
    return fixturesDir / "semantic_fixture" / "Semantic Location History"


# Semantic Location History (old Takeout)

def test_semanticYieldsExpectedVisitsInFileOrder(semanticDir: Path) -> None:
    assert list(parseSemanticHistory(semanticDir)) == SEMANTIC_EXPECTED


def test_semanticAcceptsParentFolders(semanticDir: Path, tmp_path: Path) -> None:
    assert list(parseSemanticHistory(semanticDir.parent)) == SEMANTIC_EXPECTED
    # Takeout layout: <takeout>/Location History (Timeline)/Semantic Location History
    nested = tmp_path / "Location History (Timeline)" / "Semantic Location History" / "2022"
    nested.mkdir(parents=True)
    source = semanticDir / "2022" / "2022_SEPTEMBER.json"
    (nested / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert list(parseSemanticHistory(tmp_path)) == SEMANTIC_EXPECTED[-1:]


def test_semanticDatetimesAreUtcAware(semanticDir: Path) -> None:
    for visit in parseSemanticHistory(semanticDir):
        assert visit.start.utcoffset().total_seconds() == 0
        assert visit.end.utcoffset().total_seconds() == 0


def test_semanticMonthBoundaryVisit(semanticDir: Path) -> None:
    crossing = [v for v in parseSemanticHistory(semanticDir) if v.start.month != v.end.month]
    assert len(crossing) == 1
    assert crossing[0].start == utc(2022, 8, 31, 22, 0)
    assert crossing[0].end == utc(2022, 9, 1, 1, 30)


def test_semanticSkipsBadVisitsWithOneWarningPerFile(semanticDir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        visits = list(parseSemanticHistory(semanticDir))
    placeIds = {v.placeId for v in visits}
    assert "ChIJfixture003" not in placeIds  # missing coordinates
    assert "ChIJfixture004" not in placeIds  # end before start
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "2022_AUGUST.json" in warnings[0].getMessage()
    assert "skipped 2" in warnings[0].getMessage()


def test_semanticMissingFolderRaises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        list(parseSemanticHistory(tmp_path))


# Timeline export (on-device)

def test_timelineDegreesFixture(fixturesDir: Path) -> None:
    assert list(parseTimelineExport(fixturesDir / "timeline_degrees.json")) == TIMELINE_EXPECTED


def test_timelineGeoFixtureMatchesDegrees(fixturesDir: Path) -> None:
    degrees = list(parseTimelineExport(fixturesDir / "timeline_degrees.json"))
    geo = list(parseTimelineExport(fixturesDir / "timeline_geo.json"))
    assert geo == degrees


def test_timelineSkipsActivityAndPathSegments(fixturesDir: Path) -> None:
    visits = list(parseTimelineExport(fixturesDir / "timeline_degrees.json"))
    assert len(visits) == 3
    assert all(v.source == "timeline" and v.name is None and v.address is None for v in visits)


def test_timelineOffsetsNormalisedToUtc(fixturesDir: Path) -> None:
    for visit in parseTimelineExport(fixturesDir / "timeline_degrees.json"):
        assert visit.start.tzinfo is not None
        assert visit.start.utcoffset().total_seconds() == 0
        assert visit.end.utcoffset().total_seconds() == 0


def test_timelineMalformedVisitSkippedWithWarning(fixturesDir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        visits = list(parseTimelineExport(fixturesDir / "timeline_geo.json"))
    assert "ChIJfixture104" not in {v.placeId for v in visits}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "skipped 1" in warnings[0].getMessage()


def test_timelineSkipsEndBeforeStart(tmp_path: Path) -> None:
    path = tmp_path / "Timeline.json"
    path.write_text(json.dumps({"semanticSegments": [
        {"startTime": "2025-01-01T10:00:00+00:00", "endTime": "2025-01-01T09:00:00+00:00",
         "visit": {"topCandidate": {"placeId": "ChIJfixture900", "placeLocation": {"latLng": "1.0,2.0"}}}},
    ]}), encoding="utf-8")
    assert list(parseTimelineExport(path)) == []


# parseLatLng

@pytest.mark.parametrize("value", [
    "50.0506312°, 14.3439906°",
    "geo:50.0506312,14.3439906",
    "50.0506312,14.3439906",
    " 50.0506312 , 14.3439906 ",
])
def test_parseLatLngEncodings(value: str) -> None:
    assert parseLatLng(value) == (50.0506312, 14.3439906)


def test_parseLatLngNegative() -> None:
    assert parseLatLng("-33.9°, -151.2°") == (-33.9, -151.2)
    assert parseLatLng("geo:-33.9,151.2") == (-33.9, 151.2)


@pytest.mark.parametrize("value", ["", "garbage", "50.05", "50.05;14.34", "geo:", "abc°, def°", "95.0,14.0", "1,2,3"])
def test_parseLatLngGarbageRaises(value: str) -> None:
    with pytest.raises(ValueError):
        parseLatLng(value)


def test_parseLatLngNonStringRaises() -> None:
    with pytest.raises(ValueError):
        parseLatLng(None)  # type: ignore[arg-type]


# detectFormat / ingestVisits

def test_detectFormatSemanticFolderAndParents(semanticDir: Path) -> None:
    assert detectFormat(semanticDir) == "semantic"
    assert detectFormat(semanticDir.parent) == "semantic"
    assert detectFormat(semanticDir.parent.parent) == "semantic"  # two levels up


def test_detectFormatTimelineFile(fixturesDir: Path) -> None:
    assert detectFormat(fixturesDir / "timeline_degrees.json") == "timeline"
    assert detectFormat(fixturesDir / "timeline_geo.json") == "timeline"


def test_detectFormatUnrelatedPathsRaise(tmp_path: Path, semanticDir: Path) -> None:
    with pytest.raises(ValueError):
        detectFormat(tmp_path)  # empty directory
    with pytest.raises(ValueError):
        detectFormat(tmp_path / "does-not-exist.json")
    other = tmp_path / "Settings.json"
    other.write_text('{"createdTime": "2020-01-01T00:00:00Z"}', encoding="utf-8")
    with pytest.raises(ValueError):
        detectFormat(other)
    notJson = tmp_path / "notes.txt"
    notJson.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        detectFormat(notJson)
    with pytest.raises(ValueError):
        detectFormat(semanticDir / "2022" / "2022_AUGUST.json")  # semantic month file, not a timeline export


def test_ingestVisitsSortsByStart(fixturesDir: Path) -> None:
    visits = ingestVisits(fixturesDir / "semantic_fixture")
    assert visits == sorted(SEMANTIC_EXPECTED, key=lambda v: v.start)
    timeline = ingestVisits(fixturesDir / "timeline_geo.json")
    assert timeline == TIMELINE_EXPECTED
