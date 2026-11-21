# Odločitve

Obseg: **samo regijski dispečerski center**. Vse, kar se tiče reševalca na terenu, je izpuščeno
(zaslon voznika/navigacija, premikanje enot po poteh, sporočila ekip `POST /sporocilo`, obveščanje javnosti).
Kjer specifikacija ni določala podrobnosti, je izbrana najpreprostejša rešitev, ki ne krši pravil iz razdelka 2.

## Splošno
| # | Odločitev | Razlog |
|---|---|---|
| 1 | Izpuščeno: pogled voznika, javnosti, `POST /sporocilo`, WS `enota_premik`, simulirana ura s premikanjem enot. Enote ostanejo na zadnji znani lokaciji. | Zahteva uporabnika: samo regijski center. |
| 2 | Dogodke plana (okvara, megla …) vnese dispečer ročno ali jih AI zazna **iz klica**; dispečer jih vedno potrdi. | Nadomestek za sporočila ekip; pravilo 2.1. |
| 3 | »ODOBRI IN POŠLJI« iz **potrjenih** podatkov ustvari nalogo `nujna_voznja` in dogodek `nujna_naloga` → planer predlaga plan, ki ga dispečer potrdi posebej. Dejansko pošiljanje ekipi ni del tega obsega. | Dvojna potrditev: podatki klica, nato plan. |
| 4 | Začetni plan izmene ob zagonu potrdi `sistem (začetek izmene)` – predstavlja plan, ki že velja. Vsak nadaljnji plan potrdi dispečer. | Demo potrebuje izhodiščni plan za primerjavo prej/potem. |
| 5 | Prijava je poenostavljena: ime dispečerja v glavi se pošilja kot `X-Uporabnik` in gre v revizijo. | Prototip; avtentikacija ni bila specificirana. |
| 6 | Objekti so v SQLite kot JSON (`objekti(tip, id, podatki)`), revizija v svoji tabeli. WAL, izvoz JSON vsakih 60 s v `podatki/izvoz.json`. | Najpreprostejša shema, ki pokrije model iz razdelka 4. |

## AI poslušalec
| # | Odločitev | Razlog |
|---|---|---|
| 7 | Primarni LLM: Claude prek Anthropic API (`ANTHROPIC_API_KEY`), model `claude-opus-5-5` (nastavljivo z `AID_LLM_MODEL`), `effort: low` zaradi cilja ≤ 3 s, strukturiran izhod (JSON shema), strežniška rezerva ob zavrnitvi (`fallbacks: "default"`). Rezerva: Ollama (`OLLAMA_URL`). | Razdelek 5 in 7. |
| 8 | Med lokalni LLM in »brez predlogov« je dodana **deterministična rezerva s pravili** (`poslusalec/pravila.py`), ki vrača izhod po isti shemi z dobesednimi citati. Izklop: `AID_PRAVILA=0`. | Demo in delovanje brez interneta; nič se ne domneva – vsak podatek ima citat. |
| 9 | Vsak izhod (tudi LLM) gre skozi `validiraj()`: posodobitev brez citata, ki se dobesedno pojavi v navedenem segmentu, se zavrže. | Pravilo 2.3 – AI si ničesar ne izmišlja. |
| 10 | `lokacija` predlaga geokodiranje (ne LLM), `predlagana_enota` usmerjanje (najhitrejša prosta enota). Oba sta predloga z dokazom (citat naslova) in ju dispečer potrdi. | Tabela polj v razdelku 4. |
| 11 | Dvoumen naslov (več kandidatov v registru) → stari predlog `zastarelo`, več alternativnih predlogov z nižjim zaupanjem (×0,6). | Razdelek 5: naslov se ne ugiba. |
| 12 | Neveljaven ali neznan dogodek iz klica (npr. okvara brez vozila) se ne da potrditi brez dopolnitve – vmesnik ponudi izbiro vozila. | Razdelek 9 (»Katero vozilo?«). |

