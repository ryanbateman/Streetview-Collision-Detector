from datetime import datetime, timedelta, timezone

import pytest

from svcd.match import (
    buildCandidates,
    buildPanoUrl,
    dwellInMonth,
    findCollisions,
    haversineM,
    monthsSpanned,
    rankCollisions,
)
from svcd.models import (
    KIND_PLACE, KIND_WALKING, SOURCE_GOOGLE, SOURCE_USER, Collision, PanoLookup, Place, Visit,
)
from svcd.places import buildPlaces
from svcd.scoring import coverage, proximity, visibility

UTC = timezone.utc
LAT, LNG = 52.48568, 13.37656
KEY = "place-1"
GOOGLE_CREDIT = "\u00a9 Google"
USER_CREDIT = "\u00a9 Jane Contributor"


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
    copyright: str | None = GOOGLE_CREDIT,
) -> PanoLookup:
    return PanoLookup(placeKey=placeKey, queryLat=LAT, queryLng=LNG, status=status, panoId=panoId,
                      date=date, panoLat=panoLat, panoLng=panoLng, copyright=copyright)


def makeCollision(placeKey: str, month: str, probability: float, panoId: str = "p",
                  distanceM: float = 0.0, name: str | None = None, source: str = SOURCE_GOOGLE) -> Collision:
    return Collision(placeKey=placeKey, name=name, month=month, panoId=panoId, panoDate=month, panoLat=1.0,
                     panoLng=2.0, distanceM=distanceM, visitCount=1, dwellMinutes=1.0, score=probability,
                     url=f"u-{panoId}", coverage=0.5, visibility=0.3, proximity=1.0, probability=probability,
                     source=source)


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
    assert c.coverage == pytest.approx(coverage(visits, "2022-08"))
    assert c.coverage == pytest.approx(1.5 / (12 * 31))
    assert c.visibility == pytest.approx(visibility(visits, "Cafe"))
    assert c.proximity == pytest.approx(proximity(c.distanceM))
    assert c.probability == pytest.approx(c.coverage * c.visibility * c.proximity)
    assert c.score == c.probability
    assert c.url == buildPanoUrl("pano-a", LAT + 0.0001, LNG)


def testLookupWithoutVisitInMonthGivesNothing():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    assert findCollisions(visits, [makePlace()], [makeLookup(date="2019-03")]) == []


def testMonthCrossingVisitMatchesBothMonths():
    visits = [makeVisit(dt(2022, 8, 31, 22), dt(2022, 9, 1, 1, 30))]
    lookups = [makeLookup(panoId="aug", date="2022-08"), makeLookup(panoId="sep", date="2022-09")]
    byPano = {c.panoId: c for c in findCollisions(visits, [makePlace()], lookups)}
    assert set(byPano) == {"aug", "sep"}
    # 22:00-01:30 UTC is night at lng 13.4, so neither month gets daylight dwell, only night weight.
    assert byPano["aug"].dwellMinutes == pytest.approx(0)
    assert byPano["sep"].dwellMinutes == pytest.approx(0)
    assert byPano["aug"].coverage == pytest.approx(0.05 * 2 / (12 * 31))
    assert byPano["sep"].coverage == pytest.approx(0.05 * 1.5 / (12 * 30))


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


def testDaylightDwellCountsOnlyDaylight():
    # 05:00-09:00 UTC at lng 0: solar 07:00 splits it into 2 h night and 2 h daylight.
    visits = [makeVisit(dt(2022, 8, 5, 5), dt(2022, 8, 5, 9), lng=0.0)]
    place = makePlace(lng=0.0)
    collisions = findCollisions(visits, [place], [makeLookup(panoLng=0.0)])
    assert collisions[0].dwellMinutes == pytest.approx(120)


def testWalkingVisitsGetPathVisibility():
    visits = [Visit(placeId=None, name=None, address=None, lat=LAT, lng=LNG, start=dt(2022, 8, 5, 10),
                    end=dt(2022, 8, 5, 10, 1), source="timeline", kind=KIND_WALKING)]
    [place] = buildPlaces(visits)
    assert place.key.startswith("path:")
    collisions = findCollisions(visits, [place], [makeLookup(placeKey=place.key)])
    assert collisions[0].placeKey == place.key
    assert collisions[0].visibility == pytest.approx(0.9)


# ranking

def testRankCollisionsOrdersByProbabilityThenKeyThenMonth():
    a = makeCollision("b", "2022-01", 1.0)
    b = makeCollision("a", "2022-02", 1.0)
    c = makeCollision("a", "2022-01", 1.0)
    d = makeCollision("z", "2020-01", 5.0)
    assert rankCollisions([a, b, c, d]) == [d, c, b, a]


