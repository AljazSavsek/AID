# Odločitve

Obseg veje `voznik`: regijski center **in** voznik (skupni strežnik; Flet aplikacija in spletni zaslon).
Veja `regijski-center` vsebuje samo del za dispečerja; odločitvi 1–2 veljata zanjo. Obveščanje javnosti ni del nobene veje.
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

## Voznikova stran (veja `voznik`) – nadomesti #1 za te točke
Odločitev #1 (izpuščen pogled voznika, `enota_premik`, sporočila ekip) za spodnje točke **ne velja več**; zgodovina ostane zgoraj.

| # | Odločitev | Razlog |
|---|---|---|
| 28 | Vsaka enota ima voznikovo napravo (ena enota = ena naprava). Odjemalec: Flet (Android najprej, iOS kasneje); izvedljivost GPS v ozadju se najprej preveri na pravi napravi. Helikopter uporablja isto aplikacijo brez cestne navigacije in brez dodatnih stanj. | Odločitev uporabnika; Flet ima storitev v ospredju + wake lock (Android) in posodobitve v ozadju (iOS). PWA v ozadju ne pošilja GPS. |
| 29 | Prijava: enota + koda (scrypt zgoščeno; izda jo skrbnik `POST /enote/{id}/koda` z `X-Skrbnik-Zeton` ali demo `kode_voznikov` v scenariju, izklop `AID_VOZNIK_DEMO_KODE=0`). Dostopni žeton 15 min, osvežitveni 12 h, HMAC-SHA256 (`AID_VOZNIK_SKRIVNOST`; brez nje nova ob zagonu). Osvežitveni žeton se zamenja ob vsaki uporabi; ponovna uporaba prekliče sejo. Nova prijava odjavi prejšnjo napravo. 5 napačnih kod v 5 min -> zaklep. | Žeton voznika dostopa samo do lastne enote; enota vedno iz žetona. |
| 30 | Položaji: paketi fiksov `{seq, ts, lat, lon, hitrost, smer, natancnost}`, ključ (enota, naprava, seq) -> idempotentno. Trenutni položaj se posodobi samo z novejšim fiksom (čas fiksa), zato ponovno poslana vrsta ne premakne enote nazaj. Odmik ure naprave > 5 s se popravi. Nemogoč skok (> 250 km/h v < 60 s), lažni (mocked) in nenatančni (> 500 m) fiksi gredo samo v zgodovino. | Ponovni poskusi in priklopi ne smejo podvajati ali premikati nazaj. |
| 31 | Živi položaj je v tabeli `polozaj_enote`, ne v JSON enote; `Center.enote()` ga prekrije. | Planer ob računanju shrani stari posnetek stanja in bi povozil novejši položaj. Planer tako sam dobi žive položaje. |
| 32 | `enota_premik` gre samo prek WebSocket (brez `seq`, ne v zgodovino); `/stanje` vrne posnetek `polozaji` za polling. | Pogosti premiki bi izrinili pomembne dogodke iz zgodovine 1000 dogodkov. |
| 33 | Signal po starosti položaja: na nalogi 30 s zakasnjeno / 60 s ni signala; prosto glede na interval pošiljanja (privzeto 120 s / 180 s); brez fiksa »brez GPS«. Nadzor vsaki 2 s objavi `enota_signal` ob prehodih. Vmesnik starost računa tudi sam v času strežnika in vzame slabše stanje; brez povezave s strežnikom je stanje »neznano«. | Star položaj nikoli ni prikazan kot živ. |
| 34 | Frekvence GPS določa strežnik v vsakem odgovoru: na nalogi fiks 2 s / pošiljanje 5 s; prosto 30 s / 60 s; prosto z baterijo < 20 % brez polnjenja 120 s. Na nalogi se nikoli ne zniža. GPS teče samo med prijavo (tudi »ni na voljo« med izmeno), po odjavi nikoli. | Varčevanje baterije brez izgube nadzora med nalogo. |
| 35 | Zasebnost: položaji so osebni podatki. Namen: nadzor in razporejanje enot med izmeno. Hramba zgodovine 30 dni (`AID_POLOZAJ_HRANJENJE_DNI`), čiščenje vsako uro. Zgodovina samo za skrbnika (`GET /interno/polozaji/{id}`), vsak vpogled v revizijo. Prijave, odjave, zavrnjeni dostopi in izdaje kod gredo v revizijo; posamezni paketi ne (zgodovina je v `polozaj`). Omejitve: 10 prijav/min na IP, 60 paketov/min na enoto. | Pravna presoja (GDPR, ZVOP-2) ostane naročniku. |
| 36 | Plan dobi opombo »RV-x: položaj star N min«, ko enota nima signala. | Dispečer ve, da planer računa s starim položajem. |
| 37 | Pot za trenutni korak izračuna strežnik (`usmerjanje`, skrit ponudnik) ob potrditvi plana in ob zaključku koraka, z zadnjega znanega položaja. Velja kot del potrjenega plana; izrecna potrditev poti in preusmeritev pride v fazi 2 (M5). Navodila za zavijanje (slovenščina) in opozorila o prometu vrne usmerjanje (glej #46); predaja zunanji navigaciji ni dovoljena. | Odgovor uporabnika (vprašanji 4, 5). |
| 38 | Koraki voznika so `koraki_po_enoti` AKTIVNEGA plana; ali je korak opravljen, sledi iz dejstev (naloga opravljena, tovor). Hrani se le faza trenutnega koraka (`voznik_faza`). | Brez ločenega števca, ki bi se razšel s planerjem. |
| 39 | Prehodi: dodeljeno → prejeto → na_poti → na_prizoriscu → {pacient_nalozen (prevzem s ciljem) \| dostavljeno (dostava) \| opravljeno (nujna vožnja brez cilja)}. Izmena: prosto ↔ ni_na_voljo samo brez aktivnih korakov. Ponovljen enak zahtevek je idempotenten; zahtevek za neaktualen korak → 409. Vsak prehod v revizijo. | Vprašanje 6. Prevoz pacienta z nujne vožnje (izbira bolnišnice) pride v M3 kot predlog dispečerju. |
| 40 | Učinki dejstev: pacient_nalozen → tovor enote; dostavljeno → iz tovora, naloga opravljena, center `zasedena` +1 (če pacienti); po zadnjem koraku enota »prosto«. Novega plana ne sprožijo. | Dejstva ekipe, plan ostane v rokah dispečerja. |
| 41 | `ni_na_voljo` je novo stanje enote; planer ga izpusti kot `okvaro` (`NERAZPOLOZLJIVA`), potrditev plana ga ne spremeni. Planer ob izračunu ne shrani več celotnega posnetka stanja, le spremenjene dostave (preusmeritve centrov). | Vprašanje 12; sicer bi planer povozil stanja voznika, sporočena med računanjem. |
| 42 | Potrditev prejema: ob potrditvi plana dobi vsaka enota, ki se ji je spremenil seznam preostalih korakov, obvestilo z razliko (dodani, odstranjeni, vrstni red) in rokom 30 s (`AID_VOZNIK_ROK_POTRDITVE_S`). »Prejeto« potrdi vsa odprta obvestila. Naslednji korak že potrjenega plana je samodejno »prejeto« (manj dotikov med vožnjo). Dispečer vidi »čaka potrditev« / »NI POTRDIL«; dejavna eskalacija pride v M3. | Vprašanje 7. |
| 43 | `WS /ws/voznik`: žeton v prvem sporočilu (ne v URL), dogodki samo za lastno enoto, `zagon` ob ponovnem zagonu strežnika ponastavi seq, »ziv« vsakih 15 s s ponovnim preverjanjem seje. Rezerva `GET /voznik/dogodki`. Naloga (`GET /voznik/naloga`) je vir resnice, dogodek je le znak za osvežitev. | Ponovna raba vzorca seq + priklop + polling. |
| 44 | Aplikacija (`aplikacija_voznik/`): jedro brez Flet (odjemalec, trajna vrsta SQLite, pošiljanje, sinhronizacija, navigacija) + tanek Flet sloj (`naprava.py`, `ui.py`). Brez oblikovanja; gumbi visoki 84 px, polna širina. Besedila v `i18n/sl.json`. Žetoni na napravi v zasebni mapi aplikacije, koda enote se ne shrani. Ob odjavi se neposlani fiksi zavržejo. Obvestilo: vibracija + sistemsko obvestilo (v ozadju) vsakih 10 s do potrditve. | Brez FCM (vprašanje 18). |
| 45 | Najbližja enota = najkrajši čas prihoda **s prometom** (Routing API s `traffic=true`), med prostimi enotami; ne zračna razdalja. Pot (`GET /pot`) se nariše po cestah na mini karti klica; plan se riše po cestah prek `POST /poti` (najprej ravna črta, nato geometrija). Gumb »Osveži po prometu« ponovno izračuna čase; če je zdaj hitrejša druga enota, je to le nov predlog (potrjena enota se ne zamenja). | Pravilo 2.1; zahteva uporabnika. |
| 46 | Drugi odjemalec istega API-ja voznika: spletni zaslon `static/voznik.html` (prijava enota + koda, `Authorization: Bearer`, `WS /ws/voznik` z žetonom v prvem sporočilu, polling `/voznik/dogodki`). Nočna 3D karta, pot po cestah s prometom, navodila za zavijanje, opozorila o zastojih iz odsekov poti, stanja iz `dovoljena_stanja`, potrditev prejema. GPS: naprava (`geolocation`, le na https ali localhost) ali demo »Simuliraj vožnjo« (10× pospešeno). Spletni zaslon ni nadomestek za Flet aplikacijo (brez GPS v ozadju, #28). | Demo na enem računalniku; obe aplikaciji uporabljata isti strežnik. |
| 47 | `POST /voznik/sporocilo` (enota iz žetona) → AI poslušalec → dogodek s stanjem `predlagano` (vir `ekipa`); pri okvari brez navedenega vozila se predlaga pošiljatelj. Dispečer ga v pogledu »Plan in karta« (Sporočila ekip) sprejme, spremeni ali zavrne (`/dogodek/{id}/sprejmi|zavrni`). | Razdelek 9 in pravilo 2.1. |
| 48 | »Obveščenih v bližini« na zaslonu voznika je simulacija in je tako označena – v prototipu ni vira teh podatkov. | Ne prikazujemo izmišljenih podatkov kot resničnih. |
| 49 | Ime uporabnika gre v glavo `X-Uporabnik` URL-kodirano (šumniki v HTTP glavah niso dovoljeni). | Napaka pri imenih s č/š/ž. |
| 50 | `python zazeni.py` zažene strežnik in odpre oba zaslona (dispečer + voznik); s Flet nameščenim zažene še aplikacijo voznika v simulaciji. Oba zaslona sta tudi namestljivi spletni aplikaciji (manifest). | Zahteva uporabnika: oboje naenkrat. |
