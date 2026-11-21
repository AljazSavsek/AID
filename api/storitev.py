"""Storitev regijskega centra: povezuje prepis, AI poslušalca, geokodiranje,
usmerjanje, planer in revizijo. Vse spremembe gredo skozi tukaj.

Pravila (specifikacija 2, 5):
- AI predlaga, dispečer odloča: plan in naloge nastanejo le iz potrjenih podatkov.
- potrjenih polj AI ne prepiše -> nov predlog z `nasprotuje_potrjenemu`.
- spremenjen `predlagano` -> stari `zastarelo`, novi `predlagano`.
- vsako dejanje v revizijo (uporabnik, čas, stara, nova vrednost).
"""
from __future__ import annotations

import asyncio
import collections
import copy
import json
import logging
import time
from pathlib import Path

from dnevnik import model as M
from dnevnik.shramba import Shramba
from geokodiranje.geokoder import Geokoder
from planer import dogodki as D
from planer.vrp import Nastavitve, planiraj, preusmeri_po_zmogljivosti, primerjaj, sestavi_model
from poslusalec.poslusalec import Poslusalec
from usmerjanje.ponudniki import helikopter, zracna_ocena
from usmerjanje.servis import UsmerjevalniServis

dnevnik = logging.getLogger("center")
KOREN = Path(__file__).resolve().parent.parent
AI = "AI poslušalec"
SISTEM = "sistem"
OBVEZNA_ZA_ODOBRITEV = ("naslov", "lokacija", "prioriteta", "predlagana_enota")
NIKOLI_SKUPINSKO = ("prioriteta", "predlagana_enota")


class NapakaStoritve(Exception):
    def __init__(self, sporocilo: str, koda: int = 400):
        super().__init__(sporocilo)
        self.koda = koda


class Objava:
    """Dogodki za frontend (WebSocket + polling rezerva)."""

    def __init__(self, velikost: int = 1000):
        self.seq = 0
        self.zgodovina: collections.deque = collections.deque(maxlen=velikost)
        self.narocniki: set[asyncio.Queue] = set()

    def objavi(self, tip: str, podatki) -> None:
        self.seq += 1
        sporocilo = {"seq": self.seq, "tip": tip, "cas": time.time(), "podatki": podatki}
        self.zgodovina.append(sporocilo)
        for q in list(self.narocniki):
            try:
                q.put_nowait(sporocilo)
            except asyncio.QueueFull:
                pass

    def od(self, seq: int) -> list[dict]:
        return [s for s in self.zgodovina if s["seq"] > seq]


