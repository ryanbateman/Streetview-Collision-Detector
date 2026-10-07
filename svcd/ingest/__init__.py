"""Input parsers: turn Google location-history exports into Visit records."""
from pathlib import Path
from typing import Literal

from svcd.ingest.semanticHistory import findSemanticRoot, parseSemanticHistory
from svcd.ingest.timelineExport import parseLatLng, parseTimelineExport
from svcd.models import Visit

__all__ = ["detectFormat", "ingestVisits", "parseSemanticHistory", "parseTimelineExport", "parseLatLng"]


def _hasSemanticSegments(path: Path, chunkSize: int = 1 << 20) -> bool:
    """Scan the file for the "semanticSegments" key without parsing it (exports can be hundreds of MB)."""
    needle = '"semanticSegments"'
    tail = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            while chunk := handle.read(chunkSize):
                if needle in tail + chunk:
                    return True
                tail = chunk[-len(needle):]
    except OSError:
        return False
    return False


def detectFormat(path: Path) -> Literal["semantic", "timeline"]:
    """Identify the export format at path.

    "semantic": a directory that is, or contains within two levels, a
    "Semantic Location History" folder (old Google Takeout).
    "timeline": a .json file with a top-level "semanticSegments" key (on-device export).
    """
    path = Path(path)
    if path.is_dir():
        if findSemanticRoot(path) is not None:
            return "semantic"
        raise ValueError(
            f"{path} is a directory but has no 'Semantic Location History' folder within two levels. "
            "Point at your Takeout folder, its 'Location History (Timeline)' folder, or a Timeline.json export."
        )
    if path.is_file() and path.suffix.lower() == ".json":
        if _hasSemanticSegments(path):
            return "timeline"
        raise ValueError(
            f"{path} is not a Timeline export (no top-level 'semanticSegments' key). "
            "Records.json and Settings.json are not supported; use the Semantic Location History folder instead."
        )
    raise ValueError(
        f"Cannot detect location-history format for {path}: expected a Takeout folder containing "
        "'Semantic Location History', or a Timeline.json / location-history.json file."
    )


def ingestVisits(path: Path) -> list[Visit]:
    """Parse any supported export into Visits sorted by start time."""
    path = Path(path)
    if detectFormat(path) == "semantic":
        visits = list(parseSemanticHistory(path))
    else:
        visits = list(parseTimelineExport(path))
    visits.sort(key=lambda visit: visit.start)
    return visits
