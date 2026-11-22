# Whisper 112: AI pomočnik za dispečerje

Pri klicu na 112 mora dispečer poslušati klicatelja, ga spraševati in sproti vse zapisovati, pri tem pa šteje vsaka sekunda. Whisper 112 mu pri tem pomaga: klic posluša v živo in sam izpolni kartico dogodka.

## Kako deluje

1. **Govor v besedilo.** Model Whisper (large-v3-turbo) sproti pretvarja pogovor v slovensko besedilo in loči, kaj je rekel klicatelj in kaj dispečer. Vnaprej mu podamo besedišče klicev 112 (nesreče, poškodbe, kraji, orientacijske točke), zato besede iz tega področja prepozna bolje.
2. **Izluščenje podatkov.** Iz prepisa sistem izlušči šest ključnih podatkov:
   - vrsta dogodka
   - opis
   - naslov
   - orientacija (npr. "nasproti avtobusnega postajališča")
   - stanje pacienta
   - število poškodovanih
3. **Kartica dogodka.** Kartica se posodablja med pogovorom. Če klicatelj kaj popravi, se popravi tudi kartica.

## Varnost na prvem mestu

- Sistem si ne izmišljuje. Upošteva samo tisto, kar klicatelj izrecno pove, ne pa domnev iz dispečerjevih vprašanj. Na odgovor "Ne vem." polje ostane prazno.
- Pri vsakem podatku je zapisan stavek, iz katerega izhaja, zato ga dispečer lahko takoj preveri.
- Negotove podatke sistem označi z "preveri prepis". Manjkajoče podatke dispečer vnese ročno.
- Odločitve, kot so prioriteta, oprema in vozilo, ostanejo dispečerju. Sistem jih ne predlaga.

## Vloga turrija

Logika za izluščenje podatkov ni napisana ročno, ampak je opisana v specifikaciji ***plain (`kartica.plain`). Ta v navadnem jeziku pove, katera polja potrebujemo in katera pravila veljajo. Turri iz specifikacije zgradi kodo (`kartica.py`) in jo preveri s testi (`tests/kartica/`), ki temeljijo na resničnih primerih klicev. Ko so potrebe drugačne, na primer dodatno polje ali nova vrsta dogodka, spremenimo samo specifikacijo, z `regenerate` pa turri kodo zgradi in preveri znova.

## Zakaj je to uporabno

Dispečer se lahko posveti klicatelju namesto tipkanju. Podatki so hitreje na voljo ekipam na terenu, v zapisu je manj napak in vsak podatek je mogoče preveriti v prepisu.

## Zagon

- **Windows:** dvoklik na `ZAZENI.bat`. Ob prvem zagonu namesti Python 3.12 in knjižnice ter prenese model Whisper.
- **Brez zvoka (iz referenčnega besedila):** `python demo_prepis.py`
- **Drug model:** `set WHISPER_MODEL=large-v3` (natančnejši) ali `medium` (hitrejši)
- **Testi:** `python -m unittest -v`

## Datoteke

| Datoteka | Namen |
|---|---|
| `poslusalec.py` | Whisper, prepis klica v živo, prikaz kartice in ročni vnos |
| `kartica.py` | izluščenje šestih podatkov (zgradi ga turri) |
| `kartica.plain` | specifikacija ***plain za turri |
| `tests/kartica/` | sprejemni in skladnostni testi za turri |
| `config.turri.yaml` | nastavitve testov za turri |
| `zvok/`, `scenarij.txt` | simuliran klic za demo |
