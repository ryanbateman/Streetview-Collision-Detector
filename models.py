import datetime
from google_takeout_parser.models import PlaceVisit, Location

class StreetViewCollision:
    def __init__(self, placeVisit: PlaceVisit, urlOfMap, dateOfStreetviewPhoto: datetime):
        self.placeVisit = placeVisit
        self.lat = placeVisit.lat
        self.lng = placeVisit.lng
        self.urlOfMap = urlOfMap
        self.dateOfStreetviewPhoto = dateOfStreetviewPhoto
    
    def printDetails(self):
        print("Hit!")
        print("----")
        if self.placeVisit is PlaceVisit and self.placeVisit['address']:
            print(f"Collision! Your visit was within the month of Streetview visits for {self.placeVisit['address']}")
        else:
            print(f"Collision! Your visit was within the month of Streetview visits for {self.placeVisit.address}")
        if self.placeVisit.dt:
            print(f"You visited date: {self.placeVisit.dt}")
        print(f"Streetview date: {self.dateOfStreetviewPhoto.year}-{self.dateOfStreetviewPhoto.month}")
        print(f"Maps: {self.urlOfMap}")
        print("----")
        print("")
