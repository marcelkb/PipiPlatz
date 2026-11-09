import json, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
CODEC = re.search(r"// <share-codec>\n(.*?)// </share-codec>", HTML, re.S).group(1)

def run(expr):
    """Evaluate a JS expression with the page's own share codec (node), return it as JSON."""
    js = CODEC + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

ROUTE = {"org": [51.50001, 6.90002], "on": "Köln, Nordrhein-Westfalen, Deutschland",
         "dest": {"lat": 48.13743, "lon": 11.57549, "name": "München & Umland #1, Bayern"},
         "via": [{"lat": 50.11, "lon": 8.68, "name": "Frankfurt, Hessen"}, {"lat": 49.45, "lon": 11.07, "name": "Nürnberg"}],
         "alt": 1, "rad": 4000, "ahead": 300}

@unittest.skipUnless(shutil.which("node"), "node is needed to run the page's own codec")
class ShareLink(unittest.TestCase):
    def roundTrip(self, route):
        return run("decodeShare('#'+encodeShare(%s))" % json.dumps(route))

    def test_a_route_survives_the_round_trip_with_commas_ampersands_hashes_and_umlauts(self):
        self.assertEqual(self.roundTrip(ROUTE), ROUTE)

    def test_an_empty_start_name_and_no_via_round_trip(self):
        r = dict(ROUTE, on="", via=[])
        self.assertEqual(self.roundTrip(r), r)

    def test_the_link_is_a_fragment_value_without_raw_hash_or_space(self):
        enc = run("encodeShare(%s)" % json.dumps(ROUTE))
        self.assertNotIn("#", enc)
        self.assertNotIn(" ", enc)

    def test_garbage_is_ignored_not_half_applied(self):
        for bad in ["", "#", "#nonsense", "#s=1,2", "#d=1,2", "#s=abc,def&d=1,2", "#s=91,0&d=1,2",
                    "#s=1,181&d=1,2", "#s=NaN,1&d=1,2", "#s=1,2&d=3,4&v=x", "#s=1e9,1&d=1,2"]:
            self.assertIsNone(run("decodeShare(%s)" % json.dumps(bad)), bad)

    def test_too_many_via_points_are_refused(self):
        h = "#s=1,2&d=3,4" + "&v=5,6" * 26
        self.assertIsNone(run("decodeShare(%s)" % json.dumps(h)))

    def test_radius_ahead_and_alt_are_snapped_to_what_the_form_offers(self):
        r = run("decodeShare('#s=1,2&d=3,4&rad=999999&ahead=-5&alt=abc')")
        self.assertEqual((r["rad"], r["ahead"], r["alt"]), (2000, 150, 0))
        r = run("decodeShare('#s=1,2&d=3,4&rad=1000&ahead=50&alt=99')")
        self.assertEqual((r["rad"], r["ahead"], r["alt"]), (1000, 50, 9))

    def test_names_are_capped(self):
        r = run("decodeShare('#s=1,2&d=3,4&dn=' + 'x'.repeat(5000))")
        self.assertEqual(len(r["dest"]["name"]), 120)

if __name__ == "__main__":
    unittest.main()
