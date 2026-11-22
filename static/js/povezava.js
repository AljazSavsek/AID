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

  AID.uporabnik = () => AID.imeUporabnika || "";
  // vsi deli vmesnika dispečerja počakajo na prijavo (seja je HttpOnly piškotek; ime pride s strežnika)
  let razresiPrijavo;
  AID.prijava = new Promise((r) => (razresiPrijavo = r));

  AID.na = function (tip, fn) { (poslusalci[tip] = poslusalci[tip] || []).push(fn); };
  /* ura strežnika (starost položajev se računa v času strežnika, ne brskalnika) */
  let odmikUre = 0;
  AID.streznikZdaj = () => Date.now() / 1000 + odmikUre;
  AID.stanjePovezave = "ok";
  function razposlji(s) {
    if (s.cas) odmikUre = s.cas - Date.now() / 1000;
    if (s.seq && s.seq <= seq) return;
    if (s.seq) seq = s.seq;
    (poslusalci[s.tip] || []).forEach((f) => { try { f(s.podatki, s); } catch (e) { console.error(e); } });
    (poslusalci["*"] || []).forEach((f) => f(s.podatki, s));
  }

  function nastaviStanje(stanje, besedilo) {
    AID.stanjePovezave = stanje;
    const el = document.getElementById("povezava");
    const pas = document.getElementById("pas-povezave");
    const st = document.getElementById("st-streznik");
    if (!el || !pas) return;
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
        headers: { "Content-Type": "application/json" }, // ime dispečerja pride iz seje na strežniku
        body: telo === undefined ? undefined : JSON.stringify(telo),
      });
    } catch (e) {
      nastaviStanje("brez", "Brez povezave");
      throw new Error("Strežnik ni dosegljiv.");
    }
    zadnjiOdziv = Date.now();
    const podatki = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (r.status === 401 && document.getElementById("prijava-disp")) { pokaziPrijavo("Seja je potekla – prijavite se znova."); throw new Error("Prijava je potrebna."); }
    if (!r.ok) throw new Error((podatki && (podatki.napaka || podatki.detail)) || "Napaka " + r.status);
    return podatki;
  };

  function nastaviIme(ime) {
    AID.imeUporabnika = ime;
    const el = document.getElementById("ime-uporabnika");
    if (el) el.textContent = ime;
    const av = document.getElementById("avatar");
    if (av) av.textContent = ime.split(/[\s.]+/).filter(Boolean).map((x) => x[0]).join("").slice(0, 2).toUpperCase();
  }
  function pokaziPrijavo(sporocilo) {
    const o = document.getElementById("prijava-disp");
    if (AID.imeUporabnika && sporocilo) { location.reload(); return; } // seja potekla med delom -> čisto stanje
    o.classList.remove("skrito");
    document.getElementById("napaka-prijave-disp").textContent = sporocilo || "";
    document.getElementById("prijava-ime").focus();
  }
  async function prijavaDispecerja() {
    const o = document.getElementById("prijava-disp");
    if (!o) return false;
    try {
      const r = await fetch("/dispecer/jaz");
      if (r.ok) { nastaviIme((await r.json()).ime); return true; }
    } catch (_) { nastaviStanje("brez", "Brez povezave"); }
    pokaziPrijavo();
    return new Promise((koncano) => {
      document.getElementById("obrazec-prijave-disp").onsubmit = async (e) => {
        e.preventDefault();
        const ime = document.getElementById("prijava-ime").value.trim(), geslo = document.getElementById("prijava-geslo").value;
        try {
          const r = await fetch("/dispecer/prijava", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ime, geslo }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) { document.getElementById("napaka-prijave-disp").textContent = d.detail || d.napaka || "Prijava ni uspela."; return; }
          document.getElementById("prijava-geslo").value = "";
          o.classList.add("skrito");
          nastaviIme(d.ime);
          koncano(true);
        } catch (_) { document.getElementById("napaka-prijave-disp").textContent = "Strežnik ni dosegljiv."; }
      };
    });
  }

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
        if (r.polozaji) razposlji({ tip: "polozaji", cas: r.cas, podatki: r.polozaji });  // enota_premik je samo WS
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

  // začni od trenutnega stanja strežnika (začetno stanje se naloži prek REST)
  function poveziZacetek() {
    AID.api("/stanje?od=999999999").then((r) => { seq = r.seq; }).catch(() => {}).finally(poveziWs);
  }

  /* ---------- pogledi ---------- */
  function preklopi(ime) {
    document.querySelectorAll(".pogledi button").forEach((b) => { if (b.dataset.pogled === ime) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    document.getElementById("pogled-klic")?.classList.toggle("skrito", ime !== "klic");
    document.getElementById("pogled-plan")?.classList.toggle("skrito", ime !== "plan");
    AID.pisi("aid_pogled", ime);
    (poslusalci["pogled"] || []).forEach((f) => f(ime));
  }
  AID.preklopi = preklopi;

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".pogledi button").forEach((b) => (b.onclick = () => preklopi(b.dataset.pogled)));
    if (!document.getElementById("prijava-disp")) { poveziZacetek(); return; } // stran brez prijave dispečerja
    preklopi(AID.beri("aid_pogled", "klic"));
    const odjava = document.getElementById("gumb-odjava-disp");
    if (odjava) odjava.onclick = async () => { try { await fetch("/dispecer/odjava", { method: "POST" }); } catch (_) {} location.reload(); };
    prijavaDispecerja().then(() => { razresiPrijavo(); poveziZacetek(); });
  });
})();

