import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._d, self.text = status, data, text
    def json(self):
        if self._d is None: raise ValueError("Expecting value")  # a block page is not JSON
        return self._d

def nom(names):  # a Nominatim answer with display names
    return Resp(200, [{"display_name": n, "lat": "50.1", "lon": "8.6"} for n in names])

def phot(props, lon=8.68, lat=50.11):
    return Resp(200, {"features": [{"geometry": {"coordinates": [lon, lat]}, "properties": props}]})

class GeoList(unittest.TestCase):
    """The geocoder falls back to Photon when Nominatim refuses a datacentre IP."""

    def test_nominatim_answer_is_used(self):
        with mock.patch("app.requests.get") as g:
            g.return_value = nom(["Frankfurt am Main, Hessen, Deutschland"])
            hits = app.geoList("frankfurt", 5)
        self.assertEqual(hits[0]["name"], "Frankfurt am Main, Hessen, Deutschland")
        self.assertEqual((hits[0]["lat"], hits[0]["lon"]), (50.1, 8.6))
        self.assertEqual(g.call_count, 1)  # no fallback query when Nominatim answers

    def test_block_page_falls_back_to_photon(self):
        with mock.patch("app.requests.get") as g:
            g.side_effect = [Resp(403, text="<html>blocked</html>"),
                             phot({"name": "Frankfurt am Main", "city": "Frankfurt am Main",
                                   "state": "Hessen", "country": "Deutschland"})]
            hits = app.geoList("frankfurt", 5)
        self.assertEqual(g.call_count, 2)
        self.assertEqual(hits[0]["name"], "Frankfurt am Main, Hessen, Deutschland")  # repeat dropped
        self.assertEqual((hits[0]["lat"], hits[0]["lon"]), (50.11, 8.68))  # lon/lat order swapped

    def test_photon_name_joins_street_and_number(self):
        with mock.patch("app.requests.get") as g:
            g.side_effect = [Resp(403), phot({"name": "Sternwarte", "street": "Kupferhuette",
                                              "housenumber": "6", "city": "Bonn"})]
            hits = app.geoList("sternwarte bonn", 5)
        self.assertEqual(hits[0]["name"], "Sternwarte, Kupferhuette 6, Bonn")

    def test_both_refusing_names_the_statuses(self):
        with mock.patch("app.requests.get") as g:
            g.side_effect = [Resp(403), Resp(429)]
            with self.assertRaises(ValueError) as cm:
                app.geoList("frankfurt", 5)
        self.assertIn("Nominatim HTTP 403", str(cm.exception))
        self.assertIn("Photon HTTP 429", str(cm.exception))

class GeocodeView(unittest.TestCase):
    def test_endpoint_serves_the_list(self):
        with mock.patch("app.requests.get") as g:
            g.return_value = nom(["Fulda, Hessen, Deutschland"])
            with app.app.test_client() as c:
                r = c.get("/api/geocode?q=fulda")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()[0]["name"], "Fulda, Hessen, Deutschland")

    def test_endpoint_survives_a_block_page(self):
        with mock.patch("app.requests.get") as g:
            g.side_effect = [Resp(403), phot({"name": "Fulda"})]
            with app.app.test_client() as c:
                r = c.get("/api/geocode?q=fulda")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()), 1)

if __name__ == "__main__":
    unittest.main()