## Geokodiranje, usmerjanje, planer
| # | Odločitev | Razlog |
|---|---|---|
| 13 | Geokodiranje: GURS storitev (`GURS_GEOKODER_URL`) → lokalni izvoz registra naslovov (`demo/naslovi.json`) → Nominatim (`AID_NOMINATIM=0` izklopi) → klik na karto. | Javni GURS geokoder z enostavnim API ni na voljo; lokalni izvoz RPE deluje brez interneta. |
| 14 | Usmerjanje: TomTom (`TOMTOM_API_KEY`) → ORS (`ORS_API_KEY`) → OSRM (`OSRM_URL`) → star predpomnilnik → zračna ocena (×1,35, 45 km/h). Ponudnik brez nastavitve se preskoči. | Razdelek 7. |
| 15 | Matrika za planer: celotna matrika je hitra ocena; vrstice od enot do ciljev izboljša usmerjevalni servis z rokom 1,5 s. | Polna matrika prek API (~900 klicev) ne gre v cilj ≤ 5 s za nov plan. |
| 16 | Planer: čas iskanja 2,2 s za najboljši plan + 0,6 s za vsako alternativo (»najmanj sprememb«, »brez helikopterja«, »najkrajši skupni čas«); prikažejo se le različne. Skupaj ~4 s. | Razdelek 8 (3 s) in 11 (≤ 5 s). |
| 17 | Uteži: zamuda P1…P5 = 100/40/15/5/2 na sekundo; izpust P1 = 5·10⁷ … P5 = 3·10⁵; kazen spremembe = 600 (≈ 10 min). Helikopter: fiksni strošek 40 min in le za P1/P2 naloge. | Ciljna funkcija iz razdelka 8; helikopter je redek vir. |
| 18 | Ob okvari se tovor (`tovor` enote) pretvori v novo nalogo prevzema na lokaciji okvare; stara naloga dobi stanje `prenesena`. | Razdelek 9. |
| 19 | Manjša zmogljivost centra: paciente nižje prioritete planer preusmeri v najbližji center s prostim mestom; če ga ni, zapiše opombo o zamiku. | Razdelek 9. |
| 20 | Prioriteta nujne naloge iz klica določi okno: P1 10 min, P2 15, P3 25, P4 45, P5 60. | »Visoka prioriteta in ozko okno«. |

## Prepis in zajem
| # | Odločitev | Razlog |
|---|---|---|
| 21 | Klic za demo prihaja kot besedilo (`demo/klic1.json`, `POST /demo/klic` ali `python -m zajem.simulator`). `prepis/stt.py` ima verigo oblak → faster-whisper (+ Silero VAD) → ročni zapis; VoIP (Twilio/Asterisk) še ni izveden. | Vrstni red izdelave, korak 6. |

## Vmesnik
| # | Odločitev | Razlog |
|---|---|---|
| 22 | Dva pogleda v regijskem centru: **Klic** (levo klici in telefon, sredina prepis, desno obrazec) in **Plan in karta** (enote, karta, predlog plana prej/potem, časovnica, ročni dogodek). | Vizualna navodila uporabnika + razdelek 6 (karta, časovnica). |
| 23 | Leaflet je vključen lokalno (`static/vendor/`), podlaga karte je CARTO/OSM (brez ključa). | Brez build koraka, deluje tudi, ko CDN ni dosegljiv. |
| 24 | Barve strogo po logotipu (#1D2228, bela, #8A1525); izjema je rumena za zaupanje < 0,5. Logotip je začasen SVG (`static/img/aid-logo.svg`). | Pravi logotip ni bil priložen. |
| 25 | Povezava: WebSocket → ponovni priklop z eksponentnim zamikom + polling `/stanje` vsaki 2 s → zadnje stanje v `localStorage` in ocena časov prihoda v brskalniku. | Razdelek 7, vrstica »Povezava frontend«. |
| 26 | `/interno/zdravje` zahteva glavo `X-Skrbnik-Zeton`, enako `AID_SKRBNIK_ZETON`; brez nastavljenega žetona je endpoint zaprt. | Pravilo 2.4 – imena ponudnikov samo za skrbnika. |
| 27 | Karta in promet: strežniški posrednik `/ploscice/{karta,promet,nesrece}/{z}/{x}/{y}.png` (ključ `TOMTOM_MAP_KEY` ostane na strežniku, odgovor brez imena ponudnika, predpomnilnik 24 h / 2 min, varovalka 60 s). Ob napakah vmesnik preklopi na OSM/CARTO. Atribucija je nastavljiva (`AID_KARTA_ATRIBUCIJA`); pogoje ponudnika glede oznake na karti mora preveriti ekipa. | Pravili 2.4 in 2.7. |
