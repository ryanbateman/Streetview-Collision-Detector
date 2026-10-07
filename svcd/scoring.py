"""Likelihood model: the chance that a Street View camera caught the user.

A panorama is shot at one unknown instant of its month, in daylight. For panorama i at a place:

    P(caught by i) = coverage * visibility * proximity_i

coverage is the share of the month's daylight the user spent at the place, visibility the prior
that the user was outdoors and in view there, and proximity_i how close the panorama is to the
place centre. Pano dates have month granularity, so several panoramas at one place in one month
are treated as one drive: a place-month counts only its nearest panorama (see svcd.match).
All constants are module-level so they can be tuned.
"""
import calendar
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Iterable

from svcd.models import (
    IMPORTANCE_TRANSITIONAL,
    KIND_CYCLING,
    KIND_WALKING,
    SEMANTIC_HOME,
    SEMANTIC_WORK,
    Visit,
)

# Daylight window in solar local hours, and how much a night hour counts relative to a day hour.
DAYLIGHT_START = 7
DAYLIGHT_END = 19
NIGHT_WEIGHT = 0.05

# Visibility priors.
VISIBILITY_PATH = 0.9        # walking or cycling: outdoors by definition
VISIBILITY_HOME_WORK = 0.1   # mostly indoors
VISIBILITY_OUTDOOR = 0.7     # outdoor-sounding name or a transitional stop
VISIBILITY_BRIEF = 0.5       # short stops are usually outdoors
VISIBILITY_DEFAULT = 0.3
BRIEF_VISIT = timedelta(minutes=15)
MAJORITY_SHARE = 0.5         # "majority" means strictly more than this share of the visits

# Outdoor-name keywords. A word equal to, or ending with, a suffix keyword matches, so German
# compounds like "Volkspark", "Hauptbahnhof" or "Grunewald" count. Word keywords match whole words
# only, so "Papier" (pier) and "Kaiserstr" (kai) do not. "see" is left out entirely because it is
# both a lake and a street suffix. Excluded words are indoor places that end with a suffix keyword.
OUTDOOR_SUFFIX_KEYWORDS = (
    "park", "garden", "garten", "beach", "strand", "platz", "square", "plaza", "piazza", "market",
    "markt", "stadium", "stadion", "station", "bahnhof", "haltestelle", "bridge", "brücke", "bruecke",
    "promenade", "playground", "spielplatz", "cemetery", "friedhof", "ufer", "hafen", "harbour",
    "harbor", "festival", "flohmarkt", "wiese", "wald", "strandbad", "freibad",
)
OUTDOOR_WORD_KEYWORDS = ("zoo", "pier", "trail", "lake", "field", "forest", "quay", "kai")
OUTDOOR_EXCLUDED_WORDS = (
    "supermarkt", "biomarkt", "baumarkt", "drogeriemarkt", "getränkemarkt", "wochenmarkt", "parkhaus",
    "parkdeck", "kindergarten", "waldorf", "papier",
)

# Proximity decay length in metres.
PROXIMITY_SCALE_M = 40.0

_DAY = timedelta(days=1)


