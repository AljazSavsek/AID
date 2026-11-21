"""Nastavitve voznikove strani (vse iz okoljskih spremenljivk, vse neobvezne).

Strežnik pošlje frekvence GPS napravi v vsakem odgovoru na /voznik/polozaj,
zato se spreminjajo brez posodobitve aplikacije.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _f(ime: str, privzeto: float) -> float:
    try:
        return float(os.environ.get(ime, privzeto))
    except ValueError:
        return float(privzeto)


@dataclass
class NastavitveVoznika:
    # žetoni
    skrivnost: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    dostop_s: float = 900.0             # 15 min
    osvezitev_s: float = 12 * 3600.0    # dolžina izmene
    # GPS: na nalogi
    aktivno_fiks_s: float = 2.0
    aktivno_posiljanje_s: float = 5.0
    # GPS: prosto (in »ni na voljo« med izmeno)
    prosto_fiks_s: float = 30.0
    prosto_posiljanje_s: float = 60.0
    baterija_prosto_posiljanje_s: float = 120.0
    baterija_prag: int = 20             # %
    # signal (starost zadnjega položaja)
    aktivno_zakasnjeno_s: float = 30.0
    aktivno_brez_s: float = 60.0
    prosto_faktor: float = 3.0          # prosto: zakasnjeno = 1×, brez = faktor × pošiljanje
    # preverjanje fiksov
    max_paket: int = 200
    max_natancnost_m: float = 500.0     # slabši fiksi gredo samo v zgodovino
    max_hitrost_kmh: dict = field(default_factory=lambda: {"vozilo": 250.0, "motorist": 250.0, "helikopter": 450.0})
    odmik_ure_s: float = 5.0            # večji odmik ure naprave -> popravek
    prihodnost_s: float = 60.0
    dovoli_lazno: bool = False          # »mocked« položaji ne postanejo trenutni
    # potrditev prejema plana
    rok_potrditve_s: float = 30.0
    # hramba in omejitve
    hranjenje_dni: float = 30.0
    omejitev_polozaj_min: int = 60      # zahtevkov na enoto na minuto
    omejitev_prijava_min: int = 10      # poskusov prijave na IP na minuto
    zaklep_napake: int = 5              # napačnih kod na enoto ...
    zaklep_okno_s: float = 300.0        # ... v tem oknu -> zaklep

    @classmethod
    def iz_okolja(cls) -> "NastavitveVoznika":
        s = os.environ.get("AID_VOZNIK_SKRIVNOST")
        return cls(
            skrivnost=s.encode() if s else secrets.token_bytes(32),  # brez nastavitve: nova ob vsakem zagonu
            dostop_s=_f("AID_VOZNIK_DOSTOP_S", 900),
            osvezitev_s=_f("AID_VOZNIK_OSVEZITEV_S", 12 * 3600),
            aktivno_fiks_s=_f("AID_GPS_AKTIVNO_FIKS_S", 2),
            aktivno_posiljanje_s=_f("AID_GPS_AKTIVNO_POSILJANJE_S", 5),
            prosto_fiks_s=_f("AID_GPS_PROSTO_FIKS_S", 30),
            prosto_posiljanje_s=_f("AID_GPS_PROSTO_POSILJANJE_S", 60),
            baterija_prosto_posiljanje_s=_f("AID_GPS_BATERIJA_POSILJANJE_S", 120),
            aktivno_zakasnjeno_s=_f("AID_SIGNAL_ZAKASNJENO_S", 30),
            aktivno_brez_s=_f("AID_SIGNAL_BREZ_S", 60),
            hranjenje_dni=_f("AID_POLOZAJ_HRANJENJE_DNI", 30),
            rok_potrditve_s=_f("AID_VOZNIK_ROK_POTRDITVE_S", 30),
            dovoli_lazno=os.environ.get("AID_GPS_DOVOLI_LAZNO") == "1",
        )

    def frekvence(self, aktivno: bool, baterija: dict | None = None) -> dict:
        """Frekvence za napravo. Na nalogi se nikoli ne znižajo zaradi baterije."""
        if aktivno:
            return {"fiks_s": self.aktivno_fiks_s, "posiljanje_s": self.aktivno_posiljanje_s, "nacin": "aktivno"}
        nizka = bool(baterija) and not baterija.get("polni") and (baterija.get("nivo") or 100) < self.baterija_prag
        return {"fiks_s": self.prosto_fiks_s,
                "posiljanje_s": self.baterija_prosto_posiljanje_s if nizka else self.prosto_posiljanje_s,
                "nacin": "varcno" if nizka else "prosto"}

    def pragovi_signala(self, aktivno: bool, interval_s: float | None = None) -> tuple[float, float]:
        """(zakasnjeno, ni_signala) v sekundah starosti položaja. Prosto: glede na interval,
        ki ga je strežnik nazadnje sporočil napravi (varčni način pošilja redkeje)."""
        if aktivno:
            return self.aktivno_zakasnjeno_s, self.aktivno_brez_s
        interval = max(interval_s or self.prosto_posiljanje_s, self.aktivno_posiljanje_s)
        return (interval + self.prosto_fiks_s + self.aktivno_zakasnjeno_s,
                max(self.prosto_faktor * interval, interval + self.prosto_fiks_s + self.aktivno_brez_s))
