// pipiplatz-v2: the page and the manifest are fetched fresh (3 s, then the cached copy) and stored on
// every visit; the icons come from the cache. /api/ is never touched: stale stops would look real.
const C='pipiplatz-v2',WAIT=3000;
const FRESH=['/','/manifest.webmanifest'],ICONS=['/icon.svg','/icon-180.png','/icon-192.png','/icon-512.png'];
const store=(k,res)=>{if(res.ok)caches.open(C).then(c=>c.put(k,res.clone()));return res};
const freshFirst=(r,k)=>{
 const net=fetch(r).then(res=>store(k,res));
 net.catch(()=>{});  // the timeout may win; a later failure of net is then expected
 const late=new Promise((_,no)=>setTimeout(no,WAIT));
 return Promise.race([net,late]).catch(()=>caches.match(k).then(hit=>hit||net));};
const cacheFirst=(r,k)=>caches.match(k).then(hit=>hit||fetch(r).then(res=>store(k,res)));
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(c=>c.addAll([...FRESH,...ICONS]))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>
 Promise.all(ks.filter(k=>k.startsWith('pipiplatz-')&&k!==C).map(k=>caches.delete(k))))));
self.addEventListener('fetch',e=>{
 const r=e.request,u=new URL(r.url),k=u.pathname;
 if(r.method!=='GET'||u.origin!==location.origin)return;
 if(FRESH.includes(k))e.respondWith(freshFirst(r,k));
 else if(ICONS.includes(k))e.respondWith(cacheFirst(r,k));});
