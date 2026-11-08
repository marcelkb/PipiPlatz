const C='pipiplatz-v1',A=['/','/manifest.webmanifest','/icon.svg'];
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(c=>c.addAll(A))));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);
 if(u.pathname.startsWith('/api/')||u.origin!==location.origin)return;
 e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));});
