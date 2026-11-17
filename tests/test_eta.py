import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def step(coords, duration, ref=""):
    return {"geometry": {"coordinates": [[lon, lat] for lat, lon in coords]}, "duration": duration, "ref": ref}

# a slow town step (1 km in 120 s), then a fast motorway step (about 10 km in 300 s)
ROUTE = {"legs": [{"steps": [step([(50.0, 8.0), (50.009, 8.0)], 120),
                              step([(50.009, 8.0), (50.099, 8.0)], 300, "A 5")]}], "duration": 420}

class Eta(unittest.TestCase):
    def test_the_timeline_is_in_the_same_metres_as_seglist(self):
        tl = app.timeline(ROUTE)
        _segs, total = app.segList(ROUTE)
        self.assertAlmostEqual(tl[-1][0], total, places=6)
        self.assertEqual(tl[-1][1], 420)

    def test_a_stop_inside_a_step_is_interpolated_at_that_steps_speed(self):
        tl = app.timeline(ROUTE)
        town = tl[1][0]
        self.assertAlmostEqual(app.etaAt(tl, town / 2), 60, places=3)
        self.assertAlmostEqual(app.etaAt(tl, town + (tl[2][0] - town) / 2), 270, places=3)

    def test_outside_the_route_it_clamps(self):
        tl = app.timeline(ROUTE)
        self.assertEqual(app.etaAt(tl, -5), 0)
        self.assertEqual(app.etaAt(tl, 1e9), 420)

if __name__ == "__main__":
    unittest.main()
