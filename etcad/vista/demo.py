"""Disegni di esempio di ETCAD, generati da zero (nessun disegno reale).

- `passi(d, spec)`: la scheda di un cavo disegnata un passo alla volta. La usa la demo della vista: Claude disegna
  il foglio e a ogni passo manda il suo ragionamento.
- `scheda_cavo`, `sdoppiatore`, `schema_ingressi`, `copertina`: i fogli del set di esempio
  (examples/genera_esempi.py), tutti parametrici.

    python -m etcad.vista demo [cartella_uscita]
"""
from __future__ import annotations

import math
import os

import ezdxf

from .. import hershey

FOGLIO = "Demo - Cavo sensore M12"

COLORI = {"BN": (150, 90, 45), "WH": (200, 200, 200), "BU": (40, 110, 230), "BK": (95, 95, 95),
          "GY": (140, 140, 140), "GN": (60, 170, 70), "YE": (225, 200, 40), "PK": (240, 130, 180),
          "RD": (225, 50, 50), "VT": (150, 80, 200), "GNYE": (110, 190, 60), "OG": (240, 140, 30)}

LAYER = (("CORNICE", 7, 50), ("CARTIGLIO", 7, 25), ("CONNETTORI", 7, 35), ("FILI", 7, 35), ("GUAINA", 8, 25),
         ("SCHERMO", 8, 18), ("SIMBOLI", 7, 35), ("SIGLE", 2, 25), ("TESTI", 7, 18), ("TABELLE", 7, 18),
         ("QUOTE", 30, 18))

DEMO = {
    "titolo": "CAVO SENSORE M12 - 4 POLI",
    "sottotitolo": "Sensore induttivo PNP -B1 verso morsettiera -X2",
    "codice": "CAV-M12-4P-2000",
    "nota_codice": "L = 2000 mm",
    "sinistra": {"tipo": "circolare", "sigla": "-XS1", "descrizione": "M12 femmina 4 poli, codifica A", "poli": 4},
    "destra": {"tipo": "morsetti", "sigla": "-X2"},
    "cavo": "Cavo PUR 4x0,34  %%c5,2",
    "sezione": "PUR 4x0,34",
    "fili": [("1", "BN", "+24 V", "1"), ("2", "WH", "uscita 2", "2"), ("3", "BU", "0 V", "3"),
             ("4", "BK", "uscita 1", "4")],
    "intestazione": ("PIN", "COLORE", "FUNZIONE", "MORS."),
    "lunghezza": 2000,
    "distinta": [("1", "1", "Connettore M12 femmina 4 poli diritto", "M12-F4-D"),
                 ("2", "2 m", "Cavo PUR 4x0,34 %%c5,2", "PUR-4X034"),
                 ("3", "4", "Puntale isolato 0,34", "PI-034"),
                 ("4", "1", "Guaina termorestringente %%c9 L 30", "TR-9-30")],
    "note": ["Puntali isolati sui quattro fili", "Guaina TR %%c9 all'uscita del cavo", "Spelatura 40 mm",
             "Collaudo continuita' 100%"],
    "foglio": "1", "di": "1",
}


def nuovo_doc():
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    doc.styles.add("ROMANS", font="romans.shx")
    doc.styles.add("ISOCP", font="isocp.shx")
    doc.linetypes.add("TRATTEGGIO_ETCAD", pattern=[4.5, 3.0, -1.5], description="__ __ __")
    for nome, col, lw in LAYER:
        doc.layers.add(nome, color=col, lineweight=lw)
    doc.layers.get("SCHERMO").dxf.linetype = "TRATTEGGIO_ETCAD"
    return doc


