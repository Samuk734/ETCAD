"""PDF -> DWG apribile da SPAC, identico al PDF e modificabile.

Cosa produce per ogni pagina:
- geometria pulita: LINE / LWPOLYLINE (con archi come bulge) / CIRCLE /
  ARC / SPLINE; i tratteggi del plotter tornano linee con tipo di linea;
  i pieni triangolati tornano HATCH solidi (colore vero se non nero);
- testi veri (TEXT) ricostruiti dai tratti SHX (vedi pdf_testi) con stile
  ROMANS (romans.shx) / ISOCP (isocp.shx) / ITALICC, altezza, rotazione,
  inclinazione e spessore penna; i testi veri del PDF con font TrueType;
- cornice: gli elementi identici su tutte le pagine diventano il blocco
  CORNICE; i testi del cartiglio che cambiano da pagina a pagina (oggetto,
  foglio, segue foglio...) diventano attributi.

Uscita: un DWG per pagina + un DWG con tutti i fogli affiancati, via SPAC
(accoreconsole) o ODA File Converter, e un rapporto JSON per la revisione
AI (testi con id, confidenza, fonte). Le correzioni dell'AI si passano come
`correzioni` / `testi_aggiunti` / `etichette_glifi` e si riconverte.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field

from . import pdf_testi as pt
from . import pdf_vettori as pv

LAYER_GEO = "GEOMETRIA"
LAYER_TESTI = "TESTI"
LAYER_PIENI = "RIEMPIMENTI"
LAYER_CORNICE = "CORNICE"
LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90,
               100, 106, 120, 140, 158, 200, 211]
STILI_SHX = {"ROMANS": "romans.shx", "ISOCP": "isocp.shx", "ITALICC": "italicc.shx"}
TTF = [  # (regex sul nome font PDF, stile, file ttf)
    (r"swiss.*bold.*outline|swiss.*bdoul", "SWISS_BDOUL", "swissbo.ttf"),
    (r"swiss.*bold", "SWISS_BOLD", "swissb.ttf"),
    (r"swiss", "SWISS", "swiss.ttf"),
    (r"arial.*bold", "ARIAL_BOLD", "arialbd.ttf"),
    (r"isocpeur|iso.*cp", "ISOCPEUR", "isocpeur.ttf"),
    (r"times", "TIMES", "times.ttf"),
    (r"courier", "COURIER", "cour.ttf"),
    (r".*", "ARIAL", "arial.ttf"),
]


CODICI_SHX = {"°": "%%d", "±": "%%p", "Ø": "%%c", "⌀": "%%c"}


def testo_cad(testo: str, stile: str) -> str:
    """Simboli speciali con i codici AutoCAD (%%d gradi, %%p piu'/meno, %%c
    diametro): i font SHX li disegnano sempre, il carattere Unicode no."""
    if stile in STILI_SHX:
        for k, v in CODICI_SHX.items():
            testo = testo.replace(k, v)
    return testo


def lw_da_mm(mm: float) -> int:
    v = mm * 100
    return min(LINEWEIGHTS, key=lambda s: abs(s - v))


@dataclass
class Ent:
    """Entita' intermedia (mm, Y in alto) prima della scrittura DXF."""
    tipo: str
    dati: dict
    layer: str = LAYER_GEO
    lw: int = 25
    rgb: tuple | None = None
    linetype: str = ""
    firma: tuple = ()

    def calcola_firma(self) -> tuple:
        r = lambda v: round(v, 1)  # noqa: E731
        d = self.dati
        if self.tipo == "LINE":
            a, b = sorted([(r(d["p0"][0]), r(d["p0"][1])), (r(d["p1"][0]), r(d["p1"][1]))])
            f = ("L", a, b)
        elif self.tipo == "LWPOLYLINE":
            pts = tuple((r(x), r(y), round(bu, 2)) for x, y, bu in d["pts"])
            f = ("P", min(pts, pts[::-1]), d["chiusa"])
        elif self.tipo == "CIRCLE":
            f = ("C", r(d["c"][0]), r(d["c"][1]), round(d["r"], 2))
        elif self.tipo == "ARC":
            f = ("A", r(d["c"][0]), r(d["c"][1]), round(d["r"], 2),
                 round(d["a0"]), round(d["a1"]))
        elif self.tipo == "SPLINE":
            f = ("S", tuple((r(x), r(y)) for x, y in d["ctrl"]))
        elif self.tipo == "HATCH":
            xs = [p[0] for p in d["anelli"][0]]
            ys = [p[1] for p in d["anelli"][0]]
            f = ("H", r(min(xs)), r(min(ys)), r(max(xs)), r(max(ys)),
                 sum(len(a) for a in d["anelli"]))
        elif self.tipo == "TEXT":
            if d.get("firma_tratti"):
                # stessi tratti nel PDF = stesso testo, anche se su una pagina
                # l'OCR l'ha letto diverso (il contenuto si decide a maggioranza)
                f = ("T", d["firma_tratti"])
            else:
                f = ("T", d["testo"], r(d["x"]), r(d["y"]), round(d["h"], 2),
                     round(d["rot"]), d["stile"])
        else:
            f = (self.tipo, id(self))
        self.firma = (f, self.lw, self.rgb, self.linetype)
        return self.firma


# ---------------------------------------------------------------------------
# geometria
# ---------------------------------------------------------------------------

def _angolo_firmato(c, a, b) -> float:
    a1 = math.atan2(a[1] - c[1], a[0] - c[0])
    a2 = math.atan2(b[1] - c[1], b[0] - c[0])
    d = a2 - a1
    while d <= -math.pi:
        d += 2 * math.pi
    while d > math.pi:
        d -= 2 * math.pi
    return d


def _bulge_run(run: list):
    """Sequenza di Bezier -> bulge se e' un arco di cerchio, altrimenti None."""
    arc = pv.arco_da_bezier(run)
    if not arc:
        return None
    c = arc[0]
    p0, p1 = run[0][1], run[-1][4]
    if math.dist(p0, p1) < 1e-4:
        return None  # cerchio intero: gestito a parte
    theta = 0.0
    for cv in run:  # somma delle spazzate firmate dei singoli tratti
        m = pv.bezier_pt(cv, 0.5)
        theta += _angolo_firmato(c, cv[1], m) + _angolo_firmato(c, m, cv[4])
    return math.tan(theta / 4)


