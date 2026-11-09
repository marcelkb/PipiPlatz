import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def poi(**tags):
    return app.to_poi({"type": "node", "id": 1, "lat": 50.0, "lon": 8.0, "tags": tags})

class ExplicitNoWc(unittest.TestCase):
    """OSM says toilets=no: the card must not show the green "WC vorhanden" (B16, T15)."""

    def test_a_petrol_station_tagged_no_is_dropped(self):
        self.assertIsNone(poi(amenity="fuel", name="Aral", toilets="no"))

    def test_a_petrol_station_without_the_tag_keeps_its_wc(self):
        p = poi(amenity="fuel", name="Aral")
        self.assertEqual((p["kind"], p["wc"]), ("tank", True))

    def test_a_petrol_station_tagged_yes_keeps_its_wc(self):
        self.assertTrue(poi(amenity="fuel", name="Aral", toilets="yes")["wc"])

    def test_a_motorway_service_area_tagged_no_has_no_wc(self):
        p = poi(highway="services", name="Raststätte Nord", toilets="no")
        self.assertEqual(p["kind"], "raststaette")
        self.assertIs(p["wc"], False)

    def test_a_motorway_service_area_otherwise_has_a_wc(self):
        self.assertIs(poi(highway="services", name="Raststätte Nord")["wc"], True)
        self.assertIs(poi(highway="services", toilets="yes")["wc"], True)

    def test_a_rest_area_tagged_no_has_no_wc(self):
        self.assertIs(poi(highway="rest_area", toilets="no")["wc"], False)
        self.assertIs(poi(highway="rest_area")["wc"], True)

    def test_the_kinds_that_were_already_dropped_still_are(self):
        for tags in ({"shop": "supermarket", "name": "Edeka X"}, {"shop": "furniture", "name": "IKEA"},
                     {"shop": "doityourself", "name": "OBI"}, {"shop": "mall", "name": "Center"},
                     {"railway": "station", "name": "Hbf"},
                     {"amenity": "fast_food", "name": "McDonald's"}):
            with self.subTest(tags=tags):
                self.assertIsNone(poi(toilets="no", **tags))

    def test_a_public_toilet_is_a_wc(self):
        p = poi(amenity="toilets")
        self.assertEqual((p["kind"], p["wc"]), ("wc", True))

if __name__ == "__main__":
    unittest.main()
