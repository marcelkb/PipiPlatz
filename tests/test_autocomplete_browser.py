import os, re, shutil, subprocess, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = next((p for p in (os.environ.get("CHROME"), shutil.which("chrome"), shutil.which("google-chrome"),
                           "C:/Program Files/Google/Chrome/Application/chrome.exe") if p and os.path.exists(p)), None)

# Runs in the real page (headless Chrome, fetch mocked): the geocoder answers on the test's terms.
PROBE = r"""
<script>
window.addEventListener('load',()=>setTimeout(async()=>{
  const res=[]; let unhandled=0;
  window.addEventListener('unhandledrejection',e=>{unhandled++;e.preventDefault()});
  window.addEventListener('error',()=>{unhandled++});
  const waits={};
  window.fetch=(url)=>{
    const m=/geocode\?q=([^&]*)/.exec(url);
    if(!m)return Promise.reject(new TypeError('offline'));
    const q=decodeURIComponent(m[1]);
    return new Promise(ok=>{waits[q]=body=>ok({status:body.error?502:200,json:()=>Promise.resolve(body)})});
  };
  const sl=ms=>new Promise(r=>setTimeout(r,ms));
  const type=(id,v)=>{const el=$(id);el.value=v;el.dispatchEvent(new Event('input'))};
  const names=()=>[...document.querySelectorAll('#sugg div')].map(d=>d.textContent).join('|');
  type('start','Mün'); await sl(500); waits['Mün']({error:'boom'}); await sl(100);
  res.push('A unhandled='+unhandled+' dropdown=['+names()+']');
  type('start','Fra'); await sl(500); type('dest','Ber'); await sl(500);
  waits['Ber']([{name:'Berlin',lat:52.5,lon:13.4}]); await sl(100);
  waits['Fra']([{name:'Frankfurt',lat:50.1,lon:8.7}]); await sl(100);
  res.push('B dropdown=['+names()+']');
  const first=document.querySelector('#sugg div'); if(first)first.click();
  res.push('B start='+$('start').value+' dest='+$('dest').value);
  type('start','Hamb'); await sl(500); type('start','Ha'); await sl(100);
  waits['Hamb']([{name:'Hamburg',lat:53.5,lon:10}]); await sl(100);
  res.push('C dropdown=['+names()+']');
  document.title='RESULT|'+res.join(' ; ');
},1500));
</script></body>"""

def runPage():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "probe.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(html.replace("</body>", PROBE))
        out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--user-data-dir=" + os.path.join(tmp, "prof"),
                              "--window-size=420,800", "--virtual-time-budget=30000", "--dump-dom",
                              "file:///" + page.replace("\\", "/")],
                             capture_output=True, text=True, encoding="utf-8", timeout=120).stdout
    m = re.search(r"<title>RESULT\|([^<]*)", out)
    return m.group(1) if m else None

@unittest.skipUnless(CHROME, "Chrome is needed to run the page")
class AutocompleteInBrowser(unittest.TestCase):
    """The route fields' suggestions (F3/F4): errors stay quiet, a late answer never lands in the wrong field."""

    @classmethod
    def setUpClass(cls):
        cls.result = runPage()
        if cls.result is None:
            raise unittest.SkipTest("the page did not finish (Leaflet from unpkg needs the network)")

    def test_a_geocoder_error_is_not_an_unhandled_rejection(self):
        self.assertIn("A unhandled=0 dropdown=[]", self.result)

    def test_a_slow_answer_for_the_first_field_does_not_replace_the_second_fields(self):
        self.assertIn("B dropdown=[Berlin]", self.result)

    def test_picking_a_suggestion_fills_the_field_it_was_for(self):
        self.assertIn("B start=Fra dest=Berlin", self.result)

    def test_an_answer_for_text_that_was_shortened_since_is_dropped(self):
        self.assertIn("C dropdown=[]", self.result)

if __name__ == "__main__":
    unittest.main()
