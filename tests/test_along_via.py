import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def fakeRoute():
    n = 200
    coords = [[8.68 + (11.58 - 8.68) * i / n, 50.11 + (48.14 - 50.11) * i / n] for i in range(n + 1)]
    return {"routes": [{"geometry": {"coordinates": coords}, "duration": 9000}]}

class AlongVia(unittest.TestCase):
    def post(self, body):
        seen = {}
        class R:
            def json(self): return fakeRoute()
        def get(url, **kw):
            seen["url"] = url
            return R()
        with mock.patch.object(app.requests, "get", get), \
             mock.patch.object(app, "fetchAll", lambda q, w: ([], [], 0)):
            r = app.app.test_client().post("/api/along", json=body)
        return r, seen["url"]

    def test_via_is_routed_between_start_and_destination(self):
        r, url = self.post({"from": [50.11, 8.68], "to": [48.14, 11.58], "via": [[49.4, 9.9]]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("8.68,50.11;9.9,49.4;11.58,48.14", url)

    def test_via_along_is_returned_in_order(self):
        r, _ = self.post({"from": [50.11, 8.68], "to": [48.14, 11.58], "via": [[49.4, 9.9], [48.9, 10.8]]})
        va = r.get_json()["via_along"]
        self.assertEqual(len(va), 2)
        self.assertTrue(0 < va[0] < va[1])

    def test_without_via_is_unchanged(self):
        r, url = self.post({"from": [50.11, 8.68], "to": [48.14, 11.58]})
        self.assertIn("8.68,50.11;11.58,48.14?", url)
        self.assertEqual(r.get_json()["via_along"], [])

if __name__ == "__main__":
    unittest.main()
