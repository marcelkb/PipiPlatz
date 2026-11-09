import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def el(tags):
    return {"type": "way", "id": 1, "center": {"lat": 51.0, "lon": 7.0}, "tags": tags}

class SubName(unittest.TestCase):
    """A rest area is listed as "Rastplatz"; its own name (Schwarze Heide, Ruraue West ...) goes under it."""

    def test_rest_area_keeps_its_osm_name_as_sub(self):
        p = app.to_poi(el({"highway": "rest_area", "name": "Schwarze Heide", "toilets": "yes"}))
        self.assertEqual(p["name"], "Rastplatz")
        self.assertEqual(p["sub"], "Schwarze Heide")

    def test_service_area_too(self):
        p = app.to_poi(el({"highway": "services", "name": "Waldblick Süd"}))
        self.assertEqual(p["sub"], "Waldblick Süd")

    def test_unnamed_rest_area_has_no_sub(self):
        p = app.to_poi(el({"highway": "rest_area", "toilets": "yes"}))
        self.assertEqual(p["sub"], "")

    def test_no_repeat_when_the_name_is_already_the_title(self):
        p = app.to_poi(el({"amenity": "toilets", "name": "Stadtpark WC"}))
        self.assertEqual(p["name"], "Stadtpark WC")
        self.assertEqual(p["sub"], "")

if __name__ == "__main__":
    unittest.main()