def testProbabilityGrowsWithDwellAndShrinksWithDistance():
    short = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    long = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 14))]
    near = makeLookup(panoLat=LAT + 0.0001)
    far = makeLookup(panoLat=LAT + 0.0005)
    shortNear = findCollisions(short, [makePlace()], [near])[0].probability
    longNear = findCollisions(long, [makePlace()], [near])[0].probability
    shortFar = findCollisions(short, [makePlace()], [far])[0].probability
    assert longNear > shortNear > shortFar > 0


def testVisitEndingExactlyAtMonthStartDoesNotMatchThatMonth():
    visits = [makeVisit(dt(2022, 8, 31, 22, 0), dt(2022, 9, 1, 0, 0))]
    collisions = findCollisions(visits, [makePlace()], [makeLookup(date="2022-09")])
    assert collisions == []
    collisions = findCollisions(visits, [makePlace()], [makeLookup(date="2022-08")])
    assert len(collisions) == 1 and collisions[0].dwellMinutes == pytest.approx(0)  # night at lng 13.4
    assert collisions[0].probability > 0


# buildCandidates

def testBuildCandidatesMergesPanosOfOnePlaceMonth():
    place = Place(key="a", placeId=None, name="Park", lat=52.5, lng=13.4, visitCount=2, kind=KIND_WALKING)
    weak = makeCollision("a", "2022-08", 0.2, panoId="weak", distanceM=30.0)
    strong = makeCollision("a", "2022-08", 0.5, panoId="strong", distanceM=10.0)
    candidates = buildCandidates([weak, strong], [place])
    assert len(candidates) == 1
    c = candidates[0]
    assert c.rank == 1
    assert (c.placeKey, c.month, c.name) == ("a", "2022-08", "Park")
    assert (c.lat, c.lng, c.kind) == (52.5, 13.4, KIND_WALKING)
    assert c.probability == pytest.approx(0.5)  # the best panorama only; one month is one drive
    assert (c.coverage, c.visibility, c.visitCount, c.dwellMinutes) == (0.5, 0.3, 1, 1.0)
    assert [p["panoId"] for p in c.panos] == ["strong", "weak"]
    assert c.panos[0] == {"panoId": "strong", "date": "2022-08", "url": "u-strong", "lat": 1.0, "lng": 2.0,
                          "distanceM": 10.0, "probability": 0.5, "source": SOURCE_GOOGLE, "copyright": None}
    assert c.source == SOURCE_GOOGLE
    assert c.checkKey == "a|2022-08|google"


def testBuildCandidatesRanksByProbabilityThenKeyThenMonth():
    places = [Place(k, None, None, 0.0, 0.0, 1) for k in ("a", "b", "z")]
    collisions = [
        makeCollision("b", "2022-01", 0.1),
        makeCollision("a", "2022-02", 0.1),
        makeCollision("a", "2022-01", 0.1),
        makeCollision("z", "2020-01", 0.3, panoId="p1"),
        makeCollision("z", "2020-01", 0.3, panoId="p2"),
    ]
    candidates = buildCandidates(collisions, places)
    assert [(c.rank, c.placeKey, c.month) for c in candidates] == [
        (1, "z", "2020-01"), (2, "a", "2022-01"), (3, "a", "2022-02"), (4, "b", "2022-01"),
    ]
    assert candidates[0].probability == pytest.approx(0.3)


def testBuildCandidatesWithoutPlace():
    collision = makeCollision("orphan", "2022-08", 0.4, name="Kiosk")
    candidates = buildCandidates([collision], [])
    assert len(candidates) == 1
    c = candidates[0]
    assert (c.placeKey, c.name, c.kind, c.rank) == ("orphan", "Kiosk", KIND_PLACE, 1)
    assert (c.lat, c.lng) == (1.0, 2.0)
    assert c.probability == pytest.approx(0.4)


def testBuildCandidatesCountsOnlyTheNearestPanoOfAMonth():
    def pano(panoId: str, distanceM: float) -> Collision:
        p = 0.1 * 0.5 * proximity(distanceM)
        return Collision(placeKey="a", name=None, month="2022-08", panoId=panoId, panoDate="2022-08", panoLat=1.0,
                         panoLng=2.0, distanceM=distanceM, visitCount=1, dwellMinutes=1.0, score=p,
                         url=f"u-{panoId}", coverage=0.1, visibility=0.5, proximity=proximity(distanceM),
                         probability=p)

    far, near = pano("far", 80.0), pano("near", 0.0)
    [c] = buildCandidates([far, near], [])
    assert c.probability == pytest.approx(0.05)
    assert c.probability == pytest.approx(c.coverage * c.visibility * max(near.proximity, far.proximity))
    assert [p["panoId"] for p in c.panos] == ["near", "far"]
    assert c.panos[1]["probability"] == pytest.approx(0.05 * proximity(80.0))


