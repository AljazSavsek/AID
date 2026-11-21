/* AID – pogled klica v regijskem centru.
 * Podatki prihajajo s strežnika (AI poslušalec, geokodiranje, usmerjanje).
 * Vmesnik nikoli ne potrdi ničesar sam: vsak gumb je dejanje dispečerja.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);

  const POLJA = [
    { polje: "vrsta_dogodka", oznaka: "Vrsta dogodka", tip: "enum" },
    { polje: "opis", oznaka: "Opis", tip: "besedilo", vec: true },
    { polje: "naslov", oznaka: "Naslov", tip: "besedilo", obvezno: true },
    { polje: "orientacija", oznaka: "Orientacija", tip: "besedilo" },
    { polje: "lokacija", oznaka: "Lokacija (koordinate)", tip: "lokacija", obvezno: true },
    { polje: "stanje_pacienta", oznaka: "Stanje pacienta", tip: "besedilo", vec: true },
    { polje: "stevilo_poskodovanih", oznaka: "Število poškodovanih", tip: "stevilo" },
    { polje: "prioriteta", oznaka: "Prioriteta", tip: "prioriteta", obvezno: true, kljucno: true },
    { polje: "potrebna_oprema", oznaka: "Potrebna oprema", tip: "seznam" },
    { polje: "predlagana_enota", oznaka: "Predlagana enota", tip: "enota", obvezno: true, kljucno: true },
    { polje: "kontakt", oznaka: "Kontakt klicatelja", tip: "besedilo" },
  ];
  const KLJUCNA = ["prioriteta", "predlagana_enota"];
  const VRSTE = { prometna_nesreca: "Prometna nesreča", padec: "Padec", bolecina_v_prsih: "Bolečina v prsih", nezavest: "Nezavest", porod: "Porod", drugo: "Drugo" };
  const PRIORITETE = { 1: "1 – življenjsko ogrožen", 2: "2 – zelo nujno", 3: "3 – nujno", 4: "4 – manj nujno", 5: "5 – ni nujno" };
  const DOGODKI = { okvara_vozila: "Okvara vozila", megla: "Megla", nujna_naloga: "Nujna naloga", manjsa_zmogljivost_centra: "Manjša zmogljivost centra", sprememba_prioritete: "Sprememba prioritete" };
  const POTRJENA = ["sprejeto", "spremenjeno"];

  const S = {
    klic: null, segmenti: [], predlogi: new Map(), vprasanja: [], enote: [], cakajoci: [],
    urejanje: {}, zavracanje: {}, urediVse: false, aiNaVoljo: true, poteka: false,
  };
  AID.stanjeKlica = S;

  const enako = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const predlogiPolja = (polje) => [...S.predlogi.values()].filter((p) => p.polje === polje);
  const potrjenZa = (polje) => predlogiPolja(polje).find((p) => POTRJENA.includes(p.stanje));
  const odprtiZa = (polje) => predlogiPolja(polje).filter((p) => p.stanje === "predlagano").sort((a, b) => (b.zaupanje || 0) - (a.zaupanje || 0));
  const oznakaPolja = (polje) => (POLJA.find((x) => x.polje === polje) || {}).oznaka || (polje === "zaznani_dogodek_plana" ? "Dogodek plana" : polje);
  const napaka = (e) => AID.obvestilo("Dejanje ni uspelo", e.message, true, 6000);

  /* ================= čakalna vrsta in telefon ================= */
  function izrisiVrsto() {
    $("st-cakajocih").textContent = S.cakajoci.length;
    const zdaj = Date.now() / 1000;
    const vrstice = [];
    if (S.klic) vrstice.push(`<li class="aktiven"><span class="stevilka">${esc(S.klic.stevilka || "neznana številka")}</span><span class="cakanje">V TEKU</span><span class="podatek">${esc(S.klic.omrezje || "")}</span><span class="podatek"></span></li>`);
    S.cakajoci.forEach((k, i) => vrstice.push(`<li class="${!S.klic && i === 0 ? "naslednji" : ""}"><span class="stevilka">${esc(k.stevilka)}</span>
      <span class="cakanje">${AID.trajanje(zdaj - k.prispel)}</span><span class="podatek">${esc(k.omrezje || "")}</span><span class="podatek">čaka</span></li>`));
    $("vrsta-klicev").innerHTML = vrstice.join("") || `<li><span class="podatek">Ni čakajočih klicev.</span></li>`;
  }
  setInterval(izrisiVrsto, 1000);

  const val = $("valovanje");
  for (let i = 0; i < 28; i++) val.appendChild(document.createElement("i"));
  let govori = 0;
  setInterval(() => { [...val.children].forEach((b) => (b.style.height = (Date.now() < govori ? 4 + Math.random() * 22 : 4 + Math.random() * 3) + "px")); }, 120);
  setInterval(() => { if (S.klic && S.klic.stanje === "v_teku") $("tel-cas").textContent = AID.trajanje(Date.now() / 1000 - S.klic.zacetek); }, 500);

  function izrisiTelefon() {
    const v = S.klic && S.klic.stanje === "v_teku";
    $("telefon").classList.toggle("v-teku", !!v);
    $("tel-stanje").textContent = v ? "V teku" : S.klic ? "Zaključen" : "Prosto";
    $("tel-stevilka").textContent = S.klic ? S.klic.stevilka || "—" : "—";
    $("tel-omrezje").textContent = S.klic ? (S.klic.omrezje || "Klic v teku") : "Ni aktivnega klica";
    ["tel-utisaj", "tel-zadrzi"].forEach((id) => ($(id).disabled = !v));
    $("tel-glavni").textContent = v ? "KONČAJ KLIC" : S.cakajoci.length ? "SPREJMI KLIC" : "NI ČAKAJOČIH";
    $("tel-glavni").disabled = !v && !S.cakajoci.length;
    $("v-zivo").classList.toggle("skrito", !v);
  }

  $("tel-glavni").onclick = async () => {
    try {
      if (S.klic && S.klic.stanje === "v_teku") {
        const k = await AID.api(`/klic/${S.klic.id}/koncaj`, { metoda: "POST" });
        S.klic = { ...S.klic, ...k };
        izrisiTelefon();
        return;
      }
      const q = S.cakajoci[0];
      if (!q) return;
      const k = q.demo
        ? await AID.api("/demo/klic", { metoda: "POST", telo: { hitrost: Number($("hitrost").value) } })
        : await AID.api("/klic/zacni", { metoda: "POST", telo: { cakajoci_id: q.id } });
      nastaviKlic({ ...k, omrezje: q.omrezje, segmenti: [], predlogi: [] });
      S.cakajoci = await AID.api("/cakajoci");
      izrisiVrsto(); izrisiTelefon();
    } catch (e) { napaka(e); }
  };
  ["tel-utisaj", "tel-zadrzi"].forEach((id) => ($(id).onclick = (e) => e.currentTarget.classList.toggle("vklopljen")));

  function nastaviKlic(k) {
    S.klic = k;
    S.segmenti = []; S.predlogi = new Map(); S.vprasanja = k.vprasanja || [];
    S.urejanje = {}; S.zavracanje = {}; S.urediVse = false;
    $("uredi-vse").textContent = "UREDI PODATKE";
    $("obrazec").classList.toggle("poslano", !!k.naloga_id);
    $("prepis").innerHTML = "";
    (k.segmenti || []).forEach((s) => dodajSegment(s, false));
    (k.predlogi || []).forEach((p) => S.predlogi.set(p.id, p));
    AID.pisi("aid_klic", k.id);
    izrisiTelefon(); izrisiVse(); izrisiVrsto();
  }

  /* ================= prepis ================= */
  function dodajSegment(seg, animiraj) {
    if (S.segmenti.some((s) => s.id === seg.id)) return;
    S.segmenti.push(seg);
    $("prepis-prazno")?.remove();
    const el = document.createElement("div");
    el.className = `segment ${seg.govorec}${seg.rocni ? " rocni" : ""}`;
    el.id = "seg-" + seg.id;
    el.innerHTML = `<div class="cas">${AID.trajanje(seg.od_s)}</div><div><div class="govorec">${seg.govorec === "klicatelj" ? "Klicatelj" : "Dispečer"}</div><div class="besedilo"></div></div>`;
    $("prepis").appendChild(el);
    const b = el.querySelector(".besedilo");
    if (animiraj && !seg.rocni) {
      // prikaz kot pri pretočnem prepisu: besede se izpisujejo sproti
      const besede = seg.besedilo.split(" ");
      let n = 0;
      el.classList.add("delni");
      govori = Date.now() + besede.length * 90;
      $("telefon").classList.toggle("govori-klicatelj", seg.govorec === "klicatelj");
      const t = setInterval(() => {
        b.textContent = besede.slice(0, ++n).join(" ");
        skrolaj();
        if (n >= besede.length) { clearInterval(t); el.classList.remove("delni"); oznaciCitate(); }
      }, 80);
      if (seg.govorec === "klicatelj" && S.aiNaVoljo) aiAnalizira();
    } else {
      b.textContent = seg.besedilo;
    }
    skrolaj();
  }
  const skrolaj = () => { const p = $("prepis"); p.scrollTop = p.scrollHeight; };
  let aiTimer = null;
  function aiAnalizira() { $("ai-analizira").classList.remove("skrito"); clearTimeout(aiTimer); aiTimer = setTimeout(() => $("ai-analizira").classList.add("skrito"), 3000); }

  $("rocni-zapis").onsubmit = async (e) => {
    e.preventDefault();
    const t = $("rocni-vnos").value.trim();
    if (!t) return;
    if (!S.klic || S.klic.stanje !== "v_teku") { AID.obvestilo("Ni aktivnega klica", "Najprej sprejmite klic.", true); return; }
    try {
      await AID.api(`/klic/${S.klic.id}/segment`, { metoda: "POST", telo: { govorec: $("rocni-govorec").value, besedilo: t, rocni: true } });
      $("rocni-vnos").value = "";
    } catch (e2) { napaka(e2); }
  };

  function oznaciCitate() {
    S.segmenti.forEach((seg) => {
      const el = $("seg-" + seg.id);
      if (!el || el.classList.contains("delni")) return;
      const citati = [];
      S.predlogi.forEach((p) => { if (p.stanje !== "zastarelo" && p.stanje !== "zavrnjeno") (p.dokazi || []).forEach((d) => { if (d.segment_id === seg.id) citati.push({ c: d.citat, p: p.polje }); }); });
      const obseg = [];
      const nizko = seg.besedilo.toLowerCase();
      citati.sort((a, b) => b.c.length - a.c.length).forEach(({ c, p }) => {
        const i = nizko.indexOf(c.toLowerCase());
        if (i < 0 || obseg.some(([a, b]) => i < b && i + c.length > a)) return;
        obseg.push([i, i + c.length, p]);
      });
      obseg.sort((a, b) => a[0] - b[0]);
      let out = "", zadnji = 0;
      obseg.forEach(([a, b, p]) => { out += esc(seg.besedilo.slice(zadnji, a)) + `<mark data-polje="${p}" title="${esc(oznakaPolja(p))}">${esc(seg.besedilo.slice(a, b))}</mark>`; zadnji = b; });
      el.querySelector(".besedilo").innerHTML = out + esc(seg.besedilo.slice(zadnji));
    });
  }
  function skociNaSegment(id) {
    const el = $("seg-" + id);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("zasvetljeno");
    setTimeout(() => el.classList.remove("zasvetljeno"), 1600);
  }
  $("prepis").addEventListener("click", (e) => {
    const m = e.target.closest("mark[data-polje]");
    const k = m && document.querySelector(`.kartica[data-polje="${m.dataset.polje}"]`);
    if (k) { k.scrollIntoView({ behavior: "smooth", block: "center" }); k.focus({ preventScroll: true }); }
  });

  /* ================= kartice predlogov ================= */
  function fmt(polje, v) {
    if (v == null || v === "") return "";
    switch (polje) {
      case "vrsta_dogodka": return VRSTE[v] || v;
      case "prioriteta": return PRIORITETE[v] || String(v);
      case "potrebna_oprema": return Array.isArray(v) ? v.join(", ") : v;
      case "lokacija": return Number(v.lat).toFixed(5) + ", " + Number(v.lon).toFixed(5);
      case "predlagana_enota": { const e = S.enote.find((x) => x.id === v); return e ? `${e.id} · ${e.ime}` : v; }
      case "zaznani_dogodek_plana": {
        const p = v.parametri || {};
        const deli = [DOGODKI[v.tip] || v.tip];
        if (v.tip === "okvara_vozila") deli.push(p.enota_id ? `vozilo ${p.enota_id}` : "vozilo: manjka", p.tovor_ostane ? "tovor ostane v vozilu" : "");
        if (v.tip === "megla") deli.push(p.enota_id || "helikopter", `+${p.zamik_min || 60} min`);
        return deli.filter(Boolean).join(" · ");
      }
      default: return String(v);
    }
  }
  function dokaziHtml(p) {
    if (!p.dokazi || !p.dokazi.length) return "";
    return `<div class="dokazi">${p.dokazi.map((d) => {
      const s = S.segmenti.find((x) => x.id === d.segment_id);
      return `<button class="citat" data-seg="${esc(d.segment_id)}" title="Skok na prepis"><span class="c-cas">${s ? AID.trajanje(s.od_s) : "—"}</span>„${esc(d.citat)}“</button>`;
    }).join("")}</div>`;
  }
  function zaupanjeHtml(z) {
    if (z == null) return `<span class="znacka">ročni vnos</span>`;
    return `<span class="zaupanje ${z < 0.5 ? "nizko" : ""}" title="Zaupanje AI"><span class="trak"><i style="width:${Math.round(z * 100)}%"></i></span>${AID.odstotek(z)}</span>`;
  }
  function urejevalnikHtml(def, v) {
    const id = "ur-" + def.polje;
    switch (def.tip) {
      case "enum": return `<select id="${id}">${Object.entries(VRSTE).map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
      case "prioriteta": return `<select id="${id}"><option value="">– izberi –</option>${Object.entries(PRIORITETE).map(([k, l]) => `<option value="${k}" ${String(k) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
      case "stevilo": return `<input id="${id}" type="number" min="0" max="99" value="${v ?? ""}">`;
      case "seznam": return `<input id="${id}" value="${esc(Array.isArray(v) ? v.join(", ") : v || "")}"><span class="namig">Ločite z vejico.</span>`;
      case "lokacija": return `<input id="${id}" value="${v ? Number(v.lat).toFixed(5) + ", " + Number(v.lon).toFixed(5) : ""}" placeholder="46.07138, 14.48870"><span class="namig">Vpišite »lat, lon« ali kliknite na karto zgoraj.</span>`;
      case "enota": return `<select id="${id}">${S.enote.filter((e) => e.stanje !== "okvara").map((e) => `<option value="${e.id}" ${e.id === v ? "selected" : ""}>${e.id} · ${e.ime} (${e.stanje === "prosto" ? "prosto" : "na nalogi"})</option>`).join("")}</select>`;
      default: return def.vec ? `<textarea id="${id}">${esc(v || "")}</textarea>` : `<input id="${id}" value="${esc(v || "")}">`;
    }
  }
  function preberiUrejevalnik(def) {
    const el = $("ur-" + def.polje);
    if (!el) return undefined;
    const t = el.value.trim();
    if (t === "") return null;
    switch (def.tip) {
      case "prioriteta": case "stevilo": return Number(t);
      case "seznam": return t.split(",").map((x) => x.trim()).filter(Boolean);
      case "lokacija": { const m = t.match(/(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)/); return m ? { lat: Number(m[1]), lon: Number(m[2]) } : undefined; }
      default: return t;
    }
  }
  function enoteHtml(p, omogoceno) {
    let top = (p && p.dodatno && p.dodatno.enote) || [];
    const lok = potrjenZa("lokacija") || odprtiZa("lokacija")[0];
    if (!top.length && lok) { // zadnja rezerva: ocena v brskalniku
      top = S.enote.filter((e) => e.stanje !== "okvara").map((e) => ({ enota_id: e.id, tip: e.tip, stanje: e.stanje, ...AID.ocenaEta(e, lok.vrednost.lat, lok.vrednost.lon) }))
        .sort((a, b) => a.cas_s - b.cas_s).slice(0, 3);
    }
    if (!top.length) return "";
    const izbrana = p && p.vrednost;
    return `<table class="enote-tabela" aria-label="Najhitrejše enote"><tbody>${top.map((x) => {
      const kak = x.kakovost === "natančno" ? "natancno" : x.kakovost === "ocena" ? "ocena" : "";
      return `<tr class="${x.enota_id === izbrana ? "izbrana" : ""}"><td><b>${esc(x.enota_id)}</b> <span style="color:var(--besedilo-2)">${esc(x.stanje === "prosto" ? x.tip : x.tip + ", na nalogi")}</span></td>
        <td class="eta">${Math.max(1, Math.round(x.cas_s / 60))} min</td><td>${(x.razdalja_m / 1000).toFixed(1)} km</td>
        <td><span class="kakovost ${kak}">${esc(x.kakovost)}</span></td>
        <td>${omogoceno && x.enota_id !== izbrana ? `<button class="gumb majhen" data-izberi-enoto="${esc(x.enota_id)}">Izberi</button>` : ""}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function razlogHtml(p) {
    if (!S.zavracanje[p.id]) return "";
    return `<div class="razlog"><input data-razlog="${p.id}" placeholder="Razlog zavrnitve (neobvezno)"><button class="gumb majhen" data-akcija="potrdi-zavrnitev" data-id="${p.id}">Zavrni</button></div>`;
  }

  function karticaHtml(def) {
    const vsi = predlogiPolja(def.polje);
    const potrjen = vsi.find((p) => POTRJENA.includes(p.stanje));
    const odprti = odprtiZa(def.polje);
    const zgodovina = vsi.filter((p) => p.stanje === "zastarelo" || p.stanje === "zavrnjeno");
    const ureja = S.urediVse || S.urejanje[def.polje];
    const glavni = potrjen || odprti[0];
    let razred = "kartica" + (def.kljucno ? " kljucno" : ""), telo = "", gumbi = "";
    const zgHtml = zgodovina.length ? `<details class="zgodovina"><summary>Zgodovina (${zgodovina.length})</summary><ul>${zgodovina.map((p) =>
      `<li><s>${esc(fmt(def.polje, p.vrednost))}</s> · ${p.stanje}${p.razlog ? " – " + esc(p.razlog) : ""}</li>`).join("")}</ul></details>` : "";

    if (ureja) {
      razred += " potrjeno";
      telo = `<div class="urejevalnik">${urejevalnikHtml(def, glavni ? glavni.vrednost : null)}</div>`;
      if (!S.urediVse) gumbi = `<button class="gumb sprejmi" data-akcija="shrani">Shrani <kbd>⏎</kbd></button><button class="gumb" data-akcija="preklici">Prekliči <kbd>Esc</kbd></button>`;
    } else if (potrjen) {
      razred += " potrjeno";
      const prv = potrjen.stanje === "spremenjeno" && potrjen.prvotna_ai_vrednost != null && !enako(potrjen.prvotna_ai_vrednost, potrjen.vrednost)
        ? `<span class="prvotna">AI: ${esc(fmt(def.polje, potrjen.prvotna_ai_vrednost))}</span>` : "";
      telo = `<div class="vrednost">${esc(fmt(def.polje, potrjen.vrednost))}${prv}</div>${dokaziHtml(potrjen)}
        <div class="potrjeno-info">${potrjen.stanje === "sprejeto" ? "Sprejeto" : potrjen.predlagal === "dispecer" ? "Ročni vnos" : "Spremenjeno"} · ${esc(potrjen.potrdil)} · ${AID.uraZdaj(new Date(potrjen.potrjeno_ob * 1000))}</div>`;
      if (def.tip === "enota") telo += enoteHtml(potrjen, false);
      telo += odprti.map((p) => `<div class="konflikt"><div class="naslovek">⚠ AI zaznal drugačno vrednost · nasprotuje potrjenemu</div>
        <div class="vrednost">${esc(fmt(def.polje, p.vrednost))} ${zaupanjeHtml(p.zaupanje)}</div>${dokaziHtml(p)}
        <div class="k-gumbi"><button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi novo</button><button class="gumb" data-akcija="zavrni" data-id="${p.id}">Ohrani potrjeno</button></div>${razlogHtml(p)}</div>`).join("");
      gumbi = `<button class="gumb" data-akcija="uredi">Uredi <kbd>E</kbd></button>`;
    } else if (odprti.length) {
      const p = odprti[0];
      razred += (p.zaupanje ?? 1) < 0.5 ? " negotovo" : " predlagano";
      if (odprti.length > 1) {
        telo = `<p class="pojasnilo">Dvoumno – izberite pravo možnost:</p><div class="alternative">${odprti.map((a) => `<div class="alternativa"><div><div class="vrednost">${esc(fmt(def.polje, a.vrednost))}</div>${dokaziHtml(a)}</div>
          <div class="k-gumbi">${zaupanjeHtml(a.zaupanje)}<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${a.id}">Izberi</button></div></div>`).join("")}</div>`;
      } else {
        telo = `<div class="vrednost">${esc(fmt(def.polje, p.vrednost))}${(p.zaupanje ?? 1) < 0.5 ? ' <span class="znacka negotovo">negotovo</span>' : ""}</div>${dokaziHtml(p)}`;
      }
      if (def.tip === "enota") telo += enoteHtml(p, true);
      gumbi = `<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi <kbd>⏎</kbd></button>
        <button class="gumb" data-akcija="uredi">Uredi <kbd>E</kbd></button>
        <button class="gumb zavrni" data-akcija="zavrni" data-id="${p.id}">Zavrni <kbd>Del</kbd></button>`;
      telo += razlogHtml(p);
    } else {
      razred += " manjka";
      telo = `<div class="vrednost prazna">manjka</div>`;
      if (def.tip === "enota") telo += enoteHtml(null, false);
      gumbi = `<button class="gumb" data-akcija="uredi">+ Vnesi ročno <kbd>E</kbd></button>`;
    }
    const desno = potrjen ? `<span class="znacka temna">potrjeno</span>` : glavni ? `${zaupanjeHtml(glavni.zaupanje)}<span class="znacka">AI predlog</span>` : `<span class="znacka rdeca-bleda">manjka</span>`;
    return `<article class="${razred}" tabindex="0" data-polje="${def.polje}" aria-label="${esc(def.oznaka)}">
      <div class="k-glava"><span class="oznaka">${esc(def.oznaka)}</span><span class="desno">${desno}</span></div>
      ${telo}<div class="k-gumbi">${gumbi}</div>${zgHtml}</article>`;
  }

  /* zaznani dogodki plana iz klica (npr. »vozilo je obstalo«) */
  function izrisiDogodke() {
    const d = predlogiPolja("zaznani_dogodek_plana").filter((p) => p.stanje === "predlagano" || POTRJENA.includes(p.stanje));
    $("dogodki-klica").innerHTML = d.map((p) => {
      const manjka = p.vrednost.tip === "okvara_vozila" && !p.vrednost.parametri.enota_id;
      const ur = S.urejanje["dogodek-" + p.id];
      const telo = ur ? `<div class="urejevalnik"><label>Vozilo <select id="ur-dog-${p.id}">${S.enote.filter((e) => e.tip !== "helikopter").map((e) => `<option ${e.id === p.vrednost.parametri.enota_id ? "selected" : ""}>${e.id}</option>`).join("")}</select></label></div>`
        : `<div class="vrednost">${esc(fmt("zaznani_dogodek_plana", p.vrednost))}${manjka ? ' <span class="znacka negotovo">manjka vozilo</span>' : ""}</div>${dokaziHtml(p)}`;
      const gumbi = POTRJENA.includes(p.stanje) ? `<div class="potrjeno-info">Potrjeno · ${esc(p.potrdil)} – planer je predlagal nov plan</div>`
        : ur ? `<div class="k-gumbi"><button class="gumb sprejmi" data-dog-akcija="shrani" data-id="${p.id}">Potrdi z izbranim vozilom</button><button class="gumb" data-dog-akcija="preklici" data-id="${p.id}">Prekliči</button></div>`
          : `<div class="k-gumbi">${manjka ? "" : `<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi</button>`}
             <button class="gumb" data-dog-akcija="uredi" data-id="${p.id}">${manjka ? "Izberi vozilo" : "Uredi"}</button>
             <button class="gumb zavrni" data-akcija="zavrni" data-id="${p.id}">Zavrni</button></div>${razlogHtml(p)}`;
      return `<article class="kartica dogodek ${POTRJENA.includes(p.stanje) ? "potrjeno" : "negotovo"}" data-polje="zaznani_dogodek_plana">
        <div class="k-glava"><span class="oznaka">Zaznan dogodek plana</span><span class="desno">${zaupanjeHtml(p.zaupanje)}</span></div>${telo}${gumbi}</article>`;
    }).join("");
  }
  $("dogodki-klica").addEventListener("click", async (e) => {
    const c = e.target.closest(".citat"); if (c) { skociNaSegment(c.dataset.seg); return; }
    const b = e.target.closest("[data-dog-akcija]");
    if (b) {
      const p = S.predlogi.get(b.dataset.id);
      if (b.dataset.dogAkcija === "uredi") S.urejanje["dogodek-" + p.id] = true;
      else if (b.dataset.dogAkcija === "preklici") delete S.urejanje["dogodek-" + p.id];
      else {
        const v = { ...p.vrednost, parametri: { ...p.vrednost.parametri, enota_id: $("ur-dog-" + p.id).value } };
        try { upsert(await AID.api(`/predlog/${p.id}/spremeni`, { metoda: "POST", telo: { vrednost: v } })); delete S.urejanje["dogodek-" + p.id]; AID.obvestilo("Dogodek potrjen", "Planer pripravlja nov plan – potrdite ga v pogledu »Plan in karta«."); }
        catch (e2) { napaka(e2); }
      }
      izrisiDogodke();
      return;
    }
    obdelajAkcijo(e);
  });

  function izrisiVse(fokus) {
    const aktivni = document.activeElement && document.activeElement.closest && document.activeElement.closest("#kartice .kartica");
    const fokusPolje = fokus || (aktivni && aktivni.dataset.polje);
    const vsebnik = $("kartice");
    const obstojece = {};
    vsebnik.querySelectorAll(".kartica").forEach((k) => (obstojece[k.dataset.polje] = k));
    POLJA.forEach((def) => {
      const star = obstojece[def.polje];
      // kartic v urejanju ne prepisujemo – posodobitve AI ne smejo prekiniti dispečerja
      if (star && (S.urejanje[def.polje] || S.urediVse) && star.querySelector(".urejevalnik") && !izrisiVse.prisili) return;
      const tmp = document.createElement("div");
      tmp.innerHTML = karticaHtml(def).trim();
      if (star) star.replaceWith(tmp.firstChild); else vsebnik.appendChild(tmp.firstChild);
    });
    izrisiVse.prisili = false;
    if (fokusPolje) {
      const k = vsebnik.querySelector(`.kartica[data-polje="${fokusPolje}"]`);
      if (k && !k.contains(document.activeElement)) (k.querySelector(".urejevalnik input, .urejevalnik select, .urejevalnik textarea, .razlog input") || k).focus();
    }
    izrisiDogodke(); izrisiVprasanja(); izrisiNapredek(); oznaciCitate(); posodobiMiniKarto();
  }

  function izrisiVprasanja() {
    $("vprasanja").classList.toggle("skrito", !S.vprasanja.length);
    $("vprasanja-seznam").innerHTML = S.vprasanja.map((q, i) => `<li>${esc(q)}<button class="gumb majhen" data-vprasano="${i}">Vprašano</button></li>`).join("");
  }
  $("vprasanja-seznam").onclick = (e) => {
    const b = e.target.closest("[data-vprasano]"); if (!b) return;
    S.vprasanja.splice(Number(b.dataset.vprasano), 1);
    izrisiVprasanja();
  };

  const stPotrjenih = () => POLJA.filter((d) => potrjenZa(d.polje)).length;
  function kandidatiSprejmiVse() {
    return POLJA.filter((d) => !KLJUCNA.includes(d.polje) && !potrjenZa(d.polje)).map((d) => {
      const od = odprtiZa(d.polje);
      return od.length === 1 && (od[0].zaupanje || 0) >= 0.8 ? od[0] : null;
    }).filter(Boolean);
  }
  const manjkajoca = () => POLJA.filter((d) => d.obvezno && !potrjenZa(d.polje));
  function izrisiNapredek() {
    const n = stPotrjenih();
    $("napredek-besedilo").textContent = `${n} od ${POLJA.length} polj potrjenih`;
    $("napredek-trak").style.width = (n / POLJA.length) * 100 + "%";
    const k = kandidatiSprejmiVse();
    $("sprejmi-vse").disabled = !k.length || S.urediVse || !S.klic;
    $("sprejmi-vse").textContent = `Sprejmi vse z zaupanjem ≥ 0,8${k.length ? " (" + k.length + ")" : ""}`;
    const m = manjkajoca();
    const el = $("manjka-opozorilo");
    if (S.klic && S.klic.naloga_id) { el.textContent = "✓ Odobreno – naloga je v predlogu plana."; el.style.color = ""; }
    else { el.textContent = m.length ? "Za pošiljanje potrdite: " + m.map((d) => d.oznaka.toLowerCase()).join(", ") : "Obvezna polja so potrjena."; el.style.color = m.length ? "" : "var(--antracit)"; }
    $("odobri").disabled = !S.klic || !!S.klic.naloga_id;
    $("uredi-vse").disabled = !S.klic;
  }

  /* ---------- dejanja ---------- */
  function upsert(p) { if (p && p.id) S.predlogi.set(p.id, p); }
  const najdi = (id) => S.predlogi.get(id);

  async function sprejmi(p) {
    try { upsert(await AID.api(`/predlog/${p.id}/sprejmi`, { metoda: "POST" })); izrisiVse(p.polje); }
    catch (e) { napaka(e); }
  }
  async function zavrni(p, razlog) {
    try { upsert(await AID.api(`/predlog/${p.id}/zavrni`, { metoda: "POST", telo: { razlog: razlog || null } })); delete S.zavracanje[p.id]; izrisiVse(p.polje); }
    catch (e) { napaka(e); }
  }
  async function shraniVrednost(polje, vrednost) {
    const cilj = potrjenZa(polje) || odprtiZa(polje).find((p) => !p.nasprotuje_potrjenemu);
    if (cilj && enako(cilj.vrednost, vrednost) && POTRJENA.includes(cilj.stanje)) return;
    const odg = cilj
      ? await AID.api(`/predlog/${cilj.id}/spremeni`, { metoda: "POST", telo: { vrednost } })
      : await AID.api(`/klic/${S.klic.id}/rocno`, { metoda: "POST", telo: { polje, vrednost } });
    upsert(odg);
  }
  async function shraniKartico(polje) {
    const def = POLJA.find((d) => d.polje === polje);
    const v = preberiUrejevalnik(def);
    if (v === undefined) { AID.obvestilo("Neveljaven vnos", "Preverite obliko vrednosti.", true); return; }
    try {
      if (v !== null) await shraniVrednost(polje, v);
      delete S.urejanje[polje];
      izrisiVse.prisili = true; izrisiVse(polje);
    } catch (e) { napaka(e); }
  }
  function urejaj(polje) {
    if (!S.klic) { AID.obvestilo("Ni aktivnega klica", "Najprej sprejmite klic.", true); return; }
    S.urejanje[polje] = true; izrisiVse.prisili = true; izrisiVse(polje);
  }

  async function obdelajAkcijo(e) {
    const c = e.target.closest(".citat"); if (c) { skociNaSegment(c.dataset.seg); return; }
    const en = e.target.closest("[data-izberi-enoto]");
    if (en) { try { await shraniVrednost("predlagana_enota", en.dataset.izberiEnoto); izrisiVse("predlagana_enota"); } catch (e2) { napaka(e2); } return; }
    const b = e.target.closest("[data-akcija]"); if (!b) return;
    const k = b.closest(".kartica"); const polje = k.dataset.polje;
    const a = b.dataset.akcija;
    if (a === "sprejmi") sprejmi(najdi(b.dataset.id));
    else if (a === "zavrni") { S.zavracanje[b.dataset.id] = true; izrisiVse(polje); setTimeout(() => document.querySelector(`[data-razlog="${b.dataset.id}"]`)?.focus(), 0); }
    else if (a === "potrdi-zavrnitev") zavrni(najdi(b.dataset.id), document.querySelector(`[data-razlog="${b.dataset.id}"]`)?.value.trim());
    else if (a === "uredi") urejaj(polje);
    else if (a === "shrani") shraniKartico(polje);
    else if (a === "preklici") { delete S.urejanje[polje]; izrisiVse.prisili = true; izrisiVse(polje); }
  }
  $("kartice").addEventListener("click", obdelajAkcijo);

  /* tipkovnica: Enter = sprejmi, E = uredi, Del = zavrni, ↑/↓ = premik */
  $("kartice").addEventListener("keydown", (e) => {
    const k = e.target.closest(".kartica"); if (!k) return;
    const polje = k.dataset.polje;
    if (e.target.matches("[data-razlog]")) {
      if (e.key === "Enter") { e.preventDefault(); zavrni(najdi(e.target.dataset.razlog), e.target.value.trim()); }
      if (e.key === "Escape") { delete S.zavracanje[e.target.dataset.razlog]; izrisiVse(polje); }
      return;
    }
    if (e.target.matches("input, textarea, select")) {
      if (e.key === "Enter" && !e.shiftKey && !S.urediVse) { e.preventDefault(); shraniKartico(polje); }
      if (e.key === "Escape" && !S.urediVse) { delete S.urejanje[polje]; izrisiVse.prisili = true; izrisiVse(polje); }
      return;
    }
    if (e.target !== k && e.target.matches("button")) return;
    const p = odprtiZa(polje)[0];
    if (e.key === "Enter" && p) { e.preventDefault(); sprejmi(p); }
    else if ((e.key === "e" || e.key === "E") && !e.ctrlKey && !e.metaKey) { e.preventDefault(); urejaj(polje); }
    else if ((e.key === "Delete" || e.key === "Backspace") && p) { e.preventDefault(); S.zavracanje[p.id] = true; izrisiVse(polje); setTimeout(() => document.querySelector(`[data-razlog="${p.id}"]`)?.focus(), 0); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const vse = [...document.querySelectorAll("#kartice .kartica")];
      const i = vse.indexOf(k) + (e.key === "ArrowDown" ? 1 : -1);
      if (vse[i]) vse[i].focus();
    }
  });
  $("kartice").addEventListener("mouseover", (e) => {
    const k = e.target.closest(".kartica");
    document.querySelectorAll(".segment mark.zasvetljeno").forEach((m) => m.classList.remove("zasvetljeno"));
    if (k) document.querySelectorAll(`.segment mark[data-polje="${k.dataset.polje}"]`).forEach((m) => m.classList.add("zasvetljeno"));
  });

  $("sprejmi-vse").onclick = async () => {
    try {
      const r = await AID.api(`/klic/${S.klic.id}/sprejmi-vse`, { metoda: "POST" });
      r.forEach(upsert);
      AID.obvestilo(`Sprejetih ${r.length} predlogov`, "Prioriteto in enoto potrdite posebej.");
      izrisiVse();
    } catch (e) { napaka(e); }
  };

  $("uredi-vse").onclick = async () => {
    if (!S.urediVse) {
      S.urediVse = true;
      $("uredi-vse").textContent = "SHRANI UREDITVE";
      izrisiVse.prisili = true; izrisiVse();
      document.querySelector("#kartice .urejevalnik input, #kartice .urejevalnik select")?.focus();
      return;
    }
    const spremembe = [];
    for (const def of POLJA) {
      const v = preberiUrejevalnik(def);
      if (v === undefined) { AID.obvestilo("Neveljavna vrednost", def.oznaka, true); return; }
      if (v === null) continue;
      const pot = potrjenZa(def.polje), odp = odprtiZa(def.polje)[0];
      // nespremenjen AI predlog ostane nepotrjen – potrdi ga lahko le dispečer izrecno
      if (pot ? !enako(pot.vrednost, v) : !odp || !enako(odp.vrednost, v)) spremembe.push([def.polje, v]);
    }
    try {
      for (const [polje, v] of spremembe) await shraniVrednost(polje, v);
      S.urediVse = false;
      $("uredi-vse").textContent = "UREDI PODATKE";
      izrisiVse.prisili = true; izrisiVse();
      if (spremembe.length) AID.obvestilo(`Shranjenih sprememb: ${spremembe.length}`, "Nespremenjeni AI predlogi ostanejo za potrditev.");
    } catch (e) { napaka(e); }
  };

  $("odobri").onclick = async () => {
    if (!S.klic) return;
    if (S.urediVse) { AID.obvestilo("Najprej shranite ureditve", "", true); return; }
    const m = manjkajoca();
    if (m.length) {
      await AID.modal({ naslov: "Pošiljanje ni mogoče", html: `<p>Pred pošiljanjem morate sami potrditi:</p><ul>${m.map((d) => `<li><b>${esc(d.oznaka)}</b></li>`).join("")}</ul>` });
      const k = document.querySelector(`.kartica[data-polje="${m[0].polje}"]`);
      if (k) { k.scrollIntoView({ block: "center", behavior: "smooth" }); k.focus({ preventScroll: true }); }
      return;
    }
    const nepotrjeni = POLJA.filter((d) => !potrjenZa(d.polje) && odprtiZa(d.polje).length);
    const konflikti = [...S.predlogi.values()].filter((p) => p.nasprotuje_potrjenemu && p.stanje === "predlagano");
    const v = Object.fromEntries(POLJA.map((d) => [d.polje, potrjenZa(d.polje)?.vrednost]).filter(([, x]) => x != null));
    const ok = await AID.modal({
      naslov: `Odobri in pošlji enoti ${v.predlagana_enota}?`,
      html: `<p>Uporabljeni bodo <b>samo potrjeni</b> podatki (${Object.keys(v).length}). Planer pripravi predlog plana, ki ga potrdite v pogledu »Plan in karta«.</p>
        ${nepotrjeni.length ? `<p style="color:var(--rdeca)"><b>Nepotrjeni AI predlogi ne bodo uporabljeni:</b></p><ul>${nepotrjeni.map((d) => `<li>${esc(d.oznaka)}</li>`).join("")}</ul>` : ""}
        ${konflikti.length ? `<p style="color:var(--rdeca)"><b>${konflikti.length} AI opozoril nasprotuje potrjenim podatkom.</b></p>` : ""}
        <p><b>Povzetek:</b></p><ul><li>Prioriteta ${esc(PRIORITETE[v.prioriteta])}</li><li>${esc(v.naslov)}${v.orientacija ? " – " + esc(v.orientacija) : ""}</li>
        <li>${esc(VRSTE[v.vrsta_dogodka] || "")} ${v.stanje_pacienta ? "· " + esc(v.stanje_pacienta) : ""}</li></ul>`,
      gumbi: [{ besedilo: "Nazaj", vrednost: false }, { besedilo: "ODOBRI IN POŠLJI", vrednost: true, razred: "rdec" }],
    });
    if (!ok) return;
    try {
      $("odobri").disabled = true;
      const r = await AID.api(`/klic/${S.klic.id}/odobri`, { metoda: "POST" });
      S.klic.naloga_id = r.naloga.id;
      $("obrazec").classList.add("poslano");
      izrisiNapredek();
      AID.obvestilo(`Naloga ${r.naloga.id} ustvarjena`, "Predlog plana je pripravljen – potrdite ga.");
      if (r.plan_predlog) AID.preklopi("plan");
    } catch (e) { napaka(e); izrisiNapredek(); }
  };

  /* ---------- mini karta ---------- */
  let karta = null, mPredlog = null, mPotrjen = null, zadnjaLok = null;
  function zacniKarto() {
    if (!window.L) { $("mini-karta").innerHTML = '<div style="padding:12px;font-size:13px;color:var(--besedilo-2)">Karta ni na voljo – vpišite koordinate ročno.</div>'; return; }
    karta = L.map("mini-karta", { zoomControl: false }).setView([46.0569, 14.5058], 12);
    AID.podlagaKarte(karta);
    karta.on("click", async (e) => {
      if (!S.klic) return;
      try { await shraniVrednost("lokacija", { lat: +e.latlng.lat.toFixed(6), lon: +e.latlng.lng.toFixed(6) }); izrisiVse.prisili = true; izrisiVse("lokacija"); }
      catch (e2) { napaka(e2); }
    });
  }
  function posodobiMiniKarto() {
    if (!karta) return;
    const pot = potrjenZa("lokacija"), odp = odprtiZa("lokacija")[0];
    mPredlog && mPredlog.remove(); mPotrjen && mPotrjen.remove(); mPredlog = mPotrjen = null;
    if (odp) mPredlog = L.circleMarker([odp.vrednost.lat, odp.vrednost.lon], { radius: 10, color: (odp.zaupanje ?? 1) < 0.5 ? "#C98A00" : "#5A6270", dashArray: "4 3", weight: 2, fillOpacity: 0.15 }).addTo(karta).bindTooltip("AI predlog (nepotrjeno)");
    if (pot) mPotrjen = L.marker([pot.vrednost.lat, pot.vrednost.lon], { icon: AID.ikonaCilja() }).addTo(karta).bindTooltip("Potrjena lokacija");
    const t = pot || odp;
    if (t && !enako(zadnjaLok, t.vrednost)) { karta.setView([t.vrednost.lat, t.vrednost.lon], 15); zadnjaLok = t.vrednost; }
  }
  AID.ikonaCilja = () => L.divIcon({ className: "", html: '<div style="width:22px;height:22px;border-radius:50% 50% 50% 0;background:#8A1525;transform:rotate(-45deg);border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4)"></div>', iconSize: [22, 22], iconAnchor: [11, 22] });

  /* ---------- revizija ---------- */
  $("gumb-revizija").onclick = async () => {
    const kratko = (v) => { if (v == null) return "–"; const s = typeof v === "string" ? v : JSON.stringify(v); return s.length > 80 ? s.slice(0, 80) + "…" : s; };
    try {
      const sled = await AID.api("/revizija?omejitev=300");
      $("revizija-tabela").innerHTML = sled.map((r) => {
        const p = r.objekt === "Predlog" ? S.predlogi.get(r.objekt_id) : null;
        return `<tr><td class="mono">${AID.uraZdaj(new Date(r.cas * 1000))}</td><td>${esc(r.uporabnik)}</td><td>${esc(r.akcija)}</td>
          <td>${esc(p ? oznakaPolja(p.polje) : r.objekt + " " + r.objekt_id)}</td><td>${esc(kratko(r.stara_vrednost))} → ${esc(kratko(r.nova_vrednost))}</td></tr>`;
      }).join("") || `<tr><td colspan="5">Ni vnosov.</td></tr>`;
      $("revizija-predal").hidden = false;
      $("revizija-zapri").focus();
    } catch (e) { napaka(e); }
  };
  $("revizija-zapri").onclick = () => ($("revizija-predal").hidden = true);

  /* ---------- demo izpad AI ---------- */
  $("izpad-ai").onchange = async (e) => {
    try { await AID.api("/demo/izpad?ai=" + e.target.checked, { metoda: "POST" }); } catch (e2) { napaka(e2); }
  };
  function nastaviAi(na) {
    S.aiNaVoljo = na;
    $("st-ai").textContent = na ? "aktiven" : "ni na voljo – ročni vnos";
    $("st-ai").className = "pika " + (na ? "" : "napaka");
    $("rocni-zapis").classList.toggle("poudarjeno", !na);
  }

  /* ================= dogodki s strežnika ================= */
  const zaMojKlic = (x) => S.klic && x && x.klic_id === S.klic.id;
  AID.na("segment", (s) => { if (zaMojKlic(s)) { dodajSegment(s, true); } });
  AID.na("predlog_nov", (p) => { if (zaMojKlic(p)) { upsert(p); $("ai-analizira").classList.add("skrito"); izrisiVse(); } });
  AID.na("predlog_posodobljen", (p) => { if (zaMojKlic(p)) { upsert(p); izrisiVse(); } });
  AID.na("vprasanja", (v) => { if (S.klic && v.klic_id === S.klic.id) { S.vprasanja = v.vprasanja; izrisiVprasanja(); } });
  AID.na("klic", (k) => { if (S.klic && k.id === S.klic.id) { Object.assign(S.klic, k); izrisiTelefon(); } else AID.api("/cakajoci").then((c) => { S.cakajoci = c; izrisiVrsto(); izrisiTelefon(); }).catch(() => {}); });
  AID.na("enote", (e) => { S.enote = e; izrisiVse(); });
  AID.na("ai_stanje", (v) => nastaviAi(v.na_voljo));
  AID.na("opozorilo", (o) => {
    if (o.vrsta === "ai") nastaviAi(o.na_voljo);
    AID.obvestilo(o.vrsta === "ai" ? "AI poslušalec" : "Opozorilo", o.sporocilo, true, 6000);
    if (o.vrsta === "geokodiranje") $("mini-karta").classList.add("izbiranje");
  });

  /* ================= zagon ================= */
  async function zagon() {
    zacniKarto();
    try {
      [S.cakajoci, S.enote] = await Promise.all([AID.api("/cakajoci"), AID.api("/enote")]);
      AID.pisi("aid_enote", S.enote);
      const id = AID.beri("aid_klic", null);
      if (id) {
        const k = await AID.api("/klic/" + id).catch(() => null);
        if (k && k.stanje === "v_teku") nastaviKlic(k);
      }
      const st = await AID.api("/stanje?od=999999999");
      nastaviAi(st.ai_na_voljo);
      $("st-eta").textContent = "na voljo"; $("st-eta").className = "pika";
    } catch (e) {
      S.enote = AID.beri("aid_enote", []); // zadnje znano stanje
      $("st-eta").textContent = "ocena v brskalniku"; $("st-eta").className = "pika napaka";
    }
    izrisiVrsto(); izrisiTelefon(); izrisiVse();
  }
  document.addEventListener("DOMContentLoaded", zagon);
})();
