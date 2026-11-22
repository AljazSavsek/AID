import unittest
from pathlib import Path
from kartica import build_card, LABELS

POLJA = {"vrsta_dogodka", "opis", "naslov", "orientacija", "stanje_pacienta", "stevilo_poskodovanih"}


class Scenarij(unittest.TestCase):
    def test_cel_klic(self):
        card = build_card(Path(__file__).with_name("scenarij.txt").read_text(encoding="utf-8"))
        self.assertEqual(set(card), POLJA)
        for key in POLJA:
            self.assertIn(card[key]["status"], ("razbrano", "preveri prepis"), key)
            self.assertTrue(card[key]["dokaz"])
        self.assertEqual(card["stevilo_poskodovanih"]["vrednost"], 1)
        self.assertEqual(card["naslov"]["vrednost"], "Testna ulica 12, Zagorje ob Savi")

    def test_brez_domnev(self):
        card = build_card("Dispečer: Poškodovani sta dve osebi? Klicatelj: Ne vem.")
        for key in LABELS:
            self.assertIsNone(card[key]["vrednost"])
