"""Collapse visits into distinct places and generate the sample points to query.

Each place is queried at its centre plus a ring of offsets so that panoramas on
surrounding streets are found even when the place itself sits back from the road. Points on
walking or cycling paths are already on the street, so they are queried at the centre only.
"""
import math
from collections import defaultdict
from typing import Iterable

from svcd.models import KIND_PLACE, Place, SamplePoint, Visit

METRES_PER_DEG_LAT = 111_320.0
EARTH_RADIUS_M = 6_371_000.0
COORD_DECIMALS = 5
PATH_COORD_DECIMALS = 4  # ~10 m, so nearby walking or cycling points merge into one place
PATH_KEY_PREFIX = "path:"


def placeKey(visit: Visit) -> str:
    """Group key: the Google placeId when known, else the rounded coordinate.

    Stays round to ~1 m ("52.51631,13.37773"); path points (walking, cycling) round to ~10 m so
    they merge, and carry a prefix and fixed 4 decimals ("path:52.5163,13.3777") so a path key can
    never equal a stay key that happens to round to the same digits.
    """
    if visit.placeId:
        return visit.placeId
    if visit.kind == KIND_PLACE:
        return f"{round(visit.lat, COORD_DECIMALS)},{round(visit.lng, COORD_DECIMALS)}"
    return f"{PATH_KEY_PREFIX}{visit.lat:.{PATH_COORD_DECIMALS}f},{visit.lng:.{PATH_COORD_DECIMALS}f}"


def _pathKeyCentre(key: str) -> tuple[float, float] | None:
    """(lat, lng) encoded in a path key, else None."""
    if not key.startswith(PATH_KEY_PREFIX):
        return None
    lat, lng = key[len(PATH_KEY_PREFIX):].split(",")
    return float(lat), float(lng)


def buildPlaces(visits: Iterable[Visit]) -> list[Place]:
    """Deduplicate visits into places, most visited first (ties broken by key).

    A place's kind is its visits' kind when they all agree, else KIND_PLACE. A path place keyed by
    its rounded coordinate is centred on that coordinate rather than on the mean of its points, so
    the key and the centre stay in step when visits are re-ingested.
    """
    groups: dict[str, list[Visit]] = defaultdict(list)
    for visit in visits:
        groups[placeKey(visit)].append(visit)

    places = []
    for key, group in groups.items():
        centre = _pathKeyCentre(key)
        if centre is None:
            centre = (sum(v.lat for v in group) / len(group), sum(v.lng for v in group) / len(group))
        places.append(Place(
            key=key,
            placeId=group[0].placeId or None,
            name=next((v.name for v in group if v.name is not None), None),
            lat=centre[0],
            lng=centre[1],
            visitCount=len(group),
            kind=_commonKind(group),
        ))
    places.sort(key=lambda p: (-p.visitCount, p.key))
    return places


def _commonKind(group: list[Visit]) -> str:
    kinds = {v.kind for v in group}
    return kinds.pop() if len(kinds) == 1 else KIND_PLACE


def ringPoints(lat: float, lng: float, radiusM: float, count: int) -> list[tuple[float, float]]:
    """count points evenly spaced on a circle of radiusM metres around (lat, lng)."""
    if count <= 0:
        return []
    dLat = radiusM / METRES_PER_DEG_LAT
    dLng = dLat / math.cos(math.radians(lat))
    points = []
    for k in range(count):
        angle = 2 * math.pi * k / count
        points.append((lat + dLat * math.cos(angle), lng + dLng * math.sin(angle)))
    return points


def buildSamplePoints(
    places: Iterable[Place], ringCount: int = 6, ringRadiusM: float = 40.0
) -> list[SamplePoint]:
    """Centre (ringIndex 0) plus ring points (1..ringCount) per place.

    Only KIND_PLACE places get a ring; path places (walking, cycling) get the centre only.

    Deduplication is per place, so two places at the same spot (shops in one building) each keep
    their points and each can collide. Identical coordinates across places cost no extra API calls
    because the request cache serves the repeat.
    """
    samples: list[SamplePoint] = []
    for place in places:
        seen: set[tuple[float, float]] = set()
        coords = [(place.lat, place.lng)]
        if place.kind == KIND_PLACE:
            coords += ringPoints(place.lat, place.lng, ringRadiusM, ringCount)
        for ringIndex, (lat, lng) in enumerate(coords):
            rounded = (round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS))
            if rounded in seen:
                continue
            seen.add(rounded)
            samples.append(SamplePoint(place.key, rounded[0], rounded[1], ringIndex))
    return samples


def haversineM(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dPhi = phi2 - phi1
    dLambda = math.radians(lng2 - lng1)
    a = math.sin(dPhi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dLambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))
