"""Collapse visits into distinct places and generate the sample points to query.

Each place is queried at its centre plus a ring of offsets so that panoramas on
surrounding streets are found even when the place itself sits back from the road.
"""
import math
from collections import defaultdict
from typing import Iterable

from svcd.models import Place, SamplePoint, Visit

METRES_PER_DEG_LAT = 111_320.0
EARTH_RADIUS_M = 6_371_000.0
COORD_DECIMALS = 5


def placeKey(visit: Visit) -> str:
    """Group key: the Google placeId when known, else the coordinate rounded to ~1 m."""
    if visit.placeId:
        return visit.placeId
    return f"{round(visit.lat, COORD_DECIMALS)},{round(visit.lng, COORD_DECIMALS)}"


def buildPlaces(visits: Iterable[Visit]) -> list[Place]:
    """Deduplicate visits into places, most visited first (ties broken by key)."""
    groups: dict[str, list[Visit]] = defaultdict(list)
    for visit in visits:
        groups[placeKey(visit)].append(visit)

    places = []
    for key, group in groups.items():
        places.append(Place(
            key=key,
            placeId=group[0].placeId or None,
            name=next((v.name for v in group if v.name is not None), None),
            lat=sum(v.lat for v in group) / len(group),
            lng=sum(v.lng for v in group) / len(group),
            visitCount=len(group),
        ))
    places.sort(key=lambda p: (-p.visitCount, p.key))
    return places


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

    Deduplication is per place, so two places at the same spot (shops in one building) each keep
    their points and each can collide. Identical coordinates across places cost no extra API calls
    because the request cache serves the repeat.
    """
    samples: list[SamplePoint] = []
    for place in places:
        seen: set[tuple[float, float]] = set()
        coords = [(place.lat, place.lng)] + ringPoints(place.lat, place.lng, ringRadiusM, ringCount)
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
