import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_sides

KM = 1 / 111.32  # degrees of latitude per km

# a carriageway going south along lon 8.0, split into two ways at node 3, where a ramp turns east
WAYS = {1: ([1, 2, 3], [(50.0, 8.0), (50.0 - 0.5 * KM, 8.0), (50.0 - 1.0 * KM, 8.0)]),
        2: ([3, 4], [(50.0 - 1.0 * KM, 8.0), (50.0 - 3.0 * KM, 8.0)]),
        3: ([3, 5], [(50.0 - 1.0 * KM, 8.0), (50.0 - 1.1 * KM, 8.02)])}

class Walk(unittest.TestCase):
    # ===== Core behavior =====
    def test_walk_follows_the_carriageway_into_the_next_way_not_the_ramp(self):
        cw = build_sides.Carriageways(WAYS)
        (lat, lon), done = cw.walk(1, 0, 0.0, 1500, True)
        self.assertAlmostEqual(lat, 50.0 - 1.5 * KM, places=4)
        self.assertAlmostEqual(lon, 8.0, places=6)
        self.assertAlmostEqual(done, 1500)

    def test_walk_backwards_goes_against_the_driving_direction(self):
        cw = build_sides.Carriageways(WAYS)
        (lat, _), _ = cw.walk(2, 0, 0.5, 500, False)  # from 2 km south of the start, 500 m back north
        self.assertAlmostEqual(lat, 50.0 - 1.5 * KM, places=4)

    def test_walk_stops_where_the_motorway_ends(self):
        cw = build_sides.Carriageways(WAYS)
        (lat, _), done = cw.walk(2, 0, 0.0, 5000, True)
        self.assertAlmostEqual(lat, 50.0 - 3.0 * KM, places=6)
        self.assertAlmostEqual(done, 2000, delta=5)  # 111.32 km per degree vs the haversine's earth radius

    def test_stop_where_the_motorway_ends_within_1500_m_is_not_stored(self):
        # 500 m after the motorway starts: a route from further back may leave before it and reach the
        # stop cheaply, which a stretch starting level with the stop cannot see. The live check decides
        cw = build_sides.Carriageways(WAYS)
        def ask(pts):
            raise AssertionError("OSRM asked for a stretch shorter than 1.5 km")
        with mock.patch.object(build_sides.app, "osrmRoutes", ask):
            self.assertEqual(build_sides.measure(cw, (50.0 - 0.5 * KM, 8.001), 1, 1, 0.0), [])

if __name__ == "__main__":
    unittest.main()
