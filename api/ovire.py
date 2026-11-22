"""Ovire na cesti med nujno vožnjo (regijski center).

Potek (vse odločitve sprejme dispečer, pravilo 2.1):
1. ovira postane aktivna (potrjen predlog ekipe ali prometna informacija) -> sistem zazna vozila,
   katerih poslana pot gre čez oviro (`vpliv`) in obvesti dispečerja;
2. dispečer odpre predlog obvoza (`predlog_obvoza`: stara proti novi poti, ki se oviri izogne) in ga
   potrdi (`potrdi_obvoz`) -> voznik takoj dobi novo pot, navodila in sporočilo dispečerja;
3. če vozilo za oviro obtiči (`ujeta_enota`), obvoz ni mogoč: dispečer izbere drugo vozilo
   (`kandidati` – časi prihoda se izogibajo oviram) in nalogo prerazporedi (`dodeli`) -> predlog plana,
   ki ga potrdi; prvo vozilo dobi obvestilo, da je nalogo prevzela druga enota.
"""
from __future__ import annotations

import math
import time

from usmerjanje.ponudniki import zracna_razdalja_m

POLMER_M = 150        # polmer območja ovire, ki se mu pot izogne
BLIZINA_M = 60        # dodatni rob pri zaznavi, ali gre pot čez oviro


class NapakaOvire(Exception):
    def __init__(self, sporocilo: str, koda: int = 409):
        super().__init__(sporocilo)
        self.koda = koda


