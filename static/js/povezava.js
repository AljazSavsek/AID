/* AID – povezava s strežnikom (specifikacija 7, vrstica »Povezava frontend«):
 * WebSocket → samodejni ponovni priklop + polling (/stanje) → zadnje stanje v brskalniku
 * in ocena časov prihoda v brskalniku.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const poslusalci = {};
  let seq = 0;
  let ws = null, wsOdprt = false, poskusi = 0, pollTimer = null, zadnjiOdziv = 0;

  AID.uporabnik = () => (document.getElementById("ime-uporabnika")?.value || "dispečer").trim();

  AID.na = function (tip, fn) { (poslusalci[tip] = poslusalci[tip] || []).push(fn); };
  function razposlji(s) {
    if (s.seq && s.seq <= seq) return;
    if (s.seq) seq = s.seq;
    (poslusalci[s.tip] || []).forEach((f) => { try { f(s.podatki, s); } catch (e) { console.error(e); } });
    (poslusalci["*"] || []).forEach((f) => f(s.podatki, s));
  }

  function nastaviStanje(stanje, besedilo) {
    const el = document.getElementById("povezava");
    const pas = document.getElementById("pas-povezave");
    const st = document.getElementById("st-streznik");
    el.classList.toggle("napaka", stanje === "brez");
    el.textContent = besedilo;
    if (st) { st.textContent = besedilo.toLowerCase(); st.className = "pika " + (stanje === "brez" ? "napaka" : ""); }
    pas.classList.toggle("skrito", stanje !== "brez");
    if (stanje === "brez") pas.textContent = "Strežnik ni dosegljiv – prikazano je zadnje znano stanje. Časi prihoda so ocena v brskalniku. Ponovno povezovanje …";
  }

  /* ---------- REST ---------- */
  AID.api = async function (pot, { metoda = "GET", telo } = {}) {
    let r;
    try {
      r = await fetch(pot, {
        method: metoda,
        headers: { "Content-Type": "application/json", "X-Uporabnik": encodeURIComponent(AID.uporabnik()) }, // šumniki niso dovoljeni v glavi
        body: telo === undefined ? undefined : JSON.stringify(telo),
      });
    } catch (e) {
      nastaviStanje("brez", "Brez povezave");
      throw new Error("Strežnik ni dosegljiv.");
    }
    zadnjiOdziv = Date.now();
    const podatki = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (!r.ok) throw new Error((podatki && (podatki.napaka || podatki.detail)) || "Napaka " + r.status);
    return podatki;
  };

  /* ---------- WebSocket z rezervo ---------- */
  function poveziWs() {
    try {
      ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?od=" + seq);
    } catch (e) { zacniPolling(); return; }
    ws.onopen = () => { wsOdprt = true; poskusi = 0; ustaviPolling(); nastaviStanje("ok", "Povezava OK"); };
    ws.onmessage = (e) => { zadnjiOdziv = Date.now(); razposlji(JSON.parse(e.data)); };
    ws.onclose = () => {
      wsOdprt = false;
      zacniPolling();
      const zamik = Math.min(15000, 500 * 2 ** poskusi++);
      setTimeout(poveziWs, zamik);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }
  function zacniPolling() {
    if (pollTimer) return;
    nastaviStanje(Date.now() - zadnjiOdziv < 5000 ? "rezerva" : "brez", Date.now() - zadnjiOdziv < 5000 ? "Povezava (rezerva)" : "Brez povezave");
    const korak = async () => {
      try {
        const r = await AID.api("/stanje?od=" + seq);
        r.dogodki.forEach(razposlji);
        if (!wsOdprt) nastaviStanje("rezerva", "Povezava (rezerva)");
        razposlji({ tip: "ai_stanje", podatki: { na_voljo: r.ai_na_voljo } });
      } catch (_) { nastaviStanje("brez", "Brez povezave"); }
    };
    korak();
    pollTimer = setInterval(korak, 2000);
  }
  function ustaviPolling() { clearInterval(pollTimer); pollTimer = null; }

  /* ---------- ocena v brskalniku (zadnja rezerva) ---------- */
  AID.ocenaEta = function (enota, lat, lon) {
    const d = AID.razdaljaM([enota.lat, enota.lon], [lat, lon]);
    if (enota.tip === "helikopter") return { cas_s: d / (200 / 3.6) + 300, razdalja_m: d, kakovost: "ocena" };
    const cesta = d * 1.35;
    return { cas_s: cesta / (45 / 3.6) * (enota.tip === "motorist" ? 0.9 : 1), razdalja_m: cesta, kakovost: "ocena" };
  };
  AID.razdaljaM = function (a, b) {
    const R = 6371000, r = Math.PI / 180;
    const x = Math.sin((b[0] - a[0]) * r / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  };

  /* ---------- pogledi ---------- */
  function preklopi(ime) {
    document.querySelectorAll(".pogledi button").forEach((b) => { if (b.dataset.pogled === ime) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    document.getElementById("pogled-klic").classList.toggle("skrito", ime !== "klic");
    document.getElementById("pogled-plan").classList.toggle("skrito", ime !== "plan");
    AID.pisi("aid_pogled", ime);
    (poslusalci["pogled"] || []).forEach((f) => f(ime));
  }
  AID.preklopi = preklopi;

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".pogledi button").forEach((b) => (b.onclick = () => preklopi(b.dataset.pogled)));
    const ime = document.getElementById("ime-uporabnika");
    ime.value = AID.beri("aid_uporabnik", ime.value);
    const avatar = () => (document.getElementById("avatar").textContent = ime.value.split(/[\s.]+/).filter(Boolean).map((x) => x[0]).join("").slice(0, 2).toUpperCase());
    avatar();
    ime.onchange = () => { AID.pisi("aid_uporabnik", ime.value.trim()); avatar(); };
    preklopi(AID.beri("aid_pogled", "klic"));
    // začni od trenutnega stanja strežnika (začetno stanje se naloži prek REST)
    AID.api("/stanje?od=999999999").then((r) => { seq = r.seq; }).catch(() => {}).finally(poveziWs);
  });
})();

