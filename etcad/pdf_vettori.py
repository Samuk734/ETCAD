"""Estrazione vettoriale da PDF (PyMuPDF) in coordinate disegno.

Unita' di uscita: millimetri, origine in basso a sinistra, Y verso l'alto
(come lo spazio modello di SPAC/AutoCAD). 1 pt PDF = 25.4/72 mm; con
`scala` diversa da 1 si ricostruisce un disegno plottato in scala.

Il plotter di AutoCAD/SPAC (pdfplot*.hdi) scrive:
- testi SHX come tratti (un path per tratto di penna, in ordine di disegno);
- tratteggi/solidi come triangoli pieni (vanno riuniti in poligoni);
- cerchi come 4 curve di Bezier; linee tratteggiate come segmenti singoli.
Qui si estraggono i path grezzi e si riconoscono le primitive CAD;
il riconoscimento testi e' in pdf_testi.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

PT_MM = 25.4 / 72.0
KAPPA = 0.5522847498  # controllo Bezier per quarto di cerchio


@dataclass
class Tratto:
    """Un path del PDF convertito in mm (Y in alto)."""
    seq: int
    tipo: str                    # 's' tratto, 'f' pieno, 'fs' pieno+tratto
    items: list                  # ('l', p0, p1) | ('c', p0, c1, c2, p3)
    larghezza: float             # spessore penna in mm
    colore: tuple | None         # RGB 0-255 del tratto
    riempimento: tuple | None    # RGB 0-255 del pieno
    chiuso: bool
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    pari_dispari: bool = False

    @property
    def larg(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def alt(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def dim(self) -> float:
        return max(self.larg, self.alt)

    @property
    def ha_curve(self) -> bool:
        return any(it[0] == "c" for it in self.items)


@dataclass
class TestoPdf:
    """Testo vero del PDF (font TrueType/Type1 incorporati)."""
    testo: str
    x: float
    y: float                     # punto base (baseline) in mm
    altezza: float               # altezza maiuscole stimata in mm
    rotazione: float             # gradi, antiorario
    font: str
    grassetto: bool
    corsivo: bool
    colore: tuple
    bbox: tuple
    larghezza: float = 1.0       # fattore di larghezza (Tz/Tc del PDF)


@dataclass
class PaginaVettoriale:
    numero: int                  # 1-based
    larghezza: float             # mm
    altezza: float               # mm
    tratti: list[Tratto] = field(default_factory=list)
    testi: list[TestoPdf] = field(default_factory=list)
    immagini: list[dict] = field(default_factory=list)
    produttore: str = ""


def _rgb(c) -> tuple | None:
    if c is None:
        return None
    if len(c) == 1:  # grigio
        v = int(round(c[0] * 255))
        return (v, v, v)
    if len(c) == 4:  # CMYK approssimato
        cc, m, y, k = c
        return tuple(int(round(255 * (1 - min(1, v + k)))) for v in (cc, m, y))
    return tuple(int(round(v * 255)) for v in c[:3])


def _bbox_items(items) -> tuple:
    xs, ys = [], []
    for it in items:
        for p in it[1:]:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def estrai_pagina(page, numero: int, scala: float = 1.0) -> PaginaVettoriale:
    """Legge path, testi veri e immagini di una pagina PyMuPDF."""
    import pymupdf
    k = PT_MM * scala
    rot = page.rotation_matrix  # PyMuPDF restituisce coordinate non ruotate
    ruotata = page.rotation % 360 != 0
    h_pt = page.rect.height

    def tp(p) -> tuple:
        if ruotata:
            p = p * rot
        return (round(p.x * k, 4), round((h_pt - p.y) * k, 4))

    pv = PaginaVettoriale(numero=numero, larghezza=page.rect.width * k,
                          altezza=page.rect.height * k)
    for d in page.get_drawings():
        items = []
        for it in d["items"]:
            op = it[0]
            if op == "l":
                items.append(("l", tp(it[1]), tp(it[2])))
            elif op == "c":
                items.append(("c", tp(it[1]), tp(it[2]), tp(it[3]), tp(it[4])))
            elif op == "re":
                r = it[1]
                pts = [tp(r.tl), tp(r.tr), tp(r.br), tp(r.bl)]
                items += [("l", pts[i], pts[(i + 1) % 4]) for i in range(4)]
            elif op == "qu":
                q = it[1]
                pts = [tp(q.ul), tp(q.ur), tp(q.lr), tp(q.ll)]
                items += [("l", pts[i], pts[(i + 1) % 4]) for i in range(4)]
        if not items:
            continue
        t = Tratto(seq=int(d.get("seqno", 0)), tipo=d.get("type") or "s",
                   items=items, larghezza=float(d.get("width") or 0.0) * k,
                   colore=_rgb(d.get("color")), riempimento=_rgb(d.get("fill")),
                   chiuso=bool(d.get("closePath")),
                   pari_dispari=bool(d.get("even_odd")))
        t.bbox = _bbox_items(items)
        pv.tratti.append(t)
    pv.tratti.sort(key=lambda t: t.seq)

    raw = page.get_text("rawdict")
    font_cache: dict = {}
    for b in raw.get("blocks", []):
        if b.get("type") == 1:  # immagine
            x0, y0, x1, y1 = b["bbox"]
            pv.immagini.append({"bbox": (x0 * k, (h_pt - y1) * k,
                                         x1 * k, (h_pt - y0) * k),
                                "larghezza_px": b.get("width"),
                                "altezza_px": b.get("height")})
            continue
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if ruotata:
                v = (page.rotation_matrix.a * dx + page.rotation_matrix.c * dy,
                     page.rotation_matrix.b * dx + page.rotation_matrix.d * dy)
                dx, dy = v
            ang = math.degrees(math.atan2(-dy, dx))
            for sp in ln.get("spans", []):
                chars = sp.get("chars", [])
                testo = "".join(c.get("c", "") for c in chars) or sp.get("text", "")
                if not testo.strip():
                    continue
                o = chars[0]["origin"] if chars else sp["origin"]
                po = pymupdf.Point(o)
                x, y = tp(po)
                size = float(sp.get("size", 10))
                fl = int(sp.get("flags", 0))
                font = sp.get("font", "")
                x0, y0, x1, y1 = sp["bbox"]
                cap, wf = _metriche_font(page, font, font_cache, testo, chars, size)
                pv.testi.append(TestoPdf(
                    testo=testo, x=x, y=y, altezza=round(size * cap * k, 3),
                    rotazione=round(ang, 2), font=font,
                    grassetto=bool(fl & 16) or "bold" in font.lower(),
                    corsivo=bool(fl & 2) or "italic" in font.lower()
                    or "oblique" in font.lower(),
                    colore=_rgb([((sp.get("color", 0) >> s) & 255) / 255
                                 for s in (16, 8, 0)]) or (0, 0, 0),
                    bbox=(x0 * k, (h_pt - y1) * k, x1 * k, (h_pt - y0) * k),
                    larghezza=wf))
    return pv


def _metriche_font(page, nome: str, cache: dict, testo: str, chars: list,
                   size: float) -> tuple[float, float]:
    """(altezza maiuscole / corpo, fattore di larghezza) di un testo PDF.

    Il fattore di larghezza confronta la distanza reale tra il primo e
    l'ultimo carattere con quella naturale del font incorporato (testi
    compressi o espansi con Tz/Tc nel PDF)."""
    import io
    import pymupdf
    if nome not in cache:
        cache[nome] = (None, 0.716)
        for f in page.get_fonts():
            base = f[3].split("+")[-1]
            if base == nome or base.endswith(nome) or nome.endswith(base):
                try:
                    buf = page.parent.extract_font(f[0])[3]
                    fo = pymupdf.Font(fontbuffer=buf) if buf else None
                    cap = 0.716
                    try:
                        from fontTools.ttLib import TTFont
                        tt = TTFont(io.BytesIO(buf))
                        os2 = tt["OS/2"] if "OS/2" in tt else None
                        if os2 is not None and getattr(os2, "sCapHeight", 0):
                            cap = os2.sCapHeight / tt["head"].unitsPerEm
                    except Exception:  # noqa: BLE001 - font CFF/Type1: default
                        pass
                    cache[nome] = (fo, cap)
                except Exception:  # noqa: BLE001
                    pass
                break
    fo, cap = cache[nome]
    wf = 1.0
    if fo is not None and len(chars) >= 2:
        try:
            naturale = fo.text_length(testo[:-1], fontsize=size)
            reale = math.dist(chars[0]["origin"], chars[-1]["origin"])
            if naturale > 1e-3:
                wf = round(reale / naturale, 3)
                if abs(wf - 1.0) < 0.02 or not (0.3 < wf < 3.0):
                    wf = 1.0
        except Exception:  # noqa: BLE001
            wf = 1.0
    return cap, wf


# --------------------------------------------------------------------------
# Riconoscimento primitive CAD
# --------------------------------------------------------------------------

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bezier_pt(c, t: float) -> tuple:
    _, p0, p1, p2, p3 = c
    u = 1 - t
    a, b, cc, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + cc * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + cc * p2[1] + d * p3[1])


def appiattisci(items, passi: int = 8) -> list[list[tuple]]:
    """Items -> liste di punti (sottopercorsi continui)."""
    out: list[list[tuple]] = []
    cur: list[tuple] = []
    for it in items:
        p0 = it[1]
        if not cur or _dist(cur[-1], p0) > 1e-3:
            if len(cur) > 1:
                out.append(cur)
            cur = [p0]
        if it[0] == "l":
            cur.append(it[2])
        else:
            cur += [bezier_pt(it, i / passi) for i in range(1, passi + 1)]
    if len(cur) > 1:
        out.append(cur)
    return out


def _cerchio_3p(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arco_da_bezier(curve: list, tol: float = 0.02):
    """Sequenza di Bezier contigue -> arco (centro, r, a0, a1) o None.

    Angoli in gradi, verso antiorario da a0 ad a1 (convenzione DXF).
    """
    p0 = curve[0][1]
    p1 = curve[-1][4]
    # punto medio: meta' della curva centrale (dispari) o giunzione centrale (pari)
    mid = bezier_pt(curve[len(curve) // 2], 0.5) if len(curve) % 2         else curve[len(curve) // 2][1]
    chiuso = _dist(p0, p1) < tol
    if chiuso:
        # cerchio: centro = media dei punti di giunzione
        pts = [c[1] for c in curve]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        r = sum(_dist(p, (cx, cy)) for p in pts) / len(pts)
        centro = (cx, cy)
    else:
        fit = _cerchio_3p(p0, mid, p1)
        if not fit:
            return None
        centro, r = fit
    if r < 1e-3 or r > 5000:
        return None
    for c in curve:  # tutti i punti campione sul cerchio
        for t in (0.25, 0.5, 0.75):
            if abs(_dist(bezier_pt(c, t), centro) - r) > max(tol, r * 0.004):
                return None
    if chiuso:
        return centro, r, 0.0, 360.0
    a0 = math.degrees(math.atan2(p0[1] - centro[1], p0[0] - centro[0]))
    a1 = math.degrees(math.atan2(p1[1] - centro[1], p1[0] - centro[0]))
    am = math.degrees(math.atan2(mid[1] - centro[1], mid[0] - centro[0]))
    # verso: il punto medio deve stare nell'arco antiorario a0->a1
    def _in(a, s, e):
        return (a - s) % 360 <= (e - s) % 360
    if not _in(am, a0, a1):
        a0, a1 = a1, a0
    return centro, r, a0 % 360, a1 % 360


def cerchio_da_tratto(t: Tratto, tol: float = 0.02):
    """Path di sole curve chiuse (es. 4 Bezier) -> (centro, r) oppure None."""
    if not t.items or any(it[0] != "c" for it in t.items):
        return None
    arc = arco_da_bezier(t.items, tol)
    if arc and arc[3] - arc[2] == 360.0:
        return arc[0], arc[1]
    return None


def catene(items, tol: float = 1e-3) -> list[list]:
    """Divide gli items in catene contigue (fine==inizio del successivo)."""
    out, cur = [], []
    for it in items:
        if cur and _dist(cur[-1][-1], it[1]) > tol:
            out.append(cur)
            cur = []
        cur.append(it)
    if cur:
        out.append(cur)
    return out


def segmenti(t: Tratto) -> list[tuple]:
    """Solo segmenti retti (p0, p1) del tratto; curve appiattite."""
    out = []
    for pts in appiattisci(t.items):
        out += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return out


def unisci_triangoli(tratti: list[Tratto], buffer_mm: float = 0.02):
    """Pieni (spesso triangolati dal plotter) -> poligoni per colore.

    Ritorna [(rgb, shapely Polygon)]. Richiede shapely.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    per_colore: dict = {}
    for t in tratti:
        col = t.riempimento or t.colore or (0, 0, 0)
        for pts in appiattisci(t.items, passi=6):
            if len(pts) >= 3:
                if _dist(pts[0], pts[-1]) > 1e-3:
                    pts = pts + [pts[0]]
                try:
                    pg = Polygon(pts)
                    if not pg.is_valid:
                        pg = pg.buffer(0)
                    if pg.area > 1e-6:
                        per_colore.setdefault(col, []).append(pg)
                except Exception:  # noqa: BLE001 - poligono degenere
                    continue
    out = []
    for col, polys in per_colore.items():
        u = unary_union([p.buffer(buffer_mm, join_style=2) for p in polys])
        u = u.buffer(-buffer_mm, join_style=2)
        geoms = getattr(u, "geoms", [u])
        for g in geoms:
            if g.is_empty or g.area < 1e-5:
                continue
            out.append((col, g.simplify(0.005, preserve_topology=True)))
    return out
