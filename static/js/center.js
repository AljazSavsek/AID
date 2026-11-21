/* AID – pogled klicnega centra.
 * Pravila (specifikacija razdelek 2):
 *  - AI predlaga, človek odloča: nič ne gre k ekipi brez potrditve.
 *  - vsak predlog ima citat (časovni žig + besedilo) in zaupanje; manjkajoče = »manjka«.
 *  - potrjenih polj AI nikoli ne prepiše (nov predlog z oznako nasprotuje_potrjenemu).
 *  - vsako dejanje gre v revizijsko sled.
 */
(function () {
  "use strict";
  const { esc } = AID;
  const DEMO = window.AID_DEMO_KLIC;
  const ENOTE = window.AID_ENOTE;
  const ETA = window.AID_ETA;

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
  const KLJUCNA = ["prioriteta", "predlagana_enota"]; // nikoli s »Sprejmi vse«
  const VRSTE = { prometna_nesreca: "Prometna nesreča", padec: "Padec", bolecina_v_prsih: "Bolečina v prsih", nezavest: "Nezavest", porod: "Porod", drugo: "Drugo" };
  const PRIORITETE = { 1: "1 – življenjsko ogrožen", 2: "2 – zelo nujno", 3: "3 – nujno", 4: "4 – manj nujno", 5: "5 – ni nujno" };
  const POTRJENA = ["sprejeto", "spremenjeno"];

  /* ---------- stanje ---------- */
  const S = {
    klic: null,          // { id, zacetek, stanje }
    segmenti: [],        // { id, govorec, od_s, do_s, besedilo, koncni, rocni }
    predlogi: [],        // Predlog po podatkovnem modelu
    vprasanja: [],
    urejanje: {},        // polje -> true
    zavracanje: {},      // predlog_id -> true
    urediVse: false,
    poslano: false,
    izpadAi: false,
    izpadPrepis: false,
    tekoci: null,        // časovnik simulatorja
  };
  let stPredloga = 0;

  const $ = (id) => document.getElementById(id);

  /* ---------- čakalna vrsta ---------- */
  const vrsta = [
    { id: "k-1042", stevilka: DEMO.klicatelj, omrezje: DEMO.omrezje, cakanje: 14, stanje: "caka" },
    { id: "k-1043", stevilka: "+386 31 207 114", omrezje: "Domžale", cakanje: 6, stanje: "caka" },
    { id: "k-1044", stevilka: "+386 40 883 090", omrezje: "Ljubljana – Vič", cakanje: 2, stanje: "caka" },
  ];
  function izrisiVrsto() {
    const cakajo = vrsta.filter((k) => k.stanje === "caka");
    $("st-cakajocih").textContent = cakajo.length;
    $("vrsta-klicev").innerHTML = vrsta.map((k, i) => {
      const razred = k.stanje === "aktiven" ? "aktiven" : k.stanje === "zakljucen" ? "zakljucen" : (k === cakajo[0] && !S.klic ? "naslednji" : "");
      const desno = k.stanje === "aktiven" ? "V TEKU" : k.stanje === "zakljucen" ? "✓" : AID.trajanje(k.cakanje);
      return `<li class="${razred}"><span class="stevilka">${esc(k.stevilka)}</span><span class="cakanje">${desno}</span>
        <span class="podatek">${esc(k.omrezje)}</span><span class="podatek">${k.stanje === "caka" ? "čaka" : ""}</span></li>`;
    }).join("");
  }
  setInterval(() => { vrsta.forEach((k) => { if (k.stanje === "caka") k.cakanje++; }); izrisiVrsto(); }, 1000);

  /* ---------- telefon ---------- */
  const val = $("valovanje");
  for (let i = 0; i < 28; i++) val.appendChild(document.createElement("i"));
  let govori = null;
  setInterval(() => {
    [...val.children].forEach((b) => { b.style.height = (govori ? 4 + Math.random() * 22 : 4 + Math.random() * 3) + "px"; });
  }, 120);

  let telCasovnik = null;
  function zacniKlic() {
    const k = vrsta.find((x) => x.stanje === "caka");
    if (!k) return;
    k.stanje = "aktiven";
    S.klic = { id: k.id, zacetek: Date.now(), stanje: "v_teku", stevilka: k.stevilka };
    S.segmenti = []; S.predlogi = []; S.vprasanja = []; S.poslano = false; S.urejanje = {}; S.urediVse = false;
    $("obrazec-telo").closest(".obrazec").classList.remove("poslano");
    AID.revizija("Klic", k.id, "zacetek", null, { stevilka: k.stevilka });
    $("telefon").classList.add("v-teku");
    $("tel-stanje").textContent = "V teku";
    $("tel-stevilka").textContent = k.stevilka;
    $("tel-omrezje").textContent = k.omrezje;
    ["tel-utisaj", "tel-zadrzi", "tel-prevezi"].forEach((id) => ($(id).disabled = false));
    $("tel-glavni").textContent = "KONČAJ KLIC";
    $("v-zivo").classList.remove("skrito");
    $("prepis-prazno")?.remove();
    clearInterval(telCasovnik);
    telCasovnik = setInterval(() => ($("tel-cas").textContent = AID.trajanje((Date.now() - S.klic.zacetek) / 1000)), 500);
    izrisiVrsto();
    izrisiVse();
    if (k.id === "k-1042") predvajajDemo();
  }
  function koncajKlic() {
    if (!S.klic) return;
    clearTimeout(S.tekoci); S.tekoci = null; govori = null;
    S.klic.stanje = "zakljucen";
    AID.revizija("Klic", S.klic.id, "konec", "v_teku", "zakljucen");
    const k = vrsta.find((x) => x.id === S.klic.id); if (k) k.stanje = "zakljucen";
    $("telefon").classList.remove("v-teku", "govori-klicatelj");
    $("tel-stanje").textContent = "Zaključen";
    $("v-zivo").classList.add("skrito");
    clearInterval(telCasovnik);
    ["tel-utisaj", "tel-zadrzi", "tel-prevezi"].forEach((id) => ($(id).disabled = true));
    $("tel-glavni").textContent = vrsta.some((x) => x.stanje === "caka") ? "SPREJMI NASLEDNJI KLIC" : "NI ČAKAJOČIH";
    S.klic = null;
    izrisiVrsto();
  }
  $("tel-glavni").onclick = () => {
    if (S.klic) koncajKlic();
    else {
      if (vrsta.find((x) => x.stanje === "zakljucen")) { $("prepis").innerHTML = ""; }
      zacniKlic();
    }
  };
  ["tel-utisaj", "tel-zadrzi"].forEach((id) => ($(id).onclick = (e) => e.currentTarget.classList.toggle("vklopljen")));
  $("tel-prevezi").onclick = () => AID.obvestilo("Prevezava", "V demu prevezava ni na voljo.");

  /* ---------- simulator klica (besedilo namesto zvoka) ---------- */
  function hitrost() { return Number($("hitrost").value) || 1; }
  function predvajajDemo() {
    const segs = DEMO.segmenti;
    let i = 0;
    const naslednji = () => {
      if (!S.klic || i >= segs.length) { govori = null; return; }
      const seg = segs[i];
      const prej = i ? segs[i - 1].do_s : 0;
      const zamik = Math.max(0.3, seg.od_s - prej) * 1000 / hitrost();
      S.tekoci = setTimeout(() => {
        pretakajSegment(seg, () => { i++; naslednji(); });
      }, zamik);
    };
    naslednji();
  }
  /* delni segmenti (kot pri streaming STT), nato končni */
  function pretakajSegment(seg, koncano) {
    govori = seg.govorec;
    $("telefon").classList.toggle("govori-klicatelj", seg.govorec === "klicatelj");
    if (S.izpadPrepis) {
      // prepis odpove → govor se ne izpiše; dispečer tipka ročno
      S.tekoci = setTimeout(() => { govori = null; koncano(); }, (seg.do_s - seg.od_s) * 1000 / hitrost());
      return;
    }
    const besede = seg.besedilo.split(" ");
    const trajanje = (seg.do_s - seg.od_s) * 1000 / hitrost();
    const korak = trajanje / besede.length;
    let n = 0;
    const el = dodajSegmentEl({ ...seg, koncni: false, besedilo: "" });
    const tik = () => {
      n++;
      el.querySelector(".besedilo").textContent = besede.slice(0, n).join(" ");
      skrolajPrepis();
      if (n < besede.length) S.tekoci = setTimeout(tik, korak);
      else {
        govori = null;
        $("telefon").classList.remove("govori-klicatelj");
        koncniSegment({ ...seg, koncni: true }, el);
        koncano();
      }
    };
    S.tekoci = setTimeout(tik, korak);
  }

  function dodajSegmentEl(seg) {
    const el = document.createElement("div");
    el.className = `segment ${seg.govorec}${seg.koncni ? "" : " delni"}${seg.rocni ? " rocni" : ""}`;
    el.id = "seg-" + seg.id;
    el.innerHTML = `<div class="cas">${AID.trajanje(seg.od_s)}</div><div><div class="govorec">${seg.govorec === "klicatelj" ? "Klicatelj" : "Dispečer"}</div><div class="besedilo"></div></div>`;
    el.querySelector(".besedilo").textContent = seg.besedilo;
    $("prepis").appendChild(el);
    skrolajPrepis();
    return el;
  }
  function skrolajPrepis() { const p = $("prepis"); p.scrollTop = p.scrollHeight; }

  function koncniSegment(seg, el) {
    S.segmenti.push(seg);
    el.classList.remove("delni");
    AID.revizija("Segment", seg.id, "prepis", null, seg.besedilo, seg.rocni ? AID.UPORABNIK.ime : "sistem (prepis)");
    oznaciCitate();
    poklicAi(seg);
  }

  /* ročni zapis – zadnja rezerva prepisa */
  $("rocni-zapis").onsubmit = (e) => {
    e.preventDefault();
    const t = $("rocni-vnos").value.trim();
    if (!t) return;
    if (!S.klic) { AID.obvestilo("Ni aktivnega klica", "Najprej sprejmite klic."); return; }
    const od = (Date.now() - S.klic.zacetek) / 1000;
    const seg = { id: "r" + (S.segmenti.length + 1), govorec: $("rocni-govorec").value, od_s: od, do_s: od, besedilo: t, koncni: true, rocni: true };
    const el = dodajSegmentEl(seg);
    koncniSegment(seg, el);
    $("rocni-vnos").value = "";
  };

  /* ---------- AI poslušalec (demo: skriptiran izhod po shemi) ---------- */
  function poklicAi(seg) {
    if (S.izpadAi) return; // brez predlogov → ročni vnos, vmesnik dela naprej
    const izhod = DEMO.ai[seg.id];
    if (!izhod) return;
    $("ai-analizira").classList.remove("skrito");
    setTimeout(() => {
      $("ai-analizira").classList.add("skrito");
      if (!S.klic || S.izpadAi) return;
      uveljaviIzhodAi(izhod);
    }, 700 + Math.random() * 600);
  }

  function uveljaviIzhodAi(izhod) {
    (izhod.posodobitve || []).forEach(uveljaviPosodobitev);
    if (izhod.vprasanja_za_klicatelja) S.vprasanja = izhod.vprasanja_za_klicatelja.slice();
    izrisiVse();
  }

  function enako(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function uveljaviPosodobitev(u) {
    // zavrni posodobitev brez dokaza (AI si ne sme izmišljevati)
    if (!u.dokazi || !u.dokazi.length) return;
    const zaPolje = S.predlogi.filter((p) => p.polje === u.polje);
    const potrjen = zaPolje.find((p) => POTRJENA.includes(p.stanje));
    const odprti = zaPolje.filter((p) => p.stanje === "predlagano");
    const nov = () => ({
      id: "p" + (++stPredloga), klic_id: S.klic ? S.klic.id : null, polje: u.polje, vrednost: u.vrednost, zaupanje: u.zaupanje,
      dokazi: u.dokazi, stanje: "predlagano", verzija: zaPolje.length + 1, predlagal: "ai",
      potrdil: null, potrjeno_ob: null, prvotna_ai_vrednost: u.vrednost, nasprotuje_potrjenemu: false, ustvarjeno: Date.now(),
    });
    if (potrjen) {
      if (enako(potrjen.vrednost, u.vrednost)) return;
      if (odprti.some((p) => enako(p.vrednost, u.vrednost))) return;
      const p = nov(); p.nasprotuje_potrjenemu = true;
      S.predlogi.push(p);
      AID.revizija("Predlog", p.id, "predlog_ai", potrjen.vrednost, p.vrednost, "AI poslušalec");
      AID.obvestilo("AI zaznal drugačno vrednost", `${oznakaPolja(u.polje)} je že potrjen – preverite.`, true);
      return;
    }
    const isti = odprti.find((p) => enako(p.vrednost, u.vrednost));
    if (isti) { isti.zaupanje = u.zaupanje; isti.dokazi = u.dokazi; return; }
    if (!u.alternativa) odprti.forEach((p) => { p.stanje = "zastarelo"; AID.revizija("Predlog", p.id, "zastarelo", p.vrednost, u.vrednost, "AI poslušalec"); });
    const p = nov();
    S.predlogi.push(p);
    AID.revizija("Predlog", p.id, "predlog_ai", null, p.vrednost, "AI poslušalec");
  }

  /* ---------- dejanja dispečerja ---------- */
  function potrdi(p, stanje, novaVrednost) {
    const stara = p.vrednost;
    if (novaVrednost !== undefined) p.vrednost = novaVrednost;
    p.stanje = stanje;
    p.potrdil = AID.UPORABNIK.ime;
    p.potrjeno_ob = Date.now();
    AID.revizija("Predlog", p.id, stanje === "sprejeto" ? "sprejmi" : "spremeni", stara, p.vrednost);
  }
  function sprejmi(p) {
    // ostali odprti/potrjeni predlogi tega polja niso več veljavni
    S.predlogi.filter((x) => x.polje === p.polje && x !== p && (x.stanje === "predlagano" || POTRJENA.includes(x.stanje)))
      .forEach((x) => { x.stanje = POTRJENA.includes(x.stanje) ? "zastarelo" : "zavrnjeno"; x.razlog = "izbrana druga vrednost"; });
    potrdi(p, "sprejeto");
    if (p.polje === "naslov") predlagajLokacijoIzNaslova(p);
    izrisiVse(p.polje);
  }
  function zavrni(p, razlog) {
    p.stanje = "zavrnjeno"; p.razlog = razlog || null;
    AID.revizija("Predlog", p.id, "zavrni", p.vrednost, null, undefined);
    if (razlog) AID.revizija("Predlog", p.id, "razlog_zavrnitve", null, razlog);
    delete S.zavracanje[p.id];
    izrisiVse(p.polje);
  }
  function shraniUreditev(polje, vrednost) {
    const zaPolje = S.predlogi.filter((p) => p.polje === polje);
    const potrjen = zaPolje.find((p) => POTRJENA.includes(p.stanje));
    const odprt = zaPolje.find((p) => p.stanje === "predlagano" && !p.nasprotuje_potrjenemu);
    const cilj = potrjen || odprt;
    if (cilj) {
      if (enako(cilj.vrednost, vrednost) && POTRJENA.includes(cilj.stanje)) return;
      zaPolje.filter((p) => p !== cilj && p.stanje === "predlagano").forEach((p) => { p.stanje = "zavrnjeno"; p.razlog = "ročno popravljeno"; });
      potrdi(cilj, enako(cilj.prvotna_ai_vrednost, vrednost) ? "sprejeto" : "spremenjeno", vrednost);
    } else {
      // ročni vnos brez AI
      const p = { id: "p" + (++stPredloga), klic_id: S.klic ? S.klic.id : null, polje, vrednost, zaupanje: null, dokazi: [], stanje: "spremenjeno",
        verzija: zaPolje.length + 1, predlagal: "dispecer", potrdil: AID.UPORABNIK.ime, potrjeno_ob: Date.now(), prvotna_ai_vrednost: null };
      S.predlogi.push(p);
      AID.revizija("Predlog", p.id, "rocni_vnos", null, vrednost);
    }
    if (polje === "naslov") predlagajLokacijoIzNaslova(S.predlogi.find((p) => p.polje === "naslov" && POTRJENA.includes(p.stanje)));
  }

  /* geokodiranje potrjenega naslova → nov PREDLOG lokacije (ne potrjen) */
  const GEO_DEMO = { "celovška cesta 105": { lat: 46.07138, lon: 14.4887 }, "celovška cesta 150": { lat: 46.07405, lon: 14.48565 } };
  function predlagajLokacijoIzNaslova(p) {
    if (!p) return;
    const kljuc = String(p.vrednost).toLowerCase().split(",")[0].trim();
    const loc = GEO_DEMO[kljuc];
    if (!loc) { AID.obvestilo("Naslova ni bilo mogoče geokodirati", "Kliknite lokacijo na karti.", true); $("mini-karta").classList.add("izbiranje"); return; }
    const dokazi = p.dokazi.length ? p.dokazi : [{ segment_id: "", citat: "geokodirano iz potrjenega naslova" }];
    uveljaviPosodobitev({ polje: "lokacija", vrednost: loc, zaupanje: 0.9, dokazi });
  }

  /* ---------- izris ---------- */
  function oznakaPolja(polje) { return (POLJA.find((x) => x.polje === polje) || {}).oznaka || polje; }
  function fmt(polje, v) {
    if (v == null || v === "") return "";
    switch (polje) {
      case "vrsta_dogodka": return VRSTE[v] || v;
      case "prioriteta": return PRIORITETE[v] || String(v);
      case "potrebna_oprema": return Array.isArray(v) ? v.join(", ") : v;
      case "lokacija": return v.lat.toFixed(5) + ", " + v.lon.toFixed(5);
      case "predlagana_enota": { const e = ENOTE.find((x) => x.id === v); return e ? `${e.id} · ${e.ime}` : v; }
      default: return String(v);
    }
  }
  function segmentPo(id) { return S.segmenti.find((s) => s.id === id); }
  function dokaziHtml(p) {
    if (!p.dokazi || !p.dokazi.length) return "";
    return `<div class="dokazi">${p.dokazi.map((d) => {
      const s = segmentPo(d.segment_id);
      return `<button class="citat" data-seg="${esc(d.segment_id)}" title="Skok na prepis"><span class="c-cas">${s ? AID.trajanje(s.od_s) : "--:--"}</span>„${esc(d.citat)}“</button>`;
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
      case "lokacija": return `<input id="${id}" value="${v ? v.lat.toFixed(5) + ", " + v.lon.toFixed(5) : ""}" placeholder="46.07138, 14.48870"><span class="namig">Vpišite »lat, lon« ali kliknite na karto zgoraj.</span>`;
      case "enota": return `<select id="${id}">${ENOTE.filter((e) => e.stanje === "prosto").map((e) => `<option value="${e.id}" ${e.id === v ? "selected" : ""}>${e.id} · ${e.ime}</option>`).join("")}</select>`;
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

  function enoteHtml(izbrana, omogoceno) {
    const top = ETA.filter((x) => (ENOTE.find((e) => e.id === x.enota_id) || {}).stanje === "prosto").slice(0, 3);
    return `<table class="enote-tabela" aria-label="Tri najhitrejše enote"><tbody>${top.map((x) => {
      const e = ENOTE.find((y) => y.id === x.enota_id);
      const kak = x.kakovost === "natančno" ? "natancno" : x.kakovost === "ocena" ? "ocena" : "";
      return `<tr class="${x.enota_id === izbrana ? "izbrana" : ""}"><td><b>${e.id}</b> <span style="color:var(--besedilo-2)">${e.tip}</span></td>
        <td class="eta">${Math.round(x.cas_s / 60)} min</td><td>${(x.razdalja_m / 1000).toFixed(1)} km</td>
        <td><span class="kakovost ${kak}">${x.kakovost}</span></td>
        <td>${omogoceno && x.enota_id !== izbrana ? `<button class="gumb majhen" data-izberi-enoto="${e.id}">Izberi</button>` : ""}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function karticaHtml(def) {
    const zaPolje = S.predlogi.filter((p) => p.polje === def.polje);
    const potrjen = zaPolje.find((p) => POTRJENA.includes(p.stanje));
    const odprti = zaPolje.filter((p) => p.stanje === "predlagano").sort((a, b) => b.zaupanje - a.zaupanje);
    const zgodovina = zaPolje.filter((p) => p.stanje === "zastarelo" || p.stanje === "zavrnjeno");
    const ureja = S.urediVse || S.urejanje[def.polje];
    const glavni = potrjen || odprti[0];
    let razred = "kartica", telo = "", gumbi = "";
    if (def.kljucno) razred += " kljucno";

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
        <div class="potrjeno-info">${potrjen.stanje === "sprejeto" ? "Sprejeto" : potrjen.predlagal === "dispecer" ? "Ročni vnos" : "Spremenjeno"} · ${esc(potrjen.potrdil)} · ${AID.uraZdaj(new Date(potrjen.potrjeno_ob))}</div>`;
      if (def.tip === "enota") telo += enoteHtml(potrjen.vrednost, false);
      telo += odprti.map((p) => `<div class="konflikt" data-predlog="${p.id}"><div class="naslovek">⚠ AI zaznal drugačno vrednost · nasprotuje potrjenemu</div>
        <div class="vrednost">${esc(fmt(def.polje, p.vrednost))} ${zaupanjeHtml(p.zaupanje)}</div>${dokaziHtml(p)}
        <div class="k-gumbi"><button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi novo</button><button class="gumb" data-akcija="zavrni" data-id="${p.id}">Ohrani potrjeno</button></div>${razlogHtml(p)}</div>`).join("");
      gumbi = `<button class="gumb" data-akcija="uredi">Uredi <kbd>E</kbd></button>`;
    } else if (odprti.length) {
      const p = odprti[0];
      razred += p.zaupanje < 0.5 ? " negotovo" : " predlagano";
      const negot = p.zaupanje < 0.5 ? ` <span class="znacka negotovo">negotovo</span>` : "";
      if (odprti.length > 1) {
        telo = `<div class="alternative">${odprti.map((a) => `<div class="alternativa"><div><div class="vrednost">${esc(fmt(def.polje, a.vrednost))}</div>${dokaziHtml(a)}</div>
          <div class="k-gumbi">${zaupanjeHtml(a.zaupanje)}<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${a.id}">Izberi</button></div></div>`).join("")}</div>`;
      } else {
        telo = `<div class="vrednost">${esc(fmt(def.polje, p.vrednost))}${negot}</div>${dokaziHtml(p)}`;
      }
      if (def.tip === "enota") telo += enoteHtml(p.vrednost, true);
      gumbi = `<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi <kbd>⏎</kbd></button>
        <button class="gumb" data-akcija="uredi">Uredi <kbd>E</kbd></button>
        <button class="gumb zavrni" data-akcija="zavrni" data-id="${p.id}">Zavrni <kbd>Del</kbd></button>`;
      telo += razlogHtml(p);
    } else {
      razred += " manjka";
      telo = `<div class="vrednost prazna">manjka</div>`;
      gumbi = `<button class="gumb" data-akcija="uredi">+ Vnesi ročno <kbd>E</kbd></button>`;
    }
    const desno = potrjen ? `<span class="znacka temna">potrjeno</span>` : glavni ? `${zaupanjeHtml(glavni.zaupanje)}<span class="znacka">AI predlog</span>` : `<span class="znacka rdeca-bleda">manjka</span>`;
    return `<article class="${razred}" tabindex="0" data-polje="${def.polje}" aria-label="${esc(def.oznaka)}">
      <div class="k-glava"><span class="oznaka">${esc(def.oznaka)}</span><span class="desno">${desno}</span></div>
      ${telo}<div class="k-gumbi">${gumbi}</div>${zgHtml}</article>`;
  }
  function razlogHtml(p) {
    if (!S.zavracanje[p.id]) return "";
    return `<div class="razlog"><input data-razlog="${p.id}" placeholder="Razlog zavrnitve (neobvezno)"><button class="gumb majhen" data-akcija="potrdi-zavrnitev" data-id="${p.id}">Zavrni</button></div>`;
  }

  function izrisiVse(fokus) {
    const aktivni = document.activeElement && document.activeElement.closest && document.activeElement.closest(".kartica");
    const fokusPolje = fokus || (aktivni && aktivni.dataset.polje);
    const vsebnik = $("kartice");
    // kartic v urejanju ne prepisujemo – AI posodobitve ne smejo prekiniti dispečerja
    const obstojece = {};
    vsebnik.querySelectorAll(".kartica").forEach((k) => (obstojece[k.dataset.polje] = k));
    POLJA.forEach((def, i) => {
      const star = obstojece[def.polje];
      if (star && (S.urejanje[def.polje] || S.urediVse) && star.querySelector(".urejevalnik") && !izrisiVse.prisili) return;
      const tmp = document.createElement("div");
      tmp.innerHTML = karticaHtml(def).trim();
      const nov = tmp.firstChild;
      if (star) star.replaceWith(nov); else vsebnik.appendChild(nov);
    });
    izrisiVse.prisili = false;
    if (fokusPolje) {
      const k = vsebnik.querySelector(`.kartica[data-polje="${fokusPolje}"]`);
      if (k && !k.contains(document.activeElement)) { const inp = k.querySelector(".urejevalnik input, .urejevalnik select, .urejevalnik textarea, .razlog input"); (inp || k).focus({ preventScroll: false }); }
    }
    izrisiVprasanja();
    izrisiNapredek();
    oznaciCitate();
    posodobiMiniKarto();
  }

  function izrisiVprasanja() {
    const v = S.vprasanja;
    $("vprasanja").classList.toggle("skrito", !v.length);
    $("vprasanja-seznam").innerHTML = v.map((q, i) => `<li>${esc(q)}<button class="gumb majhen" data-vprasano="${i}">Vprašano</button></li>`).join("");
  }
  $("vprasanja-seznam").onclick = (e) => {
    const b = e.target.closest("[data-vprasano]"); if (!b) return;
    const q = S.vprasanja.splice(Number(b.dataset.vprasano), 1)[0];
    AID.revizija("Vprasanje", "-", "vprasano", q, null);
    izrisiVprasanja();
  };

  function stPotrjenih() { return POLJA.filter((d) => S.predlogi.some((p) => p.polje === d.polje && POTRJENA.includes(p.stanje))).length; }
  function kandidatiSprejmiVse() {
    return POLJA.filter((d) => !KLJUCNA.includes(d.polje)).map((d) => {
      const zp = S.predlogi.filter((p) => p.polje === d.polje);
      if (zp.some((p) => POTRJENA.includes(p.stanje))) return null;
      const od = zp.filter((p) => p.stanje === "predlagano");
      return od.length === 1 && od[0].zaupanje >= 0.8 ? od[0] : null;
    }).filter(Boolean);
  }
  function manjkajoca() {
    return POLJA.filter((d) => d.obvezno && !S.predlogi.some((p) => p.polje === d.polje && POTRJENA.includes(p.stanje)));
  }
  function izrisiNapredek() {
    const n = stPotrjenih();
    $("napredek-besedilo").textContent = `${n} od ${POLJA.length} polj potrjenih`;
    $("napredek-trak").style.width = (n / POLJA.length * 100) + "%";
    const k = kandidatiSprejmiVse();
    $("sprejmi-vse").disabled = !k.length || S.urediVse;
    $("sprejmi-vse").textContent = `Sprejmi vse z zaupanjem ≥ 0,8${k.length ? " (" + k.length + ")" : ""}`;
    const m = manjkajoca();
    if (S.poslano) $("manjka-opozorilo").textContent = "✓ Poslano ekipi. Spremembe pošljite znova.";
    else $("manjka-opozorilo").textContent = m.length ? "Za pošiljanje potrdite: " + m.map((d) => d.oznaka.toLowerCase()).join(", ") : "Obvezna polja so potrjena.";
    $("manjka-opozorilo").style.color = (!S.poslano && !m.length) ? "var(--antracit)" : "";
  }

  /* označi citate v prepisu */
  function oznaciCitate() {
    S.segmenti.forEach((seg) => {
      const el = $("seg-" + seg.id); if (!el) return;
      const citati = [];
      S.predlogi.filter((p) => p.stanje !== "zastarelo" && p.stanje !== "zavrnjeno").forEach((p) => (p.dokazi || []).forEach((d) => { if (d.segment_id === seg.id) citati.push({ c: d.citat, p: p.polje }); }));
      let html = esc(seg.besedilo);
      // najdaljše najprej, da se ne prekrivajo
      const obseg = [];
      citati.sort((a, b) => b.c.length - a.c.length).forEach(({ c, p }) => {
        const i = seg.besedilo.toLowerCase().indexOf(c.toLowerCase());
        if (i < 0) return;
        if (obseg.some(([a, b]) => i < b && i + c.length > a)) return;
        obseg.push([i, i + c.length, p]);
      });
      if (obseg.length) {
        obseg.sort((a, b) => a[0] - b[0]);
        let out = "", zadnji = 0;
        obseg.forEach(([a, b, p]) => { out += esc(seg.besedilo.slice(zadnji, a)) + `<mark data-polje="${p}" title="${esc(oznakaPolja(p))}">` + esc(seg.besedilo.slice(a, b)) + "</mark>"; zadnji = b; });
        html = out + esc(seg.besedilo.slice(zadnji));
      }
      el.querySelector(".besedilo").innerHTML = html;
    });
  }

  function skociNaSegment(id) {
    const el = $("seg-" + id); if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("zasvetljeno");
    setTimeout(() => el.classList.remove("zasvetljeno"), 1600);
  }
  $("prepis").addEventListener("click", (e) => {
    const m = e.target.closest("mark[data-polje]"); if (!m) return;
    const k = document.querySelector(`.kartica[data-polje="${m.dataset.polje}"]`);
    if (k) { k.scrollIntoView({ behavior: "smooth", block: "center" }); k.focus({ preventScroll: true }); }
  });

  /* ---------- dogodki na karticah ---------- */
  function najdi(id) { return S.predlogi.find((p) => p.id === id); }
  function odprtZaPolje(polje) { return S.predlogi.filter((p) => p.polje === polje && p.stanje === "predlagano").sort((a, b) => b.zaupanje - a.zaupanje)[0]; }
  function defPolja(polje) { return POLJA.find((d) => d.polje === polje); }

  function shraniKartico(polje) {
    const def = defPolja(polje);
    const v = preberiUrejevalnik(def);
    if (v === undefined) { AID.obvestilo("Neveljaven vnos", "Preverite obliko vrednosti.", true); return; }
    delete S.urejanje[polje];
    if (v !== null) shraniUreditev(polje, v);
    izrisiVse.prisili = true;
    izrisiVse(polje);
  }

  $("kartice").addEventListener("click", (e) => {
    const c = e.target.closest(".citat"); if (c) { skociNaSegment(c.dataset.seg); return; }
    const en = e.target.closest("[data-izberi-enoto]");
    if (en) { shraniUreditev("predlagana_enota", en.dataset.izberiEnoto); izrisiVse("predlagana_enota"); return; }
    const b = e.target.closest("[data-akcija]"); if (!b) return;
    const k = b.closest(".kartica"); const polje = k.dataset.polje;
    const a = b.dataset.akcija;
    if (a === "sprejmi") sprejmi(najdi(b.dataset.id));
    else if (a === "zavrni") { S.zavracanje[b.dataset.id] = true; izrisiVse(polje); setTimeout(() => document.querySelector(`[data-razlog="${b.dataset.id}"]`)?.focus(), 0); }
    else if (a === "potrdi-zavrnitev") zavrni(najdi(b.dataset.id), document.querySelector(`[data-razlog="${b.dataset.id}"]`)?.value.trim());
    else if (a === "uredi") { S.urejanje[polje] = true; izrisiVse.prisili = true; izrisiVse(polje); }
    else if (a === "shrani") shraniKartico(polje);
    else if (a === "preklici") { delete S.urejanje[polje]; izrisiVse.prisili = true; izrisiVse(polje); }
  });

  /* tipkovnica: Enter = sprejmi, E = uredi, Del = zavrni */
  $("kartice").addEventListener("keydown", (e) => {
    const k = e.target.closest(".kartica"); if (!k) return;
    const polje = k.dataset.polje;
    const vnos = e.target.matches("input, textarea, select");
    if (e.target.matches("[data-razlog]")) {
      if (e.key === "Enter") { e.preventDefault(); zavrni(najdi(e.target.dataset.razlog), e.target.value.trim()); }
      if (e.key === "Escape") { delete S.zavracanje[e.target.dataset.razlog]; izrisiVse(polje); }
      return;
    }
    if (vnos) {
      if (e.key === "Enter" && !e.shiftKey && !S.urediVse) { e.preventDefault(); shraniKartico(polje); }
      if (e.key === "Escape" && !S.urediVse) { delete S.urejanje[polje]; izrisiVse.prisili = true; izrisiVse(polje); }
      return;
    }
    if (e.target !== k && e.target.matches("button")) return; // gumbi delajo po svoje
    const p = odprtZaPolje(polje);
    if (e.key === "Enter" && p) { e.preventDefault(); sprejmi(p); }
    else if ((e.key === "e" || e.key === "E") && !e.ctrlKey && !e.metaKey) { e.preventDefault(); S.urejanje[polje] = true; izrisiVse.prisili = true; izrisiVse(polje); }
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

  /* Sprejmi vse ≥ 0,8 – nikoli prioriteta in enota */
  $("sprejmi-vse").onclick = () => {
    const k = kandidatiSprejmiVse();
    k.forEach((p) => potrdi(p, "sprejeto"));
    AID.revizija("Obrazec", S.klic ? S.klic.id : "-", "sprejmi_vse_08", null, k.map((p) => p.polje));
    AID.obvestilo(`Sprejetih ${k.length} predlogov`, "Prioriteto in enoto potrdite posebej.");
    izrisiVse();
  };

  /* UREDI PODATKE – vse kartice v urejanje */
  $("uredi-vse").onclick = () => {
    if (!S.urediVse) {
      S.urediVse = true;
      $("uredi-vse").textContent = "SHRANI UREDITVE";
      izrisiVse.prisili = true; izrisiVse();
      document.querySelector("#kartice .urejevalnik input, #kartice .urejevalnik select")?.focus();
      return;
    }
    let napaka = false;
    POLJA.forEach((def) => {
      const v = preberiUrejevalnik(def);
      if (v === undefined) { napaka = true; return; }
      if (v === null) return;
      const zp = S.predlogi.filter((p) => p.polje === def.polje);
      const trenutna = (zp.find((p) => POTRJENA.includes(p.stanje)) || zp.find((p) => p.stanje === "predlagano") || {}).vrednost;
      // ključna polja in nespremenjeni AI predlogi ostanejo nepotrjeni, dokler jih dispečer izrecno ne potrdi
      const jePotrjen = zp.some((p) => POTRJENA.includes(p.stanje));
      if (!enako(trenutna, v) || jePotrjen) shraniUreditev(def.polje, v);
    });
    if (napaka) { AID.obvestilo("Nekatere vrednosti niso veljavne", "Preverite označena polja.", true); return; }
    S.urediVse = false;
    $("uredi-vse").textContent = "UREDI PODATKE";
    izrisiVse.prisili = true; izrisiVse();
  };

  /* ODOBRI IN POŠLJI – samo potrjeni podatki gredo k ekipi */
  $("odobri").onclick = async () => {
    if (S.urediVse) { AID.obvestilo("Najprej shranite ureditve", "", true); return; }
    const m = manjkajoca();
    if (m.length) {
      await AID.modal({ naslov: "Pošiljanje ni mogoče", html: `<p>Pred pošiljanjem ekipi morate sami potrditi:</p><ul>${m.map((d) => `<li><b>${esc(d.oznaka)}</b></li>`).join("")}</ul>`, gumbi: [{ besedilo: "V redu", vrednost: true, razred: "rdec" }] });
      const k = document.querySelector(`.kartica[data-polje="${m[0].polje}"]`); if (k) { k.scrollIntoView({ block: "center", behavior: "smooth" }); k.focus({ preventScroll: true }); }
      return;
    }
    const nepotrjeni = POLJA.filter((d) => !S.predlogi.some((p) => p.polje === d.polje && POTRJENA.includes(p.stanje)) && S.predlogi.some((p) => p.polje === d.polje && p.stanje === "predlagano"));
    const konflikti = S.predlogi.filter((p) => p.nasprotuje_potrjenemu && p.stanje === "predlagano");
    const vrednosti = {};
    POLJA.forEach((d) => { const p = S.predlogi.find((x) => x.polje === d.polje && POTRJENA.includes(x.stanje)); if (p) vrednosti[d.polje] = p.vrednost; });
    const enota = ENOTE.find((e) => e.id === vrednosti.predlagana_enota);
    const ok = await AID.modal({
      naslov: `Pošlji nalogo enoti ${enota ? enota.id : ""}?`,
      html: `<p>Poslani bodo <b>samo potrjeni</b> podatki (${Object.keys(vrednosti).length}).</p>
        ${nepotrjeni.length ? `<p style="color:var(--rdeca)"><b>Nepotrjeni AI predlogi ne bodo poslani:</b></p><ul>${nepotrjeni.map((d) => `<li>${esc(d.oznaka)}</li>`).join("")}</ul>` : ""}
        ${konflikti.length ? `<p style="color:var(--rdeca)"><b>${konflikti.length} AI opozoril nasprotuje potrjenim podatkom.</b></p>` : ""}
        <p><b>Povzetek naloge:</b></p><ul><li>Prioriteta ${esc(PRIORITETE[vrednosti.prioriteta])}</li><li>${esc(vrednosti.naslov)}${vrednosti.orientacija ? " – " + esc(vrednosti.orientacija) : ""}</li><li>${esc(VRSTE[vrednosti.vrsta_dogodka] || "")} ${vrednosti.stanje_pacienta ? "· " + esc(vrednosti.stanje_pacienta) : ""}</li></ul>`,
      gumbi: [{ besedilo: "Nazaj", vrednost: false }, { besedilo: "ODOBRI IN POŠLJI", vrednost: true, razred: "rdec" }],
    });
    if (!ok) return;
    const naloga = {
      id: "N-" + (S.klic ? S.klic.id.replace("k-", "") : Date.now()),
      tip: "nujna_voznja", poslano_ob: Date.now(), poslal: AID.UPORABNIK.ime,
      vrsta: VRSTE[vrednosti.vrsta_dogodka] || "", opis: vrednosti.opis || "", naslov: vrednosti.naslov, orientacija: vrednosti.orientacija || "",
      lokacija: vrednosti.lokacija, stanje_pacienta: vrednosti.stanje_pacienta || "", stevilo: vrednosti.stevilo_poskodovanih ?? null,
      prioriteta: vrednosti.prioriteta, oprema: vrednosti.potrebna_oprema || [], kontakt: vrednosti.kontakt || "", enota: vrednosti.predlagana_enota,
    };
    AID.pisi("aid_naloga", naloga);
    AID.poslji("naloga", naloga);
    AID.revizija("Naloga", naloga.id, "odobri_in_poslji", null, naloga);
    S.poslano = true;
    $("obrazec-telo").closest(".obrazec").classList.add("poslano");
    izrisiNapredek();
    AID.obvestilo(`Poslano enoti ${naloga.enota}`, "Poslani so samo potrjeni podatki.");
  };

  /* ---------- mini karta (Leaflet) ---------- */
  let karta = null, markerPredlog = null, markerPotrjen = null;
  function zacniKarto() {
    if (!window.L) { $("mini-karta").innerHTML = '<div style="padding:12px;font-size:13px;color:var(--besedilo-2)">Karta ni na voljo – vpišite koordinate ročno.</div>'; return; }
    karta = L.map("mini-karta", { zoomControl: false, attributionControl: true }).setView([46.0569, 14.5058], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { maxZoom: 19, attribution: "© OpenStreetMap, © CARTO" }).addTo(karta);
    L.circleMarker([46.05452, 14.52218], { radius: 6, color: "#1D2228", fillColor: "#fff", fillOpacity: 1, weight: 3 }).addTo(karta).bindTooltip("UKC Ljubljana");
    karta.on("click", (e) => {
      if (!S.klic && !S.predlogi.length) return;
      shraniUreditev("lokacija", { lat: e.latlng.lat, lon: e.latlng.lng });
      $("mini-karta").classList.remove("izbiranje");
      izrisiVse.prisili = true; izrisiVse("lokacija");
    });
  }
  function posodobiMiniKarto() {
    if (!karta) return;
    const zp = S.predlogi.filter((p) => p.polje === "lokacija");
    const pot = zp.find((p) => POTRJENA.includes(p.stanje));
    const odp = zp.filter((p) => p.stanje === "predlagano").sort((a, b) => b.zaupanje - a.zaupanje)[0];
    if (markerPredlog) { markerPredlog.remove(); markerPredlog = null; }
    if (markerPotrjen) { markerPotrjen.remove(); markerPotrjen = null; }
    if (odp) markerPredlog = L.circleMarker([odp.vrednost.lat, odp.vrednost.lon], { radius: 10, color: odp.zaupanje < 0.5 ? "#C98A00" : "#5A6270", dashArray: "4 3", weight: 2, fillOpacity: 0.15 }).addTo(karta).bindTooltip("AI predlog (nepotrjeno)");
    if (pot) markerPotrjen = L.marker([pot.vrednost.lat, pot.vrednost.lon], { icon: L.divIcon({ className: "", html: '<div style="width:22px;height:22px;border-radius:50% 50% 50% 0;background:#8A1525;transform:rotate(-45deg);border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4)"></div>', iconSize: [22, 22], iconAnchor: [11, 22] }) }).addTo(karta).bindTooltip("Potrjena lokacija");
    const t = pot || odp;
    if (t && !enako(posodobiMiniKarto.zadnja, t.vrednost)) { karta.setView([t.vrednost.lat, t.vrednost.lon], 15); posodobiMiniKarto.zadnja = t.vrednost; }
  }

  /* ---------- revizija ---------- */
  $("gumb-revizija").onclick = () => {
    const sled = AID.beri("aid_revizija", []).slice().reverse();
    const kratko = (v) => { if (v == null) return "–"; const s = typeof v === "string" ? v : JSON.stringify(v); return s.length > 70 ? s.slice(0, 70) + "…" : s; };
    $("revizija-tabela").innerHTML = sled.map((r) => {
      const p = r.objekt === "Predlog" ? S.predlogi.find((x) => x.id === r.objekt_id) : null;
      return `<tr><td class="mono">${AID.uraZdaj(new Date(r.cas))}</td><td>${esc(r.uporabnik)}</td><td>${esc(r.akcija)}</td><td>${esc(p ? oznakaPolja(p.polje) : r.objekt + " " + r.objekt_id)}</td><td>${esc(kratko(r.stara_vrednost))} → ${esc(kratko(r.nova_vrednost))}</td></tr>`;
    }).join("") || `<tr><td colspan="5">Ni vnosov.</td></tr>`;
    $("revizija-predal").hidden = false;
    $("revizija-zapri").focus();
  };
  $("revizija-zapri").onclick = () => ($("revizija-predal").hidden = true);

  /* ---------- demo izpadi ---------- */
  $("izpad-ai").onchange = (e) => {
    S.izpadAi = e.target.checked;
    $("st-ai").textContent = S.izpadAi ? "ni na voljo – ročni vnos" : "aktiven";
    $("st-ai").classList.toggle("napaka", S.izpadAi);
    $("st-ai").classList.toggle("ok", !S.izpadAi);
    if (S.izpadAi) AID.obvestilo("AI poslušalec ni na voljo", "Vmesnik deluje naprej – podatke vnesite ročno.", true);
  };
  $("izpad-prepis").onchange = (e) => {
    S.izpadPrepis = e.target.checked;
    $("st-prepis").textContent = S.izpadPrepis ? "ni na voljo – tipkajte" : "v živo";
    $("st-prepis").classList.toggle("napaka", S.izpadPrepis);
    $("rocni-zapis").classList.toggle("poudarjeno", S.izpadPrepis);
    if (S.izpadPrepis) { AID.obvestilo("Prepis ni na voljo", "Tipkajte povzetek pogovora – AI dela iz zapisanega.", true); $("rocni-vnos").focus(); }
  };

  /* ---------- zagon ---------- */
  izrisiVrsto();
  zacniKarto();
  izrisiVse();
})();
