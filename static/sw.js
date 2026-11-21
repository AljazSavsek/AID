/* AID – lupina aplikacije brez povezave. API odgovori se NIKOLI ne shranjujejo (vedno z omrežja). */
const LUPINA = "aid-lupina-v1";
const DATOTEKE = ["./", "voznik.html", "css/aid.css", "css/center.css", "css/plan.css", "css/voznik.css",
  "js/skupno.js", "js/povezava.js", "js/klic.js", "js/plan.js", "js/voznik.js", "img/aid-logo.svg", "img/aid-logo-svetel.svg",
  "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css", "vendor/maplibre/maplibre-gl.js", "vendor/maplibre/maplibre-gl.css"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(LUPINA).then((c) => c.addAll(DATOTEKE)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((k) => Promise.all(k.filter((x) => x !== LUPINA).map((x) => caches.delete(x)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  const lupina = DATOTEKE.some((d) => u.pathname.endsWith("/" + d.replace("./", "")) || (d === "./" && u.pathname === "/"));
  if (!lupina) return; // API, ploščice, WebSocket: samo omrežje
  e.respondWith(fetch(e.request).then((r) => { const k = r.clone(); caches.open(LUPINA).then((c) => c.put(e.request, k)); return r; })
    .catch(() => caches.match(e.request)));
});
