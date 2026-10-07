"""Parser for the old Google Takeout "Semantic Location History" export.

Layout: <takeout>/Location History (Timeline)/Semantic Location History/<year>/<year>_<MONTH>.json,
each file holding timelineObjects[]. placeVisit entries become KIND_PLACE Visits; walking,
running and cycling activitySegment entries become one Visit per path point, each covering at
most PATH_POINT_MAX_MINUTES.
"""
import json
import logging as log
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from svcd.models import (
    IMPORTANCE_MAIN,
    IMPORTANCE_TRANSITIONAL,
    KIND_CYCLING,
    KIND_PLACE,
    KIND_WALKING,
    PATH_POINT_MAX_MINUTES,
    SEMANTIC_HOME,
    SEMANTIC_WORK,
    Visit,
)

SEMANTIC_DIR_NAME = "Semantic Location History"

_SEMANTIC_TYPES = {"TYPE_HOME": SEMANTIC_HOME, "TYPE_WORK": SEMANTIC_WORK}
_IMPORTANCES = {"MAIN": IMPORTANCE_MAIN, "TRANSITIONAL": IMPORTANCE_TRANSITIONAL}
_ACTIVITY_KINDS = {
    "WALKING": KIND_WALKING,
    "ON_FOOT": KIND_WALKING,
    "RUNNING": KIND_WALKING,
    "CYCLING": KIND_CYCLING,
    "ON_BICYCLE": KIND_CYCLING,
}

# (lat, lng, start, end) for one path point
Leg = tuple[float, float, datetime, datetime]

# Each path point covers at most this long, however sparse the points of a long activity are.
PATH_POINT_MAX = timedelta(minutes=PATH_POINT_MAX_MINUTES)


def findSemanticRoot(root: Path, maxDepth: int = 2) -> Path | None:
    """Return the "Semantic Location History" folder at or below root (up to maxDepth levels), else None."""
    root = Path(root)
    if not root.is_dir():
        return None
    if root.name == SEMANTIC_DIR_NAME:
        return root
    frontier = [root]
    for _ in range(maxDepth):
        nextFrontier: list[Path] = []
        for directory in frontier:
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                if child.name == SEMANTIC_DIR_NAME:
                    return child
                nextFrontier.append(child)
        frontier = nextFrontier
    return None


def parseIsoUtc(value: str) -> datetime:
    """Parse an ISO 8601 timestamp (Z suffix or numeric offset) into an aware UTC datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def activityKind(activityType: Any) -> str | None:
    """Map an activity type name (either format, any case) to KIND_WALKING / KIND_CYCLING, else None."""
    if not isinstance(activityType, str):
        return None
    return _ACTIVITY_KINDS.get(activityType.strip().upper())


def capPathWindow(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """A path point's window, shortened to at most PATH_POINT_MAX from its start."""
    return start, min(end, start + PATH_POINT_MAX)


