"""Trajna čakalna vrsta GPS fiksov (SQLite v zasebni mapi aplikacije).

- vsak fiks dobi zaporedno številko `seq` (trajni števec na napravo) in svoj izvirni čas;
- briše se šele, ko strežnik potrdi (`potrjeno_do_seq`) -> izpad povezave ali ponovni zagon
  aplikacije ne izgubi ničesar, ponovno pošiljanje pa ne podvoji (strežnik je idempotenten);
- omejitev velikosti: ob prepolni vrsti se zavržejo najstarejši fiksi (najmanj vredni za nadzor).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from pathlib import Path


class VrstaFiksov:
    def __init__(self, pot: str | Path = ":memory:", najvec: int = 5000):
        self.najvec = najvec
        self._lock = threading.Lock()
        if str(pot) != ":memory:":
            Path(pot).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(pot), check_same_thread=False, isolation_level=None)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (kljuc TEXT PRIMARY KEY, vrednost TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fiksi (seq INTEGER PRIMARY KEY, podatki TEXT NOT NULL);
        """)
        if self._meta("naprava_id") is None:
            self._nastavi_meta("naprava_id", "n-" + secrets.token_hex(8))
        if self._meta("seq") is None:
            self._nastavi_meta("seq", "0")
        self.zavrzeni = 0

    def _meta(self, k: str) -> str | None:
        r = self._db.execute("SELECT vrednost FROM meta WHERE kljuc=?", (k,)).fetchone()
        return r[0] if r else None

    def _nastavi_meta(self, k: str, v: str) -> None:
        self._db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(kljuc) DO UPDATE SET vrednost=excluded.vrednost", (k, v))

    @property
    def naprava_id(self) -> str:
        return self._meta("naprava_id")

    def dodaj(self, fiks: dict) -> int:
        """fiks: {ts, lat, lon, hitrost?, smer?, natancnost?, lazno?} -> dodeli seq."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            seq = int(self._meta("seq")) + 1
            self._nastavi_meta("seq", str(seq))
            self._db.execute("INSERT INTO fiksi VALUES (?,?)", (seq, json.dumps({**fiks, "seq": seq})))
            n = self._db.execute("SELECT COUNT(*) FROM fiksi").fetchone()[0]
            if n > self.najvec:
                odvec = n - self.najvec
                self._db.execute("DELETE FROM fiksi WHERE seq IN (SELECT seq FROM fiksi ORDER BY seq LIMIT ?)", (odvec,))
                self.zavrzeni += odvec
            self._db.execute("COMMIT")
            return seq

    def paket(self, n: int = 50) -> list[dict]:
        with self._lock:
            return [json.loads(r[0]) for r in self._db.execute("SELECT podatki FROM fiksi ORDER BY seq LIMIT ?", (n,))]

    def potrdi_do(self, seq: int | None) -> None:
        if seq is None:
            return
        with self._lock:
            self._db.execute("DELETE FROM fiksi WHERE seq <= ?", (int(seq),))

    def __len__(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM fiksi").fetchone()[0]

    def pocisti(self) -> None:
        """Ob odjavi: neposlani fiksi se zavržejo (po odjavi se položaj ne pošilja)."""
        with self._lock:
            self._db.execute("DELETE FROM fiksi")
