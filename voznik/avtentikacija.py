"""Identiteta voznika: ena enota = ena naprava = en voznik.

- Koda enote: izda jo dispečer (skrbnik) ali je v demo/scenarij.json (`kode_voznikov`).
  Hranimo samo scrypt zgoščeno vrednost.
- Dostopni žeton (15 min) in osvežitveni žeton (12 h) sta podpisana s HMAC-SHA256.
  Seja je v SQLite: osvežitveni žeton se ob vsaki uporabi zamenja; ponovna uporaba starega
  pomeni krajo -> seja se prekliče. Nova prijava enote prekliče prejšnjo napravo.
- Enota pride VEDNO iz žetona, nikoli iz telesa zahteve.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from dnevnik.shramba import Shramba

from .nastavitve import NastavitveVoznika

TABELE = """
CREATE TABLE IF NOT EXISTS voznik_koda (
    enota_id TEXT PRIMARY KEY, sol BLOB NOT NULL, zgostitev BLOB NOT NULL,
    izdal TEXT NOT NULL, izdano REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voznik_seja (
    id TEXT PRIMARY KEY, enota_id TEXT NOT NULL, naprava_id TEXT NOT NULL,
    osvezitev TEXT NOT NULL, ustvarjeno REAL NOT NULL, potece REAL NOT NULL,
    preklicano REAL, razlog TEXT);
CREATE INDEX IF NOT EXISTS voznik_seja_enota ON voznik_seja(enota_id);
"""
ABECEDA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # brez dvoumnih znakov


class NapakaPrijave(Exception):
    def __init__(self, sporocilo: str, koda: int = 401):
        super().__init__(sporocilo)
        self.koda = koda


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _odb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _zgosti(koda: str, sol: bytes) -> bytes:
    return hashlib.scrypt(koda.strip().upper().encode(), salt=sol, n=2 ** 14, r=8, p=1, dklen=32)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Avtentikacija:
    def __init__(self, db: Shramba, nast: NastavitveVoznika, ura=time.time):
        self.db, self.nast, self.ura = db, nast, ura
        db.skripta(TABELE)
        self._napake: dict[str, list[float]] = {}

    # ---------- kode ----------
    def nastavi_kodo(self, enota_id: str, koda: str, izdal: str) -> None:
        sol = secrets.token_bytes(16)
        self.db.izvedi("INSERT INTO voznik_koda VALUES (?,?,?,?,?) ON CONFLICT(enota_id) DO UPDATE SET "
                       "sol=excluded.sol, zgostitev=excluded.zgostitev, izdal=excluded.izdal, izdano=excluded.izdano",
                       (enota_id, sol, _zgosti(koda, sol), izdal, self.ura()))

    def izdaj_kodo(self, enota_id: str, izdal: str) -> str:
        """Nova koda (prikaže se enkrat). Stare seje enote se prekličejo."""
        koda = "".join(secrets.choice(ABECEDA) for _ in range(4)) + "-" + "".join(secrets.choice(ABECEDA) for _ in range(4))
        self.nastavi_kodo(enota_id, koda, izdal)
        self.preklici_enoto(enota_id, "nova koda")
        self.db.revizija(izdal, "Enota", enota_id, "izdaj_kodo_voznika", None, {"izdano": True})
        return koda

    def nalozi_demo_kode(self, kode: dict[str, str]) -> None:
        obstojece = {r["enota_id"] for r in self.db.izvedi("SELECT enota_id FROM voznik_koda")}
        for eid, koda in kode.items():
            if eid not in obstojece:
                self.nastavi_kodo(eid, koda, "scenarij (demo)")

    def _preveri_kodo(self, enota_id: str, koda: str) -> bool:
        r = self.db.izvedi("SELECT sol, zgostitev FROM voznik_koda WHERE enota_id=?", (enota_id,))
        if not r:
            _zgosti(koda or "x", b"0" * 16)  # enak čas odziva za neznano enoto
            return False
        return hmac.compare_digest(_zgosti(koda or "", r[0]["sol"]), r[0]["zgostitev"])

    # ---------- žetoni ----------
    def _podpisi(self, vsebina: dict) -> str:
        telo = _b64(json.dumps(vsebina, separators=(",", ":"), sort_keys=True).encode())
        return telo + "." + _b64(hmac.new(self.nast.skrivnost, telo.encode(), hashlib.sha256).digest())

    def _preberi(self, zeton: str, vrsta: str) -> dict:
        try:
            telo, podpis = zeton.split(".")
            if not hmac.compare_digest(_odb64(podpis), hmac.new(self.nast.skrivnost, telo.encode(), hashlib.sha256).digest()):
                raise ValueError
            v = json.loads(_odb64(telo))
        except Exception as e:  # noqa: BLE001
            raise NapakaPrijave("neveljaven žeton") from e
        if v.get("vrsta") != vrsta:
            raise NapakaPrijave("napačna vrsta žetona")
        if v["potece"] <= self.ura():
            raise NapakaPrijave("žeton je potekel")
        return v

    def _izdaj(self, seja_id: str, enota_id: str, naprava_id: str) -> dict:
        zdaj = self.ura()
        osv = self._podpisi({"vrsta": "osvezitev", "seja": seja_id, "enota": enota_id, "naprava": naprava_id,
                             "potece": zdaj + self.nast.osvezitev_s, "n": secrets.token_hex(8)})
        dostop = self._podpisi({"vrsta": "dostop", "seja": seja_id, "enota": enota_id, "naprava": naprava_id,
                                "potece": zdaj + self.nast.dostop_s})
        self.db.izvedi("UPDATE voznik_seja SET osvezitev=? WHERE id=?", (_h(osv), seja_id))
        return {"dostop": dostop, "osvezitev": osv, "dostop_potece": zdaj + self.nast.dostop_s,
                "osvezitev_potece": zdaj + self.nast.osvezitev_s}

    # ---------- seje ----------
    def _zabelezi_napako(self, kljuc: str) -> None:
        zdaj = self.ura()
        self._napake[kljuc] = [t for t in self._napake.get(kljuc, []) if zdaj - t < self.nast.zaklep_okno_s] + [zdaj]

    def _zaklenjeno(self, kljuc: str) -> bool:
        zdaj = self.ura()
        return len([t for t in self._napake.get(kljuc, []) if zdaj - t < self.nast.zaklep_okno_s]) >= self.nast.zaklep_napake

    def prijava(self, enota_id: str, koda: str, naprava_id: str) -> tuple[dict, list[dict]]:
        """Vrne (žetoni, preklicane seje drugih naprav)."""
        if self._zaklenjeno(enota_id):
            raise NapakaPrijave("preveč napačnih poskusov – počakajte nekaj minut", 429)
        if not self._preveri_kodo(enota_id, koda):
            self._zabelezi_napako(enota_id)
            self.db.revizija(f"naprava {naprava_id[:40]}", "Enota", enota_id, "prijava_voznika_zavrnjena", None, None)
            raise NapakaPrijave("napačna enota ali koda")
        self._napake.pop(enota_id, None)
        preklicane = self.preklici_enoto(enota_id, "prijava z druge naprave", razen_naprave=None)
        seja_id = "s-" + secrets.token_hex(8)
        zdaj = self.ura()
        self.db.izvedi("INSERT INTO voznik_seja(id, enota_id, naprava_id, osvezitev, ustvarjeno, potece) VALUES (?,?,?,?,?,?)",
                       (seja_id, enota_id, naprava_id, "", zdaj, zdaj + self.nast.osvezitev_s))
        self.db.revizija(f"voznik {enota_id}", "Enota", enota_id, "prijava_voznika", None,
                         {"naprava_id": naprava_id, "seja": seja_id})
        return self._izdaj(seja_id, enota_id, naprava_id), preklicane

    def osvezi(self, osvezitev: str) -> dict:
        v = self._preberi(osvezitev, "osvezitev")
        r = self.db.izvedi("SELECT * FROM voznik_seja WHERE id=?", (v["seja"],))
        if not r or r[0]["preklicano"]:
            raise NapakaPrijave("seja ni več veljavna")
        if not hmac.compare_digest(r[0]["osvezitev"], _h(osvezitev)):
            # star osvežitveni žeton = morebitna kraja -> prekliči sejo
            self._preklici(v["seja"], "ponovna uporaba osvežitvenega žetona")
            self.db.revizija("sistem", "Enota", v["enota"], "seja_voznika_preklicana", None, {"razlog": "ponovna uporaba žetona"})
            raise NapakaPrijave("seja ni več veljavna")
        return self._izdaj(v["seja"], v["enota"], v["naprava"])

    def preveri_dostop(self, dostop: str) -> dict:
        """Vrne {enota, naprava, seja}. Preverja tudi, da seja ni preklicana (takojšnja odjava)."""
        v = self._preberi(dostop, "dostop")
        r = self.db.izvedi("SELECT preklicano FROM voznik_seja WHERE id=?", (v["seja"],))
        if not r or r[0]["preklicano"]:
            raise NapakaPrijave("seja ni več veljavna")
        return {"enota": v["enota"], "naprava": v["naprava"], "seja": v["seja"]}

    def _preklici(self, seja_id: str, razlog: str) -> None:
        self.db.izvedi("UPDATE voznik_seja SET preklicano=?, razlog=? WHERE id=? AND preklicano IS NULL",
                       (self.ura(), razlog, seja_id))

    def preklici_enoto(self, enota_id: str, razlog: str, razen_naprave: str | None = None) -> list[dict]:
        aktivne = self.db.izvedi("SELECT id, naprava_id FROM voznik_seja WHERE enota_id=? AND preklicano IS NULL", (enota_id,))
        aktivne = [a for a in aktivne if a["naprava_id"] != razen_naprave]
        for a in aktivne:
            self._preklici(a["id"], razlog)
        return aktivne

    def odjava(self, seja_id: str, enota_id: str) -> None:
        self._preklici(seja_id, "odjava")
        self.db.revizija(f"voznik {enota_id}", "Enota", enota_id, "odjava_voznika", None, {"seja": seja_id})

    def pocisti(self, starejse_od_s: float = 86400) -> int:
        """Potekle in preklicane seje starejše od dneva (zgodovina prijav ostane v reviziji)."""
        meja = self.ura() - starejse_od_s
        vr = self.db.izvedi("SELECT COUNT(*) AS n FROM voznik_seja WHERE potece < ? OR (preklicano IS NOT NULL AND preklicano < ?)",
                            (meja, meja))[0]["n"]
        self.db.izvedi("DELETE FROM voznik_seja WHERE potece < ? OR (preklicano IS NOT NULL AND preklicano < ?)", (meja, meja))
        return vr

    def z_napravo(self, okno_s: float = 12 * 3600) -> set[str]:
        """Enote, ki so v zadnji izmeni (privzeto 12 h) imele prijavljeno napravo."""
        meja = self.ura() - okno_s
        return {r["enota_id"] for r in self.db.izvedi("SELECT DISTINCT enota_id FROM voznik_seja WHERE ustvarjeno > ?", (meja,))}

    def prijavljena(self) -> set[str]:
        zdaj = self.ura()
        return {r["enota_id"] for r in self.db.izvedi(
            "SELECT enota_id FROM voznik_seja WHERE preklicano IS NULL AND potece > ?", (zdaj,))}
