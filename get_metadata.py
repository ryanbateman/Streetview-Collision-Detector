import os
import urllib.parse
import logging as log
import asyncio
from typing import List
from aiohttp import ClientSession, TCPConnector
from datetime import datetime
from google_takeout_parser.models import PlaceVisit
from itertools import islice
from tqdm.asyncio import tqdm

# setup
meta_base = 'https://maps.googleapis.com/maps/api/streetview/metadata?'
pic_base = 'https://maps.googleapis.com/maps/api/streetview?'
api_key = os.environ.get('GMAPS_STATIC_API_KEY')
max_workers = 100

def chunkedIterable(iterable, chunk_size):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, chunk_size))
        if not chunk:
            break
        yield chunk

async def checkAllVisitsForHits(placeVisits: List[PlaceVisit]):
	# setup request
	tcp_connection = TCPConnector(limit=max_workers)
	
	async with ClientSession(connector=tcp_connection) as session:
		for placeVisit in chunkedIterable(placeVisits, max_workers):
			results = await checkChunkForHits(placeVisit, session)
		await tcp_connection.close()
		return 

async def checkChunkForHits(placeVisitsChunk: List[PlaceVisit], session: ClientSession):
	tasks = []
	
	for placeVisit in placeVisitsChunk:
		# obtain the metadata of the request
		task = asyncio.ensure_future(checkPlaceVisitForHit(placeVisit, session))
		tasks.append(task)
	return await tqdm.gather(*tasks)

async def checkPlaceVisitForHit(placeVisit: PlaceVisit, session: ClientSession):
	# Eliminate the check when the place visit is out of hours
	if placeVisit.dt.hour > 17 and placeVisit.dt.hour < 7:
		log.debug("Discarded")
		return
	
	# Set up a request
	meta_params = {'key': api_key, 'location': f"{placeVisit.lat},{placeVisit.lng}"}
	log.debug("Processing...")
	async with session.get(meta_base, params=meta_params) as meta_response:
		# Parse response
		log.debug(f"Returned: {meta_response.status}")
		response_json = await meta_response.json()
		if meta_response.status != 200:
			log.error(f"Failed API call {meta_response.status_code}")
			log.error(response_json)
			return
		elif response_json['status'] == "ZERO_RESULTS":
			log.info("No results")
			return
		elif response_json['status'] == "UNKNOWN_ERROR" or response_json['status'] == "REQUEST_DENIED":
			log.error(f"Error with request: {response_json['status']}")
			return
		else: 	
			date = datetime.strptime(response_json['date'], "%Y-%m")
			if date.month == placeVisit.dt.month and date.year == placeVisit.dt.year:
				log.info("Hit!")
				log.info("----")
				log.info(f"Collision! Your visit was within the month of Streetview visits for {placeVisit.location['address']}")
				log.info(f"Streetview date: {date.year}-{date.month}")
				log.info(f"You visited date: {placeVisit.dt}")
				params = urllib.parse.urlencode({'query_place_id': f"{[placeVisit.location['placeId']]}", 'query': f"{placeVisit.lat},{placeVisit.lng}"})
				log.info(f"Maps: https://www.google.com/maps/search/?api=1&{params}")
				log.info("----")
				log.info("")
			else:
				log.debug("Miss")
			return