class Disegno:
    """Primitive di disegno su un layout (modello o blocco), con origine spostabile per i fogli affiancati."""

    def __init__(self, doc=None):
        self.doc = doc or nuovo_doc()
        self.msp = self.doc.modelspace()
        self.lay = self.msp
        self.layer = None            # se impostato, tutto va su questo layer
        self.ox = self.oy = 0.0

    def P(self, p):
        return (p[0] + self.ox, p[1] + self.oy)

    def _a(self, ly, extra=None):
        a = {"layer": self.layer or ly}
        a.update(extra or {})
        return a

    def linea(self, p0, p1, ly="CONNETTORI", rgb=None):
        e = self.lay.add_line(self.P(p0), self.P(p1), dxfattribs=self._a(ly))
        if rgb:
            e.rgb = rgb
        return e

    def poly(self, pts, ly="CONNETTORI", chiusa=False, rgb=None):
        e = self.lay.add_lwpolyline([self.P(p) for p in pts], close=chiusa, dxfattribs=self._a(ly))
        if rgb:
            e.rgb = rgb
        return e

    def rett(self, x0, y0, x1, y1, ly="CONNETTORI"):
        return self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], ly, True)

    def cerchio(self, c, r, ly="CONNETTORI", rgb=None):
        e = self.lay.add_circle(self.P(c), r, dxfattribs=self._a(ly))
        if rgb:
            e.rgb = rgb
        return e

    def arco(self, c, r, a0, a1, ly="GUAINA"):
        self.lay.add_arc(self.P(c), r, a0, a1, dxfattribs=self._a(ly))

    def pieno(self, pts, ly="QUOTE", rgb=None):
        h = self.lay.add_hatch(dxfattribs=self._a(ly))
        if rgb:
            h.rgb = rgb
        h.paths.add_polyline_path([self.P(p) for p in pts], is_closed=True)
        return h

    def disco(self, c, r, ly="FILI", rgb=None):
        n = 20
        return self.pieno([(c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n))
                           for k in range(n)], ly, rgb)

    def testo(self, s, x, y, h=2.5, ly="TESTI", al="L", stile="ROMANS", rot=0.0, wf=1.0):
        from ezdxf.enums import TextEntityAlignment
        t = self.lay.add_text(s, height=h, rotation=rot, dxfattribs=self._a(ly, {"style": stile, "width": wf}))
        t.set_placement(self.P((x, y)), align={"L": TextEntityAlignment.LEFT, "C": TextEntityAlignment.CENTER,
                                               "R": TextEntityAlignment.RIGHT}[al])
        return t

    def freccia(self, punta, verso, ly="QUOTE", lung=2.5):
        dx, dy = verso
        b = (punta[0] - dx * lung, punta[1] - dy * lung)
        self.pieno([punta, (b[0] - dy * 0.45, b[1] + dx * 0.45), (b[0] + dy * 0.45, b[1] - dx * 0.45)], ly)


def larg(s, h):
    return hershey.larghezza(s, h)


# -- cornice A3 ------------------------------------------------------------------------------------------------
CAMPI = (("OGGETTO", 16, 30, 4.0, "SIGLE"), ("SOTTOTITOLO", 16, 18, 2.5, "TESTI"), ("CODICE", 155, 24, 3.5, "SIGLE"),
         ("NOTA", 155, 15, 2.5, "TESTI"), ("DISEGNATO", 293, 31.5, 2.5, "TESTI"), ("DATA", 293, 16, 2.5, "TESTI"),
         ("FOGLIO", 362, 31.5, 2.5, "TESTI"), ("DI", 384, 31.5, 2.5, "TESTI"))


def _cornice_fissa(d: Disegno):
    d.rett(10, 10, 410, 287, "CORNICE")
    d.linea((10, 281), (410, 281), "CORNICE")
    for k in range(8):
        x0 = 10 + 50 * k
        if k:
            d.linea((x0, 281), (x0, 287), "CORNICE")
        d.testo(str(k + 1), x0 + 25, 282.6, 2.5, "CORNICE", "C")
    d.linea((10, 42), (410, 42), "CARTIGLIO")
    for x in (150, 270, 340):
        d.linea((x, 10), (x, 42), "CARTIGLIO")
    d.linea((270, 26), (410, 26), "CARTIGLIO")
    d.testo("CODICE", 155, 35, 2.0)
    d.testo("DISEGN.", 275, 31.5, 2.5)
    d.testo("DATA", 275, 16, 2.5)
    d.testo("FOGLIO", 345, 31.5, 2.5)
    d.testo("DI", 377, 31.5, 2.5)
    d.testo("ETCAD", 375, 14.5, 5.0, "SIGLE", "C")


def valori_cartiglio(spec: dict, data: str = "01/02/2026") -> dict:
    return {"OGGETTO": spec["titolo"], "SOTTOTITOLO": spec.get("sottotitolo", ""), "CODICE": spec.get("codice", ""),
            "NOTA": spec.get("nota_codice", ""), "DISEGNATO": "ETCAD", "DATA": data,
            "FOGLIO": str(spec.get("foglio", "1")), "DI": str(spec.get("di", "1"))}


def blocco_cornice(doc, nome: str = "CORNICE_A3"):
    """Definisce (una volta) il blocco della cornice con gli attributi del cartiglio."""
    if nome in doc.blocks:
        return nome
    blk = doc.blocks.new(nome)
    d = Disegno(doc)
    d.lay = blk
    _cornice_fissa(d)
    for tag, x, y, h, ly in CAMPI:
        blk.add_attdef(tag, (x, y), dxfattribs={"height": h, "style": "ROMANS", "layer": ly})
    return nome


