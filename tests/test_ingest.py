import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from svcd.ingest import detectFormat, ingestVisits
from svcd.ingest.semanticHistory import parseSemanticHistory
from svcd.ingest.timelineExport import parseLatLng, parseTimelineExport
from svcd.models import (
    IMPORTANCE_MAIN,
    IMPORTANCE_TRANSITIONAL,
    KIND_CYCLING,
    KIND_PLACE,
    KIND_WALKING,
    SEMANTIC_HOME,
    SEMANTIC_WORK,
    Visit,
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


HOME, WORK, MAIN, TRANS = SEMANTIC_HOME, SEMANTIC_WORK, IMPORTANCE_MAIN, IMPORTANCE_TRANSITIONAL


def path(lat: float, lng: float, start: datetime, end: datetime, kind: str = KIND_WALKING,
         source: str = "semantic") -> Visit:
    return Visit(None, None, None, lat, lng, start, end, source, kind)


SEMANTIC_AUGUST = [
    Visit("ChIJfixture001", "Brandenburg Gate", "Pariser Platz, 10117 Berlin, Germany",
          52.5163, 13.3777, utc(2022, 8, 5, 9, 0), utc(2022, 8, 5, 11, 30), "semantic"),
    # WALKING segment with neither path: start and end locations, half the duration each
    path(52.5163, 13.3777, utc(2022, 8, 5, 11, 30), utc(2022, 8, 5, 11, 42, 30)),
    path(52.5208, 13.4094, utc(2022, 8, 5, 11, 42, 30), utc(2022, 8, 5, 11, 55)),
    Visit("ChIJfixture002", None, None,
          52.5208, 13.4094, utc(2022, 8, 12, 14, 0), utc(2022, 8, 12, 15, 15), "semantic", KIND_PLACE, HOME, MAIN),
    Visit("ChIJfixture005", "Reichstag Building", "Platz der Republik 1, 11011 Berlin, Germany",
          52.5186, 13.3762, utc(2022, 8, 31, 22, 0), utc(2022, 9, 1, 1, 30), "semantic", KIND_PLACE, WORK, TRANS),
]

SEMANTIC_SEPTEMBER = [
    Visit("ChIJfixture006", "Southern Hemisphere Fixture", "Fixture Street 1, Sydney, Australia",
          -33.9, 151.2, utc(2022, 9, 10, 8, 0), utc(2022, 9, 10, 9, 45), "semantic", KIND_PLACE, None, MAIN),
    # WALKING with simplifiedRawPath (preferred over waypointPath): each leg ends at the next point
    path(-33.9, 151.2, utc(2022, 9, 10, 10, 0), utc(2022, 9, 10, 10, 5)),
    path(-33.89, 151.205, utc(2022, 9, 10, 10, 5), utc(2022, 9, 10, 10, 12)),
    path(-33.88, 151.21, utc(2022, 9, 10, 10, 12), utc(2022, 9, 10, 10, 20)),
    # RUNNING with waypointPath only: three equal windows over 10 minutes
    path(-33.88, 151.21, utc(2022, 9, 10, 11, 0), utc(2022, 9, 10, 11, 3, 20)),
    path(-33.87, 151.215, utc(2022, 9, 10, 11, 3, 20), utc(2022, 9, 10, 11, 6, 40)),
    path(-33.86, 151.22, utc(2022, 9, 10, 11, 6, 40), utc(2022, 9, 10, 11, 10)),
    # CYCLING with neither path
    path(-33.86, 151.22, utc(2022, 9, 10, 12, 0), utc(2022, 9, 10, 12, 15), KIND_CYCLING),
    path(-33.84, 151.23, utc(2022, 9, 10, 12, 15), utc(2022, 9, 10, 12, 30), KIND_CYCLING),
    # IN_TRAIN and type-less segments are skipped
]

SEMANTIC_EXPECTED = SEMANTIC_AUGUST + SEMANTIC_SEPTEMBER

TIMELINE_EXPECTED = [
    Visit("ChIJfixture101", None, None, 52.5163, 13.3777,
          utc(2025, 3, 1, 9, 0), utc(2025, 3, 1, 11, 30), "timeline"),
    # WALKING activity: start and end points, half the duration each
    path(52.5163, 13.3777, utc(2025, 3, 1, 11, 30), utc(2025, 3, 1, 11, 42, 30), source="timeline"),
    path(52.5208, 13.4094, utc(2025, 3, 1, 11, 42, 30), utc(2025, 3, 1, 11, 55), source="timeline"),
    Visit("ChIJfixture102", None, None, 52.5208, 13.4094,
          utc(2025, 3, 30, 0, 30), utc(2025, 3, 30, 2, 0), "timeline", KIND_PLACE, HOME),
    Visit("ChIJfixture103", None, None, 40.7580, -73.9855,
          utc(2025, 4, 2, 12, 0), utc(2025, 4, 2, 13, 30), "timeline", KIND_PLACE, WORK),
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
    assert list(parseSemanticHistory(tmp_path)) == SEMANTIC_SEPTEMBER


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
    assert "skipped 2 place visits and 1 walking/cycling segments" in warnings[0].getMessage()


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


def test_timelineSkipsNonWalkingActivityAndPathSegments(fixturesDir: Path) -> None:
    visits = list(parseTimelineExport(fixturesDir / "timeline_degrees.json"))
    # 3 place visits + 2 walking points; the driving, type-less and timelinePath segments add nothing
    assert len(visits) == 5
    assert [v.kind for v in visits].count(KIND_WALKING) == 2
    assert all(v.source == "timeline" and v.name is None and v.address is None for v in visits)
    assert all(v.start.hour != 14 and v.start.hour != 15 for v in visits)  # driving / type-less times (UTC)


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
    assert "skipped 1 visit segments and 0 walking/cycling segments" in warnings[0].getMessage()


def test_timelineSkipsEndBeforeStart(tmp_path: Path) -> None:
    path = tmp_path / "Timeline.json"
    path.write_text(json.dumps({"semanticSegments": [
        {"startTime": "2025-01-01T10:00:00+00:00", "endTime": "2025-01-01T09:00:00+00:00",
         "visit": {"topCandidate": {"placeId": "ChIJfixture900", "placeLocation": {"latLng": "1.0,2.0"}}}},
    ]}), encoding="utf-8")
    assert list(parseTimelineExport(path)) == []


# Semantic type, importance and outdoor path points

def test_semanticPlaceVisitsCarrySemanticTypeAndImportance(semanticDir: Path) -> None:
    places = {v.placeId: v for v in parseSemanticHistory(semanticDir) if v.kind == KIND_PLACE}
    assert (places["ChIJfixture001"].semanticType, places["ChIJfixture001"].importance) == (None, None)
    assert (places["ChIJfixture002"].semanticType, places["ChIJfixture002"].importance) == (HOME, MAIN)
    assert (places["ChIJfixture005"].semanticType, places["ChIJfixture005"].importance) == (WORK, TRANS)
    # TYPE_SEARCHED_ADDRESS maps to None
    assert (places["ChIJfixture006"].semanticType, places["ChIJfixture006"].importance) == (None, MAIN)


def test_timelinePlaceVisitsCarrySemanticType(fixturesDir: Path) -> None:
    places = {v.placeId: v for v in parseTimelineExport(fixturesDir / "timeline_degrees.json") if v.kind == KIND_PLACE}
    assert places["ChIJfixture101"].semanticType is None  # UNKNOWN
    assert places["ChIJfixture102"].semanticType == HOME  # INFERRED_HOME
    assert places["ChIJfixture103"].semanticType == WORK
    assert all(v.importance is None for v in places.values())


def writeSemanticMonth(tmp_path: Path, timelineObjects: list[dict]) -> Path:
    month = tmp_path / "Semantic Location History" / "2023"
    month.mkdir(parents=True)
    (month / "2023_MAY.json").write_text(json.dumps({"timelineObjects": timelineObjects}), encoding="utf-8")
    return tmp_path


def segment(activityType: str | None, start: str, end: str, **extra: object) -> dict:
    body: dict = {
        "startLocation": {"latitudeE7": 100000000, "longitudeE7": 200000000},
        "endLocation": {"latitudeE7": 110000000, "longitudeE7": 210000000},
        "duration": {"startTimestamp": start, "endTimestamp": end},
        **extra,
    }
    if activityType is not None:
        body["activityType"] = activityType
    return {"activitySegment": body}


def test_semanticRawPathLegsEndAtNextPoint(tmp_path: Path) -> None:
    points = [
        {"latE7": 100000000, "lngE7": 200000000, "timestamp": "2023-05-01T10:00:00Z"},
        {"latE7": 105000000, "lngE7": 205000000, "timestamp": "2023-05-01T10:07:00Z"},
    ]
    root = writeSemanticMonth(tmp_path, [segment(
        "ON_FOOT", "2023-05-01T10:00:00Z", "2023-05-01T10:30:00Z", simplifiedRawPath={"points": points},
        waypointPath={"waypoints": [{"latE7": 1, "lngE7": 1}]})])
    assert list(parseSemanticHistory(root)) == [
        path(10.0, 20.0, utc(2023, 5, 1, 10, 0), utc(2023, 5, 1, 10, 7)),
        path(10.5, 20.5, utc(2023, 5, 1, 10, 7), utc(2023, 5, 1, 10, 22)),  # 23 min capped at 15
    ]


def test_semanticRawPathLegsStayInsideSegment(tmp_path: Path) -> None:
    # The first raw point comes after the segment start; the last comes after the segment end.
    points = [
        {"latE7": 100000000, "lngE7": 200000000, "timestamp": "2023-05-01T10:05:00Z"},
        {"latE7": 105000000, "lngE7": 205000000, "timestamp": "2023-05-01T10:12:00Z"},
        {"latE7": 110000000, "lngE7": 210000000, "timestamp": "2023-05-01T10:40:00Z"},
    ]
    root = writeSemanticMonth(tmp_path, [segment(
        "WALKING", "2023-05-01T10:00:00Z", "2023-05-01T10:20:00Z", simplifiedRawPath={"points": points})])
    visits = list(parseSemanticHistory(root))
    assert [(v.start, v.end) for v in visits] == [
        (utc(2023, 5, 1, 10, 0), utc(2023, 5, 1, 10, 12)),
        (utc(2023, 5, 1, 10, 12), utc(2023, 5, 1, 10, 20)),
        (utc(2023, 5, 1, 10, 20), utc(2023, 5, 1, 10, 20)),
    ]
    assert all(utc(2023, 5, 1, 10, 0) <= v.start <= v.end <= utc(2023, 5, 1, 10, 20) for v in visits)


def test_semanticWaypointWindowsAreCapped(tmp_path: Path) -> None:
    waypoints = [{"latE7": 100000000, "lngE7": 200000000}, {"latE7": 110000000, "lngE7": 210000000}]
    root = writeSemanticMonth(tmp_path, [segment(
        "WALKING", "2023-05-01T10:00:00Z", "2023-05-01T12:00:00Z", waypointPath={"waypoints": waypoints})])
    assert [(v.start, v.end) for v in parseSemanticHistory(root)] == [
        (utc(2023, 5, 1, 10, 0), utc(2023, 5, 1, 10, 15)),
        (utc(2023, 5, 1, 11, 0), utc(2023, 5, 1, 11, 15)),
    ]


def test_semanticWaypointWindowsTileSegmentExactly(tmp_path: Path) -> None:
    waypoints = [{"latE7": 100000000 + k, "lngE7": 200000000} for k in range(7)]
    root = writeSemanticMonth(tmp_path, [segment(
        "WALKING", "2023-05-01T10:00:00Z", "2023-05-01T10:10:00Z", waypointPath={"waypoints": waypoints})])
    visits = list(parseSemanticHistory(root))
    assert len(visits) == 7
    assert all(v.kind == KIND_WALKING and v.placeId is None and v.source == "semantic" for v in visits)
    assert visits[0].start == utc(2023, 5, 1, 10, 0)
    assert visits[-1].end == utc(2023, 5, 1, 10, 10)
    assert all(a.end == b.start for a, b in zip(visits, visits[1:]))
    durations = [(v.end - v.start).total_seconds() for v in visits]
    assert max(durations) - min(durations) < 1e-3


def test_semanticSegmentWithoutPathsYieldsStartAndEnd(tmp_path: Path) -> None:
    root = writeSemanticMonth(tmp_path, [segment("ON_BICYCLE", "2023-05-01T10:00:00Z", "2023-05-01T11:00:00Z")])
    assert list(parseSemanticHistory(root)) == [
        path(10.0, 20.0, utc(2023, 5, 1, 10, 0), utc(2023, 5, 1, 10, 15), KIND_CYCLING),
        path(11.0, 21.0, utc(2023, 5, 1, 10, 30), utc(2023, 5, 1, 10, 45), KIND_CYCLING),
    ]


def test_semanticSkipsNonOutdoorSegmentsSilently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = writeSemanticMonth(tmp_path, [
        segment(kind, "2023-05-01T10:00:00Z", "2023-05-01T11:00:00Z")
        for kind in ("IN_TRAIN", "IN_PASSENGER_VEHICLE", "FLYING", "UNKNOWN_ACTIVITY_TYPE", None)
    ])
    with caplog.at_level(logging.WARNING):
        assert list(parseSemanticHistory(root)) == []
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_semanticInvalidOutdoorSegmentsCountedInOneWarning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = writeSemanticMonth(tmp_path, [
        segment("WALKING", "2023-05-01T11:00:00Z", "2023-05-01T10:00:00Z"),  # end before start
        {"activitySegment": {"activityType": "RUNNING", "startLocation": {"latitudeE7": 1, "longitudeE7": 1}}},
    ])
    with caplog.at_level(logging.WARNING):
        assert list(parseSemanticHistory(root)) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "skipped 0 place visits and 2 walking/cycling segments" in warnings[0].getMessage()


def writeTimeline(tmp_path: Path, segments: list[dict]) -> Path:
    target = tmp_path / "Timeline.json"
    target.write_text(json.dumps({"semanticSegments": segments}), encoding="utf-8")
    return target


def activity(activityType: str | None, start: str = "2025-01-01T10:00:00+00:00",
             end: str = "2025-01-01T10:20:00+00:00") -> dict:
    body: dict = {"start": {"latLng": "1.0°, 2.0°"}, "end": {"latLng": "1.5°, 2.5°"}}
    if activityType is not None:
        body["topCandidate"] = {"type": activityType, "probability": 0.9}
    return {"startTime": start, "endTime": end, "activity": body}


def test_timelineWalkingActivityYieldsTwoPoints(tmp_path: Path) -> None:
    visits = list(parseTimelineExport(writeTimeline(tmp_path, [activity("walking")])))  # case-insensitive
    assert visits == [
        path(1.0, 2.0, utc(2025, 1, 1, 10, 0), utc(2025, 1, 1, 10, 10), source="timeline"),
        path(1.5, 2.5, utc(2025, 1, 1, 10, 10), utc(2025, 1, 1, 10, 20), source="timeline"),
    ]


def test_timelineLongWalkingActivityPointsAreCapped(tmp_path: Path) -> None:
    long = activity("WALKING", start="2025-01-01T08:00:00+00:00", end="2025-01-01T18:00:00+00:00")
    visits = list(parseTimelineExport(writeTimeline(tmp_path, [long])))
    assert visits == [
        path(1.0, 2.0, utc(2025, 1, 1, 8, 0), utc(2025, 1, 1, 8, 15), source="timeline"),
        path(1.5, 2.5, utc(2025, 1, 1, 13, 0), utc(2025, 1, 1, 13, 15), source="timeline"),
    ]


def test_timelineCyclingActivityKind(tmp_path: Path) -> None:
    visits = list(parseTimelineExport(writeTimeline(tmp_path, [activity("CYCLING")])))
    assert [v.kind for v in visits] == [KIND_CYCLING, KIND_CYCLING]


def test_timelineSkipsDrivingTypelessAndPathSegments(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    pathSegment = {"startTime": "2025-01-01T10:00:00+00:00", "endTime": "2025-01-01T11:00:00+00:00",
                   "timelinePath": [{"point": "1.0°, 2.0°", "time": "2025-01-01T10:30:00+00:00"}]}
    target = writeTimeline(tmp_path, [activity("IN_PASSENGER_VEHICLE"), activity(None), pathSegment])
    with caplog.at_level(logging.WARNING):
        assert list(parseTimelineExport(target)) == []
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_timelineInvalidWalkingActivityWarnsOnce(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = activity("WALKING", start="2025-01-01T11:00:00+00:00", end="2025-01-01T10:00:00+00:00")
    noCoordinates = activity("RUNNING")
    noCoordinates["activity"]["start"] = {}
    noCoordinates["activity"]["end"] = {"latLng": "garbage"}
    with caplog.at_level(logging.WARNING):
        assert list(parseTimelineExport(writeTimeline(tmp_path, [bad, noCoordinates]))) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "skipped 0 visit segments and 2 walking/cycling segments" in warnings[0].getMessage()


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
