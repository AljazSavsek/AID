/* AID – zaslon voznika reševalnega vozila.
 * Prikazuje le naloge iz POTRJENEGA plana in podatke, ki jih je potrdil dispečer.
 * Pot: po cestah s prometom (ponudnik je skrit), navodila za zavijanje, opozorila v živo.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);
  const V = { enota: null, enote: [], d: null, sim: null, sledi: true, planId: null, obvestila: [] };
  const IKONE = { TURN_LEFT: "↰", SHARP_LEFT: "↰", BEAR_LEFT: "↖", KEEP_LEFT: "↖", TURN_RIGHT: "↱", SHARP_RIGHT: "↱", BEAR_RIGHT: "↗",
    KEEP_RIGHT: "↗", STRAIGHT: "↑", ROUNDABOUT_LEFT: "⟲", ROUNDABOUT_RIGHT: "⟳", ROUNDABOUT_CROSS: "⟳", ROUNDABOUT_BACK: "⟲",
    ENTER_MOTORWAY: "↗", ENTER_FREEWAY: "↗", MOTORWAY_EXIT_LEFT: "↖", MOTORWAY_EXIT_RIGHT: "↗", TAKE_EXIT: "↗", MAKE_UTURN: "↶",
    ARRIVE: "⚑", ARRIVE_LEFT: "⚑", ARRIVE_RIGHT: "⚑", DEPART: "↑", FOLLOW: "↑" };
  const AKCIJE = { na_poti: "NA POTI", na_kraju: "NA KRAJU", prevzeto: "PREVZETO", dostavljeno: "DOSTAVLJENO", opravljeno: "OPRAVLJENO" };
  const napaka = (e) => AID.obvestilo("Ni uspelo", e.message, true, 5000);
  const km = (m) => (m >= 1000 ? (m / 1000).toFixed(1).replace(".", ",") + " km" : Math.round(m / 10) * 10 + " m");

  /* ---------- karta (3D, nočna) ---------- */
  let karta = null, mVozilo = null, mCilj = null;
  async function zacniKarto() {
    let st = { karta: false };
    try { st = await AID.api("/ploscice/stanje"); } catch (_) {}
    const rezerva = ["https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"];
    const ploscice = st.karta ? [location.origin + "/ploscice/karta-nocna/{z}/{x}/{y}.png"] : rezerva;
    karta = new maplibregl.Map({
      container: "karta", center: [14.5058, 46.0569], zoom: 14, pitch: 55, bearing: 0, attributionControl: { compact: true },
      style: { version: 8, sources: { podlaga: { type: "raster", tiles: ploscice, tileSize: 256, attribution: st.karta ? esc(st.atribucija || "") : "© OpenStreetMap, © CARTO" } },
        layers: [{ id: "ozadje", type: "background", paint: { "background-color": "#14181C" } },
          { id: "podlaga", type: "raster", source: "podlaga", paint: { "raster-brightness-max": 0.85, "raster-saturation": -0.3 } }] },
    });
    let napake = 0;
    karta.on("error", (e) => { // ploščice posrednika ne delujejo -> rezerva
      if (st.karta && e && e.sourceId === "podlaga" && ++napake === 4) {
        karta.removeLayer("podlaga"); karta.removeSource("podlaga");
        karta.addSource("podlaga", { type: "raster", tiles: rezerva, tileSize: 256, attribution: "© OpenStreetMap, © CARTO" });
        karta.addLayer({ id: "podlaga", type: "raster", source: "podlaga", paint: { "raster-brightness-max": 0.85 } }, "pot-obroba");
      }
    });
    karta.on("dragstart", () => nastaviSledi(false));
    await new Promise((r) => karta.on("load", r));
    karta.addSource("pot", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    karta.addLayer({ id: "pot-obroba", type: "line", source: "pot", layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": "#FF4D5E", "line-width": 16, "line-opacity": 0.25, "line-blur": 4 } });
    karta.addLayer({ id: "pot", type: "line", source: "pot", layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": "#FF4D5E", "line-width": 7 } });
    karta.addLayer({ id: "pot-zracna", type: "line", source: "pot", filter: ["==", ["get", "zracna"], true], paint: { "line-color": "#fff", "line-width": 2, "line-dasharray": [1, 3] } });
    const el = document.createElement("div"); el.className = "vozilo-oznaka"; el.innerHTML = '<div class="puscica"></div>';
    mVozilo = new maplibregl.Marker({ element: el, rotationAlignment: "map", pitchAlignment: "map" }).setLngLat([14.5, 46.05]).addTo(karta);
    const c = document.createElement("div"); c.className = "cilj-oznaka"; c.textContent = "!";
    mCilj = new maplibregl.Marker({ element: c }).setLngLat([14.5, 46.05]);
  }

  /* ---------- podatki ---------- */
  async function osvezi() {
    if (!V.enota) return;
    try { V.d = await AID.api("/voznik/" + encodeURIComponent(V.enota)); }
    catch (e) { napaka(e); return; }
    const d = V.d;
    if (V.planId && d.plan && d.plan.id !== V.planId) dodajObvestilo(`Nov plan v${d.plan.verzija} – naloge posodobljene`, "center");
    V.planId = d.plan && d.plan.id;
    (d.obvestila_centra || []).forEach((o) => dodajObvestilo(o.besedilo, "center", o.cas));
    if (V.sim) ustaviSimulacijo();
    izrisi();
  }
  function dodajObvestilo(besedilo, razred, cas) {
    if (V.obvestila.some((o) => o.besedilo === besedilo)) return;
    V.obvestila.unshift({ besedilo, razred, cas: cas || Date.now() / 1000 });
    V.obvestila = V.obvestila.slice(0, 4);
  }

  function izrisi() {
    const d = V.d, e = d.enota, k = d.korak, p = d.pot;
    $("status-enote").textContent = { prosto: "prosto", na_nalogi: "na nalogi", okvara: "okvara" }[e.stanje] || e.stanje;
    $("status-enote").className = "znacka " + e.stanje;
    // ETA
    if (p) {
      const prihod = new Date(p.prihod * 1000);
      $("eta-min").textContent = Math.max(1, Math.round(p.cas_s / 60));
      $("eta-ura").textContent = "prihod " + AID.uraKratko(prihod);
      $("eta-km").textContent = km(p.razdalja_m);
      $("eta-kakovost").textContent = e.tip === "helikopter" ? "zračna pot" : p.kakovost;
      $("eta-kakovost").className = "kakovost-v" + (p.kakovost === "natančno" ? " natancno" : "");
      const z = [];
      if (p.zamuda_prometa_s > 60) z.push(`Promet: +${Math.round(p.zamuda_prometa_s / 60)} min`);
      if (p.zamik_s > 60) z.push(`Na voljo čez ${Math.round(p.zamik_s / 60)} min`);
      $("eta-zamuda").textContent = z.join(" · ");
      $("eta-zamuda").classList.toggle("skrito", !z.length);
    } else {
      ["eta-min", "eta-ura", "eta-km"].forEach((id) => ($(id).textContent = "—"));
      $("eta-kakovost").textContent = ""; $("eta-zamuda").classList.add("skrito");
    }
    // naloga (samo podatki, ki jih je potrdil dispečer)
    const n = k && k.naloga;
    $("prio").textContent = n ? "P" + n.prioriteta : "P–";
    $("prio").className = "prio" + (n ? " p" + n.prioriteta : "");
    $("naloga-tip").textContent = n ? (n.opis || n.tip).split(" · ")[0] : "Ni naloge";
    $("naloga-akcija").textContent = k ? (k.nujna ? "Nujna vožnja na kraj dogodka" : k.akcija === "prevzem" ? "Prevzem" : "Dostava") + ` · ${k.naloga_id}` : "Čakam na potrjen plan dispečerja";
    $("naslov").textContent = k ? k.naslov || "—" : "—";
    $("orientacija").textContent = k && k.orientacija ? k.orientacija : "";
    const pk = (n && n.podatki_klica) || {};
    const vrstice = [];
    if (pk.stanje_pacienta) vrstice.push(["Pacient", esc(pk.stanje_pacienta)]);
    if (pk.stevilo_poskodovanih != null) vrstice.push(["Poškodovanih", esc(pk.stevilo_poskodovanih)]);
    if (pk.potrebna_oprema && pk.potrebna_oprema.length) vrstice.push(["Oprema", esc([].concat(pk.potrebna_oprema).join(", "))]);
    if (pk.kontakt) vrstice.push(["Klicatelj", `<a href="tel:${esc(String(pk.kontakt).replace(/\s/g, ""))}">${esc(pk.kontakt)}</a>`]);
    if (n && n.kolicina && (n.kolicina.pacienti || n.kolicina.oprema)) vrstice.push(["Tovor", `${n.kolicina.pacienti ? n.kolicina.pacienti + " pacient" : ""}${n.kolicina.oprema ? " " + n.kolicina.oprema + "× oprema" : ""}`]);
    $("podatki").innerHTML = vrstice.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("");
    $("naslednji").innerHTML = d.naslednji.length ? "Nato: " + d.naslednji.map((x) => `<b>${esc(x.naloga_id)}</b> ${x.akcija === "dostava" ? "dostava" : "prevzem"} – ${esc(x.naslov || "")}`).join(" · ") : "";
    izrisiStatuse();
    izrisiOpozorila();
    izrisiPot();
    izrisiManever(0);
  }

  function izrisiStatuse() {
    const k = V.d.korak;
    if (!k) { $("statusi").innerHTML = ""; return; }
    const tok = k.nujna ? ["na_poti", "na_kraju", "opravljeno"] : k.akcija === "prevzem" ? ["na_poti", "na_kraju", "prevzeto"] : ["na_poti", "dostavljeno"];
    const zadnji = (k.naloga.status_ekipe[0] || {}).akcija;
    const i = Math.max(0, tok.indexOf(zadnji) + 1);
    $("statusi").innerHTML = tok.map((a, j) => `<button class="gumb ${j === i ? "naslednji" : j < i ? "opravljen" : ""}" data-status="${a}" ${j < i ? "disabled" : ""}>${AKCIJE[a]}</button>`).join("");
  }
  $("statusi").onclick = async (e) => {
    const b = e.target.closest("[data-status]"); if (!b) return;
    const k = V.d.korak;
    try {
      await AID.api(`/enota/${V.enota}/status`, { metoda: "POST", telo: { naloga_id: k.naloga_id, akcija: b.dataset.status } });
      AID.obvestilo(AKCIJE[b.dataset.status], `${k.naloga_id} – sporočeno centru.`);
      await osvezi();
    } catch (e2) { napaka(e2); }
  };

  function izrisiOpozorila() {
    const p = V.d.pot, nav = (p && p.navodila) || [];
    const ulica = (m) => { const pred = nav.filter((x) => x.odmik_m <= m && x.ulica).pop(); return pred ? pred.ulica : ""; };
    const prometna = ((p && p.opozorila) || []).map((o) => {
      const pred = Math.max(0, o.od_m - (V.sim ? V.sim.prevozeno : 0));
      return `<li>${esc(o.vrsta[0].toUpperCase() + o.vrsta.slice(1))}${ulica(o.od_m) ? " – " + esc(ulica(o.od_m)) : ""}${o.zamuda_s ? ` · +${Math.max(1, Math.round(o.zamuda_s / 60))} min` : ""}
        <small>čez ${km(pred)}${o.hitrost_kmh ? ` · ${o.hitrost_kmh} km/h` : ""}</small></li>`;
    });
    const centra = V.obvestila.map((o) => `<li class="${o.razred}">${esc(o.besedilo)}<small>regijski center · ${AID.uraKratko(new Date(o.cas * 1000))}</small></li>`);
    const vse = [...prometna, ...centra];
    $("opozorila").innerHTML = vse.join("") || '<li class="ni">Ni opozoril na poti.</li>';
  }

  function izrisiPot() {
    if (!karta || !karta.getSource("pot")) return;
    const d = V.d, e = d.enota, p = d.pot;
    mVozilo.setLngLat([e.lon, e.lat]).setRotation(e.smer || 0);
    const geo = p ? p.geometrija.map(([la, lo]) => [lo, la]) : [];
    karta.getSource("pot").setData({ type: "FeatureCollection", features: geo.length ? [{ type: "Feature", properties: { zracna: e.tip === "helikopter" }, geometry: { type: "LineString", coordinates: geo } }] : [] });
    if (d.korak) {
      mCilj.setLngLat([d.korak.lon, d.korak.lat]).addTo(karta);
      mCilj.getElement().textContent = d.korak.nujna ? "!" : d.korak.akcija === "dostava" ? "H" : "P";
      if (V.sledi && geo.length > 1) {
        const b = geo.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(geo[0], geo[0]));
        karta.fitBounds(b, { padding: { top: 120, bottom: 80, left: 340, right: 400 }, pitch: 50, bearing: smer(geo[0], geo[Math.min(geo.length - 1, 8)]), duration: 900, maxZoom: 16 });
      }
    } else {
      mCilj.remove();
      if (V.sledi) karta.easeTo({ center: [e.lon, e.lat], zoom: 14, duration: 600 });
    }
  }
  function smer(a, b) {
    const r = Math.PI / 180;
    const y = Math.sin((b[0] - a[0]) * r) * Math.cos(b[1] * r);
    const x = Math.cos(a[1] * r) * Math.sin(b[1] * r) - Math.sin(a[1] * r) * Math.cos(b[1] * r) * Math.cos((b[0] - a[0]) * r);
    return (Math.atan2(y, x) / r + 360) % 360;
  }

  function izrisiManever(prevozeno) {
    const p = V.d && V.d.pot;
    const nav = ((p && p.navodila) || []).filter((x) => x.manever !== "DEPART" && x.odmik_m > prevozeno + 15);
    const n = nav[0];
    $("manever").classList.toggle("skrito", !n);
    if (!n) return;
    $("manever-ikona").textContent = IKONE[n.manever] || "↑";
    $("manever-razdalja").textContent = km(n.odmik_m - prevozeno);
    $("manever-besedilo").textContent = n.sporocilo;
  }

  /* ---------- obveščeni v bližini (simulacija – realni podatki niso del prototipa) ---------- */
  setInterval(() => {
    const v = V.sim ? 1 : 0.4;
    $("obvesceni").textContent = V.d && V.d.korak ? Math.round((18 + 14 * Math.abs(Math.sin(Date.now() / 45000))) * (0.6 + v)) : "—";
  }, 3000);

  /* ---------- demo: simulirana vožnja ---------- */
  function ustaviSimulacijo() {
    if (!V.sim) return;
    clearInterval(V.sim.t); V.sim = null;
    $("gumb-simulacija").textContent = "▶ Simuliraj vožnjo"; $("gumb-simulacija").classList.remove("vklopljen");
  }
  $("gumb-simulacija").onclick = () => {
    if (V.sim) { ustaviSimulacijo(); return; }
    const p = V.d && V.d.pot;
    if (!p || p.geometrija.length < 2) { AID.obvestilo("Ni poti", "Najprej mora dispečer potrditi plan z nalogo za to vozilo."); return; }
    const g = p.geometrija, kum = [0];
    for (let i = 1; i < g.length; i++) kum.push(kum[i - 1] + AID.razdaljaM(g[i - 1], g[i]));
    const hitrost = Math.max(8, (p.razdalja_m / Math.max(60, p.cas_s)) * 10); // 10× pospešeno
    V.sim = { prevozeno: 0, t: null, zadnjiPoslan: 0 };
    nastaviSledi(true);
    $("gumb-simulacija").textContent = "■ Ustavi"; $("gumb-simulacija").classList.add("vklopljen");
    V.sim.t = setInterval(() => {
      const s = V.sim; if (!s) return;
      s.prevozeno = Math.min(kum[kum.length - 1], s.prevozeno + hitrost);
      let i = 1; while (i < kum.length - 1 && kum[i] < s.prevozeno) i++;
      const t = (s.prevozeno - kum[i - 1]) / (kum[i] - kum[i - 1] || 1);
      const lat = g[i - 1][0] + (g[i][0] - g[i - 1][0]) * t, lon = g[i - 1][1] + (g[i][1] - g[i - 1][1]) * t;
      const sm = smer([g[i - 1][1], g[i - 1][0]], [g[i][1], g[i][0]]);
      mVozilo.setLngLat([lon, lat]).setRotation(sm);
      if (V.sledi) karta.easeTo({ center: [lon, lat], bearing: sm, pitch: 60, zoom: 16.3, duration: 950, easing: (x) => x });
      const ostane = kum[kum.length - 1] - s.prevozeno;
      $("eta-min").textContent = Math.max(0, Math.round((ostane / hitrost) * 10 / 60));
      $("eta-km").textContent = km(ostane);
      izrisiManever(s.prevozeno); izrisiOpozorila();
      if (Date.now() - s.zadnjiPoslan > 2000) {
        s.zadnjiPoslan = Date.now();
        AID.api(`/enota/${V.enota}/lokacija`, { metoda: "POST", telo: { lat, lon, smer: Math.round(sm), hitrost_kmh: Math.round((hitrost / 10) * 3.6) } }).catch(() => {});
      }
      if (ostane <= 1) { ustaviSimulacijo(); AID.obvestilo("Na cilju", "Potrdite status NA KRAJU."); }
    }, 1000);
  };
  function nastaviSledi(v) { V.sledi = v; $("gumb-sledi").classList.toggle("vklopljen", v); }
  $("gumb-sledi").onclick = () => { nastaviSledi(true); izrisiPot(); };

  /* ---------- sporočilo centru ---------- */
  const dlg = $("dlg-sporocilo");
  $("gumb-sporocilo").onclick = () => { $("sporocilo-besedilo").value = ""; dlg.showModal(); };
  dlg.querySelectorAll("[data-hitro]").forEach((b) => (b.onclick = () => ($("sporocilo-besedilo").value = b.dataset.hitro)));
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "poslji") return;
    const t = $("sporocilo-besedilo").value.trim(); if (!t) return;
    try {
      const r = await AID.api("/sporocilo", { metoda: "POST", telo: { enota_id: V.enota, besedilo: t } });
      AID.obvestilo("Poslano centru", r.predlogi_dogodkov.length ? "Dispečer bo potrdil predlagan dogodek." : "Sporočilo je prejel dispečer.");
    } catch (e) { napaka(e); }
  });

  /* ---------- dogodki s strežnika ---------- */
  let zakasnitev = null;
  const kasneje = () => { clearTimeout(zakasnitev); zakasnitev = setTimeout(osvezi, 400); };
  AID.na("plan_aktiven", kasneje);
  AID.na("dogodek", (d) => { if (d.stanje !== "predlagano" && (d.parametri.enota_id === V.enota)) { dodajObvestilo(d.opis || d.tip, "center"); kasneje(); } });
  AID.na("enote", () => { if (!V.sim) kasneje(); });

  /* ---------- zagon ---------- */
  document.addEventListener("DOMContentLoaded", async () => {
    try { V.enote = await AID.api("/enote"); } catch (e) { napaka(e); }
    const iz = new URLSearchParams(location.search).get("enota");
    V.enota = iz || AID.beri("aid_voznik_enota", null) || (V.enote.find((e) => e.tip === "vozilo") || {}).id;
    $("enota").innerHTML = V.enote.map((e) => `<option value="${esc(e.id)}" ${e.id === V.enota ? "selected" : ""}>${esc(e.id)} · ${esc(e.ime)}</option>`).join("");
    $("enota").onchange = () => { V.enota = $("enota").value; AID.imeUporabnika = "voznik " + V.enota; AID.pisi("aid_voznik_enota", V.enota); history.replaceState(null, "", "?enota=" + V.enota); V.planId = null; V.obvestila = []; ustaviSimulacijo(); osvezi(); };
    AID.imeUporabnika = "voznik " + V.enota;
    nastaviSledi(true);
    if (window.maplibregl) { try { await zacniKarto(); } catch (e) { console.error(e); } }
    await osvezi();
  });
})();
