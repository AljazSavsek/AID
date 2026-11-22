"""Lokalni izluščevalnik s pravili (deterministična rezerva, deluje brez interneta).

Uporabi se, ko oblačni in lokalni LLM nista na voljo. Kot LLM vrača izhod po
shemi, vsak podatek pa ima DOBESEDEN citat iz prepisa – nič se ne domneva.
Zaupanja so namenoma zmerna (pravila ne razumejo konteksta).
"""
from __future__ import annotations

import re

ENICE = {"en": 1, "ena": 1, "dva": 2, "dve": 2, "tri": 3, "štiri": 4, "pet": 5, "šest": 6, "sedem": 7, "osem": 8, "devet": 9}
DESETICE = {"dvajset": 20, "trideset": 30, "štirideset": 40, "petdeset": 50, "šestdeset": 60,
            "sedemdeset": 70, "osemdeset": 80, "devetdeset": 90}
NAJSTICE = {"deset": 10, "enajst": 11, "dvanajst": 12, "trinajst": 13, "štirinajst": 14, "petnajst": 15,
            "šestnajst": 16, "sedemnajst": 17, "osemnajst": 18, "devetnajst": 19}
KRAJI = {"ljubljani": "Ljubljana", "mariboru": "Maribor", "celju": "Celje", "kranju": "Kranj", "kopru": "Koper",
         "novem mestu": "Novo mesto", "vrhniki": "Vrhnika", "brezovici": "Brezovica", "domžalah": "Domžale",
         "kamniku": "Kamnik", "grosupljem": "Grosuplje"}
NEGOTOVO = re.compile(r"\b(mislim|menda|nisem prepričan|nisem prepričana|morda|verjetno)\b", re.I)
ULICA = re.compile(
    r"((?:[A-ZČŠŽ][a-zčšž]+\s){0,2}(?:cesta|ulica|trg|pot|nabrežje|cesti|ulici|trgu|poti|nabrežju|Cesta|Ulica|Trg|Cesti|Ulici|Trgu)(?:\s(?:v|na|pod|ob)?\s?[A-ZČŠŽa-zčšž]+)?)"
    r"(?:,?\s*(?:mislim da|menda|številka)?\s*(\d+[a-zA-Z]?))?"
    r"(?:,?\s*v\s((?:novem mestu)|[A-ZČŠŽ][a-zčšž]+))?"
)
KRAJ_KLICA = re.compile(r"kličem iz ((?:[A-ZČŠŽ]\w+)(?:\s(?:ob\s?)?\w+)?)", re.I)
# znani kraji (imenovalnik) in vzorci, ki ujamejo tudi napake prepisa govora
ZNANI_KRAJI = [(r"^zagor\w*\s*(?:ob\s*)?sav", "Zagorje ob Savi"), (r"^ljubljan", "Ljubljana"), (r"^maribor", "Maribor"),
               (r"^celj", "Celje"), (r"^kranj", "Kranj"), (r"^trbovl", "Trbovlje"), (r"^hrastnik", "Hrastnik"),
               (r"^litij", "Litija"), (r"^domžal", "Domžale"), (r"^kamnik", "Kamnik")]


def imenovalnik_ulice(ulica: str) -> str:
    """Mestnik -> imenovalnik (»Celovški cesti« -> »Celovška cesta«, »Kongresnem trgu« -> »Kongresni trg«)."""
    b = ulica.split()
    if not b:
        return ulica
    jedro = {"cesti": "cesta", "ulici": "ulica", "poti": "pot", "trgu": "trg", "nabrežju": "nabrežje"}
    for i, w in enumerate(b):
        nov = jedro.get(w.lower())
        if nov is None:
            continue
        b[i] = nov.capitalize() if w[0].isupper() else nov
        zenski = nov in ("cesta", "ulica", "pot")
        for j in range(i):  # pridevniki pred jedrom
            if zenski and b[j].endswith("i") and len(b[j]) > 3:
                b[j] = b[j][:-1] + "a"
            elif nov in ("trg", "nabrežje") and b[j].endswith("em"):
                b[j] = b[j][:-2] + ("i" if nov == "trg" else "o")
        break
    return " ".join(b)


