from dnevnik.shramba import Shramba


def test_wal_in_izvoz(tmp_path):
    s = Shramba(tmp_path / "aid.sqlite")
    assert s._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.shrani("enota", {"id": "RV-1", "stanje": "prosto"})
    s.revizija("M. Novak", "Enota", "RV-1", "spremeni", "prosto", "okvara")
    p = s.izvozi_json(tmp_path / "izvoz.json")
    assert "RV-1" in p.read_text(encoding="utf-8")
    r = s.revizije("RV-1")[0]
    assert r["uporabnik"] == "M. Novak" and r["stara_vrednost"] == "prosto" and r["nova_vrednost"] == "okvara" and r["cas"] > 0