def cornice(d: Disegno, spec: dict, blocco: bool = False):
    """Cornice A3 e cartiglio: entita' sciolte (demo) o INSERT del blocco con attributi (set di fogli)."""
    val = valori_cartiglio(spec)
    if blocco:
        nome = blocco_cornice(d.doc)
        ins = d.lay.add_blockref(nome, d.P((0, 0)), dxfattribs=d._a("CORNICE"))
        ins.add_auto_attribs(val)
        return ins
    _cornice_fissa(d)
    for tag, x, y, h, ly in CAMPI:
        if val[tag]:
            d.testo(val[tag], x, y, h, ly)


# -- connettori ------------------------------------------------------------------------------------------------
def pos_pin(n: int):
    """Posizioni dei pin (vista lato contatti) per connettori circolari M8/M12/M23, raggio unitario."""
    if n == 3:
        return [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in (135, 45, 270)]
    if n == 4:
        return [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in (135, 45, -45, -135)]
    if n == 5:
        return pos_pin(4) + [(0.0, 0.0)]
    giro = n - 1
    return [(math.cos(math.radians(135 - 360 * k / giro)), math.sin(math.radians(135 - 360 * k / giro)))
            for k in range(giro)] + [(0.0, 0.0)]


def vista_frontale(d: Disegno, cx, cy, poli: int, nomi=None, r=13.0, didascalia=("", "")):
    d.cerchio((cx, cy), r)
    d.cerchio((cx, cy), r - 3)
    d.poly([(cx - 1.6, cy + r - 3), (cx - 1.6, cy + r - 0.6), (cx + 1.6, cy + r - 0.6), (cx + 1.6, cy + r - 3)])
    rp = (r - 3) * (0.5 if poli <= 3 else 0.56 if poli <= 5 else 0.66)
    rc = 1.2 if poli <= 5 else 0.9
    hn = 1.5 if r >= 12 else 1.2                    # numeri dei pin piu' piccoli sui connettori piccoli (M8)
    nomi = nomi or [str(k + 1) for k in range(poli)]
    for (ux, uy), s in zip(pos_pin(poli), nomi):
        p = (cx + rp * ux, cy + rp * uy)
        d.cerchio(p, rc)
        dist = rc + (1.3 if r >= 12 else 1.0)
        if ux or uy:
            q = (p[0] + dist * ux, p[1] + dist * uy - hn / 2)
        else:
            q = (p[0] + rc + 0.6, p[1] - 2.6)
        d.testo(s, q[0], q[1], hn, "TESTI", "C", "ISOCP")
    if didascalia[0]:
        d.testo(didascalia[0], cx, cy - r - 8, 2.2, "TESTI", "C")
    if didascalia[1]:
        d.testo(didascalia[1], cx, cy - r - 12.5, 1.8, "TESTI", "C")


def morsetti(d: Disegno, x, ys, nomi, sigla, verso=1, puntali=True):
    """Morsettiera verticale. verso=1: fili da sinistra (morsetti a destra di x); -1: specchiata."""
    passo = abs(ys[0] - ys[1]) if len(ys) > 1 else 9.0
    hm = min(3.5, passo / 2 - 0.4)
    for y, s in zip(ys, nomi):
        if puntali:
            a, b = (x - 9, x - 2) if verso > 0 else (x + 2, x + 9)
            d.rett(min(a, b), y - 1.3, max(a, b), y + 1.3)
        m0, m1 = (x, x + 10) if verso > 0 else (x - 10, x)
        d.rett(m0, y - hm, m1, y + hm)
        d.cerchio(((m0 + m1) / 2, y), min(1.4, hm - 0.7))
        if verso > 0:
            d.testo(s, m1 + 3, y - 1.1, 2.2, "TESTI", "L", "ISOCP")
        else:
            d.testo(s, m0 - 3, y - 1.1, 2.2, "TESTI", "R", "ISOCP")
    xm = x + 5 * verso
    d.testo(sigla, xm, max(ys) + 8, 3.5, "SIGLE", "C")


# -- scheda di un cavo -----------------------------------------------------------------------------------------
def _ys(n: int, yc: float = 213.0):
    passo = 9.0 if n <= 4 else 7.0 if n <= 6 else 6.0
    return [yc + (n - 1) / 2 * passo - k * passo for k in range(n)], passo


def _geo(spec):
    n = len(spec["fili"])
    ys, passo = _ys(n)
    x0 = 90.0 if spec["sinistra"]["tipo"] == "circolare" else 89.0       # dove partono i fili
    return {"n": n, "ys": ys, "passo": passo, "x0": x0, "x1": 311.0, "yt": ys[0] + passo / 2 + 2,
            "yb": ys[-1] - passo / 2 - 2}


