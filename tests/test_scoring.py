import math
from datetime import datetime, timedelta, timezone

import pytest

from svcd import scoring
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
from svcd.scoring import combine, coverage, daylightSplit, daysInMonth, proximity, solarHour, visibility

UTC = timezone.utc
H = timedelta(hours=1)


def dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def makeVisit(start: datetime = dt(2022, 9, 5, 10), end: datetime = dt(2022, 9, 5, 11), lng: float = 0.0,
              **overrides) -> Visit:
    base = dict(placeId="p", name=None, address=None, lat=52.5, lng=lng, start=start, end=end, source="timeline")
    base.update(overrides)
    return Visit(**base)


# solarHour

def testSolarHourAtGreenwich():
    assert solarHour(dt(2022, 9, 5, 12), 0.0) == pytest.approx(12)


def testSolarHourEastOfGreenwich():
    assert solarHour(dt(2022, 9, 5, 12), 13.4) == pytest.approx(12 + 13.4 / 15)
    assert solarHour(dt(2022, 9, 5, 12), 13.4) == pytest.approx(12.9, abs=0.01)


def testSolarHourWestOfGreenwich():
    assert solarHour(dt(2022, 9, 5, 12), -75.0) == pytest.approx(7)


def testSolarHourWrapsPastMidnight():
    assert solarHour(dt(2022, 9, 5, 23), 30.0) == pytest.approx(1)
    assert solarHour(dt(2022, 9, 5, 2), -45.0) == pytest.approx(23)
    assert solarHour(dt(2022, 9, 5, 12, 30), 0.0) == pytest.approx(12.5)


# daylightSplit

def testDaylightSplitFullDay():
    assert daylightSplit(dt(2022, 9, 5), dt(2022, 9, 6), 0.0) == (12 * H, 12 * H)


def testDaylightSplitNightOnly():
    start, end = dt(2022, 9, 5, 20), dt(2022, 9, 6, 6)
    assert daylightSplit(start, end, 0.0) == (timedelta(0), end - start)


def testDaylightSplitCrossingDawn():
    assert daylightSplit(dt(2022, 9, 5, 5), dt(2022, 9, 5, 9), 0.0) == (2 * H, 2 * H)
    # At lng 15 solar time runs one hour ahead, so 06:00-07:00 UTC is solar 07:00-08:00.
    assert daylightSplit(dt(2022, 9, 5, 5), dt(2022, 9, 5, 7), 15.0) == (H, H)


def testDaylightSplitCrossingDusk():
    assert daylightSplit(dt(2022, 9, 5, 18), dt(2022, 9, 5, 20), 0.0) == (H, H)


def testDaylightSplitMultiDay():
    # Mon 12:00 to Wed 12:00: 7 h + 12 h + 5 h daylight.
    daylight, night = daylightSplit(dt(2022, 9, 5, 12), dt(2022, 9, 7, 12), 0.0)
    assert daylight == 24 * H
    assert night == 24 * H


def testDaylightSplitEmptyOrReversed():
    assert daylightSplit(dt(2022, 9, 5, 12), dt(2022, 9, 5, 12), 0.0) == (timedelta(0), timedelta(0))
    assert daylightSplit(dt(2022, 9, 5, 12), dt(2022, 9, 5, 10), 0.0) == (timedelta(0), timedelta(0))


def testDaylightSplitWestOfGreenwichAcrossUtcMidnight():
    # 00:00-04:00 UTC at lng -90 is solar 18:00-22:00 of the previous day.
    assert daylightSplit(dt(2022, 9, 5, 0), dt(2022, 9, 5, 4), -90.0) == (H, 3 * H)


# daysInMonth

@pytest.mark.parametrize("month, days", [("2022-09", 30), ("2022-08", 31), ("2022-02", 28), ("2024-02", 29),
                                         ("2022-12", 31)])
def testDaysInMonth(month, days):
    assert daysInMonth(month) == days


# coverage

def testCoverageWholeMonthIsCapped():
    visits = [makeVisit(dt(2022, 9, 1), dt(2022, 10, 1))]
    assert coverage(visits, "2022-09") == 1.0
    daily = [makeVisit(dt(2022, 9, d, 7), dt(2022, 9, d, 19)) for d in range(1, 31)]
    assert coverage(daily, "2022-09") == pytest.approx(1.0)


def testCoverageOneDaylightHour():
    assert coverage([makeVisit(dt(2022, 9, 5, 10), dt(2022, 9, 5, 11))], "2022-09") == pytest.approx(1 / 360)


def testCoverageNightOnly():
    visits = [makeVisit(dt(2022, 9, 5, 22), dt(2022, 9, 6, 2))]
    assert coverage(visits, "2022-09") == pytest.approx(scoring.NIGHT_WEIGHT * 4 / 360)