def _asUtc(value: datetime) -> datetime:
    """Naive datetimes are taken to be UTC; aware ones are converted."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _monthBounds(month: str) -> tuple[datetime, datetime]:
    year, mon = int(month[:4]), int(month[5:7])
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if mon == 12 else datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    return start, end


def solarHour(moment: datetime, lng: float) -> float:
    """Local solar hour [0, 24) of a moment at longitude lng: (UTC hour + lng / 15) mod 24."""
    moment = _asUtc(moment)
    utcHour = moment.hour + moment.minute / 60 + (moment.second + moment.microsecond / 1e6) / 3600
    return (utcHour + lng / 15) % 24


def daylightSplit(start: datetime, end: datetime, lng: float) -> tuple[timedelta, timedelta]:
    """(daylight, night) time within [start, end], walking solar day by solar day."""
    start, end = _asUtc(start), _asUtc(end)
    if end <= start:
        return timedelta(0), timedelta(0)
    # Shift into solar local time; durations are unchanged by a constant offset.
    offset = timedelta(hours=lng / 15)
    solarStart, solarEnd = (start + offset).replace(tzinfo=None), (end + offset).replace(tzinfo=None)
    day = datetime(solarStart.year, solarStart.month, solarStart.day)
    daylight = timedelta(0)
    while day < solarEnd:
        windowStart = max(day + timedelta(hours=DAYLIGHT_START), solarStart)
        windowEnd = min(day + timedelta(hours=DAYLIGHT_END), solarEnd)
        if windowEnd > windowStart:
            daylight += windowEnd - windowStart
        day += _DAY
    return daylight, (end - start) - daylight


def daysInMonth(month: str) -> int:
    """Number of days in a "YYYY-MM" month."""
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def daylightDwell(visits: Iterable[Visit], month: str) -> tuple[timedelta, timedelta]:
    """Summed (daylight, night) time of the visits' intersections with the UTC month."""
    monthStart, monthEnd = _monthBounds(month)
    daylight, night = timedelta(0), timedelta(0)
    for visit in visits:
        day, nightPart = daylightSplit(max(_asUtc(visit.start), monthStart),
                                       min(_asUtc(visit.end), monthEnd), visit.lng)
        daylight += day
        night += nightPart
    return daylight, night


def coverage(visits: Iterable[Visit], month: str) -> float:
    """Fraction of the month's daylight spent at the place (night counted at NIGHT_WEIGHT), capped at 1."""
    daylight, night = daylightDwell(visits, month)
    weighted = daylight + NIGHT_WEIGHT * night
    available = timedelta(hours=DAYLIGHT_END - DAYLIGHT_START) * daysInMonth(month)
    return min(1.0, weighted / available)


def _isMajority(values: list, accepted: set) -> bool:
    return bool(values) and sum(v in accepted for v in values) > MAJORITY_SHARE * len(values)


def isOutdoorName(name: str | None) -> bool:
    """True when a word of the name (case-insensitive) ends with an outdoor suffix keyword or equals
    an outdoor word keyword. Words in OUTDOOR_EXCLUDED_WORDS never match."""
    if not name:
        return False
    for word in re.findall(r"\w+", name.casefold()):
        if word in OUTDOOR_EXCLUDED_WORDS:
            continue
        if word in OUTDOOR_WORD_KEYWORDS or word.endswith(OUTDOOR_SUFFIX_KEYWORDS):
            return True
    return False


def visibility(visits: Iterable[Visit], name: str | None) -> float:
    """Prior that the user was outdoors and in view of the camera at this place."""
    visits = list(visits)
    if any(v.kind in (KIND_WALKING, KIND_CYCLING) for v in visits):
        return VISIBILITY_PATH
    if _isMajority([v.semanticType for v in visits], {SEMANTIC_HOME, SEMANTIC_WORK}):
        return VISIBILITY_HOME_WORK
    if isOutdoorName(name) or _isMajority([v.importance for v in visits], {IMPORTANCE_TRANSITIONAL}):
        return VISIBILITY_OUTDOOR
    if visits and statistics.median(_asUtc(v.end) - _asUtc(v.start) for v in visits) < BRIEF_VISIT:
        return VISIBILITY_BRIEF
    return VISIBILITY_DEFAULT


def proximity(distanceM: float) -> float:
    """exp(-distance / PROXIMITY_SCALE_M): 1 at the centre, 1/e at one scale length."""
    return math.exp(-max(0.0, distanceM) / PROXIMITY_SCALE_M)


def combine(probabilities: Iterable[float]) -> float:
    """Chance that at least one independent event happens: 1 - prod(1 - p).

    Not used for candidates: panoramas of one place-month are not independent (see svcd.match).
    """
    miss = 1.0
    for p in probabilities:
        miss *= 1.0 - p
    return 1.0 - miss
