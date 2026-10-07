"""MATCH stage: turn visits, places and pano lookups into ranked Collision records.

A collision is a (place, panorama, month) where the user stayed at the place during
the calendar month (UTC) in which the panorama was photographed.
"""
import logging as log
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from svcd.models import Collision, PanoLookup, Place, Visit
from svcd.places import haversineM, placeKey
from svcd.streetview import buildPanoUrl


def _asUtc(value: datetime) -> datetime:
    """Naive datetimes are taken to be UTC; aware ones are converted."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _monthBounds(month: str) -> tuple[datetime, datetime]:
    """[first instant, first instant of next month) for a "YYYY-MM" string, in UTC."""
    year, mon = int(month[:4]), int(month[5:7])
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if mon == 12 else datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    return start, end


def monthsSpanned(start: datetime, end: datetime) -> list[str]:
    """Every "YYYY-MM" from start to end inclusive (UTC). Returns [] when end < start."""
    start, end = _asUtc(start), _asUtc(end)
    if end < start:
        return []
    months: list[str] = []
    year, mon = start.year, start.month
    while (year, mon) <= (end.year, end.month):
        months.append(f"{year:04d}-{mon:02d}")
        mon += 1
        if mon > 12:
            year, mon = year + 1, 1
    return months


def dwellInMonth(visit: Visit, month: str) -> timedelta:
    """Length of the overlap of [visit.start, visit.end] with the UTC calendar month."""
    monthStart, monthEnd = _monthBounds(month)
    overlapStart = max(_asUtc(visit.start), monthStart)
    overlapEnd = min(_asUtc(visit.end), monthEnd)
    if overlapEnd <= overlapStart:
        return timedelta(0)
    return overlapEnd - overlapStart


def scoreCollision(dwellMinutes: float, visitCount: int, distanceM: float) -> float:
    return math.log1p(dwellMinutes) + 0.5 * math.log1p(visitCount) - distanceM / 200


def visitKey(visit: Visit) -> str:
    """Same key the lookup stage uses for a place."""
    return placeKey(visit)


def _panoPosition(lookup: PanoLookup) -> tuple[float, float]:
    """Pano coordinates, falling back to the query coordinates when the API omitted them."""
    lat = lookup.panoLat if lookup.panoLat is not None else lookup.queryLat
    lng = lookup.panoLng if lookup.panoLng is not None else lookup.queryLng
    return lat, lng


def _usableLookups(lookups: Iterable[PanoLookup]) -> dict[str, list[PanoLookup]]:
    byPlace: dict[str, list[PanoLookup]] = defaultdict(list)
    for lookup in lookups:
        if lookup.status != "OK" or lookup.panoId is None:
            continue
        if lookup.date is None or len(lookup.date) < 7:
            continue
        byPlace[lookup.placeKey].append(lookup)
    return byPlace


def findCollisions(
    visits: Iterable[Visit],
    places: Iterable[Place],
    lookups: Iterable[PanoLookup],
) -> list[Collision]:
    """One Collision per (placeKey, panoId, month) with at least one visit in that month."""
    placeByKey = {place.key: place for place in places}
    visitsByKey: dict[str, list[Visit]] = defaultdict(list)
    for visit in visits:
        visitsByKey[visitKey(visit)].append(visit)
    visitMonths = {id(v): set(monthsSpanned(v.start, v.end)) for vs in visitsByKey.values() for v in vs}

    collisions: list[Collision] = []
    for placeKey, placeLookups in _usableLookups(lookups).items():
        placeVisits = visitsByKey.get(placeKey)
        if not placeVisits:
            continue
        place = placeByKey.get(placeKey)
        if place is not None:
            centreLat, centreLng, name = place.lat, place.lng, place.name
        else:
            log.warning(f"No Place record for {placeKey}; using mean visit position")
            centreLat = sum(v.lat for v in placeVisits) / len(placeVisits)
            centreLng = sum(v.lng for v in placeVisits) / len(placeVisits)
            name = next((v.name for v in placeVisits if v.name), None)

        # Several ring samples often hit the same panorama: keep the one nearest the centre.
        best: dict[tuple[str, str], tuple[float, PanoLookup]] = {}
        for lookup in placeLookups:
            panoMonth = lookup.date[:7]
            lat, lng = _panoPosition(lookup)
            distance = haversineM(centreLat, centreLng, lat, lng)
            dedupeKey = (lookup.panoId, panoMonth)
            if dedupeKey not in best or distance < best[dedupeKey][0]:
                best[dedupeKey] = (distance, lookup)

        for (panoId, panoMonth), (distance, lookup) in best.items():
            matching = [v for v in placeVisits
                        if panoMonth in visitMonths[id(v)] and dwellInMonth(v, panoMonth) > timedelta(0)]
            if not matching:
                continue
            dwellMinutes = sum(dwellInMonth(v, panoMonth).total_seconds() for v in matching) / 60
            panoLat, panoLng = _panoPosition(lookup)
            collisions.append(Collision(
                placeKey=placeKey,
                name=name,
                month=panoMonth,
                panoId=panoId,
                panoDate=lookup.date,
                panoLat=panoLat,
                panoLng=panoLng,
                distanceM=distance,
                visitCount=len(matching),
                dwellMinutes=dwellMinutes,
                score=scoreCollision(dwellMinutes, len(matching), distance),
                url=buildPanoUrl(panoId, panoLat, panoLng),
            ))
    return collisions


def rankCollisions(collisions: Iterable[Collision]) -> list[Collision]:
    """Highest score first; ties broken by placeKey, month, panoId for deterministic output."""
    return sorted(collisions, key=lambda c: (-c.score, c.placeKey, c.month, c.panoId))
