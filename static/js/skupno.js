/* AID – skupne funkcije regijskega dispečerskega centra.
 * - kanal za obvestilo o poslani nalogi (BroadcastChannel, rezerva: localStorage dogodki)
 * - revizijska sled (lokalno, zadnja rezerva za stanje)
 * - ura, obvestila, pomožne funkcije
 * Ime ponudnika poti se tu nikoli ne pojavi (pravilo 2.4).
 */
(function () {
  "use strict";
  const AID = (window.AID = window.AID || {});

  AID.UPORABNIK = { id: "disp-07", ime: "M. Novak", inicialke: "MN" };

  /* ---------- varno shranjevanje ---------- */
  AID.beri = function (kljuc, privzeto) {
    try { const v = localStorage.getItem(kljuc); return v == null ? privzeto : JSON.parse(v); }
    catch (e) { return privzeto; }
  };
  AID.pisi = function (kljuc, vrednost) {
    try { localStorage.setItem(kljuc, JSON.stringify(vrednost)); } catch (e) { /* zasebno okno */ }
  };

  /* ---------- kanal med pogledi ---------- */
  const poslusalci = [];
  let kanal = null;
  try { kanal = new BroadcastChannel("aid-demo"); kanal.onmessage = (e) => poslusalci.forEach((f) => f(e.data)); }
  catch (e) { kanal = null; }
  window.addEventListener("storage", (e) => {
    if (kanal || e.key !== "aid_kanal" || !e.newValue) return;
    try { const m = JSON.parse(e.newValue); poslusalci.forEach((f) => f(m)); } catch (_) {}
  });
  AID.poslji = function (tip, podatki) {
    const sporocilo = { tip, podatki, cas: Date.now() };
    if (kanal) kanal.postMessage(sporocilo); else AID.pisi("aid_kanal", sporocilo);
  };
  AID.poslusaj = function (fn) { poslusalci.push(fn); };

  /* ---------- revizijska sled ---------- */
  AID.revizija = function (objekt, objekt_id, akcija, stara, nova, uporabnik) {
    const sled = AID.beri("aid_revizija", []);
    const vnos = {
      id: "r" + (sled.length + 1),
      cas: new Date().toISOString(),
      uporabnik: uporabnik || AID.UPORABNIK.ime,
      objekt, objekt_id, akcija,
      stara_vrednost: stara === undefined ? null : stara,
      nova_vrednost: nova === undefined ? null : nova,
    };
    sled.push(vnos);
    AID.pisi("aid_revizija", sled.slice(-500));
    return vnos;
  };

  /* ---------- pomožne ---------- */
  AID.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };
  AID.dvomestno = (n) => String(n).padStart(2, "0");
  AID.uraZdaj = function (d) {
    d = d || new Date();
    return AID.dvomestno(d.getHours()) + ":" + AID.dvomestno(d.getMinutes()) + ":" + AID.dvomestno(d.getSeconds());
  };
  AID.uraKratko = function (d) { d = d || new Date(); return AID.dvomestno(d.getHours()) + ":" + AID.dvomestno(d.getMinutes()); };
  AID.trajanje = function (s) { s = Math.max(0, Math.floor(s)); return AID.dvomestno(Math.floor(s / 60)) + ":" + AID.dvomestno(s % 60); };
  AID.odstotek = (z) => Math.round(z * 100) + " %";

  AID.zazeniUro = function (el) {
    if (!el) return;
    const t = () => (el.textContent = AID.uraZdaj());
    t(); setInterval(t, 1000);
  };

  AID.obvestilo = function (naslov, besedilo, rdece, trajanjeMs) {
    let vsebnik = document.querySelector(".obvestila");
    if (!vsebnik) { vsebnik = document.createElement("div"); vsebnik.className = "obvestila"; vsebnik.setAttribute("aria-live", "polite"); document.body.appendChild(vsebnik); }
    const o = document.createElement("div");
    o.className = "obvestilo" + (rdece ? " rdece" : "");
    o.innerHTML = "<b>" + AID.esc(naslov) + "</b>" + AID.esc(besedilo || "");
    vsebnik.appendChild(o);
    setTimeout(() => o.remove(), trajanjeMs || 4500);
  };

  AID.modal = function ({ naslov, html, gumbi }) {
    return new Promise((resolve) => {
      const oz = document.createElement("div");
      oz.className = "modal-ozadje";
      oz.innerHTML = '<div class="modal" role="dialog" aria-modal="true"><h3>' + AID.esc(naslov) + "</h3>" + html + '<div class="vrstica-gumbov"></div></div>';
      const vrsta = oz.querySelector(".vrstica-gumbov");
      (gumbi || [{ besedilo: "V redu", vrednost: true, razred: "rdec" }]).forEach((g) => {
        const b = document.createElement("button");
        b.className = "gumb " + (g.razred || "");
        b.textContent = g.besedilo;
        b.onclick = () => { oz.remove(); resolve(g.vrednost); };
        vrsta.appendChild(b);
      });
      oz.addEventListener("keydown", (e) => { if (e.key === "Escape") { oz.remove(); resolve(null); } });
      document.body.appendChild(oz);
      const zadnji = vrsta.lastElementChild; if (zadnji) zadnji.focus();
    });
  };

  /* ---------- glava ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    AID.zazeniUro(document.querySelector("[data-ura]"));
    const pov = document.querySelector("[data-povezava]");
    if (pov) {
      const posodobi = () => { pov.classList.toggle("napaka", !navigator.onLine); pov.textContent = navigator.onLine ? "Povezava OK" : "Brez povezave – lokalni način"; };
      window.addEventListener("online", posodobi); window.addEventListener("offline", posodobi); posodobi();
    }
  });
})();
