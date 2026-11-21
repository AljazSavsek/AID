/* AID – ekipe na terenu (pogled »Plan in karta«): eskalacije nepotrjenih planov, predlogi ekip
 * (okvara, ovira, prevoz z nujne vožnje, ni na voljo) v vzorcu Sprejmi / Uredi / Zavrni in sporočila.
 * Predlog ekipe ne spremeni ničesar, dokler ga dispečer ne sprejme; nov plan nato potrdi posebej.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);
  const E = { predlogi: [], eskalacije: [], sporocila: [], hitra: { dispecer: {}, voznik: {} }, enote: [], centri: [], urejanje: {} };
  const RAZLOG = { ni_potrditve: "ni potrdil v roku", voznik_ni_prijavljen: "voznik ni prijavljen", ni_signala: "naprava brez signala" };
  const OVIRE = { zapora: "zapora", nesreca: "nesreča", dela_na_cesti: "dela na cesti", poplava: "poplava", zastoj: "zastoj", drugo: "drugo" };
  const napaka = (e) => AID.obvestilo("Dejanje ni uspelo", e.message, true, 6000);
  const pred = (t) => { const s = Math.max(0, AID.streznikZdaj() - t); return s < 60 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`; };
  const ura = (t) => { const d = new Date(t * 1000); return AID.dvomestno(d.getHours()) + ":" + AID.dvomestno(d.getMinutes()); };
  const centerIme = (id) => (E.centri.find((c) => c.id === id) || {}).ime || id || "—";

  function opis(d) {
    const p = d.parametri || {};
    switch (d.tip) {
      case "okvara_vozila": return `Okvara vozila ${p.enota_id}${p.opis ? " – " + p.opis : ""}${p.tovor_ostane ? " (tovor ostane v vozilu)" : ""}`;
      case "ovira_na_cesti": return `Ovira: ${OVIRE[p.vrsta] || p.vrsta}${p.opis ? " – " + p.opis : ""} (${(+p.lokacija.lat).toFixed(5)}, ${(+p.lokacija.lon).toFixed(5)})`;
      case "prevoz_z_nujne_voznje": return `Pacient naložen (${p.naloga_id}) – prevoz v: ${centerIme(p.center_id)}`;
      case "enota_ni_na_voljo": return `${p.enota_id} ni na voljo${p.razlog ? " – " + p.razlog : ""}; naloge bo planer prerazporedil`;
      default: return d.tip;
    }
  }

  function urejevalnik(d) {
    const p = d.parametri || {};
    if (d.tip === "okvara_vozila") return `<label class="vrstica"><input type="checkbox" name="tovor_ostane" ${p.tovor_ostane ? "checked" : ""}> Tovor ostane v vozilu (nova naloga prevzema)</label>`;
    if (d.tip === "ovira_na_cesti") return `<label>Vrsta<select name="vrsta">${Object.entries(OVIRE).map(([k, v]) => `<option value="${k}" ${k === p.vrsta ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Opis<input name="opis" value="${esc(p.opis || "")}"></label>`;
    if (d.tip === "prevoz_z_nujne_voznje") return `<label>Bolnišnica<select name="center_id">${E.centri.map((c) => `<option value="${esc(c.id)}" ${c.id === p.center_id ? "selected" : ""}>${esc(c.ime)} · prosto ${c.sprejemna_mesta - c.zasedena}</option>`).join("")}</select></label>`;
    return `<label>Razlog<input name="razlog" value="${esc(p.razlog || "")}"></label>`;
  }

  function izrisi() {
    if (!$("ekipe-eskalacije")) return;
    $("ekipe-eskalacije").innerHTML = E.eskalacije.map((x) =>
      `<li class="eskalacija" data-enota="${esc(x.enota_id)}"><b>${esc(x.enota_id)}</b> · ${esc(RAZLOG[x.razlog] || x.razlog)} · plan čaka ${pred(x.ustvarjeno)}</li>`).join("");
    $("ekipe-predlogi").innerHTML = E.predlogi.map((d) => {
      const ur = E.urejanje[d.id];
      const telo = ur ? `<div class="urejevalnik obrazec-dogodka" data-ur="${esc(d.id)}">${urejevalnik(d)}</div>` : `<div class="vrednost">${esc(opis(d))}</div>`;
      const gumbi = ur
        ? `<button class="gumb sprejmi" data-ek="spremeni" data-id="${esc(d.id)}">Sprejmi spremenjeno</button><button class="gumb" data-ek="preklici" data-id="${esc(d.id)}">Prekliči</button>`
        : `<button class="gumb sprejmi" data-ek="sprejmi" data-id="${esc(d.id)}">Sprejmi</button><button class="gumb" data-ek="uredi" data-id="${esc(d.id)}">Uredi</button><button class="gumb zavrni" data-ek="zavrni" data-id="${esc(d.id)}">Zavrni</button>`;
      return `<article class="kartica dogodek negotovo"><div class="k-glava"><span class="oznaka">Predlog ekipe · ${esc(d.enota_id)}</span><span class="desno">${pred(d.ustvarjeno)}</span></div>
        ${telo}<div class="k-gumbi">${gumbi}</div>
        <div class="razlog"><input data-razlog="${esc(d.id)}" placeholder="Razlog zavrnitve (neobvezno)"></div></article>`;
    }).join("");
    $("ekipe-sporocila").innerHTML = E.sporocila.slice(0, 15).map((m) =>
      `<li class="sporocilo ${m.smer} ${m.nujno ? "nujno" : ""}"><span>${ura(m.cas)}</span> <b>${m.smer === "od_voznika" ? esc(m.enota_id) + " →" : "→ " + esc(m.enota_id)}</b> ${esc(m.besedilo)}${m.smer === "od_dispecerja" ? (m.prebrano ? " · prebrano" : " · neprebrano") : ""}</li>`).join("");
    const izbrana = $("sp-enota").value;
    $("sp-enota").innerHTML = E.enote.map((e) => `<option ${e.id === izbrana ? "selected" : ""}>${esc(e.id)}</option>`).join("");
    if (!$("sp-hitro").options.length) {
      $("sp-hitro").innerHTML = `<option value="">— prosto besedilo —</option>` + Object.entries(E.hitra.dispecer).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    }
  }

  async function nalozi() {
    try {
      const [p, e, s, en, c] = await Promise.all([AID.api("/ekipe/predlogi"), AID.api("/ekipe/eskalacije"), AID.api("/ekipe/sporocila"), AID.api("/enote"), AID.api("/centri")]);
      Object.assign(E, { predlogi: p, eskalacije: e, sporocila: s, enote: en, centri: c });
      if (!Object.keys(E.hitra.dispecer).length) E.hitra = await AID.api("/ekipe/hitra-sporocila");
    } catch (_) { /* brez povezave: ostane zadnje stanje */ }
    izrisi();
  }
  let cakajoce = null;
  const naloziKmalu = () => { clearTimeout(cakajoce); cakajoce = setTimeout(nalozi, 150); };

  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-ek]");
    if (!b) return;
    const id = b.dataset.id, akcija = b.dataset.ek;
    if (akcija === "uredi") { E.urejanje[id] = true; izrisi(); return; }
    if (akcija === "preklici") { delete E.urejanje[id]; izrisi(); return; }
    const telo = {};
    if (akcija === "zavrni") telo.razlog = document.querySelector(`[data-razlog="${CSS.escape(id)}"]`)?.value || null;
    if (akcija === "spremeni") {
      telo.parametri = {};
      document.querySelectorAll(`[data-ur="${CSS.escape(id)}"] [name]`).forEach((el) => { telo.parametri[el.name] = el.type === "checkbox" ? el.checked : el.value; });
    }
    b.disabled = true;
    try {
      const r = await AID.api(`/ekipe/predlog/${encodeURIComponent(id)}/${akcija}`, { metoda: "POST", telo });
      delete E.urejanje[id];
      AID.obvestilo(akcija === "zavrni" ? "Predlog zavrnjen" : "Predlog sprejet", r.plan_predlog ? "Planer je predlagal nov plan – preverite in potrdite." : (r.dogodek.opis || ""));
    } catch (e) { napaka(e); }
    nalozi();
  });

  document.addEventListener("DOMContentLoaded", () => {
    $("obrazec-sporocila").onsubmit = async (ev) => {
      ev.preventDefault();
      const koda = $("sp-hitro").value, besedilo = $("sp-besedilo").value.trim();
      if (!koda && !besedilo) return;
      try {
        await AID.api(`/enote/${encodeURIComponent($("sp-enota").value)}/sporocilo`, { metoda: "POST", telo: koda ? { koda } : { besedilo } });
        $("sp-besedilo").value = ""; $("sp-hitro").value = "";
      } catch (e) { napaka(e); }
      nalozi();
    };
    nalozi();
  });

  ["dogodek_predlog", "dogodek_posodobljen", "eskalacija", "eskalacija_razresena", "sporocilo", "sporocilo_prebrano", "voznik_potrditev"].forEach((t) => AID.na(t, naloziKmalu));
  AID.na("sporocilo", (m) => { if (m.smer === "od_voznika") AID.obvestilo(`Sporočilo · ${m.enota_id}`, m.besedilo, !!m.nujno, m.nujno ? 15000 : 6000); });
  AID.na("eskalacija", (x) => AID.obvestilo(`Eskalacija · ${x.enota_id}`, (RAZLOG[x.razlog] || x.razlog) + " – plan ni potrjen.", true, 10000));
  setInterval(izrisi, 15000);
  setInterval(nalozi, 30000);
})();