/* ---------- podlaga karte: strežniški posrednik → brezplačna rezerva ---------- */
(function () {
  "use strict";
  const AID = window.AID;
  let stanje = null;
  const stanjePromise = () => (stanje = stanje || AID.api("/ploscice/stanje").catch(() => ({ karta: false, promet: false })));

  function rezerva(karta) {
    return L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { maxZoom: 19, attribution: "© OpenStreetMap, © CARTO" }).addTo(karta);
  }

  /* Doda podlago na karto. Če posrednik ne odgovarja (več napak zapored), preklopi na rezervo. */
  AID.podlagaKarte = async function (karta, { promet = false } = {}) {
    const st = await stanjePromise();
    const izid = { promet: null };
    if (!st.karta) { rezerva(karta); return izid; }
    const sloj = L.tileLayer("/ploscice/karta/{z}/{x}/{y}.png", { maxZoom: 19, attribution: AID.esc(st.atribucija || "") }).addTo(karta);
    let napake = 0, uspehi = 0;
    sloj.on("tileload", () => { uspehi++; napake = 0; });
    sloj.on("tileerror", () => {
      if (++napake >= 4 && karta.hasLayer(sloj)) { karta.removeLayer(sloj); rezerva(karta); if (izid.promet) karta.removeLayer(izid.promet); }
    });
    if (promet && st.promet) {
      izid.promet = L.layerGroup([
        L.tileLayer("/ploscice/promet/{z}/{x}/{y}.png", { maxZoom: 19, opacity: 0.85 }),
        L.tileLayer("/ploscice/nesrece/{z}/{x}/{y}.png", { maxZoom: 19 }),
      ]);
    }
    return izid;
  };
})();
