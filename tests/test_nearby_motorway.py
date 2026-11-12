import os, re, sys, gc, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

AROUND = re.compile(r"around:(\d+),(\d+\.\d+),(\d+\.\d+)")


class NearbyOnMotorway(unittest.TestCase):
    """On an Autobahn the next stop is far away: the radius is 10 km there, 3 km elsewhere."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        mock.patch.object(app, "DB", self.db).start()
        self.motorway = True
        self.radii = []
        def live(q):
            if "motorway" in q:
                if self.motorway is None:
                    raise RuntimeError("down")
                return [{"type": "way", "id": 1}] if self.motorway else []
            self.radii.append(int(AROUND.search(q).group(1)))
            return [{"type": "node", "id": i, "lat": 51.5 + i / 1e4, "lon": 7.0,
                     "tags": {"highway": "rest_area"}} for i in range(9)]
        mock.patch.object(app, "overpass_live", live).start()

    def tearDown(self):
        app.INFLIGHT.clear(); app.DEAD.clear()
        mock.patch.stopall(); gc.collect()
        try: os.remove(self.db)
        except OSError: pass

    def search(self):
        return app.app.test_client().get("/api/nearby?lat=51.5&lon=7.0").get_json()

    def test_on_the_motorway_the_radius_is_10_km(self):
        d = self.search()
        self.assertEqual(d["radius"], 10000)
        self.assertTrue(self.radii and min(self.radii) >= 10000, self.radii)

    def test_elsewhere_it_stays_3_km(self):
        self.motorway = False
        d = self.search()
        self.assertEqual(d["radius"], 3000)
        self.assertTrue(self.radii and max(self.radii) < 4000, self.radii)

    def test_a_failed_check_falls_back_to_3_km(self):
        self.motorway = None
        self.assertEqual(self.search()["radius"], 3000)


if __name__ == "__main__":
    unittest.main()
