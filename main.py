from get_metadata import checkAllVisitsForHits
import parse_takeout
import logging as log
import asyncio

log.basicConfig(level=log.INFO)
log.info("Starting search on streetview...")

async def main():
    placeVisits = parse_takeout.getPlaceVisits()
    log.info("Starting place visit check")
    await checkAllVisitsForHits(placeVisits)
    
asyncio.run(main())