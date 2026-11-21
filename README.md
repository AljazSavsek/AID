# AID – regijski dispečerski center z AI poslušalcem

Prototip za AdaHack 2026 (Epilogov izziv). **AI predlaga, dispečer odloča.**

Veja `voznik` vsebuje **oba dela skupaj** na istem strežniku: regijski center (dispečer) in voznika
(Flet aplikacija za telefon + spletni zaslon v vozilu). Veja `regijski-center` vsebuje samo del za dispečerja.

| Zaslon | Naslov |
|---|---|
| Regijski center (dispečer) | <http://localhost:8000/> |
| Reševalno vozilo (voznik) | <http://localhost:8000/voznik.html?enota=RV-2> |
| Opozorilo za javnost | <http://localhost:8000/javnost.html> |

```
Klic → Prepis → AI poslušalec → Predlogi → Dispečer (sprejme / spremeni / zavrne)
                                              ↓ potrjeno
           Geokodiranje → Usmerjanje (skrito, redundantno) → Planer (OR-Tools) → Predlog plana → Dispečer
```

## Zagon – vse naenkrat

```bash
pip install -r requirements.txt
```

```bash
pip install -r aplikacija_voznik/requirements-razvoj.txt
```
(drugi ukaz le za Flet aplikacijo voznika)

```bash
python zazeni.py
```

Na Windows lahko dvokliknete `zazeni.bat`, na Macu/Linuxu `./zazeni.sh`. En ukaz zažene **strežnik**, odpre
**zaslon dispečerja** in **spletni zaslon voznika** ter zažene **Flet aplikacijo voznika** (simuliran GPS).
Ustavite s `Ctrl+C`. Možnosti: `--brez-flet`, `--enota RV-3`, `--port 8000`, `--javno` (telefon v istem omrežju).

| Zaslon | Naslov | Prijava |
|---|---|---|
| Regijski center (dispečer) | <http://localhost:8000/> | `M. Novak` / `DEMO-DISP` (ali `A. Kos` / `DEMO-DISP2`) |
| Reševalno vozilo (splet) | <http://localhost:8000/voznik.html> | enota + koda, npr. `RV-3` / `DEMO-RV3` |
| Aplikacija voznika (Flet) | okno ali <http://localhost:8550> | enota + koda |
| Opozorilo za javnost | <http://localhost:8000/javnost.html> | brez prijave; klik na karto = vaša lokacija |

Opozorilo za javnost se prikaže, ko je v radiju 1,5 km vozilo, ki je **na poti na nujno vožnjo** (P1/P2) in pošilja GPS
(npr. spletni zaslon voznika z »Simuliraj vožnjo«). Javni endpoint ne vrne oznake vozila ne podatkov o nalogi ali pacientu.

Ena enota = ena naprava: prijava iste enote v drugi aplikaciji odjavi prejšnjo. Za demo prijavite spletni zaslon in
Flet aplikacijo z različnima voziloma. Oba spletna zaslona lahko v Chromu/Edgu namestite kot aplikacijo
(ikona »Namesti« v naslovni vrstici).

**Demo potek:** dispečer sprejme klic → potrdi naslov, lokacijo in prioriteto → v kartici **Vozilo za odhod** izbere vozilo
(prosta najprej, po času prihoda s prometom; vidi, ali ima voznik aplikacijo) → **ODOBRI IN POŠLJI** → **POTRDI PLAN IN POŠLJI**
→ voznik izbranega vozila takoj dobi nalogo, pot in navodila, potrdi prejem in sporoča stanja; dispečer to vidi pod obrazcem.
Če je voznik prijavljen pred klicem (npr. `voznik.html?enota=RV-3`, koda `DEMO-RV3`), se mu vse prikaže samodejno.

Samo strežnik (brez odpiranja oken):

```bash
uvicorn api.app:app --port 8000
```

Testi:

```bash
python -m pytest -q tests
```

## Prijava in varnost

- **Dispečer** se prijavi z imenom in geslom. Seja je piškotek `HttpOnly` / `SameSite=Strict`; ime v revizijski sledi
  pride iz seje (glava odjemalca se ne upošteva). Računi: `AID_DISPECERJI="Ime Priimek:geslo;…"`; demo računa iz
  scenarija izklopite z `AID_DISPECER_DEMO=0`. 5 napačnih gesel v 5 min zaklene račun.
- **Brez prijave** so dostopne le statične strani, prijava, API voznika (lastni žetoni), `/javnost/*` in `/interno/*`
  z žetonom skrbnika. Vse drugo (naloge, podatki pacientov, plan, `/ws`, revizija, karta, poti) vrne 401.
