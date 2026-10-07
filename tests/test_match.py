from datetime import datetime, timedelta, timezone

import pytest

from svcd.match import (
    buildPanoUrl,
    dwellInMonth,
    findCollisions,
    haversineM,
    monthsSpanned,
    rankCollisions,
    scoreCollision,
)
from svcd.models import Collision, PanoLookup, Place, Visit

UTC = timezone.utc
LAT, LNG = 52.48568, 13.37656
KEY = "place-1"


def dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def makeVisit(start: datetime, end: datetime, placeId: str | None = KEY, lat: float = LAT, lng: float = LNG) -> Visit:
    return Visit(placeId=placeId, name="Cafe", address=None, lat=lat, lng=lng, start=start, end=end, source="semantic")


def makePlace(key: str = KEY, name: str | None = "Cafe", lat: float = LAT, lng: float = LNG) -> Place:
    return Place(key=key, placeId=key, name=name, lat=lat, lng=lng, visitCount=1)


def makeLookup(
    panoId: str | None = "pano-a",
    date: str | None = "2022-08",
    status: str = "OK",
    placeKey: str = KEY,
    panoLat: float = LAT + 0.0001,
    panoLng: float = LNG,
) -> PanoLookup:
    return PanoLookup(placeKey=placeKey, queryLat=LAT, queryLng=LNG, status=status, panoId=panoId,
                      date=date, panoLat=panoLat, panoLng=panoLng)


def makeCollision(placeKey: str, month: str, score: float) -> Collision:
    return Collision(placeKey=placeKey, name=None, month=month, panoId="p", panoDate=month, panoLat=0.0,
                     panoLng=0.0, distanceM=0.0, visitCount=1, dwellMinutes=1.0, score=score, url="u")


# monthsSpanned

def testMonthsSpannedSameMonth():
    assert monthsSpanned(dt(2022, 8, 3, 10), dt(2022, 8, 3, 11)) == ["2022-08"]
    assert monthsSpanned(dt(2022, 8, 3), dt(2022, 8, 3)) == ["2022-08"]


def testMonthsSpannedAcrossMonthBoundary():
    assert monthsSpanned(dt(2022, 8, 31, 22), dt(2022, 9, 1, 1)) == ["2022-08", "2022-09"]


def testMonthsSpannedAcrossYearEnd():
    assert monthsSpanned(dt(2021, 12, 30), dt(2022, 2, 2)) == ["2021-12", "2022-01", "2022-02"]


def testMonthsSpannedEndBeforeStartIsEmpty():
    # Documented choice: a reversed interval spans no months rather than raising.
    assert monthsSpanned(dt(2022, 9, 1), dt(2022, 8, 1)) == []


def testMonthsSpannedUsesUtc():
    plusTwo = timezone(timedelta(hours=2))
    # 2022-09-01 01:00 at +02:00 is still 2022-08-31 in UTC.
    start = datetime(2022, 9, 1, 1, tzinfo=plusTwo)
    assert monthsSpanned(start, start) == ["2022-08"]


# dwellInMonth

def testDwellInMonthSplitsAtBoundary():
    visit = makeVisit(dt(2022, 8, 31, 22), dt(2022, 9, 1, 1, 30))
    assert dwellInMonth(visit, "2022-08") == timedelta(minutes=120)
    assert dwellInMonth(visit, "2022-09") == timedelta(minutes=90)
    assert dwellInMonth(visit, "2022-07") == timedelta(0)


def testDwellInMonthDecemberBoundary():
    visit = makeVisit(dt(2021, 12, 31, 23), dt(2022, 1, 1, 1))
    assert dwellInMonth(visit, "2021-12") == timedelta(hours=1)
    assert dwellInMonth(visit, "2022-01") == timedelta(hours=1)


# helpers

def testHaversineKnownDistance():
    assert haversineM(LAT, LNG, LAT, LNG) == 0
    # One degree of latitude is about 111.2 km.
    assert haversineM(0, 0, 1, 0) == pytest.approx(111195, rel=1e-3)


def testBuildPanoUrl():
    assert buildPanoUrl("abc", 1.5, 2.5) == (
        "https://www.google.com/maps/@?api=1&map_action=pano&pano=abc&viewpoint=1.5,2.5"
    )


# findCollisions

