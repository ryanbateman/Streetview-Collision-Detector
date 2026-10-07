"""Parser for the on-device Timeline export (Timeline.json / location-history.json).

The file has a top-level semanticSegments[] array. Visit segments carry startTime,
endTime and visit.topCandidate.{placeId, semanticType, placeLocation.latLng} and become
KIND_PLACE Visits. Walking, running and cycling activity segments become two path-point
Visits (start and end, each covering half the duration, capped at PATH_POINT_MAX_MINUTES). Other activity types,
timelinePath segments, rawSignals and userLocationProfile are ignored.
"""
import json
import logging as log
import re
from pathlib import Path
from typing import Any, Iterator

from svcd.ingest.semanticHistory import activityKind, capPathWindow, parseIsoUtc, splitWindows
from svcd.models import KIND_PLACE, SEMANTIC_HOME, SEMANTIC_WORK, Visit

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_LAT_LNG_PATTERN = re.compile(rf"^\s*(?:geo:)?\s*({_NUMBER})\s*°?\s*,\s*({_NUMBER})\s*°?\s*$")

_SEMANTIC_TYPES = {
    "HOME": SEMANTIC_HOME,
    "INFERRED_HOME": SEMANTIC_HOME,
    "WORK": SEMANTIC_WORK,
    "INFERRED_WORK": SEMANTIC_WORK,
}


def parseLatLng(value: str) -> tuple[float, float]:
    """Parse "50.05°, 14.34°", "geo:50.05,14.34" or "50.05,14.34" into (lat, lng).

    Raises ValueError for anything else, including out-of-range values.
    """
    if not isinstance(value, str):
        raise ValueError(f"latLng must be a string, got {type(value).__name__}")
    match = _LAT_LNG_PATTERN.match(value)
    if match is None:
        raise ValueError(f"Unrecognised latLng encoding: {value!r}")
    lat, lng = float(match.group(1)), float(match.group(2))
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        raise ValueError(f"latLng out of range: {value!r}")
    return lat, lng


def _latLngString(location: Any) -> str | None:
    """The latLng string of a location that is either a bare string or {"latLng": "..."}."""
    if isinstance(location, str):
        return location
    if isinstance(location, dict):
        latLng = location.get("latLng")
        if isinstance(latLng, str):
            return latLng
    return None


def _semanticType(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _SEMANTIC_TYPES.get(value.strip().upper())


def _visitFromSegment(segment: dict[str, Any]) -> Visit | None:
    topCandidate = (segment.get("visit") or {}).get("topCandidate") or {}
    latLngRaw = _latLngString(topCandidate.get("placeLocation"))
    startRaw = segment.get("startTime")
    endRaw = segment.get("endTime")
    if latLngRaw is None or not startRaw or not endRaw:
        return None
    try:
        lat, lng = parseLatLng(latLngRaw)
        start = parseIsoUtc(startRaw)
        end = parseIsoUtc(endRaw)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return Visit(
        placeId=topCandidate.get("placeId"),
        name=None,
        address=None,
        lat=lat,
        lng=lng,
        start=start,
        end=end,
        source="timeline",
        kind=KIND_PLACE,
        semanticType=_semanticType(topCandidate.get("semanticType")),
        importance=None,
    )


def _visitsFromActivity(segment: dict[str, Any]) -> list[Visit] | None:
    """Start and end path points of a walking/cycling activity; [] for other types; None if invalid."""
    activity = segment.get("activity") or {}
    kind = activityKind((activity.get("topCandidate") or {}).get("type"))
    if kind is None:
        return []
    startRaw = segment.get("startTime")
    endRaw = segment.get("endTime")
    if not startRaw or not endRaw:
        return None
    try:
        start = parseIsoUtc(startRaw)
        end = parseIsoUtc(endRaw)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    visits = []
    for key, window in zip(("start", "end"), splitWindows(start, end, 2)):
        legStart, legEnd = capPathWindow(*window)
        latLngRaw = _latLngString(activity.get(key))
        if latLngRaw is None:
            continue
        try:
            lat, lng = parseLatLng(latLngRaw)
        except ValueError:
            continue
        visits.append(Visit(placeId=None, name=None, address=None, lat=lat, lng=lng, start=legStart, end=legEnd,
                            source="timeline", kind=kind, semanticType=None, importance=None))
    return visits or None


def parseTimelineExport(path: Path) -> Iterator[Visit]:
    """Yield Visits for the visit and walking/cycling activity segments of a Timeline export, in file order."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "semanticSegments" not in data:
        raise ValueError(f"{path} has no top-level 'semanticSegments' key; not a Timeline export")
    skippedVisits = 0
    skippedActivities = 0
    for segment in data["semanticSegments"]:
        if not isinstance(segment, dict):
            continue
        if "visit" in segment:
            visit = _visitFromSegment(segment)
            if visit is None:
                skippedVisits += 1
            else:
                yield visit
        elif "activity" in segment:
            pathVisits = _visitsFromActivity(segment)
            if pathVisits is None:
                skippedActivities += 1
            else:
                yield from pathVisits
    if skippedVisits or skippedActivities:
        log.warning(
            f"{path.name}: skipped {skippedVisits} visit segments and {skippedActivities} walking/cycling segments "
            "with missing or invalid coordinates/timestamps"
        )
