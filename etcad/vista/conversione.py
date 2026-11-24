"""Scena della vista dal vivo: disegni convertiti in oggetti JSON leggeri.

Sorgenti: file DXF/DWG, documenti ezdxf, liste di Ent di pdf2dwg (gli script
di modifica). Ogni oggetto e' un'entita' del disegno, coordinate in mm:

  {"id": firma, "t": tipo DXF, "ly": layer, "c": "#rrggbb" o None (primo piano),
   "lw": spessore mm, "d": tratteggio [trattino, vuoto, ...] o None,
   "p": [[x, y, x, y, ...], ...]   polilinee gia' appiattite
   "f": [[x, y, ...], ...]          poligoni pieni (HATCH/SOLID)
   "x": testo {"s", "x", "y", "h", "r", "wf", "ob", "al", "va", "shx", "fam"}
   "img": {"file": percorso, "q": [x0, y0, x1, y1]}
   "b": [x0, y0, x1, y1]}

I testi SHX (romans, isocp...) li disegna il browser con i glifi Hershey
(stessa resa delle anteprime PDF); i TrueType con il font di sistema.
`confronta` trova cosa e' stato aggiunto, tolto, spostato o riscritto.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict

from .. import hershey

ND = 2  # decimali delle coordinate (0,01 mm)
ERRORI: list[str] = []  # ultime entita' non convertite (per capire cosa manca nella vista)
FONT_FAMIGLIA = [  # (regex sul file del font, famiglia CSS, grassetto)
    (r"arialbd|arial.*bold", "Arial", True),
    (r"swissb", "Arial", True),
    (r"isocpeur", "ISOCPEUR", False),
    (r"swiss|arial", "Arial", False),
    (r"times", "Times New Roman", False),
    (r"cour", "Courier New", False),
    (r"calibri", "Calibri", False),
    (r"leelawadee", "Leelawadee UI", False),
]


def _r(v: float) -> float:
    return round(float(v), ND)


def _piatto(pts) -> list:
    out = []
    for p in pts:
        out += [_r(p[0]), _r(p[1])]
    return out


_FILE_TTF = {  # famiglia CSS -> file (il primo che esiste); ISOCPEUR manca spesso: come il browser, Arial Narrow
    ("Arial", False): ["arial.ttf"], ("Arial", True): ["arialbd.ttf", "arial.ttf"],
    ("ISOCPEUR", False): ["isocpeur.ttf", "ARIALN.TTF", "arial.ttf"],
    ("Times New Roman", False): ["times.ttf"], ("Courier New", False): ["cour.ttf"],
    ("Calibri", False): ["calibri.ttf"], ("Leelawadee UI", False): ["LeelawUI.ttf", "arial.ttf"],
}


@functools.lru_cache(maxsize=None)
def _font_pil(fam: str, bold: bool):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    cartelle = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    for nome in _FILE_TTF.get((fam, bold), _FILE_TTF.get((fam, False), ["arial.ttf"])):
        for c in cartelle:
            p = os.path.join(c, nome)
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, 100)
                except OSError:
                    pass
    return None


def larghezza_ttf(s: str, h: float, wf: float = 1.0, fam: str = "Arial", bold: bool = False) -> float:
    """Larghezza in mm di un testo TrueType alto h (altezza delle maiuscole, come nel browser)."""
    f = _font_pil(fam, bold)
    if f is None:
        return len(s) * h * 0.72 * wf
    return f.getlength(s) / 100.0 * h / 0.716 * wf


def famiglia(font: str) -> tuple[str, bool]:
    f = (font or "").lower()
    for rx, fam, bold in FONT_FAMIGLIA:
        if re.search(rx, f):
            return fam, bold
    return "Arial", False


def _testo_codici(s: str) -> str:
    for k, v in (("%%d", "°"), ("%%D", "°"), ("%%p", "±"), ("%%P", "±"),
                 ("%%c", "Ø"), ("%%C", "Ø"), ("%%%", "%")):
        s = s.replace(k, v)
    return re.sub(r"%%[uUoO]", "", s)          # sottolineato / sopralineato: interruttori, non testo


def oggetto(t: str, p=(), f=(), x: dict | None = None, ly: str = "0", c=None,
            lw: float = 0.25, d=None, img: dict | None = None) -> dict:
    o = {"t": t, "ly": ly, "c": c, "lw": round(lw, 3), "d": d,
         "p": [q for q in p if len(q) >= 4], "f": [q for q in f if len(q) >= 6]}
    if x:
        o["x"] = x
    if img:
        o["img"] = img
    o["b"] = _bbox(o)
    o["id"] = firma(o)
    return o


def firma(o: dict) -> str:
    chiave = [o["t"], o["ly"], o["c"], o["lw"], o["d"], o["p"], o["f"], o.get("x"),
              (o.get("img") or {}).get("q")]
    return hashlib.md5(json.dumps(chiave, separators=(",", ":"), ensure_ascii=False)
                       .encode()).hexdigest()[:16]


def _testo_bbox(x: dict) -> list:
    h, s = x["h"], x["s"]
    righe = s.split("\n")
    if x["shx"]:
        w = max(hershey.larghezza(r, h, x["wf"]) for r in righe)
    else:
        w = max(larghezza_ttf(r, h, x["wf"], x.get("fam", "Arial"), x.get("bold", False)) for r in righe)
    w = max(w, h * 0.3)
    dx0 = {"L": 0.0, "C": -w / 2, "R": -w}.get(x["al"], 0.0)
    dy0 = {0: 0.0, 1: 0.0, 2: -h / 2, 3: -h}.get(x["va"], 0.0)
    y_basso = -0.35 * h - (len(righe) - 1) * 1.667 * h
    loc = [(dx0, dy0 + y_basso), (dx0 + w, dy0 + y_basso), (dx0 + w, dy0 + h), (dx0, dy0 + h)]
    ca, sa = math.cos(math.radians(x["r"])), math.sin(math.radians(x["r"]))
    pts = [(x["x"] + u * ca - v * sa, x["y"] + u * sa + v * ca) for u, v in loc]
    return [_r(min(q[0] for q in pts)), _r(min(q[1] for q in pts)),
            _r(max(q[0] for q in pts)), _r(max(q[1] for q in pts))]


def _bbox(o: dict) -> list:
    xs, ys = [], []
    for q in list(o["p"]) + list(o["f"]):
        xs += q[0::2]
        ys += q[1::2]
    if o.get("img"):
        q = o["img"]["q"]
        xs += [q[0], q[2]]
        ys += [q[1], q[3]]
    if o.get("x"):
        b = _testo_bbox(o["x"])
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    if not xs:
        return [0, 0, 0, 0]
    return [min(xs), min(ys), max(xs), max(ys)]


def testo(s: str, x: float, y: float, h: float, rot: float = 0.0, wf: float = 1.0,
          obl: float = 0.0, al: str = "L", va: int = 0, font: str = "romans.shx") -> dict:
    """Parametri di un testo. font = file del font dello stile (romans.shx, arial.ttf...)."""
    font = (font or "").lower()
    shx = not font.endswith((".ttf", ".otf", ".ttc"))
    if shx:
        wf *= hershey.SCALA_X.get(font if font.endswith(".shx") else font + ".shx", 1.0)
        if font.startswith("italic") and not obl:
            obl = 15.0
        fam, bold = "", False
    else:
        fam, bold = famiglia(font)
    out = {"s": _testo_codici(s), "x": _r(x), "y": _r(y), "h": round(h, 3), "r": round(rot, 2),
           "wf": round(wf, 3), "ob": round(obl, 2), "al": al, "va": int(va), "shx": shx}
    if not shx:
        out["fam"] = fam
        if bold:
            out["bold"] = True
    return out


# ---------------------------------------------------------------------------
# geometria
# ---------------------------------------------------------------------------

def arco(c, r, a0, a1, passo_mm: float = 0.25) -> list:
    """Punti dell'arco (gradi, antiorario da a0 ad a1)."""
    while a1 <= a0:
        a1 += 360.0
    n = max(4, min(256, int(math.radians(a1 - a0) * r / passo_mm) + 1))
    return [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def _bulge(p0, p1, b) -> list:
    """Punti fra p0 e p1 (escluso p0) per un tratto di polilinea con bulge b."""
    if abs(b) < 1e-9:
        return [p1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    corda = math.hypot(dx, dy)
    if corda < 1e-9:
        return [p1]
    teta = 4 * math.atan(b)                       # angolo al centro, con segno (+ = antiorario)
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    nx, ny = -dy / corda, dx / corda              # normale a sinistra di p0->p1
    hc = (corda / 2) / math.tan(teta / 2)         # dal punto medio al centro, lungo la normale
    cx, cy = mx + nx * hc, my + ny * hc
    r = math.hypot(p0[0] - cx, p0[1] - cy)
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    n = max(4, min(128, int(abs(teta) * r / 0.25) + 1))
    return [(cx + r * math.cos(a0 + teta * k / n), cy + r * math.sin(a0 + teta * k / n)) for k in range(1, n + 1)]


def polilinea_bulge(pts, chiusa: bool = False) -> list:
    """pts = [(x, y, bulge), ...] -> punti appiattiti."""
    if not pts:
        return []
    q = list(pts) + ([pts[0]] if chiusa else [])
    out = [(q[0][0], q[0][1])]
    for a, b in zip(q, q[1:]):
        out += _bulge((a[0], a[1]), (b[0], b[1]), a[2] if len(a) > 2 else 0.0)
    return out


def _bezier(c0, c1, c2, c3, n: int = 12) -> list:
    out = []
    for k in range(1, n + 1):
        t = k / n
        u = 1 - t
        out.append((u ** 3 * c0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * c3[0],
                    u ** 3 * c0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * c3[1]))
    return out


def tratteggio_da_nome(nome: str):
    """TRATT_2p79_2p19 (pdf2dwg) -> [2.79, 2.19]."""
    if not nome or not nome.upper().startswith("TRATT_"):
        return None
    try:
        v = [float(t.replace("p", ".")) for t in nome[6:].split("_") if t]
    except ValueError:
        return None
    return v or None


# ---------------------------------------------------------------------------
# Ent di pdf2dwg (script di modifica)
# ---------------------------------------------------------------------------

def da_ent(ents) -> list[dict]:
    """Lista di pdf2dwg.Ent -> oggetti (le entita' del blocco cornice comprese, se passate)."""
    from .. import pdf2dwg as P
    font_stile = dict(P.STILI_SHX)
    for _, stile, ttf in P.TTF:
        font_stile.setdefault(stile, ttf)
    out = []
    for e in ents:
        d = e.dati
        c = "#%02x%02x%02x" % tuple(e.rgb) if e.rgb else None
        lw = (e.lw or 0) / 100.0
        tr = tratteggio_da_nome(e.linetype)
        kw = {"ly": e.layer, "c": c, "lw": lw, "d": tr}
        if e.tipo == "LINE":
            out.append(oggetto("LINE", [_piatto([d["p0"], d["p1"]])], **kw))
        elif e.tipo == "LWPOLYLINE":
            pts = [(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in d["pts"]]
            out.append(oggetto("LWPOLYLINE", [_piatto(polilinea_bulge(pts, d.get("chiusa", False)))], **kw))
        elif e.tipo == "CIRCLE":
            out.append(oggetto("CIRCLE", [_piatto(arco(d["c"], d["r"], 0, 360))], **kw))
        elif e.tipo == "ARC":
            out.append(oggetto("ARC", [_piatto(arco(d["c"], d["r"], d["a0"], d["a1"]))], **kw))
        elif e.tipo == "SPLINE":
            cp = [tuple(p[:2]) for p in d["ctrl"]]
            pts = [cp[0]]
            for k in range(d.get("n", (len(cp) - 1) // 3)):
                pts += _bezier(*cp[3 * k:3 * k + 4])
            out.append(oggetto("SPLINE", [_piatto(pts)], **kw))
        elif e.tipo == "HATCH":
            kw["lw"] = 0.0
            out.append(oggetto("HATCH", f=[_piatto(a) for a in d["anelli"]], **kw))
        elif e.tipo == "TEXT":
            al, xa = "L", d["x"]
            if d.get("allinea") == "C" and "xc" in d:
                al, xa = "C", d["xc"]
            elif d.get("allinea") == "R" and "xr" in d:
                al, xa = "R", d["xr"]
            font = font_stile.get(d.get("stile", "ROMANS"), "arial.ttf")
            x = testo(P.testo_cad(d["testo"], d.get("stile", "")), xa, d["y"], d["h"], d.get("rot", 0.0),
                      d.get("wf", 1.0), d.get("obl", 0.0), al, 0, font)
            out.append(oggetto("TEXT", x=x, **kw))
    return out


# ---------------------------------------------------------------------------
# DXF / DWG / documento ezdxf
# ---------------------------------------------------------------------------

class _Risolutore:
    """Colore, spessore e tratteggio effettivi (DaLayer/DaBlocco)."""

    def __init__(self, doc):
        self.doc = doc
        self.ltscale = float(doc.header.get("$LTSCALE", 1.0) or 1.0)
        self._lt: dict = {}

    def layer(self, nome):
        try:
            return self.doc.layers.get(nome)
        except Exception:  # noqa: BLE001
            return None

    def colore(self, e):
        from ezdxf import colors
        if e.dxf.hasattr("true_color"):
            return "#%02x%02x%02x" % colors.int2rgb(e.dxf.true_color)
        aci = e.dxf.get("color", 256)
        if aci == 256:
            ly = self.layer(e.dxf.get("layer", "0"))
            if ly is not None:
                if ly.dxf.hasattr("true_color"):
                    return "#%02x%02x%02x" % colors.int2rgb(ly.dxf.true_color)
                aci = abs(ly.dxf.get("color", 7))
            else:
                aci = 7
        if aci in (0, 7, 255) or aci > 255:
            return None
        return "#%02x%02x%02x" % colors.aci2rgb(aci)

    def spessore(self, e) -> float:
        lw = e.dxf.get("lineweight", -1)
        if lw == -1:
            ly = self.layer(e.dxf.get("layer", "0"))
            lw = ly.dxf.get("lineweight", -3) if ly is not None else -3
        return 0.25 if lw < 0 else lw / 100.0

    def tratteggio(self, e):
        nome = e.dxf.get("linetype", "BYLAYER")
        if nome.upper() == "BYLAYER":
            ly = self.layer(e.dxf.get("layer", "0"))
            nome = ly.dxf.get("linetype", "Continuous") if ly is not None else "Continuous"
        if nome.upper() in ("BYBLOCK", "CONTINUOUS"):
            return None
        if nome not in self._lt:
            pat = None
            try:
                v = [abs(float(t)) for t in self.doc.linetypes.get(nome).simplified_line_pattern()]
                pat = [round(t * self.ltscale, 3) for t in v] if v and sum(v) > 0 else None
            except Exception:  # noqa: BLE001
                pat = tratteggio_da_nome(nome)
            self._lt[nome] = pat
        pat = self._lt[nome]
        if pat and e.dxf.get("ltscale", 1.0) not in (1.0, None):
            pat = [round(t * e.dxf.ltscale, 3) for t in pat]
        return pat


def _font_stile(doc, nome: str) -> str:
    try:
        st = doc.styles.get(nome)
        return (st.dxf.font or "").lower() or "txt"
    except Exception:  # noqa: BLE001
        return "txt"


def _cartella_doc(doc) -> str:
    fn = getattr(doc, "filename", None) or ""
    return os.path.dirname(os.path.abspath(fn)) if fn else ""


def da_doc(doc, layer: str | None = None, cartella: str = "", tieni=None) -> list[dict]:
    """Spazio modello di un documento ezdxf -> oggetti (blocchi e quote scomposti).

    Ogni oggetto porta l'handle dell'entita' del DWG da cui viene ("hd") e,
    se viene da un blocco, il nome del blocco ("blk"): una selezione fatta
    nella vista si ritrova cosi' nel DWG. tieni(e): filtro sulle entita'.
    """
    ris = _Risolutore(doc)
    cartella = cartella or _cartella_doc(doc)
    out = []
    for e in doc.modelspace():
        if tieni is None or tieni(e):
            out += _oggetti_entita(e, ris, doc, cartella, layer)
    return out


def _oggetti_entita(top, ris, doc, cartella: str, layer: str | None = None,
                    valori: dict | None = None) -> list[dict]:
    """Un'entita' dello spazio modello (scomposta se e' un blocco o una quota) -> oggetti."""
    from ezdxf.disassemble import recursive_decompose
    hd = top.dxf.get("handle")
    blk = top.dxf.name if top.dxftype() == "INSERT" else None
    ly_top = top.dxf.get("layer", "0")
    out = []
    try:
        parti = list(recursive_decompose([top]))
    except Exception as ex:  # noqa: BLE001
        ERRORI.append(f"{top.dxftype()} {hd}: {ex}")
        del ERRORI[:-50]
        return out
    for e in parti:
        if blk and e.dxf.get("layer", "0") == "0":
            e.dxf.layer = ly_top            # come in AutoCAD: il layer 0 del blocco prende quello dell'INSERT
        if layer and e.dxf.get("layer", "0") != layer:
            continue
        for o in _converti(e, ris, doc, cartella, valori):
            if hd:
                o["hd"] = hd
            if blk:
                o["blk"] = blk
            out.append(o)
    return out


def _converti(e, ris, doc, cartella: str, valori: dict | None = None) -> list[dict]:
    from ezdxf import path as ezpath
    out = []
    t = e.dxftype()
    ly = e.dxf.get("layer", "0")
    if True:
        try:
            kw = {"ly": ly, "c": ris.colore(e), "lw": ris.spessore(e), "d": ris.tratteggio(e)}
            if t in ("TEXT", "ATTRIB", "ATTDEF"):
                if t == "ATTDEF":
                    return out
                s = e.dxf.get("text", "")
                if t == "ATTRIB" and not s.strip() and valori and e.dxf.get("tag") in valori:
                    s = str(valori[e.dxf.tag])          # campi che SPAC riempie foglio per foglio
                if not s.strip():
                    return out
                if t == "ATTRIB" and e.dxf.get("flags", 0) & 1:      # attributo invisibile
                    return out
                x, y, al, va = hershey._parametri(e)
                tx = testo(s, x, y, e.dxf.get("height", 2.5), e.dxf.get("rotation", 0.0),
                           e.dxf.get("width", 1.0), e.dxf.get("oblique", 0.0), al, va,
                           _font_stile(doc, e.dxf.get("style", "Standard")))
                kw["d"] = None
                o = oggetto("TEXT", x=tx, **kw)
                if t == "ATTRIB":
                    o["tag"] = e.dxf.get("tag", "")
                out.append(o)
            elif t == "MTEXT":
                s = e.plain_text()
                if not s.strip():
                    return out
                p = e.dxf.insert
                att = e.dxf.get("attachment_point", 1)
                al = {1: "L", 2: "C", 3: "R"}.get((att - 1) % 3 + 1, "L")
                h = e.dxf.get("char_height", 2.5)
                righe = s.split("\n")
                n_r = len(righe) - 1
                if att <= 3:                                   # in alto
                    y0 = p[1] - h
                elif att <= 6:                                 # al centro
                    y0 = p[1] + n_r * 1.667 * h / 2 - h / 2
                else:                                          # in basso
                    y0 = p[1] + n_r * 1.667 * h
                rot = e.dxf.get("rotation", 0.0)
                if e.dxf.hasattr("text_direction"):
                    td = e.dxf.text_direction
                    rot = math.degrees(math.atan2(td[1], td[0]))
                tx = testo("\n".join(righe), p[0], y0, h, rot, 1.0, 0.0, al, 0,
                           _font_stile(doc, e.dxf.get("style", "Standard")))
                kw["d"] = None
                out.append(oggetto("MTEXT", x=tx, **kw))
            elif t == "LINE":
                out.append(oggetto(t, [_piatto([e.dxf.start, e.dxf.end])], **kw))
            elif t == "HATCH" or t == "MPOLYGON":
                pieno = bool(e.dxf.get("solid_fill", 0))
                sub = []
                for pa in ezpath.from_hatch(e):
                    for sp in pa.sub_paths():
                        sub.append(_piatto(sp.flattening(0.05)))
                kw["d"] = None
                if pieno:
                    kw["lw"] = 0.0
                    out.append(oggetto(t, f=sub, **kw))
                else:
                    out.append(oggetto(t, p=sub, **kw))
            elif t in ("SOLID", "TRACE", "3DFACE"):
                v = [e.dxf.get(k) for k in ("vtx0", "vtx1", "vtx3", "vtx2") if e.dxf.hasattr(k)]
                kw["lw"] = 0.0
                out.append(oggetto(t, f=[_piatto(v)], **kw))
            elif t == "IMAGE":
                q = [tuple(v)[:2] for v in e.boundary_path_wcs()]
                fn = e.image_def.dxf.filename if e.image_def else ""
                if fn and not os.path.isabs(fn):
                    fn = os.path.join(cartella, fn)
                box = [_r(min(v[0] for v in q)), _r(min(v[1] for v in q)),
                       _r(max(v[0] for v in q)), _r(max(v[1] for v in q))]
                kw["d"] = None
                out.append(oggetto(t, img={"file": os.path.abspath(fn) if fn else "", "q": box}, **kw))
            elif t in ("POINT", "VIEWPORT", "ATTRIB"):
                return out
            else:
                pa = ezpath.make_path(e)
                sub = [_piatto(sp.flattening(0.05)) for sp in pa.sub_paths()] if pa.has_sub_paths \
                    else [_piatto(pa.flattening(0.05))]
                if any(len(s) >= 4 for s in sub):
                    out.append(oggetto(t, sub, **kw))
        except Exception as ex:  # noqa: BLE001 - un'entita' strana non ferma la vista
            ERRORI.append(f"{t} {e.dxf.get('handle', '?')}: {ex}")
            del ERRORI[:-50]
    return out


def da_file(percorso: str, layer: str | None = None) -> list[dict]:
    """DXF o DWG (DWG convertito con ODA/SPAC, in cache) -> oggetti."""
    import ezdxf
    from .. import render
    p = os.path.abspath(percorso)
    if p.lower().endswith(".dwg"):
        doc = ezdxf.readfile(render.dxf_da_dwg(p))
    else:
        doc = ezdxf.readfile(p)
    return da_doc(doc, layer, cartella=os.path.dirname(p))


# ---------------------------------------------------------------------------
# multifoglio SPAC: un DWG, un layer per foglio, mappa in dbSPAC.PlotSequence
# ---------------------------------------------------------------------------

def sequenza_spac(percorso: str, db: str = "") -> list[dict] | None:
    """Fogli del multifoglio (PlotSequence del dbSPAC.db accanto al DWG, o db): None se non e' un multifoglio."""
    nome = os.path.splitext(os.path.basename(percorso))[0]
    cart = os.path.dirname(os.path.abspath(percorso))
    cand = [db] if db else [os.path.join(cart, "dbSPAC.db"), os.path.join(os.path.dirname(cart), "dbSPAC.db")]
    for c in cand:
        if not c or not os.path.isfile(c):
            continue
        try:
            con = sqlite3.connect("file:" + c.replace("\\", "/") + "?mode=ro", uri=True)
            try:
                righe = con.execute("SELECT foglio, descrizione, layer FROM PlotSequence "
                                    "WHERE multifoglio=? ORDER BY riga", (nome,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if righe:
            return [{"foglio": str(f), "descrizione": d or "", "layer": str(ly or f), "db": c}
                    for f, d, ly in righe]
    return None


def _fogli_per_layer(doc) -> list[dict] | None:
    """Senza database: layer con nome numerico = fogli (come li salva SPAC)."""
    n = Counter(e.dxf.get("layer", "0") for e in doc.modelspace())
    num = sorted((ly for ly in n if ly.isdigit() and ly != "0"), key=int)
    if len(num) < 2:
        return None
    return [{"foglio": ly, "descrizione": "", "layer": ly} for ly in num]


def _fogli_per_cornice(doc) -> list[dict] | None:
    """DWG con i fogli affiancati (es. _tutti_i_fogli di pdf2dwg): un foglio per cornice ripetuta."""
    from ezdxf import bbox
    ins = defaultdict(list)
    for e in doc.modelspace():
        if e.dxftype() == "INSERT":
            ins[e.dxf.name].append(e)
    migliore = None
    for nome, lst in ins.items():
        if len(lst) < 2:
            continue
        zone = []
        for e in lst:
            b = bbox.extents([e], fast=True)
            if b.has_data and b.size.x >= 100 and b.size.y >= 60:
                zone.append((b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y, e))
        if len(zone) >= 2 and (migliore is None or len(zone) > len(migliore)):
            migliore = zone
    if not migliore:
        return None
    migliore.sort(key=lambda z: (round(-z[3] / 50), z[0]))
    out = []
    for k, (x0, y0, x1, y1, e) in enumerate(migliore, 1):
        att = {a.dxf.tag.upper(): a.dxf.text for a in getattr(e, "attribs", [])}
        out.append({"foglio": att.get("FOGLIO") or str(k), "descrizione": att.get("OGGETTO", ""),
                    "zona": [x0, y0, x1, y1], "cornice": e.dxf.handle})
    return out


def fogli_doc(doc, percorso: str = "", db: str = "", cartella: str = "") -> tuple[str, list[dict]] | None:
    """Divide un DWG in fogli. -> (tipo, [{"foglio", "descrizione", "layer"|"zona", "oggetti"}]) o None.

    tipo "spac": multifoglio SPAC (fogli sovrapposti, uno per layer; gli INSERT
    del layer 0, cioe' la cornice, valgono per tutti i fogli; i layer N_REV_xx
    no, come in SPAC). Nella cornice condivisa i campi vuoti NUMERO_DI_FOGLIO e
    FOGLIO_SEGUENTE vengono riempiti foglio per foglio, come in stampa.
    tipo "layer": layer numerici senza database. tipo "cornici": fogli affiancati.
    """
    from ezdxf import bbox
    ris = _Risolutore(doc)
    cartella = cartella or _cartella_doc(doc)
    righe = sequenza_spac(percorso, db) if percorso else None
    tipo = "spac"
    if not righe:
        righe, tipo = _fogli_per_layer(doc), "layer"
    if not righe:
        righe, tipo = _fogli_per_cornice(doc), "cornici"
    if not righe:
        return None
    msp = doc.modelspace()
    out = []
    if tipo in ("spac", "layer"):
        condivisi, per_layer = [], defaultdict(list)
        for e in msp:
            ly = e.dxf.get("layer", "0")
            if ly == "0" and e.dxftype() == "INSERT":
                condivisi.append(e)
            else:
                per_layer[ly].append(e)
        for k, r in enumerate(righe):
            succ = righe[k + 1]["foglio"] if k + 1 < len(righe) else ""
            val = {"NUMERO_DI_FOGLIO": r["foglio"], "FOGLIO_SEGUENTE": succ}
            ogg = []
            for e in condivisi:
                ogg += _oggetti_entita(e, ris, doc, cartella, valori=val)
            for e in per_layer.get(r["layer"], []):
                ogg += _oggetti_entita(e, ris, doc, cartella)
            out.append({**r, "oggetti": ogg})
        return tipo, out
    # cornici affiancate: ogni entita' va al foglio che contiene il suo centro
    gruppi = [[] for _ in righe]
    for e in msp:
        b = bbox.extents([e], fast=True)
        if not b.has_data:
            continue
        cx, cy = (b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2
        k = min(range(len(righe)), key=lambda i: _distanza_zona(righe[i]["zona"], cx, cy))
        gruppi[k].append(e)
    for r, ents in zip(righe, gruppi):
        ogg = []
        for e in ents:
            ogg += _oggetti_entita(e, ris, doc, cartella)
        out.append({**r, "zona": [_r(v) for v in r["zona"]], "oggetti": ogg})
    return tipo, out


def _distanza_zona(z, x, y) -> float:
    return math.hypot(max(z[0] - x, 0, x - z[2]), max(z[1] - y, 0, y - z[3]))


def leggi_doc(percorso: str):
    import ezdxf
    from .. import render
    p = os.path.abspath(percorso)
    return ezdxf.readfile(render.dxf_da_dwg(p) if p.lower().endswith(".dwg") else p)


# ---------------------------------------------------------------------------
# analisi per la collaborazione con l'utente: zona, ricerca, controllo
# ---------------------------------------------------------------------------

def _tocca(b, z) -> bool:
    return not (b[2] < z[0] or b[0] > z[2] or b[3] < z[1] or b[1] > z[3])


def _dentro(b, z) -> bool:
    return b[0] >= z[0] and b[1] >= z[1] and b[2] <= z[2] and b[3] <= z[3]


def contenuto(oggetti: list[dict], zona=None, ids=None, max_testi: int = 120) -> dict:
    """Cosa c'e' in una zona (o in una lista di id): conteggi, testi con posizione, handle del DWG."""
    if ids is not None:
        ids = set(ids)
        sel = [o for o in oggetti if o["id"] in ids]
    else:
        sel = [o for o in oggetti if _tocca(o["b"], zona)]
    per_tipo, per_layer = Counter(o["t"] for o in sel), Counter(o["ly"] for o in sel)
    testi = []
    for o in sel:
        x = o.get("x")
        if x and len(testi) < max_testi:
            t = {"s": x["s"], "x": x["x"], "y": x["y"], "h": x["h"], "ly": o["ly"]}
            if x["r"]:
                t["rot"] = x["r"]
            if x["al"] != "L":
                t["allinea"] = x["al"]
            for k in ("hd", "tag", "blk"):
                if o.get(k):
                    t[k] = o[k]
            testi.append(t)
    testi.sort(key=lambda t: (-t["y"], t["x"]))
    hd = []
    for o in sel:
        if o.get("hd") and o["hd"] not in hd:
            hd.append(o["hd"])
    b = None
    for o in sel:
        b = o["b"] if b is None else [min(b[0], o["b"][0]), min(b[1], o["b"][1]),
                                      max(b[2], o["b"][2]), max(b[3], o["b"][3])]
    return {"oggetti": len(sel), "per_tipo": dict(per_tipo), "per_layer": dict(per_layer),
            "testi": testi, "testi_troncati": sum(1 for o in sel if o.get("x")) > len(testi),
            "handle": hd[:300], "estensione": b}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _testo_codici(s or "")).strip().lower()


def cerca(oggetti: list[dict], q: str, limite: int = 200) -> list[dict]:
    """Testi che contengono q (senza distinguere maiuscole; spazi multipli = uno)."""
    qn = _norm(q)
    out = []
    for o in oggetti:
        x = o.get("x")
        if x and qn and qn in _norm(x["s"]):
            r = {"s": x["s"], "x": x["x"], "y": x["y"], "h": x["h"], "b": o["b"], "ly": o["ly"], "id": o["id"]}
            if o.get("hd"):
                r["hd"] = o["hd"]
            out.append(r)
            if len(out) >= limite:
                break
    return out


def _poligono_testo(x: dict):
    """Nucleo del testo (dalla linea di base all'altezza delle maiuscole, un po' rientrato)."""
    from shapely.geometry import Polygon
    h, righe = x["h"], x["s"].split("\n")
    if x["shx"]:
        w = max(hershey.larghezza(r, h, x["wf"]) for r in righe)
    else:
        w = max(larghezza_ttf(r, h, x["wf"], x.get("fam", "Arial"), x.get("bold", False)) for r in righe)
    if w <= 0:
        return None
    dx0 = {"L": 0.0, "C": -w / 2, "R": -w}.get(x["al"], 0.0)
    dy0 = {0: 0.0, 1: 0.0, 2: -h / 2, 3: -h}.get(x["va"], 0.0)
    su, sv = min(0.12 * h, 0.25), min(0.1 * h, 0.2)
    v0, v1 = -(len(righe) - 1) * 1.667 * h + sv, h - sv
    loc = [(dx0 + su, dy0 + v0), (dx0 + w - su, dy0 + v0), (dx0 + w - su, dy0 + v1), (dx0 + su, dy0 + v1)]
    ca, sa = math.cos(math.radians(x["r"])), math.sin(math.radians(x["r"]))
    t = math.tan(math.radians(x.get("ob", 0.0)))
    return Polygon([(x["x"] + (u + v * t) * ca - v * sa, x["y"] + (u + v * t) * sa + v * ca) for u, v in loc])


def controlla(oggetti: list[dict], zona=None, limite: int = 150) -> list[dict]:
    """Possibili problemi grafici: testi sovrapposti fra loro, testi attraversati da una linea
    (anche il testo che esce dalla sua cella), testi doppi. Euristica: le larghezze dei font
    sono stimate, quindi i casi al limite vanno guardati."""
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    ogg = [o for o in oggetti if zona is None or _tocca(o["b"], zona)]
    testi, poli = [], []
    for o in ogg:
        x = o.get("x")
        if x and x["s"].strip():
            p = _poligono_testo(x)
            if p is not None and p.area > 0:
                testi.append(o)
                poli.append(p)
    if not poli:
        return []
    albero = STRtree(poli)
    out, visti = [], set()

    def corto(s):
        s = s.replace("\n", " ")
        return s if len(s) <= 40 else s[:38] + "…"
    # testi doppi o sovrapposti
    for i, p in enumerate(poli):
        for j in albero.query(p):
            j = int(j)
            if j <= i or (i, j) in visti:
                continue
            q = poli[j]
            if not p.intersects(q):
                continue
            a, b = testi[i]["x"], testi[j]["x"]
            area = p.intersection(q).area
            if area < 0.04 * min(p.area, q.area):
                continue
            visti.add((i, j))
            doppio = a["s"] == b["s"] and abs(a["x"] - b["x"]) < 0.3 and abs(a["y"] - b["y"]) < 0.3
            bb = [min(testi[i]["b"][0], testi[j]["b"][0]), min(testi[i]["b"][1], testi[j]["b"][1]),
                  max(testi[i]["b"][2], testi[j]["b"][2]), max(testi[i]["b"][3], testi[j]["b"][3])]
            out.append({"tipo": "doppio" if doppio else "testi",
                        "msg": f"«{corto(a['s'])}» doppio" if doppio else
                        f"«{corto(a['s'])}» e «{corto(b['s'])}» si sovrappongono",
                        "b": bb, "ids": [testi[i]["id"], testi[j]["id"]]})
    # testi attraversati da linee (una segnalazione per testo)
    toccati = defaultdict(list)
    for o in ogg:
        if o.get("x") or not o["p"]:
            continue
        for q in o["p"]:
            if len(q) < 4:
                continue
            ls = LineString(list(zip(q[0::2], q[1::2])))
            for j in albero.query(ls):
                j = int(j)
                if o in toccati[j]:
                    continue
                pj = poli[j]
                if pj.intersects(ls) and pj.intersection(ls).length >= 0.15:
                    toccati[j].append(o)
    for j, lst in toccati.items():
        if not lst:
            continue
        x = testi[j]["x"]
        chi = lst[0]["t"].lower() + (f" e altre {len(lst) - 1} linee" if len(lst) > 1 else "")
        out.append({"tipo": "linea", "msg": f"«{corto(x['s'])}» toccato da {chi} ({lst[0]['ly']})",
                    "b": testi[j]["b"], "ids": [testi[j]["id"]] + [o["id"] for o in lst]})
    out.sort(key=lambda r: (-round(r["b"][3]), r["b"][0]))
    return out[:limite]


# ---------------------------------------------------------------------------
# confronto fra due stati
# ---------------------------------------------------------------------------

def _forma(o: dict) -> tuple[str, float, float]:
    """Firma della geometria a meno di una traslazione, e la traslazione."""
    bx, by = o["b"][0], o["b"][1]

    def rel(q):
        return [_r(v - (bx if k % 2 == 0 else by)) for k, v in enumerate(q)]
    x = o.get("x")
    if x:
        x = dict(x, x=_r(x["x"] - bx), y=_r(x["y"] - by))
    chiave = [o["t"], o["ly"], o["c"], o["lw"], o["d"], [rel(q) for q in o["p"]],
              [rel(q) for q in o["f"]], x]
    return hashlib.md5(json.dumps(chiave, separators=(",", ":"), ensure_ascii=False)
                       .encode()).hexdigest()[:16], bx, by


def confronta(prima: list[dict], dopo: list[dict]) -> dict:
    """Differenze fra due stati dello stesso foglio.

    aggiunti: oggetti nuovi (nell'ordine di `dopo`, cioe' di creazione);
    tolti: id spariti; spostati: [{"da": id, "a": oggetto, "dx", "dy"}];
    modificati: testi riscritti nello stesso punto [{"da": id, "a": oggetto}].
    """
    resto = Counter(o["id"] for o in prima)
    aggiunti = []
    for o in dopo:
        if resto[o["id"]] > 0:
            resto[o["id"]] -= 1
        else:
            aggiunti.append(o)
    tolti = []
    usati = Counter()
    for o in prima:
        if usati[o["id"]] < resto[o["id"]]:
            usati[o["id"]] += 1
            tolti.append(o)
    # spostamenti: stessa forma, posizione diversa (si abbina il piu' vicino)
    per_forma = defaultdict(list)
    for o in tolti:
        k, bx, by = _forma(o)
        per_forma[k].append((o, bx, by))
    spostati, ancora = [], []
    for o in aggiunti:
        k, bx, by = _forma(o)
        cand = per_forma.get(k)
        if cand:
            j = min(range(len(cand)), key=lambda i: math.hypot(cand[i][1] - bx, cand[i][2] - by))
            v, vx, vy = cand.pop(j)
            spostati.append({"da": v["id"], "a": o, "dx": _r(bx - vx), "dy": _r(by - vy)})
        else:
            ancora.append(o)
    restano = [v for lst in per_forma.values() for v, _, _ in lst]
    # testi riscritti: stesso punto d'inserimento, stessa altezza
    modificati, nuovi = [], []
    tt = {}
    for v in restano:
        if v.get("x"):
            x = v["x"]
            tt.setdefault((x["x"], x["y"], x["h"], x["al"]), []).append(v)
    via = set()
    for o in ancora:
        x = o.get("x")
        lst = tt.get((x["x"], x["y"], x["h"], x["al"])) if x else None
        if lst:
            v = lst.pop(0)
            via.add(id(v))
            modificati.append({"da": v["id"], "a": o})
        else:
            nuovi.append(o)
    tolti_id = [v["id"] for v in restano if id(v) not in via]
    return {"aggiunti": nuovi, "tolti": tolti_id, "spostati": spostati, "modificati": modificati}


def glifi() -> dict:
    """Tabella dei glifi Hershey per il browser: {car: [L, R, tratti, avanzamento]}."""
    car = [chr(c) for c in range(32, 127)] + list("ØøàèéìòùÀÈÉÌÒÙ°±µ²³ç")
    out = {}
    for c in car:
        L, R, tratti = hershey._glifo(c)
        out[c] = [L, R, [[v for p in t for v in p] for t in tratti], hershey.avanzamento(c)]
    return {"glifi": out}