def splitWindows(start: datetime, end: datetime, count: int) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into count equal consecutive windows that tile it exactly."""
    total = end - start
    bounds = [start + (total * k) / count for k in range(count)] + [end]
    return [(bounds[k], bounds[k + 1]) for k in range(count)]


def _parseDuration(duration: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(duration, dict):
        return None
    startRaw = duration.get("startTimestamp")
    endRaw = duration.get("endTimestamp")
    if not startRaw or not endRaw:
        return None
    try:
        start = parseIsoUtc(startRaw)
        end = parseIsoUtc(endRaw)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return start, end


def _parseE7(point: Any, latKey: str, lngKey: str) -> tuple[float, float] | None:
    if not isinstance(point, dict):
        return None
    latE7 = point.get(latKey)
    lngE7 = point.get(lngKey)
    if latE7 is None or lngE7 is None:
        return None
    try:
        return int(latE7) / 1e7, int(lngE7) / 1e7
    except (TypeError, ValueError):
        return None


def _parsePointTime(point: dict[str, Any]) -> datetime | None:
    try:
        if point.get("timestamp"):
            return parseIsoUtc(point["timestamp"])
        if point.get("timestampMs") is not None:
            return datetime.fromtimestamp(int(point["timestampMs"]) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def _visitFromPlaceVisit(placeVisit: dict[str, Any]) -> Visit | None:
    location = placeVisit.get("location") or {}
    window = _parseDuration(placeVisit.get("duration"))
    coordinates = _parseE7(location, "latitudeE7", "longitudeE7")
    if window is None or coordinates is None:
        return None
    semanticType = location.get("semanticType")
    importance = placeVisit.get("placeVisitImportance")
    return Visit(
        placeId=location.get("placeId"),
        name=location.get("name"),
        address=location.get("address"),
        lat=coordinates[0],
        lng=coordinates[1],
        start=window[0],
        end=window[1],
        source="semantic",
        kind=KIND_PLACE,
        semanticType=_SEMANTIC_TYPES.get(semanticType) if isinstance(semanticType, str) else None,
        importance=_IMPORTANCES.get(importance) if isinstance(importance, str) else None,
    )


def _rawPathLegs(segment: dict[str, Any], segmentStart: datetime, segmentEnd: datetime) -> list[Leg]:
    """simplifiedRawPath points; each leg runs from its own timestamp to the next point's (or the segment end).

    Legs are clipped to [segmentStart, segmentEnd], and the first leg starts at the segment start
    even when the first raw point was recorded later.
    """
    points = (segment.get("simplifiedRawPath") or {}).get("points")
    if not isinstance(points, list):
        return []
    timed: list[tuple[datetime, float, float]] = []
    for point in points:
        coordinates = _parseE7(point, "latE7", "lngE7")
        timestamp = _parsePointTime(point) if isinstance(point, dict) else None
        if coordinates is None or timestamp is None:
            continue
        timed.append((timestamp, coordinates[0], coordinates[1]))
    timed.sort(key=lambda item: item[0])
    legs: list[Leg] = []
    for index, (timestamp, lat, lng) in enumerate(timed):
        legStart = segmentStart if index == 0 else timestamp
        legStart = min(max(legStart, segmentStart), segmentEnd)
        legEnd = timed[index + 1][0] if index + 1 < len(timed) else segmentEnd
        legEnd = min(max(legEnd, legStart), segmentEnd)
        legs.append((lat, lng, legStart, legEnd))
    return legs


def _interpolatedLegs(coordinates: list[tuple[float, float]], start: datetime, end: datetime) -> list[Leg]:
    """Spread untimed points over [start, end]: point k of n covers [start + k*T/n, start + (k+1)*T/n]."""
    if not coordinates:
        return []
    windows = splitWindows(start, end, len(coordinates))
    return [(lat, lng, legStart, legEnd) for (lat, lng), (legStart, legEnd) in zip(coordinates, windows)]


def _visitsFromActivitySegment(segment: dict[str, Any]) -> list[Visit] | None:
    """Path-point Visits for a walking/cycling segment; [] for other activity types; None if invalid."""
    kind = activityKind(segment.get("activityType"))
    if kind is None:
        return []
    window = _parseDuration(segment.get("duration"))
    if window is None:
        return None
    start, end = window
    legs = _rawPathLegs(segment, start, end)
    if not legs:
        waypoints = (segment.get("waypointPath") or {}).get("waypoints")
        parsed = [_parseE7(w, "latE7", "lngE7") for w in waypoints] if isinstance(waypoints, list) else []
        legs = _interpolatedLegs([c for c in parsed if c is not None], start, end)
    if not legs:
        endpoints = [_parseE7(segment.get(key), "latitudeE7", "longitudeE7") for key in ("startLocation", "endLocation")]
        legs = _interpolatedLegs([c for c in endpoints if c is not None], start, end)
    if not legs:
        return None
    visits = []
    for lat, lng, legStart, legEnd in legs:
        legStart, legEnd = capPathWindow(legStart, legEnd)
        visits.append(Visit(placeId=None, name=None, address=None, lat=lat, lng=lng, start=legStart, end=legEnd,
                            source="semantic", kind=kind, semanticType=None, importance=None))
    return visits


def parseSemanticFile(path: Path) -> Iterator[Visit]:
    """Yield the place visits and outdoor path points of one monthly JSON file, in file order."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    skippedVisits = 0
    skippedSegments = 0
    for timelineObject in data.get("timelineObjects", []):
        placeVisit = timelineObject.get("placeVisit")
        if placeVisit is not None:
            visit = _visitFromPlaceVisit(placeVisit)
            if visit is None:
                skippedVisits += 1
            else:
                yield visit
            continue
        activitySegment = timelineObject.get("activitySegment")
        if isinstance(activitySegment, dict):
            pathVisits = _visitsFromActivitySegment(activitySegment)
            if pathVisits is None:
                skippedSegments += 1
            else:
                yield from pathVisits
    if skippedVisits or skippedSegments:
        log.warning(
            f"{path.name}: skipped {skippedVisits} place visits and {skippedSegments} walking/cycling segments "
            "with missing or invalid coordinates/timestamps"
        )


def parseSemanticHistory(root: Path) -> Iterator[Visit]:
    """Yield Visits from every <year>/*.json file under the Semantic Location History folder.

    root may be the takeout folder, the "Location History (Timeline)" folder, or the
    "Semantic Location History" folder itself.
    """
    semanticRoot = findSemanticRoot(Path(root))
    if semanticRoot is None:
        raise ValueError(f"No '{SEMANTIC_DIR_NAME}' folder found under {root}")
    for yearDir in sorted(p for p in semanticRoot.iterdir() if p.is_dir()):
        for monthFile in sorted(yearDir.rglob("*.json")):
            yield from parseSemanticFile(monthFile)
