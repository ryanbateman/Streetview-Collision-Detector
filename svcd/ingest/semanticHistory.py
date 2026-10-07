"""Parser for the old Google Takeout "Semantic Location History" export.

Layout: <takeout>/Location History (Timeline)/Semantic Location History/<year>/<year>_<MONTH>.json,
each file holding timelineObjects[] whose placeVisit entries become Visit records.
"""
import json
import logging as log
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from svcd.models import Visit

SEMANTIC_DIR_NAME = "Semantic Location History"


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


def _visitFromPlaceVisit(placeVisit: dict[str, Any]) -> Visit | None:
    location = placeVisit.get("location") or {}
    duration = placeVisit.get("duration") or {}
    latE7 = location.get("latitudeE7")
    lngE7 = location.get("longitudeE7")
    startRaw = duration.get("startTimestamp")
    endRaw = duration.get("endTimestamp")
    if latE7 is None or lngE7 is None or not startRaw or not endRaw:
        return None
    try:
        start = parseIsoUtc(startRaw)
        end = parseIsoUtc(endRaw)
        lat = int(latE7) / 1e7
        lng = int(lngE7) / 1e7
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return Visit(
        placeId=location.get("placeId"),
        name=location.get("name"),
        address=location.get("address"),
        lat=lat,
        lng=lng,
        start=start,
        end=end,
        source="semantic",
    )


def parseSemanticFile(path: Path) -> Iterator[Visit]:
    """Yield the placeVisit entries of one monthly JSON file, in file order."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    skipped = 0
    for timelineObject in data.get("timelineObjects", []):
        placeVisit = timelineObject.get("placeVisit")
        if placeVisit is None:
            continue
        visit = _visitFromPlaceVisit(placeVisit)
        if visit is None:
            skipped += 1
            continue
        yield visit
    if skipped:
        log.warning(f"{path.name}: skipped {skipped} place visits with missing or invalid coordinates/timestamps")


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