def _ent_da_catena(catena: list, lw: int, rgb, layer: str) -> list[Ent]:
    """Catena contigua di items -> LWPOLYLINE con bulge, LINE o SPLINE."""
    out: list[Ent] = []
    pts: list[list] = []  # [x, y, bulge]

    def chiudi_poly():
        nonlocal pts
        if len(pts) >= 2:
            chiusa = math.dist(pts[0][:2], pts[-1][:2]) < 1e-3 and len(pts) > 2
            if chiusa:
                pts = pts[:-1]
            if len(pts) == 2 and not chiusa and abs(pts[0][2]) < 1e-9:
                out.append(Ent("LINE", {"p0": tuple(pts[0][:2]), "p1": tuple(pts[1][:2])},
                               layer, lw, rgb))
            else:
                out.append(Ent("LWPOLYLINE", {"pts": [tuple(p) for p in pts],
                                              "chiusa": chiusa}, layer, lw, rgb))
        pts = []

    i = 0
    while i < len(catena):
        it = catena[i]
        if it[0] == "l":
            if not pts:
                pts = [[it[1][0], it[1][1], 0.0]]
            pts.append([it[2][0], it[2][1], 0.0])
            i += 1
            continue
        j = i
        while j < len(catena) and catena[j][0] == "c":
            j += 1
        run = catena[i:j]
        bu = _bulge_run(run)
        if bu is not None:
            if not pts:
                pts = [[run[0][1][0], run[0][1][1], 0.0]]
            pts[-1][2] = bu
            pts.append([run[-1][4][0], run[-1][4][1], 0.0])
        else:
            arc = pv.arco_da_bezier(run)
            chiudi_poly()
            if arc and arc[3] - arc[2] == 360.0:
                out.append(Ent("CIRCLE", {"c": arc[0], "r": arc[1]}, layer, lw, rgb))
            else:
                ctrl = [run[0][1]]
                for cv in run:
                    ctrl += [cv[2], cv[3], cv[4]]
                out.append(Ent("SPLINE", {"ctrl": ctrl, "n": len(run)}, layer, lw, rgb))
        i = j
    chiudi_poly()
    return out


def geometria(pag: pv.PaginaVettoriale, usati: set) -> list[Ent]:
    """Tratti e pieni non usati dai testi -> entita' CAD."""
    ents: list[Ent] = []
    pieni = [t for i, t in enumerate(pag.tratti)
             if i not in usati and t.tipo in ("f", "fs")]
    for i, t in enumerate(pag.tratti):
        if i in usati or t.tipo == "f":
            continue
        if t.tipo == "fs" and t.larghezza <= 0.001:
            continue  # solo pieno: va in HATCH
        lw = lw_da_mm(t.larghezza)
        rgb = t.colore if t.colore not in (None, (0, 0, 0)) else None
        cer = pv.cerchio_da_tratto(t) if t.ha_curve else None
        if cer:
            ents.append(Ent("CIRCLE", {"c": cer[0], "r": cer[1]}, LAYER_GEO, lw, rgb))
            continue
        for cat in pv.catene(t.items):
            ents += _ent_da_catena(cat, lw, rgb, LAYER_GEO)
    if pieni:
        try:
            for rgb, poly in pv.unisci_triangoli(pieni):
                anelli = [list(poly.exterior.coords)[:-1]]
                anelli += [list(r.coords)[:-1] for r in poly.interiors]
                if len(anelli[0]) < 3:
                    continue
                ents.append(Ent("HATCH", {"anelli": anelli}, LAYER_PIENI, 0,
                                rgb if rgb != (0, 0, 0) else None))
        except ImportError:  # shapely assente: un HATCH per path
            for t in pieni:
                for pts in pv.appiattisci(t.items):
                    if len(pts) >= 3:
                        ents.append(Ent("HATCH", {"anelli": [pts]}, LAYER_PIENI, 0,
                                        t.riempimento if t.riempimento != (0, 0, 0) else None))
    return ents


def deduplica(ents: list[Ent]) -> list[Ent]:
    visti, out = set(), []
    for e in ents:
        f = e.calcola_firma()
        if f in visti:
            continue
        visti.add(f)
        out.append(e)
    return out