def estremo_sinistro(d: Disegno, spec: dict):
    g = _geo(spec)
    sx, ys = spec["sinistra"], g["ys"]
    if sx["tipo"] == "circolare":
        d.rett(80, ys[-1] - 5, 90, ys[0] + 5)
        for (pin, *_), y in zip(spec["fili"], ys):
            d.testo(pin, 85, y - 1.0, 2.2, "TESTI", "C", "ISOCP")
        d.testo(sx["sigla"], 85, ys[0] + 9, 3.5, "SIGLE", "C")
        r = 13.0 if sx.get("poli", 4) <= 5 else 15.0
        vista_frontale(d, 45, (ys[0] + ys[-1]) / 2, sx.get("poli", 4), sx.get("pin"), r,
                       (sx.get("descrizione", ""), "vista lato contatti"))
    else:
        morsetti(d, 80, ys, [f[0] for f in spec["fili"]], sx["sigla"], verso=-1)
        if sx.get("descrizione"):
            d.testo(sx["descrizione"], 74, ys[-1] - 9, 1.8, "TESTI", "L")


def fili(d: Disegno, spec: dict):
    g = _geo(spec)
    for (pin, col, funz, _a), y in zip(spec["fili"], g["ys"]):
        d.linea((g["x0"], y), (g["x1"], y), "FILI", COLORI.get(col))
        d.testo(f"{col}  {funz}", g["x0"] + 6, y + 1.1, 2.0)
    return g["ys"]


def richiamo(d: Disegno, da, a, ly="TESTI"):
    """Linea di richiamo con la freccia sul punto indicato."""
    d.linea(da, a, ly)
    lung = math.hypot(a[0] - da[0], a[1] - da[1]) or 1.0
    d.freccia(a, ((a[0] - da[0]) / lung, (a[1] - da[1]) / lung), ly)


def _x_guaina(spec):
    g = _geo(spec)
    r = (g["yt"] - g["yb"]) / 2
    piu_lunga = max(larg(f"{c}  {f}", 2.0) for _p, c, f, _a in spec["fili"])
    sinistra = max(122.0, g["x0"] + 6 + piu_lunga + 4)
    return sinistra + r, 288 - r, r


def guaina(d: Disegno, spec: dict):
    g = _geo(spec)
    yt, yb = g["yt"], g["yb"]
    yc = (yt + yb) / 2
    xa, xb, r = _x_guaina(spec)
    d.arco((xa, yc), r, 90, 270)
    d.arco((xb, yc), r, 270, 90)
    d.linea((xa, yt), (xb, yt), "GUAINA")
    d.linea((xa, yb), (xb, yb), "GUAINA")
    if spec.get("schermo"):
        rs = r - 1.6
        d.arco((xa, yc), rs, 90, 270, "SCHERMO")
        d.arco((xb, yc), rs, 270, 90, "SCHERMO")
        d.linea((xa, yt - 1.6), (xb, yt - 1.6), "SCHERMO")
        d.linea((xa, yb + 1.6), (xb, yb + 1.6), "SCHERMO")
    t = spec["cavo"]
    w = larg(t, 2.5)
    xm = (xa + xb) / 2
    if spec.get("schermo"):
        ts = spec["schermo"]
        ws = larg(ts, 2.2)
        tx = min(xm + w / 2 + 12, xb + r - ws)
        d.testo(ts, tx, yt + 5, 2.2)
        d.linea((tx - 0.5, yt + 4.2), (tx + ws + 0.5, yt + 4.2), "TESTI")
        richiamo(d, (tx - 0.5, yt + 4.2), (tx - 5, yt - 1.4))
    d.testo(t, xm - w / 2, yt + 12, 2.5)
    d.linea((xm - w / 2 - 0.5, yt + 11.2), (xm + w / 2 + 0.5, yt + 11.2), "TESTI")
    d.linea((xm, yt + 11.2), (xm, yt + 2.5), "TESTI")
    d.freccia((xm, yt), (0, -1), "TESTI")


def sezione(d: Disegno, spec: dict, cx=378.0):
    """Sezione del cavo con le anime colorate (e lo schermo, se c'e')."""
    g = _geo(spec)
    cy = (g["ys"][0] + g["ys"][-1]) / 2
    n = g["n"]
    R = 12.0
    d.cerchio((cx, cy), R, "GUAINA")
    if spec.get("schermo"):
        d.cerchio((cx, cy), R - 1.5, "SCHERMO")
    rc = 3.0 if n <= 4 else 2.4 if n <= 5 else 2.0
    k = 4.6 if n <= 5 else 6.2
    for (ux, uy), (_p, col, *_r) in zip(pos_pin(n if n > 3 else 4), spec["fili"]):
        c = (cx + k * ux, cy + k * uy)
        d.disco(c, rc, "FILI", COLORI.get(col))
        d.cerchio(c, rc, "FILI")
    d.testo("SEZIONE", cx, cy + R + 5, 2.5, "SIGLE", "C")
    d.testo(spec.get("sezione", ""), cx, cy - R - 6, 2.0, "TESTI", "C")