- **Kvota zunanjih ponudnikov:** karta in poti le s prijavo (dispečer ali voznik), omejitev zahtevkov na uporabnika,
  le območje Slovenije.
- **Skrivnosti** za žetone (`AID_DISPECER_SKRIVNOST`, `AID_VOZNIK_SKRIVNOST`) `zazeni.py` ustvari enkrat in jih hrani
  v `podatki/.skrivnosti.json` (ni v gitu), zato prijave preživijo ponovni zagon. Za strežnik jih nastavite v `.env`.
- Dokumentacija API (`/interno/docs`) je izklopljena; vklop `AID_DOCS=1`.

**Preizkus AI v oblaku** (z `ANTHROPIC_API_KEY` v `.env`; stane nekaj centov):

```bash
python -m poslusalec.preveri
```

Če AI v oblaku med delom odpove, dispečer dobi opozorilo »AI v oblaku ni na voljo – predloge pripravlja rezerva«,
razlog je na `/interno/zdravje`.

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

## Zaslon voznika

Nočna 3D karta s potjo po cestah (s prometom), navodila za zavijanje v slovenščini, kartice zgoraj levo
(čas do prihoda, obveščeni v bližini – simulacija, opozorila v živo: zastoji na poti in obvestila centra) in naloga desno.
Voznik vidi **samo naloge iz potrjenega plana in podatke, ki jih je potrdil dispečer**.

- statusi **NA POTI → NA KRAJU → PREVZETO / DOSTAVLJENO** gredo dispečerju in v revizijo,
- **Sporočilo centru** (npr. »Vozilo je obstalo. Oprema je še v njem.«) → AI predlaga dogodek → dispečer ga potrdi
  v pogledu »Plan in karta« (Sporočila ekip) → planer predlaga nov plan → dispečer ga potrdi → vozilo dobi novo nalogo,
- **Simuliraj vožnjo** (demo) premika vozilo po poti; dispečer vidi premik na karti.

## Moduli

| Mapa | Vsebina |
|---|---|
| `api/` | FastAPI, WebSocket `/ws`, polling `/stanje`, storitev centra (`storitev.py`), ekipa na terenu (`ekipa.py`) |
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

### Simulator vozil (brez telefona)

```bash
python -m zajem.vozilo_sim --url http://localhost:8000 --enote RV-1,RV-4
```

Vsako vozilo se prijavi z demo kodo, potrdi prejem plana, vozi po potrjeni poti in sporoča stanja do »opravljeno«;
dispečer vidi premike, stanja in potrditve. Uporablja isto jedro kot aplikacija voznika. Možnosti: `--pospesek 3`,
`--izpad 30:45` (45 s brez omrežja po 30 s – fiksi čakajo v vrsti in se nato pošljejo z izvirnimi časi),
`--ne-potrdi RV-4` (eskalacija), `--prevoz` (predlog prevoza z nujne vožnje), `--do-prostega`.
Skupaj z ostalim: `python zazeni.py --vozila RV-1,RV-4`. Simulirane enote ne prijavljajte še drugje (ena enota = ena naprava).

**Celoten krog:** dispečer sprejme klic in ODOBRI IN POŠLJI → potrdi plan → simulirano vozilo potrdi prejem → vozi →
na prizorišču → opravljeno (ali pacient naložen → dostava).

### Zasebnost položajev

| | |
|---|---|
| Namen | nadzor in razporejanje reševalnih enot med izmeno |
| Kdaj se zbira | samo med prijavo naprave enote; po odjavi nikoli (neposlani fiksi se zavržejo) |
| Kaj | enota, naprava (naključni id), čas, lat/lon, hitrost, smer, natančnost |
| Kdo vidi | dispečer: trenutni položaj in starost; zgodovino samo skrbnik (`GET /interno/polozaji/{id}`, vsak vpogled v reviziji) |
| Hramba | `AID_POLOZAJ_HRANJENJE_DNI` (privzeto 30) za položaje in sporočila; seje 1 dan po poteku |
| Zaščita | žeton samo za lastno enoto, omejevanje zahtevkov, revizija vseh dejanj voznika, ključi ponudnikov ostanejo na strežniku |

Pravno presojo (GDPR, ZVOP-2, rok hrambe) mora opraviti naročnik.

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
| `POST /enote/{id}/koda`, `POST /enote/{id}/odjavi-voznika` | skrbnik (`X-Skrbnik-Zeton`): nova koda, preklic sej |

Dispečer (razdelek **Ekipe**): `GET /ekipe/predlogi`, `POST /ekipe/predlog/{id}/{sprejmi\|spremeni\|zavrni}`,
`GET /ekipe/eskalacije`, `GET /ekipe/sporocila`, `POST /enote/{id}/sporocilo`, `GET /ovire`.
