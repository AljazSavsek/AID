"""Naloge voznika: koraki iz AKTIVNEGA plana, stanja voznika, potrditve prejema, potrjena pot.

Koraki enote so `koraki_po_enoti[enota_id]` aktivnega plana. Ali je korak opravljen, sledi iz
dejstev (naloga opravljena, tovor v vozilu), zato ni ločenega števca napredka, ki bi se lahko
razšel s planerjem. Ločeno se hrani le faza trenutnega koraka (prejeto / na poti / na prizorišču).

Stanja, ki jih sporoči voznik, so dejstva: uveljavijo se takoj, gredo v revizijo in k dispečerju,
NE sprožijo pa novega plana. Plan ostaja v rokah dispečerja.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import secrets
import time

dnevnik = logging.getLogger("voznik")

TABELE = """
CREATE TABLE IF NOT EXISTS voznik_faza (
    enota_id TEXT PRIMARY KEY, naloga_id TEXT NOT NULL, akcija TEXT NOT NULL,
    faza TEXT NOT NULL, cas REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voznik_obvestilo (
    id TEXT PRIMARY KEY, enota_id TEXT NOT NULL, plan_id TEXT NOT NULL, verzija INTEGER,
    ustvarjeno REAL NOT NULL, rok REAL NOT NULL, stanje TEXT NOT NULL, potrjeno REAL, razlike TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS voznik_obvestilo_enota ON voznik_obvestilo(enota_id);
CREATE TABLE IF NOT EXISTS voznik_pot (
    enota_id TEXT PRIMARY KEY, podatki TEXT NOT NULL);
"""

FAZE = ("prejeto", "na_poti", "na_prizoriscu")
KONCNA = ("pacient_nalozen", "dostavljeno", "opravljeno")
IZMENA = ("prosto", "ni_na_voljo")
VSA_STANJA = FAZE + KONCNA + IZMENA
ZAKLJUCENE_NALOGE = ("opravljena", "preklicana", "prenesena")


class NapakaStanja(Exception):
    def __init__(self, sporocilo: str, koda: int = 409):
        super().__init__(sporocilo)
        self.koda = koda


class ObjavaVoznikom:
    """Dogodki za naprave voznikov, ločeno po enotah (WebSocket + polling /voznik/dogodki).
    `zagon` se ob ponovnem zagonu strežnika spremeni -> naprava zavrže svoj seq in vse prebere znova."""

    def __init__(self, velikost: int = 200):
        self.zagon = secrets.token_hex(4)
        self.seq = 0
        self.zgodovina: dict[str, collections.deque] = {}
        self.narocniki: dict[str, set[asyncio.Queue]] = {}
        self.velikost = velikost

    def objavi(self, enota_id: str, tip: str, podatki) -> dict:
        self.seq += 1
        s = {"seq": self.seq, "zagon": self.zagon, "tip": tip, "cas": time.time(), "podatki": podatki}
        self.zgodovina.setdefault(enota_id, collections.deque(maxlen=self.velikost)).append(s)
        for q in list(self.narocniki.get(enota_id, ())):
            try:
                q.put_nowait(s)
            except asyncio.QueueFull:
                pass
        return s

    def od(self, enota_id: str, seq: int, zagon: str | None = None) -> list[dict]:
        vse = list(self.zgodovina.get(enota_id, ()))
        if zagon and zagon != self.zagon:
            return vse
        return [s for s in vse if s["seq"] > seq]


def _kljuc(k: dict) -> tuple:
    return (k["naloga_id"], k["akcija"])


class NalogeVoznikov:
    def __init__(self, center, rok_potrditve_s: float = 30.0, ura=time.time):
        self.c, self.db, self.ura = center, center.db, ura
        self.rok_potrditve_s = rok_potrditve_s
        self.objava = ObjavaVoznikom()
        self.db.skripta(TABELE)
        center.po_potrditvi_plana.append(self._po_potrditvi)

    # ================= koraki =================
    def _opravljen(self, k: dict, enota: dict, naloge: dict) -> bool:
        n = naloge.get(k["naloga_id"])
        if not n or n.get("stanje") in ZAKLJUCENE_NALOGE:
            return True
        if k["akcija"] == "prevzem" and any(t["naloga_id"] == k["naloga_id"] for t in enota.get("tovor", [])):
            return True
        return False

    def preostali(self, enota_id: str, plan: dict | None = None) -> list[dict]:
        plan = plan if plan is not None else self.c.plan_aktiven()
        e = self.db.beri("enota", enota_id)
        if not plan or not e:
            return []
        naloge = {n["id"]: n for n in self.db.vsi("naloga")}
        return [k for k in plan["koraki_po_enoti"].get(enota_id, []) if not self._opravljen(k, e, naloge)]

    def trenutni(self, enota_id: str) -> dict | None:
        p = self.preostali(enota_id)
        return p[0] if p else None

    def faza(self, enota_id: str, korak: dict | None = None) -> str | None:
        korak = korak if korak is not None else self.trenutni(enota_id)
        if not korak:
            return None
        r = self.db.izvedi("SELECT * FROM voznik_faza WHERE enota_id=?", (enota_id,))
        if r and (r[0]["naloga_id"], r[0]["akcija"]) == _kljuc(korak):
            return r[0]["faza"]
        return None  # faza je veljala za drug (prejšnji) korak

    def _nastavi_fazo(self, enota_id: str, korak: dict, faza: str) -> None:
        self.db.izvedi("INSERT INTO voznik_faza VALUES (?,?,?,?,?) ON CONFLICT(enota_id) DO UPDATE SET "
                       "naloga_id=excluded.naloga_id, akcija=excluded.akcija, faza=excluded.faza, cas=excluded.cas",
                       (enota_id, korak["naloga_id"], korak["akcija"], faza, self.ura()))

    def dovoljena(self, enota_id: str) -> list[str]:
        """Stanja, ki jih voznik lahko sporoči zdaj (za gumbe v aplikaciji)."""
        e = self.db.beri("enota", enota_id)
        if not e or e["stanje"] == "okvara":
            return []
        k = self.trenutni(enota_id)
        if not k:
            return ["prosto"] if e["stanje"] in ("ni_na_voljo", "na_nalogi") else ["ni_na_voljo"]
        f = self.faza(enota_id, k)
        if f is None:
            return ["prejeto"]
        if f == "prejeto":
            return ["na_poti"]
        if f == "na_poti":
            return ["na_prizoriscu"]
        n = self.db.beri("naloga", k["naloga_id"]) or {}
        if k["akcija"] == "dostava":
            return ["dostavljeno"]
        if k.get("nujna") or not n.get("dostava"):
            return ["opravljeno"]  # prevoz brez cilja določi dispečer (M3: predlog)
        return ["pacient_nalozen"]

    # ================= sprememba stanja =================
    def spremeni(self, enota_id: str, stanje: str, naloga_id: str | None = None, akcija: str | None = None) -> dict:
        if stanje not in VSA_STANJA:
            raise NapakaStanja("neznano stanje", 422)
        e = self.db.beri("enota", enota_id)
        k = self.trenutni(enota_id)
        if stanje in IZMENA:
            return self._izmena(e, stanje, k)
        if not k:
            raise NapakaStanja("enota nima aktivnega koraka")
        if (naloga_id, akcija) != (None, None) and (naloga_id, akcija) != _kljuc(k):
            raise NapakaStanja("korak ni več aktualen – osvežite nalogo")
        trenutna = self.faza(enota_id, k)
        if trenutna == stanje:
            return self.za_voznika(enota_id)  # ponovljen zahtevek (idempotentno)
        if stanje not in self.dovoljena(enota_id):
            raise NapakaStanja(f"prehod {trenutna or 'dodeljeno'} → {stanje} ni dovoljen")
        uporabnik = f"voznik {enota_id}"
        if stanje == "prejeto":
            self._potrdi_odprta(enota_id)
        if stanje in FAZE:
            self._nastavi_fazo(enota_id, k, stanje)
        else:
            self._zakljuci_korak(e, k, stanje)
        self.db.revizija(uporabnik, "Naloga", k["naloga_id"], "stanje_voznika",
                         {"faza": trenutna or "dodeljeno"}, {"faza": stanje, "akcija": k["akcija"], "enota_id": enota_id})
        self._objavi_spremembo(enota_id, k, stanje)
        if stanje == "na_poti" and not self.pot(enota_id):
            self._izracunaj_pot_v_ozadju(enota_id)
        return self.za_voznika(enota_id)

    def _izmena(self, e: dict, stanje: str, k: dict | None) -> dict:
        if e["stanje"] == "okvara":
            raise NapakaStanja("enota je v okvari")
        if stanje == "ni_na_voljo":
            if k:
                raise NapakaStanja("najprej zaključite korake ali obvestite dispečerja")
            nova = "ni_na_voljo"
        else:
            if k:
                raise NapakaStanja("enota ima aktivne korake")
            nova = "prosto"
        if e["stanje"] != nova:
            stara = e["stanje"]
            e["stanje"] = nova
            self.db.shrani("enota", e)
            self.db.revizija(f"voznik {e['id']}", "Enota", e["id"], "izmena_voznika", stara, nova)
            self.c.objava.objavi("voznik_stanje", {"enota_id": e["id"], "stanje": nova, "cas": self.ura()})
            self.c.objava.objavi("enote", self.c.enote())
        return self.za_voznika(e["id"])

    def _zakljuci_korak(self, e: dict, k: dict, stanje: str) -> None:
        n = self.db.beri("naloga", k["naloga_id"])
        if stanje == "pacient_nalozen":
            kol = n.get("kolicina", {})
            e.setdefault("tovor", []).append({"naloga_id": n["id"], "pacienti": kol.get("pacienti", 0), "oprema": kol.get("oprema", 0)})
        elif stanje == "dostavljeno":
            e["tovor"] = [t for t in e.get("tovor", []) if t["naloga_id"] != n["id"]]
            n.update(stanje="opravljena", opravljena_ob=self.ura())
            cid = (n.get("dostava") or {}).get("center_id")
            if cid and n.get("kolicina", {}).get("pacienti"):
                c = self.db.beri("center", cid)
                if c:
                    c["zasedena"] = c.get("zasedena", 0) + n["kolicina"]["pacienti"]
                    self.db.shrani("center", c)
        elif stanje == "opravljeno":
            n.update(stanje="opravljena", opravljena_ob=self.ura())
        self.db.shrani("naloga", n)
        self.db.izvedi("DELETE FROM voznik_faza WHERE enota_id=?", (e["id"],))
        self.db.izvedi("DELETE FROM voznik_pot WHERE enota_id=?", (e["id"],))
        if not self.preostali(e["id"]) and e["stanje"] == "na_nalogi":
            e["stanje"] = "prosto"
        self.db.shrani("enota", {k2: v for k2, v in e.items() if k2 not in ("polozaj", "voznik")})
        self.c.objava.objavi("naloge", self.db.vsi("naloga"))
        self.c.objava.objavi("centri", self.db.vsi("center"))
        naslednji = self.trenutni(e["id"])
        if naslednji:
            if not self.obvestila(e["id"]):
                # plan je že potrjen s strani voznika -> naslednji korak je prejet (manj tipkanja med vožnjo)
                self._nastavi_fazo(e["id"], naslednji, "prejeto")
            self._izracunaj_pot_v_ozadju(e["id"])

    def _objavi_spremembo(self, enota_id: str, k: dict, stanje: str) -> None:
        s = {"enota_id": enota_id, "naloga_id": k["naloga_id"], "akcija": k["akcija"], "stanje": stanje, "cas": self.ura()}
        self.c.objava.objavi("voznik_stanje", s)
        self.c.objava.objavi("enote", self.c.enote())
        self.objava.objavi(enota_id, "naloga", {"razlog": "stanje"})

    # ================= potrditve prejema =================
    def _po_potrditvi(self, star: dict | None, nov: dict) -> None:
        """Ob potrditvi plana: enotam, katerim se je spremenil seznam preostalih korakov, pošlji obvestilo z razliko."""
        enote = set(nov["koraki_po_enoti"]) | set((star or {}).get("koraki_po_enoti", {}))
        for eid in sorted(enote):
            prej = [_kljuc(k) for k in self.preostali(eid, star)] if star else []
            potem = [_kljuc(k) for k in self.preostali(eid, nov)]
            if prej == potem:
                continue
            razlike = {"dodani": [list(x) for x in potem if x not in prej],
                       "odstranjeni": [list(x) for x in prej if x not in potem],
                       "vrstni_red": [x for x in prej if x in potem] != [x for x in potem if x in prej],
                       "prej": [list(x) for x in prej], "potem": [list(x) for x in potem]}
            self.db.izvedi("UPDATE voznik_obvestilo SET stanje='zamenjano' WHERE enota_id=? AND stanje='caka'", (eid,))
            zdaj = self.ura()
            o = {"id": "o-" + secrets.token_hex(6), "enota_id": eid, "plan_id": nov["id"], "verzija": nov.get("verzija"),
                 "ustvarjeno": zdaj, "rok": zdaj + self.rok_potrditve_s, "stanje": "caka" if potem else "obvescen",
                 "potrjeno": None, "razlike": razlike}
            self.db.izvedi("INSERT INTO voznik_obvestilo VALUES (?,?,?,?,?,?,?,?,?)",
                           (o["id"], eid, o["plan_id"], o["verzija"], zdaj, o["rok"], o["stanje"], None, json.dumps(razlike)))
            if not prej or not potem or prej[0] != potem[0]:
                self.db.izvedi("DELETE FROM voznik_pot WHERE enota_id=?", (eid,))
                if potem:
                    self._izracunaj_pot_v_ozadju(eid)
            self.objava.objavi(eid, "plan", {"obvestilo": o})
            self.c.objava.objavi("voznik_obvestilo", {k: o[k] for k in ("id", "enota_id", "plan_id", "stanje", "rok", "razlike")})

    def obvestila(self, enota_id: str, samo_odprta: bool = True) -> list[dict]:
        sql = "SELECT * FROM voznik_obvestilo WHERE enota_id=?" + (" AND stanje='caka'" if samo_odprta else "") + " ORDER BY ustvarjeno"
        return [dict(r, razlike=json.loads(r["razlike"])) for r in self.db.izvedi(sql, (enota_id,))]

    def zadnje_obvestilo(self, enota_id: str) -> dict | None:
        r = self.db.izvedi("SELECT * FROM voznik_obvestilo WHERE enota_id=? ORDER BY ustvarjeno DESC LIMIT 1", (enota_id,))
        return dict(r[0], razlike=json.loads(r[0]["razlike"])) if r else None

    def _potrdi_odprta(self, enota_id: str) -> list[str]:
        odprta = self.obvestila(enota_id)
        zdaj = self.ura()
        for o in odprta:
            self.db.izvedi("UPDATE voznik_obvestilo SET stanje='potrjeno', potrjeno=? WHERE id=?", (zdaj, o["id"]))
            self.db.revizija(f"voznik {enota_id}", "Plan", o["plan_id"], "potrditev_prejema", None,
                             {"obvestilo": o["id"], "cez_s": round(zdaj - o["ustvarjeno"], 1)})
            self.c.objava.objavi("voznik_potrditev", {"enota_id": enota_id, "obvestilo_id": o["id"], "plan_id": o["plan_id"],
                                                     "potrjeno": zdaj, "pravocasno": zdaj <= o["rok"]})
        return [o["id"] for o in odprta]

    def potrdi(self, enota_id: str, obvestilo_id: str) -> dict:
        r = self.db.izvedi("SELECT stanje FROM voznik_obvestilo WHERE id=? AND enota_id=?", (obvestilo_id, enota_id))
        if not r:
            raise NapakaStanja("obvestilo ne obstaja", 404)
        if r[0]["stanje"] == "caka":
            self._potrdi_odprta(enota_id)
            k = self.trenutni(enota_id)
            if k and self.faza(enota_id, k) is None:
                self._nastavi_fazo(enota_id, k, "prejeto")
                self._objavi_spremembo(enota_id, k, "prejeto")
        return self.za_voznika(enota_id)

    # ================= pot =================
    def pot(self, enota_id: str) -> dict | None:
        r = self.db.izvedi("SELECT podatki FROM voznik_pot WHERE enota_id=?", (enota_id,))
        if not r:
            return None
        p = json.loads(r[0]["podatki"])
        k = self.trenutni(enota_id)
        return p if k and (p["naloga_id"], p["akcija"]) == _kljuc(k) else None

    def _izracunaj_pot_v_ozadju(self, enota_id: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # brez zanke (npr. sinhroni test) -> izračuna se ob prvem branju naloge
        self.c._v_ozadju(self.izracunaj_pot(enota_id))

    async def izracunaj_pot(self, enota_id: str) -> dict | None:
        """Pot za trenutni korak z zadnjega znanega položaja. Velja kot del potrjenega plana (ODLOCITVE #37)."""
        k = self.trenutni(enota_id)
        e = next((x for x in self.c.enote() if x["id"] == enota_id), None)
        if not k or not e:
            return None
        r = await self.c.usmerjanje.izracunaj((e["lat"], e["lon"]), (k["lat"], k["lon"]), e["tip"])
        plan = self.c.plan_aktiven()
        p = {"plan_id": plan["id"] if plan else None, "naloga_id": k["naloga_id"], "akcija": k["akcija"],
             "od": {"lat": e["lat"], "lon": e["lon"]}, "do": {"lat": k["lat"], "lon": k["lon"]},
             "cas_s": r["cas_s"], "razdalja_m": r["razdalja_m"], "geometrija": r["geometrija"], "kakovost": r["kakovost"],
             "cestna": e["tip"] != "helikopter", "izracunano": self.ura(), "navodila": []}
        if self.trenutni(enota_id) != k:
            return None  # med računanjem se je korak spremenil
        self.db.izvedi("INSERT INTO voznik_pot VALUES (?,?) ON CONFLICT(enota_id) DO UPDATE SET podatki=excluded.podatki",
                       (enota_id, json.dumps(p)))
        self.objava.objavi(enota_id, "pot", {"naloga_id": k["naloga_id"], "akcija": k["akcija"]})
        return p

    # ================= pogledi =================
    def za_voznika(self, enota_id: str) -> dict:
        """Vse, kar potrebuje naprava (GET /voznik/naloga). Brez imen ponudnikov."""
        e = next((x for x in self.c.enote() if x["id"] == enota_id), None)
        plan = self.c.plan_aktiven()
        naloge = {n["id"]: n for n in self.db.vsi("naloga")}
        vsi = plan["koraki_po_enoti"].get(enota_id, []) if plan else []
        k = self.trenutni(enota_id)
        koraki = []
        for x in vsi:
            n = naloge.get(x["naloga_id"]) or {}
            koraki.append({**{f: x.get(f) for f in ("naloga_id", "akcija", "nujna", "eta", "zamuda_s", "prioriteta", "lat", "lon")},
                           "opravljen": self._opravljen(x, e, naloge) if e else True,
                           "trenutni": bool(k) and _kljuc(x) == _kljuc(k),
                           "naloga": _naloga_za_voznika(n)})
        return {"enota": {"id": e["id"], "ime": e["ime"], "tip": e["tip"], "stanje": e["stanje"],
                          "lat": e["lat"], "lon": e["lon"],  # zadnji znani položaj lastne enote
                          "tovor": [t["naloga_id"] for t in e.get("tovor", [])]} if e else None,
                "plan": {"id": plan["id"], "verzija": plan.get("verzija"), "potrjen_ob": plan.get("potrjen_ob")} if plan else None,
                "koraki": koraki, "faza": self.faza(enota_id, k) if k else None,
                "dovoljena_stanja": self.dovoljena(enota_id), "pot": self.pot(enota_id),
                "obvestila": self.obvestila(enota_id), "zagon": self.objava.zagon, "seq": self.objava.seq}

    def opis_za_dispecerja(self, enota_id: str) -> dict:
        k = self.trenutni(enota_id)
        o = self.zadnje_obvestilo(enota_id)
        return {"prijavljen": enota_id in self.c.avtentikacija.prijavljena(),
                "korak": {"naloga_id": k["naloga_id"], "akcija": k["akcija"]} if k else None,
                "faza": self.faza(enota_id, k) if k else None,
                "obvestilo": {"id": o["id"], "stanje": o["stanje"], "rok": o["rok"], "potrjeno": o["potrjeno"]} if o else None}


def _naloga_za_voznika(n: dict) -> dict:
    """Potrjeni podatki naloge (iz potrjenih polj klica, nikoli nepotrjeni predlogi AI)."""
    if not n:
        return {}
    potrjeno = n.get("podatki_klica") or {}
    return {"id": n.get("id"), "tip": n.get("tip"), "opis": n.get("opis"), "prioriteta": n.get("prioriteta"),
            "stanje": n.get("stanje"), "prevzem": n.get("prevzem"), "dostava": n.get("dostava"),
            "okno_do": n.get("okno_do"), "kolicina": n.get("kolicina"),
            "potrjeno": {k: potrjeno[k] for k in ("vrsta_dogodka", "opis", "naslov", "orientacija", "stevilo_poskodovanih",
                                                  "stanje_pacienta", "potrebna_oprema", "kontakt") if k in potrjeno}}
