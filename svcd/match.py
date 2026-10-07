"""MATCH stage: turn visits, places and pano lookups into ranked Collision and Candidate records.

A collision is a (place, panorama, month) where the user stayed at the place during
the calendar month (UTC) in which the panorama was photographed. Each collision carries the
probability that this panorama caught the user (see svcd.scoring); a Candidate groups the
collisions of one place and month. Pano dates have month granularity, so several panoramas at one
place in one month are almost always a single drive by one car: the candidate counts only the
nearest of them, coverage * visibility * max(proximity), rather than treating them as independent.
"""
import logging as log
from dataclasses import replace
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from svcd.models import KIND_PLACE, Candidate, Collision, PanoLookup, Place, Visit
from svcd.places import haversineM, placeKey
from svcd.scoring import coverage, daylightDwell, proximity, visibility
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
    missingPlaces = 0
    for placeKey, placeLookups in _usableLookups(lookups).items():
        placeVisits = visitsByKey.get(placeKey)
        if not placeVisits:
            continue
        place = placeByKey.get(placeKey)
        if place is not None:
            centreLat, centreLng, name = place.lat, place.lng, place.name
        else:
            missingPlaces += 1
            log.debug(f"No Place record for {placeKey}; using mean visit position")
            centreLat = sum(v.lat for v in placeVisits) / len(placeVisits)
            centreLng = sum(v.lng for v in placeVisits) / len(placeVisits)
            name = next((v.name for v in placeVisits if v.name), None)
        placeVisibility = visibility(placeVisits, name)

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
            daylight, _ = daylightDwell(matching, panoMonth)
            placeCoverage = coverage(matching, panoMonth)
            panoProximity = proximity(distance)
            probability = placeCoverage * placeVisibility * panoProximity
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
                dwellMinutes=daylight.total_seconds() / 60,
                score=probability,
                url=buildPanoUrl(panoId, panoLat, panoLng),
                coverage=placeCoverage,
                visibility=placeVisibility,
                proximity=panoProximity,
                probability=probability,
            ))
    if missingPlaces:
        log.warning(f"{missingPlaces} places with visits and panoramas have no Place record; "
                    "used the mean visit position (rerun ingest to rebuild places)")
    return collisions


def rankCollisions(collisions: Iterable[Collision]) -> list[Collision]:
    """Most probable first; ties broken by placeKey, month, panoId for deterministic output."""
    return sorted(collisions, key=lambda c: (-c.probability, c.placeKey, c.month, c.panoId))


def buildCandidates(collisions: Iterable[Collision], places: Iterable[Place]) -> list[Candidate]:
    """One Candidate per (placeKey, month), ranked 1..n by probability (ties: placeKey, month).

    Its probability is coverage * visibility * max(proximity) over its distinct panoramas, which is
    the best panorama's own probability since all of them share the place-month's coverage and
    visibility. Several panoramas in one month are treated as one drive, not as independent chances.
    A panorama listed twice keeps its best entry. A collision without a Place record still yields a
    Candidate, centred on its nearest panorama.
    """
    placeByKey = {place.key: place for place in places}
    groups: dict[tuple[str, str], dict[str, Collision]] = defaultdict(dict)
    for collision in collisions:
        byPano = groups[(collision.placeKey, collision.month)]
        known = byPano.get(collision.panoId)
        if known is None or collision.probability > known.probability:
            byPano[collision.panoId] = collision

    unranked: list[Candidate] = []
    for (key, month), byPano in groups.items():
        members = sorted(byPano.values(), key=lambda c: (-c.probability, c.distanceM, c.panoId))
        first = members[0]
        place = placeByKey.get(key)
        if place is not None:
            lat, lng, kind, name = place.lat, place.lng, place.kind, place.name
        else:
            nearest = min(members, key=lambda c: (c.distanceM, c.panoId))
            lat, lng, kind, name = nearest.panoLat, nearest.panoLng, KIND_PLACE, first.name
        unranked.append(Candidate(
            rank=0,
            placeKey=key,
            name=name if name is not None else first.name,
            month=month,
            lat=lat,
            lng=lng,
            kind=kind,
            probability=first.probability,
            coverage=max(c.coverage for c in members),
            visibility=max(c.visibility for c in members),
            visitCount=max(c.visitCount for c in members),
            dwellMinutes=max(c.dwellMinutes for c in members),
            panos=[{
                "panoId": c.panoId,
                "date": c.panoDate,
                "url": c.url,
                "lat": c.panoLat,
                "lng": c.panoLng,
                "distanceM": c.distanceM,
                "probability": c.probability,
            } for c in members],
        ))

    unranked.sort(key=lambda c: (-c.probability, c.placeKey, c.month))
    return [replace(candidate, rank=rank) for rank, candidate in enumerate(unranked, start=1)]
