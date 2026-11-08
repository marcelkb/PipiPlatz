import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

DIR = "https://www.google.com/maps/dir/"

class Resp:
    def __init__(self, status=200, location=None, data=None):
        self.status_code, self.headers, self._d = status, ({"Location": location} if location else {}), data
    def json(self): return self._d

class Parse(unittest.TestCase):
    def test_names_are_decoded(self):
        s = app.gmapsStops(DIR + "Frankfurt+am+Main/Stuttgart/M%C3%BCnchen/@49.1,9.7,8z/data=!4m2!4m1!3e0")
        self.assertEqual([x["name"] for x in s], ["Frankfurt am Main", "Stuttgart", "München"])
        self.assertTrue(all(x["lat"] is None for x in s))

    def test_coordinate_segments(self):
        s = app.gmapsStops(DIR + "50.1109,8.6821/48.1351,11.582/@49,10,7z")
        self.assertEqual((s[0]["lat"], s[0]["lon"]), (50.1109, 8.6821))
        self.assertEqual((s[1]["lat"], s[1]["lon"]), (48.1351, 11.582))

    def test_data_coordinates_fill_named_stops(self):
        data = "data=!4m8!4m7!1m2!1m1!1s0x1:0x2!2m2!1d8.6821!2d50.1109!1m0!1m2!2m2!1d11.582!2d48.1351"
        s = app.gmapsStops(DIR + "Frankfurt/Hidden+Stop/Muenchen/@49,10,7z/" + data)
        self.assertEqual(len(s), 3)  # 2 coordinate pairs for 3 names: counts differ, so names only
        self.assertTrue(all(x["lat"] is None for x in s))
        data = "data=!1m2!2m2!1d8.6821!2d50.1109!1m2!2m2!1d11.582!2d48.1351"
        s = app.gmapsStops(DIR + "Frankfurt/Muenchen/@49,10,7z/" + data)
        self.assertEqual((s[0]["lat"], s[0]["lon"]), (50.1109, 8.6821))
        self.assertEqual((s[1]["lat"], s[1]["lon"]), (48.1351, 11.582))

    def test_api_url_form(self):
        s = app.gmapsStops("https://www.google.com/maps/dir/?api=1&origin=Fulda&destination=50.1,8.6&waypoints=Gelnhausen%7C49.9,9.1")
        self.assertEqual([x["name"] for x in s][:2], ["Fulda", "Gelnhausen"])
        self.assertEqual(len(s), 4)
        self.assertEqual((s[2]["lat"], s[2]["lon"]), (49.9, 9.1))
        self.assertEqual((s[3]["lat"], s[3]["lon"]), (50.1, 8.6))

    def test_not_a_route_link(self):
        for u in ("https://www.google.com/maps/place/Berlin", DIR + "Berlin/@52,13,9z"):
            with self.assertRaises(ValueError):
                app.gmapsStops(u)

class Resolve(unittest.TestCase):
    def test_follows_short_link(self):
        chain = {"https://maps.app.goo.gl/abc": Resp(302, DIR + "A/B"), }
        with mock.patch.object(app.requests, "get", lambda u, **kw: chain[u]):
            self.assertEqual(app.gmapsResolve("https://maps.app.goo.gl/abc"), DIR + "A/B")

    def test_unwraps_consent_page(self):
        cons = "https://consent.google.com/m?continue=" + "https%3A%2F%2Fwww.google.com%2Fmaps%2Fdir%2FA%2FB&x=1"
        chain = {"https://maps.app.goo.gl/abc": Resp(302, cons)}
        with mock.patch.object(app.requests, "get", lambda u, **kw: chain[u]):
            self.assertEqual(app.gmapsResolve("https://maps.app.goo.gl/abc"), "https://www.google.com/maps/dir/A/B")

    def test_refuses_foreign_hosts(self):
        with mock.patch.object(app.requests, "get", side_effect=AssertionError("must not fetch")):
            with self.assertRaises(ValueError):
                app.gmapsResolve("http://127.0.0.1:5000/maps/dir/A/B")
        chain = {"https://maps.app.goo.gl/abc": Resp(302, "http://169.254.169.254/x")}
        with mock.patch.object(app.requests, "get", lambda u, **kw: chain[u]):
            with self.assertRaises(ValueError):
                app.gmapsResolve("https://maps.app.goo.gl/abc")

class Endpoint(unittest.TestCase):
    def test_geocodes_names_without_coordinates(self):
        def get(u, **kw):
            q = kw["params"]["q"]
            return Resp(data=[{"display_name": q + ", Deutschland", "lat": "50.0", "lon": "8.0"}])
        with mock.patch.object(app.requests, "get", get):
            r = app.app.test_client().post("/api/gmaps", json={"url": DIR + "Fulda/9.5,50.5/Hanau"})
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(p["lat"], p["lon"]) for p in j["points"]], [(50.0, 8.0), (9.5, 50.5), (50.0, 8.0)])
        self.assertEqual(j["points"][0]["name"], "Fulda")

    def test_bad_link_gives_german_error(self):
        r = app.app.test_client().post("/api/gmaps", json={"url": "https://example.com/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Google", r.get_json()["error"])

if __name__ == "__main__":
    unittest.main()
