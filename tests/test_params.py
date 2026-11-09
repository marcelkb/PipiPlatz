import math, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

# a motorway going south for ~670 km at lon 8.0, far longer than any lookahead the form offers
LINE = [[8.0, 55.0 - i / 100] for i in range(1001)]
ROUTE = {"routes": [{"geometry": {"coordinates": LINE}, "distance": 1000, "duration": 3600,
                     "legs": [{"steps": [{"ref": "A 3", "geometry": {"coordinates": LINE}}]}]}]}
OK = {"from": [55.0, 8.0], "to": [48.0, 8.0]}

class Calls:
    """/api/along with OSRM and Overpass mocked; records what the backend would have asked for."""
    def __init__(self):
        self.osrm, self.boxes = [], []

    def post(self, path, body):
        def osrm(stops, alternatives=False, steps=False):
            self.osrm.append((stops, alternatives))
            return ROUTE["routes"]
        realBox = app.bboxOf
        def box(points, pad):
            self.boxes.append((points, pad))
            return realBox(points, pad)
        with mock.patch.object(app, "osrmRoutes", osrm), mock.patch.object(app, "bboxOf", box), \
             mock.patch.object(app, "fetchAll", lambda q, w, deadline=None: ([], [], 0, 0)), \
             mock.patch.object(app, "shopEvidence", lambda p, limit=25: []):
            return app.app.test_client().post(path, json=body)

class BadShapes(unittest.TestCase):
    """Wrong types and impossible coordinates are the client's mistake: 400, nothing is fetched (B11)."""

    def refused(self, path, **over):
        c = Calls()
        r = c.post(path, dict(OK, **over))
        self.assertEqual(r.status_code, 400, (path, over, r.get_json()))
        self.assertIn("error", r.get_json())
        self.assertEqual(c.osrm, [], "OSRM was called for a bad request")
        self.assertEqual(c.boxes, [], "Overpass was queried for a bad request")

    def test_along_numeric_fields_with_the_wrong_type(self):
        for key, bad in (("radius", None), ("radius", "abc"), ("radius", [1]), ("radius", True),
                         ("radius", float("nan")), ("ahead_km", None), ("ahead_km", "far"),
                         ("ahead_km", float("inf")), ("alt", "abc"), ("alt", [1]), ("alt", True)):
            with self.subTest(key=key, bad=bad):
                self.refused("/api/along", **{key: bad})

    def test_points_with_the_wrong_shape_or_range(self):
        for key, bad in (("from", [1]), ("from", "x"), ("from", None), ("from", [91, 0]), ("to", [0, 181]),
                         ("to", [1, 2, 3]), ("to", ["a", "b"]), ("to", [float("nan"), 1]), ("from", [True, 1]),
                         ("via", "x"), ("via", [[1]]), ("via", [[95, 0]]), ("via", [None])):
            for path in ("/api/along", "/api/routes"):
                with self.subTest(path=path, key=key, bad=bad):
                    self.refused(path, **{key: bad})

    def test_a_missing_point_is_a_400_not_a_502(self):
        for path in ("/api/along", "/api/routes"):
            for key in ("from", "to"):
                body = dict(OK); body.pop(key)
                c = Calls()
                r = c.post(path, body)
                self.assertEqual(r.status_code, 400, (path, key))

    def test_the_body_must_be_an_object(self):
        for path in ("/api/along", "/api/routes"):
            c = Calls()
            self.assertEqual(c.post(path, [1, 2]).status_code, 400)

    def test_too_many_via_points(self):
        for path in ("/api/along", "/api/routes"):
            self.refused(path, via=[[50.0, 8.0]] * 26)

    def test_huge_numbers_do_not_crash_the_parser(self):
        self.refused("/api/along", radius=10 ** 400)

class Clamped(unittest.TestCase):
    """Numbers that are merely too big or too small are pulled into what the form offers (B11/S1)."""

    def test_radius_is_capped_at_the_largest_form_value(self):
        c = Calls()
        self.assertEqual(c.post("/api/along", dict(OK, radius=10 ** 8)).status_code, 200)
        self.assertTrue(c.boxes)
        self.assertEqual({pad for _p, pad in c.boxes}, {8000 + app.BBOX_SLACK})

    def test_radius_has_a_floor(self):
        c = Calls()
        self.assertEqual(c.post("/api/along", dict(OK, radius=-5)).status_code, 200)
        self.assertEqual({pad for _p, pad in c.boxes}, {500 + app.BBOX_SLACK})

    def test_lookahead_is_capped_at_the_largest_form_value(self):
        c = Calls()
        self.assertEqual(c.post("/api/along", dict(OK, ahead_km=10 ** 6)).status_code, 200)
        far = max(app.hav((55.0, 8.0), p) for pts, _pad in c.boxes for p in pts)
        self.assertLess(far, 300 * 1000 + 25000, "searched %.0f km ahead" % (far / 1000))

    def test_alternatives_are_capped_at_what_osrm_returns(self):
        c = Calls()
        self.assertEqual(c.post("/api/along", dict(OK, alt=10 ** 9)).status_code, 200)

    def test_the_form_values_pass_unchanged(self):
        c = Calls()
        self.assertEqual(c.post("/api/along", dict(OK, radius=4000, ahead_km=50, alt=1,
                                                   via=[[52.0, 8.0], [50.0, 8.0]])).status_code, 200)
        self.assertEqual({pad for _p, pad in c.boxes}, {4000 + app.BBOX_SLACK})
        self.assertEqual(c.osrm[0][0], [[55.0, 8.0], [52.0, 8.0], [50.0, 8.0], [48.0, 8.0]])
        self.assertTrue(c.osrm[0][1])

    def test_routes_accepts_a_normal_request(self):
        c = Calls()
        r = c.post("/api/routes", dict(OK, via=[[52.0, 8.0]]))
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(len(c.osrm[0][0]), 3)

if __name__ == "__main__":
    unittest.main()