/* ---------- podlaga karte: strežniški posrednik → brezplačna rezerva ---------- */
(function () {
  "use strict";
  const AID = window.AID;
  let stanje = null;
  const stanjePromise = () => (stanje = stanje || AID.api("/ploscice/stanje").catch(() => ({ karta: false, promet: false })));

  function rezerva(karta) {
    // rezervna podlaga LEŽI POD glavno: ploščice glavne, ki se naložijo, jo prekrijejo; luknje zapolni rezerva
    return L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { maxZoom: 19, zIndex: 0, attribution: "© OpenStreetMap, © CARTO" }).addTo(karta);
  }
  // manj zahtevkov: ploščice se ne nalagajo med animacijo povečave, rob karte ostane v pomnilniku
  const VARCNO = { maxZoom: 19, updateWhenZooming: false, keepBuffer: 4 };
  /* Neuspela ploščica (npr. strežnik začasno omejuje, 503/429) se ne zavrže: do 4 poskusi s premorom 1,5–12 s. */
  function ponavljaj(sloj) {
    sloj.on("tileerror", (e) => {
      const t = e.tile, n = Number(t.dataset.poskus || 0) + 1;
      if (n > 4) return;
      t.dataset.poskus = String(n);
      setTimeout(() => { if (sloj._map) t.src = sloj.getTileUrl(e.coords) + "?p=" + n; }, 1500 * 2 ** (n - 1));
    });
    sloj.on("tileload", (e) => { delete e.tile.dataset.poskus; });
  }

  /* Doda podlago na karto. Glavni sloj se ob napakah NE odstrani (karta nikoli ne izgine): ponavlja neuspele
   * ploščice, po več zaporednih napakah pa pod njim doda rezervno podlago. */
  AID.podlagaKarte = async function (karta, { promet = false } = {}) {
    const st = await stanjePromise();
    const izid = { promet: null };
    if (!st.karta) { rezerva(karta); return izid; }
    const sloj = L.tileLayer("/ploscice/karta/{z}/{x}/{y}.png", { ...VARCNO, zIndex: 1, attribution: AID.esc(st.atribucija || "") }).addTo(karta);
    ponavljaj(sloj);
    let napake = 0, imaRezervo = false;
    sloj.on("tileload", () => { napake = 0; });
    sloj.on("tileerror", () => {
      if (++napake >= 6 && !imaRezervo) { imaRezervo = true; rezerva(karta); }
    });
    if (promet && st.promet) {
      const tok = L.tileLayer("/ploscice/promet/{z}/{x}/{y}.png", { ...VARCNO, zIndex: 2, opacity: 0.85 });
      const nes = L.tileLayer("/ploscice/nesrece/{z}/{x}/{y}.png", { ...VARCNO, zIndex: 3 });
      [tok, nes].forEach(ponavljaj);
      izid.promet = L.layerGroup([tok, nes]);
      let napakePrometa = 0, preverjam = false;
      [tok, nes].forEach((sl) => {
        sl.on("tileload", () => { napakePrometa = 0; });
        sl.on("tileerror", async () => {
          // sloj skrijemo le, če ga strežnik res nima (npr. Traffic API ni vklopljen), ne ob začasni omejitvi
          if (++napakePrometa < 6 || preverjam) return;
          preverjam = true;
          try {
            const zdaj = await AID.api("/ploscice/stanje");
            if (!zdaj.promet) { karta.removeLayer(izid.promet); izid.onIzpad && izid.onIzpad(); }
          } catch (_) { /* brez odgovora: sloj ostane */ }
          napakePrometa = 0; preverjam = false;
        });
      });
    }
    return izid;
  };
})();
