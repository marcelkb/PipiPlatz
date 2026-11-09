import json, math, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
BLOCK = re.search(r"// <pan-rotation>\n(.*?)// </pan-rotation>", HTML, re.S).group(1)

def unturn(dx, dy, deg):
    """The page's own unturn() (node): a screen drag delta in the frame of the turned map."""
    js = BLOCK + "\nprocess.stdout.write(JSON.stringify(unturn(%r,%r,%r)));" % (dx, dy, deg)
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

@unittest.skipUnless(shutil.which("node"), "node is needed to run the page's own helper")
class PanRotation(unittest.TestCase):
    """Dragging a turned map (F1): the content has to follow the finger, not slide off at an angle."""

    def test_an_unturned_map_is_left_alone(self):
        self.assertEqual(unturn(100, -40, 0), [100, -40])

    def test_a_quarter_turn_swaps_the_axes(self):
        # the map is turned 90 deg clockwise: a finger moving right drags the content up in the map's own frame
        x, y = unturn(100, 0, 90)
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(y, -100, places=6)

    def test_a_half_turn_reverses_the_drag(self):
        x, y = unturn(50, 50, 180)
        self.assertAlmostEqual(x, -50, places=6)
        self.assertAlmostEqual(y, -50, places=6)

    def test_turning_the_result_by_the_map_angle_gives_back_the_finger_move(self):
        # CSS rotate(r) maps map-frame (x, y) to the screen as (x cos r - y sin r, x sin r + y cos r)
        for deg in (-170, -30, 7, 45, 135):
            for dx, dy in ((60, -80), (-70, 40), (0, 25)):
                x, y = unturn(dx, dy, deg)
                r = math.radians(deg)
                self.assertAlmostEqual(x * math.cos(r) - y * math.sin(r), dx, places=6)
                self.assertAlmostEqual(x * math.sin(r) + y * math.cos(r), dy, places=6)

    def test_the_drag_override_is_installed_and_compensates_the_measured_scale(self):
        self.assertIn("L.Draggable.prototype._onMove=", HTML)
        self.assertIn("this._parentScale", HTML)

if __name__ == "__main__":
    unittest.main()