def testCoverageOnlyCountsTheMonth():
    visits = [makeVisit(dt(2022, 8, 31, 12), dt(2022, 9, 1, 12))]
    # September part: 00:00-07:00 night, 07:00-12:00 daylight.
    assert coverage(visits, "2022-09") == pytest.approx((5 + scoring.NIGHT_WEIGHT * 7) / 360)
    assert coverage(visits, "2022-07") == 0.0


def testCoverageSumsVisits():
    visits = [makeVisit(dt(2022, 9, 5, 10), dt(2022, 9, 5, 11)), makeVisit(dt(2022, 9, 6, 10), dt(2022, 9, 6, 12))]
    assert coverage(visits, "2022-09") == pytest.approx(3 / 360)


# visibility

LONG = dict(start=dt(2022, 9, 5, 10), end=dt(2022, 9, 5, 12))
SHORT = dict(start=dt(2022, 9, 5, 10), end=dt(2022, 9, 5, 10, 5))


def testVisibilityPath():
    assert visibility([makeVisit(kind=KIND_WALKING, semanticType=SEMANTIC_HOME, **LONG)], "Office") == 0.9
    visits = [makeVisit(**LONG), makeVisit(kind=KIND_CYCLING, **LONG)]
    assert visibility(visits, None) == scoring.VISIBILITY_PATH


def testVisibilityHomeWorkMajority():
    visits = [makeVisit(semanticType=SEMANTIC_HOME, **LONG), makeVisit(semanticType=SEMANTIC_HOME, **LONG),
              makeVisit(**LONG)]
    assert visibility(visits, None) == 0.1
    assert visibility([makeVisit(semanticType=SEMANTIC_WORK, **SHORT)], "Volkspark") == 0.1


def testVisibilityHomeMinorityDoesNotCount():
    visits = [makeVisit(semanticType=SEMANTIC_HOME, **LONG), makeVisit(**LONG), makeVisit(**LONG)]
    assert visibility(visits, None) == 0.3


@pytest.mark.parametrize("name", ["Volkspark", "Alexanderplatz", "BAHNHOF Zoo", "Oberbaumbrücke", "Strandbad Wannsee"])
def testVisibilityOutdoorName(name):
    assert visibility([makeVisit(**LONG)], name) == 0.7


def testVisibilityTransitionalMajority():
    visits = [makeVisit(importance=IMPORTANCE_TRANSITIONAL, **LONG), makeVisit(importance=IMPORTANCE_TRANSITIONAL, **LONG),
              makeVisit(importance=IMPORTANCE_MAIN, **LONG)]
    assert visibility(visits, "Cafe") == 0.7


def testVisibilityBriefStops():
    visits = [makeVisit(**SHORT), makeVisit(**SHORT), makeVisit(**LONG)]
    assert visibility(visits, "Cafe") == 0.5


def testVisibilityPlain():
    assert visibility([makeVisit(kind=KIND_PLACE, **LONG)], "Cafe") == 0.3
    assert visibility([], None) == scoring.VISIBILITY_DEFAULT


# proximity

def testProximity():
    assert proximity(0) == 1.0
    assert proximity(40) == pytest.approx(math.exp(-1))
    values = [proximity(d) for d in (0, 5, 10, 40, 100, 500)]
    assert all(a > b for a, b in zip(values, values[1:]))


# combine

def testCombine():
    assert combine([]) == 0.0
    assert combine([0.5, 0.5]) == pytest.approx(0.75)
    assert combine([1.0, 0.3]) == 1.0
    assert combine(iter([0.2])) == pytest.approx(0.2)


def testOutdoorKeywordsMatchCompoundsButNotSubstrings():
    from svcd.scoring import isOutdoorName
    assert isOutdoorName("Volkspark Friedrichshain")
    assert isOutdoorName("Hauptbahnhof")
    assert isOutdoorName("Alexanderplatz")
    assert isOutdoorName("Pier 39")
    assert not isOutdoorName("Papierwarenladen")
    assert not isOutdoorName("Chausseestrasse 12")
    assert not isOutdoorName(None)


@pytest.mark.parametrize("name", [
    "Volkspark", "Hauptbahnhof", "Alexanderplatz", "Pier 39", "Grunewald", "Strandbad Wannsee",
])
def testOutdoorNamePositives(name):
    from svcd.scoring import isOutdoorName
    assert isOutdoorName(name)


@pytest.mark.parametrize("name", [
    "Papier", "Papierwarenladen", "Supermarkt", "Rewe City Kaiserstr", "Parkhaus", "Waldorf Astoria",
    "Kindergarten", "Kaiser Wilhelm", "Chausseestrasse", None, "",
])
def testOutdoorNameNegatives(name):
    from svcd.scoring import isOutdoorName
    assert not isOutdoorName(name)