def pravokotnik(lat: float, lon: float, polmer_m: float = POLMER_M) -> tuple:
    dlat = polmer_m / 111320
    dlon = polmer_m / (111320 * max(0.2, math.cos(math.radians(lat))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def v_pravokotniku(t: tuple, r: tuple) -> bool:
    return r[0] <= t[0] <= r[2] and r[1] <= t[1] <= r[3]


class Ovire:
    def __init__(self, center):
        self.c = center

    # ---------- aktivne ovire ----------
    def aktivne(self) -> list[dict]:
        out = []
        for d in self.c.db.vsi("dogodek"):
            p = d.get("parametri") or {}
            if d["tip"] != "ovira_na_cesti" or d["stanje"] not in ("sprejeto", "spremenjeno") or p.get("odstranjena"):
                continue
            lok = p.get("lokacija") or {}
            if lok.get("lat") is None:
                continue
            out.append({"id": d["id"], "vrsta": p.get("vrsta", "drugo"), "opis": p.get("opis") or "", "lat": float(lok["lat"]),
                        "lon": float(lok["lon"]), "polmer_m": float(p.get("polmer_m") or POLMER_M),
                        "ujeta_enota": p.get("ujeta_enota"), "zamuda_min": p.get("zamuda_min"), "ustvarjeno": d.get("ustvarjeno")})
        return out

    def izogibanja(self, od: tuple, do: tuple) -> tuple[list, list]:
        """Pravokotniki aktivnih ovir za izračun poti (brez tistih, v katerih sta začetek ali cilj –
        tam se ni mogoče izogniti; za obtičano vozilo velja prerazporeditev)."""
        rects, ids = [], []
        for o in self.aktivne():
            r = pravokotnik(o["lat"], o["lon"], o["polmer_m"])
            if v_pravokotniku(od, r) or v_pravokotniku(do, r):
                continue
            rects.append(r)
            ids.append(o["id"])
        return rects, ids

    # ---------- vpliv na poslane poti ----------
    def vpliv(self) -> list[dict]:
        nv = self.c.naloge_voznikov
        ovire = self.aktivne()
        if not ovire:
            return []
        out = []
        for e in self.c.enote():
            k = nv.trenutni(e["id"])
            if not k:
                continue
            pot = nv.pot(e["id"]) or {}
            geo = pot.get("geometrija") or []
            izogiba = set(pot.get("ovire") or [])
            for o in ovire:
                ujeta = o["ujeta_enota"] == e["id"]
                if not ujeta:
                    if o["id"] in izogiba or not geo:
                        continue
                    meja = o["polmer_m"] + BLIZINA_M
                    if not any(zracna_razdalja_m((o["lat"], o["lon"]), tuple(t)) <= meja for t in geo):
                        continue
                out.append({"ovira": o, "enota_id": e["id"], "naloga_id": k["naloga_id"], "akcija": k["akcija"],
                            "prioriteta": k.get("prioriteta"), "ujeta": ujeta, "obvoz_mogoc": not ujeta,
                            "trenutna_pot": {"cas_s": pot.get("cas_s"), "razdalja_m": pot.get("razdalja_m")} if pot else None})
        return out

    def objavi_vpliv(self) -> list[dict]:
        v = self.vpliv()
        self.c.objava.objavi("ovire", {"ovire": self.aktivne(), "vpliv": v})
        for x in v:
            o = x["ovira"]
            self.c.objava.objavi("opozorilo", {
                "vrsta": "ovira", "enota_id": x["enota_id"], "nujno": True,
                "sporocilo": (f"{x['enota_id']} je obtičal ({o['vrsta']}) – obvoz ni mogoč, pošljite drugo enoto."
                              if x["ujeta"] else f"Ovira ({o['vrsta']}) na poti {x['enota_id']} – preverite obvoz.")})
        return v

    # ---------- obvoz ----------
    async def predlog_obvoza(self, enota_id: str) -> dict:
        nv = self.c.naloge_voznikov
        k = nv.trenutni(enota_id)
        e = next((x for x in self.c.enote() if x["id"] == enota_id), None)
        if not k or not e:
            raise NapakaOvire("enota nima trenutne naloge", 404)
        if any(o["ujeta_enota"] == enota_id for o in self.aktivne()):
            return {"mogoc": False, "razlog": f"{enota_id} je obtičal za oviro – obvoz ni mogoč. Pošljite drugo enoto."}
        stara = nv.pot(enota_id) or {}
        od, do = (e["lat"], e["lon"]), (k["lat"], k["lon"])
        rects, ids = self.izogibanja(od, do)
        nova = await self.c.usmerjanje.izracunaj(od, do, e["tip"], izogibaj=rects)
        return {"mogoc": True, "enota_id": enota_id, "naloga_id": k["naloga_id"], "ovire": ids,
                "stara": {"cas_s": stara.get("cas_s"), "razdalja_m": stara.get("razdalja_m"), "geometrija": stara.get("geometrija", [])},
                "nova": {"cas_s": nova["cas_s"], "razdalja_m": nova["razdalja_m"], "geometrija": self.c._redci(nova["geometrija"], 400),
                         "kakovost": nova["kakovost"], "izogiba_oviram": nova.get("izogiba_oviram")},
                "razlika_s": (nova["cas_s"] - stara["cas_s"]) if stara.get("cas_s") is not None else None}

    async def potrdi_obvoz(self, enota_id: str, uporabnik: str) -> dict:
        nv = self.c.naloge_voznikov
        if any(o["ujeta_enota"] == enota_id for o in self.aktivne()):
            raise NapakaOvire(f"{enota_id} je obtičal – obvoz ni mogoč, pošljite drugo enoto.")
        stara = nv.pot(enota_id) or {}
        p = await nv.izracunaj_pot(enota_id)  # izračun poti se izogne aktivnim oviram
        if not p:
            raise NapakaOvire("poti ni bilo mogoče izračunati")
        vrste = ", ".join(sorted({o["vrsta"] for o in self.aktivne() if o["id"] in (p.get("ovire") or [])})) or "ovira"
        self.c.db.revizija(uporabnik, "Enota", enota_id, "potrdi_obvoz",
                           {"cas_s": stara.get("cas_s"), "razdalja_m": stara.get("razdalja_m")},
                           {"cas_s": p["cas_s"], "razdalja_m": p["razdalja_m"], "ovire": p.get("ovire")})
        # voznik: nova pot (dogodek »pot«) + vidno sporočilo dispečerja
        self.c.ekipa.od_dispecerja(enota_id, uporabnik, None,
                                   f"OBVOZ zaradi ovire ({vrste}). Nova pot je na zaslonu – prihod čez {max(1, round(p['cas_s'] / 60))} min.")
        self.c.objava.objavi("obvoz", {"enota_id": enota_id, "naloga_id": p["naloga_id"], "cas_s": p["cas_s"], "potrdil": uporabnik})
        self.objavi_vpliv()
        return p

    # ---------- druga enota ----------
    async def kandidati(self, naloga_id: str) -> list[dict]:
        n = self.c.db.beri("naloga", naloga_id)
        if not n:
            raise NapakaOvire("naloga ne obstaja", 404)
        cilj = n["prevzem"]
        ujete = {o["ujeta_enota"]: o for o in self.aktivne() if o["ujeta_enota"]}
        sedanja = next((e for e, ks in (self.c.plan_aktiven() or {}).get("koraki_po_enoti", {}).items()
                        if any(k["naloga_id"] == naloga_id for k in ks)), None)
        out = []
        enote = {e["id"]: e for e in self.c.db.vsi("enota")}
        for x in await self.c.eta_enot(cilj["lat"], cilj["lon"], omejitev=12, izogibaj_ovire=True):
            x["primerno"], x["razlog"] = self._primerno(n, enote.get(x["enota_id"]) or {})
            if x["enota_id"] in ujete:
                x["obtical"] = True
                x["cas_s"] += int((ujete[x["enota_id"]].get("zamuda_min") or 30) * 60)
            x["sedanja"] = x["enota_id"] == sedanja
            out.append(x)
        out.sort(key=lambda x: (not x["primerno"], x.get("obtical", False), x["stanje"] != "prosto", x["cas_s"]))
        return out

    @staticmethod
    def _primerno(n: dict, e: dict) -> tuple[bool, str | None]:
        """Ali vozilo lahko opravi nalogo (kapaciteta pacientov/opreme)."""
        kol, kap = n.get("kolicina") or {}, e.get("kapaciteta") or {}
        if (kol.get("pacienti") or 0) > (kap.get("pacienti") or 0):
            return False, "ne prevaža pacientov"
        if (kol.get("oprema") or 0) > (kap.get("oprema") or 0):
            return False, "premalo prostora za opremo"
        return True, None

    async def dodeli(self, naloga_id: str, enota_id: str, uporabnik: str) -> dict | None:
        n = self.c.db.beri("naloga", naloga_id)
        if not n:
            raise NapakaOvire("naloga ne obstaja", 404)
        e = self.c.db.beri("enota", enota_id)
        if not e:
            raise NapakaOvire("enota ne obstaja", 404)
        ok, razlog = self._primerno(n, e)
        if not ok:
            raise NapakaOvire(f"{enota_id} {razlog}", 422)
        stara = n.get("dodeljena_enota")
        n["dodeljena_enota"] = enota_id
        n["zelena_enota"] = enota_id
        self.c.db.shrani("naloga", n)
        self.c.db.revizija(uporabnik, "Naloga", naloga_id, "prerazporedi", stara, enota_id)
        return await self.c.predlagaj_plan(f"prerazporeditev {naloga_id}: {stara or '—'} → {enota_id}", zelena_enota=(naloga_id, enota_id))

    # ---------- demo ----------
    async def demo(self, faza: str, enota_id: str | None, uporabnik: str) -> dict:
        """Simulacija: 'zapora' na poslani poti vozila (prometna informacija) ali 'obtical' (vozilo za nesrečo)."""
        nv = self.c.naloge_voznikov
        if not enota_id:
            # le vozila, ki res vozijo (pot po cestah; ne tista, ki že stojijo na mestu prevzema)
            kandidati = [e["id"] for e in self.c.enote() if nv.trenutni(e["id"]) and len((nv.pot(e["id"]) or {}).get("geometrija") or []) >= 4]
            nujni = [eid for eid in kandidati if (nv.trenutni(eid) or {}).get("nujna")]
            enota_id = (nujni or kandidati or [None])[0]
        if not enota_id or not nv.trenutni(enota_id):
            raise NapakaOvire("ni vozila z nalogo – najprej pošljite vozilo in potrdite plan", 422)
        pot = nv.pot(enota_id) or await nv.izracunaj_pot(enota_id)
        e = next(x for x in self.c.enote() if x["id"] == enota_id)
        from dnevnik import model as M
        if faza == "zapora":
            geo = pot.get("geometrija") or []
            if len(geo) < 4:
                raise NapakaOvire("pot je prekratka za simulacijo", 422)
            t = geo[int(len(geo) * 0.45)]
            par = {"vrsta": "zapora", "opis": "Zapora ceste (prometne informacije)", "lokacija": {"lat": t[0], "lon": t[1]}}
            d = M.dogodek("ovira_na_cesti", par, "sistem", "sprejeto")
            d["predlagal"] = "prometne informacije (simulacija)"
        else:
            par = {"vrsta": "nesreca", "opis": f"Nesreča pred {enota_id} – vozilo stoji v zastoju", "porocal": enota_id,
                   "lokacija": {"lat": e["lat"], "lon": e["lon"]}, "ujeta_enota": enota_id, "zamuda_min": 30}
            d = M.dogodek("ovira_na_cesti", par, "ekipa", "sprejeto")
            d.update(enota_id=enota_id, predlagal=f"voznik {enota_id} (simulacija)")
            self.c.ekipa.od_voznika(enota_id, None, "Nesreča pred nami, stojimo v zastoju – ne moremo naprej.")
        d["opis"] = par["opis"]
        self.c.db.shrani("dogodek", d)
        self.c.db.revizija(uporabnik, "Dogodek", d["id"], f"demo_ovira_{faza}", None, par)
        self.c.objava.objavi("dogodek", d)
        v = self.objavi_vpliv()
        return {"dogodek": d, "enota_id": enota_id, "vpliv": v}

    def odstrani(self, dogodek_id: str, uporabnik: str) -> dict:
        d = self.c.db.beri("dogodek", dogodek_id)
        if not d or d["tip"] != "ovira_na_cesti":
            raise NapakaOvire("ovira ne obstaja", 404)
        d["parametri"]["odstranjena"] = time.time()
        self.c.db.shrani("dogodek", d)
        self.c.db.revizija(uporabnik, "Dogodek", d["id"], "odstrani_oviro", None, None)
        self.objavi_vpliv()
        return d

