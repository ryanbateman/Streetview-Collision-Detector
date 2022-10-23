import requests_cache, os
import urllib.parse
from datetime import datetime, date
from google_takeout_parser.models import PlaceVisit

# setup
meta_base = 'https://maps.googleapis.com/maps/api/streetview/metadata?'
pic_base = 'https://maps.googleapis.com/maps/api/streetview?'
api_key = os.environ.get('GMAPS_STATIC_API_KEY')

# setup request
session = requests_cache.CachedSession('maps_cache', expire_after=360)

def checkPlaceVisit(placeVisit: PlaceVisit):
	# Eliminate the check when the place visit is out of hours
	if placeVisit.startTime.hour > 17 and placeVisit.startTime.hour < 7:
		return
	# obtain the metadata of the request
	meta_params = {'key': api_key, 'location': f"{placeVisit.lat},{placeVisit.lng}"}
	meta_response = session.get(meta_base, params=meta_params)

	# Parse response
	response_json = meta_response.json()
	if meta_response.status_code != 200:
		print(f"Failed API call {meta_response.status_code}")
		print(response_json)
		return
	elif response_json['status'] == "ZERO_RESULTS":
		return
	elif response_json['status'] == "UNKNOWN_ERROR" or response_json['status'] == "REQUEST_DENIED":
		print(f"Error with request: {response_json['status']}")
		return
	else: 	
		date = datetime.strptime(response_json['date'], "%Y-%m")
		if date.month == placeVisit.startTime.month and date.year == placeVisit.startTime.year:
			print("----")
			print(f"Collision! Your visit was within the month of Streetview visits for {placeVisit.location['address']}")
			print(f"Streetview date: {date.year}-{date.month}")
			print(f"You visited date: {placeVisit.startTime}")
			params = urllib.parse.urlencode({'query_place_id': f"{[placeVisit.location['placeId']]}", 'query': f"{placeVisit.lat},{placeVisit.lng}"})
			print(f"Maps: https://www.google.com/maps/search/?api=1&{params}")
			print("----")
			print("")
		
