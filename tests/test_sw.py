import json, os, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SW = os.path.join(ROOT, "static", "sw.js").replace("\\", "/")

# The real sw.js runs in a vm against a small fake of caches / fetch / events.
# The fake network counts its calls and numbers its bodies (n1, n2, ...), so a test can tell
# a fresh answer from the one stored at install time.
DRIVER = r"""
const fs=require('fs'),vm=require('vm');
const ORIGIN='https://pipi.test';
const href=k=>typeof k==='string'?new URL(k,ORIGIN).href:k.url;
const store=new Map();
const caches={
  open:async n=>{if(!store.has(n))store.set(n,new Map());const m=store.get(n);return{
    put:async(k,v)=>{m.set(href(k),v)},
    addAll:async ks=>{for(const k of ks){const r=await fetch(k);if(!r.ok)throw new Error('addAll '+k);m.set(href(k),r)}}}},
  match:async k=>{for(const m of store.values())if(m.has(href(k)))return m.get(href(k))},
  keys:async()=>[...store.keys()],
  delete:async n=>store.delete(n)};
const net={mode:'ok',n:0,calls:[]};
const fetch=async k=>{net.calls.push(href(k));
  if(net.mode==='down')throw new TypeError('offline');
  if(net.mode==='hang')return new Promise(()=>{});
  const body='n'+(++net.n);return{ok:true,status:200,url:href(k),body,clone(){return this}}};
const handlers={};
const sandbox={self:{addEventListener:(t,f)=>{handlers[t]=f}},caches,fetch,URL,Promise,
  location:{origin:ORIGIN},Response:{error:()=>({error:true})},
  setTimeout:(f,ms)=>setTimeout(f,ms>0?20:0),console};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
const waitEvent=async t=>{const w=[];handlers[t]&&handlers[t]({waitUntil:p=>w.push(p)});await Promise.all(w)};
const request=async(path,o={})=>{let out;let hit=false;
  handlers.fetch({request:{url:ORIGIN+path,method:o.method||'GET'},respondWith:p=>{hit=true;out=p}});
  if(!hit)return{intercepted:false};
  const giveUp=new Promise(ok=>setTimeout(()=>ok({gaveUp:true}),500));  // a worker that never answers
  try{const r=await Promise.race([out,giveUp]);return{intercepted:true,body:r.body,error:!!r.error,gaveUp:!!r.gaveUp}}
  catch(e){return{intercepted:true,rejected:true}}};
const res={};
(async()=>{
  await waitEvent('install');
  res.cacheNames=await caches.keys();
  res.precached=[...store.get(res.cacheNames[0]).keys()].map(u=>u.replace(ORIGIN,'')).sort();
  res.installBody=store.get(res.cacheNames[0]).get(ORIGIN+'/').body;

  const online=await request('/');res.onlineBody=online.body;
  net.mode='down';const offline=await request('/');res.offlineBody=offline.body;

  net.mode='ok';const before=net.calls.length;
  res.icon=await request('/icon-192.png');res.iconNetCalls=net.calls.length-before;
  res.manifestOnline=(await request('/manifest.webmanifest')).body;
  net.mode='down';res.manifestOffline=(await request('/manifest.webmanifest')).body;

  net.mode='hang';res.hung=await request('/');
  net.mode='ok';
  res.api=await request('/api/along');
  res.post=await request('/',{method:'POST'});
  res.other=await request('/something-else.txt');
  res.query=await request('/?utm=1');
  net.mode='down';res.queryOffline=(await request('/?other=2')).body;

  store.set('pipiplatz-v1',new Map());store.set('unrelated',new Map());
  await waitEvent('activate');
  res.afterActivate=await caches.keys();
  process.stdout.write(JSON.stringify(res));
})().catch(e=>{console.error(e);process.exit(1)});
"""

def run():
    out = subprocess.run(["node", "-e", DRIVER, SW], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

@unittest.skipUnless(shutil.which("node"), "node is needed to run the service worker")
class ServiceWorker(unittest.TestCase):
    """The service worker has to cache for real (F22): offline start, icons from the cache, old caches gone."""

    @classmethod
    def setUpClass(cls):
        cls.r = run()

    def test_install_precaches_the_page_the_manifest_and_every_icon(self):
        self.assertEqual(self.r["precached"], ["/", "/icon-180.png", "/icon-192.png", "/icon-512.png",
                                               "/icon.svg", "/manifest.webmanifest"])

    def test_the_cache_name_is_not_the_old_one(self):
        self.assertEqual(len(self.r["cacheNames"]), 1)
        self.assertNotEqual(self.r["cacheNames"][0], "pipiplatz-v1")

    def test_the_page_comes_from_the_network_when_it_is_reachable(self):
        self.assertNotEqual(self.r["onlineBody"], self.r["installBody"])

    def test_the_page_is_stored_on_every_visit_so_offline_shows_the_latest(self):
        self.assertEqual(self.r["offlineBody"], self.r["onlineBody"])

    def test_a_hanging_network_falls_back_to_the_cached_page(self):
        self.assertEqual(self.r["hung"]["body"], self.r["onlineBody"])

    def test_the_query_string_does_not_make_the_page_miss_the_cache(self):
        self.assertEqual(self.r["queryOffline"], self.r["query"]["body"])

    def test_icons_are_served_from_the_cache_without_the_network(self):
        self.assertTrue(self.r["icon"]["intercepted"])
        self.assertEqual(self.r["iconNetCalls"], 0)

    def test_the_manifest_is_fresh_online_and_cached_offline(self):
        self.assertEqual(self.r["manifestOffline"], self.r["manifestOnline"])

    def test_api_calls_other_origins_and_non_gets_are_left_alone(self):
        for k in ("api", "post", "other"):
            with self.subTest(k=k):
                self.assertFalse(self.r[k]["intercepted"])

    def test_activate_drops_old_pipiplatz_caches_only(self):
        self.assertNotIn("pipiplatz-v1", self.r["afterActivate"])
        self.assertIn("unrelated", self.r["afterActivate"])
        self.assertIn(self.r["cacheNames"][0], self.r["afterActivate"])

if __name__ == "__main__":
    unittest.main()
