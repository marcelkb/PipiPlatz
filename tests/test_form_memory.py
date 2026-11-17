import json, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
CODE = (re.search(r"// <share-codec>\n(.*?)// </share-codec>", HTML, re.S).group(1)
        + re.search(r"// <form-memory>\n(.*?)// </form-memory>", HTML, re.S).group(1))

def run(expr):
    """Evaluate a JS expression with the page's own form-memory code (node), return it as JSON."""
    js = CODE + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

FORM = {"start": None, "dest": {"lat": 48.13743, "lon": 11.57549, "name": "München"},
        "via": [{"lat": 50.11, "lon": 8.68, "name": "Frankfurt"}], "rad": 4000, "ahead": 300,
        "gml": "https://maps.app.goo.gl/abc"}

@unittest.skipUnless(shutil.which("node"), "node is needed to run the page's own code")
class FormMemory(unittest.TestCase):
    def read(self, raw):
        return run("readForm(%s)" % json.dumps(raw))

    def test_a_saved_form_comes_back(self):
        self.assertEqual(self.read(json.dumps(FORM)), FORM)

    def test_the_form_values_as_saved_from_the_selects_are_strings(self):
        self.assertEqual(self.read(json.dumps(dict(FORM, rad="4000", ahead="300"))), FORM)

    def test_an_empty_start_stays_empty(self):
        self.assertIsNone(self.read(json.dumps(FORM))["start"])

    def test_garbage_gives_null_or_safe_defaults(self):
        for bad in [None, "", "{", "null", "42", '"x"']:
            self.assertIsNone(self.read(bad), bad)
        r = self.read(json.dumps({"dest": {"lat": 91, "lon": 0}, "via": "x", "rad": 999, "ahead": -1, "gml": 5}))
        self.assertEqual(r, {"start": None, "dest": None, "via": [], "rad": 2000, "ahead": 150, "gml": ""})

    def test_one_bad_via_point_drops_the_whole_via_list(self):
        r = self.read(json.dumps(dict(FORM, via=[{"lat": 1, "lon": 2}, {"lat": "x", "lon": 2}])))
        self.assertEqual(r["via"], [])

if __name__ == "__main__":
    unittest.main()