def testTwoVisitsInPanoMonthGiveOneCollision():
    visits = [
        makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11)),
        makeVisit(dt(2022, 8, 20, 12), dt(2022, 8, 20, 12, 30)),
    ]
    collisions = findCollisions(visits, [makePlace()], [makeLookup()])
    assert len(collisions) == 1
    c = collisions[0]
    assert c.placeKey == KEY
    assert c.name == "Cafe"
    assert c.month == "2022-08"
    assert c.panoDate == "2022-08"
    assert c.visitCount == 2
    assert c.dwellMinutes == pytest.approx(90)
    assert c.distanceM == pytest.approx(haversineM(LAT, LNG, LAT + 0.0001, LNG))
    assert c.score == pytest.approx(scoreCollision(90, 2, c.distanceM))
    assert c.url == buildPanoUrl("pano-a", LAT + 0.0001, LNG)


def testLookupWithoutVisitInMonthGivesNothing():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    assert findCollisions(visits, [makePlace()], [makeLookup(date="2019-03")]) == []


def testMonthCrossingVisitMatchesBothMonths():
    visits = [makeVisit(dt(2022, 8, 31, 22), dt(2022, 9, 1, 1, 30))]
    lookups = [makeLookup(panoId="aug", date="2022-08"), makeLookup(panoId="sep", date="2022-09")]
    byPano = {c.panoId: c for c in findCollisions(visits, [makePlace()], lookups)}
    assert set(byPano) == {"aug", "sep"}
    assert byPano["aug"].dwellMinutes == pytest.approx(120)
    assert byPano["sep"].dwellMinutes == pytest.approx(90)


def testDuplicatePanoIdCollapsesKeepingClosest():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    far = makeLookup(panoLat=LAT + 0.001)
    near = makeLookup(panoLat=LAT + 0.0002)
    collisions = findCollisions(visits, [makePlace()], [far, near, far])
    assert len(collisions) == 1
    assert collisions[0].panoLat == LAT + 0.0002


def testFullDateIsTruncatedToMonth():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    collisions = findCollisions(visits, [makePlace()], [makeLookup(date="2022-08-14")])
    assert [c.month for c in collisions] == ["2022-08"]
    assert collisions[0].panoDate == "2022-08-14"


def testUnusableLookupsAreIgnored():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    lookups = [
        makeLookup(status="ZERO_RESULTS"),
        makeLookup(date=None),
        makeLookup(panoId=None),
        makeLookup(date="2022"),
    ]
    assert findCollisions(visits, [makePlace()], lookups) == []


def testPlaceWithoutNameUsesNone():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    collisions = findCollisions(visits, [makePlace(name=None)], [makeLookup()])
    assert collisions[0].name is None


def testVisitWithoutPlaceIdKeyedByRoundedCoordinate():
    lat, lng = 48.1234567, 11.7654321
    key = f"{round(lat, 5)},{round(lng, 5)}"
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11), placeId=None, lat=lat, lng=lng)]
    place = makePlace(key=key, lat=lat, lng=lng)
    collisions = findCollisions(visits, [place], [makeLookup(placeKey=key, panoLat=lat, panoLng=lng)])
    assert len(collisions) == 1
    assert collisions[0].placeKey == key
    assert collisions[0].distanceM == pytest.approx(0)


def testVisitsAtOtherPlacesDoNotMatch():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11), placeId="elsewhere")]
    assert findCollisions(visits, [makePlace()], [makeLookup()]) == []


# ranking and scoring

def testRankCollisionsOrdersByScoreThenKeyThenMonth():
    a = makeCollision("b", "2022-01", 1.0)
    b = makeCollision("a", "2022-02", 1.0)
    c = makeCollision("a", "2022-01", 1.0)
    d = makeCollision("z", "2020-01", 5.0)
    assert rankCollisions([a, b, c, d]) == [d, c, b, a]


def testScoreMonotonicity():
    assert scoreCollision(120, 1, 10) > scoreCollision(30, 1, 10)
    assert scoreCollision(60, 1, 10) > scoreCollision(60, 1, 50)
    assert scoreCollision(60, 3, 10) > scoreCollision(60, 1, 10)
    assert scoreCollision(0, 0, 0) == 0


def testVisitEndingExactlyAtMonthStartDoesNotMatchThatMonth():
    visits = [makeVisit(dt(2022, 8, 31, 22, 0), dt(2022, 9, 1, 0, 0))]
    collisions = findCollisions(visits, [makePlace()], [makeLookup(date="2022-09")])
    assert collisions == []
    collisions = findCollisions(visits, [makePlace()], [makeLookup(date="2022-08")])
    assert len(collisions) == 1 and collisions[0].dwellMinutes == pytest.approx(120)
