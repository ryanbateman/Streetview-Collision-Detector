import os
import urllib.parse
import logging as log
import asyncio
from typing import List
from aiohttp import TCPConnector
from aiohttp_client_cache import CachedSession, SQLiteBackend
from datetime import datetime
from google_takeout_parser.models import PlaceVisit
from itertools import islice
from tqdm.asyncio import tqdm

from models import StreetViewCollision

# setup
meta_base = 'https://maps.googleapis.com/maps/api/streetview/metadata?'
pic_base = 'https://maps.googleapis.com/maps/api/streetview?'
api_key = os.environ.get('GMAPS_STATIC_API_KEY')
max_workers = 2000

def chunkedIterable(iterable, chunk_size):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, chunk_size))
        if not chunk:
            break
        yield chunk

async def checkAllVisitsForHits(placeVisits: List[PlaceVisit]) -> List[StreetViewCollision]:
	# setup request
	tcp_connection = TCPConnector(limit=max_workers)
	pbar = tqdm(total = len(placeVisits), desc="Requesting Streetview date for each place visited...", unit="requests", colour="#002fa7")
	results = []
	# Cache requests in local SQLite DB, make requests in chunks of tasks
	async with CachedSession(cache=SQLiteBackend('mapsRequestCache', expire_after=60*60*24), connector=tcp_connection) as session:
		for placeVisitChunk in chunkedIterable(placeVisits, max_workers):
			result = await checkChunkForHits(placeVisitChunk, session)
			results.extend(result)
			pbar.update(len(placeVisitChunk))	
		pbar.close()
		await tcp_connection.close()
		return results

async def checkChunkForHits(placeVisitsChunk: List[PlaceVisit], session: CachedSession):
	tasks = []
	
	for placeVisit in placeVisitsChunk:
		# obtain the metadata of the request
		task = asyncio.ensure_future(checkPlaceVisitForHit(placeVisit, session))
		tasks.append(task)

	return await asyncio.gather(*tasks)

async def checkPlaceVisitForHit(placeVisit: PlaceVisit, session: CachedSession):
	# Eliminate the check when the place visit is out of hours
	try:
		if placeVisit.dt.hour > 17 and placeVisit.dt.hour < 7:
			log.debug("Discarded")
	except AttributeError:
		log.error(placeVisit)
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
		elif response_json['status'] == "ZERO_RESULTS" or response_json['status'] == "NOT_FOUND":
			log.debug("No results")
		elif response_json['status'] == "UNKNOWN_ERROR" or response_json['status'] == "REQUEST_DENIED":
			log.debug(f"Error with request: {response_json['status']} {meta_response.url}")
		else:
			# Some streetview panos don't have a date
			if 'date' in response_json: 	
				date = datetime.strptime(response_json['date'], "%Y-%m")
				if date.month == placeVisit.dt.month and date.year == placeVisit.dt.year:
					# params = urllib.parse.urlencode({'query_place_id': f"{[placeVisit.location['placeId']]}", 'query': f"{placeVisit.lat},{placeVisit.lng}"})			
					paramArray = {'api': '1', 'map_action': 'pano', 'viewpoint': f"{placeVisit.lat},{placeVisit.lng}"}
					if placeVisit is PlaceVisit:
						paramArray = paramArray + {'pano': placeVisit.location['placeId']}			
					params = urllib.parse.urlencode(paramArray)
					return StreetViewCollision(placeVisit, f"https://www.google.com/maps/@?{params}", date)
			else:
				log.debug("Miss")