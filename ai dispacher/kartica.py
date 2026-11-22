"""Razčlenjevalnik za slovenska demonstracijska klica, brez povezave v omrežje."""
import re

LABELS = {
    "vrsta_dogodka": "Vrsta dogodka", "opis": "Opis", "naslov": "Naslov",
    "orientacija": "Orientacija", "stanje_pacienta": "Stanje pacienta",
    "stevilo_poskodovanih": "Število poškodovanih",
}


def build_card(text):
    card = {key: {"naziv": label, "vrednost": None, "dokaz": "",
                  "status": "manjka"}
            for key, label in LABELS.items()}
    turns = re.split(r"\b(Klicatelj|Dispečer):\s*", text, flags=re.I)
    for role, utterance in zip(turns[1::2], turns[2::2]):
        if role.lower() != "klicatelj":
            continue
        utterance = utterance.strip()

        def record(key, value, source):
            card[key].update(vrednost=value, dokaz=source, status="razbrano")

        for sentence in re.split(r"(?<=[.!?])\s+", utterance):
            words = sentence.lower()
            if words.endswith("?") or re.search(r"\b(ne vem|morda|mogoče)\b", words):
                continue
            if re.search(r"\bprometna ne ?sreča\b", words) or re.search(r"\b(?:je trčil|sta trčila|sta se zaletela)\b", words):
                record("vrsta_dogodka", "Prometna nesreča", sentence)
                if "prometna nesreča" != words.strip(" .!"):
                    record("opis", sentence, sentence)
            address = re.search(r"\bnaslov je\s+(.+)", sentence, re.I)
            if address:
                record("naslov", re.sub(r"\bdvanajst\b", "12", address[1].rstrip("."), flags=re.I), sentence)
            elif re.search(r"\bulica\s+\d+", words):
                record("naslov", sentence, sentence)
                card["naslov"]["status"] = "preveri prepis"
            if re.search(r"\bnasproti\b|\bvhod je pri\b|\bmed mostom in križiščem\b", words):
                record("orientacija", sentence, sentence)
                card["orientacija"]["status"] = "preveri prepis"
            if re.search(r"\b(pri zavesti|nezavesten|diha|boli)\b", words):
                old = card["stanje_pacienta"]["dokaz"]
                combined = (old + " " + sentence).strip()
                record("stanje_pacienta", combined, combined)
            count = re.search(r"poškodovan\w*\s+(?:je|sta|so)\s+(ena|dve|tri|štiri|\d+)\s+os[eo]b", words)
            if count:
                number = {"ena": 1, "dve": 2, "tri": 3, "štiri": 4}.get(count[1], count[1])
                record("stevilo_poskodovanih", int(number), sentence)
    return card
