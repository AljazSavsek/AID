import json
import tempfile
import unittest
from pathlib import Path
from kartica import build_card, LABELS
from poslusalec import poslusaj


class KarticaTest(unittest.IsolatedAsyncioTestCase):
    def test_dispecer(self):
        card = build_card("Dispečer: Sta trčila dva avtomobila? Klicatelj: Ne vem.")
        self.assertTrue(all(card[k]["vrednost"] is None for k in LABELS))

    async def test_poslusaj(self):
        class UI:
            async def send_str(self, payload):
                self.last = json.loads(payload)

        async def dogodki():
            yield 1, "Klicatelj: Ne vem."
            yield 1, "Klicatelj: Med mostom in križiščem sta trčila dva avtomobila."

        ui = UI()
        with tempfile.TemporaryDirectory() as folder:
            path = await poslusaj(dogodki(), ui, Path(folder))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), ui.last)
        card = ui.last["kartica"]
        self.assertEqual(card["vrsta_dogodka"]["vrednost"], "Prometna nesreča")
        self.assertIn("med mostom", card["orientacija"]["vrednost"].lower())
        self.assertNotIn("Ne vem", ui.last["text"])
