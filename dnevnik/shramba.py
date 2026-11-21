"""Shramba (SQLite z WAL) in revizijska sled (specifikacija 2.6, 7).

- objekti: tabela (tip, id, podatki JSON)
- revizija: vsako dejanje z uporabnikom, časom, staro in novo vrednostjo
- izvozi_json: periodičen izvoz kot rezerva
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


class Shramba:
    def __init__(self, pot: str | os.PathLike = ":memory:"):
        self.pot = str(pot)
        if self.pot != ":memory:":
            Path(self.pot).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.pot, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if self.pot != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS objekti (
                tip TEXT NOT NULL, id TEXT NOT NULL, podatki TEXT NOT NULL,
                posodobljeno REAL NOT NULL, PRIMARY KEY (tip, id));
            CREATE TABLE IF NOT EXISTS revizija (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cas REAL NOT NULL, uporabnik TEXT NOT NULL,
                objekt TEXT NOT NULL, objekt_id TEXT NOT NULL, akcija TEXT NOT NULL,
                stara_vrednost TEXT, nova_vrednost TEXT);
            CREATE INDEX IF NOT EXISTS revizija_objekt ON revizija(objekt_id);
            """
        )

    # ---------- objekti ----------
    def shrani(self, tip: str, obj: dict) -> dict:
        with self._lock:
            self._db.execute(
                "INSERT INTO objekti(tip, id, podatki, posodobljeno) VALUES (?,?,?,?) "
                "ON CONFLICT(tip, id) DO UPDATE SET podatki=excluded.podatki, posodobljeno=excluded.posodobljeno",
                (tip, obj["id"], json.dumps(obj, ensure_ascii=False), time.time()),
            )
        return obj

    def beri(self, tip: str, obj_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT podatki FROM objekti WHERE tip=? AND id=?", (tip, obj_id)).fetchone()
        return json.loads(r["podatki"]) if r else None

    def vsi(self, tip: str, **filter_) -> list[dict]:
        with self._lock:
            vrstice = self._db.execute("SELECT podatki FROM objekti WHERE tip=? ORDER BY rowid", (tip,)).fetchall()
        out = [json.loads(r["podatki"]) for r in vrstice]
        for k, v in filter_.items():
            out = [o for o in out if o.get(k) == v]
        return out

    def izbrisi(self, tip: str, obj_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM objekti WHERE tip=? AND id=?", (tip, obj_id))

    # ---------- lastne tabele modulov (npr. voznik/) ----------
    def skripta(self, sql: str) -> None:
        with self._lock:
            self._db.executescript(sql)

    def izvedi(self, sql: str, parametri: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, parametri).fetchall()]

    def izvedi_vec(self, sql: str, vrstice: list) -> list[bool]:
        """Vsako vrstico posebej v eni transakciji; vrne, katere so kaj spremenile
        (npr. INSERT OR IGNORE -> False za podvojene)."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                izid = [self._db.execute(sql, v).rowcount > 0 for v in vrstice]
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return izid

    # ---------- revizija ----------
    def revizija(self, uporabnik: str, objekt: str, objekt_id: str, akcija: str,
                 stara=None, nova=None) -> dict:
        vnos = {
            "cas": time.time(), "uporabnik": uporabnik, "objekt": objekt, "objekt_id": objekt_id,
            "akcija": akcija, "stara_vrednost": stara, "nova_vrednost": nova,
        }
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO revizija(cas, uporabnik, objekt, objekt_id, akcija, stara_vrednost, nova_vrednost) "
                "VALUES (?,?,?,?,?,?,?)",
                (vnos["cas"], uporabnik, objekt, objekt_id, akcija,
                 json.dumps(stara, ensure_ascii=False), json.dumps(nova, ensure_ascii=False)),
            )
            vnos["id"] = cur.lastrowid
        return vnos

    def revizije(self, objekt_id: str | None = None, omejitev: int = 1000) -> list[dict]:
        with self._lock:
            if objekt_id:
                vr = self._db.execute("SELECT * FROM revizija WHERE objekt_id=? ORDER BY id", (objekt_id,)).fetchall()
            else:
                vr = self._db.execute("SELECT * FROM revizija ORDER BY id DESC LIMIT ?", (omejitev,)).fetchall()
        return [
            {**dict(r), "stara_vrednost": json.loads(r["stara_vrednost"]), "nova_vrednost": json.loads(r["nova_vrednost"])}
            for r in vr
        ]

    # ---------- rezerva ----------
    def izvozi_json(self, pot: str | os.PathLike) -> Path:
        pot = Path(pot)
        pot.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            vrstice = self._db.execute("SELECT tip, podatki FROM objekti").fetchall()
        podatki: dict[str, list] = {}
        for r in vrstice:
            podatki.setdefault(r["tip"], []).append(json.loads(r["podatki"]))
        zacasna = pot.with_suffix(".tmp")
        zacasna.write_text(json.dumps({"cas": time.time(), "objekti": podatki}, ensure_ascii=False), encoding="utf-8")
        zacasna.replace(pot)
        return pot
