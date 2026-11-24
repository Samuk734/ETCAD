"""Testi ROMANS disegnati con i glifi Hershey Roman Simplex.

romans.shx di AutoCAD/SPAC deriva dai font Hershey (pubblico dominio): le
forme coincidono, le larghezze sono state misurate sui PDF plottati da SPAC
2025 (dati/romans_avanzamenti.json). Serve per anteprime fedeli quando i
font SHX non sono disponibili (PC senza SPAC): i testi del DWG vengono
disegnati come polilinee solo nella copia usata per l'anteprima.
"""
from __future__ import annotations

import json
import math
import os

_DATI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati")
_GLIFI: dict | None = None
_AVANZ: dict = {}
STILI_HERSHEY = {"romans.shx", "romans", "simplex.shx", "txt.shx", "italicc.shx",
                 "romanc.shx", "romand.shx", "isocp.shx"}
# ISOCP non e' Hershey: stesse proporzioni verticali, ~13% piu' stretto
# (misurato sui PDF SPAC). Approssimazione solo per le anteprime.
SCALA_X = {"isocp.shx": 0.87}


def _carica() -> dict:
    global _GLIFI, _AVANZ
    if _GLIFI is not None:
        return _GLIFI
    _GLIFI = {}
    with open(os.path.join(_DATI, "rowmans.jhf"), encoding="latin-1") as f:
        righe = f.read().splitlines()
    rec, buf = [], ""
    for r in righe:
        if len(r) >= 8 and r[:5].strip().isdigit() and buf:
            rec.append(buf)
            buf = r
        else:
            buf += r
    if buf:
        rec.append(buf)
    for k, r in enumerate(rec):
        n = int(r[5:8])
        d = r[8:]
        L, R = ord(d[0]) - 82, ord(d[1]) - 82
        tratti, cur = [], []
        for i in range(2, 2 * n, 2):
            a, b = d[i], d[i + 1]
            if a == " " and b == "R":
                if cur:
                    tratti.append(cur)
                cur = []
            else:
                cur.append((ord(a) - 82, ord(b) - 82))
        if cur:
            tratti.append(cur)
        _GLIFI[chr(32 + k)] = (L, R, tratti)
    try:
        with open(os.path.join(_DATI, "romans_avanzamenti.json"), encoding="utf-8") as f:
            _AVANZ = json.load(f).get("avanzamenti", {})
    except (OSError, ValueError):
        _AVANZ = {}
    return _GLIFI


def _glifo(c: str):
    g = _carica()
    if c in g:
        return g[c]
    if c in ("Ø", "ø"):  # diametro: O barrata (Hershey non lo ha)
        L, R, tr = g["O" if c == "Ø" else "o"]
        xs = [p[0] for t in tr for p in t]
        ys = [p[1] for t in tr for p in t]
        barra = [(min(xs) - 1, max(ys) + 1), (max(xs) + 1, min(ys) - 1)]
        return (L, R, list(tr) + [barra])
    if c == "±":  # + alzato con la barra sulla linea di base
        L, R, tr = g["+"]
        su = [[(x, y - 3) for x, y in t] for t in tr]
        xs = [p[0] for t in tr for p in t]
        return (L, R, su + [[(min(xs), 9), (max(xs), 9)]])
    if c == "°":  # cerchietto in alto
        cerchio = [(3 * math.cos(math.radians(a)), -9 + 3 * math.sin(math.radians(a))) for a in range(0, 361, 30)]
        return (-5, 5, [cerchio])
    # accentate e simboli comuni -> base Hershey
    base = {"à": "a", "è": "e", "é": "e", "ì": "i", "ò": "o", "ù": "u",
            "À": "A", "È": "E", "É": "E", "Ì": "I", "Ò": "O", "Ù": "U",
            "°": "o", "±": "+", "µ": "u", "²": "2", "³": "3", "ç": "c"}.get(c, "?")
    return g.get(base, g["?"])


def avanzamento(c: str) -> float:
    """Avanzamento in unita' Hershey (altezza maiuscole = 21)."""
    _carica()
    if c in _AVANZ:
        return float(_AVANZ[c])
    L, R, _ = _glifo(c)
    return float(R - L)