class Center:
    def __init__(self, shramba: Shramba | None = None, usmerjanje: UsmerjevalniServis | None = None,
                 geokoder: Geokoder | None = None, poslusalec: Poslusalec | None = None,
                 scenarij: str | Path | None = None, nastavitve_planerja: Nastavitve | None = None):
        self.db = shramba or Shramba(":memory:")
        self.usmerjanje = usmerjanje or UsmerjevalniServis()
        self.geokoder = geokoder or Geokoder()
        self.poslusalec = poslusalec or Poslusalec()
        self.nast = nastavitve_planerja or Nastavitve()
        self.objava = Objava()
        self._ai_zaklepi: dict[str, asyncio.Lock] = {}
        self._ai_cakajoci: set[str] = set()
        self._ozadje: set[asyncio.Task] = set()
        self._plan_zaklep = asyncio.Lock()
        self.ai_na_voljo = True
        self.nalozi_scenarij(scenarij or KOREN / "demo" / "scenarij.json")

    # ================= scenarij in stanje =================
    def nalozi_scenarij(self, pot) -> None:
        sc = json.loads(Path(pot).read_text(encoding="utf-8"))
        t0 = time.time()
        self.cakajoci = [dict(k, id=f"q{i + 1}", prispel=t0 - 20 + 6 * i) for i, k in enumerate(sc.get("cakajoci_klici", []))]
        if self.db.vsi("enota"):
            return  # obstoječe stanje iz SQLite
        for c in sc["centri"]:
            self.db.shrani("center", c)
        for e in sc["enote"]:
            self.db.shrani("enota", e)
        for n in sc["naloge"]:
            n = dict(n, stanje="odprta", ustvarjeno=t0)
            for k in ("okno_od", "okno_do"):
                if n.get(k) is not None:
                    n[k] = t0 + n[k] * 60
            self.db.shrani("naloga", n)

    def stanje(self) -> dict:
        return {"enote": self.db.vsi("enota"), "naloge": self.db.vsi("naloga"), "centri": self.db.vsi("center")}

    def _shrani_stanje(self, st: dict) -> None:
        for tip, kljuc in (("enota", "enote"), ("naloga", "naloge"), ("center", "centri")):
            for o in st[kljuc]:
                self.db.shrani(tip, o)

    def _v_ozadju(self, koro) -> asyncio.Task:
        t = asyncio.ensure_future(koro)
        self._ozadje.add(t)
        t.add_done_callback(self._ozadje.discard)
        t.add_done_callback(lambda t: t.cancelled() or t.exception() is None or dnevnik.error("ozadje: %r", t.exception()))
        return t

    async def pocakaj_ozadje(self) -> None:
        while self._ozadje:
            await asyncio.gather(*list(self._ozadje), return_exceptions=True)

    # ================= klic =================
    def zacni_klic(self, uporabnik: str, stevilka: str | None = None, cakajoci_id: str | None = None) -> dict:
        if cakajoci_id:
            q = next((q for q in self.cakajoci if q["id"] == cakajoci_id), None)
            if q:
                stevilka = stevilka or q["stevilka"]
                self.cakajoci.remove(q)
        k = M.klic(stevilka)
        k["prevzel"] = uporabnik
        self.db.shrani("klic", k)
        self.db.revizija(uporabnik, "Klic", k["id"], "zacetek", None, {"stevilka": stevilka})
        self.objava.objavi("klic", k)
        return k

    def klic(self, klic_id: str) -> dict:
        k = self.db.beri("klic", klic_id)
        if not k:
            raise NapakaStoritve("klic ne obstaja", 404)
        return k

    def koncaj_klic(self, klic_id: str, uporabnik: str) -> dict:
        k = self.klic(klic_id)
        k["stanje"], k["konec"] = "zakljucen", time.time()
        self.db.shrani("klic", k)
        self.db.revizija(uporabnik, "Klic", klic_id, "konec", "v_teku", "zakljucen")
        self.objava.objavi("klic", k)
        return k

    def segmenti(self, klic_id: str) -> list[dict]:
        return [s for s in self.db.vsi("segment", klic_id=klic_id) if s["koncni"]]

    def dodaj_segment(self, klic_id: str, govorec: str, besedilo: str, od_s: float | None = None,
                      do_s: float | None = None, koncni: bool = True, rocni: bool = False, uporabnik: str = SISTEM) -> dict:
        k = self.klic(klic_id)
        if k["stanje"] != "v_teku":
            raise NapakaStoritve("klic je zaključen")
        besedilo = (besedilo or "").strip()
        if not besedilo:
            raise NapakaStoritve("prazen segment")
        t = time.time() - k["zacetek"]
        seg = M.segment(klic_id, govorec, od_s if od_s is not None else t, do_s if do_s is not None else t, besedilo, koncni, rocni,
                        seg_id=f"{klic_id}-s{len(self.db.vsi('segment', klic_id=klic_id)) + 1}")
        self.db.shrani("segment", seg)
        self.db.revizija(uporabnik if rocni else "prepis", "Segment", seg["id"], "prepis", None, besedilo)
        self.objava.objavi("segment", seg)
        if koncni:
            self._v_ozadju(self._ai_za_klic(klic_id))
        return seg

    # ================= AI poslušalec =================
    async def _ai_za_klic(self, klic_id: str) -> None:
        """Ne blokira: segmenti, ki pridejo med obdelavo, se združijo v en naslednji krog."""
        zaklep = self._ai_zaklepi.setdefault(klic_id, asyncio.Lock())
        if zaklep.locked():
            self._ai_cakajoci.add(klic_id)
            return
        async with zaklep:
            while True:
                self._ai_cakajoci.discard(klic_id)
                st = self.stanje()
                izhod = await self.poslusalec.obdelaj(self.segmenti(klic_id), self.predlogi(klic_id), st["enote"], st["centri"])
                na_voljo = izhod is not None
                if na_voljo != self.ai_na_voljo:
                    self.ai_na_voljo = na_voljo
                    self.objava.objavi("opozorilo", {"vrsta": "ai", "na_voljo": na_voljo,
                                                     "sporocilo": "AI poslušalec ni na voljo – vnesite podatke ročno." if not na_voljo
                                                     else "AI poslušalec spet deluje."})
                if izhod:
                    await self.uveljavi_izhod(klic_id, izhod)
                if klic_id not in self._ai_cakajoci:
                    break

    async def uveljavi_izhod(self, klic_id: str, izhod: dict) -> None:
        spremenjena = set()
        for u in izhod["posodobitve"]:
            if self._uveljavi_posodobitev(klic_id, u):
                spremenjena.add(u["polje"])
        if izhod.get("vprasanja_za_klicatelja") is not None:
            self._nastavi_vprasanja(klic_id, izhod["vprasanja_za_klicatelja"])
        for d in izhod.get("zaznani_dogodki", []):
            self._uveljavi_posodobitev(klic_id, {"polje": "zaznani_dogodek_plana",
                                                 "vrednost": {"tip": d["tip"], "parametri": d["parametri"]},
                                                 "zaupanje": d["zaupanje"], "dokazi": d["dokazi"]})
        if "naslov" in spremenjena:
            await self._geokodiraj_predloge(klic_id)
        if spremenjena & {"naslov", "lokacija"}:
            await self._predlagaj_enoto(klic_id)

    def predlogi(self, klic_id: str) -> list[dict]:
        return self.db.vsi("predlog", klic_id=klic_id)

    def _uveljavi_posodobitev(self, klic_id: str, u: dict, objavi: bool = True) -> dict | None:
        """Vrne nov predlog ali None, če ni spremembe."""
        if not u.get("dokazi"):
            return None  # brez dokaza ni predloga (pravilo 2.3)
        za_polje = [p for p in self.predlogi(klic_id) if p["polje"] == u["polje"]]
        potrjen = next((p for p in za_polje if p["stanje"] in M.POTRJENA), None)
        odprti = [p for p in za_polje if p["stanje"] == "predlagano"]
        enako = lambda a, b: json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)  # noqa: E731
        if potrjen:
            if enako(potrjen["vrednost"], u["vrednost"]) or any(enako(p["vrednost"], u["vrednost"]) for p in odprti):
                return None
            p = M.predlog(klic_id, u["polje"], u["vrednost"], u["zaupanje"], u["dokazi"], len(za_polje) + 1,
                          nasprotuje_potrjenemu=True, dodatno=u.get("dodatno"))
            self.db.shrani("predlog", p)
            self.db.revizija(AI, "Predlog", p["id"], "predlog_ai", potrjen["vrednost"], p["vrednost"])
            if objavi:
                self.objava.objavi("predlog_nov", p)
                self.objava.objavi("opozorilo", {"vrsta": "nasprotuje_potrjenemu", "polje": u["polje"],
                                                 "sporocilo": "AI je zaznal vrednost, ki nasprotuje potrjeni."})
            return p
        isti = next((p for p in odprti if enako(p["vrednost"], u["vrednost"])), None)
        if isti:
            if isti["zaupanje"] == u["zaupanje"] and isti["dokazi"] == u["dokazi"] and isti.get("dodatno") == u.get("dodatno", isti.get("dodatno")):
                return None
            isti.update(zaupanje=u["zaupanje"], dokazi=u["dokazi"])
            if "dodatno" in u:
                isti["dodatno"] = u["dodatno"]
            self.db.shrani("predlog", isti)
            if objavi:
                self.objava.objavi("predlog_posodobljen", isti)
            return None
        if not u.get("alternativa"):
            for p in odprti:
                p["stanje"] = "zastarelo"
                self.db.shrani("predlog", p)
                self.db.revizija(AI, "Predlog", p["id"], "zastarelo", p["vrednost"], u["vrednost"])
                if objavi:
                    self.objava.objavi("predlog_posodobljen", p)
        p = M.predlog(klic_id, u["polje"], u["vrednost"], u["zaupanje"], u["dokazi"], len(za_polje) + 1, dodatno=u.get("dodatno"))
        if u.get("alternativa"):
            p["dodatno"]["alternativa"] = True
        self.db.shrani("predlog", p)
        self.db.revizija(AI, "Predlog", p["id"], "predlog_ai", None, p["vrednost"])
        if objavi:
            self.objava.objavi("predlog_nov", p)
        return p

    def _nastavi_vprasanja(self, klic_id: str, vprasanja: list[str]) -> None:
        k = self.klic(klic_id)
        if k.get("vprasanja") != vprasanja:
            k["vprasanja"] = vprasanja
            self.db.shrani("klic", k)
            self.objava.objavi("vprasanja", {"klic_id": klic_id, "vprasanja": vprasanja})

    # ---------- geokodiranje -> predlog lokacije ----------
    async def _geokodiraj_predloge(self, klic_id: str) -> None:
        naslovi = [p for p in self.predlogi(klic_id) if p["polje"] == "naslov" and p["stanje"] in ("predlagano", *M.POTRJENA)
                   and not p.get("dodatno", {}).get("geokodirano")]
        for p in naslovi:
            kandidati = await self.geokoder.geokodiraj(p["vrednost"])
            p.setdefault("dodatno", {})["geokodirano"] = True
            self.db.shrani("predlog", p)
            if not kandidati:
                self.objava.objavi("opozorilo", {"vrsta": "geokodiranje", "polje": "lokacija",
                                                 "sporocilo": f"Naslova »{p['vrednost']}« ni bilo mogoče najti – kliknite lokacijo na karti."})
                continue
            if len(kandidati) > 1 and p["stanje"] == "predlagano":
                # dvoumno: naslova ne ugibamo -> več predlogov z nižjim zaupanjem
                p["stanje"] = "zastarelo"
                self.db.shrani("predlog", p)
                self.db.revizija(SISTEM, "Predlog", p["id"], "dvoumno", p["vrednost"], [k["naslov"] for k in kandidati])
                self.objava.objavi("predlog_posodobljen", p)
                for k in kandidati[:4]:
                    self._uveljavi_posodobitev(klic_id, {"polje": "naslov", "vrednost": k["naslov"], "alternativa": True,
                                                         "zaupanje": round(min(p["zaupanje"], 0.9) * 0.6, 2), "dokazi": p["dokazi"],
                                                         "dodatno": {"geokodirano": True, "lokacija": {"lat": k["lat"], "lon": k["lon"]}}})
                continue
            k = kandidati[0]
            dokazi = p["dokazi"] or [{"segment_id": "", "citat": f"naslov, ki ga je vnesel dispečer: {p['vrednost']}"}]
            self._uveljavi_posodobitev(klic_id, {"polje": "lokacija", "vrednost": {"lat": k["lat"], "lon": k["lon"]},
                                                 "zaupanje": round(min(p["zaupanje"] or 0.9, 0.95) * k.get("ujemanje", 1), 2),
                                                 "dokazi": dokazi, "dodatno": {"iz_naslova": k["naslov"]}})

    # ---------- usmerjanje + planer -> predlog enote ----------
    async def eta_enot(self, lat: float, lon: float, omejitev: int = 3) -> list[dict]:
        """Časi prihoda prostih enot (samo čas, razdalja, kakovost – brez ponudnika)."""
        enote = [e for e in self.db.vsi("enota") if e["stanje"] != "okvara"]

        async def ena(e):
            pot = await self.usmerjanje.izracunaj((e["lat"], e["lon"]), (lat, lon), e["tip"])
            zamik = max(0, (e.get("na_voljo_od") or 0) - time.time())
            return {"enota_id": e["id"], "tip": e["tip"], "ime": e["ime"], "stanje": e["stanje"],
                    "cas_s": round(pot["cas_s"] + zamik), "razdalja_m": pot["razdalja_m"], "kakovost": pot["kakovost"]}
        rez = await asyncio.gather(*(ena(e) for e in enote))
        rez.sort(key=lambda x: (x["stanje"] != "prosto", x["cas_s"]))
        return rez[:omejitev]

    async def _predlagaj_enoto(self, klic_id: str) -> None:
        za_lok = [p for p in self.predlogi(klic_id) if p["polje"] == "lokacija"]
        lok = next((p for p in za_lok if p["stanje"] in M.POTRJENA), None) or \
            max((p for p in za_lok if p["stanje"] == "predlagano"), key=lambda p: p["zaupanje"] or 0, default=None)
        if not lok:
            return
        top = await self.eta_enot(lok["vrednost"]["lat"], lok["vrednost"]["lon"])
        prosti = [t for t in top if t["stanje"] == "prosto"] or top
        if not prosti:
            return
        dokazi = lok["dokazi"] or [{"segment_id": "", "citat": "lokacija, ki jo je določil dispečer"}]
        dodatno = {"enote": top, "izracunano": time.time()}
        self._uveljavi_posodobitev(klic_id, {"polje": "predlagana_enota", "vrednost": prosti[0]["enota_id"],
                                             "zaupanje": 0.8 if lok["stanje"] in M.POTRJENA else 0.65,
                                             "dokazi": dokazi, "dodatno": dodatno})
        # osveži tabelo časov tudi na že potrjenem predlogu (vrednosti ne spreminja)
        for p in self.predlogi(klic_id):
            if p["polje"] == "predlagana_enota" and p["stanje"] in M.POTRJENA:
                p["dodatno"] = {**p.get("dodatno", {}), **dodatno}
                self.db.shrani("predlog", p)
                self.objava.objavi("predlog_posodobljen", p)

    async def osvezi_najblizjo(self, klic_id: str, uporabnik: str) -> list[dict]:
        """Ponovno poišče najbližjo enoto po trenutnem prometu. Sprememba je le PREDLOG."""
        self.klic(klic_id)
        self.db.revizija(uporabnik, "Klic", klic_id, "osvezi_najblizjo", None, None)
        await self._predlagaj_enoto(klic_id)
        return self.predlogi(klic_id)

    @staticmethod
    def _redci(geometrija: list, najvec: int = 400) -> list:
        if len(geometrija) <= najvec:
            return geometrija
        korak = len(geometrija) / (najvec - 1)
        return [geometrija[int(i * korak)] for i in range(najvec - 1)] + [geometrija[-1]]

    async def pot_enote(self, enota_id: str, lat: float, lon: float) -> dict:
        """Pot enote do cilja po cestah (s prometom, če je na voljo). Brez imena ponudnika."""
        e = self.db.beri("enota", enota_id)
        if not e:
            raise NapakaStoritve("enota ne obstaja", 404)
        pot = await self.usmerjanje.izracunaj((e["lat"], e["lon"]), (lat, lon), e["tip"])
        zamik = max(0, (e.get("na_voljo_od") or 0) - time.time())
        return {"enota_id": enota_id, "tip": e["tip"], "od": [e["lat"], e["lon"]], "do": [lat, lon],
                "cas_s": round(pot["cas_s"] + zamik), "razdalja_m": pot["razdalja_m"],
                "kakovost": pot["kakovost"], "geometrija": self._redci(pot["geometrija"])}

    async def poti(self, odseki: list[dict]) -> list[dict]:
        """Geometrija več odsekov naenkrat (za risanje plana po cestah)."""
        async def en(o):
            pot = await self.usmerjanje.izracunaj(tuple(o["od"]), tuple(o["do"]), o.get("tip", "vozilo"))
            return {"cas_s": pot["cas_s"], "razdalja_m": pot["razdalja_m"], "kakovost": pot["kakovost"],
                    "geometrija": self._redci(pot["geometrija"], 200)}
        return list(await asyncio.gather(*(en(o) for o in odseki[:80])))

    # ================= dejanja dispečerja =================
    def predlog(self, predlog_id: str) -> dict:
        p = self.db.beri("predlog", predlog_id)
        if not p:
            raise NapakaStoritve("predlog ne obstaja", 404)
        return p

    def _potrdi(self, p: dict, stanje: str, uporabnik: str, nova=None, akcija: str | None = None) -> dict:
        stara = p["vrednost"]
        if nova is not None:
            p["vrednost"] = nova
        p.update(stanje=stanje, potrdil=uporabnik, potrjeno_ob=time.time())
        # ostali odprti/potrjeni predlogi istega polja niso več veljavni
        for x in self.predlogi(p["klic_id"]):
            if x["polje"] == p["polje"] and x["id"] != p["id"] and x["stanje"] in ("predlagano", *M.POTRJENA):
                star = x["stanje"]
                x["stanje"] = "zastarelo" if star in M.POTRJENA else "zavrnjeno"
                x["razlog"] = "izbrana druga vrednost"
                self.db.shrani("predlog", x)
                self.db.revizija(uporabnik, "Predlog", x["id"], x["stanje"], star, x["stanje"])
                self.objava.objavi("predlog_posodobljen", x)
        self.db.shrani("predlog", p)
        self.db.revizija(uporabnik, "Predlog", p["id"], akcija or ("sprejmi" if stanje == "sprejeto" else "spremeni"),
                         {"vrednost": stara, "stanje": "predlagano"} if akcija != "rocni_vnos" else None,
                         {"vrednost": p["vrednost"], "stanje": stanje})
        self.objava.objavi("predlog_posodobljen", p)
        return p

    async def _po_potrditvi(self, p: dict) -> None:
        if p["polje"] == "naslov":
            if p.get("dodatno", {}).get("lokacija"):
                self._uveljavi_posodobitev(p["klic_id"], {"polje": "lokacija", "vrednost": p["dodatno"]["lokacija"], "zaupanje": 0.9,
                                                          "dokazi": p["dokazi"] or [{"segment_id": "", "citat": p["vrednost"]}]})
            else:
                p.setdefault("dodatno", {})["geokodirano"] = False
                self.db.shrani("predlog", p)
                await self._geokodiraj_predloge(p["klic_id"])
            await self._predlagaj_enoto(p["klic_id"])
        elif p["polje"] == "lokacija":
            await self._predlagaj_enoto(p["klic_id"])
        elif p["polje"] == "zaznani_dogodek_plana":
            d = M.dogodek(p["vrednost"]["tip"], p["vrednost"]["parametri"], "klic", "sprejeto", p["dokazi"], p["zaupanje"])
            d["potrdil"] = p["potrdil"]
            await self.uveljavi_dogodek(d, p["potrdil"])

    def _preveri_dogodek(self, p: dict) -> None:
        if p["polje"] == "zaznani_dogodek_plana":
            v = p["vrednost"]
            if v["tip"] == "okvara_vozila" and not v["parametri"].get("enota_id"):
                raise NapakaStoritve("Katero vozilo? Pred potrditvijo uredite predlog in vpišite enoto.")

    async def sprejmi(self, predlog_id: str, uporabnik: str) -> dict:
        p = self.predlog(predlog_id)
        if p["stanje"] != "predlagano":
            raise NapakaStoritve(f"predlog je že {p['stanje']}", 409)
        self._preveri_dogodek(p)
        p = self._potrdi(p, "sprejeto", uporabnik)
        await self._po_potrditvi(p)
        return p

    async def spremeni(self, predlog_id: str, vrednost, uporabnik: str) -> dict:
        p = self.predlog(predlog_id)
        if p["stanje"] in ("zavrnjeno", "zastarelo"):
            raise NapakaStoritve(f"predlog je {p['stanje']}", 409)
        vrednost = preveri_vrednost(p["polje"], vrednost)
        self._preveri_dogodek(dict(p, vrednost=vrednost))
        p = self._potrdi(p, "spremenjeno", uporabnik, vrednost)
        await self._po_potrditvi(p)
        return p

    async def rocni_vnos(self, klic_id: str, polje: str, vrednost, uporabnik: str) -> dict:
        """Ročni vnos je vedno mogoč, tudi brez AI (specifikacija 6)."""
        self.klic(klic_id)
        if polje not in M.POLJA_AI:
            raise NapakaStoritve("neznano polje")
        vrednost = preveri_vrednost(polje, vrednost)
        za_polje = [p for p in self.predlogi(klic_id) if p["polje"] == polje]
        p = M.predlog(klic_id, polje, vrednost, None, [], len(za_polje) + 1, predlagal="dispecer")
        self.db.shrani("predlog", p)
        p = self._potrdi(p, "spremenjeno", uporabnik, akcija="rocni_vnos")
        await self._po_potrditvi(p)
        return p

    def zavrni(self, predlog_id: str, uporabnik: str, razlog: str | None = None) -> dict:
        p = self.predlog(predlog_id)
        if p["stanje"] != "predlagano":
            raise NapakaStoritve(f"predlog je že {p['stanje']}", 409)
        p.update(stanje="zavrnjeno", razlog=razlog or None)
        self.db.shrani("predlog", p)
        self.db.revizija(uporabnik, "Predlog", p["id"], "zavrni", {"vrednost": p["vrednost"], "stanje": "predlagano"},
                         {"stanje": "zavrnjeno", "razlog": razlog})
        self.objava.objavi("predlog_posodobljen", p)
        return p

    async def sprejmi_vse(self, klic_id: str, uporabnik: str, prag: float = 0.8) -> list[dict]:
        """Nikoli za prioriteto in predlagano enoto (specifikacija 6)."""
        sprejeti = []
        predlogi = self.predlogi(klic_id)
        for polje in {p["polje"] for p in predlogi} - set(NIKOLI_SKUPINSKO) - {"zaznani_dogodek_plana"}:
            za = [p for p in predlogi if p["polje"] == polje]
            if any(p["stanje"] in M.POTRJENA for p in za):
                continue
            odprti = [p for p in za if p["stanje"] == "predlagano"]
            if len(odprti) == 1 and (odprti[0]["zaupanje"] or 0) >= prag:
                sprejeti.append(await self.sprejmi(odprti[0]["id"], uporabnik))
        self.db.revizija(uporabnik, "Klic", klic_id, "sprejmi_vse", None, [p["polje"] for p in sprejeti])
        return sprejeti

    def potrjeno(self, klic_id: str) -> dict:
        return {p["polje"]: p["vrednost"] for p in self.predlogi(klic_id) if p["stanje"] in M.POTRJENA}

    async def odobri_klic(self, klic_id: str, uporabnik: str) -> dict:
        """ODOBRI IN POŠLJI: iz POTRJENIH podatkov nastane nujna naloga -> predlog plana."""
        k = self.klic(klic_id)
        if k.get("naloga_id"):
            raise NapakaStoritve("klic je že odobren", 409)
        pot = self.potrjeno(klic_id)
        manjka = [p for p in OBVEZNA_ZA_ODOBRITEV if p not in pot]
        if manjka:
            raise NapakaStoritve("Potrdite še: " + ", ".join(manjka), 422)
        lok = pot["lokacija"]
        opis = " · ".join(str(x) for x in (pot.get("vrsta_dogodka"), pot.get("opis"), pot.get("stanje_pacienta")) if x)
        n = M.naloga("nujna_voznja", {"lat": lok["lat"], "lon": lok["lon"], "naslov": pot["naslov"],
                                      "orientacija": pot.get("orientacija")}, int(pot["prioriteta"]),
                     okno_od=time.time(), okno_do=time.time() + {1: 10, 2: 15, 3: 25, 4: 45, 5: 60}[int(pot["prioriteta"])] * 60,
                     opis=opis)
        n.update(klic_id=klic_id, zelena_enota=pot["predlagana_enota"], podatki_klica=pot)
        d = M.dogodek("nujna_naloga", {"naloga": n}, "klic", "sprejeto")
        k["naloga_id"] = n["id"]
        self.db.shrani("klic", k)
        self.db.revizija(uporabnik, "Klic", klic_id, "odobri_in_poslji", None, {"naloga_id": n["id"], "podatki": pot})
        plan = await self.uveljavi_dogodek(d, uporabnik, zelena_enota=(n["id"], pot["predlagana_enota"]))
        return {"naloga": n, "plan_predlog": plan}

    # ================= dogodki in plan =================
    async def uveljavi_dogodek(self, d: dict, uporabnik: str, zelena_enota: tuple | None = None) -> dict | None:
        st = self.stanje()
        try:
            opis = D.uveljavi(st, d)
        except D.NeveljavenDogodek as e:
            raise NapakaStoritve(str(e), 422) from e
        d["stanje"] = "sprejeto"
        d["opis"] = opis
        self.db.shrani("dogodek", d)
        self._shrani_stanje(st)
        self.db.revizija(uporabnik, "Dogodek", d["id"], "uveljavi", None, {"tip": d["tip"], "parametri": d["parametri"], "opis": opis})
        self.objava.objavi("dogodek", d)
        self.objava.objavi("enote", self.db.vsi("enota"))
        return await self.predlagaj_plan(f"{d['tip']}: {opis}", zelena_enota)

    def plan_aktiven(self) -> dict | None:
        return next((p for p in self.db.vsi("plan") if p["stanje"] == "aktiven"), None)

    def plan(self, plan_id: str) -> dict:
        p = self.db.beri("plan", plan_id)
        if not p:
            raise NapakaStoritve("plan ne obstaja", 404)
        return p

    async def _matrike(self, tocke: list[tuple], tipi: list[str], zacetki: int) -> tuple[dict, dict]:
        """Osnova: hitra ocena za celotno matriko; vrstice od enot do ciljev
        izboljša usmerjevalni servis (rok 1,5 s, sicer ostane ocena)."""
        casi, razdalje = {}, {}
        for t in tipi:
            f = helikopter if t == "helikopter" else (lambda a, b, t=t: zracna_ocena(a, b, t))
            C = [[0.0] * len(tocke) for _ in tocke]
            R = [[0.0] * len(tocke) for _ in tocke]
            for i, a in enumerate(tocke):
                for j, b in enumerate(tocke):
                    if i != j and a != (0.0, 0.0) and b != (0.0, 0.0):
                        p = f(a, b)
                        C[i][j], R[i][j] = p.cas_s, p.razdalja_m
            casi[t], razdalje[t] = C, R
        cestni = [t for t in tipi if t != "helikopter"]
        izvori = tocke[:zacetki]
        cilji = [(j, b) for j, b in enumerate(tocke[zacetki:], start=zacetki) if b != (0.0, 0.0)]
        try:
            for t in cestni:
                m = await asyncio.wait_for(self.usmerjanje.matrika(izvori, [b for _, b in cilji], t), 1.5)
                for i in range(len(izvori)):
                    for (j, _), r in zip(cilji, m[i]):
                        casi[t][i][j], razdalje[t][i][j] = r["cas_s"], r["razdalja_m"]
        except asyncio.TimeoutError:
            dnevnik.info("matrika: rok presežen – uporabljena ocena")
        return casi, razdalje

    async def predlagaj_plan(self, razlog: str, zelena_enota: tuple | None = None) -> dict | None:
        async with self._plan_zaklep:
            st = self.stanje()
            prejsnji = self.plan_aktiven()
            if zelena_enota and prejsnji:
                # dispečer je izbral enoto -> uporabi jo kot "prejšnjo dodelitev" (kazen za spremembo)
                prejsnji = copy.deepcopy(prejsnji)
                prejsnji["koraki_po_enoti"].setdefault(zelena_enota[1], []).insert(0, {"naloga_id": zelena_enota[0], "akcija": "prevzem"})
            n_zacetkov = len([e for e in st["enote"] if e["stanje"] != "okvara"])
            opombe = preusmeri_po_zmogljivosti(st)  # najprej, da se vozlišča ujemajo z matriko
            tocke = [(v.lat, v.lon) for v in sestavi_model(copy.deepcopy(st), time.time()).vozlisca]
            casi, razdalje = await self._matrike(tocke, sorted({e["tip"] for e in st["enote"] if e["stanje"] != "okvara"}), n_zacetkov)
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            planovi = await loop.run_in_executor(None, lambda: planiraj(st, lambda *_: (casi, razdalje), prejsnji, self.nast))
            for p in planovi:
                p["opombe"] = opombe + p.get("opombe", [])
                p["primerjava"] = primerjaj(prejsnji if not zelena_enota else self.plan_aktiven(), p)
            if not planovi:
                self.objava.objavi("opozorilo", {"vrsta": "plan", "sporocilo": "Plana ni bilo mogoče izračunati."})
                return None
            self._shrani_stanje(st)  # morebitne preusmeritve centrov
            for p in self.db.vsi("plan", stanje="predlog"):
                p["stanje"] = "zamenjan"
                self.db.shrani("plan", p)
            verzija = len(self.db.vsi("plan")) + 1
            glavni = None
            for i, p in enumerate(planovi):
                p.update(id=M.nov_id("plan"), verzija=verzija, ustvarjen=time.time(), stanje="predlog",
                         razlog=razlog, prejsnji_id=prejsnji["id"] if prejsnji else None,
                         razlike_od_prejsnjega=p["primerjava"]["spremenjene_dodelitve"], cas_racunanja_s=round(time.perf_counter() - t0, 2))
                if i == 0:
                    glavni = p
                else:
                    p["alternativa_od"] = glavni["id"]
                self.db.shrani("plan", p)
            glavni["alternative"] = [p["id"] for p in planovi[1:]]
            self.db.shrani("plan", glavni)
            self.db.revizija(SISTEM, "Plan", glavni["id"], "predlog_plana", prejsnji["id"] if prejsnji else None,
                             {"razlog": razlog, "razlaga": glavni["primerjava"]["razlaga"]})
            self.objava.objavi("plan_predlog", glavni)
            return glavni

    def alternative(self, plan_id: str) -> list[dict]:
        p = self.plan(plan_id)
        glavni = self.plan(p["alternativa_od"]) if p.get("alternativa_od") else p
        return [self.plan(a) for a in glavni.get("alternative", [])] + ([glavni] if p.get("alternativa_od") else [])

    def potrdi_plan(self, plan_id: str, uporabnik: str) -> dict:
        p = self.plan(plan_id)
        if p["stanje"] != "predlog":
            raise NapakaStoritve(f"plan je {p['stanje']}", 409)
        star = self.plan_aktiven()
        if star:
            star["stanje"] = "zamenjan"
            self.db.shrani("plan", star)
        for x in self.db.vsi("plan", stanje="predlog"):
            if x["id"] != plan_id:
                x["stanje"] = "zamenjan"
                self.db.shrani("plan", x)
        p["stanje"], p["potrdil"], p["potrjen_ob"] = "aktiven", uporabnik, time.time()
        self.db.shrani("plan", p)
        # stanje enot po potrjenem planu
        for e in self.db.vsi("enota"):
            if e["stanje"] == "okvara":
                continue
            e["stanje"] = "na_nalogi" if p["koraki_po_enoti"].get(e["id"]) else "prosto"
            self.db.shrani("enota", e)
        self.db.revizija(uporabnik, "Plan", plan_id, "potrdi_plan", star["id"] if star else None, plan_id)
        self.objava.objavi("plan_aktiven", p)
        self.objava.objavi("enote", self.db.vsi("enota"))
        return p

    def zavrni_plan(self, plan_id: str, uporabnik: str, razlog: str | None = None) -> dict:
        p = self.plan(plan_id)
        if p["stanje"] != "predlog":
            raise NapakaStoritve(f"plan je {p['stanje']}", 409)
        p["stanje"], p["razlog_zavrnitve"] = "zamenjan", razlog
        self.db.shrani("plan", p)
        self.db.revizija(uporabnik, "Plan", plan_id, "zavrni_plan", "predlog", {"razlog": razlog})
        self.objava.objavi("plan_zavrnjen", p)
        return p


def preveri_vrednost(polje: str, v):
    """Ročne vrednosti dispečerja – oblika, ne vsebina."""
    try:
        if polje == "prioriteta":
            v = int(v)
            assert 1 <= v <= 5
        elif polje == "stevilo_poskodovanih":
            v = int(v)
            assert v >= 0
        elif polje == "lokacija":
            v = {"lat": float(v["lat"]), "lon": float(v["lon"])}
            assert -90 <= v["lat"] <= 90 and -180 <= v["lon"] <= 180
        elif polje == "potrebna_oprema":
            v = [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip()]
        elif polje == "vrsta_dogodka":
            assert v in M.VRSTE_DOGODKA
        elif polje == "zaznani_dogodek_plana":
            assert v["tip"] in M.TIPI_DOGODKOV and isinstance(v.get("parametri", {}), dict)
            v = {"tip": v["tip"], "parametri": v.get("parametri", {})}
        elif polje == "vprasanja_za_klicatelja":
            v = [str(x) for x in v]
        else:
            v = str(v).strip()
            assert v
    except (AssertionError, KeyError, TypeError, ValueError) as e:
        raise NapakaStoritve(f"neveljavna vrednost za {polje}", 422) from e
    return v
