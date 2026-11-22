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
    { polje: "predlagana_enota", oznaka: "Vozilo za odhod", tip: "enota", obvezno: true, kljucno: true },
    { polje: "dodatne_enote", oznaka: "Dodatne enote (večja nesreča)", tip: "enote", kljucno: true, leSPredlogom: true },
    { polje: "kontakt", oznaka: "Kontakt klicatelja", tip: "besedilo" },
  ];
  const KLJUCNA = ["prioriteta", "predlagana_enota", "dodatne_enote"];
  // kartica »Dodatne enote« je vidna le, ko jo AI predlaga ali jo dispečer vnese (večja nesreča)
  const vidno = (d) => !d.leSPredlogom || predlogiPolja(d.polje).some((p) => p.stanje !== "zastarelo") || S.urejanje[d.polje]
    || (d.polje === "dodatne_enote" && !!S.klic);  // dispečer lahko več enot doda že od začetka klica
  // število poškodovanih (potrjeno ali predlog AI) -> koliko enot sme dispečer poslati (vozilo na poškodovanega + motorist + helikopter)
  function stPoskodovanih() {
    const p = potrjenZa("stevilo_poskodovanih") || odprtiZa("stevilo_poskodovanih")[0];
    return p ? Number(p.vrednost) || 0 : 0;
  }
  const vrednostPolja = (polje) => { const p = potrjenZa(polje) || odprtiZa(polje)[0]; return p ? p.vrednost : null; };
  // enako kot strežnik (Center.najvec_dodatnih): ≥ 2 poškodovanih -> poškodovani + 1; prometna nesreča s P1 -> vsaj motorist
  const najvecDodatnih = () => Math.max(stPoskodovanih() >= 2 ? stPoskodovanih() + 1 : 0,
    vrednostPolja("vrsta_dogodka") === "prometna_nesreca" && Number(vrednostPolja("prioriteta")) === 1 ? 1 : 0);
  const glavnaEnota = () => (potrjenZa("predlagana_enota") || odprtiZa("predlagana_enota")[0] || {}).vrednost;
  const dodatne = () => { const p = potrjenZa("dodatne_enote") || odprtiZa("dodatne_enote")[0]; return (p && Array.isArray(p.vrednost)) ? p.vrednost : []; };
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
    S.cakajoci.forEach((k, i) => vrstice.push(`<li class="${!S.klic && i === 0 ? "naslednji" : ""} ${S.klic ? "" : "izbirno"}" data-cakajoci="${esc(k.id)}"
      title="${S.klic ? "" : "Klik = sprejmi ta klic"}"><span class="stevilka">${esc(k.stevilka)}</span>
      <span class="cakanje">${AID.trajanje(zdaj - k.prispel)}</span><span class="podatek">${esc(k.omrezje || "")}</span>
      <span class="podatek">${k.demo === "zvok" ? "zvok · čaka" : "čaka"}</span></li>`));
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
        if (AID.ustaviZvok) AID.ustaviZvok();
        const k = await AID.api(`/klic/${S.klic.id}/koncaj`, { metoda: "POST" });
        S.klic = { ...S.klic, ...k };
        izrisiTelefon();
        return;
      }
      await sprejmiKlic(S.cakajoci[0]);
    } catch (e) { napaka(e); }
  };
  $("vrsta-klicev").addEventListener("click", (e) => {
    const li = e.target.closest("[data-cakajoci]");
    if (!li || (S.klic && S.klic.stanje === "v_teku")) return;
    sprejmiKlic(S.cakajoci.find((q) => q.id === li.dataset.cakajoci)).catch(napaka);
  });
  async function sprejmiKlic(q) {
    {
      if (!q) return;
      const hitrost = Number($("hitrost").value);
      // zvočni klic: posnetki iz mape poslusalec -> Whisper -> AI; ostali klici: dispečer zapisuje ročno
      const k = q.demo === "zvok" ? await AID.api("/demo/klic-zvok", { metoda: "POST", telo: { hitrost } })
        : await AID.api("/klic/zacni", { metoda: "POST", telo: { cakajoci_id: q.id } });
      nastaviKlic({ ...k, omrezje: q.omrezje, segmenti: [], predlogi: [] });
      if (AID.ponoviZgodnje) AID.ponoviZgodnje(k.id);
      S.cakajoci = await AID.api("/cakajoci");
      izrisiVrsto(); izrisiTelefon();
    }
  }
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
      case "dodatne_enote": return (Array.isArray(v) ? v : [v]).map((id) => { const e = S.enote.find((x) => x.id === id); return e ? `${e.id} · ${e.ime}` : id; }).join(", ") || "brez";
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
      case "enote": {
        const glavna = (potrjenZa("predlagana_enota") || odprtiZa("predlagana_enota")[0] || {}).vrednost;
        const izbrane = Array.isArray(v) ? v : [];
        return `<div id="${id}" class="izbira-enot">${S.enote.filter((e) => !["okvara", "ni_na_voljo"].includes(e.stanje) && e.id !== glavna).map((e) =>
          `<label><input type="checkbox" value="${esc(e.id)}" ${izbrane.includes(e.id) ? "checked" : ""}> ${esc(e.id)} · ${esc(e.ime)} <span class="tip-vozila">${e.stanje === "prosto" ? "prosto" : "na nalogi"}</span></label>`).join("")}</div>`;
      }
      case "enota": return `<select id="${id}">${S.enote.filter((e) => e.stanje !== "okvara").map((e) => `<option value="${e.id}" ${e.id === v ? "selected" : ""}>${e.id} · ${e.ime} (${e.stanje === "prosto" ? "prosto" : "na nalogi"})</option>`).join("")}</select>`;
      default: return def.vec ? `<textarea id="${id}">${esc(v || "")}</textarea>` : `<input id="${id}" value="${esc(v || "")}">`;
    }
  }
  function preberiUrejevalnik(def) {
    const el = $("ur-" + def.polje);
    if (!el) return undefined;
    if (def.tip === "enote") return [...el.querySelectorAll("input:checked")].map((x) => x.value);
    const t = el.value.trim();
    if (t === "") return null;
    switch (def.tip) {
      case "prioriteta": case "stevilo": return Number(t);
      case "seznam": return t.split(",").map((x) => x.trim()).filter(Boolean);
      case "lokacija": { const m = t.match(/(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)/); return m ? { lat: Number(m[1]), lon: Number(m[2]) } : undefined; }
      default: return t;
    }
  }
  /* vozila za odhod: dispečer sam izbere – prosta najprej, po času prihoda s prometom */
  function enoteHtml(p) {
    let vse = (p && p.dodatno && p.dodatno.enote) || [];
    const lok = potrjenZa("lokacija") || odprtiZa("lokacija")[0];
    if (!vse.length && lok) { // zadnja rezerva: ocena v brskalniku
      vse = S.enote.filter((e) => !["okvara", "ni_na_voljo"].includes(e.stanje)).map((e) => ({ enota_id: e.id, tip: e.tip, stanje: e.stanje, ...AID.ocenaEta(e, lok.vrednost.lat, lok.vrednost.lon) }));
    }
    if (!vse.length) return '<p class="pojasnilo">Najprej določite lokacijo – nato se izračunajo časi prihoda vozil.</p>';
    vse = vse.slice().sort((a, b) => (a.stanje !== "prosto") - (b.stanje !== "prosto") || a.cas_s - b.cas_s);
    const izbrana = p && POTRJENA.includes(p.stanje) ? p.vrednost : null;
    const predlog = p && !POTRJENA.includes(p.stanje) ? p.vrednost : null;
    const najblizja = vse.find((x) => x.stanje === "prosto");
    const izracunano = p && p.dodatno && p.dodatno.izracunano ? " · " + AID.uraKratko(new Date(p.dodatno.izracunano * 1000)) : "";
    const glava = `<div class="najblizja">Časi prihoda s prometom${izracunano}
      ${S.klic ? '<button class="gumb majhen" data-osvezi-najblizjo>↻ Osveži</button>' : ""}</div>`;
    return glava + `<table class="enote-tabela izbira-vozila" aria-label="Vozila za odhod"><tbody>${vse.map((x) => {
      const e = S.enote.find((y) => y.id === x.enota_id) || {};
      const prosto = x.stanje === "prosto";
      const kak = x.kakovost === "natančno" ? "natancno" : x.kakovost === "ocena" ? "ocena" : "";
      const voznik = e.voznik ? (e.voznik.prijavljen ? '<span class="voznik-da" title="Voznik je prijavljen v aplikaciji – nalogo prejme samodejno">● aplikacija</span>'
        : '<span class="voznik-ne" title="Voznik ni prijavljen – obvestite ga po radiu">○ brez aplikacije</span>') : "";
      const oznake = [x === najblizja ? '<span class="znacka temna">najbližja prosta</span>' : "", x.enota_id === predlog ? '<span class="znacka">predlog AI</span>' : ""].join(" ");
      return `<tr class="${x.enota_id === izbrana ? "izbrana" : ""} ${prosto ? "" : "zasedena"}">
        <td><b>${esc(x.enota_id)}</b> <span class="tip-vozila">${esc(x.tip)}${prosto ? "" : " · na nalogi"}</span><br>${oznake} ${voznik}</td>
        <td class="eta">${Math.max(1, Math.round(x.cas_s / 60))} min</td><td>${(x.razdalja_m / 1000).toFixed(1)} km</td>
        <td><span class="kakovost ${kak}">${esc(x.kakovost)}</span></td>
        <td>${x.enota_id === izbrana ? '<span class="znacka temna">izbrano</span>'
          : dodatne().includes(x.enota_id) ? `<button class="gumb majhen" data-dodatna-enota="${esc(x.enota_id)}" ${S.klic && !S.klic.naloga_id ? "" : "disabled"} title="Odstrani iz dodatnih enot">− dodatna</button>`
          : `<button class="gumb majhen ${prosto ? "temen" : ""}" data-izberi-enoto="${esc(x.enota_id)}" ${S.klic && !S.klic.naloga_id ? "" : "disabled"}>Izberi</button>`
            + (x.enota_id !== glavnaEnota() ? ` <button class="gumb majhen" data-dodatna-enota="${esc(x.enota_id)}" ${S.klic && !S.klic.naloga_id ? "" : "disabled"} title="Pošlji tudi to enoto (več vozil)">+ dodatna</button>` : "")}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function obsegHtml(p) {
    const d = (p && p.dodatno) || {};
    const manjka = Object.entries(d.manjka || {}).filter(([, n]) => n > 0).map(([t, n]) => `${n}× ${t}`);
    return (d.razlog ? `<p class="pojasnilo">⚠ Večja nesreča: ${esc(d.razlog)}.</p>` : "")
      + (manjka.length ? `<p class="pojasnilo" style="color:var(--rdeca)">Ni dovolj prostih enot – manjka še ${esc(manjka.join(", "))}.</p>` : "");
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
      if (def.tip === "enota") telo += enoteHtml(potrjen);
      if (def.tip === "enote") telo += obsegHtml(potrjen);
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
      if (def.tip === "enota") telo += enoteHtml(p);
      if (def.tip === "enote") telo += obsegHtml(p);
      gumbi = `<button class="gumb sprejmi" data-akcija="sprejmi" data-id="${p.id}">Sprejmi <kbd>⏎</kbd></button>
        <button class="gumb" data-akcija="uredi">Uredi <kbd>E</kbd></button>
        <button class="gumb zavrni" data-akcija="zavrni" data-id="${p.id}">Zavrni <kbd>Del</kbd></button>`;
      telo += razlogHtml(p);
    } else {
      razred += " manjka";
      telo = `<div class="vrednost prazna">manjka</div>`;
      if (def.tip === "enota") telo += enoteHtml(null);
      gumbi = `<button class="gumb" data-akcija="uredi">+ Vnesi ročno <kbd>E</kbd></button>`;
    }
    if (def.tip === "lokacija" && S.klic && !ureja) {
      // približna lokacija (npr. le središče kraja) -> dispečer klikne točno mesto na mini karti
      gumbi += `<button class="gumb${glavni && (glavni.zaupanje ?? 1) < 0.5 ? " rdec-obroba" : ""}" data-doloci-na-karti>📍 Določi na karti</button>`;
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
      if (!vidno(def)) { if (star) star.remove(); return; }
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

  const stPotrjenih = () => POLJA.filter((d) => vidno(d) && potrjenZa(d.polje)).length;
  function kandidatiSprejmiVse() {
    return POLJA.filter((d) => !KLJUCNA.includes(d.polje) && !potrjenZa(d.polje)).map((d) => {
      const od = odprtiZa(d.polje);
      return od.length === 1 && (od[0].zaupanje || 0) >= 0.8 ? od[0] : null;
    }).filter(Boolean);
  }
  const manjkajoca = () => POLJA.filter((d) => d.obvezno && !potrjenZa(d.polje));
  function izrisiNapredek() {
    const n = stPotrjenih();
    const vsa = POLJA.filter(vidno).length;
    $("napredek-besedilo").textContent = `${n} od ${vsa} polj potrjenih`;
    $("napredek-trak").style.width = (n / vsa) * 100 + "%";
    const k = kandidatiSprejmiVse();
    $("sprejmi-vse").disabled = !k.length || S.urediVse || !S.klic;
    $("sprejmi-vse").textContent = `Sprejmi vse z zaupanjem ≥ 0,8${k.length ? " (" + k.length + ")" : ""}`;
    const m = manjkajoca();
    const el = $("manjka-opozorilo");
    if (S.klic && S.klic.naloga_id) {
      const p = S.posiljanje || {};
      const e = S.enote.find((x) => x.id === p.enota) || {};
      const STAT = { prejeto: "voznik je potrdil prejem", na_poti: "na poti", na_prizoriscu: "na kraju dogodka", pacient_nalozen: "pacient naložen",
        dostavljeno: "dostavljeno", opravljeno: "opravljeno" };
      el.textContent = p.stanje === "predlog" ? "✓ Odobreno – plan čaka potrditev (do takrat voznik ne prejme naloge)."
        : p.stanje === "poslano" ? `✓ Poslano ${p.enota || ""}${e.voznik && !e.voznik.prijavljen ? " – voznik nima aplikacije (radio)" : " – čakam potrditev prejema"}`
        : p.stanje ? `✓ ${p.enota}: ${STAT[p.stanje] || p.stanje}` : "✓ Odobreno.";
      el.style.color = "";
    }
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
    if (e.target.closest("[data-doloci-na-karti]")) {
      const mk = $("mini-karta");
      mk.scrollIntoView({ behavior: "smooth", block: "center" });
      mk.classList.add("izbiranje");
      const lok = potrjenZa("lokacija") || odprtiZa("lokacija")[0];
      if (karta && lok) karta.setView([lok.vrednost.lat, lok.vrednost.lon], 16); // približaj na ocenjeno mesto
      AID.obvestilo("Določite lokacijo", "Kliknite točno mesto dogodka na karti.");
      return;
    }
    if (e.target.closest("[data-osvezi-najblizjo]")) {
      try {
        (await AID.api(`/klic/${S.klic.id}/najblizja`, { metoda: "POST" })).forEach(upsert);
        potKljuc = null; izrisiVse("predlagana_enota");
        AID.obvestilo("Časi prihoda osveženi", "Če je zdaj hitrejša druga enota, je prikazana kot nov predlog.");
      } catch (e2) { napaka(e2); }
      return;
    }
    const dod = e.target.closest("[data-dodatna-enota]");
    if (dod) {
      const id = dod.dataset.dodatnaEnota, zdaj = dodatne();
      const novo = zdaj.includes(id) ? zdaj.filter((x) => x !== id) : [...zdaj, id];
      if (novo.length > najvecDodatnih() && novo.length > zdaj.length)
        AID.obvestilo("Več enot", `Dodanih ${novo.length} enot; število poškodovanih (${stPoskodovanih() || "ni znano"}) jih utemelji ${najvecDodatnih()}. Odločitev je vaša.`, false, 5000);
      try { await shraniVrednost("dodatne_enote", novo); izrisiVse.prisili = true; izrisiVse("predlagana_enota"); } catch (e2) { napaka(e2); }
      return;
    }
    const en = e.target.closest("[data-izberi-enoto]");
    if (en) {
      try {
        const id = en.dataset.izberiEnoto;
        await shraniVrednost("predlagana_enota", id);
        if (dodatne().includes(id)) await shraniVrednost("dodatne_enote", dodatne().filter((x) => x !== id)); // glavna ni hkrati dodatna
        izrisiVse.prisili = true; izrisiVse("predlagana_enota");
      } catch (e2) { napaka(e2); }
      return;
    }
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
    const dod = (v.dodatne_enote || []).filter((x) => x !== v.predlagana_enota); // glavna ni hkrati dodatna (tudi strežnik)
    const ok = await AID.modal({
      naslov: dod.length ? `Odobri in pošlji ${1 + dod.length} enot: ${[v.predlagana_enota, ...dod].join(", ")}?`
        : `Odobri in pošlji enoti ${v.predlagana_enota}?`,
      html: `<p>Uporabljeni bodo <b>samo potrjeni</b> podatki (${Object.keys(v).length}). Planer pripravi predlog plana, ki ga potrdite v pogledu »Plan in karta«.</p>
        ${nepotrjeni.length ? `<p style="color:var(--rdeca)"><b>Nepotrjeni AI predlogi ne bodo uporabljeni:</b></p><ul>${nepotrjeni.map((d) => `<li>${esc(d.oznaka)}</li>`).join("")}</ul>` : ""}
        ${konflikti.length ? `<p style="color:var(--rdeca)"><b>${konflikti.length} AI opozoril nasprotuje potrjenim podatkom.</b></p>` : ""}
        ${dod.length > najvecDodatnih() ? `<p style="color:var(--rdeca)"><b>Pošiljate ${dod.length} dodatnih enot; število poškodovanih (${stPoskodovanih() || "ni znano"}) jih utemelji ${najvecDodatnih()}.</b> Zabeleženo bo v revizijski sledi.</p>` : ""}
        <p><b>Povzetek:</b></p><ul><li>Prioriteta ${esc(PRIORITETE[v.prioriteta])}</li><li>${esc(v.naslov)}${v.orientacija ? " – " + esc(v.orientacija) : ""}</li>
        <li>${esc(VRSTE[v.vrsta_dogodka] || "")} ${v.stanje_pacienta ? "· " + esc(v.stanje_pacienta) : ""}</li></ul>`,
      gumbi: [{ besedilo: "Nazaj", vrednost: false }, { besedilo: "ODOBRI IN POŠLJI", vrednost: true, razred: "rdec" }],
    });
    if (!ok) return;
    try {
      $("odobri").disabled = true;
      $("odobri").textContent = "PLANER RAČUNA …";
      const r = await AID.api(`/klic/${S.klic.id}/odobri`, { metoda: "POST" });
      S.klic.naloga_id = r.naloga.id;
      S.posiljanje = { naloga_id: r.naloga.id, enota: v.predlagana_enota, plan_id: r.plan_predlog && r.plan_predlog.id, stanje: "predlog" };
      $("obrazec").classList.add("poslano");
      izrisiNapredek();
      if (r.opozorilo) AID.obvestilo("Več enot, kot jih utemelji število poškodovanih", r.opozorilo + " Zabeleženo v revizijski sledi.", true, 8000);
      if ((r.dodatne_naloge || []).length) AID.obvestilo("Večja nesreča", `Poslanih ${1 + r.dodatne_naloge.length} enot: ${[v.predlagana_enota, ...r.dodatne_naloge.map((n) => n.dodeljena_enota)].join(", ")}.`);
      if (r.plan_predlog) await potrdiPlanTukaj(r.plan_predlog, r.naloga);
    } catch (e) { napaka(e); izrisiNapredek(); }
    $("odobri").textContent = "ODOBRI IN POŠLJI";
  };

  document.addEventListener("input", (e) => { if (e.target.id === "navodilo-ekipi") S.navodilo = e.target.value.trim(); });

  /* Potrditev plana kar v pogledu klica: brez skritega drugega koraka (sicer voznik ne prejme ničesar). */
  async function potrdiPlanTukaj(plan, naloga) {
    const korak = Object.entries(plan.koraki_po_enoti).flatMap(([eid, ks]) => ks.map((k) => ({ ...k, eid }))).find((k) => k.naloga_id === naloga.id);
    const e = S.enote.find((x) => x.id === (korak && korak.eid)) || {};
    const prihod = korak ? `prihod ob ${AID.uraKratko(new Date(korak.eta * 1000))} (čez ${Math.max(1, Math.round((korak.eta - Date.now() / 1000) / 60))} min)` : "ni dodeljeno";
    const druge = (plan.primerjava.spremenjene_dodelitve || []).filter((x) => x.naloga_id !== naloga.id);
    const ok = await AID.modal({
      naslov: `Pošlji ${korak ? korak.eid : "nalogo"} na ${naloga.prevzem.naslov}?`,
      html: `<p><b>${esc(korak ? korak.eid : "—")}</b> · ${esc(prihod)}</p>
        <p>${e.voznik && e.voznik.prijavljen ? "Voznik je prijavljen v aplikaciji: nalogo, pot in navodila prejme takoj, ko potrdite plan."
          : '<b style="color:var(--rdeca)">Voznik nima prijavljene aplikacije – po potrditvi ga obvestite po radiu.</b>'}</p>
        ${druge.length ? `<p>Spremembe pri drugih vozilih (${druge.length}):</p><ul>${druge.slice(0, 5).map((x) => `<li>${esc(x.naloga_id)}: ${esc(x.prej || "—")} → ${esc(x.potem || x.opomba || "—")}</li>`).join("")}</ul>` : "<p>Drugi razporedi ostanejo enaki.</p>"}
        <p class="pojasnilo">${esc(plan.primerjava.razlaga)}</p>
        <label class="navodilo-ekipi">Navodilo ekipi (neobvezno – voznik ga dobi na zaslon)
          <textarea id="navodilo-ekipi" rows="2" maxlength="500" placeholder="npr. Vhod z dvorišča, sosed čaka na cesti.">${esc(S.navodilo || "")}</textarea></label>`,
      gumbi: [{ besedilo: "Pokaži v planu", vrednost: "plan" }, { besedilo: "POTRDI PLAN IN POŠLJI", vrednost: "potrdi", razred: "rdec" }],
    });
    if (ok === "plan") { AID.preklopi("plan"); return; }
    if (ok !== "potrdi") { AID.obvestilo("Plan še ni potrjen", "Potrdite ga v pogledu »Plan in karta« – do takrat voznik ne prejme naloge.", true, 7000); return; }
    try {
      await AID.api(`/plan/${plan.id}/potrdi`, { metoda: "POST" });
      S.posiljanje = { ...(S.posiljanje || {}), enota: korak && korak.eid, stanje: "poslano" };
      if (S.navodilo && korak) { // navodilo dispečerja -> zaslon voznika (sporočilo z »Prebrano«)
        await AID.api(`/enote/${encodeURIComponent(korak.eid)}/sporocilo`, { metoda: "POST", telo: { koda: null, besedilo: S.navodilo } });
        S.navodilo = "";
      }
      izrisiNapredek();
      AID.obvestilo(`Poslano ${korak ? korak.eid : ""}`, e.voznik && e.voznik.prijavljen ? "Čakam potrditev prejema voznika." : "Obvestite voznika po radiu.");
    } catch (e2) { napaka(e2); }
  }

  /* ---------- mini karta ---------- */
  let karta = null, mPredlog = null, mPotrjen = null, zadnjaLok = null;
  function zacniKarto() {
    if (!window.L) { $("mini-karta").innerHTML = '<div style="padding:12px;font-size:13px;color:var(--besedilo-2)">Karta ni na voljo – vpišite koordinate ročno.</div>'; return; }
    karta = L.map("mini-karta", { zoomControl: false }).setView([46.0569, 14.5058], 12);
    AID.podlagaKarte(karta);
    karta.on("click", async (e) => {
      if (!S.klic) return;
      try {
        await shraniVrednost("lokacija", { lat: +e.latlng.lat.toFixed(6), lon: +e.latlng.lng.toFixed(6) });
        $("mini-karta").classList.remove("izbiranje");
        izrisiVse.prisili = true; izrisiVse("lokacija");
        AID.obvestilo("Lokacija določena", "Časi prihoda vozil se preračunajo.");
      }
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
    narisiPotEnote(t);
  }

  /* pot najbližje (predlagane) enote do kraja dogodka – po cestah, s prometom, če je na voljo */
  let potSloj = null, potKljuc = null;
  async function narisiPotEnote(lok) {
    const en = potrjenZa("predlagana_enota") || odprtiZa("predlagana_enota")[0];
    const kljuc = lok && en ? [en.vrednost, lok.vrednost.lat, lok.vrednost.lon, en.dodatno && en.dodatno.izracunano].join("|") : null;
    if (kljuc === potKljuc) return;
    potKljuc = kljuc;
    if (potSloj) { potSloj.remove(); potSloj = null; }
    if (!kljuc) return;
    let r;
    try {
      r = await AID.api(`/pot?enota_id=${encodeURIComponent(en.vrednost)}&lat=${lok.vrednost.lat}&lon=${lok.vrednost.lon}`);
    } catch (_) { // brez strežnika: ravna črta in ocena v brskalniku
      const e = S.enote.find((x) => x.id === en.vrednost);
      if (!e) return;
      r = { enota_id: e.id, od: [e.lat, e.lon], geometrija: [[e.lat, e.lon], [lok.vrednost.lat, lok.vrednost.lon]], ...AID.ocenaEta(e, lok.vrednost.lat, lok.vrednost.lon) };
    }
    if (kljuc !== potKljuc) return; // med čakanjem se je izbira spremenila
    const potrjena = POTRJENA.includes(en.stanje);
    const zracna = r.tip === "helikopter";
    const ravna = !zracna && r.kakovost === "ocena";
    const opis = `${r.enota_id} · ${Math.max(1, Math.round(r.cas_s / 60))} min · ${(r.razdalja_m / 1000).toFixed(1)} km · ${zracna ? "zračna pot" : r.kakovost}`;
    if (ravna) { // pot po cestah trenutno ni na voljo – poskusi znova
      const k = kljuc;
      setTimeout(() => { if (potKljuc === k) { potKljuc = null; posodobiMiniKarto(); } }, 15000);
    }
    potSloj = L.layerGroup([
      L.polyline(r.geometrija, { color: "#fff", weight: 8, opacity: 0.9 }),
      L.polyline(r.geometrija, { color: "#8A1525", weight: zracna ? 3 : 4.5, dashArray: zracna ? "2 8" : potrjena ? null : "9 7" }).bindTooltip(opis, { sticky: true }),
      L.marker(r.od, { icon: L.divIcon({ className: "", iconSize: [40, 22], iconAnchor: [20, 11],
        html: `<div class="oznaka-enote">${esc(r.enota_id)}</div>` }) }).bindTooltip(opis),
    ]).addTo(karta);
    karta.fitBounds(L.latLngBounds([...r.geometrija, [lok.vrednost.lat, lok.vrednost.lon]]), { padding: [18, 18], maxZoom: 16 });
    const n = document.getElementById("pot-povzetek");
    if (n) n.textContent = (potrjena ? "Pot potrjene enote: " : "Pot predlagane enote: ") + opis +
      (ravna ? " – ravna črta: pot po cestah trenutno ni na voljo, poskušam znova …" : "");
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
  const OPIS_MODELA = { ni_zagnan: "—", nalagam: "nalagam Whisper …", pripravljen: "Whisper pripravljen", napaka: "ni na voljo", ni_namescen: "Whisper ni nameščen" };
  function nastaviModel(m) {
    const el = $("st-prepis");
    if (!m || (S.klic && S.klic.stanje === "v_teku" && S.prepisVZivo)) return;
    el.textContent = m.stanje === "nalagam" && m.preneseno_mb ? `prenašam Whisper … ${m.preneseno_mb} MB` : (OPIS_MODELA[m.stanje] || m.stanje);
    el.title = m.razlog || ("Whisper " + (m.model || ""));
    el.className = "pika" + (m.stanje === "napaka" || m.stanje === "ni_namescen" ? " napaka" : "");
    if (m.stanje === "napaka") AID.obvestilo("Prepis govora", m.razlog, true, 8000);
  }
  AID.na("prepis_model", nastaviModel);
  AID.prijava.then(() => AID.api("/prepis/stanje")).then(nastaviModel).catch(() => {});

  // dogodki, ki pridejo, preden brskalnik ve za id sprejetega klica, se predvajajo ob nastaviKlic
  const zgodnji = [];
  const zaKasneje = (tip, p) => { zgodnji.push({ tip, p, t: Date.now() }); if (zgodnji.length > 20) zgodnji.shift(); };
  AID.ponoviZgodnje = (klicId) => zgodnji.splice(0).filter((x) => x.p.klic_id === klicId && Date.now() - x.t < 15000)
    .forEach((x) => (x.tip === "zvok" ? predvajajOdsek : prepisStanje)(x.p));

  // posnetki se predvajajo eden za drugim, vsak do konca (nikoli ne prekinemo prejšnjega)
  let zvok = null;
  const vrstaZvoka = [];
  function naslednjiZvok() {
    if (zvok || !vrstaZvoka.length) return;
    const o = vrstaZvoka.shift();
    if (!S.klic || o.klic_id !== S.klic.id || $("tel-utisaj").classList.contains("vklopljen")) return naslednjiZvok();
    zvok = new Audio(`/klic/${encodeURIComponent(o.klic_id)}/zvok/${encodeURIComponent(o.posnetek)}`);
    zvok.playbackRate = o.hitrost > 1 ? Math.min(4, o.hitrost) : 1;
    const konec = () => { zvok = null; naslednjiZvok(); };
    zvok.onended = konec; zvok.onerror = konec;
    zvok.play().catch((e) => { AID.obvestilo("Zvok klica", "Brskalnik ni dovolil predvajanja: " + e.message, true, 5000); konec(); });
  }
  function predvajajOdsek(o) {
    if (!S.klic || o.klic_id !== S.klic.id) return zaKasneje("zvok", o);
    $("st-prepis").textContent = `v živo (Whisper) ${o.st}/${o.skupaj}`;
    vrstaZvoka.push(o);
    naslednjiZvok();
  }
  AID.na("zvok_odsek", predvajajOdsek);
  AID.ustaviZvok = () => { vrstaZvoka.length = 0; if (zvok) { zvok.onended = null; zvok.pause(); zvok = null; } };
  $("tel-utisaj").addEventListener("click", () => { if ($("tel-utisaj").classList.contains("vklopljen")) AID.ustaviZvok(); });

  function prepisStanje(p) {
    if (!S.klic || p.klic_id !== S.klic.id) return zaKasneje("stanje", p);
    const el = $("st-prepis");
    S.prepisVZivo = p.stanje === "v_zivo";
    el.textContent = p.stanje === "nalagam" ? "nalagam Whisper …" : p.stanje === "konec" ? "končan (Whisper)"
      : p.stanje === "napaka" ? "ni na voljo – ročni zapis" : "v živo (Whisper)";
    el.className = "pika" + (p.stanje === "napaka" ? " napaka" : "");
    if (p.stanje === "konec" || p.stanje === "napaka") { S.prepisVZivo = false; if (p.stanje === "napaka") AID.ustaviZvok(); }
    if (p.stanje !== "konec") AID.obvestilo("Prepis govora", p.sporocilo, p.stanje === "napaka", 6000);
  }
  AID.na("prepis_stanje", prepisStanje);
  (AID.naPrerazporeditev = AID.naPrerazporeditev || []).push((nalogaId, enota) => {
    if (S.posiljanje && S.posiljanje.naloga_id === nalogaId) { S.posiljanje.enota = enota; S.posiljanje.stanje = "poslano"; izrisiNapredek(); }
  });
  AID.na("plan_aktiven", (p) => { if (S.posiljanje && S.posiljanje.plan_id === p.id && S.posiljanje.stanje === "predlog") { S.posiljanje.stanje = "poslano"; izrisiNapredek(); } });
  AID.na("voznik_potrditev", (x) => { if (S.posiljanje && x.enota_id === S.posiljanje.enota) { S.posiljanje.stanje = "prejeto"; izrisiNapredek(); } });
  AID.na("voznik_stanje", (x) => { if (S.posiljanje && x.naloga_id && x.naloga_id === S.posiljanje.naloga_id) { S.posiljanje.stanje = x.stanje; izrisiNapredek(); } });
  AID.na("voznik_prijava", () => AID.api("/enote").then((e) => { S.enote = e; izrisiVse(); }).catch(() => {}));
  AID.na("opozorilo", (o) => {
    if (o.vrsta === "ai") nastaviAi(o.na_voljo);
    AID.obvestilo(o.vrsta === "ai" ? "AI poslušalec" : "Opozorilo", o.sporocilo, true, 6000);
    if (o.vrsta === "geokodiranje") $("mini-karta").classList.add("izbiranje");
  });

  function nastaviKakovost(k) {
    const el = $("st-eta");
    el.textContent = k === "natančno" ? "natančno (s prometom)" : k === "brez prometa" ? "brez prometa" : k ? "ocena (ravne črte)" : "ocena v brskalniku";
    el.className = "pika" + (k === "natančno" ? "" : " napaka");
  }

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
      nastaviKakovost(st.kakovost_poti);
      setInterval(() => AID.api("/stanje?od=999999999").then((x) => nastaviKakovost(x.kakovost_poti)).catch(() => nastaviKakovost(null)), 15000);
    } catch (e) {
      S.enote = AID.beri("aid_enote", []); // zadnje znano stanje
      $("st-eta").textContent = "ocena v brskalniku"; $("st-eta").className = "pika napaka";
    }
    izrisiVrsto(); izrisiTelefon(); izrisiVse();
  }
  document.addEventListener("DOMContentLoaded", () => AID.prijava.then(zagon));
})();
