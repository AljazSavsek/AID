# AID – regijski dispečerski center z AI poslušalcem

Prototip za AdaHack 2026 (Epilogov izziv). **AI predlaga, dispečer odloča.**
Obseg tega dela je samo regijski center; pogledi za reševalce na terenu niso vključeni (glej [ODLOCITVE.md](ODLOCITVE.md)).

```
Klic → Prepis → AI poslušalec → Predlogi → Dispečer (sprejme / spremeni / zavrne)
                                              ↓ potrjeno
           Geokodiranje → Usmerjanje (skrito, redundantno) → Planer (OR-Tools) → Predlog plana → Dispečer
```

## Zagon

```bash
pip install -r requirements.txt
```

```bash
uvicorn api.app:app --port 8000
```

Odprite <http://localhost:8000/> in kliknite **SPREJMI KLIC** – predvaja se demo klic (padec z lestve, Celovška cesta).
Brez ključev sistem dela v celoti lokalno: AI poslušalec s pravili, geokodiranje iz lokalnega registra, časi prihoda kot ocena.

Simulator klica kot zunanji odjemalec (namesto VoIP):

```bash
python -m zajem.simulator --url http://localhost:8000 --hitrost 1
```

Testi:

```bash
python -m pytest -q tests
```

## Ključi API (neobvezni)

Ključe vpišite v lokalno datoteko `.env` (predloga: `.env.example`). Strežnik jo prebere ob zagonu;
datoteka je v `.gitignore` in nikoli ne gre na GitHub.

```bash
cp .env.example .env
```

## Okoljske spremenljivke (vse neobvezne)

| Spremenljivka | Namen |
|---|---|
| `ANTHROPIC_API_KEY`, `AID_LLM_MODEL`, `AID_LLM_EFFORT` | AI poslušalec v oblaku (privzeto `claude-opus-5-5`, effort `low`) |
| `OLLAMA_URL`, `OLLAMA_MODEL` | lokalni LLM (rezerva) |
| `AID_PRAVILA=0` | izklopi rezervo s pravili |
| `TOMTOM_API_KEY`, `ORS_API_KEY`, `OSRM_URL` | ponudniki poti (skriti pred dispečerjem) |
| `TOMTOM_MAP_KEY`, `AID_KARTA_ATRIBUCIJA` | podlaga karte in sloj prometa prek strežniškega posrednika `/ploscice/…` (brez ključa: OSM/CARTO) |
| `GURS_GEOKODER_URL`, `AID_NOMINATIM=0`, `AID_REGISTER_NASLOVOV` | geokodiranje |
| `AID_HELI_KMH`, `AID_HELI_DODATEK_MIN` | helikopter (privzeto 200 km/h + 5 min) |
| `AID_DB`, `AID_IZVOZ`, `AID_USMERJANJE_LOG` | poti do SQLite, izvoza JSON, internega dnevnika poti |
| `AID_SKRBNIK_ZETON` | dostop do `/interno/zdravje` |

Ključi so samo na strežniku; frontend jih nikoli ne dobi.

## Vmesnik

**Klic** – levo čakajoči klici in telefon, sredina AI transkripcija v živo (citati, ki podpirajo predloge, so označeni),
desno predlagani dispečerski obrazec. Vsaka kartica ima vrednost, zaupanje (rumeno < 0,5), citat (klik → skok na prepis) in
gumbe **Sprejmi** (`Enter`), **Uredi** (`E`), **Zavrni** (`Del`, neobvezen razlog). **Sprejmi vse ≥ 0,8** nikoli ne zajame
prioritete in enote. **UREDI PODATKE** odpre vsa polja, **ODOBRI IN POŠLJI** uporabi samo potrjene podatke.

**Plan in karta** – enote, karta z aktivnim planom in predlogom, primerjava prej/potem (spremenjene dodelitve, zamude po
prioritetah, dodatni km, nepokrite naloge, razlaga), **Pokaži alternativo**, **Potrdi plan**, časovnica po enotah in ročni vnos
dogodkov (okvara, megla, zmogljivost centra, prioriteta).

## Moduli

