import get_metadata, parse_takeout
import logging as log

log.info("Starting search on streetview...")
placeVisits = parse_takeout.getPlaceVisits()
for placeVisit in placeVisits:
    get_metadata.checkPlaceVisit(placeVisit)