def estremo_destro(d: Disegno, spec: dict):
    g = _geo(spec)
    morsetti(d, 320, g["ys"], [f[3] for f in spec["fili"]], spec["destra"]["sigla"])
    d.testo("puntali isolati", 315.5, g["ys"][-1] - 9, 1.8, "TESTI", "R")


def tabella(d: Disegno, x, y, colonne, righe, titolo="", h=7.0):
    """Tabella con intestazione: colonne = larghezze, righe[0] = intestazione."""
    xs = [x]
    for c in colonne:
        xs.append(xs[-1] + c)
    if titolo:
        d.testo(titolo, x, y + 4, 2.8, "SIGLE")
    for k in range(len(righe) + 1):
        d.linea((x, y - h * k), (xs[-1], y - h * k), "TABELLE")
    for xx in xs:
        d.linea((xx, y), (xx, y - h * len(righe)), "TABELLE")
    for k, r in enumerate(righe):
        for j, s in enumerate(r[:len(colonne)]):
            hh = 2.0 if k else 2.2
            if colonne[j] >= 40 and k:
                d.testo(s, xs[j] + 2, y - h * k - h / 2 - hh / 2, hh, "TABELLE", "L")
            else:
                d.testo(s, (xs[j] + xs[j + 1]) / 2, y - h * k - h / 2 - hh / 2, hh, "TABELLE", "C",
                        "ROMANS" if k else "ISOCP")


def tabelle(d: Disegno, spec: dict, y=128.0):
    n = len(spec["fili"])
    h = 7.0 if n <= 5 else 6.0
    tabella(d, 20, y, [16, 20, 46, 18], [spec.get("intestazione", ("PIN", "COLORE", "FUNZIONE", "MORS."))]
            + [tuple(f) for f in spec["fili"]], "COLLEGAMENTI", h)
    if spec.get("distinta"):
        tabella(d, 135, y, [10, 14, 80, 32], [("POS", "Q.TA", "DESCRIZIONE", "CODICE")] + list(spec["distinta"]),
                "DISTINTA MATERIALI", 7.0)


def quota_orizzontale(d: Disegno, xa, xb, y, testo, y_da=None):
    y_da = y if y_da is None else y_da
    d.linea((xa, y_da - 2), (xa, y - 3), "QUOTE")
    d.linea((xb, y_da - 2), (xb, y - 3), "QUOTE")
    d.linea((xa, y), (xb, y), "QUOTE")
    d.freccia((xa, y), (-1, 0))
    d.freccia((xb, y), (1, 0))
    d.testo(testo, (xa + xb) / 2, y + 1.5, 3.0, "QUOTE", "C")


def quota(d: Disegno, spec: dict):
    g = _geo(spec)
    xa = 80.0 if spec["sinistra"]["tipo"] == "circolare" else 71.0
    quota_orizzontale(d, xa, 318.0, g["ys"][-1] - 20, f"L = {spec['lunghezza']} mm %%p{spec.get('tolleranza', 10)}",
                      g["ys"][-1] - 6)


def note(d: Disegno, spec: dict, x=305.0, y=128.0):
    d.testo("NOTE", x, y + 4, 2.8, "SIGLE")
    for k, s in enumerate(spec.get("note", [])):
        d.testo(f"{k + 1}. {s}", x, y - 5 - 5.5 * k, 2.2)


def passi(d: Disegno, spec: dict | None = None, blocco: bool = False):
    """Generatore: disegna la scheda di un cavo un passo alla volta e restituisce (pensiero, titolo)."""
    spec = spec or DEMO
    sx, dx = spec["sinistra"], spec["destra"]
    cornice(d, spec, blocco)
    yield ("Imposto il foglio A3 orizzontale: cornice con le colonne 1-8 e il cartiglio con codice e lunghezza.",
           "Foglio A3: cornice e cartiglio")
    estremo_sinistro(d, spec)
    if sx["tipo"] == "circolare":
        yield (f"Disegno il connettore {sx['sigla']} ({sx.get('descrizione', '')}): la vista lato contatti con la "
               f"chiave e i pin numerati, e accanto il corpo da cui escono i fili.", f"Connettore {sx['sigla']}")
    else:
        yield (f"A sinistra la morsettiera {sx['sigla']} con i puntali.", f"Morsettiera {sx['sigla']}")
    fili(d, spec)
    yield ("Tiro i fili verso destra, ognuno del suo colore, con colore e funzione sopra il filo.", "Fili colorati")
    guaina(d, spec)
    sezione(d, spec)
    yield ("Aggiungo la guaina del cavo con l'etichetta e il richiamo, e a destra la sezione con le anime colorate.",
           "Guaina, etichetta e sezione")
    estremo_destro(d, spec)
    yield (f"Ora i puntali e la morsettiera {dx['sigla']}: ogni filo arriva al suo morsetto.",
           f"Morsettiera {dx['sigla']}")
    tabelle(d, spec)
    yield ("Riassumo i collegamenti in una tabella e aggiungo la distinta dei materiali.",
           "Tabella collegamenti e distinta")
    quota(d, spec)
    yield (f"Quoto la lunghezza totale: {spec['lunghezza']} mm dal connettore alla punta dei puntali.",
           f"Quota L = {spec['lunghezza']} mm")
    note(d, spec)
    yield ("Ultimo passo: le note di lavorazione accanto alla distinta.", "Note di lavorazione")


