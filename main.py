from datetime import datetime, timezone
from get_metadata import checkAllVisitsForHits
from google_takeout_parser.models import Location
import parse_takeout
import logging as log
import asyncio

log.info("Starting search on streetview...")
log.basicConfig(level=log.ERROR)

async def main():
    placeVisits = parse_takeout.getPlaceVisits()
    # log.basicConfig(level=log.INFO)
    # log.info("Starting place visit check")
    results = await checkAllVisitsForHits(placeVisits)
    #test = Location(lat=52.48568, lng=13.3765661, accuracy=4.0, dt=datetime(2022, 8, 8, 12, 00, 40, tzinfo=timezone.utc))
    #results = await checkAllVisitsForHits(list([test]))
    results = filter(None, results)
    for successfulHit in results:
        successfulHit.printDetails()
    
asyncio.run(main())