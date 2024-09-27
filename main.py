from datetime import datetime, timezone
from get_metadata import checkAllVisitsForHits
from google_takeout_parser.models import Location
from models import StreetViewCollision
import pandas as pd
import parse_takeout
from typing import List
import logging as log
import asyncio
import folium
from folium import LayerControl, plugins
from folium.plugins import HeatMap

map_obj = folium.Map(location=[52.48568, 13.3765661],zoom_start=7)

log.info("Starting search on streetview...")
log.basicConfig(level=log.ERROR)

marker_cluster = plugins.MarkerCluster(
    name='Markercluster',
    overlay=True,
    control=False,
    icon_create_function=None
)

async def main():
    placeVisits = parse_takeout.getPlaceVisits()
    log.basicConfig(level=log.INFO)
    log.info("Starting place visit check")
    results = await checkAllVisitsForHits(placeVisits)
    #test = Location(lat=52.48568, lng=13.3765661, accuracy=4.0, dt=datetime(2022, 8, 8, 12, 00, 40, tzinfo=timezone.utc))
    #test2 = Location(lat=52.48568, lng=13.3765661, accuracy=4.0, dt=datetime(2022, 8, 8, 12, 00, 40, tzinfo=timezone.utc))
    #results = await checkAllVisitsForHits(list([test, test2]))
    
    filtered_results = list(filter(lambda item: item is not None, results))
    results_df = pd.DataFrame([vars(result) for result in filtered_results])
    results_df = results_df.value_counts(['lat', 'lng', 'dateOfStreetviewPhoto', 'urlOfMap']).reset_index().rename(columns={0:'count'})
    results_df.to_pickle('output.pkl')
    
    showMap(filtered_results, results_df)

def showMap(results: List[StreetViewCollision], dataframe: pd.DataFrame):
    for index, result in dataframe.iterrows():
        location = result['lat'], result['lng']
        marker = folium.Marker(location = location, 
                               popup = folium.Popup(
                                   f"""Date of collision: {result['dateOfStreetviewPhoto']}<br/>Location pings: {result['count']}<br/><a href=\"{result['urlOfMap']}\">Map</a>""",
                                   max_width="300")
                               )
        marker_cluster.add_child(marker)

    HeatMap(dataframe[['lat', 'lng', 'count']]).add_to(map_obj)
    marker_cluster.add_to(map_obj)
    folium.LayerControl().add_to(map_obj)
    map_obj.save("heatmap.html")
    map_obj


asyncio.run(main())