def scheda_cavo(d: Disegno, spec: dict, blocco: bool = False) -> None:
    for _ in passi(d, spec, blocco):
        pass


# -- sdoppiatore a Y -------------------------------------------------------------------------------------------
def sdoppiatore(d: Disegno, spec: dict, blocco: bool = True) -> None:
    """Cavo a Y: un M12 maschio verso due M8 femmina, con il cablaggio interno del corpo stampato."""
    cornice(d, spec, blocco)
    tronco = [("1", "BN", "+24 V"), ("2", "WH", "ingresso B"), ("3", "BU", "0 V"), ("4", "BK", "ingresso A")]
    ys = [212.0, 204.0, 196.0, 188.0]
    d.rett(72, 183, 82, 217)
    for (pin, *_), y in zip(tronco, ys):
        d.testo(pin, 77, y - 1.0, 2.2, "TESTI", "C", "ISOCP")
    d.testo("-XS10", 77, 221, 3.5, "SIGLE", "C")
    vista_frontale(d, 38, 200, 4, None, 13, ("M12 maschio 4 poli", "vista lato contatti"))
    bus = {"BN": 186.0, "BU": 190.0, "BK": 194.0, "WH": 198.0}          # verticali dentro il corpo stampato
    for (pin, col, funz), y in zip(tronco, ys):
        d.linea((82, y), (bus[col], y), "FILI", COLORI[col])
        d.testo(f"{col}  {funz}", 88, y + 1.0, 1.8)
    r = 15.0
    d.arco((133, 200), r, 90, 270)
    d.arco((160, 200), r, 270, 90)
    d.linea((133, 215), (160, 215), "GUAINA")
    d.linea((133, 185), (160, 185), "GUAINA")
    d.rett(178, 136, 206, 252, "SCHERMO")
    d.testo("-Y1  corpo stampato", 192, 255, 2.5, "SIGLE", "C")
    rami = {"A": {"ys": [244.0, 237.0, 230.0], "sigla": "-XS11",
                  "fili": [("1", "BN", "+24 V", "BN"), ("3", "BU", "0 V", "BU"), ("4", "BK", "ingresso A", "BK")]},
            "B": {"ys": [156.0, 149.0, 142.0], "sigla": "-XS12",
                  "fili": [("1", "BN", "+24 V", "BN"), ("3", "BU", "0 V", "BU"), ("4", "BK", "ingresso B", "WH")]}}
    giunti = {}
    for ramo, rr in rami.items():
        for (pin, col, funz, sorg), y in zip(rr["fili"], rr["ys"]):
            d.linea((bus[sorg], y), (322, y), "FILI", COLORI[col])
            d.testo(f"{col}  {funz}", 214, y + 1.0, 1.8)
            giunti.setdefault(sorg, []).append(y)
    for col, ylst in giunti.items():
        y0 = ys[[t[1] for t in tronco].index(col)]
        tutte = ylst + [y0]
        d.linea((bus[col], min(tutte)), (bus[col], max(tutte)), "FILI", COLORI[col])
        if len(ylst) > 1:
            d.disco((bus[col], y0), 0.9, "FILI")
    for ramo, rr in rami.items():
        y0, y1 = rr["ys"][-1] - 4.5, rr["ys"][0] + 4.5
        rg = (y1 - y0) / 2
        d.arco((245 + rg, (y0 + y1) / 2), rg, 90, 270)
        d.arco((300 - rg, (y0 + y1) / 2), rg, 270, 90)
        d.linea((245 + rg, y1), (300 - rg, y1), "GUAINA")
        d.linea((245 + rg, y0), (300 - rg, y0), "GUAINA")
        d.rett(322, y0 - 0.5, 332, y1 + 0.5)
        for (pin, *_), y in zip(rr["fili"], rr["ys"]):
            d.testo(pin, 327, y - 1.0, 2.2, "TESTI", "C", "ISOCP")
        d.testo(rr["sigla"], 327, y1 + 4.5, 3.0, "SIGLE", "C")
        vista_frontale(d, 366, (y0 + y1) / 2, 3, ["1", "3", "4"], 11)
        cy = (y0 + y1) / 2
        d.testo(f"M8 femmina 3 poli - ramo {ramo}", 366, (cy + 12.5) if ramo == "A" else (cy - 15), 2.2, "TESTI", "C")
        d.testo(f"Ramo {ramo}: cavo PUR 3x0,34 L 300 mm", 272.5, y1 + 3, 2.0, "TESTI", "C")
    t = spec["cavo"]
    w = larg(t, 2.5)
    d.testo(t, 146.5, 226, 2.5, "TESTI", "C")
    d.linea((146.5 - w / 2, 225.2), (146.5 + w / 2, 225.2), "TESTI")
    d.linea((146.5, 225.2), (146.5, 217.5), "TESTI")
    d.freccia((146.5, 215), (0, -1), "TESTI")
    quota_orizzontale(d, 72, 192, 170, "L = 1000 mm %%p10", 183)
    tabella(d, 20, 120, [14, 18, 32, 16, 16],
            [("M12", "COLORE", "FUNZIONE", "M8 A", "M8 B"), ("1", "BN", "+24 V", "1", "1"),
             ("2", "WH", "ingresso B", "-", "4"), ("3", "BU", "0 V", "3", "3"), ("4", "BK", "ingresso A", "4", "-")],
            "COLLEGAMENTI", 7.0)
    tabella(d, 135, 120, [10, 14, 80, 32], [("POS", "Q.TA", "DESCRIZIONE", "CODICE")] + list(spec["distinta"]),
            "DISTINTA MATERIALI", 7.0)
    note(d, spec, 305, 120)


