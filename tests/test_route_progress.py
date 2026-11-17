import json, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
CODE = re.search(r"// <route-progress>\n(.*?)// </route-progress>", HTML, re.S).group(1)

def run(expr):
    """Evaluate a JS expression with the page's own route-progress code (node), return it as JSON."""
    js = CODE + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

# 10 km due north in 1 km pieces; the server measured it as 10 200 m (the thinned line cuts corners)
LINE = [[50 + i * 0.008983, 8.0] for i in range(11)]
INFO = {"route": LINE, "total": 10200, "tl": [[0, 0], [2000, 240], [10200, 540]]}

@unittest.skipUnless(shutil.which("node"), "node is needed to run the page's own code")
class RouteProgress(unittest.TestCase):
    def test_a_position_beside_the_route_is_measured_along_it_and_stretched(self):
        # halfway up, 100 m to the east
        r = run("alongOf(%s,[%f,8.0014],10200)" % (json.dumps(LINE), 50 + 5 * 0.008983))
        self.assertAlmostEqual(r["off"], 100, delta=3)
        self.assertAlmostEqual(r["along"], 5100, delta=10)

    def test_progress_is_null_off_the_route_and_at_the_start(self):
        self.assertIsNone(run("progressOf(%s,[50.0,8.05])" % json.dumps(INFO)))  # 3.6 km away
        self.assertIsNone(run("progressOf(%s,[50.0005,8.0])" % json.dumps(INFO)))  # 56 m driven
        self.assertIsNone(run("progressOf(null,[50,8])"))
        self.assertAlmostEqual(run("progressOf(%s,[%f,8.0])" % (json.dumps(INFO), 50 + 3 * 0.008983)), 3060, delta=10)

    def test_eta_is_interpolated_on_the_timeline(self):
        tl = json.dumps(INFO["tl"])
        self.assertAlmostEqual(run("etaAt(%s,1000)" % tl), 120, places=6)
        self.assertAlmostEqual(run("etaAt(%s,6100)" % tl), 390, places=6)
        self.assertEqual(run("etaAt(%s,-1)" % tl), 0)
        self.assertEqual(run("etaAt(%s,99999)" % tl), 540)
        self.assertEqual(run("etaAt(undefined,5)"), 0)

    def test_a_nearby_list_is_re_measured_and_sorted_from_the_new_position(self):
        stops = [{"id": "a", "lat": 50.0, "lon": 8.0, "dist": 100}, {"id": "b", "lat": 50.02, "lon": 8.0, "dist": 2300}]
        r = run("rebaseNear(%s,[50.02,8.0])" % json.dumps(stops))
        self.assertEqual([p["id"] for p in r], ["b", "a"])
        self.assertEqual(r[0]["dist"], 0)
        self.assertAlmostEqual(r[1]["dist"], 2224, delta=3)
        self.assertEqual(stops[0]["dist"], 100)  # the input is not changed

if __name__ == "__main__":
    unittest.main()
