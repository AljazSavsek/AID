"""Ekipa <-> dispečer: sporočila, predlogi ekip (okvara, ovira, prevoz z nujne vožnje, »ni na voljo«)
in eskalacije nepotrjenih planov.

Predlogi ekip so dogodki plana z `vir="ekipa"` in `stanje="predlagano"`. Model plana se ne spremeni,
dokler dispečer predloga ne sprejme (ali spremeni in sprejme). Šele nato planer predlaga nov plan,
ki ga dispečer potrdi posebej (enako kot pri dogodkih iz klica).
"""
from __future__ import annotations

import re
import secrets
import time

from dnevnik import model as M
from usmerjanje.ponudniki import zracna_razdalja_m

TABELE = """
CREATE TABLE IF NOT EXISTS sporocilo (
    id TEXT PRIMARY KEY, enota_id TEXT NOT NULL, smer TEXT NOT NULL, koda TEXT, besedilo TEXT NOT NULL,
    nujno INTEGER NOT NULL DEFAULT 0, cas REAL NOT NULL, poslal TEXT NOT NULL, prebrano REAL);
CREATE INDEX IF NOT EXISTS sporocilo_enota ON sporocilo(enota_id, cas);
"""

HITRA_VOZNIK = {"prejeto": "Prejeto", "na_poti": "Na poti", "zamuda": "Zamuda", "potrebujem_pomoc": "Potrebujem pomoč",
                "poklici_me": "Pokliči me", "policija": "Policija potrebna"}
NUJNA_VOZNIK = ("potrebujem_pomoc", "policija")
HITRA_DISPECER = {"poklici_center": "Pokličite center", "pocakaj": "Počakajte na prizorišču",
                  "preveri_nalogo": "Preverite nalogo", "vrni_se": "Vrnite se v bazo"}
TIPI_PREDLOGOV = ("okvara_vozila", "ovira_na_cesti", "prevoz_z_nujne_voznje", "enota_ni_na_voljo")
# parametri, ki jih dispečer lahko spremeni pred sprejemom (enota predlagatelja ostane)
UREDLJIVO = {"okvara_vozila": ("tovor_ostane", "opis"), "ovira_na_cesti": ("vrsta", "opis", "lokacija"),
             "prevoz_z_nujne_voznje": ("center_id", "pacienti"), "enota_ni_na_voljo": ("razlog",)}
_KONTROLNI = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class NapakaEkipe(Exception):
    def __init__(self, sporocilo: str, koda: int = 409):
        super().__init__(sporocilo)
        self.koda = koda


def ocisti(besedilo: str | None, najvec: int = 500) -> str:
    return _KONTROLNI.sub("", (besedilo or "")).strip()[:najvec]


