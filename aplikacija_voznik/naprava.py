"""Storitve naprave prek Flet: GPS v ozadju, dovoljenja, wake lock, vibracija, sistemska obvestila, baterija.

Android: Geolocator z `foreground_notification_config` zažene storitev v ospredju (trajno obvestilo),
ki proces ohrani živ z ugasnjenim zaslonom; wake lock CPU je vklopljen. iOS: posodobitve v ozadju
(`allow_background_location_updates`, UIBackgroundModes=location v pyproject.toml).
Na računalniku/spletu brez GPS se uporabi simuliran vir (AID_VOZNIK_SIMULACIJA=1).
Vsak klic naprave je zavarovan: nepodprta platforma ne sme ustaviti aplikacije.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Callable

import flet as ft

from jedro.gps import SimuliranVir, iz_geolocatorja
from jedro.i18n import t

dnevnik = logging.getLogger("voznik.naprava")


class Naprava:
    def __init__(self, page: ft.Page):
        self.page = page
        self.mobilna = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
        self.android = page.platform == ft.PagePlatform.ANDROID
        self.simulacija = os.environ.get("AID_VOZNIK_SIMULACIJA") == "1" or not (self.mobilna or page.web)
        self.geo = None
        self.sim: SimuliranVir | None = None
        self._ob_fiksu: Callable[[dict], None] | None = None
        self._ob_napaki: Callable[[str], None] | None = None
        self._fiks_s = 30.0
        self._enota = ""
        self.haptika = ft.HapticFeedback()
        self.wakelock = ft.Wakelock()
        self.baterija_storitev = ft.Battery()
        self.obvestila = None
        with contextlib.suppress(Exception):
            import flet_notifications as fn
            self.obvestila = fn.Notifications()
        self.dovoljenja = None
        with contextlib.suppress(Exception):
            import flet_permission_handler as fph
            self.dovoljenja = fph.PermissionHandler()

    # ---------- dovoljenja ----------
    async def pripravi(self) -> str:
        """Vrne: ok | ni_dovoljenja | izklopljen. Vrstni red zahtev je pomemben na Androidu 10+:
        najprej »med uporabo«, šele nato »vedno« (sistem sicer zavrne)."""
        if self.simulacija:
            return "ok"
        import flet_geolocator as ftg
        self.geo = self.geo or ftg.Geolocator()
        try:
            if not await self.geo.is_location_service_enabled():
                return "izklopljen"
            st = await self.geo.request_permission()
            if st in (ftg.GeolocatorPermissionStatus.DENIED, ftg.GeolocatorPermissionStatus.DENIED_FOREVER):
                return "ni_dovoljenja"
        except Exception as e:  # noqa: BLE001
            dnevnik.warning("dovoljenje lokacije: %s", e)
            return "ni_dovoljenja"
        if self.mobilna and self.dovoljenja:
            import flet_permission_handler as fph
            for p in (fph.Permission.LOCATION_ALWAYS, fph.Permission.NOTIFICATION) + \
                     ((fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS,) if self.android else ()):
                with contextlib.suppress(Exception):
                    await self.dovoljenja.request(p)
        if self.obvestila:
            with contextlib.suppress(Exception):
                import flet_notifications as fn
                await self.obvestila.create_channel(fn.NotificationChannel(
                    id="naloge", name=t("obvestilo.kanal"), importance=fn.AndroidImportance.HIGH,
                    vibration_pattern=[0, 600, 300, 600, 300, 600]))
        return "ok"

    async def odpri_nastavitve(self) -> None:
        with contextlib.suppress(Exception):
            if self.geo:
                await self.geo.open_app_settings()

    # ---------- GPS ----------
    def _nastavitve_gps(self):
        import flet_geolocator as ftg
        if self.android:
            return ftg.GeolocatorAndroidConfiguration(
                accuracy=ftg.GeolocatorPositionAccuracy.BEST, distance_filter=0,
                interval_duration=ft.Duration(milliseconds=int(self._fiks_s * 1000)),
                foreground_notification_config=ftg.ForegroundNotificationConfiguration(
                    notification_title=t("ozadje.naslov"), notification_text=t("ozadje.besedilo", enota=self._enota),
                    notification_channel_name=t("ozadje.naslov"), notification_enable_wake_lock=True,
                    notification_enable_wifi_lock=True, notification_set_ongoing=True))
        if self.page.platform == ft.PagePlatform.IOS:
            return ftg.GeolocatorIosConfiguration(
                accuracy=ftg.GeolocatorPositionAccuracy.BEST, distance_filter=0,
                activity_type=ftg.GeolocatorIosActivityType.AUTOMOTIVE_NAVIGATION,
                pause_location_updates_automatically=False, show_background_location_indicator=True,
                allow_background_location_updates=True)
        return ftg.GeolocatorConfiguration(accuracy=ftg.GeolocatorPositionAccuracy.BEST)

    def zacni_gps(self, ob_fiksu: Callable[[dict], None], ob_napaki: Callable[[str], None], fiks_s: float,
                  enota: str, zacetek: tuple | None = None) -> None:
        self._ob_fiksu, self._ob_napaki, self._fiks_s, self._enota = ob_fiksu, ob_napaki, float(fiks_s), enota
        if self.simulacija:
            self.sim = self.sim or SimuliranVir(zacetek or (46.0569, 14.5058))

            async def po_postavitvi():
                # simulacija začne na zadnjem znanem položaju enote (prva naloga), ne na privzeti točki
                for _ in range(100):
                    if getattr(self, "_sim_postavljen", False):
                        break
                    await asyncio.sleep(0.1)
                await self.sim.zacni(ob_fiksu, self._fiks_s)
            self.page.run_task(po_postavitvi)
            return
        import flet_geolocator as ftg
        self.geo = self.geo or ftg.Geolocator()

        def na_polozaj(e):
            f = iz_geolocatorja(e.position)
            if f:
                ob_fiksu(f)

        def na_napako(e):
            ob_napaki(str(getattr(e, "data", "") or "napaka GPS"))

        self.geo.configuration = self._nastavitve_gps()
        self.geo.on_position_change = na_polozaj  # tok se začne, ko obstaja upravljalnik
        self.geo.on_error = na_napako
        self.geo.update()

    def nastavi_interval(self, fiks_s: float) -> None:
        if abs(float(fiks_s) - self._fiks_s) < 0.01:
            return
        self._fiks_s = float(fiks_s)
        if self.sim:
            self.sim.nastavi_interval(self._fiks_s)
        elif self.geo and self.geo.on_position_change:
            self.geo.configuration = self._nastavitve_gps()
            self.geo.update()  # tok se ponovno zažene z novim intervalom

    def ustavi_gps(self) -> None:
        if self.sim:
            self.sim.ustavi()
        if self.geo:
            self.geo.on_position_change = None
            self.geo.on_error = None
            with contextlib.suppress(Exception):
                self.geo.update()  # brez upravljalnikov se tok in storitev v ospredju ustavita

    def simulirana_pot(self, geometrija: list | None, enota: dict | None = None) -> None:
        if not self.sim:
            return
        if enota and enota.get("lat") is not None and not getattr(self, "_sim_postavljen", False):
            self.sim.polozaj = (enota["lat"], enota["lon"])  # začni na zadnjem znanem položaju enote
            self._sim_postavljen = True
        if geometrija and geometrija != getattr(self, "_sim_geometrija", None):
            self._sim_geometrija = geometrija
            self.sim.nastavi_pot(geometrija)

    # ---------- opozorila ----------
    async def opozori(self, naslov: str, besedilo: str, sistemsko: bool) -> None:
        with contextlib.suppress(Exception):
            await self.haptika.vibrate()
            await asyncio.sleep(0.25)
            await self.haptika.heavy_impact()
        if sistemsko and self.obvestila:
            with contextlib.suppress(Exception):
                import flet_notifications as fn
                await self.obvestila.show(1, title=naslov, body=besedilo,
                                          options=fn.NotificationOptions(android=fn.AndroidOptions(channel="naloge")))

    async def preklici_obvestilo(self) -> None:
        if self.obvestila:
            with contextlib.suppress(Exception):
                await self.obvestila.cancel(1)

    async def zaslon_prizgan(self, da: bool) -> None:
        with contextlib.suppress(Exception):
            await (self.wakelock.enable() if da else self.wakelock.disable())

    async def baterija(self) -> dict | None:
        try:
            nivo = await self.baterija_storitev.get_battery_level()
            st = await self.baterija_storitev.get_battery_state()
            return {"nivo": nivo, "polni": st in (ft.BatteryState.CHARGING, ft.BatteryState.FULL)}
        except Exception:  # noqa: BLE001
            return None