# -- schema elettrico: ingressi digitali -----------------------------------------------------------------------
def _sensore(d: Disegno, x, y, sigla, descr):
    """Sensore induttivo PNP a 3 fili: quadrato con il rombo, BN sopra, BU e BK sotto."""
    d.rett(x - 7, y - 9, x + 7, y + 9, "SIMBOLI")
    d.poly([(x, y + 5), (x + 5, y), (x, y - 5), (x - 5, y)], "SIMBOLI", True)
    d.linea((x - 3.5, y - 2), (x + 3.5, y - 2), "SIMBOLI")
    d.testo(sigla, x - 9, y + 5.5, 2.5, "SIGLE", "R")
    d.testo("PNP", x - 9, y + 1.5, 1.8, "TESTI", "R")
    for k, riga in enumerate(descr.split("|")):
        d.testo(riga, x - 2, y + 27 - 3.2 * k, 1.9, "TESTI", "L")


def schema_ingressi(d: Disegno, spec: dict, blocco: bool = True) -> None:
    cornice(d, spec, blocco)
    yl, ym = 262.0, 188.0
    d.testo("L+  24 V DC", 22, yl + 1.5, 2.5, "SIGLE")
    d.testo("M  0 V", 22, ym + 1.5, 2.5, "SIGLE")
    d.rett(22, 205, 52, 240, "SIMBOLI")
    d.testo("-G1", 37, 243, 3.0, "SIGLE", "C")
    d.testo("230 V AC", 37, 230, 2.0, "TESTI", "C")
    d.testo("24 V DC", 37, 224, 2.0, "TESTI", "C")
    d.testo("5 A", 37, 218, 2.0, "TESTI", "C")
    d.linea((47, 240), (47, yl), "FILI")
    d.linea((47, 205), (47, ym), "FILI")
    d.testo("+", 49, 241, 2.0)
    d.testo("-", 49, 201, 2.0)
    d.rett(62, yl - 2, 72, yl + 2, "SIMBOLI")
    d.linea((60, yl), (74, yl), "SIMBOLI")
    d.testo("-F2", 67, yl + 4, 2.5, "SIGLE", "C")
    d.testo("2 A", 67, yl - 6, 1.8, "TESTI", "C")
    d.linea((47, yl), (60, yl), "FILI")
    d.linea((74, yl), (400, yl), "FILI")
    d.linea((47, ym), (400, ym), "FILI")
    d.rett(90, 92, 400, 126, "SIMBOLI")
    d.testo("-A1", 87, 107, 3.0, "SIGLE", "R")
    d.testo("Modulo 8 ingressi digitali 24 V DC", 245, 98, 2.5, "TESTI", "C")
    for x, nome, y in ((96, "L+", yl), (102, "M", ym)):
        d.linea((x, y), (x, 126), "FILI")
        d.disco((x, y), 0.9, "FILI")
        d.cerchio((x, 126), 1.0, "SIMBOLI")
        d.testo(nome, x, 120, 1.8, "TESTI", "C")
    for k, (sigla, descr) in enumerate(spec["sensori"]):
        x = 130 + 36 * k
        ys = 215.0
        _sensore(d, x, ys, sigla, descr)
        d.linea((x - 4, ys + 9), (x - 4, yl), "FILI", COLORI["BN"])
        d.disco((x - 4, yl), 0.9, "FILI")
        d.linea((x - 4, ys - 9), (x - 4, ym), "FILI", COLORI["BU"])
        d.disco((x - 4, ym), 0.9, "FILI")
        d.testo("BN", x - 5, ys + 12, 1.6, "TESTI", "R")
        d.testo("BU", x - 5, ys - 13, 1.6, "TESTI", "R")
        d.testo("BK", x + 5, ys - 13, 1.6, "TESTI", "L")
        d.linea((x + 4, ys - 9), (x + 4, 166), "FILI", COLORI["BK"])
        d.rett(x + 1, 158, x + 7, 166, "SIMBOLI")
        d.cerchio((x + 4, 162), 1.0, "SIMBOLI")
        d.testo(str(k + 1), x + 8.5, 160.8, 2.0, "TESTI", "L", "ISOCP")
        d.linea((x + 4, 158), (x + 4, 126), "FILI", COLORI["BK"])
        d.cerchio((x + 4, 126), 1.0, "SIMBOLI")
        d.testo(f"I0.{k}", x + 4, 120, 1.8, "TESTI", "C")
        d.testo(f"{1001 + k}", x + 2.5, 140, 2.0, "TESTI", "R", "ISOCP", 90)
    d.testo("-X2", 125, 160.5, 3.0, "SIGLE", "R")
    if spec.get("rimando"):            # rimando accanto all'uscita del primo sensore
        for k, riga in enumerate(spec["rimando"].split("|")):
            d.testo(riga, 136.5, 151 - 3.4 * k, 1.6, "QUOTE", "L")
    note(d, spec, 22, 72)


