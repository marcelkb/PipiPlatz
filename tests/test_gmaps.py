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

    def test_saddr_daddr_form(self):
        # a route shared from the phone: start is the current position, destination an address
        u = ("https://www.google.com/maps?geocode=AAAAAAAAAAAAAA%3D%3D;BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB%3D%3D"
             "&daddr=Musterstrasse+1,+Musterstadt&saddr=50.9375123,6.9603456&dirflg=d"
             "&ftid=0x0:0x0&skid=00000000&g_st=ic")
        s = app.gmapsStops(u)
        self.assertEqual([x["name"] for x in s], ["50.9375123,6.9603456", "Musterstrasse 1, Musterstadt"])
        self.assertEqual((s[0]["lat"], s[0]["lon"]), (50.9375123, 6.9603456))
        self.assertIsNone(s[1]["lat"])

    def test_daddr_chain(self):
        u = "https://www.google.com/maps?dirflg=d&saddr=Fulda&daddr=Gelnhausen+to+Hanau+to+50.1,8.6"
        s = app.gmapsStops(u)
        self.assertEqual([x["name"] for x in s], ["Fulda", "Gelnhausen", "Hanau", "50.1,8.6"])
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

    def test_directions_link_needs_no_request(self):
        u = "https://www.google.com/maps?saddr=51.5,6.9&daddr=Musterstrasse+1,+Musterstadt&dirflg=d"
        with mock.patch.object(app.requests, "get", side_effect=AssertionError("must not fetch")):
            self.assertEqual(app.gmapsResolve(u), u)

    def test_consent_bounce_back_does_not_loop(self):
        u = "https://www.google.com/maps?q=Musterstadt"  # no route in the URL: would be fetched
        cons = "https://consent.google.com/ml?continue=" + u.replace(":", "%3A").replace("/", "%2F")
        seen = []
        def get(url, **kw):
            seen.append(url)
            return Resp(302, cons) if url == u else Resp(302, u)
        with mock.patch.object(app.requests, "get", get):
            self.assertEqual(app.gmapsResolve(u), u)
        self.assertEqual(len(seen), 1)

    def test_refuses_foreign_hosts(self):
        with mock.patch.object(app.requests, "get", side_effect=AssertionError("must not fetch")):
            with self.assertRaises(ValueError):
                app.gmapsResolve("http://127.0.0.1:5000/maps/dir/A/B")
        chain = {"https://maps.app.goo.gl/abc": Resp(302, "http://169.254.169.254/x")}
        with mock.patch.object(app.requests, "get", lambda u, **kw: chain[u]):
            with self.assertRaises(ValueError):
                app.gmapsResolve("https://maps.app.goo.gl/abc")

class StopCap(unittest.TestCase):
    """A link with hundreds of stops would geocode hundreds of names against Nominatim (B8)."""
    LIMIT = app.MAX_VIA + 2          # start + via + destination: what /api/along accepts

    def post(self, n, names):
        calls = []
        def get(u, **kw):
            calls.append(u)
            return Resp(data=[{"display_name": "x", "lat": "50.0", "lon": "8.0"}])
        seg = (lambda i: "Ort%d" % i) if names else (lambda i: "50.%d,8.%d" % (i, i))
        with mock.patch.object(app.requests, "get", get):
            r = app.app.test_client().post("/api/gmaps", json={"url": DIR + "/".join(seg(i) for i in range(n))})
        return r, calls

    def test_too_many_stops_are_refused_before_any_geocoding(self):
        r, calls = self.post(self.LIMIT + 1, names=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Stopps", r.get_json()["error"])
        self.assertEqual(calls, [], "names were geocoded for a refused link")

    def test_a_huge_link_is_refused_too(self):
        r, calls = self.post(500, names=True)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(calls, [])

    def test_the_largest_allowed_route_still_works(self):
        r, _ = self.post(self.LIMIT, names=False)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(len(r.get_json()["points"]), self.LIMIT)

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

    def test_google_unreachable_is_json(self):
        # the browser shows a JSON parse error when the body is an HTML error page
        with mock.patch.object(app.requests, "get", side_effect=app.requests.Timeout("slow")):
            r = app.app.test_client().post("/api/gmaps", json={"url": "https://maps.app.goo.gl/zytX1UureJJdHFER7?g_st=ic"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("erreichbar", r.get_json()["error"])

    def test_geocoder_unreachable_is_json(self):
        def get(u, **kw):
            if "nominatim" in u:
                raise app.requests.ConnectionError("down")
            return Resp(200)
        with mock.patch.object(app.requests, "get", get):
            r = app.app.test_client().post("/api/gmaps", json={"url": "https://www.google.com/maps?saddr=51.5,6.9&daddr=Musterstadt"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("erreichbar", r.get_json()["error"])

if __name__ == "__main__":
    unittest.main()
