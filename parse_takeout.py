from google_takeout_parser.path_dispatch import TakeoutParser
from google_takeout_parser.models import Location

def getPlaceVisits():
    tp = TakeoutParser("takeout")
    # to check if files are all handled
    tp.dispatch_map()
    # to parse with cachew cache https://github.com/karlicoss/cachew
    locations = list(tp.parse())

    print(f"Number of parsed locations: {len(locations)}")
    return locations
