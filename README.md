# ADA-Hack

## Regijski dispečerski center – vmesnik (prototip)

```bash
python3 -m http.server 8000 -d static
```

Odprite <http://localhost:8000/> → **SPREJMI KLIC** zažene demo klic (padec z lestve, Celovška cesta).

- Levo: čakajoči klici, telefon, stanje sistema (demo izpadi).
- Sredina: AI transkripcija v živo; označeni citati podpirajo predloge.
- Desno: predlagani dispečerski obrazec. Tipke na kartici: `Enter` sprejmi, `E` uredi, `Del` zavrni, `↑/↓` premik.
- **UREDI PODATKE** / **ODOBRI IN POŠLJI** – k ekipi gredo le potrjeni podatki.

Odločitve: [ODLOCITVE.md](ODLOCITVE.md).
