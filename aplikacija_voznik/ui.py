"""Zasloni aplikacije voznika – BREZ oblikovanja (privzete komponente, brez barv in pisav v logiki).
Velike površine gumbov (višina VISINA_GUMBA, polna širina) so funkcionalna zahteva: med vožnjo
mora biti dotik zanesljiv. Oblikovanje pride v ločenem koraku."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import flet as ft

from jedro import i18n
from jedro.i18n import t
from jedro.navigacija import stanje_navigacije
from jedro.odjemalec import NiPovezave, Odjavljen, Odjemalec, Seja, Zavrnjeno
from jedro.posiljalec import Posiljalec
from jedro.sinhronizacija import Sinhronizacija
from jedro.vrsta import VrstaFiksov
from naprava import Naprava

dnevnik = logging.getLogger("voznik.ui")
VISINA_GUMBA = 84
OPOZORILO_VSAKIH_S = 10
PRAG_VOZNJE_MS = 10 / 3.6  # nad 10 km/h samo hitra sporočila
HITRA = ("prejeto", "na_poti", "zamuda", "potrebujem_pomoc", "poklici_me", "policija")
OVIRE = ("zapora", "nesreca", "dela_na_cesti", "poplava", "zastoj", "drugo")


async def mapa_podatkov(page: ft.Page) -> Path:
    if os.environ.get("AID_VOZNIK_PODATKI"):
        return Path(os.environ["AID_VOZNIK_PODATKI"])
    with contextlib.suppress(Exception):
        if page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS):
            return Path(await ft.StoragePaths().get_application_support_directory())
    return Path.home() / ".aid_voznik"


def velik_gumb(besedilo: str, ob_kliku, kljuc: str | None = None) -> ft.Button:
    return ft.Button(content=besedilo, on_click=ob_kliku, height=VISINA_GUMBA, expand=True, key=kljuc)


class AplikacijaVoznika:
    def __init__(self, page: ft.Page):
        self.page = page
        i18n.nalozi(os.environ.get("AID_VOZNIK_JEZIK", "sl"))
        page.title = t("aplikacija.naslov")
        self.naprava = Naprava(page)
        self.naloga: dict | None = None
        self.povezava = "brez"
        self.gps = {"stanje": "v_vrsti", "v_vrsti": 0}
        self.gps_napaka: str | None = None
        self.zadnji_fiks: dict | None = None
        self.sporocilo = ft.Text("")
        self._delam = False
        self._zadnji_izris = 0.0
        self._potrjuje_odjavo = False
        self._potrjuje_okvaro = False
        self.razdelek: str | None = None  # None | "sporocila" | "tezava"
        self.besedilo_sporocila = ft.TextField(label=t("sporocilo.besedilo"), max_length=500, multiline=True)
        self._pot_cas: float | None = None
        self.posiljalec = self.sinh = None
        self.v_ospredju = True
        page.on_app_lifecycle_state_change = self._zivljenje

    # ================= zagon =================
    async def zacni(self) -> None:
        mapa = await mapa_podatkov(self.page)
        self.vrsta = VrstaFiksov(mapa / "vrsta.sqlite")
        self.seja = Seja(mapa / "seja.json")
        self.odjemalec = Odjemalec(self.seja, os.environ.get("AID_STREZNIK") if not self.seja.podatki.get("streznik") else None)
        if self.seja.prijavljen:
            await self._po_prijavi()
        else:
            self._prikazi_prijavo()

    # ================= prijava =================
    def _prikazi_prijavo(self, napaka: str | None = None) -> None:
        self.p_enota = ft.TextField(label=t("prijava.enota"), value=(self.seja.podatki.get("enota") or {}).get("id", ""),
                                    autofocus=True, capitalization=ft.TextCapitalization.CHARACTERS)
        self.p_koda = ft.TextField(label=t("prijava.koda"), password=True, can_reveal_password=True)
        self.p_streznik = ft.TextField(label=t("prijava.streznik"), value=self.odjemalec.streznik,
                                       keyboard_type=ft.KeyboardType.URL)
        self.p_napaka = ft.Text(napaka or "")
        self.page.controls.clear()
        self.page.add(ft.SafeArea(content=ft.Column([
            ft.Text(t("prijava.naslov"), size=None),
            self.p_enota, self.p_koda, self.p_streznik,
            ft.Row([velik_gumb(t("prijava.gumb"), self._prijava, "prijava")]),
            self.p_napaka,
        ], scroll=ft.ScrollMode.AUTO)))

    async def _prijava(self, _=None) -> None:
        self.seja.podatki["streznik"] = self.p_streznik.value.strip().rstrip("/")
        try:
            await self.odjemalec.prijava(self.p_enota.value, self.p_koda.value, self.vrsta.naprava_id)
        except Zavrnjeno as e:
            self.p_napaka.value = t("prijava.napaka", napaka=str(e))
            self.page.update()
            return
        except NiPovezave:
            self.p_napaka.value = t("prijava.ni_povezave")
            self.page.update()
            return
        self.p_koda.value = ""
        await self._po_prijavi()

    async def _po_prijavi(self) -> None:
        enota = self.seja.podatki.get("enota") or {}
        self._zgradi_glavni()
        dov = await self.naprava.pripravi()
        if dov != "ok":
            self.gps["stanje"] = "ni_dovoljenja" if dov == "ni_dovoljenja" else "izklopljen"
        self.posiljalec = Posiljalec(self.odjemalec, self.vrsta, self._ob_gps)
        self.sinh = Sinhronizacija(self.odjemalec, self._ob_nalogi, self._ob_povezavi, self._ob_odjavi)
        if dov == "ok":
            self.naprava.zacni_gps(self._ob_fiksu, self._ob_napaki_gps, self.posiljalec.gps.get("fiks_s", 30),
                                   enota.get("id", ""))
        self.page.run_task(self.posiljalec.tek)
        self.page.run_task(self.sinh.tek)
        self.page.run_task(self._zanka_opozoril)
        self.page.run_task(self._zanka_baterije)
        self._izrisi()

    def _ob_odjavi(self) -> None:
        self._ustavi()
        self._prikazi_prijavo(t("odjavljen.sporocilo"))
        self.page.update()

    def _ustavi(self) -> None:
        self.naprava.ustavi_gps()
        if self.posiljalec:
            self.posiljalec.ustavi()
        if self.sinh:
            self.sinh.ustavi()
        self.page.run_task(self.naprava.zaslon_prizgan, False)

    async def _odjava(self, _=None) -> None:
        if not self._potrjuje_odjavo:
            self._potrjuje_odjavo = True
            self.g_odjava.content = t("odjava.potrdi")
            self.page.update()
            return
        self._ustavi()
        with contextlib.suppress(Exception):
            await self.odjemalec.odjava()
        self.vrsta.pocisti()
        self._potrjuje_odjavo = False
        self._potrjuje_okvaro = False
        self.razdelek: str | None = None  # None | "sporocila" | "tezava"
        self.besedilo_sporocila = ft.TextField(label=t("sporocilo.besedilo"), max_length=500, multiline=True)
        self._pot_cas: float | None = None
        self._prikazi_prijavo()
        self.page.update()

    # ================= dogodki =================
    def _ob_fiksu(self, f: dict) -> None:
        self.zadnji_fiks = f
        self.gps_napaka = None
        if self.posiljalec:
            self.posiljalec.nov_fiks(f)
        if time.time() - self._zadnji_izris > 1.0:
            self._izrisi()

    def _ob_napaki_gps(self, napaka: str) -> None:
        self.gps_napaka = napaka
        self._izrisi()

    def _ob_gps(self, povzetek: dict) -> None:
        stari = self.gps.get("gps", {}).get("fiks_s")
        self.gps = povzetek
        if povzetek.get("gps", {}).get("fiks_s") != stari:
            self.naprava.nastavi_interval(povzetek["gps"]["fiks_s"])
        if povzetek["stanje"] == "odjavljen":
            self._ob_odjavi()
            return
        self._izrisi()

    def _ob_povezavi(self, p: str) -> None:
        self.povezava = p
        self._izrisi()

    async def _ob_nalogi(self, n: dict) -> None:
        stara = self.naloga or {}
        prej = {o["id"] for o in stara.get("obvestila", [])}
        prej_sporocila = {m["id"] for m in stara.get("sporocila", [])}
        prej_predlogi = {p["id"]: p["stanje"] for p in stara.get("predlogi", [])}
        if (n.get("pot") or {}).get("izracunano") != (stara.get("pot") or {}).get("izracunano") and n.get("pot") and stara:
            self._pot_cas = n["pot"]["izracunano"]
        self.naloga = n
        if stara:
            nova_sp = [m for m in n.get("sporocila", []) if m["smer"] == "od_dispecerja" and m["id"] not in prej_sporocila]
            odloceni = [p for p in n.get("predlogi", []) if prej_predlogi.get(p["id"]) == "predlagano" and p["stanje"] != "predlagano"]
            if nova_sp:
                await self.naprava.opozori(t("sporocilo.novo"), nova_sp[0]["besedilo"], not self.v_ospredju)
            for p in odloceni:
                await self.naprava.opozori(t("predlog.odlocitev", tip=t("predlog." + p["tip"]), stanje=t("predlog." + p["stanje"])),
                                           p.get("razlog") or "", not self.v_ospredju)
        if self.v_ospredju:
            for m in n.get("sporocila", []):
                if m["smer"] == "od_dispecerja" and not m.get("prebrano"):
                    self.page.run_task(self._oznaci_prebrano, m["id"])
        if self.naprava.simulacija:
            self.naprava.simulirana_pot((n.get("pot") or {}).get("geometrija"), n.get("enota"))
        nova = [o for o in n.get("obvestila", []) if o["id"] not in prej]
        if nova:
            await self.naprava.opozori(t("obvestilo.sistemsko_naslov"), t("obvestilo.sistemsko_besedilo"), not self.v_ospredju)
        await self.naprava.zaslon_prizgan(bool(self._trenutni()) and self.v_ospredju)
        self._izrisi()

    async def _oznaci_prebrano(self, sid: str) -> None:
        with contextlib.suppress(Exception):
            await self.odjemalec.prebrano(sid)

    def _razdelek(self, ime: str | None):
        async def preklopi(_=None):
            self.razdelek = None if self.razdelek == ime else ime
            self._potrjuje_okvaro = False
            self._izrisi()
        return preklopi

    async def _hitro(self, e) -> None:
        koda = e.control.data
        await self._izvedi_sporocilo(lambda: self.odjemalec.sporocilo(koda=koda))

    async def _besedilo(self, _=None) -> None:
        b = (self.besedilo_sporocila.value or "").strip()
        if not b or self._vozi():
            return
        await self._izvedi_sporocilo(lambda: self.odjemalec.sporocilo(besedilo=b))
        self.besedilo_sporocila.value = ""

    async def _izvedi_sporocilo(self, klic) -> None:
        try:
            s = await klic()
            self.sporocilo.value = t("sporocilo.poslano", besedilo=s["besedilo"])
        except Zavrnjeno as z:
            self.sporocilo.value = t("stanje.napaka", napaka=str(z))
        except NiPovezave:
            self.sporocilo.value = t("stanje.ni_povezave")
        except Odjavljen:
            self._ob_odjavi()
            return
        if self.sinh:
            await self.sinh.osvezi_nalogo()
        self._izrisi()

    async def _okvara(self, _=None) -> None:
        if not self._potrjuje_okvaro:  # dva dotika: brez nenamerne prijave
            self._potrjuje_okvaro = True
            self._izrisi()
            return
        self._potrjuje_okvaro = False
        await self._izvedi_predlog(lambda: self.odjemalec.okvara())

    async def _ovira(self, e) -> None:
        await self._izvedi_predlog(lambda: self.odjemalec.ovira(e.control.data))

    async def _izvedi_predlog(self, klic) -> None:
        if self.posiljalec:
            self.posiljalec.zbudi()  # lokacija predloga je zadnji položaj na strežniku
            with contextlib.suppress(Exception):
                await self.posiljalec.poslji_vse(2)
        try:
            await klic()
            self.sporocilo.value = t("tezava.poslano")
            self.razdelek = None
        except Zavrnjeno as z:
            self.sporocilo.value = t("stanje.napaka", napaka=str(z))
        except NiPovezave:
            self.sporocilo.value = t("stanje.ni_povezave")
        except Odjavljen:
            self._ob_odjavi()
            return
        if self.sinh:
            await self.sinh.osvezi_nalogo()
        self._izrisi()

    def _vozi(self) -> bool:
        return bool(self.zadnji_fiks and (self.zadnji_fiks.get("hitrost") or 0) > PRAG_VOZNJE_MS
                    and time.time() - self.zadnji_fiks["ts"] < 30)

    async def _zivljenje(self, e) -> None:
        self.v_ospredju = e.state in (ft.AppLifecycleState.RESUME, ft.AppLifecycleState.SHOW)
        if self.v_ospredju and self.sinh:
            if self.posiljalec:
                self.posiljalec.zbudi()
            await self.sinh.osvezi_nalogo()

    async def _zanka_opozoril(self) -> None:
        """Nepotrjeno obvestilo: vibracija (in sistemsko obvestilo v ozadju) vsakih 10 s do potrditve."""
        while self.sinh and self.sinh._tece:
            await asyncio.sleep(OPOZORILO_VSAKIH_S)
            if (self.naloga or {}).get("obvestila"):
                await self.naprava.opozori(t("obvestilo.sistemsko_naslov"), t("obvestilo.sistemsko_besedilo"),
                                           not self.v_ospredju)

    async def _zanka_baterije(self) -> None:
        while self.posiljalec and self.posiljalec._tece is not False:
            self.posiljalec.baterija = await self.naprava.baterija()
            await asyncio.sleep(120)

    # ================= dejanja =================
    def _trenutni(self) -> dict | None:
        return next((k for k in (self.naloga or {}).get("koraki", []) if k.get("trenutni")), None)

    async def _potrdi(self, _=None) -> None:
        obv = (self.naloga or {}).get("obvestila") or []
        if not obv or self._delam:
            return
        await self._izvedi(lambda: self.odjemalec.potrdi(obv[-1]["id"]))
        await self.naprava.preklici_obvestilo()

    async def _stanje(self, e) -> None:
        st = e.control.data
        korak = self._trenutni()
        await self._izvedi(lambda: self.odjemalec.stanje(st, korak))
        if self.posiljalec:
            self.posiljalec.zbudi()  # položaj ob spremembi stanja

    async def _izvedi(self, klic) -> None:
        if self._delam:
            return
        self._delam = True
        self.sporocilo.value = ""
        self._izrisi()
        try:
            n = await klic()
            await self._ob_nalogi(n)
        except Zavrnjeno as z:
            self.sporocilo.value = t("stanje.napaka", napaka=str(z))
            if self.sinh:
                await self.sinh.osvezi_nalogo()
        except NiPovezave:
            self.sporocilo.value = t("stanje.ni_povezave")
        except Odjavljen:
            self._ob_odjavi()
        finally:
            self._delam = False
            self._izrisi()

    # ================= izris =================
    def _zgradi_glavni(self) -> None:
        self.t_status = ft.Text("")
        self.t_povezava = ft.Text("")
        self.t_enota = ft.Text("")
        self.opozorilo = ft.Column([])
        self.korak = ft.Column([])
        self.nav = ft.Column([])
        self.gumbi = ft.Column([])
        self.preostali = ft.Column([])
        self.sporocila_disp = ft.Column([])
        self.meni = ft.Column([])
        self.predlogi = ft.Column([])
        self.g_odjava = ft.TextButton(content=t("odjava.gumb"), on_click=self._odjava)
        self.page.controls.clear()
        self.page.add(ft.SafeArea(content=ft.Column([
            self.t_enota, self.t_status, self.t_povezava,
            self.opozorilo, self.sporocila_disp, self.korak, self.nav, self.gumbi, self.sporocilo, self.predlogi,
            self.meni, self.preostali,
            ft.Row([self.g_odjava]),
        ], scroll=ft.ScrollMode.AUTO, expand=True), expand=True))

    def _besedilo_gps(self) -> str:
        if self.gps_napaka:
            return t("gps.ni_gps")
        st = self.gps.get("stanje")
        if st in ("ni_dovoljenja", "izklopljen", "odjavljen"):
            return t("gps." + st)
        if not self.zadnji_fiks:
            return t("gps.ni_gps")
        starost = time.time() - self.zadnji_fiks["ts"]
        osnova = t("gps.v_vrsti", n=self.gps.get("v_vrsti", 0)) if st == "v_vrsti" and self.gps.get("v_vrsti") else t("gps.posiljam")
        return f"{osnova} · {t('gps.zadnji', cas=i18n.trajanje(starost))}"

    def _izrisi(self) -> None:
        if not hasattr(self, "t_status"):
            return
        self._zadnji_izris = time.time()
        n = self.naloga or {}
        e = n.get("enota") or self.seja.podatki.get("enota") or {}
        self.t_enota.value = f"{e.get('id', '')} · {t('izmena.stanje', stanje=t('enota.' + e['stanje']) if e.get('stanje') else '–')}"
        self.t_status.value = self._besedilo_gps()
        self.t_povezava.value = t("povezava." + self.povezava)

        # obvestilo o novem planu (potrditev prejema)
        self.opozorilo.controls.clear()
        obv = n.get("obvestila") or []
        if obv:
            r = obv[-1]["razlike"]
            vrstice = [ft.Text(t("obvestilo.nov_plan"))]
            if r.get("dodani"):
                vrstice.append(ft.Text(t("obvestilo.dodano", seznam=", ".join(f"{a} ({t('naloga.' + b)})" for a, b in r["dodani"]))))
            if r.get("odstranjeni"):
                vrstice.append(ft.Text(t("obvestilo.odstranjeno", seznam=", ".join(a for a, _ in r["odstranjeni"]))))
            if r.get("vrstni_red"):
                vrstice.append(ft.Text(t("obvestilo.vrstni_red")))
            vrstice.append(ft.Row([velik_gumb(t("obvestilo.potrdi"), self._potrdi, "potrdi")]))
            self.opozorilo.controls.extend(vrstice)

        # trenutni korak
        self.korak.controls.clear()
        k = self._trenutni()
        if not k:
            self.korak.controls.append(ft.Text(t("naloga.ni")))
        else:
            nal = k.get("naloga") or {}
            mesto = (nal.get("dostava") if k["akcija"] == "dostava" else nal.get("prevzem")) or {}
            pot = nal.get("potrjeno") or {}
            vrsta = t("naloga.nujna") if k.get("nujna") else t("naloga." + k["akcija"])
            vrstice = [ft.Text(t("naloga.trenutni")),
                       ft.Text(f"{vrsta} · {t('naloga.prioriteta', p=k['prioriteta'])} · {k['naloga_id']}"),
                       ft.Text(t("naloga.naslov", v=mesto.get("naslov") or pot.get("naslov") or "–"))]
            if mesto.get("orientacija") or pot.get("orientacija"):
                vrstice.append(ft.Text(t("naloga.orientacija", v=mesto.get("orientacija") or pot.get("orientacija"))))
            vrstice.append(ft.Text(t("naloga.opis", v=nal.get("opis") or "–")))
            for polje, kljuc in (("stanje_pacienta", "naloga.stanje_pacienta"), ("stevilo_poskodovanih", "naloga.poskodovani"),
                                 ("potrebna_oprema", "naloga.oprema"), ("kontakt", "naloga.kontakt")):
                if pot.get(polje) not in (None, "", []):
                    v = pot[polje]
                    vrstice.append(ft.Text(t(kljuc, v=", ".join(v) if isinstance(v, list) else v)))
            if k.get("eta"):
                ura = datetime.fromtimestamp(k["eta"]).strftime("%H:%M")
                zam = f" · {t('naloga.zamuda', min=round(k['zamuda_s'] / 60))}" if k.get("zamuda_s") else ""
                vrstice.append(ft.Text(t("naloga.eta_plan", ura=ura) + zam))
            vrstice.append(ft.Text(t("naloga.faza", faza=t("faza." + (n.get("faza") or "dodeljeno")))))
            self.korak.controls.extend(vrstice)

        # navigacija po potrjeni poti
        self.nav.controls.clear()
        if k:
            pot = n.get("pot")
            if not pot:
                self.nav.controls.append(ft.Text(t("nav.ni_poti")))
            else:
                p = (self.zadnji_fiks["lat"], self.zadnji_fiks["lon"]) if self.zadnji_fiks else None
                s = stanje_navigacije(p, pot, (k["lat"], k["lon"]), (self.zadnji_fiks or {}).get("hitrost"))
                self.nav.controls.append(ft.Text(t("nav.razdalja", razdalja=i18n.razdalja(s["preostalo_m"]), cas=i18n.trajanje(s["eta_s"]))))
                if s.get("smer"):
                    self.nav.controls.append(ft.Text(t("nav.smer", smer=s["smer"], razdalja=i18n.razdalja(s.get("razdalja_do_naslednje_m")))))
                if s.get("izven_poti"):
                    self.nav.controls.append(ft.Text(t("nav.izven_poti", razdalja=i18n.razdalja(s["odmik_m"]))))
                if not pot.get("cestna", True):
                    self.nav.controls.append(ft.Text(t("nav.zracna")))
                elif not pot.get("navodila"):
                    self.nav.controls.append(ft.Text(t("nav.ni_navodil")))
                if pot.get("kakovost") == "ocena":
                    self.nav.controls.append(ft.Text(t("nav.ocena")))

        # gumbi za dovoljena stanja (samo dovoljeni prehodi)
        self.gumbi.controls.clear()
        oprema = k and not ((k.get("naloga") or {}).get("kolicina") or {}).get("pacienti")
        for st in n.get("dovoljena_stanja", []):
            if st == "prejeto" and obv:
                continue  # potrditev prejema je zgoraj
            napis = t("stanje.pacient_nalozen.oprema") if st == "pacient_nalozen" and oprema else t("stanje." + st)
            g = velik_gumb(napis, self._stanje, "stanje-" + st)
            g.data, g.disabled = st, self._delam
            self.gumbi.controls.append(ft.Row([g]))

        for st in n.get("mogoci_predlogi", []):
            g = velik_gumb(t("stanje.predlog." + st), self._stanje, "predlog-" + st)
            g.data, g.disabled = st, self._delam
            self.gumbi.controls.append(ft.Row([g]))
        if self._pot_cas:
            self.nav.controls.append(ft.Text(t("pot.posodobljena", ura=datetime.fromtimestamp(self._pot_cas).strftime("%H:%M"))))

        # neprebrana / zadnja sporočila dispečerja (vedno vidna)
        self.sporocila_disp.controls.clear()
        for m in [m for m in n.get("sporocila", []) if m["smer"] == "od_dispecerja"][:2]:
            self.sporocila_disp.controls.append(ft.Text(f"{datetime.fromtimestamp(m['cas']).strftime('%H:%M')} · "
                                                        + t("sporocilo.od_dispecerja", besedilo=m["besedilo"])))

        # stanje predlogov dispečerju
        self.predlogi.controls.clear()
        for p in (n.get("predlogi") or [])[-3:]:
            vrstica = f"{t('predlog.' + p['tip'])}: {t('predlog.' + p['stanje'])}"
            if p.get("razlog"):
                vrstica += " · " + t("predlog.razlog", razlog=p["razlog"])
            self.predlogi.controls.append(ft.Text(vrstica))
        if self.predlogi.controls:
            self.predlogi.controls.insert(0, ft.Text(t("predlog.naslov")))

        # meni: sporočila / prijava težave (skrito, dokler voznik ne odpre -> manj motenj med vožnjo)
        self.meni.controls.clear()
        self.meni.controls.append(ft.Row([
            velik_gumb(t("meni.zapri") if self.razdelek == "sporocila" else t("meni.sporocila"), self._razdelek("sporocila"), "meni-sporocila"),
            velik_gumb(t("meni.zapri") if self.razdelek == "tezava" else t("meni.tezava"), self._razdelek("tezava"), "meni-tezava")]))
        if self.razdelek == "sporocila":
            for i in range(0, len(HITRA), 2):
                vrsta = []
                for koda in HITRA[i:i + 2]:
                    g = velik_gumb(t("hitro." + koda), self._hitro, "hitro-" + koda)
                    g.data = koda
                    vrsta.append(g)
                self.meni.controls.append(ft.Row(vrsta))
            if self._vozi():
                self.meni.controls.append(ft.Text(t("sporocilo.med_voznjo")))
            else:
                self.meni.controls.append(self.besedilo_sporocila)
                self.meni.controls.append(ft.Row([velik_gumb(t("sporocilo.poslji"), self._besedilo, "poslji-besedilo")]))
        elif self.razdelek == "tezava":
            self.meni.controls.append(ft.Row([velik_gumb(t("tezava.okvara_potrdi") if self._potrjuje_okvaro else t("tezava.okvara"),
                                                         self._okvara, "okvara")]))
            self.meni.controls.append(ft.Text(t("tezava.ovira")))
            for i in range(0, len(OVIRE), 2):
                vrsta = []
                for v in OVIRE[i:i + 2]:
                    g = velik_gumb(t("ovira." + v), self._ovira, "ovira-" + v)
                    g.data = v
                    vrsta.append(g)
                self.meni.controls.append(ft.Row(vrsta))

        # preostali koraki
        self.preostali.controls.clear()
        ostali = [x for x in n.get("koraki", []) if not x.get("opravljen") and not x.get("trenutni")]
        if ostali:
            self.preostali.controls.append(ft.Text(t("naloga.preostali")))
            for x in ostali:
                naslov = (((x.get("naloga") or {}).get("dostava" if x["akcija"] == "dostava" else "prevzem")) or {}).get("naslov", "")
                self.preostali.controls.append(ft.Text(f"{t('naloga.' + x['akcija'])} · P{x['prioriteta']} · {x['naloga_id']} · {naslov}"))
        with contextlib.suppress(Exception):
            self.page.update()
