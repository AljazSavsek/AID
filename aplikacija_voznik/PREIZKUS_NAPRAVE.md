# Preizkus GPS v ozadju na pravi napravi (Android)

Cilj: potrditi, da Flet aplikacija pošilja položaj **z ugasnjenim zaslonom in v ozadju** vsaj 30 min.
Brez uspešnega preizkusa aplikacija ni primerna za uporabo (ODLOCITVE #28).

## Priprava
1. Strežnik v istem omrežju kot telefon (ali HTTPS):
   ```bash
   AID_SKRBNIK_ZETON=izberi-zeton uvicorn api.app:app --host 0.0.0.0 --port 8000
   ```
2. Gradnja APK (potreben Flutter SDK in Android SDK; `flet build` ju prenese/preveri):
   ```bash
   cd aplikacija_voznik && flet build apk
   ```
   APK je v `aplikacija_voznik/build/apk/`. Namestitev: `adb install build/apk/app-release.apk`.
3. V aplikaciji: strežnik `http://IP-RAČUNALNIKA:8000`, enota `RV-2`, koda `DEMO-RV2`.
   Dovoli lokacijo **»Vedno«**, obvestila in izjemo pri varčevanju baterije.

## Preizkus (40 min)
| Čas | Dejanje | Pričakovano |
|---|---|---|
| 0 | Prijava, potrdi prejem, **NA POTI** (način »aktivno«, fiks 2 s) | trajno obvestilo »AID – pošiljanje položaja«; dispečer: GPS · n s |
| 2 min | Ugasni zaslon, telefon v žep, hodi/vozi | dispečer: marker se premika, nikoli »ni signala« |
| 15 min | Odpri drugo aplikacijo (npr. kamero) 5 min | pošiljanje se nadaljuje |
| 22 min | Letalski način 3 min, nato izklopi | dispečer: »zakasnjeno« → »ni signala«; po vklopu se zgodovina zapolni (vrsta brez povezave) |
| 30 min | Dispečer potrdi nov plan za RV-2 (z ugasnjenim zaslonom) | vibracija + sistemsko obvestilo vsakih 10 s do potrditve |
| 40 min | Konec | analiza spodaj |

## Analiza
```bash
python aplikacija_voznik/analiza_vrzeli.py --streznik http://localhost:8000 --enota RV-2 --minut 45 --zeton izberi-zeton
```
USPEH: pokritost ≥ 98 % in nobena vrzel > 60 s **razen** namerne v letalskem načinu (ta mora biti
po vklopu zapolnjena z izvirnimi časi).

Zapišite tudi: model telefona, različico Androida, varčevanje baterije proizvajalca (Samsung/Xiaomi/Huawei
imajo dodatne nastavitve), porabo baterije v 40 min.

## Znane omejitve (iz pregleda izvorne kode, ne preizkušeno)
- Če voznik aplikacijo **pometa iz nedavnih** ali jo prisilno ustavi, se pošiljanje ustavi.
  Dispečer to vidi kot »ni signala«.
- Varčevanje baterije nekaterih proizvajalcev lahko ustavi storitev v ospredju kljub izjemi.
- iOS (Mac + Xcode + Apple Developer): `flet build ipa`; UIBackgroundModes=location je v `pyproject.toml`.
- `usesCleartextTraffic=true` je samo za demo v lokalnem omrežju; za pravo uporabo HTTPS.
