"""Shared data records for every pipeline stage.

All records are frozen dataclasses so they can be written to and read from JSONL
through svcd.storage without any per-record code. Datetimes are timezone-aware UTC.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Visit:
    """One stay at a place, from either input format."""
    placeId: str | None
    name: str | None
    address: str | None
    lat: float
    lng: float
    start: datetime
    end: datetime
    source: str  # "semantic" (old Takeout) or "timeline" (phone export)


@dataclass(frozen=True)
class Place:
    """Distinct place after deduplicating visits by placeId or rounded coordinate."""
    key: str
    placeId: str | None
    name: str | None
    lat: float
    lng: float
    visitCount: int


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


@dataclass(frozen=True)
class Collision:
    """A month in which the user was at a place and its panorama was photographed."""
    placeKey: str
    name: str | None
    month: str  # "YYYY-MM"
    panoId: str
    panoDate: str
    panoLat: float
    panoLng: float
    distanceM: float
    visitCount: int
    dwellMinutes: float
    score: float
    url: str
