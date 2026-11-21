"""Prijava dispečerjev in varovanje API-ja regijskega centra.

- računi: AID_DISPECERJI="Ime Priimek:geslo;Drugi:geslo2" in/ali demo računi iz scenarija
  (`dispecerji`; izklop AID_DISPECER_DEMO=0); gesla so v pomnilniku le kot scrypt zgostitve
- seja: podpisan piškotek (HMAC-SHA256, AID_DISPECER_SKRIVNOST) HttpOnly, SameSite=Strict, 12 h;
  ime dispečerja za revizijo pride IZ SEJE, nikoli iz glave odjemalca (pravilo 2.6)
- varovalni ASGI vmesnik: privzeto ZAVRNE vse, kar ni izrecno javno (statične datoteke, prijava,
  API voznika z lastnimi žetoni, /interno/* z žetonom skrbnika, /javnost/*)
- ploščice karte: dispečerjeva seja ALI veljaven žeton voznika
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path

dnevnik = logging.getLogger("dispecer")
PISKOTEK = "aid_dispecer"
TRAJANJE_S = 12 * 3600
KONCNICE_STATIKE = (".html", ".js", ".css", ".png", ".svg", ".ico", ".webmanifest", ".json", ".txt", ".map", ".woff", ".woff2")
JAVNE_POTI = ("/dispecer/prijava", "/dispecer/odjava", "/dispecer/jaz")
JAVNE_PREDPONE = ("/voznik/", "/javnost/")  # voznik: lastni žetoni (voznik/), javnost: brez osebnih podatkov


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class NapakaPrijaveDispecerja(Exception):
    def __init__(self, sporocilo: str, koda: int = 401):
        super().__init__(sporocilo)
        self.koda = koda


class Dispecerji:
    def __init__(self, racuni: dict[str, str] | None = None, skrivnost: str | None = None, ura=time.time):
        self.ura = ura
        sk = skrivnost or os.environ.get("AID_DISPECER_SKRIVNOST")
        if not sk:
            dnevnik.warning("AID_DISPECER_SKRIVNOST ni nastavljen – seje dispečerjev ne preživijo ponovnega zagona.")
            sk = secrets.token_hex(32)
        self._kljuc = hashlib.sha256(("aid-dispecer:" + sk).encode()).digest()
        self._racuni: dict[str, tuple[bytes, bytes]] = {}
        self._preklicane: set[str] = set()
        self._napake: dict[str, list[float]] = {}
        for ime, geslo in (racuni or {}).items():
            self.dodaj(ime, geslo)

    @classmethod
    def iz_okolja(cls, scenarij: Path | None = None) -> "Dispecerji":
        racuni: dict[str, str] = {}
        if os.environ.get("AID_DISPECER_DEMO", "1") == "1" and scenarij and scenarij.exists():
            try:
                for r in json.loads(scenarij.read_text(encoding="utf-8")).get("dispecerji", []):
                    racuni[r["ime"]] = r["geslo"]
            except (OSError, ValueError, KeyError):
                pass
        for par in filter(None, (os.environ.get("AID_DISPECERJI") or "").split(";")):
            if ":" in par:
                ime, geslo = par.split(":", 1)
                racuni[ime.strip()] = geslo.strip()
        if not racuni:
            dnevnik.warning("Ni računov dispečerjev – nastavite AID_DISPECERJI ali omogočite demo račune.")
        return cls(racuni)

    # ---------- računi ----------
    @staticmethod
    def _zgosti(geslo: str, sol: bytes) -> bytes:
        return hashlib.scrypt(geslo.encode(), salt=sol, n=2 ** 14, r=8, p=1, dklen=32)

    def dodaj(self, ime: str, geslo: str) -> None:
        ime = ime.strip()[:60]
        if not ime or not geslo:
            return
        sol = secrets.token_bytes(16)
        self._racuni[ime.casefold()] = (sol, self._zgosti(geslo, sol))
        self._racuni[ime.casefold() + "\x00ime"] = (ime.encode(), b"")

    def imena(self) -> list[str]:
        return [v[0].decode() for k, v in self._racuni.items() if k.endswith("\x00ime")]

    # ---------- prijava ----------
    def prijava(self, ime: str, geslo: str) -> tuple[str, str]:
        """Vrne (piškotek, pravo ime). 5 napačnih poskusov v 5 min -> zaklep za ta račun."""
        kljuc = (ime or "").strip().casefold()
        zdaj = self.ura()
        napake = [t for t in self._napake.get(kljuc, []) if t > zdaj - 300]
        self._napake[kljuc] = napake
        if len(napake) >= 5:
            raise NapakaPrijaveDispecerja("preveč napačnih poskusov – počakajte nekaj minut", 429)
        rac = self._racuni.get(kljuc)
        # enako delo tudi za neznano ime (brez razkrivanja, kateri računi obstajajo)
        sol, zg = rac if rac else (b"\x00" * 16, b"\x00" * 32)
        if not (hmac.compare_digest(self._zgosti(geslo or "", sol), zg) and rac):
            napake.append(zdaj)
            raise NapakaPrijaveDispecerja("napačno ime ali geslo")
        self._napake.pop(kljuc, None)
        pravo = self._racuni[kljuc + "\x00ime"][0].decode()
        return self._podpisi({"u": pravo, "sid": secrets.token_hex(8), "exp": zdaj + TRAJANJE_S}), pravo

    def _podpisi(self, podatki: dict) -> str:
        telo = _b64(json.dumps(podatki, separators=(",", ":"), ensure_ascii=False).encode())
        return telo + "." + _b64(hmac.new(self._kljuc, telo.encode(), hashlib.sha256).digest())

    def preveri(self, zeton: str | None) -> dict | None:
        if not zeton or "." not in zeton:
            return None
        telo, podpis = zeton.rsplit(".", 1)
        if not hmac.compare_digest(_b64(hmac.new(self._kljuc, telo.encode(), hashlib.sha256).digest()), podpis):
            return None
        try:
            p = json.loads(_unb64(telo))
        except ValueError:
            return None
        if p.get("exp", 0) < self.ura() or p.get("sid") in self._preklicane:
            return None
        return p

    def odjava(self, zeton: str | None) -> str | None:
        p = self.preveri(zeton)
        if p:
            self._preklicane.add(p["sid"])
            return p["u"]
        return None


def piskotek_iz_glav(glave: list[tuple[bytes, bytes]]) -> str | None:
    for k, v in glave:
        if k == b"cookie":
            c = SimpleCookie()
            try:
                c.load(v.decode("latin-1"))
            except Exception:  # noqa: BLE001
                return None
            if PISKOTEK in c:
                return c[PISKOTEK].value
    return None


def glava(glave: list[tuple[bytes, bytes]], ime: bytes) -> str | None:
    for k, v in glave:
        if k == ime:
            return v.decode("latin-1")
    return None


class Varovalo:
    """ASGI vmesnik: privzeto zavrni; dovoli javne poti, sejo dispečerja, žeton voznika (ploščice), skrbnika."""

    def __init__(self, app, dispecerji, preveri_voznika, je_skrbnik):
        self.app, self.preveri_voznika, self.je_skrbnik = app, preveri_voznika, je_skrbnik
        self._dispecerji = dispecerji

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        pot = scope.get("path", "")
        glave = scope.get("headers", [])
        seja = self._dispecerji().preveri(piskotek_iz_glav(glave))
        scope.setdefault("state", {})["dispecer"] = seja["u"] if seja else None
        if seja or self._javno(pot, scope["type"]):
            return await self.app(scope, receive, send)
        if pot.startswith("/ploscice/") and self._voznik(glave):
            return await self.app(scope, receive, send)
        if (pot.startswith("/interno/") or pot.startswith("/enote/")) and self.je_skrbnik(glava(glave, b"x-skrbnik-zeton")):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        telo = json.dumps({"napaka": "prijava dispečerja je potrebna"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(telo)).encode())]})
        await send({"type": "http.response.body", "body": telo})

    @staticmethod
    def _javno(pot: str, vrsta: str) -> bool:
        if vrsta == "websocket":
            return pot == "/ws/voznik"  # lastna prijava z žetonom v prvem sporočilu
        if pot == "/" or pot in JAVNE_POTI or pot.startswith(JAVNE_PREDPONE):
            return True
        return pot.lower().endswith(KONCNICE_STATIKE) and not pot.startswith(("/ploscice/", "/interno/"))

    def _voznik(self, glave) -> bool:
        a = glava(glave, b"authorization") or ""
        if not a.lower().startswith("bearer "):
            return False
        try:
            self.preveri_voznika(a[7:].strip())
            return True
        except Exception:  # noqa: BLE001
            return False
