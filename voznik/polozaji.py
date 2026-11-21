"""Položaji enot z naprav voznikov.

- `polozaj`: zgodovina fiksov, ključ (enota, naprava, seq) -> ponovljen paket ne podvoji ničesar.
- `polozaj_enote`: trenutni položaj enote. Posodobi se SAMO z novejšim fiksom (po času fiksa),
  zato ponovno poslana čakalna vrsta enote nikoli ne premakne nazaj.
- Trenutni položaj je ločen od JSON enote: planer ob računanju shrani stari posnetek stanja
  (`_shrani_stanje`) in bi sicer povozil novejši položaj.
- Signal: starost trenutnega položaja proti pragovom (na nalogi strožje). Brez položaja = »brez_gps«.
Hramba zgodovine: `AID_POLOZAJ_HRANJENJE_DNI` (privzeto 30), čiščenje vsako uro.
"""
from __future__ import annotations

import time

from dnevnik.shramba import Shramba
from usmerjanje.ponudniki import zracna_razdalja_m

from .nastavitve import NastavitveVoznika

TABELE = """
CREATE TABLE IF NOT EXISTS polozaj (
    enota_id TEXT NOT NULL, naprava_id TEXT NOT NULL, seq INTEGER NOT NULL,
    ts REAL NOT NULL, ts_naprava REAL NOT NULL, prejeto REAL NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, hitrost REAL, smer REAL, natancnost REAL,
    lazno INTEGER NOT NULL DEFAULT 0, oznaka TEXT,
    PRIMARY KEY (enota_id, naprava_id, seq));
CREATE INDEX IF NOT EXISTS polozaj_cas ON polozaj(enota_id, ts);
CREATE TABLE IF NOT EXISTS polozaj_enote (
    enota_id TEXT PRIMARY KEY, naprava_id TEXT NOT NULL, seq INTEGER NOT NULL,
    ts REAL NOT NULL, prejeto REAL NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    hitrost REAL, smer REAL, natancnost REAL, interval_s REAL);
"""

SIGNALI = ("v_zivo", "zakasnjeno", "ni_signala", "brez_gps")