| Mapa | Vsebina |
|---|---|
| `api/` | FastAPI, WebSocket `/ws`, polling `/stanje`, storitev centra (`storitev.py`) |
| `dnevnik/` | podatkovni model, SQLite (WAL) in revizijska sled, izvoz JSON |
| `poslusalec/` | AI poslušalec: shema, preverjanje dokazov, oblak → Ollama → pravila |
| `geokodiranje/` | GURS → lokalni register → Nominatim → klik na karto |
| `usmerjanje/` | skrit servis poti: hedging, varovalka, health check, predpomnilnik, smiselnost, helikopter |
| `planer/` | OR-Tools VRP in dogodki plana |
| `prepis/`, `zajem/` | STT z rezervo, simulator klica |
| `static/` | vmesnik (HTML/JS + Leaflet, brez build koraka) |
| `demo/` | scenarij (UKC, 4 vozila, 2 motorista, helikopter, 8 nalog), klic, register naslovov |

## Voznik (veja `voznik`)

Aplikacija voznika pošilja GPS in prejema naloge. API: `POST /voznik/prijava`, `/voznik/osvezi`, `/voznik/odjava`,
`GET /voznik/jaz`, `POST /voznik/polozaj`. Demo kode enot so v `demo/scenarij.json` (`kode_voznikov`).
Dispečer vidi žive položaje, starost in stanje »ni signala« v pogledu **Plan in karta**. Podrobnosti v [ODLOCITVE.md](ODLOCITVE.md) (#28–36).

| Spremenljivka | Namen |
|---|---|
| `AID_VOZNIK_SKRIVNOST` | ključ za podpis žetonov voznikov (brez nje: nov ob vsakem zagonu) |
| `AID_VOZNIK_DOSTOP_S`, `AID_VOZNIK_OSVEZITEV_S` | trajanje žetonov (900 s, 12 h) |
| `AID_VOZNIK_DEMO_KODE=0` | ne naloži demo kod iz scenarija |
| `AID_GPS_AKTIVNO_FIKS_S`, `AID_GPS_AKTIVNO_POSILJANJE_S`, `AID_GPS_PROSTO_FIKS_S`, `AID_GPS_PROSTO_POSILJANJE_S`, `AID_GPS_BATERIJA_POSILJANJE_S` | frekvence GPS |
| `AID_SIGNAL_ZAKASNJENO_S`, `AID_SIGNAL_BREZ_S` | pragova signala na nalogi (30 s, 60 s) |
| `AID_POLOZAJ_HRANJENJE_DNI`, `AID_GPS_DOVOLI_LAZNO` | hramba zgodovine (30), sprejem lažnih položajev |

### Aplikacija voznika (`aplikacija_voznik/`, Flet)

Razvoj na računalniku (simuliran GPS vozi po potrjeni poti):

```bash
pip install -r aplikacija_voznik/requirements-razvoj.txt
```

```bash
cd aplikacija_voznik && AID_STREZNIK=http://localhost:8000 AID_VOZNIK_SIMULACIJA=1 python main.py --splet
```

Prijava: enota `RV-1`, koda `DEMO-RV1`. Telefon (Android): `flet build apk`, nato preizkus po
[aplikacija_voznik/PREIZKUS_NAPRAVE.md](aplikacija_voznik/PREIZKUS_NAPRAVE.md).

| Endpoint | Namen |
|---|---|
| `GET /voznik/naloga` | koraki aktivnega plana, potrjeni podatki, pot trenutnega koraka, dovoljena stanja, odprta obvestila |
| `POST /voznik/stanje` | prejeto, na_poti, na_prizoriscu, pacient_nalozen, dostavljeno, opravljeno, prosto, ni_na_voljo |
| `POST /voznik/potrdi` | potrditev prejema plana |
| `WS /ws/voznik`, `GET /voznik/dogodki` | spremembe za napravo (prvo sporočilo: `{"zeton": …}`) |
| `POST /voznik/sporocilo`, `/voznik/sporocilo/{id}/prebrano` | sporočila ekipe (hitra ali besedilo) |
| `POST /voznik/okvara`, `POST /voznik/ovira` | predloga ekipe (dispečer ju potrdi) |

Dispečer (razdelek **Ekipe**): `GET /ekipe/predlogi`, `POST /ekipe/predlog/{id}/{sprejmi\|spremeni\|zavrni}`,
`GET /ekipe/eskalacije`, `GET /ekipe/sporocila`, `POST /enote/{id}/sporocilo`, `GET /ovire`.
