import get_metadata, parse_takeout
import logging as log
import asyncio
from tqdm.asyncio import tqdm

log.basicConfig(level=log.INFO)
log.info("Starting search on streetview...")

async def main():
    placeVisits = parse_takeout.getPlaceVisits()
    tasklist = []
    log.info("Starting place visit check")
    for placeVisit in placeVisits[0:1000]:
        tasklist.append(asyncio.create_task(get_metadata.checkPlaceVisit(placeVisit)))
    pbar = tqdm(asyncio.as_completed(tasklist), total=len(tasklist))
    res = [await t for t in pbar]

asyncio.run(main())