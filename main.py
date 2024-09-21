from get_metadata import checkAllVisitsForHits
import parse_takeout
import logging as log
import asyncio

log.info("Starting search on streetview...")
log.basicConfig(level=log.ERROR)

async def main():
    placeVisits = parse_takeout.getPlaceVisits()
    log.basicConfig(level=log.INFO)
    log.info("Starting place visit check")
    await checkAllVisitsForHits(placeVisits)
    
asyncio.run(main())