def _kraj(besedilo: str) -> str | None:
    b = besedilo.lower().strip()
    return next((ime for vz, ime in ZNANI_KRAJI if re.search(vz, b)), None)


POPRAVEK_HS = re.compile(r"ni\s(\d+[a-z]?),?\s(?:ampak\s|temveč\s)?je\s(\d+[a-z]?)", re.I)
TELEFON = re.compile(r"(?:\+386\s?|0)\d{1,2}[\s/]?\d{3}[\s]?\d{3}")


def stevilo_iz_besed(beseda: str) -> int | None:
    b = beseda.lower()
    if b.isdigit():
        return int(b)
    if b in ENICE:
        return ENICE[b]
    if b in NAJSTICE:
        return NAJSTICE[b]
    if b in DESETICE:
        return DESETICE[b]
    m = re.fullmatch(r"(\w+?)in(\w+)", b)
    if m and m.group(1) in ENICE and m.group(2) in DESETICE:
        return ENICE[m.group(1)] + DESETICE[m.group(2)]
    return None


def _dokaz(seg, citat):
    return {"segment_id": seg["id"], "citat": citat}


def izlusci(segmenti: list[dict], trenutni: dict[str, object] | None = None) -> dict:
    """segmenti: končni segmenti klica. trenutni: {polje: vrednost} zadnji veljavni predlogi."""
    trenutni = dict(trenutni or {})
    po = {}
    dogodki = []

    def nastavi(polje, vrednost, zaupanje, dokazi):
        po[polje] = {"polje": polje, "vrednost": vrednost, "zaupanje": zaupanje, "dokazi": dokazi}

    stanje_deli: list[tuple[str, dict]] = []
    prejsnji_dispecer = ""
    for seg in segmenti:
        t = seg["besedilo"]
        tl = t.lower()
        if seg["govorec"] == "dispecer":
            prejsnji_dispecer = tl
            # dispečerjeva vprašanja niso podatki, sporočila ekip pa so lahko dogodki
        if seg["govorec"] == "klicatelj":
            # --- vrsta dogodka in opis
            for vzorec, vrsta in ((r"padel\w*|padla|padec", "padec"),
                                  (r"prometn\w* nesreč\w*|trčil\w*|zaletel\w*", "prometna_nesreca"),
                                  (r"bolečin\w* v prsih|boli\w* v prsih", "bolecina_v_prsih"),
                                  (r"nezavest\w*|onesvešč\w*", "nezavest"),
                                  (r"porod\w*|popadk\w*|rodi\b", "porod")):
                m = re.search(vzorec + r"(?:\s(?:z|s|na|po)\s\w+)?", t, re.I)
                if m and "vrsta_dogodka" not in po:
                    nastavi("vrsta_dogodka", vrsta, 0.8, [_dokaz(seg, m.group(0))])
                    stavek = re.split(r"(?<=[.!?])\s", t)
                    st = next(s for s in stavek if m.group(0) in s).strip().rstrip("!.?")
                    st = re.sub(r"^(dob\w*\s+dan|halo|prosim)[, ]*", "", st, flags=re.I)  # tudi »Dobr dan« iz prepisa
                    deli = [d.strip() for d in st.split(" in ")]
                    citat = deli[0] if m.group(0) in deli[0] else st
                    nastavi("opis", citat[0].upper() + citat[1:], 0.7, [_dokaz(seg, citat)])
            # --- starost
            m = re.search(r"\b(\w+)\b(?=\s*(?:let\b|\.|,|$))", t)
            if "star" in prejsnji_dispecer and m and (n := stevilo_iz_besed(m.group(1))) and 0 < n < 120:
                osnova = str(trenutni.get("opis") or po.get("opis", {}).get("vrednost") or "").strip()
                osnova = re.sub(r",?\s*\d+ let", "", osnova)
                dokazi = [_dokaz(seg, m.group(1))] + (po["opis"]["dokazi"] if "opis" in po else [])
                nastavi("opis", f"{osnova}, {n} let" if osnova else f"{n} let", 0.8, dokazi)
            # --- naslov
            m = ULICA.search(t)
            if m and m.group(2):
                ulica, hs, kraj = imenovalnik_ulice(m.group(1).strip()), m.group(2), m.group(3)
                kraj_ime = KRAJI.get((kraj or "").lower())
                vrednost = f"{ulica} {hs}" + (f", {kraj_ime}" if kraj_ime else "")
                z = 0.6 if NEGOTOVO.search(m.group(0)) else 0.8
                if not kraj_ime:
                    z -= 0.2
                nastavi("naslov", vrednost, round(z, 2), [_dokaz(seg, m.group(0).strip().rstrip(","))])
            # kraj brez ulice (»kličem iz Zagorja ob Savi«) – tudi z napakami prepisa govora; nizko zaupanje
            m = KRAJ_KLICA.search(t)
            if m and "naslov" not in po and not trenutni.get("naslov"):
                kraj = _kraj(m.group(1))
                if kraj:
                    nastavi("naslov", kraj, 0.45, [_dokaz(seg, m.group(0))])
            m = POPRAVEK_HS.search(t)
            prejsnji = (po.get("naslov") or {}).get("vrednost") or trenutni.get("naslov")
            if m and prejsnji and m.group(1) in str(prejsnji):
                nastavi("naslov", str(prejsnji).replace(m.group(1), m.group(2), 1), 0.75, [_dokaz(seg, m.group(0))])
            # --- orientacija
            m = re.search(r"((?:rumen|bel|rdeč|zelen|modr|siv)\w*\s(?:hiš\w*|blok\w*|stavb\w*)\s)?"
                          r"(?:za|pri|nasproti|ob|pred)\s(?:\w+\s){0,2}(?:črpalk\w*|trgovin\w*|cerkv\w*|šol\w*|gostiln\w*|pošt\w*"
                          r"|most\w*|železnišk\w*\s\w*postaj\w*|križišč\w*)", t, re.I)
            if m:
                nastavi("orientacija", m.group(0)[0].lower() + m.group(0)[1:], 0.85, [_dokaz(seg, m.group(0))])
            # --- stanje pacienta
            for vzorec in (r"se ne odziva", r"ne diha", r"(?<!ne )diha(?:,\s*ampak\s(?:zelo\s)?težko)?",
                           r"je nezavesten|je nezavestna", r"(?:iz \w+ mu |mu )?teče kri|krvavi\w*", r"bruha\w*", r"je pri zavesti"):
                for mm in re.finditer(vzorec, t, re.I):
                    stanje_deli.append((mm.group(0), seg))
            # --- število poškodovanih
            m = re.search(r"samo (?:oče|on|ona|mama|mož|žena|sin|hči|ena oseba)", t, re.I)
            if m:
                nastavi("stevilo_poskodovanih", 1, 0.85, [_dokaz(seg, m.group(0))])
            m = re.search(r"(\w+)\s(?:sta|so)\s(?:poškodovan\w*|ranjen\w*)", t, re.I)
            if m and (n := stevilo_iz_besed(m.group(1))):
                nastavi("stevilo_poskodovanih", n, 0.8, [_dokaz(seg, m.group(0))])
            # število ljudi na kraju (ne nujno poškodovanih) – nizko zaupanje
            for mm in re.finditer(r"\b(\w+)\s(?:ljudi|osebe|osebi|osob\w*)\b", t, re.I):
                n = stevilo_iz_besed(mm.group(1).lower())
                if n and n < 50:
                    nastavi("stevilo_poskodovanih", n, 0.45, [_dokaz(seg, mm.group(0))])
            # --- kontakt
            m = TELEFON.search(t)
            if m:
                nastavi("kontakt", m.group(0).strip(), 0.9, [_dokaz(seg, m.group(0).strip())])
        # --- dogodki plana (iz klica ali sporočila)
        m = re.search(r"(obstal\w*|pokvaril\w*|okvar\w*|se ne vžge)", t, re.I)
        if m:
            dokazi = [_dokaz(seg, m.group(0))]
            enota = re.search(r"\b(RV|MOT|HELI)[\s-]?(\d)\b", t, re.I)
            tovor = re.search(r"(oprema|pacient|tovor) je še (?:v njem|v vozilu|notri)", t, re.I)
            if tovor:
                dokazi.append(_dokaz(seg, tovor.group(0)))
            dogodki.append({"tip": "okvara_vozila", "enota_id": f"{enota.group(1).upper()}-{enota.group(2)}" if enota else None,
                            "tovor_ostane": True if tovor else None, "zamik_min": None, "center_id": None,
                            "nova_kapaciteta": None, "naloga_id": None, "nova_prioriteta": None,
                            "zaupanje": 0.75, "dokazi": dokazi})
        m = re.search(r"megl\w*", t, re.I)
        if m:
            dogodki.append({"tip": "megla", "enota_id": "HELI-1" if re.search(r"helikopter", t, re.I) else None,
                            "tovor_ostane": None, "zamik_min": 60, "center_id": None, "nova_kapaciteta": None,
                            "naloga_id": None, "nova_prioriteta": None, "zaupanje": 0.7, "dokazi": [_dokaz(seg, m.group(0))]})

    if stanje_deli:
        # zadnja omemba dihanja velja; ostali opisi se ohranijo
        besede, dokazi, videno = [], [], set()
        for citat, seg in reversed(stanje_deli):
            kljuc = "dihanje" if "diha" in citat.lower() else citat.lower()
            if kljuc in videno:
                continue
            videno.add(kljuc)
            besede.append(citat.lower())
            dokazi.append(_dokaz(seg, citat))
        nastavi("stanje_pacienta", ", ".join(reversed(besede)), 0.85, list(reversed(dokazi)))
        ogrozen = [d for d in dokazi if re.search(r"ne odziva|ne diha|nezavest|težko", d["citat"], re.I)]
        if ogrozen:
            nastavi("prioriteta", 1, 0.75, ogrozen)
        elif any(re.search(r"kri|krvavi", d["citat"], re.I) for d in dokazi):
            nastavi("prioriteta", 2, 0.6, [d for d in dokazi if re.search(r"kri", d["citat"], re.I)])

    vprasanja = []
    vse = " ".join(s["besedilo"].lower() for s in segmenti)
    if "naslov" not in po and not trenutni.get("naslov"):
        vprasanja.append("Kje točno ste – ulica, hišna številka, kraj?")
    if "diha" not in vse and po.get("vrsta_dogodka"):
        vprasanja.append("Ali pacient diha?")
    if po.get("vrsta_dogodka") and not re.search(r"odziva|zavest", vse):
        vprasanja.append("Ali je pacient pri zavesti?")
    if "stevilo_poskodovanih" not in po and not trenutni.get("stevilo_poskodovanih") and po.get("vrsta_dogodka"):
        vprasanja.append("Ali je še kdo poškodovan?")
    if "kontakt" not in po and not trenutni.get("kontakt") and len(segmenti) > 6:
        vprasanja.append("Na kateri številki ste dosegljivi?")
    for d in dogodki:
        if d["tip"] == "okvara_vozila" and not d["enota_id"]:
            vprasanja.append("Katero vozilo?")

    return {"posodobitve": list(po.values()), "vprasanja_za_klicatelja": vprasanja[:3], "zaznani_dogodki": dogodki}
