"""Shared data records for every pipeline stage.

All records are frozen dataclasses so they can be written to and read from JSONL
through svcd.storage without any per-record code. Datetimes are timezone-aware UTC.
Fields with defaults were added after the first release; storage fills them in when
reading older files.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Visit.kind values
KIND_PLACE = "place"        # a stay at a place (placeVisit / visit segment)
KIND_WALKING = "walking"    # a point on a walking or running path (outdoors by definition)
KIND_CYCLING = "cycling"    # a point on a cycling path

# Longest time a single walking or cycling path point is taken to cover. A path point is a sample
# along a route, not a stay, so a long activity with few points must not credit hours to one spot.
PATH_POINT_MAX_MINUTES = 15

# Visit.semanticType values (normalised across both input formats)
SEMANTIC_HOME = "HOME"
SEMANTIC_WORK = "WORK"

# Visit.importance values (old format only; None elsewhere)
IMPORTANCE_MAIN = "MAIN"
IMPORTANCE_TRANSITIONAL = "TRANSITIONAL"


@dataclass(frozen=True)
class Visit:
    """One stay at a place, or one point on an outdoor path, from either input format."""
    placeId: str | None
    name: str | None
    address: str | None
    lat: float
    lng: float
    start: datetime
    end: datetime
    source: str  # "semantic" (old Takeout) or "timeline" (phone export)
    kind: str = KIND_PLACE
    semanticType: str | None = None  # SEMANTIC_HOME, SEMANTIC_WORK or None
    importance: str | None = None    # IMPORTANCE_MAIN, IMPORTANCE_TRANSITIONAL or None


@dataclass(frozen=True)
class Place:
    """Distinct place after deduplicating visits by placeId or rounded coordinate."""
    key: str
    placeId: str | None
    name: str | None
    lat: float
    lng: float
    visitCount: int
    kind: str = KIND_PLACE  # path points are sampled at the centre only, no ring


@dataclass(frozen=True)
class SamplePoint:
    """A coordinate to query: the place centre (ringIndex 0) or a ring offset."""
    placeKey: str
    lat: float
    lng: float
    ringIndex: int


@dataclass(frozen=True)
class PanoLookup:
    """Result of one Street View metadata request for a sample point."""
    placeKey: str
    queryLat: float
    queryLng: float
    status: str  # API status: OK, ZERO_RESULTS, NOT_FOUND, ...
    panoId: str | None
    date: str | None  # "YYYY-MM" as returned, or None when absent
    panoLat: float | None
    panoLng: float | None
    copyright: str | None = None  # API credit line; Google imagery is credited to Google, user photospheres to the contributor


# Panorama provenance, derived from PanoLookup.copyright
SOURCE_GOOGLE = "google"  # Google's own car, trekker or backpack imagery
SOURCE_USER = "user"      # a photosphere uploaded by a Maps contributor


def panoSource(copyright: str | None) -> str:
    """Classify a panorama by its credit line. Missing, empty or blank credits count as Google (the common case)."""
    if copyright and copyright.strip() and "google" not in copyright.casefold():
        return SOURCE_USER
    return SOURCE_GOOGLE


@dataclass(frozen=True)
class Collision:
    """One panorama at one place photographed in a month the user was there.

    probability = coverage * visibility * proximity, the chance this one panorama caught the user.
    """
    placeKey: str
    name: str | None
    month: str  # "YYYY-MM"
    panoId: str
    panoDate: str
    panoLat: float
    panoLng: float
    distanceM: float
    visitCount: int
    dwellMinutes: float  # daylight dwell in that month, minutes
    score: float         # kept for backwards compatibility; equals probability
    url: str
    coverage: float = 0.0    # daylight dwell / daylight hours in the month, capped at 1
    visibility: float = 0.0  # prior that the user was outdoors and in view at this place
    proximity: float = 0.0   # exp(-distanceM / 40)
    probability: float = 0.0
    source: str = SOURCE_GOOGLE  # SOURCE_GOOGLE or SOURCE_USER, from the lookup's copyright


@dataclass(frozen=True)
class Candidate:
    """A place, month and provenance, ranked by the chance that the camera caught the user there.

    One Candidate per (placeKey, month, source): Google imagery and user photospheres at the same
    place are ranked separately so the map can filter by provenance without recomputing odds.
    probability = coverage * visibility * max(proximity) over the panoramas in panos; several
    panoramas of one source in one month are treated as one capture.
    panos entries are plain dicts (JSON-safe) with keys:
      panoId, date, url, lat, lng, distanceM, probability, source, copyright
    """
    rank: int
    placeKey: str
    name: str | None
    month: str
    lat: float
    lng: float
    kind: str
    probability: float
    coverage: float
    visibility: float
    visitCount: int
    dwellMinutes: float
    panos: list[dict[str, Any]] = field(default_factory=list)
    source: str = SOURCE_GOOGLE
    checkKey: str = ""  # stable id for the map's "checked" state: f"{placeKey}|{month}|{source}"