# -- copertina con la lista dei fogli --------------------------------------------------------------------------
def copertina(d: Disegno, spec: dict, fogli: list, blocco: bool = True) -> None:
    cornice(d, spec, blocco)
    grande = spec.get("grande", "SET CABLAGGI")
    w = larg(grande, 12.0)
    d.testo(grande, 210, 238, 12.0, "SIGLE", "C")
    d.linea((210 - w / 2, 233), (210 + w / 2, 233), "TESTI")
    d.testo(spec.get("medio", ""), 210, 222, 6.0, "TESTI", "C")
    righe = [("FOGLIO", "CODICE", "DESCRIZIONE", "REV.")] + [(str(f["foglio"]), f.get("codice", ""), f["titolo"], "0")
                                                           for f in fogli]
    tabella(d, 95, 190, [20, 55, 125, 20], righe, "LISTA FOGLI", 8.0)
    d.testo("Disegni generati da ETCAD come esempio: nessun dato reale.", 210, 60, 2.5, "TESTI", "C")


# -- demo nella vista ------------------------------------------------------------------------------------------
def salva(doc, cartella: str, nome: str) -> dict:
    """Salva il DXF e, se c'e' un convertitore (ODA File Converter o SPAC), il DWG R2018."""
    os.makedirs(cartella, exist_ok=True)
    dxf = os.path.join(cartella, nome + ".dxf")
    doc.saveas(dxf)
    out = {"dxf": dxf}
    try:
        from .. import dwg_io
        dwg_io.dxf_a_dwg([dxf], cartella, "2018")
        dwg = os.path.splitext(dxf)[0] + ".dwg"
        if os.path.exists(dwg):
            out["dwg"] = dwg
    except Exception as ex:  # noqa: BLE001 - senza ODA/SPAC resta il DXF
        out["errore_dwg"] = str(ex)
    return out


def esegui(cartella: str = "") -> dict:
    """Disegna la demo nella vista (se aperta) e salva DXF e DWG nella cartella (se data)."""
    from . import attiva, fuoco, nota, pensa, scena, svuota
    d = Disegno()
    viva = attiva()
    if viva:
        svuota(FOGLIO)
        pensa("Disegno da zero la scheda di un cavo: sensore M12 a 4 poli collegato alla morsettiera -X2, "
              "con tabella dei collegamenti, distinta e quota della lunghezza.")
    n = 0
    for pensiero, titolo in passi(d):
        n += 1
        if viva:
            pensa(pensiero)
            scena(FOGLIO, d.doc, titolo)
    out = {"passi": n, "entita": len(d.msp)}
    if cartella:
        out.update(salva(d.doc, cartella, "Demo cavo sensore M12"))
        if viva:
            nota("Salvato il DWG R2018" if "dwg" in out else "Salvato il DXF")
    if viva:
        pensa("Fatto: la scheda e' completa. Ogni oggetto e' un'entita' vera del DWG, modificabile in qualsiasi CAD.")
        fuoco(FOGLIO, (0, 0, 420, 297))
    return out
