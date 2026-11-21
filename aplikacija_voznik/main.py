"""AID – aplikacija voznika (Flet). Zagon za razvoj:

    AID_STREZNIK=http://localhost:8000 AID_VOZNIK_SIMULACIJA=1 flet run main.py
    (ali: python main.py --splet  -> v brskalniku)

Gradnja: flet build apk  (glej pyproject.toml in PREIZKUS_NAPRAVE.md)
"""
import logging
import sys

import flet as ft

from ui import AplikacijaVoznika

logging.basicConfig(level=logging.INFO)


async def main(page: ft.Page):
    await AplikacijaVoznika(page).zacni()


if __name__ == "__main__":
    if "--splet" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8550
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=port, no_cdn=True)
    else:
        ft.run(main)
