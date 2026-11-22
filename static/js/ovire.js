/* AID – ovire na poti (regijski center).
 * Sistem zazna, da gre poslana pot čez oviro, odloča dispečer:
 *   1) »Predlagaj obvoz« -> primerjava stare in nove poti -> »POTRDI OBVOZ IN POŠLJI« (voznik dobi novo pot),
 *   2) če vozilo obtiči -> »Pošlji drugo enoto« -> izbira vozila (časi se izogibajo oviram) -> potrditev plana.
 */
(function () {
  "use strict";
  const AID = window.AID;
  const { esc } = AID;
  const $ = (id) => document.getElementById(id);
  const VRSTE = { zapora: "zapora ceste", nesreca: "nesreča", dela_na_cesti: "dela na cesti", poplava: "poplava", zastoj: "zastoj", drugo: "ovira" };
  const O = { ovire: [], vpliv: [] };
  AID.ovire = O;
  const min = (s) => Math.max(1, Math.round(s / 60));
  const km = (m) => (m / 1000).toFixed(1).replace(".", ",") + " km";
  const napaka = (e) => AID.obvestilo("Ni uspelo", e.message, true, 6000);

  function izrisi() {
    const pas = $("pas-ovire");
    if (!pas) return;
    pas.classList.toggle("skrito", !O.vpliv.length);
    pas.innerHTML = O.vpliv.map((x) => `<div class="ovira-vrstica ${x.ujeta ? "ujeta" : ""}">
      <span class="ovira-ikona" aria-hidden="true">⛔</span>
      <span><b>${esc(x.enota_id)}</b> · ${esc(x.naloga_id)} – ${x.ujeta
        ? `vozilo je obtičalo (${esc(VRSTE[x.ovira.vrsta] || x.ovira.vrsta)}, ocena +${esc(x.ovira.zamuda_min || 30)} min). Obvoz ni mogoč.`
        : `${esc(VRSTE[x.ovira.vrsta] || x.ovira.vrsta)} na poslani poti${x.ovira.opis ? " (" + esc(x.ovira.opis) + ")" : ""}.`}</span>
      ${x.obvoz_mogoc ? `<button class="gumb majhen rdec" data-obvoz="${esc(x.enota_id)}">Predlagaj obvoz</button>` : ""}
      <button class="gumb majhen ${x.ujeta ? "rdec" : ""}" data-druga="${esc(x.naloga_id)}" data-enota="${esc(x.enota_id)}">Pošlji drugo enoto</button>
    </div>`).join("");
    (AID.naOvire || []).forEach((f) => f(O));
  }

  /* ---------- 1) obvoz ---------- */
  async function obvoz(enotaId) {
    let p;
    try { p = await AID.api(`/enote/${encodeURIComponent(enotaId)}/obvoz`); } catch (e) { napaka(e); return; }
    if (!p.mogoc) { AID.obvestilo("Obvoz ni mogoč", p.razlog, true, 8000); return; }
    const razlika = p.razlika_s == null ? "" : ` (${p.razlika_s >= 0 ? "+" : "−"}${min(Math.abs(p.razlika_s))} min)`;
    const izbira = AID.modal({
      naslov: `Spremeni pot ${enotaId}?`,
      html: `<div id="obvoz-karta" class="obvoz-karta"></div>
        <table class="prej-potem"><thead><tr><th></th><th>Sedanja pot</th><th>Obvoz</th></tr></thead><tbody>
        <tr><td>Čas</td><td class="st">${p.stara.cas_s != null ? min(p.stara.cas_s) + " min" : "—"}</td><td class="st"><b>${min(p.nova.cas_s)} min</b>${esc(razlika)}</td></tr>
        <tr><td>Razdalja</td><td class="st">${p.stara.razdalja_m != null ? km(p.stara.razdalja_m) : "—"}</td><td class="st">${km(p.nova.razdalja_m)}</td></tr>
        <tr><td>Kakovost</td><td></td><td class="st">${esc(p.nova.kakovost)}</td></tr></tbody></table>
        <p class="pojasnilo">${p.nova.izogiba_oviram ? "Nova pot se izogne oviri." : "Pozor: pot po cestah z izogibanjem ni na voljo – prikazana je ocena."}
        Voznik po potrditvi takoj dobi novo pot, navodila in vaše sporočilo.</p>`,
      gumbi: [{ besedilo: "Prekliči", vrednost: false }, { besedilo: "POTRDI OBVOZ IN POŠLJI", vrednost: true, razred: "rdec" }],
    });
    setTimeout(() => narisiObvoz(p), 30);
    if (!(await izbira)) return;
    try {
      const r = await AID.api(`/enote/${encodeURIComponent(enotaId)}/obvoz`, { metoda: "POST" });
      AID.obvestilo(`Obvoz poslan ${enotaId}`, `Nova pot: ${min(r.cas_s)} min, ${km(r.razdalja_m)}. Voznik je prejel sporočilo.`);
    } catch (e) { napaka(e); }
  }
  function narisiObvoz(p) {
    const el = $("obvoz-karta");
    if (!el || !window.L) return;
    const k = L.map(el, { zoomControl: false, attributionControl: false });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { maxZoom: 19 }).addTo(k);
    const tocke = [];
    if (p.stara.geometrija && p.stara.geometrija.length) { L.polyline(p.stara.geometrija, { color: "#8A919C", weight: 5, dashArray: "6 6" }).addTo(k); tocke.push(...p.stara.geometrija); }
    L.polyline(p.nova.geometrija, { color: "#8A1525", weight: 5 }).addTo(k); tocke.push(...p.nova.geometrija);
    O.ovire.forEach((o) => L.circle([o.lat, o.lon], { radius: o.polmer_m, color: "#8A1525", fillColor: "#8A1525", fillOpacity: 0.25, weight: 2 }).addTo(k)
      .bindTooltip("⛔ " + (VRSTE[o.vrsta] || o.vrsta), { permanent: true, direction: "top" }));
    if (tocke.length) k.fitBounds(tocke, { padding: [16, 16] });
  }

  /* ---------- 2) druga enota ---------- */
  async function drugaEnota(nalogaId, zdajEnota) {
    let seznam;
    try { seznam = await AID.api(`/naloge/${encodeURIComponent(nalogaId)}/kandidati`); } catch (e) { napaka(e); return; }
    const izbira = await AID.modal({
      naslov: `Kdo naj prevzame ${nalogaId}?`,
      html: `<p class="pojasnilo">Časi prihoda s prometom in mimo ovir. ${esc(zdajEnota)} trenutno ne more naprej.</p>
        <table class="enote-tabela izbira-vozila"><tbody>${seznam.map((x, i) => {
          const prosto = x.stanje === "prosto";
          const prvaProsta = seznam.find((y) => y.primerno && !y.obtical && !y.sedanja && y.stanje === "prosto");
          return `<tr class="${prosto ? "" : "zasedena"}"><td><b>${esc(x.enota_id)}</b> <span class="tip-vozila">${esc(x.tip)}${prosto ? "" : " · na nalogi"}</span>
            ${x.obtical ? '<br><span class="voznik-ne">obtičal</span>' : x.sedanja ? '<br><span class="znacka">sedanja</span>' : ""}
            ${x === prvaProsta ? '<br><span class="znacka temna">najhitrejša prosta</span>' : ""}</td>
            <td class="eta">${min(x.cas_s)} min</td><td>${km(x.razdalja_m)}</td>
            <td>${x.sedanja || x.obtical ? "" : !x.primerno ? `<span class="voznik-ne">${esc(x.razlog)}</span>`
              : `<button class="gumb majhen ${prosto ? "temen" : ""}" data-izberi-drugo="${esc(x.enota_id)}">Izberi</button>`}</td></tr>`;
        }).join("")}</tbody></table>`,
      gumbi: [{ besedilo: "Prekliči", vrednost: null }],
    });
    if (izbira !== null) return; // izbira gre prek gumba v tabeli (spodaj)
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-izberi-drugo]");
    if (!b) return;
    const modal = b.closest(".modal-ozadje");
    const nalogaId = modal && modal.querySelector(".modal h3").textContent.match(/(N-[\w-]+)/)[1];
    if (modal) modal.remove();
    try {
      AID.obvestilo("Planer računa …", `${nalogaId} → ${b.dataset.izberiDrugo}`);
      const r = await AID.api(`/naloge/${encodeURIComponent(nalogaId)}/dodeli`, { metoda: "POST", telo: { enota_id: b.dataset.izberiDrugo } });
      if (r.plan_predlog) await potrdiPlan(r.plan_predlog, nalogaId, b.dataset.izberiDrugo);
    } catch (e2) { napaka(e2); }
  });

  async function potrdiPlan(plan, nalogaId, enotaId) {
    const korak = (plan.koraki_po_enoti[enotaId] || []).find((k) => k.naloga_id === nalogaId);
    const spremembe = plan.primerjava.spremenjene_dodelitve || [];
    const ok = await AID.modal({
      naslov: `Pošlji ${enotaId} namesto obtičalega vozila?`,
      html: `<p><b>${esc(enotaId)}</b> · ${korak ? `prihod ob ${AID.uraKratko(new Date(korak.eta * 1000))} (čez ${min(korak.eta - Date.now() / 1000)} min)` : "ni v planu"}</p>
        <ul>${spremembe.map((x) => `<li>${esc(x.naloga_id)}: ${esc(x.prej || "—")} → <b>${esc(x.potem || x.opomba || "—")}</b></li>`).join("")}</ul>
        <p class="pojasnilo">${esc(plan.primerjava.razlaga)} Prejšnje vozilo dobi obvestilo, da je nalogo prevzela druga enota.</p>`,
      gumbi: [{ besedilo: "Pokaži v planu", vrednost: "plan" }, { besedilo: "POTRDI PLAN IN POŠLJI", vrednost: "potrdi", razred: "rdec" }],
    });
    if (ok === "plan") { AID.preklopi("plan"); return; }
    if (ok !== "potrdi") { AID.obvestilo("Plan ni potrjen", "Dokler ga ne potrdite, ostane naloga pri prejšnjem vozilu.", true, 7000); return; }
    try {
      await AID.api(`/plan/${plan.id}/potrdi`, { metoda: "POST" });
      (AID.naPrerazporeditev || []).forEach((f) => f(nalogaId, enotaId));
      AID.obvestilo(`Naloga ${nalogaId} poslana ${enotaId}`, "Novo vozilo je prejelo nalogo in pot.");
      osvezi();
    } catch (e) { napaka(e); }
  }

  /* ---------- demo ---------- */
  async function demo(faza) {
    const enota = AID.stanjeKlica && AID.stanjeKlica.posiljanje && AID.stanjeKlica.posiljanje.enota;
    try {
      const r = await AID.api("/demo/ovira", { metoda: "POST", telo: { faza, enota_id: enota || null } });
      AID.obvestilo(faza === "zapora" ? "Simulirana zapora ceste" : "Simulirano: vozilo obtiči", `${r.enota_id}: ${r.dogodek.opis}`, true);
    } catch (e) { napaka(e); }
  }

  async function osvezi() {
    try { Object.assign(O, await AID.api("/ovire/vpliv")); izrisi(); } catch (_) {}
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-obvoz]"); if (a) { obvoz(a.dataset.obvoz); return; }
    const d = e.target.closest("[data-druga]"); if (d) { drugaEnota(d.dataset.druga, d.dataset.enota); return; }
    const m = e.target.closest("[data-demo-ovira]"); if (m) demo(m.dataset.demoOvira);
  });
  AID.na("ovire", (x) => { Object.assign(O, x); izrisi(); });
  AID.na("plan_aktiven", () => setTimeout(osvezi, 1500));
  AID.na("obvoz", () => osvezi());
  document.addEventListener("DOMContentLoaded", async () => { await AID.prijava; osvezi(); });
})();
