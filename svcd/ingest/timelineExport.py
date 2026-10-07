"""Parser for the on-device Timeline export (Timeline.json / location-history.json).

The file has a top-level semanticSegments[] array. Visit segments carry startTime,
endTime and visit.topCandidate.{placeId, placeLocation.latLng}; activity and
timelinePath segments are ignored, as are rawSignals and userLocationProfile.
"""
import json
import logging as log
import re
from pathlib import Path
from typing import Any, Iterator

from svcd.ingest.semanticHistory import parseIsoUtc
from svcd.models import Visit

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_LAT_LNG_PATTERN = re.compile(rf"^\s*(?:geo:)?\s*({_NUMBER})\s*°?\s*,\s*({_NUMBER})\s*°?\s*$")


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


def _extractLatLng(topCandidate: dict[str, Any]) -> str | None:
    placeLocation = topCandidate.get("placeLocation")
    if isinstance(placeLocation, str):
        return placeLocation
    if isinstance(placeLocation, dict):
        latLng = placeLocation.get("latLng")
        if isinstance(latLng, str):
            return latLng
    return None


def _visitFromSegment(segment: dict[str, Any]) -> Visit | None:
    topCandidate = (segment.get("visit") or {}).get("topCandidate") or {}
    latLngRaw = _extractLatLng(topCandidate)
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
    )


def parseTimelineExport(path: Path) -> Iterator[Visit]:
    """Yield a Visit for each visit segment of a Timeline export, in file order."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "semanticSegments" not in data:
        raise ValueError(f"{path} has no top-level 'semanticSegments' key; not a Timeline export")
    skipped = 0
    for segment in data["semanticSegments"]:
        if not isinstance(segment, dict) or "visit" not in segment:
            continue
        visit = _visitFromSegment(segment)
        if visit is None:
            skipped += 1
            continue
        yield visit
    if skipped:
        log.warning(f"{path.name}: skipped {skipped} visit segments with missing or invalid coordinates/timestamps")
