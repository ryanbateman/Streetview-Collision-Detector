from datetime import datetime, timezone

import pytest

from svcd.models import Place, Visit
from svcd.places import buildPlaces, buildSamplePoints, haversineM, placeKey, ringPoints


def makeVisit(**overrides) -> Visit:
    base = dict(
        placeId="ChIJtest", name="Cafe", address="1 Test St", lat=52.51631, lng=13.37773,
        start=datetime(2022, 8, 31, 22, 0, tzinfo=timezone.utc),
        end=datetime(2022, 9, 1, 1, 30, tzinfo=timezone.utc), source="semantic",
    )
    base.update(overrides)
    return Visit(**base)


def test_placeKey_uses_placeId_when_present():
    assert placeKey(makeVisit()) == "ChIJtest"


def test_placeKey_falls_back_to_rounded_coordinate():
    visit = makeVisit(placeId=None, lat=52.516314999, lng=13.377734001)
    assert placeKey(visit) == "52.51631,13.37773"


def test_buildPlaces_merges_by_placeId_and_averages():
    visits = [
        makeVisit(name=None, lat=52.0, lng=13.0),
        makeVisit(name="Cafe A", lat=52.2, lng=13.4),
        makeVisit(name="Cafe B", lat=52.4, lng=13.2),
        makeVisit(placeId="ChIJother", name="Bar", lat=48.0, lng=11.0),
        makeVisit(placeId=None, name=None, lat=40.0, lng=-3.0),
        makeVisit(placeId=None, name="Park", lat=40.000001, lng=-3.000001),
    ]
    places = buildPlaces(visits)
    assert [p.key for p in places] == ["ChIJtest", "40.0,-3.0", "ChIJother"]

    cafe = places[0]
    assert cafe.placeId == "ChIJtest"
    assert cafe.name == "Cafe A"
    assert cafe.visitCount == 3
    assert cafe.lat == pytest.approx(52.2)
    assert cafe.lng == pytest.approx(13.2)

    park = places[1]
    assert park.placeId is None
    assert park.name == "Park"
    assert park.visitCount == 2


def test_buildPlaces_empty():
    assert buildPlaces([]) == []


@pytest.mark.parametrize("lat", [52.5, 0.0, 70.0])
@pytest.mark.parametrize("radius", [25.0, 40.0, 100.0])
def test_ringPoints_lie_on_circle(lat, radius):
    lng = 13.4
    points = ringPoints(lat, lng, radius, 8)
    assert len(points) == 8
    for pLat, pLng in points:
        assert abs(haversineM(lat, lng, pLat, pLng) - radius) < 1.0


def test_ringPoints_count_zero():
    assert ringPoints(52.5, 13.4, 40.0, 0) == []


def test_haversine_known_distance():
    # One degree of latitude is ~111.2 km on the mean-radius sphere.
    assert haversineM(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-3)
    assert haversineM(52.5, 13.4, 52.5, 13.4) == 0.0


def test_buildSamplePoints_centre_plus_ring():
    places = [
        Place("a", None, "A", 52.5, 13.4, 3),
        Place("b", None, "B", 48.1, 11.6, 1),
    ]
    samples = buildSamplePoints(places, ringCount=6, ringRadiusM=40.0)
    assert len(samples) == 14
    for key in ("a", "b"):
        mine = [s for s in samples if s.placeKey == key]
        assert [s.ringIndex for s in mine] == list(range(7))
        assert (mine[0].lat, mine[0].lng) == next((p.lat, p.lng) for p in places if p.key == key)
    for s in samples:
        assert round(s.lat, 5) == s.lat and round(s.lng, 5) == s.lng


def test_buildSamplePoints_colocated_places_each_keep_their_points():
    # Two shops in one building: both must be sampled, or the second can never collide.
    places = [
        Place("first", "ChIJ1", "One", 52.5, 13.4, 5),
        Place("second", "ChIJ2", "Two", 52.5, 13.4, 2),
    ]
    samples = buildSamplePoints(places, ringCount=4, ringRadiusM=40.0)
    assert len(samples) == 10
    assert sorted(s.placeKey for s in samples) == ["first"] * 5 + ["second"] * 5
    assert len({(s.lat, s.lng) for s in samples}) == 5  # identical coordinates, served from cache


def test_buildSamplePoints_dedupes_within_a_place():
    # At the pole-ish latitudes rounding can collapse ring points; the same place never lists a coordinate twice.
    samples = buildSamplePoints([Place("p", None, None, 52.5, 13.4, 1)], ringCount=12, ringRadiusM=0.2)
    assert len(samples) == len({(s.lat, s.lng) for s in samples})


def test_buildSamplePoints_no_ring():
    samples = buildSamplePoints([Place("a", None, None, 1.0, 2.0, 1)], ringCount=0)
    assert [(s.lat, s.lng, s.ringIndex) for s in samples] == [(1.0, 2.0, 0)]
