"""Planer (specifikacija 8): OR-Tools VRP s kapacitetami, časovnimi okni,
prevzemom in dostavo ter prioritetami kot kaznimi za zamudo ali izpust.

Ciljna funkcija: zamude × utež_prioritete + spremembe_dodelitev × kazen_spremembe + skupni_čas.
Prejšnji plan je začetna rešitev. Vrne najboljši plan in do 2 alternativi.

Vhod je »stanje«: {enote, naloge, centri} (slovarji iz podatkovnega modela) in
matrike časov/razdalj po tipu enote (zgradi jih api/ z usmerjevalnim servisom).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from dnevnik.model import NERAZPOLOZLJIVA

UTEZ_ZAMUDE = {1: 100, 2: 40, 3: 15, 4: 5, 5: 2}            # na sekundo zamude
KAZEN_IZPUSTA = {1: 50_000_000, 2: 20_000_000, 3: 5_000_000, 4: 1_000_000, 5: 300_000}
# fiksni strošek uporabe enote (helikopter je drag in redek vir)
FIKSNI_STROSEK = {"helikopter": 2400, "motorist": 0, "vozilo": 0}
STORITEV_S = {"prevzem": 300, "dostava": 180, "prihod": 0}
OBZORJE_S = 12 * 3600


@dataclass
class Nastavitve:
    kazen_spremembe: int = 600          # ≈ 10 min skupnega časa
    casovna_omejitev_s: float = 2.2
    alternative: bool = True
    cas_alternative_s: float = 0.6
    izkljuci_tipe: tuple = ()
    heli_max_prioriteta: int = 2       # helikopter le za P1/P2


@dataclass
class Vozlisce:
    indeks: int
    lat: float
    lon: float
    naloga_id: str | None = None
    akcija: str | None = None          # prevzem | dostava | prihod (nujna vožnja)
    pacienti: int = 0
    oprema: int = 0
    okno_od: int = 0
    okno_do: int | None = None
    prioriteta: int = 5
    dovoljena_enota: str | None = None


@dataclass
class Model:
    enote: list[dict]
    vozlisca: list[Vozlisce] = field(default_factory=list)
    zacetki: list[int] = field(default_factory=list)
    konec: int = 0
    pari: list[tuple[int, int]] = field(default_factory=list)
    opombe: list[str] = field(default_factory=list)


def _tip_vozila(e: dict) -> str:
    return e["tip"]


def sestavi_model(stanje: dict, zdaj: float) -> Model:
    """Iz stanja zgradi vozlišča. Opravljeno je zaklenjeno (izpuščeno),
    tovor v vozilu mora dostaviti isto vozilo, enote začnejo na trenutni lokaciji."""
    enote = [e for e in stanje["enote"] if e["stanje"] not in NERAZPOLOZLJIVA]
    m = Model(enote=enote)

    def dodaj(**kw) -> int:
        v = Vozlisce(indeks=len(m.vozlisca), **kw)
        m.vozlisca.append(v)
        return v.indeks

    for e in enote:
        m.zacetki.append(dodaj(lat=e["lat"], lon=e["lon"]))
    m.konec = dodaj(lat=0.0, lon=0.0)  # navidezni konec (brez stroška)

    v_tovoru = {t["naloga_id"]: e["id"] for e in enote for t in e.get("tovor", [])}
    for n in stanje["naloge"]:
        if n.get("stanje") in ("opravljena", "preklicana", "prenesena"):
            continue
        rel = lambda t: None if t is None else int(max(0, t - zdaj))  # noqa: E731
        skupno = dict(naloga_id=n["id"], prioriteta=int(n["prioriteta"]))
        okno_od, okno_do = rel(n.get("okno_od")) or 0, rel(n.get("okno_do"))
        p, o = n["kolicina"].get("pacienti", 0), n["kolicina"].get("oprema", 0)
        if n["id"] in v_tovoru:
            dodaj(lat=n["dostava"]["lat"], lon=n["dostava"]["lon"], akcija="dostava", pacienti=-p, oprema=-o,
                  okno_do=okno_do, dovoljena_enota=v_tovoru[n["id"]], **skupno)
            continue
        if n["tip"] == "nujna_voznja" or not n.get("dostava"):
            dodaj(lat=n["prevzem"]["lat"], lon=n["prevzem"]["lon"], akcija="prihod", okno_od=okno_od, okno_do=okno_do, **skupno)
            continue
        a = dodaj(lat=n["prevzem"]["lat"], lon=n["prevzem"]["lon"], akcija="prevzem", pacienti=p, oprema=o,
                  okno_od=okno_od, okno_do=okno_do, **skupno)
        b = dodaj(lat=n["dostava"]["lat"], lon=n["dostava"]["lon"], akcija="dostava", pacienti=-p, oprema=-o, **skupno)
        m.pari.append((a, b))
    return m


def preusmeri_po_zmogljivosti(stanje: dict) -> list[str]:
    """Manjša zmogljivost centra: paciente nižje prioritete preusmeri v najbližji center s prostorom."""
    from usmerjanje.ponudniki import zracna_razdalja_m
    opombe = []
    centri = {c["id"]: c for c in stanje["centri"]}
    prosto = {cid: c["sprejemna_mesta"] - c["zasedena"] for cid, c in centri.items()}
    naloge = sorted([n for n in stanje["naloge"] if n.get("stanje") not in ("opravljena", "preklicana", "prenesena")
                     and n.get("dostava") and n["dostava"].get("center_id") and n["kolicina"].get("pacienti")],
                    key=lambda n: n["prioriteta"])
    for n in naloge:
        cid = n["dostava"]["center_id"]
        if prosto.get(cid, 0) > 0:
            prosto[cid] -= 1
            continue
        kandidati = sorted((c for c in centri.values() if prosto[c["id"]] > 0),
                           key=lambda c: zracna_razdalja_m((n["prevzem"]["lat"], n["prevzem"]["lon"]), (c["lat"], c["lon"])))
        if kandidati:
            c = kandidati[0]
            n["dostava"] = {"lat": c["lat"], "lon": c["lon"], "naslov": c["ime"], "center_id": c["id"]}
            prosto[c["id"]] -= 1
            opombe.append(f"{n['id']} preusmerjena v {c['ime']} (zmogljivost {centri[cid]['ime']} zapolnjena).")
        else:
            opombe.append(f"{n['id']}: ni prostega sprejemnega mesta – dostava bo zamaknjena.")
    return opombe


def _prejsnje_dodelitve(prejsnji: dict | None) -> dict[str, str]:
    if not prejsnji:
        return {}
    return {k["naloga_id"]: eid for eid, koraki in prejsnji["koraki_po_enoti"].items() for k in koraki}


def resi(model: Model, matrike: dict[str, list[list[float]]], razdalje: dict[str, list[list[float]]],
         prejsnji: dict | None, nast: Nastavitve, zdaj: float, oznaka: str = "najboljši",
         casovna_omejitev_s: float | None = None) -> dict | None:
    n_voz = len(model.enote)
    if n_voz == 0:
        return None
    N = len(model.vozlisca)
    mgr = pywrapcp.RoutingIndexManager(N, n_voz, model.zacetki, [model.konec] * n_voz)
    r = pywrapcp.RoutingModel(mgr)
    prejsnja = _prejsnje_dodelitve(prejsnji)
    vozlisca = model.vozlisca

    def cas(tip, i, j):
        if j == model.konec or i == model.konec:
            return 0
        return int(matrike[tip][i][j]) + STORITEV_S.get(vozlisca[i].akcija or "", 0)

    # časovni klici po vozilu (helikopter / motorist / vozilo imajo različne matrike)
    casovni, stroskovni = [], []
    for v, e in enumerate(model.enote):
        tip = _tip_vozila(e)
        casovni.append(r.RegisterTransitCallback(lambda a, b, tip=tip: cas(tip, mgr.IndexToNode(a), mgr.IndexToNode(b))))

        def strosek(a, b, tip=tip, eid=e["id"]):
            i, j = mgr.IndexToNode(a), mgr.IndexToNode(b)
            c = cas(tip, i, j)
            nid = vozlisca[j].naloga_id
            if nid and nid in prejsnja and prejsnja[nid] != eid and vozlisca[j].akcija != "dostava":
                c += nast.kazen_spremembe
            return c
        stroskovni.append(r.RegisterTransitCallback(strosek))
        r.SetArcCostEvaluatorOfVehicle(stroskovni[-1], v)
        r.SetFixedCostOfVehicle(FIKSNI_STROSEK.get(tip, 0), v)

    r.AddDimensionWithVehicleTransits(casovni, OBZORJE_S, OBZORJE_S, False, "cas")
    dim = r.GetDimensionOrDie("cas")
    for v, e in enumerate(model.enote):
        na_voljo = int(max(0, (e.get("na_voljo_od") or 0) - zdaj))
        dim.CumulVar(r.Start(v)).SetRange(na_voljo, OBZORJE_S)

    for kap in ("pacienti", "oprema"):
        cb = r.RegisterUnaryTransitCallback(lambda a, kap=kap: getattr(vozlisca[mgr.IndexToNode(a)], kap))
        r.AddDimensionWithVehicleCapacity(cb, 0, [int(e["kapaciteta"].get(kap, 0)) for e in model.enote], False, kap)
        d = r.GetDimensionOrDie(kap)
        for v, e in enumerate(model.enote):  # tovor v vozilu ob začetku
            zacetno = sum(int(t.get(kap, 0)) for t in e.get("tovor", []))
            d.CumulVar(r.Start(v)).SetValue(zacetno)

    for vz in vozlisca:
        if vz.naloga_id is None:
            continue
        idx = mgr.NodeToIndex(vz.indeks)
        if vz.okno_od:
            dim.CumulVar(idx).SetMin(vz.okno_od)
        if vz.okno_do is not None:
            dim.SetCumulVarSoftUpperBound(idx, vz.okno_do, UTEZ_ZAMUDE[vz.prioriteta])
        dovoljena = [v for v, e in enumerate(model.enote)
                     if (not vz.dovoljena_enota or e["id"] == vz.dovoljena_enota)
                     and e["tip"] not in nast.izkljuci_tipe
                     and not (e["tip"] == "helikopter" and vz.prioriteta > nast.heli_max_prioriteta and not vz.dovoljena_enota)]
        if len(dovoljena) < n_voz:
            r.VehicleVar(idx).SetValues([-1] + dovoljena)  # -1 = neaktivno (izpust)
        r.AddDisjunction([idx], KAZEN_IZPUSTA[vz.prioriteta])

    for a, b in model.pari:
        ia, ib = mgr.NodeToIndex(a), mgr.NodeToIndex(b)
        r.AddPickupAndDelivery(ia, ib)
        r.solver().Add(r.VehicleVar(ia) == r.VehicleVar(ib))
        r.solver().Add(dim.CumulVar(ia) <= dim.CumulVar(ib))

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromMilliseconds(int(1000 * (casovna_omejitev_s or nast.casovna_omejitev_s)))

    resitev = None
    if prejsnji:  # prejšnji plan kot začetna rešitev
        po_kljucu = {(vz.naloga_id, "prevzem" if vz.akcija == "prihod" else vz.akcija): vz.indeks for vz in vozlisca if vz.naloga_id}
        poti = []
        for e in model.enote:
            pot = []
            if e["tip"] in nast.izkljuci_tipe:
                poti.append(pot)
                continue
            for k in prejsnji["koraki_po_enoti"].get(e["id"], []):
                kl = (k["naloga_id"], k["akcija"])
                if kl in po_kljucu and mgr.NodeToIndex(po_kljucu[kl]) not in pot:
                    pot.append(mgr.NodeToIndex(po_kljucu[kl]))
            poti.append(pot)
        r.CloseModelWithParameters(p)
        zacetna = r.ReadAssignmentFromRoutes(poti, True)
        if zacetna is not None:
            resitev = r.SolveFromAssignmentWithParameters(zacetna, p)
    if resitev is None:
        resitev = r.SolveWithParameters(p)
    if resitev is None:
        return None

    koraki: dict[str, list] = {}
    pokrite = set()
    skupni_cas = skupni_m = 0.0
    zamude = {pr: 0 for pr in range(1, 6)}
    for v, e in enumerate(model.enote):
        tip = _tip_vozila(e)
        idx = r.Start(v)
        seznam = []
        prejsnje_vozl = mgr.IndexToNode(idx)
        while not r.IsEnd(idx):
            idx = resitev.Value(r.NextVar(idx))
            vozl = mgr.IndexToNode(idx)
            if vozl == model.konec:
                break
            vz = vozlisca[vozl]
            t = resitev.Min(dim.CumulVar(idx))
            skupni_m += razdalje[tip][prejsnje_vozl][vozl]
            zamuda = max(0, t - vz.okno_do) if vz.okno_do is not None else 0
            zamude[vz.prioriteta] += zamuda
            seznam.append({"naloga_id": vz.naloga_id, "akcija": "prevzem" if vz.akcija == "prihod" else vz.akcija,
                           "nujna": vz.akcija == "prihod", "eta": zdaj + t, "zamuda_s": zamuda,
                           "prioriteta": vz.prioriteta, "lat": vz.lat, "lon": vz.lon})
            pokrite.add(vz.naloga_id)
            prejsnje_vozl = vozl
        if seznam:
            skupni_cas += seznam[-1]["eta"] - zdaj
        koraki[e["id"]] = seznam
    vse = {vz.naloga_id for vz in vozlisca if vz.naloga_id}
    return {
        "oznaka": oznaka,
        "koraki_po_enoti": koraki,
        "metrike": {
            "zamude_po_prioriteti_s": {str(k): int(v) for k, v in zamude.items()},
            "skupni_cas_s": int(skupni_cas),
            "skupni_km": round(skupni_m / 1000, 1),
            "nepokrite": sorted(vse - pokrite),
            "cilj": resitev.ObjectiveValue(),
        },
        "opombe": list(model.opombe),
    }


def _dodelitve(plan: dict) -> dict[str, str]:
    return {k["naloga_id"]: eid for eid, ks in plan["koraki_po_enoti"].items() for k in ks}


def primerjaj(prej: dict | None, potem: dict) -> dict:
    """Primerjava prej/potem za vmesnik in 2–3 stavke razlage."""
    dp, dn = (_dodelitve(prej) if prej else {}), _dodelitve(potem)
    spremembe = [{"naloga_id": n, "prej": dp.get(n), "potem": dn.get(n)}
                 for n in sorted(set(dp) | set(dn)) if dp.get(n) != dn.get(n)]
    mp = (prej or {}).get("metrike") or {}
    mn = potem["metrike"]
    zp = mp.get("zamude_po_prioriteti_s", {})
    zamude = {pr: {"prej": int(zp.get(pr, 0)), "potem": int(mn["zamude_po_prioriteti_s"].get(pr, 0))}
              for pr in ("1", "2", "3", "4", "5")}
    dodatni_km = round(mn["skupni_km"] - mp.get("skupni_km", 0), 1) if prej else 0.0
    razlaga = []
    if spremembe:
        for s in spremembe:
            if not s["potem"]:
                s["opomba"] = "nepokrito" if s["naloga_id"] in mn["nepokrite"] else "prenesena"
        primeri = ", ".join(f"{s['naloga_id']} {s['prej'] or '—'} → {s['potem'] or s['opomba']}" for s in spremembe[:3])
        razlaga.append(f"Spremenjenih dodelitev: {len(spremembe)} ({primeri}{' …' if len(spremembe) > 3 else ''}).")
    else:
        razlaga.append("Dodelitve ostanejo enake.")
    kriticne = [f"P{pr} {-(-v['potem'] // 60)} min" for pr, v in zamude.items() if v["potem"] > 0 and pr in ("1", "2")]
    razlaga.append("Zamude pri nujnih nalogah: " + (", ".join(kriticne) if kriticne else "ni zamud pri P1 in P2") + ".")
    if mn["nepokrite"]:
        razlaga.append(f"Nepokrite naloge: {', '.join(mn['nepokrite'])}.")
    elif prej:
        razlaga.append(f"Dodatno prevoženih {dodatni_km:+.1f} km." if dodatni_km else "Skupna razdalja je nespremenjena.")
    razlaga.extend(potem.get("opombe", [])[:1])
    return {"spremenjene_dodelitve": spremembe, "zamude_po_prioriteti_s": zamude,
            "dodatni_km": dodatni_km, "nepokrite": mn["nepokrite"], "razlaga": " ".join(razlaga[:3])}


def planiraj(stanje: dict, matrike_fn, prejsnji: dict | None = None, nast: Nastavitve | None = None,
             zdaj: float | None = None) -> list[dict]:
    """Vrne [najboljši, alternativa…] (vsak s 'primerjava'). matrike_fn(tocke, tipi) -> (casi, razdalje)."""
    nast = nast or Nastavitve()
    zdaj = time.time() if zdaj is None else zdaj
    opombe = preusmeri_po_zmogljivosti(stanje)
    model = sestavi_model(stanje, zdaj)
    model.opombe = opombe
    tocke = [(v.lat, v.lon) for v in model.vozlisca]
    tipi = sorted({e["tip"] for e in model.enote})
    casi, razdalje = matrike_fn(tocke, tipi)
    planovi = []
    glavni = resi(model, casi, razdalje, prejsnji, nast, zdaj, "najboljši")
    if glavni is None:
        return []
    planovi.append(glavni)
    if nast.alternative:
        uporablja_heli = any(ks for e in model.enote if e["tip"] == "helikopter" for ks in [glavni["koraki_po_enoti"].get(e["id"])])
        variante = [("najmanj sprememb", Nastavitve(kazen_spremembe=nast.kazen_spremembe * 6))]
        if uporablja_heli:
            variante.append(("brez helikopterja", Nastavitve(kazen_spremembe=nast.kazen_spremembe, izkljuci_tipe=("helikopter",))))
        variante.append(("najkrajši skupni čas", Nastavitve(kazen_spremembe=0)))
        for oznaka, n2 in variante:
            alt = resi(model, casi, razdalje, prejsnji, n2, zdaj, oznaka, nast.cas_alternative_s)
            if alt and all(_dodelitve(alt) != _dodelitve(p) for p in planovi):
                planovi.append(alt)
            if len(planovi) >= 3:
                break
    for p in planovi:
        p["primerjava"] = primerjaj(prejsnji, p)
    return planovi
