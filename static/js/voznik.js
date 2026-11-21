/* AID – spletni zaslon voznika reševalnega vozila.
 * Odjemalec istega API-ja kot Flet aplikacija (/voznik/*): prijava enota + koda, žeton v glavi,
 * enota vedno iz žetona. Prikazuje le korake POTRJENEGA plana in potrjene podatke klica.
 * Pot: po cestah s prometom (ponudnik je skrit), navodila za zavijanje, opozorila v živo.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);
  const V = { n: null, sim: null, sledi: true, gps: null, planId: null, obvestila: [], ws: null, seq: 0, zagon: null };
  const IKONE = { TURN_LEFT: "↰", SHARP_LEFT: "↰", BEAR_LEFT: "↖", KEEP_LEFT: "↖", TURN_RIGHT: "↱", SHARP_RIGHT: "↱", BEAR_RIGHT: "↗",
    KEEP_RIGHT: "↗", STRAIGHT: "↑", ROUNDABOUT_LEFT: "⟲", ROUNDABOUT_RIGHT: "⟳", ROUNDABOUT_CROSS: "⟳", ROUNDABOUT_BACK: "⟲",
    ENTER_MOTORWAY: "↗", ENTER_FREEWAY: "↗", MOTORWAY_EXIT_LEFT: "↖", MOTORWAY_EXIT_RIGHT: "↗", TAKE_EXIT: "↗", MAKE_UTURN: "↶",
    ARRIVE: "⚑", ARRIVE_LEFT: "⚑", ARRIVE_RIGHT: "⚑", DEPART: "↑", FOLLOW: "↑" };
  const STANJA = { prejeto: "PREJETO", na_poti: "NA POTI", na_prizoriscu: "NA KRAJU", pacient_nalozen: "PACIENT NALOŽEN",
    dostavljeno: "DOSTAVLJENO", opravljeno: "OPRAVLJENO", prosto: "PROSTO", ni_na_voljo: "NI NA VOLJO" };
  const napaka = (e) => AID.obvestilo("Ni uspelo", e.message, true, 5000);
  const km = (m) => (m >= 1000 ? (m / 1000).toFixed(1).replace(".", ",") + " km" : Math.round(m / 10) * 10 + " m");
  const razdaljaM = (a, b) => {
    const r = Math.PI / 180, x = Math.sin((b[0] - a[0]) * r / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(x));
  };

  /* ---------- seja voznika (žetoni samo v tem zavihku) ---------- */
  const seja = {
    get: () => { try { return JSON.parse(sessionStorage.getItem("aid_voznik_seja") || "null"); } catch (_) { return null; } },
    set: (s) => { try { s ? sessionStorage.setItem("aid_voznik_seja", JSON.stringify(s)) : sessionStorage.removeItem("aid_voznik_seja"); } catch (_) {} },
  };
  const naprava = (() => {
    let id = AID.beri("aid_naprava", null);
    if (!id) { id = "splet-" + Math.random().toString(36).slice(2, 10); AID.pisi("aid_naprava", id); }
    return id;
  })();

  function nastaviPovezavo(ok, besedilo) {
    $("povezava").textContent = besedilo; $("povezava").classList.toggle("napaka", !ok);
    $("pas-povezave").classList.toggle("skrito", ok);
    if (!ok) $("pas-povezave").textContent = "Strežnik ni dosegljiv – prikazano je zadnje znano stanje. Ponovno povezovanje …";
  }

  async function api(pot, { metoda = "GET", telo, brezZetona = false, ponovi = true } = {}) {
    const s = seja.get();
    const glave = { "Content-Type": "application/json" };
    if (!brezZetona && s) glave.Authorization = "Bearer " + s.dostop;
    let r;
    try { r = await fetch(pot, { method: metoda, headers: glave, body: telo === undefined ? undefined : JSON.stringify(telo) }); }
    catch (_) { nastaviPovezavo(false, "Brez povezave"); throw new Error("Strežnik ni dosegljiv."); }
    nastaviPovezavo(true, "Povezava OK");
    if (r.status === 401 && !brezZetona && ponovi && s) { // dostopni žeton je potekel -> osveži (enkratna raba)
      const o = await fetch("/voznik/osvezi", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ osvezitev: s.osvezitev }) });
      if (o.ok) { seja.set({ ...s, ...(await o.json()) }); return api(pot, { metoda, telo, ponovi: false }); }
      odjavljen("Seja je potekla – prijavite se znova.");
      throw new Error("Seja je potekla.");
    }
    const podatki = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (!r.ok) throw new Error((podatki && (podatki.napaka || podatki.detail)) || "Napaka " + r.status);
    return podatki;
  }

  /* ---------- prijava ---------- */
  async function pokaziPrijavo(sporocilo) {
    $("prijava").classList.remove("skrito");
    $("napaka-prijave").textContent = sporocilo || "";
    try {
      const enote = await api("/voznik/enote-za-prijavo", { brezZetona: true });
      // vozilo iz naslova (?enota=RV-3) ima prednost pred zadnjim uporabljenim
      const prej = new URLSearchParams(location.search).get("enota") || AID.beri("aid_voznik_enota", null);
      $("prijava-enota").innerHTML = enote.map((e) => `<option value="${esc(e.id)}" ${e.id === prej ? "selected" : ""}>${esc(e.id)} · ${esc(e.ime)}</option>`).join("");
    } catch (_) {}
    $("prijava-koda").focus();
  }
  $("obrazec-prijave").onsubmit = async (e) => {
    e.preventDefault();
    const enota_id = $("prijava-enota").value, koda = $("prijava-koda").value.trim();
    try {
      const r = await api("/voznik/prijava", { metoda: "POST", telo: { enota_id, koda, naprava_id: naprava }, brezZetona: true });
      seja.set({ dostop: r.dostop, osvezitev: r.osvezitev, enota: r.enota, gps: r.gps });
      AID.pisi("aid_voznik_enota", enota_id);
      $("prijava-koda").value = "";
      $("prijava").classList.add("skrito");
      await zacni();
    } catch (e2) { $("napaka-prijave").textContent = e2.message; }
  };
  function odjavljen(sporocilo) {
    ustaviSimulacijo(); ustaviGps();
    seja.set(null);
    if (V.ws) { try { V.ws.close(); } catch (_) {} V.ws = null; }
    pokaziPrijavo(sporocilo);
  }
  $("gumb-odjava").onclick = async () => {
    try { await api("/voznik/odjava", { metoda: "POST" }); } catch (_) {}
    odjavljen("Odjavljeni ste.");
  };

  /* ---------- karta (3D, nočna) ---------- */
  let karta = null, mVozilo = null, mCilj = null;
  async function zacniKarto() {
    if (karta || !window.maplibregl) return;
    let st = { karta: false };
    try { st = await api("/ploscice/stanje"); } catch (_) {}
    const rezerva = ["https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"];
    const ploscice = st.karta ? [location.origin + "/ploscice/karta-nocna/{z}/{x}/{y}.png"] : rezerva;
    karta = new maplibregl.Map({
      // ploščice posrednika zahtevajo prijavo: žeton voznika v glavi (ne v URL – dnevniki)
      transformRequest: (url) => (url.startsWith(location.origin + "/ploscice/") && seja.get()
        ? { url, headers: { Authorization: "Bearer " + seja.get().dostop } } : undefined),
      container: "karta", center: [14.5058, 46.0569], zoom: 14, pitch: 55, bearing: 0, attributionControl: { compact: true },
      style: { version: 8, sources: { podlaga: { type: "raster", tiles: ploscice, tileSize: 256, attribution: st.karta ? esc(st.atribucija || "") : "© OpenStreetMap, © CARTO" } },
        layers: [{ id: "ozadje", type: "background", paint: { "background-color": "#14181C" } },
          { id: "podlaga", type: "raster", source: "podlaga", paint: { "raster-brightness-max": 0.85, "raster-saturation": -0.3 } }] },
    });
    let napake = 0;
    karta.on("error", (e) => {
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
    const el = document.createElement("div"); el.className = "vozilo-oznaka"; el.innerHTML = '<div class="puscica"></div>';
    mVozilo = new maplibregl.Marker({ element: el, rotationAlignment: "map", pitchAlignment: "map" }).setLngLat([14.5, 46.05]).addTo(karta);
    const c = document.createElement("div"); c.className = "cilj-oznaka"; c.textContent = "!";
    mCilj = new maplibregl.Marker({ element: c }).setLngLat([14.5, 46.05]);
  }

  /* ---------- podatki ---------- */
  async function osvezi() {
    if (!seja.get()) return;
    let n;
    try { n = await api("/voznik/naloga"); } catch (e) { if (seja.get()) napaka(e); return; }
    if (V.planId && n.plan && n.plan.id !== V.planId) dodajObvestilo(`Nov plan v${n.plan.verzija} – naloge posodobljene`, "center");
    V.planId = n.plan && n.plan.id;
    V.n = n;
    if (V.sim) ustaviSimulacijo();
    izrisi();
    if (n.koraki.some((k) => k.trenutni) && !n.pot) setTimeout(osvezi, 2500); // pot se še računa
  }
  function dodajObvestilo(besedilo, razred) {
    if (V.obvestila.some((o) => o.besedilo === besedilo)) return;
    V.obvestila.unshift({ besedilo, razred, cas: Date.now() / 1000 });
    V.obvestila = V.obvestila.slice(0, 4);
  }
  const trenutni = () => (V.n ? V.n.koraki.find((k) => k.trenutni) : null);

  function izrisi() {
    const n = V.n, e = n.enota, k = trenutni(), p = n.pot;
    $("enota-ime").textContent = e ? `${e.id} · ${e.ime}` : "—";
    $("status-enote").textContent = { prosto: "prosto", na_nalogi: "na nalogi", okvara: "okvara", ni_na_voljo: "ni na voljo" }[e.stanje] || e.stanje;
    $("status-enote").className = "znacka " + e.stanje;
    if (p) {
      $("eta-min").textContent = Math.max(1, Math.round(p.cas_s / 60));
      $("eta-ura").textContent = "prihod " + AID.uraKratko(new Date(Date.now() + p.cas_s * 1000));
      $("eta-km").textContent = km(p.razdalja_m);
      $("eta-kakovost").textContent = p.cestna === false ? "zračna pot" : p.kakovost;
      $("eta-kakovost").className = "kakovost-v" + (p.kakovost === "natančno" ? " natancno" : "");
      const z = p.zamuda_prometa_s > 60 ? `Promet: +${Math.round(p.zamuda_prometa_s / 60)} min` : "";
      $("eta-zamuda").textContent = z; $("eta-zamuda").classList.toggle("skrito", !z);
    } else {
      ["eta-min", "eta-ura", "eta-km"].forEach((id) => ($(id).textContent = k ? "…" : "—"));
      $("eta-kakovost").textContent = ""; $("eta-zamuda").classList.add("skrito");
    }
    const nal = k && k.naloga;
    $("prio").textContent = nal ? "P" + nal.prioriteta : "P–";
    $("prio").className = "prio" + (nal ? " p" + nal.prioriteta : "");
    $("naloga-tip").textContent = nal ? String(nal.opis || nal.tip).split(" · ")[0] : "Ni naloge";
    $("naloga-akcija").textContent = k ? (k.nujna ? "Nujna vožnja na kraj dogodka" : k.akcija === "prevzem" ? "Prevzem" : "Dostava") + ` · ${k.naloga_id}${n.faza ? " · " + (STANJA[n.faza] || n.faza).toLowerCase() : ""}` : "Čakam na potrjen plan dispečerja";
    const mesto = nal ? (k.akcija === "dostava" ? nal.dostava : nal.prevzem) || {} : {};
    const pk = (nal && nal.potrjeno) || {};
    $("naslov").textContent = k ? mesto.naslov || pk.naslov || "—" : "—";
    $("orientacija").textContent = mesto.orientacija || pk.orientacija || "";
    const vrstice = [];
    if (pk.stanje_pacienta) vrstice.push(["Pacient", esc(pk.stanje_pacienta)]);
    if (pk.stevilo_poskodovanih != null) vrstice.push(["Poškodovanih", esc(pk.stevilo_poskodovanih)]);
    if (pk.potrebna_oprema && pk.potrebna_oprema.length) vrstice.push(["Oprema", esc([].concat(pk.potrebna_oprema).join(", "))]);
    if (pk.kontakt) vrstice.push(["Klicatelj", `<a href="tel:${esc(String(pk.kontakt).replace(/\s/g, ""))}">${esc(pk.kontakt)}</a>`]);
    if (nal && nal.kolicina && (nal.kolicina.pacienti || nal.kolicina.oprema)) vrstice.push(["Tovor", `${nal.kolicina.pacienti ? nal.kolicina.pacienti + " pacient" : ""}${nal.kolicina.oprema ? " " + nal.kolicina.oprema + "× oprema" : ""}`]);
    $("podatki").innerHTML = vrstice.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("");
    const naprej = n.koraki.filter((x) => !x.opravljen && !x.trenutni).slice(0, 3);
    $("naslednji").innerHTML = naprej.length ? "Nato: " + naprej.map((x) => `<b>${esc(x.naloga_id)}</b> ${x.akcija === "dostava" ? "dostava" : "prevzem"}`).join(" · ") : "";
    izrisiStanja(); izrisiOpozorila(); izrisiPot(); izrisiManever(0);
  }

  /* stanja določa strežnik (dovoljena_stanja); prvo je naslednji korak */
  function izrisiStanja() {
    const k = trenutni(), dovoljena = V.n.dovoljena_stanja || [];
    const izmena = dovoljena.filter((s) => s === "prosto" || s === "ni_na_voljo");
    const naloga = dovoljena.filter((s) => !izmena.includes(s));
    const gumbi = naloga.map((s, i) => `<button class="gumb ${i === 0 ? "naslednji" : ""}" data-stanje="${s}">${STANJA[s] || s}</button>`)
      .concat(izmena.map((s) => `<button class="gumb" data-stanje="${s}">${STANJA[s] || s}</button>`));
    $("statusi").innerHTML = gumbi.join("");
    $("statusi").dataset.naloga = k ? k.naloga_id : "";
    $("statusi").dataset.akcija = k ? k.akcija : "";
  }
  $("statusi").onclick = async (e) => {
    const b = e.target.closest("[data-stanje]"); if (!b) return;
    const telo = { stanje: b.dataset.stanje };
    if ($("statusi").dataset.naloga) Object.assign(telo, { naloga_id: $("statusi").dataset.naloga, akcija: $("statusi").dataset.akcija });
    b.disabled = true;
    try {
      await api("/voznik/stanje", { metoda: "POST", telo });
      AID.obvestilo(STANJA[telo.stanje] || telo.stanje, "Sporočeno centru.");
      await osvezi();
    } catch (e2) { napaka(e2); b.disabled = false; }
  };

  function izrisiOpozorila() {
    const p = V.n.pot, nav = (p && p.navodila) || [];
    const ulica = (m) => { const pred = nav.filter((x) => x.odmik_m <= m && x.ulica).pop(); return pred ? pred.ulica : ""; };
    const prejem = (V.n.obvestila || []).filter((o) => o.stanje === "caka").map((o) =>
      `<li class="prejem">Nov plan od dispečerja – potrdite prejem<small style="color:#fff">${(o.razlike && o.razlike.dodani || []).length ? "dodane naloge: " + esc(o.razlike.dodani.map((x) => (Array.isArray(x) ? x[0] : x.naloga_id || x)).join(", ")) : "spremenjen vrstni red ali naloge"}</small>
        <button data-potrdi="${esc(o.id)}">POTRDI PREJEM</button></li>`);
    if (prejem.length && navigator.vibrate) navigator.vibrate([200, 100, 200]);
    const prometna = ((p && p.opozorila) || []).map((o) => {
      const pred = Math.max(0, o.od_m - (V.sim ? V.sim.prevozeno : 0));
      return `<li>${esc(o.vrsta[0].toUpperCase() + o.vrsta.slice(1))}${ulica(o.od_m) ? " – " + esc(ulica(o.od_m)) : ""}${o.zamuda_s ? ` · +${Math.max(1, Math.round(o.zamuda_s / 60))} min` : ""}
        <small>čez ${km(pred)}${o.hitrost_kmh ? ` · ${o.hitrost_kmh} km/h` : ""}</small></li>`;
    });
    const PREDLOGI = { okvara_vozila: "Okvara vozila", ovira_na_cesti: "Ovira na cesti", prevoz_z_nujne_voznje: "Prevoz pacienta", enota_ni_na_voljo: "Ni na voljo" };
    const STP = { predlagano: "čaka dispečerja", sprejeto: "dispečer je potrdil", spremenjeno: "dispečer je potrdil (spremenjeno)", zavrnjeno: "dispečer je zavrnil" };
    const dispecer = (V.n.sporocila || []).filter((m) => m.smer === "od_dispecerja" && !m.prebrano).map((m) =>
      `<li class="dispecer">${esc(m.besedilo)}<small>dispečer · ${AID.uraKratko(new Date(m.cas * 1000))}</small><button class="mali" data-prebrano="${esc(m.id)}">Prebrano</button></li>`);
    const predlogi = (V.n.predlogi || []).filter((d) => d.stanje === "predlagano" || (d.ustvarjeno || 0) > Date.now() / 1000 - 900).slice(-2).map((d) =>
      `<li class="center">${esc(PREDLOGI[d.tip] || d.tip)}: ${esc(STP[d.stanje] || d.stanje)}${d.razlog ? " – " + esc(d.razlog) : ""}<small>predlog ekipe</small></li>`);
    const centra = V.obvestila.map((o) => `<li class="${o.razred}">${esc(o.besedilo)}<small>${AID.uraKratko(new Date(o.cas * 1000))}</small></li>`);
    $("opozorila").innerHTML = [...prejem, ...dispecer, ...predlogi, ...prometna, ...centra].join("") || '<li class="ni">Ni opozoril na poti.</li>';
  }
  $("opozorila").onclick = async (e) => {
    const pr = e.target.closest("[data-prebrano]");
    if (pr) { try { await api(`/voznik/sporocilo/${pr.dataset.prebrano}/prebrano`, { metoda: "POST" }); await osvezi(); } catch (e2) { napaka(e2); } return; }
    const b = e.target.closest("[data-potrdi]"); if (!b) return;
    try { await api("/voznik/potrdi", { metoda: "POST", telo: { obvestilo_id: b.dataset.potrdi } }); await osvezi(); }
    catch (e2) { napaka(e2); }
  };

  function izrisiPot() {
    if (!karta || !karta.getSource("pot")) return;
    const n = V.n, e = n.enota, p = n.pot, k = trenutni();
    if (!V.sim) mVozilo.setLngLat([e.lon, e.lat]);
    const geo = p ? p.geometrija.map(([la, lo]) => [lo, la]) : [];
    karta.getSource("pot").setData({ type: "FeatureCollection", features: geo.length ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: geo } }] : [] });
    if (k) {
      mCilj.setLngLat([k.lon, k.lat]).addTo(karta);
      mCilj.getElement().textContent = k.nujna ? "!" : k.akcija === "dostava" ? "H" : "P";
      if (V.sledi && geo.length > 1 && !V.sim) {
        const b = geo.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(geo[0], geo[0]));
        const ozko = window.innerWidth < 1100;
        karta.fitBounds(b, { padding: ozko ? { top: 140, bottom: window.innerHeight * 0.48, left: 30, right: 30 } : { top: 120, bottom: 80, left: 340, right: 400 },
          pitch: 50, bearing: smer(geo[0], geo[Math.min(geo.length - 1, 8)]), duration: 900, maxZoom: 16 });
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
    const p = V.n && V.n.pot;
    const n = ((p && p.navodila) || []).filter((x) => x.manever !== "DEPART" && x.odmik_m > prevozeno + 15)[0];
    $("manever").classList.toggle("skrito", !n);
    if (!n) return;
    $("manever-ikona").textContent = IKONE[n.manever] || "↑";
    $("manever-razdalja").textContent = km(n.odmik_m - prevozeno);
    $("manever-besedilo").textContent = n.sporocilo;
  }

  /* obveščeni v bližini: simulacija (v prototipu ni vira teh podatkov) */
  setInterval(() => {
    const k = trenutni();
    $("obvesceni").textContent = k ? Math.round((18 + 14 * Math.abs(Math.sin(Date.now() / 45000))) * (V.sim ? 1.6 : 1)) : "—";
  }, 3000);

  /* ---------- položaj: paketi fiksov z zaporedno številko (idempotentno) ---------- */
  let seq = AID.beri("aid_gps_seq_" + naprava, 0);
  async function posljiFiks(lat, lon, hitrost_ms, smerStopinj, natancnost) {
    seq += 1; AID.pisi("aid_gps_seq_" + naprava, seq);
    const fiks = { seq, ts: Date.now() / 1000, lat, lon, natancnost: natancnost ?? 5 };
    if (hitrost_ms != null) fiks.hitrost = Math.max(0, Math.min(200, hitrost_ms));
    if (smerStopinj != null) fiks.smer = ((smerStopinj % 360) + 360) % 360;
    try { await api("/voznik/polozaj", { metoda: "POST", telo: { naprava_id: naprava, poslano_ob: Date.now() / 1000, fiksi: [fiks] } }); } catch (_) {}
  }
  function ustaviGps() { if (V.gps != null) navigator.geolocation.clearWatch(V.gps); V.gps = null; $("gumb-gps").classList.remove("vklopljen"); }
  $("gumb-gps").onclick = () => {
    if (V.gps != null) { ustaviGps(); return; }
    if (!navigator.geolocation) { AID.obvestilo("GPS ni na voljo", "Brskalnik ne podpira določanja položaja.", true); return; }
    ustaviSimulacijo();
    let zadnji = 0;
    V.gps = navigator.geolocation.watchPosition((p) => {
      const c = p.coords;
      mVozilo && mVozilo.setLngLat([c.longitude, c.latitude]).setRotation(c.heading || 0);
      if (Date.now() - zadnji > 5000) { zadnji = Date.now(); posljiFiks(c.latitude, c.longitude, c.speed, c.heading, c.accuracy); }
    }, (e) => { AID.obvestilo("GPS", e.message, true); ustaviGps(); }, { enableHighAccuracy: true, maximumAge: 2000 });
    $("gumb-gps").classList.add("vklopljen");
  };

  /* ---------- demo: simulirana vožnja (10× pospešeno) ---------- */
  function ustaviSimulacijo() {
    if (!V.sim) return;
    clearInterval(V.sim.t); V.sim = null;
    $("gumb-simulacija").textContent = "▶ Simuliraj vožnjo"; $("gumb-simulacija").classList.remove("vklopljen");
  }
  $("gumb-simulacija").onclick = () => {
    if (V.sim) { ustaviSimulacijo(); return; }
    const p = V.n && V.n.pot;
    if (!p || p.geometrija.length < 2) { AID.obvestilo("Ni poti", "Dispečer mora potrditi plan z nalogo za to vozilo."); return; }
    ustaviGps();
    const g = p.geometrija, kum = [0];
    for (let i = 1; i < g.length; i++) kum.push(kum[i - 1] + razdaljaM(g[i - 1], g[i]));
    const hitrost = Math.max(8, (p.razdalja_m / Math.max(60, p.cas_s)) * 10);
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
      if (Date.now() - s.zadnjiPoslan > 2000) { s.zadnjiPoslan = Date.now(); posljiFiks(lat, lon, hitrost / 10, sm, 5); }
      if (ostane <= 1) { ustaviSimulacijo(); AID.obvestilo("Na cilju", "Sporočite NA KRAJU."); }
    }, 1000);
  };
  function nastaviSledi(v) { V.sledi = v; $("gumb-sledi").classList.toggle("vklopljen", v); }
  $("gumb-sledi").onclick = () => { nastaviSledi(true); izrisiPot(); };

  /* ---------- sporočila centru: hitra, besedilo (-> AI predlog), okvara, ovira ---------- */
  const dlg = $("dlg-sporocilo");
  $("gumb-sporocilo").onclick = () => { $("sporocilo-besedilo").value = ""; dlg.showModal(); };
  async function poslji(pot, telo, naslov) {
    try {
      const r = await api(pot, { metoda: "POST", telo });
      dlg.close("poslano");
      const ai = r && r.predlog_ai;
      AID.obvestilo(naslov, ai ? "AI je iz sporočila pripravil predlog – dispečer ga bo potrdil." : "Dispečer je prejel.");
      await osvezi();
    } catch (e) { napaka(e); }
  }
  dlg.querySelectorAll("[data-koda]").forEach((b) => (b.onclick = () => poslji("/voznik/sporocilo", { koda: b.dataset.koda }, b.textContent)));
  dlg.querySelector("[data-okvara]").onclick = () => poslji("/voznik/okvara", { opis: $("sporocilo-besedilo").value.trim() || null }, "Okvara prijavljena");
  dlg.querySelector("[data-ovira]").onclick = () => poslji("/voznik/ovira", { vrsta: $("vrsta-ovire").value, opis: $("sporocilo-besedilo").value.trim() || null }, "Ovira prijavljena");
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "poslji") return;
    const t = $("sporocilo-besedilo").value.trim(); if (!t) return;
    await poslji("/voznik/sporocilo", { besedilo: t }, "Sporočilo poslano");
  });

  /* ---------- sprotne spremembe: /ws/voznik (žeton v prvem sporočilu), rezerva polling ---------- */
  let zakasnitev = null, poll = null, poskusi = 0;
  const kasneje = () => { clearTimeout(zakasnitev); zakasnitev = setTimeout(osvezi, 300); };
  function sprejmi(s) {
    if (s.zagon && V.zagon && s.zagon !== V.zagon) V.seq = 0;
    if (s.zagon) V.zagon = s.zagon;
    if (s.seq) V.seq = Math.max(V.seq, s.seq);
    if (s.tip && !["ziv", "pozdrav"].includes(s.tip)) kasneje();
  }
  function poveziWs() {
    const s = seja.get(); if (!s) return;
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/voznik");
    V.ws = ws;
    ws.onopen = () => { ws.send(JSON.stringify({ zeton: s.dostop, od: V.seq, zagon: V.zagon })); poskusi = 0; clearInterval(poll); poll = null; };
    ws.onmessage = (e) => sprejmi(JSON.parse(e.data));
    ws.onclose = (e) => {
      if (V.ws !== ws || !seja.get()) return;
      if (!poll) poll = setInterval(async () => {
        try { const r = await api(`/voznik/dogodki?od=${V.seq}${V.zagon ? "&zagon=" + V.zagon : ""}`); r.dogodki.forEach(sprejmi); sprejmi({ zagon: r.zagon, seq: r.seq }); } catch (_) {}
      }, 3000);
      // 4401 = žeton je potekel: osveži ga z enim klicem API in se znova priklopi
      setTimeout(async () => { if (e.code === 4401) { try { await api("/voznik/jaz"); } catch (_) { return; } } poveziWs(); }, Math.min(15000, 800 * 2 ** poskusi++));
    };
  }

  async function zacni() {
    await zacniKarto();
    await osvezi();
    poveziWs();
  }

  document.addEventListener("DOMContentLoaded", () => {
    nastaviSledi(true);
    if (seja.get()) zacni(); else pokaziPrijavo();
  });
})();