def testBuildCandidatesEmpty():
    assert buildCandidates([], []) == []


def testFindCollisionsFeedBuildCandidates():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 12))]
    lookups = [makeLookup(panoId="a", panoLat=LAT + 0.0001), makeLookup(panoId="b", panoLat=LAT - 0.0002)]
    collisions = findCollisions(visits, [makePlace()], lookups)
    candidates = buildCandidates(collisions, [makePlace()])
    assert len(candidates) == 1
    assert candidates[0].probability == pytest.approx(max(c.probability for c in collisions))
    assert candidates[0].dwellMinutes == pytest.approx(120)
    assert [p["panoId"] for p in candidates[0].panos] == ["a", "b"]


# provenance

def testFindCollisionsSetsSourceFromCopyright():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    lookups = [
        makeLookup(panoId="g", copyright="\u00a9 2022 Google"),
        makeLookup(panoId="u", copyright=USER_CREDIT),
        makeLookup(panoId="n", copyright=None),
    ]
    sources = {c.panoId: c.source for c in findCollisions(visits, [makePlace()], lookups)}
    assert sources == {"g": SOURCE_GOOGLE, "u": SOURCE_USER, "n": SOURCE_GOOGLE}


def testRefilledLookupWithCreditWinsTieOverOlderUncreditedRow():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 11))]
    lookups = [makeLookup(panoId="u", copyright=None), makeLookup(panoId="u", copyright=USER_CREDIT)]
    [collision] = findCollisions(visits, [makePlace()], lookups)
    assert collision.source == SOURCE_USER


def testMixedSourcePlaceMonthGivesOneCandidatePerSource():
    visits = [makeVisit(dt(2022, 8, 5, 10), dt(2022, 8, 5, 12))]
    lookups = [
        makeLookup(panoId="google-pano", panoLat=LAT + 0.0004, copyright=GOOGLE_CREDIT),
        makeLookup(panoId="user-pano", panoLat=LAT + 0.00001, copyright=USER_CREDIT),
    ]
    collisions = findCollisions(visits, [makePlace()], lookups)
    byPano = {c.panoId: c for c in collisions}
    candidates = buildCandidates(collisions, [makePlace()], lookups)
    assert len(candidates) == 2
    bySource = {c.source: c for c in candidates}
    assert set(bySource) == {SOURCE_GOOGLE, SOURCE_USER}
    google, user = bySource[SOURCE_GOOGLE], bySource[SOURCE_USER]
    assert google.checkKey == f"{KEY}|2022-08|google"
    assert user.checkKey == f"{KEY}|2022-08|user"
    assert [p["panoId"] for p in google.panos] == ["google-pano"]
    assert [p["panoId"] for p in user.panos] == ["user-pano"]
    assert (google.panos[0]["source"], google.panos[0]["copyright"]) == (SOURCE_GOOGLE, GOOGLE_CREDIT)
    assert (user.panos[0]["source"], user.panos[0]["copyright"]) == (SOURCE_USER, USER_CREDIT)
    assert google.probability == pytest.approx(byPano["google-pano"].probability)
    assert user.probability == pytest.approx(byPano["user-pano"].probability)
    assert user.probability > google.probability  # the photosphere sits nearer the centre
    assert [(c.rank, c.source) for c in candidates] == [(1, SOURCE_USER), (2, SOURCE_GOOGLE)]


def testCheckKeyFormat():
    [c] = buildCandidates([makeCollision("ChIJabc", "2021-03", 0.2, source=SOURCE_USER)], [])
    assert c.checkKey == "ChIJabc|2021-03|user"
    assert c.source == SOURCE_USER
    assert c.panos[0]["source"] == SOURCE_USER
    assert c.panos[0]["copyright"] is None  # no lookups passed


def testRankIsContiguousAcrossSourcesAndTiesPutGoogleFirst():
    collisions = [
        makeCollision("a", "2022-01", 0.1, panoId="u1", source=SOURCE_USER),
        makeCollision("a", "2022-01", 0.1, panoId="g1", source=SOURCE_GOOGLE),
        makeCollision("b", "2022-01", 0.4, panoId="u2", source=SOURCE_USER),
        makeCollision("b", "2022-01", 0.2, panoId="g2", source=SOURCE_GOOGLE),
        makeCollision("c", "2020-05", 0.3, panoId="g3", source=SOURCE_GOOGLE),
    ]
    candidates = buildCandidates(collisions, [])
    assert [(c.rank, c.placeKey, c.source) for c in candidates] == [
        (1, "b", SOURCE_USER), (2, "c", SOURCE_GOOGLE), (3, "b", SOURCE_GOOGLE),
        (4, "a", SOURCE_GOOGLE), (5, "a", SOURCE_USER),
    ]
    assert len({c.checkKey for c in candidates}) == len(candidates)