def unisci_tratteggi(ents: list[Ent], doc_lt: dict) -> list[Ent]:
    """Segmenti collineari a passo regolare -> una LINE con tipo di linea.

    doc_lt raccoglie i tipi di linea da creare: nome -> [dash, -gap, ...].
    """
    lines = [e for e in ents if e.tipo == "LINE"]
    altri = [e for e in ents if e.tipo != "LINE"]
    secchi: dict = {}
    for e in lines:
        (x0, y0), (x1, y1) = e.dati["p0"], e.dati["p1"]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6 or L > 15:
            altri.append(e)
            continue
        ang = math.atan2(y1 - y0, x1 - x0) % math.pi
        ux, uy = math.cos(ang), math.sin(ang)
        off = -x0 * uy + y0 * ux
        t0, t1 = sorted((x0 * ux + y0 * uy, x1 * ux + y1 * uy))
        k = (round(math.degrees(ang), 1) % 180, round(off, 2), e.lw, e.rgb)
        secchi.setdefault(k, []).append((t0, t1, e, ux, uy, off))
    for k, segs in secchi.items():
        segs.sort(key=lambda s: s[0])
        i = 0
        while i < len(segs):
            j = i
            while j + 1 < len(segs) and 0.05 < segs[j + 1][0] - segs[j][1] < 6:
                j += 1
            run = segs[i:j + 1]
            ok = False
            if len(run) >= 4:
                dash = [s[1] - s[0] for s in run]
                gap = [b[0] - a[1] for a, b in zip(run, run[1:])]
                for periodo in (1, 2):
                    pat = []
                    buono = True
                    for f in range(periodo):
                        dd = sorted(dash[f::periodo][:-1] or dash[f::periodo])
                        gg = sorted(gap[f::periodo])
                        if not dd or not gg:
                            buono = False
                            break
                        md, mg = dd[len(dd) // 2], gg[len(gg) // 2]
                        if any(abs(v - md) > 0.12 * md + 0.03 for v in dd) or \
                                any(abs(v - mg) > 0.15 * mg + 0.03 for v in gg):
                            buono = False
                            break
                        pat += [round(md, 2), -round(mg, 2)]
                    if buono:
                        ok = True
                        break
            if ok:
                nome = "TRATT_" + "_".join(f"{abs(v):.2f}".replace(".", "p") for v in pat)
                doc_lt[nome] = pat
                _, _, e0, ux, uy, off = run[0]
                ta, tb = run[0][0], run[-1][1]
                p0 = (ta * ux - off * uy, ta * uy + off * ux)
                p1 = (tb * ux - off * uy, tb * uy + off * ux)
                altri.append(Ent("LINE", {"p0": p0, "p1": p1}, e0.layer, e0.lw,
                                 e0.rgb, linetype=nome))
                i = j + 1
            else:
                altri.append(segs[i][2])
                i += 1
    return altri


# ---------------------------------------------------------------------------
# testi
# ---------------------------------------------------------------------------

def _stile_gruppo(g: pt.GruppoTesto) -> str:
    if g.obliquo and g.font == "ROMANS":
        tratti_per_car = len(g.idx) / max(1, len(g.testo.replace(" ", "")))
        if tratti_per_car >= 2.2:
            return "ITALICC"  # corsivo con grazie (romanc/italicc)
    return g.font


def testi_da_gruppi(gruppi: list[pt.GruppoTesto]) -> list[Ent]:
    out = []
    for g in gruppi:
        if not g.accettato:
            continue
        stile = _stile_gruppo(g)
        obl = g.obliquo if stile != "ITALICC" else 0.0
        firma_tratti = (tuple(round(v * 20) / 20 for v in g.bbox), len(g.idx),
                        round(g.penna, 2))
        out.append(Ent("TEXT", {
            "testo": g.testo, "x": g.x_ins, "y": g.y_ins, "h": g.altezza,
            "rot": g.rot % 360, "stile": stile, "obl": obl, "wf": 1.0,
            "id": g.id, "conf": g.conf, "fonte": g.fonte, "bbox": g.bbox,
            "firma_tratti": firma_tratti},
            LAYER_TESTI, lw_da_mm(g.penna),
            g.colore if g.colore not in (None, (0, 0, 0)) else None))
    return out


def _stile_ttf(font: str) -> tuple[str, str]:
    f = font.lower()
    for rx, stile, ttf in TTF:
        if re.search(rx, f):
            return stile, ttf
    return "ARIAL", "arial.ttf"


def testi_pdf(pag: pv.PaginaVettoriale, stili_ttf: dict) -> list[Ent]:
    out = []
    for n, t in enumerate(pag.testi):
        stile, ttf = _stile_ttf(t.font)
        stili_ttf[stile] = ttf
        out.append(Ent("TEXT", {
            "testo": t.testo, "x": t.x, "y": t.y, "h": t.altezza,
            "rot": t.rotazione % 360, "stile": stile,
            "obl": 12.0 if t.corsivo else 0.0, "wf": t.larghezza,
            "id": f"p{pag.numero}-pdf{n}", "conf": 1.0, "fonte": "pdf",
            "bbox": t.bbox},
            LAYER_TESTI, 25, t.colore if t.colore != (0, 0, 0) else None))
    return out


# ---------------------------------------------------------------------------
# cornice (blocco comune a tutte le pagine)
# ---------------------------------------------------------------------------

@dataclass
class Cornice:
    nome: str
    entita: list = field(default_factory=list)
    attributi: list = field(default_factory=list)   # [{"tag", ...dati TEXT}]
    valori: list = field(default_factory=list)      # per pagina: {tag: testo}


def _tag(testo: str, usati: set) -> str:
    t = re.sub(r"[^A-Z0-9]+", "_", testo.upper()).strip("_")[:24] or "ATTR"
    base, n = t, 2
    while t in usati:
        t = f"{base}_{n}"
        n += 1
    usati.add(t)
    return t


def _etichetta_vicina(slot: dict, fissi: list[Ent]) -> str:
    """Etichetta fissa della cornice a sinistra o sopra lo slot (preferendo
    quelle in maiuscolo, es. FOGLIO rispetto a Sheet)."""
    def centro(d):
        b = d.get("bbox")
        return (b[0] + b[2]) / 2 if b else d["x"]

    best, bd = "", 1e9
    cs = centro(slot)
    for e in fissi:
        d = e.dati
        t = d["testo"].strip()
        if len(t) < 3 or t.replace(".", "").replace(" ", "").isdigit():
            continue  # numeri di colonna e sigle non sono etichette
        dx, dy = slot["x"] - d["x"], d["y"] - slot["y"]
        dxc = cs - centro(d)
        if 0 < dx < 70 and abs(dy) < 3.5:     # etichetta a sinistra
            dist = dx + abs(dy)
        elif abs(dxc) < 25 and 0 < dy < 12:
            dist = dy * 2 + abs(dxc)
        elif abs(dxc) < 14 and -8 < dy < 0:   # etichetta sotto (riga revisioni)
            dist = -dy * 2 + abs(dxc) + 4
        else:
            continue
        if t.upper() == t:
            dist *= 0.5
        if dist < bd:
            best, bd = t, dist
    return best


def _vettore(e: Ent):
    """(chiave discreta, vettore numerico) per il confronto con tolleranza."""
    d = e.dati
    if e.tipo == "LINE":
        a, b = sorted([tuple(d["p0"]), tuple(d["p1"])])
        return ("L", e.lw, e.linetype), (*a, *b)
    if e.tipo == "LWPOLYLINE":
        p = d["pts"]
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        return ("P", e.lw, len(p), d["chiusa"]), (min(xs), min(ys), max(xs), max(ys),
                                                   *sorted([p[0][:2], p[-1][:2]])[0])
    if e.tipo == "CIRCLE":
        return ("C", e.lw), (*d["c"], d["r"])
    if e.tipo == "ARC":
        return ("A", e.lw), (*d["c"], d["r"], d["a0"] / 50, d["a1"] / 50)
    if e.tipo == "SPLINE":
        c = d["ctrl"]
        return ("S", e.lw, len(c)), (*c[0], *c[-1])
    if e.tipo == "HATCH":
        a = d["anelli"][0]
        xs, ys = [q[0] for q in a], [q[1] for q in a]
        return ("H", e.rgb, len(d["anelli"])), (min(xs), min(ys), max(xs), max(ys))
    if e.tipo == "TEXT":
        b = d.get("bbox") or (d["x"], d["y"], d["x"], d["y"])
        return ("T", d["stile"], round(d["rot"])), tuple(b)
    return (e.tipo, id(e)), ()


def elementi_comuni(pagine: list[list[Ent]], tol: float = 0.12) -> list[set]:
    """Per ogni pagina, gli indici delle entita' presenti (entro tol mm) su
    TUTTE le pagine. Tolleranza: il plotter posiziona ogni foglio con
    piccole differenze d'origine, i confronti esatti fallirebbero."""
    indici = []
    for ents in pagine:
        idx: dict = {}
        for k, e in enumerate(ents):
            chiave, v = _vettore(e)
            if not v:
                continue
            cx, cy = (v[0] + v[2]) / 2 if len(v) >= 4 else v[0], \
                (v[1] + v[3]) / 2 if len(v) >= 4 else v[1]
            idx.setdefault((chiave, int(cx // 2), int(cy // 2)), []).append((k, v))
        indici.append(idx)

    def trova(p, chiave, v):
        cx = (v[0] + v[2]) / 2 if len(v) >= 4 else v[0]
        cy = (v[1] + v[3]) / 2 if len(v) >= 4 else v[1]
        gx, gy = int(cx // 2), int(cy // 2)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k, w in indici[p].get((chiave, gx + dx, gy + dy), []):
                    if len(w) == len(v) and max(abs(a - b) for a, b in zip(v, w)) <= tol:
                        return k
        return None

    comuni = [set() for _ in pagine]
    for k0, e in enumerate(pagine[0]):
        chiave, v = _vettore(e)
        if not v:
            continue
        match = [k0]
        for p in range(1, len(pagine)):
            k = trova(p, chiave, v)
            if k is None:
                break
            match.append(k)
        if len(match) == len(pagine):
            for p, k in enumerate(match):
                comuni[p].add(k)
    return comuni


def rileva_cornice(pagine: list[list[Ent]], nome: str = "CORNICE",
                   attributi_extra: list[str] | None = None,
                   quota_attr: float = 0.8) -> Cornice | None:
    """Elementi presenti identici in tutte le pagine -> blocco cornice.

    Testi in una posizione comune ad almeno quota_attr delle pagine ma con
    contenuto variabile -> attributi. attributi_extra: testi fissi da
    rendere comunque attributi (es. commessa, data, disegnatore).
    """
    from collections import Counter
    n = len(pagine)
    if n < 2:
        return None
    comuni = elementi_comuni(pagine)
    if len(comuni[0]) < 20:
        return None
    extra = set(attributi_extra or [])
    cor = Cornice(nome)
    # gruppi di entita' corrispondenti tra le pagine (per i testi: contenuto a
    # maggioranza, cosi' una lettura sbagliata su una pagina non conta)
    for k0 in sorted(comuni[0]):
        e0 = pagine[0][k0]
        if e0.tipo == "TEXT":
            chiave, v = _vettore(e0)
            simili = [e for p in range(n) for k, e in enumerate(pagine[p])
                      if k in comuni[p] and e.tipo == "TEXT"
                      and max(abs(a - b) for a, b in zip(_vettore(e)[1], v)) <= 0.12
                      and _vettore(e)[0] == chiave]
            testo = Counter(s.dati["testo"] for s in simili).most_common(1)[0][0]
            if testo in extra:
                continue  # testo fisso da rendere attributo
            e0 = max((s for s in simili if s.dati["testo"] == testo),
                     key=lambda s: s.dati.get("conf", 0))
            for s in simili:
                s.dati["testo"] = testo
        cor.entita.append(e0)
    for p in range(n):
        for k in comuni[p]:
            e = pagine[p][k]
            if not (e.tipo == "TEXT" and e.dati["testo"] in extra):
                e.dati["_cornice"] = True
    fissi = [e for e in cor.entita if e.tipo == "TEXT"]
    # slot attributi: testi vicini con stesso stile/altezza, contenuto diverso
    slot: list[dict] = []   # {"rif": dati, "per_pag": {p: Ent}}
    for p, ents in enumerate(pagine):
        for e in ents:
            if e.tipo != "TEXT" or e.dati.get("_cornice"):
                continue
            d = e.dati
            cx = (d["bbox"][0] + d["bbox"][2]) / 2 if d.get("bbox") else d["x"]
            for s in slot:
                r = s["rif"]
                if p in s["per_pag"] or r["stile"] != d["stile"] \
                        or abs(r["h"] - d["h"]) > 0.15 or round(r["rot"]) != round(d["rot"]):
                    continue
                if abs(r["y"] - d["y"]) <= 0.6 and (abs(r["x"] - d["x"]) <= 1.5
                                                     or abs(s["cx"] - cx) <= 1.5):
                    s["per_pag"][p] = e
                    break
            else:
                slot.append({"rif": d, "cx": cx, "per_pag": {p: e}})
    tags = set()
    for s in slot:
        per_pag = s["per_pag"]
        testi = {e.dati["testo"] for e in per_pag.values()}
        forzato = bool(testi & extra)
        if len(per_pag) < quota_attr * n or (len(testi) < 2 and not forzato):
            continue
        e0 = next(iter(per_pag.values()))
        etichetta = _etichetta_vicina(e0.dati, fissi)
        tag = _tag(etichetta or ("TITOLO" if "TITOLO" not in tags else "ATTR"), tags)
        # allineamento: centri piu' stabili dei bordi sinistri -> centrato
        xs = [e.dati["x"] for e in per_pag.values()]
        cs = [(e.dati["bbox"][0] + e.dati["bbox"][2]) / 2 for e in per_pag.values()
              if e.dati.get("bbox")]
        allin, xa = "L", e0.dati["x"]
        if len(cs) == len(xs) and len(xs) > 1 and \
                (max(cs) - min(cs)) + 0.05 < (max(xs) - min(xs)):
            allin, xa = "C", sum(cs) / len(cs)
        cor.attributi.append({"tag": tag, **e0.dati, "lw": e0.lw, "rgb": e0.rgb,
                              "allineamento": allin, "x_all": xa})
        for p, e in per_pag.items():
            e.dati["_attr"] = tag
    cor.entita = [e for e in cor.entita
                  if not (e.tipo == "TEXT" and e.dati.get("_attr"))]
    cor.valori = []
    for p, ents in enumerate(pagine):
        cor.valori.append({e.dati["_attr"]: e.dati["testo"] for e in ents
                           if e.tipo == "TEXT" and e.dati.get("_attr")})
    return cor


# ---------------------------------------------------------------------------
# scrittura DXF
# ---------------------------------------------------------------------------

def _nuovo_doc(stili_ttf: dict, linetypes: dict):
    import ezdxf
    doc = ezdxf.new("R2018", setup=["linetypes"])
    h = doc.header
    h["$INSUNITS"] = 4
    h["$MEASUREMENT"] = 1
    h["$LUNITS"] = 2
    h["$LWDISPLAY"] = 1
    h["$LTSCALE"] = 1.0
    h["$TEXTSTYLE"] = "ROMANS"
    for nome, colore in ((LAYER_GEO, 7), (LAYER_TESTI, 7), (LAYER_PIENI, 7),
                         (LAYER_CORNICE, 7)):
        if nome not in doc.layers:
            doc.layers.add(nome, color=colore)
    for nome, font in {**STILI_SHX, **stili_ttf}.items():
        if nome not in doc.styles:
            doc.styles.add(nome, font=font)
    for nome, pat in linetypes.items():
        if nome not in doc.linetypes:
            tot = sum(abs(v) for v in pat)
            doc.linetypes.add(nome, pattern=[tot] + list(pat),
                              description="Tratteggio da PDF")
    return doc


def _scrivi(layout, e: Ent, dx: float = 0.0, dy: float = 0.0):
    from ezdxf.enums import TextEntityAlignment
    from ezdxf import const
    attr = {"layer": e.layer, "lineweight": e.lw}
    if e.linetype:
        attr["linetype"] = e.linetype
    d = e.dati
    T = lambda p: (p[0] + dx, p[1] + dy)  # noqa: E731
    if e.tipo == "LINE":
        ent = layout.add_line(T(d["p0"]), T(d["p1"]), dxfattribs=attr)
    elif e.tipo == "LWPOLYLINE":
        ent = layout.add_lwpolyline([(x + dx, y + dy, b) for x, y, b in d["pts"]],
                                    format="xyb", close=d["chiusa"], dxfattribs=attr)
    elif e.tipo == "CIRCLE":
        ent = layout.add_circle(T(d["c"]), d["r"], dxfattribs=attr)
    elif e.tipo == "ARC":
        ent = layout.add_arc(T(d["c"]), d["r"], d["a0"], d["a1"], dxfattribs=attr)
    elif e.tipo == "SPLINE":
        from ezdxf.math import Bezier4P, Vec3, bezier_to_bspline
        # Vec3 espliciti: con le tuple ezdxf (accelerato) va in errore
        c = [Vec3(T(p)) for p in d["ctrl"]]
        curve = [Bezier4P(c[3 * k:3 * k + 4]) for k in range(d["n"])]
        ent = layout.add_spline(dxfattribs=attr)
        ent.apply_construction_tool(bezier_to_bspline(curve))
    elif e.tipo == "HATCH":
        attr.pop("lineweight", None)
        ent = layout.add_hatch(color=7, dxfattribs=attr)
        ent.set_solid_fill(color=7, style=0, rgb=e.rgb)
        for k, anello in enumerate(d["anelli"]):
            ent.paths.add_polyline_path(
                [T(p) for p in anello], is_closed=True,
                flags=const.BOUNDARY_PATH_EXTERNAL if k == 0 else const.BOUNDARY_PATH_DEFAULT)
        return ent
    elif e.tipo == "TEXT":
        attr.update({"style": d["stile"], "oblique": d.get("obl", 0.0),
                     "width": d.get("wf", 1.0)})
        ent = layout.add_text(testo_cad(d["testo"], d["stile"]), height=d["h"],
                              rotation=d["rot"], dxfattribs=attr)
        ent.set_placement(T((d["x"], d["y"])), align=TextEntityAlignment.LEFT)
    else:
        return None
    if e.rgb:
        ent.rgb = e.rgb
    return ent


def _definisci_cornice(doc, cor: Cornice):
    from ezdxf.enums import TextEntityAlignment
    blk = doc.blocks.new(cor.nome)
    for e in cor.entita:
        e2 = Ent(e.tipo, e.dati, "0", e.lw, e.rgb, e.linetype)
        ent = _scrivi(blk, e2)
        # testi aggiunti a mano con allineamento centrato/a destra (dati "allinea" + "xc"/"xr")
        if ent is not None and e.tipo == "TEXT" and e.dati.get("allinea") == "C" and e.dati.get("xc") is not None:
            ent.set_placement((e.dati["xc"], e.dati["y"]), align=TextEntityAlignment.CENTER)
        elif ent is not None and e.tipo == "TEXT" and e.dati.get("allinea") == "R" and e.dati.get("xr") is not None:
            ent.set_placement((e.dati["xr"], e.dati["y"]), align=TextEntityAlignment.RIGHT)
    for a in cor.attributi:
        att = blk.add_attdef(a["tag"], insert=(a["x"], a["y"]),
                             text=testo_cad(a["testo"], a["stile"]),
                             height=a["h"], rotation=a["rot"],
                             dxfattribs={"layer": "0", "style": a["stile"],
                                         "oblique": a.get("obl", 0.0),
                                         "lineweight": a["lw"],
                                         "prompt": a["tag"].replace("_", " ")})
        if a.get("allineamento") == "C":
            att.set_placement((a["x_all"], a["y"]), align=TextEntityAlignment.CENTER)
        else:
            att.set_placement((a["x"], a["y"]), align=TextEntityAlignment.LEFT)
    return blk


def _inserisci_cornice(msp, cor: Cornice, valori: dict, dx=0.0, dy=0.0):
    ref = msp.add_blockref(cor.nome, (dx, dy), dxfattribs={"layer": LAYER_CORNICE})
    vals = {a["tag"]: testo_cad(valori.get(a["tag"], ""), a["stile"])
            for a in cor.attributi}
    ref.add_auto_attribs(vals)
    return ref


def _vista(doc, x0, y0, x1, y1):
    doc.set_modelspace_vport(height=(y1 - y0) * 1.05,
                             center=((x0 + x1) / 2, (y0 + y1) / 2))
    doc.header["$EXTMIN"] = (x0, y0, 0)
    doc.header["$EXTMAX"] = (x1, y1, 0)
    doc.header["$LIMMIN"] = (x0, y0)
    doc.header["$LIMMAX"] = (x1, y1)


# ---------------------------------------------------------------------------
# orchestrazione
# ---------------------------------------------------------------------------

def _pagine(sel: str | list | None, n: int) -> list[int]:
    if not sel:
        return list(range(1, n + 1))
    if isinstance(sel, list):
        return [int(p) for p in sel if 1 <= int(p) <= n]
    out = []
    for parte in str(sel).split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif parte:
            out.append(int(parte))
    return [p for p in out if 1 <= p <= n]


def _dentro(e: Ent, x0, y0, x1, y1) -> bool:
    """Entita' geometrica tutta dentro il rettangolo (mm)."""
    _, v = _vettore(e)
    if not v:
        return False
    if e.tipo == "CIRCLE":
        cx, cy, r = v
        return x0 <= cx - r and cx + r <= x1 and y0 <= cy - r and cy + r <= y1
    if e.tipo == "ARC":
        cx, cy, r = v[:3]
        return x0 <= cx - r and cx + r <= x1 and y0 <= cy - r and cy + r <= y1
    xs, ys = v[0::2][:2], v[1::2][:2]
    return x0 <= min(xs) and max(xs) <= x1 and y0 <= min(ys) and max(ys) <= y1


def _applica_correzioni(ents: list[Ent], correzioni: dict, pagina: int,
                        aggiunti: list[dict],
                        sostituzioni: list[dict] | None = None) -> list[Ent]:
    out = []
    for e in ents:
        if e.tipo == "TEXT":
            for s in sostituzioni or []:
                if s.get("pagina") not in (None, "", pagina) or not s.get("cerca"):
                    continue
                if s["cerca"] in e.dati["testo"]:
                    e.dati["testo"] = e.dati["testo"].replace(s["cerca"], s.get("testo", ""))
                    e.dati["fonte"], e.dati["conf"] = "ai", 1.0
            c = correzioni.get(e.dati.get("id", ""))
            if c:
                if c.get("elimina"):
                    continue
                for k_in, k_out in (("testo", "testo"), ("altezza", "h"), ("x", "x"),
                                    ("y", "y"), ("rotazione", "rot"), ("stile", "stile"),
                                    ("obliquo", "obl"), ("larghezza", "wf")):
                    if k_in in c:
                        e.dati[k_out] = c[k_in]
                e.dati["fonte"] = "ai"
                e.dati["conf"] = 1.0
        out.append(e)
    for a in aggiunti:
        if int(a.get("pagina", 0)) != pagina:
            continue
        out.append(Ent("TEXT", {
            "testo": a["testo"], "x": float(a["x"]), "y": float(a["y"]),
            "h": float(a.get("altezza", 2.5)), "rot": float(a.get("rotazione", 0)),
            "stile": a.get("stile", "ROMANS"), "obl": float(a.get("obliquo", 0)),
            "wf": float(a.get("larghezza", 1.0)), "id": a.get("id", "ai"),
            "conf": 1.0, "fonte": "ai"}, LAYER_TESTI,
            lw_da_mm(float(a.get("penna_mm", 0.25)))))
    return out


def analizza_pdf(pdf: str, pagine: str | list | None = None, scala: float = 1.0,
                 usa_ocr: bool = True, etichette_glifi: dict | None = None,
                 cache_ocr: str = ""):
    """Estrae pagine e riconosce i testi (senza scrivere nulla)."""
    import pymupdf
    doc = pymupdf.open(pdf)
    sel = _pagine(pagine, doc.page_count)
    pag = [pv.estrai_pagina(doc[p - 1], p, scala) for p in sel]
    if cache_ocr:
        pt.carica_cache_ocr(cache_ocr)
    gruppi, diz, ms = pt.riconosci_pagine([p.tratti for p in pag], usa_ocr,
                                          etichette_ai=etichette_glifi)
    for p, gs in zip(pag, gruppi):  # id con il numero di pagina vero
        for g in gs:
            g.id = f"p{p.numero}-" + g.id.split("-", 1)[1]
    if cache_ocr:
        pt.salva_cache_ocr(cache_ocr)
    meta = dict(doc.metadata or {})
    return pag, gruppi, diz, ms, meta


def info_pdf(pdf: str) -> dict:
    """Pagine, formati, produttore e tipo di contenuto (vettoriale/raster,
    testo vero o testo esploso in tratti)."""
    import pymupdf
    doc = pymupdf.open(pdf)
    pagine = []
    for i, p in enumerate(doc):
        dr = p.get_drawings()
        items = sum(len(d["items"]) for d in dr)
        parole = len(p.get_text("words"))
        imm = len(p.get_images())
        tratti_corti = sum(1 for d in dr if d.get("type") == "s"
                           and max(d["rect"].width, d["rect"].height) * pv.PT_MM < 8)
        tipo = "vettoriale" if items > 50 else ("raster" if imm else "testo/vuota")
        pagine.append({
            "pagina": i + 1, "mm": [round(p.rect.width * pv.PT_MM, 1),
                                   round(p.rect.height * pv.PT_MM, 1)],
            "rotazione": p.rotation, "tipo": tipo, "tratti": len(dr),
            "segmenti": items, "parole_testo_vero": parole, "immagini": imm,
            "testo_esploso_probabile": tratti_corti > 200 and parole < 30})
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    return {"pdf": pdf, "pagine_totali": doc.page_count, "metadati": meta,
            "pagine": pagine,
            "nota": ("Testo esploso: il plotter AutoCAD/SPAC trasforma i font SHX "
                     "in tratti; pdf_a_dwg li ricostruisce come testi veri.")}


def tavola_glifi(diz, solo_senza_etichetta: bool = True, max_n: int = 120,
                 cella_px: int = 64, colonne: int = 12):
    """Immagine con i glifi (cluster) numerati, per farli etichettare all'AI.

    Ritorna (immagine PIL, elenco {id, etichetta, occorrenze, font}).
    Le etichette si passano poi come etichette_glifi {id: carattere}.
    """
    from PIL import Image, ImageDraw
    cl = [c for c in diz.cluster if c.forma and (not solo_senza_etichetta or not c.etichetta)]
    cl.sort(key=lambda c: -c.n)
    cl = cl[:max_n]
    righe = max(1, (len(cl) + colonne - 1) // colonne)
    im = Image.new("RGB", (colonne * cella_px, righe * (cella_px + 14)), "white")
    d = ImageDraw.Draw(im)
    elenco = []
    for i, c in enumerate(cl):
        cx, cy = (i % colonne) * cella_px, (i // colonne) * (cella_px + 14)
        d.rectangle([cx, cy, cx + cella_px - 1, cy + cella_px + 13], outline=(200, 200, 200))
        d.text((cx + 3, cy + 1), str(c.id), fill=(200, 0, 0))
        s = cella_px * 0.55
        ox, oy = cx + cella_px * 0.2, cy + 14 + cella_px * 0.75
        for pts in c.forma:
            q = [(ox + x * s, oy - y * s) for x, y in pts]
            if len(q) >= 2:
                d.line(q, fill="black", width=2)
            elif q:
                d.ellipse([q[0][0] - 1, q[0][1] - 1, q[0][0] + 1, q[0][1] + 1], fill="black")
        # linea di base e delle maiuscole per dare la scala
        d.line([(cx + 2, oy), (cx + cella_px - 2, oy)], fill=(170, 200, 255))
        d.line([(cx + 2, oy - s), (cx + cella_px - 2, oy - s)], fill=(220, 230, 255))
        elenco.append({"id": c.id, "etichetta": c.etichetta, "occorrenze": c.n,
                       "font": c.font, "voti": dict(c.voti)})
    return im, elenco


def converti_pdf(pdf: str, cartella_out: str, pagine: str | list | None = None,
                 nome_base: str = "", scala: float = 1.0, usa_ocr: bool = True,
                 cornice: bool = True, attributi_extra: list[str] | None = None,
                 correzioni: dict | None = None,
                 testi_aggiunti: list[dict] | None = None,
                 etichette_glifi: dict | None = None,
                 sostituzioni: list[dict] | None = None,
                 versione: str = "2018", backend: str = "auto",
                 combinato: bool = True, solo_dxf: bool = False,
                 gap_fogli: float = 20.0, avanzamento=None,
                 elimina_zone: list[dict] | None = None) -> dict:
    """PDF -> un DWG per pagina (+ DWG combinato) e rapporto di revisione.

    avanzamento: callback opzionale (fatto, totale, messaggio).
    elimina_zone: [{"pagina": 3, "zona": [x0, y0, x1, y1]}] geometria da
    togliere (entita' interamente dentro la zona, testi esclusi).
    """
    os.makedirs(cartella_out, exist_ok=True)
    nome_base = nome_base or os.path.splitext(os.path.basename(pdf))[0]
    parametri = {"pdf": os.path.abspath(pdf), "cartella_out": os.path.abspath(cartella_out),
                 "pagine": pagine, "nome_base": nome_base, "scala": scala,
                 "usa_ocr": usa_ocr, "cornice": cornice,
                 "attributi_extra": attributi_extra or [], "versione": versione,
                 "backend": backend, "combinato": combinato}
    avanza = avanzamento or (lambda *a: None)
    avanza(0, 4, "estrazione e riconoscimento testi")
    cache = os.path.join(cartella_out, f".{nome_base}_ocr.json")
    pag, gruppi, diz, ms, meta = analizza_pdf(pdf, pagine, scala, usa_ocr,
                                              etichette_glifi, cache)
    correzioni = correzioni or {}
    testi_aggiunti = testi_aggiunti or []
    stili_ttf: dict = {}
    linetypes: dict = {}
    per_pagina: list[list[Ent]] = []
    zone_da_pulire = elimina_zone or []
    for p, gs in zip(pag, gruppi):
        # correzioni a livello di gruppo di tratti: promuovi a testo un gruppo
        # scartato ("testo"), riporta a geometria ("geometria": true)
        for g in gs:
            c = correzioni.get(g.id)
            if not c:
                continue
            if c.get("geometria"):
                g.accettato = False
            elif c.get("testo") is not None or c.get("elimina"):
                if not g.accettato and c.get("testo") is not None:
                    g.testo, g.accettato = c["testo"], True
                    g.fonte, g.conf = "ai", 1.0
                    if g.altezza < 0.4:
                        g.altezza = float(c.get("altezza", 2.5))
                    pt.punto_inserimento(g)
                g.accettato = True  # "elimina": i tratti spariscono con il testo
        usati = {i for g in gs if g.accettato for i in g.idx}
        ents = geometria(p, usati)
        ents = deduplica(ents)
        ents = unisci_tratteggi(ents, linetypes)
        for z in zone_da_pulire:  # geometria da cancellare (es. simboli spuri)
            if int(z.get("pagina", 0)) == p.numero:
                x0, y0, x1, y1 = z["zona"]
                ents = [e for e in ents if not _dentro(e, x0, y0, x1, y1)]
        ents += testi_da_gruppi(gs) + testi_pdf(p, stili_ttf)
        ents = _applica_correzioni(ents, correzioni, p.numero, testi_aggiunti,
                                   sostituzioni)
        for e in ents:
            e.calcola_firma()
        per_pagina.append(ents)
    avanza(2, 4, "cornice e scrittura DXF")
    cor = rileva_cornice(per_pagina, attributi_extra=attributi_extra) if cornice else None

    tmp_dxf = tempfile.mkdtemp(prefix="pdf2dwg_")
    dxf_files, fogli = [], []
    for k, (p, ents) in enumerate(zip(pag, per_pagina)):
        doc = _nuovo_doc(stili_ttf, linetypes)
        msp = doc.modelspace()
        if cor:
            _definisci_cornice(doc, cor)
            _inserisci_cornice(msp, cor, cor.valori[k])
        n_testi = 0
        for e in ents:
            if cor and (e.dati.get("_cornice") or e.dati.get("_attr")):
                continue
            _scrivi(msp, e)
            n_testi += e.tipo == "TEXT"
        _vista(doc, 0, 0, p.larghezza, p.altezza)
        nome = f"{nome_base}_pag{p.numero:02d}"
        fp = os.path.join(tmp_dxf if not solo_dxf else cartella_out, nome + ".dxf")
        doc.saveas(fp)
        dxf_files.append(fp)
        fogli.append({"pagina": p.numero, "dxf": fp, "entita": len(ents),
                      "testi": n_testi, "larghezza_mm": round(p.larghezza, 2),
                      "altezza_mm": round(p.altezza, 2),
                      "attributi_cornice": cor.valori[k] if cor else {}})
    if combinato and len(pag) > 1:
        doc = _nuovo_doc(stili_ttf, linetypes)
        msp = doc.modelspace()
        if cor:
            _definisci_cornice(doc, cor)
        x = 0.0
        for k, (p, ents) in enumerate(zip(pag, per_pagina)):
            if cor:
                _inserisci_cornice(msp, cor, cor.valori[k], dx=x)
            for e in ents:
                if cor and (e.dati.get("_cornice") or e.dati.get("_attr")):
                    continue
                _scrivi(msp, e, dx=x)
            x += p.larghezza + gap_fogli
        _vista(doc, 0, 0, x - gap_fogli, max(p.altezza for p in pag))
        fp = os.path.join(tmp_dxf if not solo_dxf else cartella_out,
                          f"{nome_base}_tutti_i_fogli.dxf")
        doc.saveas(fp)
        dxf_files.append(fp)

    avanza(3, 4, "conversione DWG")
    rapporto = {
        "parametri": parametri,
        "pdf": pdf, "produttore": meta.get("creator", "") or meta.get("producer", ""),
        "pagine": [f["pagina"] for f in fogli], "cornice": None,
        "ocr": pt.motore_ocr() is not None,
        "glifi": {"cluster": len(diz.cluster),
                  "senza_etichetta": sum(1 for c in diz.cluster if not c.etichetta),
                  "etichette_ai": {c.id: c.etichetta_ai for c in diz.cluster
                                   if c.etichetta_ai}},
    }
    if cor:
        rapporto["cornice"] = {"blocco": cor.nome, "entita": len(cor.entita),
                               "attributi": [a["tag"] for a in cor.attributi]}
    if solo_dxf:
        rapporto["fogli"] = fogli
        rapporto["dxf"] = dxf_files
        conv = {"ok": True, "backend": "nessuno (solo DXF)"}
    else:
        from . import dwg_io
        conv = dwg_io.dxf_a_dwg(dxf_files, cartella_out, versione, backend)
        dwg_per_nome = {os.path.splitext(os.path.basename(d))[0]: d for d in conv["dwg"]}
        for f in fogli:
            nome = os.path.splitext(os.path.basename(f["dxf"]))[0]
            f["dwg"] = dwg_per_nome.get(nome, "")
            f.pop("dxf", None)
        rapporto["fogli"] = fogli
        rapporto["dwg_combinato"] = dwg_per_nome.get(f"{nome_base}_tutti_i_fogli", "")
    rapporto["conversione"] = {k: v for k, v in conv.items() if k != "dwg"}
    # testi per la revisione AI (ordinati per affidabilita' crescente)
    testi = []
    for p, ents in zip(pag, per_pagina):
        for e in ents:
            if e.tipo != "TEXT":
                continue
            d = e.dati
            testi.append({"id": d.get("id"), "pagina": p.numero, "testo": d["testo"],
                          "x": round(d["x"], 2), "y": round(d["y"], 2),
                          "altezza": d["h"], "rotazione": d["rot"], "stile": d["stile"],
                          "obliquo": d.get("obl", 0.0), "conf": d.get("conf", 1.0),
                          "fonte": d.get("fonte", ""),
                          "attributo": d.get("_attr", ""),
                          "cornice": bool(d.get("_cornice"))})
    scartati = []
    for p, gs in zip(pag, gruppi):
        for g in gs:
            if not g.accettato and g.ocr and g.nota not in ("caratteri non ammessi",):
                scartati.append({"id": g.id, "pagina": p.numero, "ocr": g.ocr,
                                 "nota": g.nota, "bbox": [round(v, 1) for v in g.bbox]})
    rapporto["testi_totali"] = len(testi)
    rapporto["da_verificare"] = sorted(
        [t for t in testi if t["conf"] < 0.8 and t["fonte"] != "ai"],
        key=lambda t: t["conf"])
    rapporto["scartati_dubbi"] = scartati
    fp = os.path.join(cartella_out, f"{nome_base}_rapporto.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({**rapporto, "testi": testi}, f, ensure_ascii=False, indent=1)
    rapporto["rapporto_json"] = fp
    if not solo_dxf:
        import shutil
        shutil.rmtree(tmp_dxf, ignore_errors=True)
    return rapporto