class Polozaji:
    def __init__(self, db: Shramba, nast: NastavitveVoznika | None = None, ura=time.time):
        self.db, self.nast, self.ura = db, nast or NastavitveVoznika(), ura
        db.skripta(TABELE)
        self._signal: dict[str, str] = {}

    # ---------- sprejem ----------
    def sprejmi(self, enota: dict, naprava_id: str, fiksi: list[dict], poslano_ob: float | None = None,
                interval_s: float | None = None) -> dict:
        """fiksi: [{seq, ts, lat, lon, hitrost?, smer?, natancnost?, lazno?}] (oblika je že preverjena).

        Vrne {potrjeno_do_seq, novi, podvojeni, zavrnjeni:[{seq, razlog}], premik: dict|None}.
        Vsi fiksi iz paketa so »obdelani« (tudi zavrnjeni) -> naprava jih lahko zbriše.
        """
        zdaj = self.ura()
        eid = enota["id"]
        # popravek ure naprave: velik odmik med »poslano_ob« in prejemom
        odmik = (zdaj - poslano_ob) if poslano_ob else 0.0
        popravek = odmik if abs(odmik) > self.nast.odmik_ure_s else 0.0
        meja_preteklost = zdaj - self.nast.hranjenje_dni * 86400
        vrstice, zavrnjeni = [], []
        for f in sorted(fiksi, key=lambda f: f["seq"]):
            ts = f["ts"] + popravek
            if ts > zdaj + self.nast.prihodnost_s:
                zavrnjeni.append({"seq": f["seq"], "razlog": "čas v prihodnosti"})
                continue
            if ts < meja_preteklost:
                zavrnjeni.append({"seq": f["seq"], "razlog": "prestar"})
                continue
            oznaka = None
            if f.get("lazno") and not self.nast.dovoli_lazno:
                oznaka = "lazno"
            elif (f.get("natancnost") or 0) > self.nast.max_natancnost_m:
                oznaka = "nenatancno"
            vrstice.append((eid, naprava_id, int(f["seq"]), ts, f["ts"], zdaj, f["lat"], f["lon"], f.get("hitrost"),
                            f.get("smer"), f.get("natancnost"), int(bool(f.get("lazno"))), oznaka))
        vstavljeno = self.db.izvedi_vec("INSERT OR IGNORE INTO polozaj VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", vrstice) if vrstice else []
        novi = sum(vstavljeno)
        # trenutni položaj samo iz NOVIH (ne podvojenih) in neoznačenih fiksov
        kandidati = [v for v, nov in zip(vrstice, vstavljeno) if nov and v[12] is None]
        premik = self._posodobi_trenutni(enota, naprava_id, kandidati, zdaj, interval_s)
        return {"potrjeno_do_seq": max((int(f["seq"]) for f in fiksi), default=None),
                "novi": novi, "podvojeni": len(vrstice) - novi, "zavrnjeni": zavrnjeni, "premik": premik}

    def _posodobi_trenutni(self, enota: dict, naprava_id: str, vrstice: list, zdaj: float, interval_s) -> dict | None:
        eid = enota["id"]
        trenutni = self.trenutni(eid)
        max_ms = self.nast.max_hitrost_kmh.get(enota.get("tip"), 250.0) / 3.6
        izbran = None
        ref = trenutni
        for v in sorted(vrstice, key=lambda v: v[3]):  # po času fiksa
            ts, lat, lon = v[3], v[6], v[7]
            if ref and ts <= ref["ts"]:
                continue  # starejši od trenutnega -> samo zgodovina
            if ref and ts - ref["ts"] < 60:
                d = zracna_razdalja_m((ref["lat"], ref["lon"]), (lat, lon))
                if d / max(ts - ref["ts"], 1.0) > max_ms:
                    self.db.izvedi("UPDATE polozaj SET oznaka='skok' WHERE enota_id=? AND naprava_id=? AND seq=?", (eid, v[1], v[2]))
                    continue  # nemogoč skok; po 60 s se sprejme (enota se ne zatakne)
            izbran = ref = {"enota_id": eid, "naprava_id": v[1], "seq": v[2], "ts": ts, "prejeto": zdaj,
                            "lat": lat, "lon": lon, "hitrost": v[8], "smer": v[9], "natancnost": v[10]}
        if izbran is None:
            if trenutni and interval_s is not None:
                self.db.izvedi("UPDATE polozaj_enote SET interval_s=? WHERE enota_id=?", (interval_s, eid))
            return None
        izbran["interval_s"] = interval_s
        self.db.izvedi("INSERT INTO polozaj_enote VALUES (:enota_id,:naprava_id,:seq,:ts,:prejeto,:lat,:lon,:hitrost,:smer,:natancnost,:interval_s) "
                       "ON CONFLICT(enota_id) DO UPDATE SET naprava_id=excluded.naprava_id, seq=excluded.seq, ts=excluded.ts, "
                       "prejeto=excluded.prejeto, lat=excluded.lat, lon=excluded.lon, hitrost=excluded.hitrost, smer=excluded.smer, "
                       "natancnost=excluded.natancnost, interval_s=excluded.interval_s", izbran)
        return izbran

    # ---------- branje ----------
    def trenutni(self, enota_id: str) -> dict | None:
        r = self.db.izvedi("SELECT * FROM polozaj_enote WHERE enota_id=?", (enota_id,))
        return r[0] if r else None

    def vsi_trenutni(self) -> dict[str, dict]:
        return {r["enota_id"]: r for r in self.db.izvedi("SELECT * FROM polozaj_enote")}

    def zgodovina(self, enota_id: str, od: float | None = None, do: float | None = None, omejitev: int = 5000) -> list[dict]:
        return self.db.izvedi("SELECT * FROM polozaj WHERE enota_id=? AND ts>=? AND ts<=? ORDER BY ts LIMIT ?",
                              (enota_id, od or 0, do or 1e12, omejitev))

    # ---------- signal ----------
    def signal(self, enota: dict, p: dict | None, zdaj: float | None = None) -> dict:
        zdaj = self.ura() if zdaj is None else zdaj
        aktivno = enota.get("stanje") == "na_nalogi"
        if not p:
            return {"signal": "brez_gps", "starost_s": None}
        zak, brez = self.nast.pragovi_signala(aktivno, p.get("interval_s"))
        starost = max(0.0, zdaj - p["ts"])
        s = "v_zivo" if starost < zak else "zakasnjeno" if starost < brez else "ni_signala"
        return {"signal": s, "starost_s": round(starost, 1), "prag_zakasnjeno_s": zak, "prag_brez_s": brez}

    def opis(self, enota: dict, p: dict | None, zdaj: float | None = None) -> dict | None:
        """Javni opis položaja za dispečerja (brez naprave)."""
        s = self.signal(enota, p, zdaj)
        if not p:
            return {"vir": "zadnji_znani", **s}
        return {"vir": "gps", "ts": p["ts"], "prejeto": p["prejeto"], "hitrost": p["hitrost"], "smer": p["smer"],
                "natancnost": p["natancnost"], **s}

    def prekrij(self, enote: list[dict], zdaj: float | None = None) -> list[dict]:
        """Enote s trenutnim GPS položajem in stanjem signala (lat/lon iz GPS, ko obstaja)."""
        trenutni = self.vsi_trenutni()
        out = []
        for e in enote:
            p = trenutni.get(e["id"])
            e = dict(e)
            if p:
                e["lat"], e["lon"] = p["lat"], p["lon"]
            e["polozaj"] = self.opis(e, p, zdaj)
            out.append(e)
        return out

    def spremembe_signala(self, enote: list[dict], zdaj: float | None = None) -> list[dict]:
        """Enote, katerih signal se je spremenil od zadnjega klica (za objavo `enota_signal`)."""
        trenutni = self.vsi_trenutni()
        out = []
        for e in enote:
            s = self.signal(e, trenutni.get(e["id"]), zdaj)
            if self._signal.get(e["id"]) != s["signal"]:
                prej = self._signal.get(e["id"])
                self._signal[e["id"]] = s["signal"]
                if prej is not None or s["signal"] != "brez_gps":
                    out.append({"enota_id": e["id"], "prej": prej, **s})
        return out

    # ---------- hramba ----------
    def pocisti(self) -> int:
        meja = self.ura() - self.nast.hranjenje_dni * 86400
        n = self.db.izvedi("SELECT COUNT(*) AS n FROM polozaj WHERE ts < ?", (meja,))[0]["n"]
        self.db.izvedi("DELETE FROM polozaj WHERE ts < ?", (meja,))
        return n