class Ekipa:
    def __init__(self, center, naloge, ura=time.time):
        self.c, self.nv, self.db, self.ura = center, naloge, center.db, ura
        self.db.skripta(TABELE)

    # ================= sporočila =================
    def _shrani(self, enota_id: str, smer: str, koda: str | None, besedilo: str, nujno: bool, poslal: str) -> dict:
        s = {"id": "m-" + secrets.token_hex(6), "enota_id": enota_id, "smer": smer, "koda": koda, "besedilo": besedilo,
             "nujno": bool(nujno), "cas": self.ura(), "poslal": poslal, "prebrano": None}
        self.db.izvedi("INSERT INTO sporocilo VALUES (?,?,?,?,?,?,?,?,?)",
                       (s["id"], enota_id, smer, koda, besedilo, int(nujno), s["cas"], poslal, None))
        return s

    def od_voznika(self, enota_id: str, koda: str | None, besedilo: str | None) -> dict:
        besedilo = ocisti(besedilo)
        if bool(koda) == bool(besedilo):
            raise NapakaEkipe("pošljite hitro sporočilo ALI besedilo", 422)
        if koda and koda not in HITRA_VOZNIK:
            raise NapakaEkipe("neznano hitro sporočilo", 422)
        s = self._shrani(enota_id, "od_voznika", koda, HITRA_VOZNIK.get(koda, besedilo), koda in NUJNA_VOZNIK,
                         f"voznik {enota_id}")
        self.db.revizija(f"voznik {enota_id}", "Sporocilo", s["id"], "sporocilo_voznika", None,
                         {"koda": koda, "besedilo": s["besedilo"]})
        self.c.objava.objavi("sporocilo", s)
        if s["nujno"]:
            self.c.objava.objavi("opozorilo", {"vrsta": "ekipa", "enota_id": enota_id, "nujno": True,
                                               "sporocilo": f"{enota_id}: {s['besedilo'].upper()}"})
        return s

    async def ai_predlog(self, enota_id: str, besedilo: str) -> dict | None:
        """Prosto besedilo ekipe -> AI poslušalec -> predlog dogodka (npr. »Vozilo je obstalo«).
        AI samo predlaga: predlog gre skozi `predlagaj` in čaka na dispečerja. Brez AI -> None."""
        seg = {"id": "ekipa", "govorec": "klicatelj", "od_s": 0.0, "do_s": 0.0, "besedilo": besedilo}
        try:
            izhod = await self.c.poslusalec.obdelaj([seg], [], [], [])
        except Exception:  # noqa: BLE001 – AI ni nujen; sporočilo je že dostavljeno
            return None
        for z in (izhod or {}).get("zaznani_dogodki", []):
            if z["tip"] != "okvara_vozila":
                continue
            if z["parametri"].get("enota_id") not in (None, enota_id):
                continue  # o drugem vozilu odloča dispečer iz sporočila
            try:
                d = self.predlagaj(enota_id, "okvara_vozila", {"opis": besedilo})
            except NapakaEkipe:
                return None  # že čaka ali enota je že v okvari
            d.update(dokazi=z["dokazi"], zaupanje=z["zaupanje"], predlagal=f"AI iz sporočila {enota_id}")
            if z["parametri"].get("tovor_ostane") is False:
                d["parametri"]["tovor_ostane"] = False
            self.db.shrani("dogodek", d)
            self.db.revizija("AI poslušalec", "Dogodek", d["id"], "predlog_ai", None, {"iz_sporocila": besedilo, "dokazi": z["dokazi"]})
            return {"id": d["id"], "tip": d["tip"]}
        return None

    def od_dispecerja(self, enota_id: str, uporabnik: str, koda: str | None, besedilo: str | None) -> dict:
        if not self.db.beri("enota", enota_id):
            raise NapakaEkipe("enota ne obstaja", 404)
        besedilo = ocisti(besedilo)
        if not koda and not besedilo:
            raise NapakaEkipe("prazno sporočilo", 422)
        if koda and koda not in HITRA_DISPECER:
            raise NapakaEkipe("neznano hitro sporočilo", 422)
        # hitro sporočilo + besedilo: pošlje se oboje (besedilo se nikoli ne izgubi)
        celo = " – ".join(x for x in (HITRA_DISPECER.get(koda), besedilo) if x)
        s = self._shrani(enota_id, "od_dispecerja", koda, celo, False, uporabnik)
        self.db.revizija(uporabnik, "Sporocilo", s["id"], "sporocilo_dispecerja", None,
                         {"enota_id": enota_id, "besedilo": s["besedilo"]})
        self.nv.objava.objavi(enota_id, "sporocilo", s)
        self.c.objava.objavi("sporocilo", s)
        return s

    def prebrano(self, enota_id: str, sporocilo_id: str) -> dict:
        r = self.db.izvedi("SELECT * FROM sporocilo WHERE id=? AND enota_id=? AND smer='od_dispecerja'", (sporocilo_id, enota_id))
        if not r:
            raise NapakaEkipe("sporočilo ne obstaja", 404)
        if not r[0]["prebrano"]:
            zdaj = self.ura()
            self.db.izvedi("UPDATE sporocilo SET prebrano=? WHERE id=?", (zdaj, sporocilo_id))
            self.c.objava.objavi("sporocilo_prebrano", {"id": sporocilo_id, "enota_id": enota_id, "prebrano": zdaj})
        return {"id": sporocilo_id, "prebrano": True}

    def za_voznika(self, enota_id: str) -> list[dict]:
        """Za napravo: zadnjih 10 sporočil dispečerja (lastna hitra sporočila jih ne izrinejo) + 5 lastnih."""
        disp = self.db.izvedi("SELECT * FROM sporocilo WHERE enota_id=? AND smer='od_dispecerja' ORDER BY cas DESC LIMIT 10", (enota_id,))
        moja = self.db.izvedi("SELECT * FROM sporocilo WHERE enota_id=? AND smer='od_voznika' ORDER BY cas DESC LIMIT 5", (enota_id,))
        return sorted((dict(r, nujno=bool(r["nujno"])) for r in disp + moja), key=lambda m: -m["cas"])

    def sporocila(self, enota_id: str | None = None, omejitev: int = 30) -> list[dict]:
        if enota_id:
            vr = self.db.izvedi("SELECT * FROM sporocilo WHERE enota_id=? ORDER BY cas DESC LIMIT ?", (enota_id, omejitev))
        else:
            vr = self.db.izvedi("SELECT * FROM sporocilo ORDER BY cas DESC LIMIT ?", (omejitev,))
        return [dict(r, nujno=bool(r["nujno"])) for r in vr]

    # ================= predlogi ekip =================
    def _odprt(self, enota_id: str, tip: str) -> dict | None:
        return next((d for d in self.db.vsi("dogodek", stanje="predlagano", vir="ekipa")
                     if d.get("enota_id") == enota_id and d["tip"] == tip), None)

    def _lokacija(self, enota_id: str) -> dict:
        e = next((x for x in self.c.enote() if x["id"] == enota_id), None)
        return {"lat": e["lat"], "lon": e["lon"], "vir": (e.get("polozaj") or {}).get("vir", "zadnji_znani")} if e else {}

    def _najblizji_center(self, lok: dict) -> str | None:
        centri = sorted((c for c in self.db.vsi("center") if c["sprejemna_mesta"] - c["zasedena"] > 0),
                        key=lambda c: zracna_razdalja_m((lok["lat"], lok["lon"]), (c["lat"], c["lon"])))
        return centri[0]["id"] if centri else None

    def predlagaj(self, enota_id: str, tip: str, vhod: dict | None = None) -> dict:
        """Predlog ekipe. Parametre sestavi strežnik: enota iz žetona, lokacija iz zadnjega položaja."""
        vhod = vhod or {}
        if tip not in TIPI_PREDLOGOV:
            raise NapakaEkipe("neznan predlog", 422)
        if self._odprt(enota_id, tip):
            raise NapakaEkipe("predlog te vrste že čaka na dispečerja")
        e = self.db.beri("enota", enota_id)
        lok = self._lokacija(enota_id)
        if tip == "okvara_vozila":
            if e["stanje"] == "okvara":
                raise NapakaEkipe("enota je že v okvari")
            par = {"enota_id": enota_id, "lokacija": {"lat": lok["lat"], "lon": lok["lon"]}, "tovor_ostane": True,
                   "opis": ocisti(vhod.get("opis"), 200)}
        elif tip == "ovira_na_cesti":
            if vhod.get("vrsta") not in M.VRSTE_OVIR:
                raise NapakaEkipe("neznana vrsta ovire", 422)
            mesto = vhod.get("lokacija") or {"lat": lok["lat"], "lon": lok["lon"]}
            par = {"vrsta": vhod["vrsta"], "lokacija": {"lat": float(mesto["lat"]), "lon": float(mesto["lon"])},
                   "opis": ocisti(vhod.get("opis"), 200), "porocal": enota_id}
        elif tip == "prevoz_z_nujne_voznje":
            k = self.nv.trenutni(enota_id)
            n = self.db.beri("naloga", k["naloga_id"]) if k else None
            if not k or k["akcija"] != "prevzem" or (n or {}).get("dostava") or self.nv.faza(enota_id, k) != "na_prizoriscu":
                raise NapakaEkipe("prevoz predlagate na prizorišču nujne vožnje")
            par = {"enota_id": enota_id, "naloga_id": k["naloga_id"], "center_id": self._najblizji_center(lok), "pacienti": 1}
        else:  # enota_ni_na_voljo
            if e.get("tovor"):
                raise NapakaEkipe("v vozilu je tovor – najprej dostava")
            par = {"enota_id": enota_id, "razlog": ocisti(vhod.get("razlog"), 200)}
        d = M.dogodek(tip, par, "ekipa", "predlagano")
        d.update(enota_id=enota_id, predlagal=f"voznik {enota_id}")
        self.db.shrani("dogodek", d)
        self.db.revizija(f"voznik {enota_id}", "Dogodek", d["id"], "predlog_ekipe", None, {"tip": tip, "parametri": par})
        self.c.objava.objavi("dogodek_predlog", d)
        self.c.objava.objavi("opozorilo", {"vrsta": "ekipa", "enota_id": enota_id, "nujno": tip == "okvara_vozila",
                                           "sporocilo": f"{enota_id}: predlog ekipe – {OPISI[tip]}. Potrdite ali zavrnite."})
        self.nv.objava.objavi(enota_id, "naloga", {"razlog": "predlog"})
        return d

    def predlog(self, dogodek_id: str) -> dict:
        d = self.db.beri("dogodek", dogodek_id)
        if not d or d.get("vir") != "ekipa":
            raise NapakaEkipe("predlog ne obstaja", 404)
        return d

    async def odloci(self, dogodek_id: str, uporabnik: str, akcija: str, parametri: dict | None = None,
                     razlog: str | None = None) -> dict:
        d = self.predlog(dogodek_id)
        if d["stanje"] != "predlagano":
            raise NapakaEkipe(f"predlog je že {d['stanje']}")
        stari = dict(d["parametri"])
        if akcija == "zavrni":
            d.update(stanje="zavrnjeno", razlog=ocisti(razlog, 200) or None, potrdil=uporabnik, odloceno=self.ura())
            self.db.shrani("dogodek", d)
            self.db.revizija(uporabnik, "Dogodek", d["id"], "zavrni_predlog_ekipe", stari, {"razlog": d["razlog"]})
            plan = None
        else:
            if akcija == "spremeni":
                dovoljeno = UREDLJIVO[d["tip"]]
                d["parametri"] = {**stari, **{k: v for k, v in (parametri or {}).items() if k in dovoljeno}}
            d.update(potrdil=uporabnik, odloceno=self.ura())
            plan = await self.c.uveljavi_dogodek(d, uporabnik, planiraj=d["tip"] != "ovira_na_cesti")
            self.db.revizija(uporabnik, "Dogodek", d["id"], "sprejmi_predlog_ekipe" if akcija == "sprejmi" else "spremeni_predlog_ekipe",
                             stari, d["parametri"])
        self.c.objava.objavi("dogodek_posodobljen", d)
        self.nv.objava.objavi(d["enota_id"], "predlog", {"id": d["id"], "tip": d["tip"], "stanje": d["stanje"]})
        return {"dogodek": d, "plan_predlog": plan}

    def predlogi(self, samo_odprti: bool = True) -> list[dict]:
        vsi = [d for d in self.db.vsi("dogodek", vir="ekipa")]
        return [d for d in vsi if d["stanje"] == "predlagano"] if samo_odprti else vsi[-50:]

    def predlogi_enote(self, enota_id: str, omejitev: int = 5) -> list[dict]:
        ds = [d for d in self.db.vsi("dogodek", vir="ekipa") if d.get("enota_id") == enota_id]
        return [{k: d.get(k) for k in ("id", "tip", "stanje", "ustvarjeno", "razlog", "parametri")} for d in ds[-omejitev:]]

    def ovire(self) -> list[dict]:
        return [d for d in self.db.vsi("dogodek") if d["tip"] == "ovira_na_cesti" and d["stanje"] == "sprejeto"]


OPISI = {"okvara_vozila": "okvara vozila", "ovira_na_cesti": "ovira na cesti",
         "prevoz_z_nujne_voznje": "pacient naložen – izberite bolnišnico", "enota_ni_na_voljo": "enota ni na voljo"}
