"""Heartbeat voznika: vsakih 30 s naprava pošlje hitrost, lokacijo in pot (trenutni korak in preostanek).

Centrala ob vsakem heartbeatu:
  1. ga shrani v JSON (podatki/heartbeat/<datum>/<enota>.json – seznam heartbeatov dneva, atomarno zapisan),
  2. ga pokaže dispečerju (dogodek `heartbeat`; izpad > IZPAD_S -> opozorilo `heartbeat_izpad`),
  3. preveri, ali je vozilo v zastoju ali je na poti ovira, in mu po potrebi izračuna DRUGO POT:
       - ovira: aktivna ovira (api/ovire.py) leži na preostanku poti, ki se ji pot še ne izogne,
       - zastoj: vozilo med vožnjo stoji (< ZASTOJ_KMH) vsaj ZASTOJ_S, cilj je še daleč -> izogni se odseku pred njim,
       - promet: najhitrejša pot s trenutnim prometom ni več naša (ponudnik predlaga drugo pot).
     Nova pot gre vozniku (dogodek `pot`, kot pri obvozu) in nazaj centrali (dogodek `preusmeritev`, revizija).
Imena ponudnikov poti se nikjer ne pokažejo.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from aplikacija_voznik.jedro.navigacija import razdalja_m, stanje_navigacije

KOREN = Path(__file__).resolve().parent.parent

INTERVAL_S = 30            # naprava pošlje heartbeat vsake pol minute
IZPAD_S = 75               # brez heartbeata 2,5 intervala -> opozorilo dispečerju
ZASTOJ_KMH = 5.0           # pod to hitrostjo vozilo »stoji«
ZASTOJ_S = 55              # toliko časa (dva heartbeata) mora stati
ZASTOJ_PRED_M = (60, 700)  # odsek poti pred vozilom, ki se mu nova pot izogne (od, do v metrih)
BLIZU_CILJA_M = 250        # blizu cilja se ne preusmerja
MED_PREUSMERITVAMI_S = 150  # najmanjši razmik med samodejnimi preusmeritvami iste enote
PROMET_VSAKIH_S = 60       # preverjanje prometa (en izračun poti na enoto na minuto)
DRUGACNA_POT_DELEZ = 0.3   # nova pot je »druga pot«, če je toliko njenih točk daleč od trenutne
DRUGACNA_POT_M = 80


def _ura() -> float:
    return time.time()


class Heartbeati:
    def __init__(self, center, mapa: str | os.PathLike | None = None, ura=_ura):
        self.c, self.ura = center, ura
        self.mapa = Path(mapa or os.environ.get("AID_HEARTBEAT_MAPA") or KOREN / "podatki" / "heartbeat")
        self.zadnji: dict[str, dict] = {}            # enota -> zadnji heartbeat
        self._zgodovina: dict[str, list] = {}        # enota -> zadnji heartbeati (za zastoj)
        self._datoteke: dict[tuple, list] = {}       # (datum, enota) -> heartbeati dneva
        self._zakleni = asyncio.Lock()
        self._zadnja_preusmeritev: dict[str, float] = {}
        self._zadnji_promet: dict[str, float] = {}
        self._izpad_javljen: set[str] = set()
        self._tece: set[str] = set()

    # ---------------- sprejem ----------------
    async def sprejmi(self, e: dict, naprava: str, hb: dict) -> dict:
        zdaj = self.ura()
        nv = self.c.naloge_voznikov
        k = nv.trenutni(e["id"])
        pot = nv.pot(e["id"]) if k else None
        lok = hb["lokacija"]
        hitrost = hb.get("hitrost_kmh")
        nav = stanje_navigacije((lok["lat"], lok["lon"]), pot, (k["lat"], k["lon"]) if k else None,
                                hitrost / 3.6 if hitrost else None) if k else {}
        pot_naprave = hb.get("pot") or {}
        zapis = {
            "ts": round(zdaj, 3), "ts_naprava": hb.get("ts"), "enota_id": e["id"], "naprava_id": naprava,
            "stanje_enote": e.get("stanje"),
            "hitrost_kmh": None if hitrost is None else round(float(hitrost), 1),
            "lokacija": {"lat": round(lok["lat"], 6), "lon": round(lok["lon"], 6),
                         **{x: lok[x] for x in ("natancnost", "smer") if lok.get(x) is not None}},
            "pot": {
                "naloga_id": k["naloga_id"] if k else None, "akcija": k["akcija"] if k else None,
                "cilj": {"lat": k["lat"], "lon": k["lon"]} if k else None,
                "izracunano": (pot or {}).get("izracunano"), "kakovost": (pot or {}).get("kakovost"),
                "skupaj_m": (pot or {}).get("razdalja_m"), "skupaj_s": (pot or {}).get("cas_s"),
                "preostalo_m": _zaokrozi(pot_naprave.get("preostalo_m", nav.get("preostalo_m"))),
                "preostalo_s": _zaokrozi(pot_naprave.get("preostalo_s", nav.get("eta_s"))),
                "odmik_m": _zaokrozi(pot_naprave.get("odmik_m", nav.get("odmik_m"))),
                "na_poti_naprave": pot_naprave.get("izracunano") in (None, (pot or {}).get("izracunano")),
            },
        }
        if hb.get("baterija") is not None:
            zapis["baterija"] = hb["baterija"]
        self.zadnji[e["id"]] = zapis
        zg = self._zgodovina.setdefault(e["id"], [])
        zg.append(zapis)
        del zg[:-6]
        self._izpad_javljen.discard(e["id"])
        await self._shrani(zapis)
        self.c.objava.objavi("heartbeat", self.povzetek(zapis), v_zgodovino=False)
        # zastoj / ovira / promet -> druga pot (v ozadju: odgovor napravi ne čaka na ponudnika poti)
        if k and pot and pot.get("cestna", True) and e["id"] not in self._tece:
            self._tece.add(e["id"])
            self.c._v_ozadju(self._preveri_pot(e, k, pot, zapis))
        return {"prejeto": zapis["ts"], "interval_s": INTERVAL_S}

    @staticmethod
    def povzetek(z: dict) -> dict:
        p = z["pot"]
        return {"enota_id": z["enota_id"], "ts": z["ts"], "hitrost_kmh": z["hitrost_kmh"], "lat": z["lokacija"]["lat"],
                "lon": z["lokacija"]["lon"], "naloga_id": p["naloga_id"], "preostalo_m": p["preostalo_m"],
                "preostalo_s": p["preostalo_s"], "odmik_m": p["odmik_m"]}

    # ---------------- shranjevanje (JSON) ----------------
    def pot_datoteke(self, enota_id: str, datum: str | None = None) -> Path:
        datum = datum or time.strftime("%Y-%m-%d", time.localtime(self.ura()))
        return self.mapa / datum / f"{enota_id}.json"

    async def _shrani(self, z: dict) -> None:
        datum = time.strftime("%Y-%m-%d", time.localtime(z["ts"]))
        kljuc = (datum, z["enota_id"])
        async with self._zakleni:
            if kljuc not in self._datoteke:
                self._datoteke = {k: v for k, v in self._datoteke.items() if k[0] == datum}  # le današnji dan v pomnilniku
                pot = self.pot_datoteke(z["enota_id"], datum)
                try:
                    self._datoteke[kljuc] = json.loads(pot.read_text(encoding="utf-8")) if pot.exists() else []
                except (OSError, ValueError):
                    self._datoteke[kljuc] = []
            self._datoteke[kljuc].append(z)
            await asyncio.to_thread(self._zapisi, self.pot_datoteke(z["enota_id"], datum), list(self._datoteke[kljuc]))

    @staticmethod
    def _zapisi(pot: Path, seznam: list) -> None:
        pot.parent.mkdir(parents=True, exist_ok=True)
        tmp = pot.with_suffix(".tmp")
        tmp.write_text(json.dumps(seznam, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(pot)

    def zgodovina(self, enota_id: str, datum: str | None = None, omejitev: int = 200) -> list[dict]:
        pot = self.pot_datoteke(enota_id, datum)
        try:
            return json.loads(pot.read_text(encoding="utf-8"))[-omejitev:] if pot.exists() else []
        except (OSError, ValueError):
            return []

    # ---------------- izpad heartbeata ----------------
    def preveri_izpade(self) -> list[str]:
        """Enote na nalogi s prijavljeno napravo, ki že IZPAD_S niso poslale heartbeata -> opozorilo (enkrat)."""
        zdaj, out = self.ura(), []
        for eid, z in self.zadnji.items():
            if zdaj - z["ts"] > IZPAD_S and eid not in self._izpad_javljen and self.c.naloge_voznikov.trenutni(eid):
                self._izpad_javljen.add(eid)
                out.append(eid)
                self.c.objava.objavi("opozorilo", {"vrsta": "heartbeat", "enota_id": eid, "nujno": True,
                                                   "sporocilo": f"{eid}: ni heartbeata že {int(zdaj - z['ts'])} s – preverite zvezo z ekipo."})
            elif zdaj - z["ts"] <= IZPAD_S:
                self._izpad_javljen.discard(eid)
        return out

    # ---------------- zastoj / ovira / promet -> druga pot ----------------
    async def _preveri_pot(self, e: dict, k: dict, pot: dict, z: dict) -> None:
        try:
            await self._preveri_pot_notri(e, k, pot, z)
        finally:
            self._tece.discard(e["id"])

    async def _preveri_pot_notri(self, e: dict, k: dict, pot: dict, z: dict) -> None:
        eid, zdaj = e["id"], self.ura()
        polozaj = (z["lokacija"]["lat"], z["lokacija"]["lon"])
        preostalo = z["pot"]["preostalo_m"]
        if preostalo is not None and preostalo < BLIZU_CILJA_M:
            return
        if zdaj - self._zadnja_preusmeritev.get(eid, 0) < MED_PREUSMERITVAMI_S:
            return
        geo = [tuple(t) for t in pot.get("geometrija") or []]
        pred = _odsek_pred(geo, polozaj, *ZASTOJ_PRED_M)
        # 1) ovira na preostanku poti, ki se ji pot še ne izogne
        izogiba = set(pot.get("ovire") or [])
        for o in getattr(self.c, "ovire", None).aktivne() if getattr(self.c, "ovire", None) else []:
            if o["id"] in izogiba or o.get("ujeta_enota") == eid:
                continue
            ostanek = _odsek_pred(geo, polozaj, 0, 1e9)
            if any(razdalja_m((o["lat"], o["lon"]), t) <= o["polmer_m"] + 60 for t in ostanek):
                return await self._preusmeri(e, k, pot, polozaj, [], "ovira",
                                             f"{o['vrsta'].replace('_', ' ')} na poti" + (f" ({o['opis']})" if o.get("opis") else ""))
        # 2) zastoj: vozilo med vožnjo stoji
        zg = [h for h in self._zgodovina.get(eid, []) if h["pot"]["naloga_id"] == k["naloga_id"]]
        stoji = [h for h in zg if h["hitrost_kmh"] is not None and h["hitrost_kmh"] < ZASTOJ_KMH]
        if len(stoji) >= 2 and stoji[-1] is zg[-1] and len(zg) >= 2 and zg[-2] in stoji \
                and zg[-1]["ts"] - zg[-2]["ts"] >= ZASTOJ_S - INTERVAL_S \
                and razdalja_m((zg[-1]["lokacija"]["lat"], zg[-1]["lokacija"]["lon"]),
                               (zg[-2]["lokacija"]["lat"], zg[-2]["lokacija"]["lon"])) < 40 and len(pred) >= 2:
            return await self._preusmeri(e, k, pot, polozaj, [_okvir(pred)], "zastoj",
                                         f"vozilo stoji že {int(zg[-1]['ts'] - zg[-2]['ts']) + INTERVAL_S} s")
        # 3) promet: ali je najhitrejša pot s trenutnim prometom še naša?
        if zdaj - self._zadnji_promet.get(eid, 0) < PROMET_VSAKIH_S:
            return
        self._zadnji_promet[eid] = zdaj
        sveza = await self.c.usmerjanje.izracunaj(polozaj, (k["lat"], k["lon"]), e["tip"])
        if sveza["kakovost"] == "ocena" or len(sveza["geometrija"]) < 3 or len(geo) < 2:
            return
        ostanek = _odsek_pred(geo, polozaj, 0, 1e9)
        if _delez_dalec(sveza["geometrija"], ostanek, DRUGACNA_POT_M) < DRUGACNA_POT_DELEZ:
            return  # najhitrejša pot s trenutnim prometom je še vedno naša
        if (z["pot"]["odmik_m"] or 0) > 80:
            await self._preusmeri(e, k, pot, polozaj, [], "odmik", "vozilo je zapustilo predvideno pot", nova=sveza)
        else:
            await self._preusmeri(e, k, pot, polozaj, [], "promet", "zastoj v prometu – hitrejša pot po drugih cestah", nova=sveza)

    async def _preusmeri(self, e, k, stara, polozaj, izogibi, razlog, opis, nova=None) -> dict | None:
        """Kandidat za drugo pot -> če je res druga (in po cestah), jo shrani vozniku in javi centrali."""
        eid = e["id"]
        cilj = (k["lat"], k["lon"])
        rects = list(self.c.ovire.izogibanja(polozaj, cilj)[0]) if getattr(self.c, "ovire", None) else []
        kandidat = nova if nova is not None and not rects and not izogibi else \
            await self.c.usmerjanje.izracunaj(polozaj, cilj, e["tip"], izogibaj=(rects + list(izogibi)) or None)
        ostanek = _odsek_pred([tuple(t) for t in stara.get("geometrija") or []], polozaj, 0, 1e9)
        if kandidat["kakovost"] == "ocena" or _delez_dalec(kandidat["geometrija"], ostanek, DRUGACNA_POT_M) < DRUGACNA_POT_DELEZ:
            return None  # druge poti po cestah ni (ali je enaka trenutni) – vozilo ostane na trenutni
        info = {"razlog": razlog, "opis": opis, "cas": self.ura(), "prej_s": z_ostanek(stara, polozaj), "prej_m": stara.get("razdalja_m")}
        p = await self.c.naloge_voznikov.izracunaj_pot(eid, od=polozaj, dodatni_izogibi=izogibi, preusmeritev=info)
        if not p:
            return None
        self._zadnja_preusmeritev[eid] = self.ura()
        info.update(zdaj_s=p["cas_s"], zdaj_m=p["razdalja_m"])
        sporocilo = (f"{eid}: {opis} – izračunana druga pot ({max(1, round(p['cas_s'] / 60))} min, {p['razdalja_m'] / 1000:.1f} km)"
                     + (f", po stari poti še ~{max(1, round(info['prej_s'] / 60))} min" if info.get("prej_s") else "") + ".")
        self.c.db.revizija("sistem (heartbeat)", "Enota", eid, "preusmeritev", {"cas_s": info["prej_s"]},
                           {"razlog": razlog, "opis": opis, "cas_s": p["cas_s"], "naloga_id": k["naloga_id"]})
        self.c.objava.objavi("preusmeritev", {"enota_id": eid, "naloga_id": k["naloga_id"], "akcija": k["akcija"], **info,
                                              "geometrija": self.c._redci(p["geometrija"], 200), "sporocilo": sporocilo})
        self.c.objava.objavi("opozorilo", {"vrsta": "preusmeritev", "enota_id": eid, "sporocilo": sporocilo})
        return p


def z_ostanek(pot: dict, polozaj: tuple) -> int | None:
    """Ocena preostalega časa po stari poti (delež preostale razdalje × čas poti)."""
    st = stanje_navigacije(polozaj, pot)
    if st.get("eta_s") is None:
        return None
    return round(st["eta_s"])


# ---------------- geometrija ----------------
def _zaokrozi(x):
    return None if x is None else round(float(x))


def _odsek_pred(geo: list, polozaj: tuple, od_m: float, do_m: float) -> list[tuple]:
    """Točke poti od od_m do do_m pred vozilom (po poti, od najbližje točke naprej)."""
    if len(geo) < 2:
        return []
    i0 = min(range(len(geo)), key=lambda i: razdalja_m(polozaj, geo[i]))
    out, d = [], 0.0
    for i in range(i0, len(geo)):
        if i > i0:
            d += razdalja_m(geo[i - 1], geo[i])
        if d > do_m:
            break
        if d >= od_m:
            out.append(tuple(geo[i]))
    return out


def _okvir(tocke: list[tuple], rob_m: float = 40) -> tuple:
    dlat = rob_m / 111320
    dlon = rob_m / 78000
    return (min(t[0] for t in tocke) - dlat, min(t[1] for t in tocke) - dlon,
            max(t[0] for t in tocke) + dlat, max(t[1] for t in tocke) + dlon)


def _delez_dalec(nova: list, stara: list, prag_m: float) -> float:
    """Delež točk nove poti, ki so dlje od prag_m od stare poti (po točkah; geometrije so goste)."""
    if not nova or not stara:
        return 1.0
    korak = max(1, len(nova) // 60)
    vzorec = nova[::korak]
    dalec = sum(1 for t in vzorec if min(razdalja_m(tuple(t), tuple(s)) for s in stara[::max(1, len(stara) // 300)]) > prag_m)
    return dalec / len(vzorec)
