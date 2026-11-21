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

## Okoljske spremenljivke (vse neobvezne)

| Spremenljivka | Namen |
|---|---|
| `ANTHROPIC_API_KEY`, `AID_LLM_MODEL`, `AID_LLM_EFFORT` | AI poslušalec v oblaku (privzeto `claude-opus-5-5`, effort `low`) |
| `OLLAMA_URL`, `OLLAMA_MODEL` | lokalni LLM (rezerva) |
| `AID_PRAVILA=0` | izklopi rezervo s pravili |
| `TOMTOM_API_KEY`, `ORS_API_KEY`, `OSRM_URL` | ponudniki poti (skriti pred dispečerjem) |
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