def larghezza(testo: str, h: float, wf: float = 1.0) -> float:
    return sum(avanzamento(c) for c in testo) * h / 21.0 * wf


def polilinee(testo: str, x: float, y: float, h: float, rot: float = 0.0,
              obl: float = 0.0, wf: float = 1.0, allineamento: str = "L",
              valign: int = 0) -> list[list[tuple]]:
    """Polilinee (coordinate disegno) del testo con punto base in (x, y)."""
    for k, v in (("%%d", "°"), ("%%D", "°"), ("%%p", "±"),
                 ("%%P", "±"), ("%%c", "Ø"), ("%%C", "Ø"), ("%%%", "%")):
        testo = testo.replace(k, v)
    s = h / 21.0
    t = math.tan(math.radians(obl))
    L_tot = larghezza(testo, h, wf)
    dx0 = {"L": 0.0, "C": -L_tot / 2, "R": -L_tot}.get(allineamento, 0.0)
    dy0 = {0: 0.0, 1: 0.35 * h, 2: -h / 2, 3: -h}.get(valign, 0.0)
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    out = []
    pen = 0.0
    for c in testo:
        L, R, tratti = _glifo(c)
        for tr in tratti:
            pts = []
            for hx, hy in tr:
                v = (9 - hy) * s                      # baseline Hershey a y=9
                u = ((hx - L) * s + v * t) * wf + pen
                u, v = u + dx0, v + dy0
                pts.append((x + u * ca - v * sa, y + u * sa + v * ca))
            if len(pts) >= 2:
                out.append(pts)
        pen += avanzamento(c) * s * wf
    return out


def _parametri(e):
    """(x, y, allineamento, valign) del TEXT/ATTRIB secondo il DXF."""
    ha = int(e.dxf.get("halign", 0))
    va = int(e.dxf.get("valign", 0))
    if ha == 0 and va == 0:
        p = e.dxf.insert
        return p[0], p[1], "L", 0
    p = e.dxf.get("align_point", e.dxf.insert)
    alli = {1: "C", 2: "R", 4: "C"}.get(ha, "L")
    if ha == 4:
        va = 2
    return p[0], p[1], alli, va


def _font_stile(doc, nome: str) -> str:
    try:
        return (doc.styles.get(nome).dxf.font or "").lower()
    except Exception:  # noqa: BLE001 - stile assente
        return ""


def esplodi_testi(doc, solo_font: set | None = None) -> int:
    """Sostituisce TEXT/ATTRIB con font SHX (romans, isocp...) con polilinee
    Hershey, in spazio modello e nei blocchi. Solo per copie di anteprima."""
    solo_font = solo_font or STILI_HERSHEY
    n = 0

    def _conv(layout, e):
        nonlocal n
        font = _font_stile(doc, e.dxf.get("style", "Standard"))
        if font not in solo_font:
            return False
        x, y, al, va = _parametri(e)
        wf = e.dxf.get("width", 1.0) * SCALA_X.get(font, 1.0)
        obl = e.dxf.get("oblique", 0.0)
        if font.startswith("italic") and not obl:
            obl = 15.0  # i font SHX italic sono gia' inclinati di suo
        attr = {"layer": e.dxf.layer, "lineweight": e.dxf.get("lineweight", -1), "color": e.dxf.get("color", 256)}
        if e.dxf.hasattr("true_color"):
            attr["true_color"] = e.dxf.true_color
        for pts in polilinee(e.dxf.text, x, y, e.dxf.height, e.dxf.get("rotation", 0.0),
                             obl, wf, al, va):
            layout.add_lwpolyline(pts, dxfattribs=attr)
        n += 1
        return True

    layouts = [doc.modelspace()] + [b for b in doc.blocks if not b.name.startswith("*")]
    for lay in layouts:
        for e in list(lay.query("TEXT")):
            if _conv(lay, e):
                lay.delete_entity(e)
    msp = doc.modelspace()
    for ins in msp.query("INSERT"):
        for a in list(ins.attribs):
            if _conv(msp, a):
                a.dxf.text = ""
    return n
