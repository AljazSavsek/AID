# Odločitve (vmesnik regijskega dispečerskega centra)

Obseg: samo pogled **regijskega dispečerskega centra** (`static/index.html`). Pogledi vozila in javnosti niso del tega dela.

| # | Odločitev | Razlog |
|---|---|---|
| 1 | Frontend v čistem HTML/JS + Leaflet, brez build koraka. Leaflet je vključen lokalno v `static/vendor/`. | Specifikacija (razd. 3); deluje tudi brez interneta (razd. 11). |
| 2 | Logotip AID je začasen SVG (`static/img/aid-logo.svg`) v barvah #1D2228 / #8A1525 / bela. | Pravi logotip ni bil priložen – zamenjajte datoteko. |
| 3 | Backend še ne obstaja; klic in izhod AI poslušalca sta skriptirana v `static/js/demo_klic.js` (shema iz razd. 5, z citati in zaupanjem). | Vrstni red izdelave (razd. 13, korak 3) – vmesnik najprej, nato API. |
| 4 | Stanje in revizijska sled se v demu hranita v `localStorage` (`aid_revizija`, `aid_naloga`). | Zadnja rezerva stanja po razd. 7; pravi vir bo SQLite. |
| 5 | »ODOBRI IN POŠLJI« zahteva, da so naslov, lokacija, prioriteta in enota potrjeni posamično; nepotrjeni AI predlogi se ne pošljejo in so našteti v potrditvenem oknu. | Pravilo 2.1 in razd. 6. |
| 6 | »UREDI PODATKE« odpre vsa polja za urejanje; nespremenjeni AI predlogi s tem **niso** potrjeni. | AI ne sme ničesar potrditi namesto dispečerja. |
| 7 | »Sprejmi vse ≥ 0,8« izpusti prioriteto, enoto, polja z več alternativami in polja z nasprotujočimi predlogi. | Razd. 6. |
| 8 | Geokodiranje potrjenega naslova ustvari nov **predlog** lokacije (ne potrjene). Če naslova ni mogoče geokodirati, dispečer klikne na karto. | Razd. 7 – zadnja rezerva geokodiranja. |
| 9 | Časi prihoda prikazujejo le čas, razdaljo in oznako `natančno` / `brez prometa` / `ocena`. Stanje sistema pokaže le »na voljo / ni na voljo«. | Pravilo 2.4 – brez imen ponudnikov. |
| 10 | Zaupanje < 0,5 je prikazano rumeno (edina barva zunaj palete logotipa). | Zahteva razd. 5. |
| 11 | Demo izpadi (AI, prepis) so v »Stanje sistema → Demo«. Vmesnik ob izpadu dela naprej z ročnim vnosom. | Razd. 7. |
| 12 | Podlaga karte: CARTO light (OSM). Brez interneta se karta ne izriše, ročni vnos koordinat še vedno deluje. | Brez API ključa v frontendu. |
