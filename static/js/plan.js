/* AID – pogled »Plan in karta« (regijski center).
 * Prikaže aktivni plan, predlog novega plana s primerjavo prej/potem in alternative.
 * Plan postane aktiven šele, ko ga dispečer potrdi.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);
  const P = { enote: [], naloge: [], centri: [], aktiven: null, predlog: null, prikazan: null, alternative: [], altIndeks: 0,
    hb: {}, preusmeritve: {} };

  const TIP = { vozilo: "Reševalno vozilo", motorist: "Motorist", helikopter: "Helikopter" };
  const STANJE = { prosto: "prosto", na_nalogi: "na nalogi", okvara: "okvara" };
  const napaka = (e) => AID.obvestilo("Dejanje ni uspelo", e.message, true, 6000);
  const naloga = (id) => P.naloge.find((n) => n.id === id);
  const minut = (s) => Math.max(0, Math.round(s / 60));

  /* ---------- karta ---------- */
  let karta = null;
  const sloji = {};
  function zacniKarto() {
    if (!window.L || karta) return;
    karta = L.map("karta", { zoomControl: true }).setView([46.056, 14.505], 12);
    AID.podlagaKarte(karta, { promet: true }).then((izid) => {
      const { promet } = izid;
      if (!promet) return;
      // stikalo »Promet« (zastoji in nesreče) – privzeto vklopljeno
      promet.addTo(karta);
      const Stikalo = L.Control.extend({
        onAdd() {
          const d = L.DomUtil.create("label", "stikalo-promet");
          d.innerHTML = '<input type="checkbox" checked> Promet';
          L.DomEvent.disableClickPropagation(d);
          d.querySelector("input").onchange = (e) => (e.target.checked ? promet.addTo(karta) : karta.removeLayer(promet));
          return d;
        },
      });
      const stikalo = new Stikalo({ position: "topright" }).addTo(karta);
      izid.onIzpad = () => stikalo.remove();
    });
    ["centri", "naloge", "aktiven", "predlog", "preusmeritve", "enote"].forEach((k) => (sloji[k] = L.layerGroup().addTo(karta)));
  }
  /* ---------- živi položaj in signal (GPS z naprave voznika) ---------- */
  const SIGNAL = { v_zivo: "GPS", zakasnjeno: "GPS zakasnjen", ni_signala: "ni signala", brez_gps: "brez GPS", neznano: "neznano" };
  const RANG = { v_zivo: 0, zakasnjeno: 1, ni_signala: 2, brez_gps: 3, neznano: 4 };
  function starostBesedilo(s) {
    if (s == null) return "";
    s = Math.max(0, s);
    return s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
  }
  /* Signal se računa tudi lokalno iz starosti položaja: star položaj ni nikoli prikazan kot živ,
   * tudi če obvestilo strežnika zamudi. Brez povezave s strežnikom je stanje »neznano«. */
  function signalEnote(e) {
    const p = e.polozaj;
    if (!p || p.vir !== "gps" || p.ts == null) return { signal: "brez_gps", starost: null };
    const starost = AID.streznikZdaj() - p.ts;
    let lokalno = starost >= p.prag_brez_s ? "ni_signala" : starost >= p.prag_zakasnjeno_s ? "zakasnjeno" : "v_zivo";
    if (RANG[p.signal] > RANG[lokalno]) lokalno = p.signal;
    if (AID.stanjePovezave === "brez") lokalno = "neznano";
    return { signal: lokalno, starost };
  }
  /* voznik: prijava, faza trenutnega koraka, potrditev prejema plana */
  const FAZA = { prejeto: "prejeto", na_poti: "na poti", na_prizoriscu: "na prizorišču" };
  /* heartbeat voznika (vsakih 30 s): hitrost, preostanek poti, starost */
  function hbHtml(eid) {
    const h = P.hb[eid];
    if (!h) return "";
    const starost = AID.streznikZdaj() - h.ts;
    const deli = [`♥ ${starostBesedilo(starost)}`, h.hitrost_kmh != null ? `${Math.round(h.hitrost_kmh)} km/h` : "",
      h.preostalo_m != null ? `še ${(h.preostalo_m / 1000).toFixed(1)} km` : "", h.preostalo_s != null ? `~${Math.max(1, Math.round(h.preostalo_s / 60))} min` : ""];
    const pr = P.preusmeritve[eid];
    return `<span class="tip st-hb ${starost > 75 ? "izpad" : ""}" title="Heartbeat voznika (vsakih 30 s)">${esc(deli.filter(Boolean).join(" · "))}</span>`
      + (pr ? `<span class="tip st-preusmeritev">↪ nova pot: ${esc(pr.opis)}</span>` : "");
  }
  function opisVoznika(e) {
    const v = e.voznik;
    if (!v) return "";
    const deli = [v.prijavljen ? "voznik prijavljen" : "voznik ni prijavljen"];
    if (v.korak) deli.push(`${v.korak.naloga_id} ${v.korak.akcija}: ${FAZA[v.faza] || "dodeljeno"}`);
    if (v.obvestilo && v.obvestilo.stanje === "caka") {
      const ostane = Math.round(v.obvestilo.rok - AID.streznikZdaj());
      deli.push(ostane > 0 ? `čaka potrditev (${ostane} s)` : `NI POTRDIL (${-ostane} s čez rok)`);
    } else if (v.obvestilo && v.obvestilo.stanje === "potrjeno") deli.push("plan potrjen");
    return deli.join(" · ");
  }
  const opisSignala = (e) => { const s = signalEnote(e); return SIGNAL[s.signal] + (s.starost != null ? " · " + starostBesedilo(s.starost) : ""); };

  const ikonaEnote = (e) => {
    const s = signalEnote(e).signal;
    const zastarel = s === "ni_signala" || s === "neznano";
    return L.divIcon({
      className: "", iconSize: [34, 22], iconAnchor: [17, 11],
      html: `<div data-signal="${s}" style="background:${e.stanje === "okvara" ? "#8A1525" : "#1D2228"};color:#fff;font:700 10.5px Inter,sans-serif;border-radius:5px;padding:3px 4px;text-align:center;border:2px ${zastarel ? "dashed" : "solid"} #fff;box-shadow:0 1px 4px rgba(0,0,0,.4);white-space:nowrap;opacity:${zastarel ? 0.5 : 1}">${esc(e.id)}${zastarel ? " ?" : ""}</div>`,
    });
  };
  const markerji = {};
  const ikonaNaloge = (n) => L.divIcon({
    className: "", iconSize: [26, 26], iconAnchor: [13, 13],
    html: `<div style="width:26px;height:26px;border-radius:50%;background:${n.prioriteta <= 2 ? "#8A1525" : n.prioriteta === 3 ? "#5A6270" : "#8A919C"};color:#fff;font:800 11px Inter,sans-serif;display:grid;place-items:center;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)">P${n.prioriteta}</div>`,
  });

  const geometrije = new Map(), zahtevano = new Set();
  const kljucOdseka = (a, b, tip) => `${a[0].toFixed(5)},${a[1].toFixed(5)}>${b[0].toFixed(5)},${b[1].toFixed(5)}|${tip}`;
  async function naloziGeometrije(odseki) {
    const edinstveni = [...new Map(odseki.map((o) => [o.kl, o])).values()].slice(0, 80);
    edinstveni.forEach((o) => zahtevano.add(o.kl));
    try {
      const r = await AID.api("/poti", { metoda: "POST", telo: { odseki: edinstveni.map(({ od, do: d, tip }) => ({ od, do: d, tip })) } });
      let ponovi = false;
      r.forEach((x, i) => {
        if (x.kakovost !== "ocena" && x.geometrija && x.geometrija.length >= 2) geometrije.set(edinstveni[i].kl, x.geometrija);
        else ponovi = true; // pot po cestah trenutno ni na voljo – ravna črta ni končna
      });
      if (ponovi) setTimeout(() => { edinstveni.forEach((o) => { if (!geometrije.has(o.kl)) zahtevano.delete(o.kl); }); izrisiKarto(); }, 20000);
      izrisiKarto();
    } catch (_) {
      edinstveni.forEach((o) => zahtevano.delete(o.kl)); // poskusi ob naslednjem izrisu
    }
  }

  function izrisiKarto() {
    if (!karta) return;
    Object.values(sloji).forEach((s) => s.clearLayers());
    P.centri.forEach((c) => L.circleMarker([c.lat, c.lon], { radius: 9, color: "#1D2228", weight: 4, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(`${esc(c.ime)} · prosto ${c.sprejemna_mesta - c.zasedena}/${c.sprejemna_mesta}`).addTo(sloji.centri));
    P.naloge.filter((n) => n.stanje === "odprta").forEach((n) => {
      L.marker([n.prevzem.lat, n.prevzem.lon], { icon: ikonaNaloge(n) }).bindTooltip(`${esc(n.id)} · ${esc(n.opis || n.tip)}<br>${esc(n.prevzem.naslov || "")}`).addTo(sloji.naloge);
    });
    P.enote.forEach((e) => {
      markerji[e.id] = L.marker([e.lat, e.lon], { icon: ikonaEnote(e), zIndexOffset: 1000 })
        .bindTooltip(() => `${esc(e.ime)} · ${STANJE[e.stanje] || e.stanje}<br>${esc(opisSignala(P.enote.find((x) => x.id === e.id) || e))}`).addTo(sloji.enote);
    });
    const manjkajo = [];
    const poti = (plan, sloj, slog) => {
      if (!plan) return;
      Object.entries(plan.koraki_po_enoti).forEach(([eid, ks]) => {
        const e = P.enote.find((x) => x.id === eid);
        if (!e || !ks.length) return;
        // odseki po cestah (predpomnjeni); dokler geometrija ne pride, ravna črta
        let prej = [e.lat, e.lon];
        const tocke = [];
        ks.forEach((k) => {
          const kl = kljucOdseka(prej, [k.lat, k.lon], e.tip);
          const g = geometrije.get(kl);
          if (g) tocke.push(...g);
          else {
            tocke.push(prej, [k.lat, k.lon]);
            // helikopter res leti po zraku – zanj ne iščemo poti po cestah
            if (e.tip !== "helikopter" && !zahtevano.has(kl)) manjkajo.push({ kl, od: prej, do: [k.lat, k.lon], tip: e.tip });
          }
          prej = [k.lat, k.lon];
        });
        L.polyline(tocke, e.tip === "helikopter" ? { ...slog, weight: 2.5, dashArray: "2 7" } : slog)
          .bindTooltip(e.tip === "helikopter" ? `${esc(e.id)} · zračna pot` : esc(e.id), { sticky: true }).addTo(sloj);
      });
    };
    poti(P.aktiven, sloji.aktiven, { color: "#1D2228", weight: 3, opacity: 0.55 });
    if (P.prikazan) poti(P.prikazan, sloji.predlog, { color: "#8A1525", weight: 4, dashArray: "8 6" });
    if (manjkajo.length) naloziGeometrije(manjkajo);
    Object.values(P.preusmeritve).forEach((r) => {
      if (r.geometrija && r.geometrija.length > 1) L.polyline(r.geometrija, { color: "#1A7F37", weight: 4, dashArray: "2 8", opacity: 0.9 })
        .bindTooltip(`${esc(r.enota_id)} · nova pot (${esc(r.razlog)}): ${esc(r.opis)}`, { sticky: true }).addTo(sloji.preusmeritve);
    });
    (AID.ovire ? AID.ovire.ovire : []).forEach((o) => L.circle([o.lat, o.lon], { radius: o.polmer_m, color: "#8A1525", weight: 2, fillColor: "#8A1525", fillOpacity: 0.3 })
      .bindTooltip(`⛔ ${esc(o.vrsta)}${o.opis ? " – " + esc(o.opis) : ""}`).addTo(sloji.enote));
    if (!izrisiKarto.prilagojeno) {
      const tocke = [...P.enote.filter((e) => e.tip !== "helikopter").map((e) => [e.lat, e.lon]),
        ...P.naloge.filter((n) => n.stanje === "odprta").map((n) => [n.prevzem.lat, n.prevzem.lon])];
      if (tocke.length) { karta.fitBounds(tocke, { padding: [30, 30] }); izrisiKarto.prilagojeno = true; }
    }
  }

  /* ---------- enote ---------- */
  function izrisiEnote() {
    const zdaj = Date.now() / 1000;
    $("seznam-enot").innerHTML = P.enote.map((e) => {
      const zamik = (e.na_voljo_od || 0) > zdaj ? `<span class="st-enote zamik">čez ${minut(e.na_voljo_od - zdaj)} min</span>` : "";
      const sig = signalEnote(e);
      return `<li data-enota="${esc(e.id)}"><span class="ime">${esc(e.id)}</span>${zamik || `<span class="st-enote ${e.stanje}">${STANJE[e.stanje] || e.stanje}</span>`}
        <span class="tip">${TIP[e.tip] || e.tip}${e.tovor && e.tovor.length ? " · tovor: " + e.tovor.map((t) => esc(t.naloga_id)).join(", ") : ""}</span>
        <span class="st-signal ${sig.signal}" data-signal-enote="${esc(e.id)}">${esc(opisSignala(e))}</span>
        <span class="tip st-voznik">${esc(opisVoznika(e))}</span>${hbHtml(e.id)}</li>`;
    }).join("");
  }
  $("seznam-enot").onclick = (e) => {
    const li = e.target.closest("[data-enota]");
    const en = li && P.enote.find((x) => x.id === li.dataset.enota);
    if (en && karta) karta.setView([en.lat, en.lon], 14);
  };

  /* ---------- predlog plana: prej / potem ---------- */
  function izrisiPredlog() {
    const p = P.prikazan;
    const gumbi = ["plan-potrdi", "plan-zavrni", "plan-alternativa"];
    $("znacka-plan").classList.toggle("skrito", !P.predlog);
    if (!p) {
      $("plan-oznaka").textContent = "—";
      $("plan-predlog-telo").innerHTML = `<p class="prazno-besedilo">Ni novega predloga. Aktivni plan${P.aktiven ? ` (v${P.aktiven.verzija}, potrdil ${esc(P.aktiven.potrdil || "")})` : ""} je prikazan na karti in v časovnici.</p>`;
      gumbi.forEach((g) => ($(g).disabled = true));
      return;
    }
    const pr = p.primerjava;
    $("plan-oznaka").textContent = p.oznaka + (P.alternative.length > 1 ? ` (${P.altIndeks + 1}/${P.alternative.length})` : "");
    const vrsticaZamude = Object.entries(pr.zamude_po_prioriteti_s).map(([prio, v]) =>
      `<tr><td>P${prio}</td><td class="st">${minut(v.prej)} min</td><td class="st ${v.potem > v.prej ? "slabse" : ""}">${minut(v.potem)} min</td></tr>`).join("");
    $("plan-predlog-telo").innerHTML = `
      <div class="plan-razlog">Razlog: ${esc(p.razlog || "")} · izračun ${p.cas_racunanja_s ?? "?"} s</div>
      <div class="razlaga">${esc(pr.razlaga)}</div>
      <table class="prej-potem"><thead><tr><th>Zamude</th><th>Prej</th><th>Potem</th></tr></thead><tbody>${vrsticaZamude}
        <tr><td>Dodatni km</td><td class="st">—</td><td class="st ${pr.dodatni_km > 0 ? "slabse" : ""}">${pr.dodatni_km > 0 ? "+" : ""}${pr.dodatni_km} km</td></tr>
        <tr><td>Nepokrite naloge</td><td class="st">—</td><td class="st ${pr.nepokrite.length ? "slabse" : ""}">${pr.nepokrite.length ? esc(pr.nepokrite.join(", ")) : "0"}</td></tr></tbody></table>
      <h3 class="plan-h3">Spremenjene dodelitve (${pr.spremenjene_dodelitve.length})</h3>
      <ul class="spremembe">${pr.spremenjene_dodelitve.map((s) => {
        const n = naloga(s.naloga_id);
        return `<li><b>${esc(s.naloga_id)}</b> <span class="opis-naloge">${esc(((n && (n.opis || n.tip)) || "").slice(0, 48))}</span>
          <span style="margin-left:auto">${s.prej ? `<span class="iz">${esc(s.prej)}</span> → ` : ""}<span class="v">${esc(s.potem || s.opomba || "—")}</span></span></li>`;
      }).join("") || "<li>Brez sprememb.</li>"}</ul>
      ${p.opombe && p.opombe.length ? `<h3 class="plan-h3">Opombe</h3><ul>${p.opombe.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}`;
    $("plan-potrdi").disabled = false;
    $("plan-zavrni").disabled = false;
    $("plan-alternativa").disabled = P.alternative.length < 2;
  }

  /* ---------- časovnica po enotah ---------- */
  function izrisiCasovnico() {
    const plan = P.prikazan || P.aktiven;
    $("casovnica-naslov").textContent = P.prikazan ? "predlog: " + P.prikazan.oznaka : P.aktiven ? `aktivni plan v${P.aktiven.verzija}` : "ni plana";
    if (!plan) { $("casovnica").innerHTML = ""; return; }
    const zdaj = Date.now() / 1000;
    const konec = Math.max(zdaj + 3600, ...Object.values(plan.koraki_po_enoti).flat().map((k) => k.eta + 600));
    const obseg = konec - zdaj;
    const x = (t) => Math.max(0, Math.min(100, ((t - zdaj) / obseg) * 100));
    const spremenjene = new Set((P.prikazan?.primerjava?.spremenjene_dodelitve || []).map((s) => s.naloga_id));
    let os = "";
    for (let m = 0; m <= obseg / 60; m += obseg > 4 * 3600 ? 60 : 30) os += `<span style="left:${x(zdaj + m * 60)}%">+${m >= 60 ? Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) : "") : m + " min"}</span>`;
    const vrstice = P.enote.map((e) => {
      const ks = plan.koraki_po_enoti[e.id] || [];
      const bloki = ks.map((k) => {
        const n = naloga(k.naloga_id);
        const dolzina = Math.max(0.8, (300 / obseg) * 100);
        const opis = `${k.naloga_id} · ${k.nujna ? "prihod" : k.akcija} ob ${AID.uraKratko(new Date(k.eta * 1000))}${k.zamuda_s ? ` · zamuda ${minut(k.zamuda_s)} min` : ""}${n ? " · " + (n.opis || "") : ""}`;
        return `<div class="cv-korak p${k.prioriteta} ${k.akcija}${k.zamuda_s ? " zamuda" : ""}${spremenjene.has(k.naloga_id) ? " spremenjeno" : ""}"
          style="left:${x(k.eta)}%;width:${dolzina}%" title="${esc(opis)}">${esc(k.naloga_id.replace(/^N-/, ""))}${k.akcija === "dostava" ? "↓" : ""}</div>`;
      }).join("");
      return `<div class="cv-vrsta"><div class="cv-ime">${esc(e.id)}<small>${STANJE[e.stanje] || e.stanje}</small></div><div class="cv-trak">${bloki}</div></div>`;
    }).join("");
    $("casovnica").innerHTML = `<div class="cv-os">${os}</div>${vrstice}`;
  }

  function izrisiVse() { izrisiEnote(); izrisiKarto(); izrisiPredlog(); izrisiCasovnico(); }

  /* ---------- dejanja ---------- */
  async function nastaviPredlog(p) {
    P.predlog = p;
    P.alternative = p ? [p] : [];
    P.altIndeks = 0;
    P.prikazan = p;
    if (p && p.alternative && p.alternative.length) {
      try { P.alternative = [p, ...(await AID.api(`/plan/${p.id}/alternative`))]; } catch (_) {}
    }
    izrisiVse();
  }
  $("plan-alternativa").onclick = () => {
    P.altIndeks = (P.altIndeks + 1) % P.alternative.length;
    P.prikazan = P.alternative[P.altIndeks];
    izrisiVse();
  };
  $("plan-potrdi").onclick = async () => {
    const p = P.prikazan;
    if (!p) return;
    const ok = await AID.modal({ naslov: `Potrdi plan »${p.oznaka}«?`, html: `<p>${esc(p.primerjava.razlaga)}</p>`,
      gumbi: [{ besedilo: "Nazaj", vrednost: false }, { besedilo: "POTRDI PLAN", vrednost: true, razred: "rdec" }] });
    if (!ok) return;
    try {
      P.aktiven = await AID.api(`/plan/${p.id}/potrdi`, { metoda: "POST" });
      P.enote = await AID.api("/enote");
      await nastaviPredlog(null);
      AID.obvestilo("Plan potrjen", `Aktiven je plan v${P.aktiven.verzija}.`);
    } catch (e) { napaka(e); }
  };
  $("plan-zavrni").onclick = async () => {
    const p = P.prikazan;
    if (!p) return;
    const razlog = await AID.modal({ naslov: "Zavrni predlog plana?", html: `<label style="display:block">Razlog (neobvezno)<input id="razlog-plana" style="width:100%;margin-top:6px;padding:8px;border:1px solid var(--rob);border-radius:8px"></label>`,
      gumbi: [{ besedilo: "Nazaj", vrednost: null }, { besedilo: "Zavrni", vrednost: "da", razred: "rdec-obroba" }] });
    if (!razlog) return;
    try {
      await AID.api(`/plan/${P.predlog.id}/zavrni`, { metoda: "POST", telo: { razlog: document.getElementById("razlog-plana")?.value || null } });
      await nastaviPredlog(null);
    } catch (e) { napaka(e); }
  };

  /* ---------- ročni dogodek ---------- */
  function poljaDogodka() {
    const tip = $("dogodek-tip").value;
    const enote = (filter) => P.enote.filter(filter).map((e) => `<option>${esc(e.id)}</option>`).join("");
    const h = {
      okvara_vozila: `<label>Vozilo<select name="enota_id">${enote((e) => e.tip !== "helikopter" && e.stanje !== "okvara")}</select></label>
        <label class="vrstica"><input type="checkbox" name="tovor_ostane" checked> Tovor ostane v vozilu</label>`,
      megla: `<label>Helikopter<select name="enota_id">${enote((e) => e.tip === "helikopter")}</select></label>
        <label>Zamik (min)<input type="number" name="zamik_min" value="60" min="5" max="600"></label>`,
      manjsa_zmogljivost_centra: `<label>Center<select name="center_id">${P.centri.map((c) => `<option value="${esc(c.id)}">${esc(c.ime)}</option>`).join("")}</select></label>
        <label>Nova kapaciteta<input type="number" name="nova_kapaciteta" value="2" min="0"></label>`,
      sprememba_prioritete: `<label>Naloga<select name="naloga_id">${P.naloge.filter((n) => n.stanje === "odprta").map((n) => `<option value="${esc(n.id)}">${esc(n.id)} · P${n.prioriteta} · ${esc(n.opis || "")}</option>`).join("")}</select></label>
        <label>Nova prioriteta<select name="nova_prioriteta">${[1, 2, 3, 4, 5].map((x) => `<option>${x}</option>`).join("")}</select></label>`,
    };
    $("dogodek-polja").innerHTML = h[tip];
  }
  $("dogodek-tip").onchange = poljaDogodka;
  $("obrazec-dogodka").onsubmit = async (e) => {
    e.preventDefault();
    const parametri = {};
    $("dogodek-polja").querySelectorAll("[name]").forEach((el) => {
      parametri[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" || el.name === "nova_prioriteta" ? Number(el.value) : el.value;
    });
    const gumb = e.submitter; gumb.disabled = true; gumb.textContent = "Planer računa …";
    try {
      const r = await AID.api("/dogodek", { metoda: "POST", telo: { tip: $("dogodek-tip").value, parametri } });
      AID.obvestilo("Dogodek vnesen", r.dogodek.opis || "");
      await osvezi();
      if (r.plan_predlog) await nastaviPredlog(r.plan_predlog);
    } catch (e2) { napaka(e2); }
    gumb.disabled = false; gumb.textContent = "Vnesi in predlagaj nov plan";
  };

  /* ---------- podatki ---------- */
  async function osvezi() {
    try {
      [P.enote, P.naloge, P.centri, P.aktiven] = await Promise.all([AID.api("/enote"), AID.api("/naloge"), AID.api("/centri"), AID.api("/plan/aktiven")]);
      AID.api("/heartbeat").then((v) => { v.forEach((z) => { P.hb[z.enota_id] = { enota_id: z.enota_id, ts: z.ts, hitrost_kmh: z.hitrost_kmh,
        preostalo_m: z.pot.preostalo_m, preostalo_s: z.pot.preostalo_s }; }); izrisiEnote(); }).catch(() => {});
      AID.pisi("aid_plan", { enote: P.enote, naloge: P.naloge, centri: P.centri, aktiven: P.aktiven });
    } catch (_) {
      Object.assign(P, AID.beri("aid_plan", {})); // zadnje znano stanje
    }
    poljaDogodka();
    izrisiVse();
  }

  AID.na("plan_predlog", async (p) => {
    try { P.naloge = await AID.api("/naloge"); } catch (_) {}
    await nastaviPredlog(p);
    AID.obvestilo("Nov predlog plana", p.primerjava.razlaga, true, 7000);
  });
  AID.na("plan_aktiven", (p) => { P.aktiven = p; if (P.predlog && (P.predlog.id === p.id || P.alternative.some((a) => a.id === p.id))) nastaviPredlog(null); else izrisiVse(); });
  AID.na("plan_zavrnjen", (p) => { if (P.predlog && P.predlog.id === p.id) nastaviPredlog(null); });
  AID.na("enote", (e) => { P.enote = e; izrisiVse(); });
  AID.na("heartbeat", (h) => { P.hb[h.enota_id] = h; izrisiEnote(); });
  AID.na("preusmeritev", (r) => { P.preusmeritve[r.enota_id] = r; izrisiVse(); });
  AID.na("voznik_stanje", (x) => { if (x && x.enota_id && P.preusmeritve[x.enota_id] && x.stanje !== "na_poti") { delete P.preusmeritve[x.enota_id]; izrisiVse(); } });
  setInterval(() => { if (Object.keys(P.hb).length) izrisiEnote(); }, 5000); // starost heartbeata
  (AID.naOvire = AID.naOvire || []).push(() => izrisiKarto());
  /* sporočila voznikov: stanja, potrditve, obvestila -> sveže enote/naloge */
  const osveziEnote = () => AID.api("/enote").then((e) => { P.enote = e; izrisiVse(); }).catch(() => {});
  AID.na("voznik_potrditev", osveziEnote);
  AID.na("voznik_obvestilo", osveziEnote);
  AID.na("voznik_prijava", osveziEnote);
  AID.na("naloge", (n) => { P.naloge = n; izrisiVse(); });
  AID.na("centri", (c) => { P.centri = c; izrisiVse(); });

  /* premik enote: marker se premakne brez ponovnega izrisa celotne karte */
  function premakni(m) {
    const e = P.enote.find((x) => x.id === m.enota_id);
    if (!e) return;
    const { enota_id, lat, lon, ...polozaj } = m;
    e.lat = lat; e.lon = lon; e.polozaj = polozaj;
    const mk = markerji[enota_id];
    if (mk) { mk.setLatLng([lat, lon]); mk.setIcon(ikonaEnote(e)); }
  }
  AID.na("enota_premik", (m) => { premakni(m); osveziSignale(); });
  AID.na("polozaji", (seznam) => { seznam.forEach(premakni); osveziSignale(); });
  AID.na("enota_signal", (s) => {
    const e = P.enote.find((x) => x.id === s.enota_id);
    if (e && e.polozaj) { e.polozaj.signal = s.signal; osveziSignale(); }
  });
  /* starost položaja teče vsako sekundo (lokalno, v času strežnika) */
  function osveziSignale() {
    P.enote.forEach((e) => {
      const el = document.querySelector(`[data-signal-enote="${CSS.escape(e.id)}"]`);
      if (el) {
        el.textContent = opisSignala(e); el.className = "st-signal " + signalEnote(e).signal;
        const v = el.parentElement && el.parentElement.querySelector(".st-voznik");
        if (v) v.textContent = opisVoznika(e);
      }
      const mk = markerji[e.id];
      const s = signalEnote(e).signal;
      if (mk && mk._signal !== s) { mk._signal = s; mk.setIcon(ikonaEnote(e)); }
    });
  }
  setInterval(osveziSignale, 1000);
  AID.na("dogodek", () => AID.api("/naloge").then((n) => { P.naloge = n; izrisiVse(); }).catch(() => {}));
  AID.na("pogled", (ime) => { if (ime === "plan") { zacniKarto(); setTimeout(() => { karta && karta.invalidateSize(); izrisiVse(); }, 50); } });
  setInterval(() => { if (!$("pogled-plan").classList.contains("skrito")) { izrisiEnote(); izrisiCasovnico(); } }, 30000);

  document.addEventListener("DOMContentLoaded", async () => {
    await AID.prijava;
    if (!$("pogled-plan").classList.contains("skrito")) zacniKarto();
    await osvezi();
    try { const p = await AID.api("/plan/predlog"); if (p) await nastaviPredlog(p); } catch (_) {}
  });
})();
