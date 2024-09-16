import get_metadata, parse_takeout
import logging as log
from tqdm import tqdm

log.info("Starting search on streetview...")
placeVisits = parse_takeout.getPlaceVisits()
for placeVisit in tqdm(placeVisits):
    get_metadata.checkPlaceVisit(placeVisit)
