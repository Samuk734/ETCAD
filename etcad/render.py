"""Anteprime PNG (PDF, DXF, DWG) allineate al millimetro e confronto.

Coordinate "zona": (x0, y0, x1, y1) in mm del disegno, origine in basso a
sinistra (come in SPAC). Le anteprime PDF e DWG della stessa zona hanno
gli stessi pixel: il confronto e' una sovrapposizione diretta.

Colori del confronto: nero = uguale, ROSSO = presente nel PDF ma non nel
DWG (manca), CIANO = presente nel DWG ma non nel PDF (in piu').
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile

import numpy as np

PT_MM = 25.4 / 72.0
CACHE_DIR = os.path.join(tempfile.gettempdir(), "etcad_render")


def _zona(zona, larghezza: float, altezza: float) -> tuple:
    if not zona:
        return (0.0, 0.0, float(larghezza), float(altezza))
    if isinstance(zona, str):
        zona = [float(v) for v in zona.replace(";", ",").split(",")]
    x0, y0, x1, y1 = [float(v) for v in zona]
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def dpi_per_lato(zona: tuple, lato_max_px: int) -> int:
    w_in = (zona[2] - zona[0]) / 25.4
    h_in = (zona[3] - zona[1]) / 25.4
    return max(20, int(lato_max_px / max(w_in, h_in, 1e-6)))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def pdf_png(pdf: str, pagina: int, zona=None, dpi: int = 0,
            lato_max_px: int = 1600, scala: float = 1.0):
    """Immagine PIL della pagina (o zona in mm) del PDF."""
    import pymupdf
    from PIL import Image
    doc = pymupdf.open(pdf)
    page = doc[pagina - 1]
    k = PT_MM * scala
    W, H = page.rect.width * k, page.rect.height * k
    z = _zona(zona, W, H)
    dpi = dpi or dpi_per_lato(z, lato_max_px)
    clip = pymupdf.Rect(z[0] / k, (H - z[3]) / k, z[2] / k, (H - z[1]) / k)
    pix = page.get_pixmap(dpi=int(dpi / scala), clip=clip, alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return im, {"zona_mm": [round(v, 2) for v in z], "dpi": dpi,
                "pagina_mm": [round(W, 2), round(H, 2)]}


# ---------------------------------------------------------------------------
# DXF / DWG
# ---------------------------------------------------------------------------

def _doc(percorso_o_doc):
    """Documento ezdxf da leggere/modificare liberamente (sempre una copia)."""
    import ezdxf
    if not isinstance(percorso_o_doc, str):
        fd, tmp = tempfile.mkstemp(suffix=".dxf")
        os.close(fd)
        try:
            percorso_o_doc.saveas(tmp)
            return ezdxf.readfile(tmp)
        finally:
            os.unlink(tmp)
    if percorso_o_doc.lower().endswith(".dwg"):
        return ezdxf.readfile(dxf_da_dwg(percorso_o_doc))
    return ezdxf.readfile(percorso_o_doc)


def dxf_da_dwg(dwg: str) -> str:
    """DWG -> DXF in cache (chiave: percorso, dimensione, data)."""
    from . import dwg_io
    st = os.stat(dwg)
    k = hashlib.md5(f"{os.path.abspath(dwg)}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, k + ".dxf")
    if not os.path.isfile(out):
        dwg_io.dwg_a_dxf(dwg, out)
    return out


def estensione(doc) -> tuple:
    from ezdxf import bbox
    ext = bbox.extents(doc.modelspace(), fast=True)
    if not ext.has_data:
        return (0.0, 0.0, 297.0, 210.0)
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def cad_png(percorso_o_doc, zona=None, dpi: int = 0, lato_max_px: int = 1600,
            sfondo: str = "bianco", layer_spenti: list | None = None,
            testi_hershey: bool = True):
    """Immagine PIL dello spazio modello (o zona in mm) di un DXF/DWG.

    testi_hershey: i testi con font SHX (romans, isocp...) vengono disegnati
    con i glifi Hershey (romans.shx ne deriva): anteprima fedele anche su un
    PC senza SPAC/font SHX. Non modifica il file.
    """
    from PIL import Image
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing import config as dcfg
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    from ezdxf.math import BoundingBox2d
    doc = _doc(percorso_o_doc)
    if testi_hershey:
        from . import hershey
        hershey.esplodi_testi(doc)
    if zona:
        z = _zona(zona, 0, 0)
    else:
        z = estensione(doc)
    dpi = dpi or dpi_per_lato(z, lato_max_px)
    cfg = dcfg.Configuration(
        background_policy=(dcfg.BackgroundPolicy.WHITE if sfondo == "bianco"
                           else dcfg.BackgroundPolicy.BLACK),
        color_policy=dcfg.ColorPolicy.COLOR,
        lineweight_policy=dcfg.LineweightPolicy.ABSOLUTE,
        lineweight_scaling=1.0)
    ctx = RenderContext(doc)
    for nome in layer_spenti or []:
        if nome in ctx.layers:
            ctx.layers[nome].is_visible = False
    backend = PyMuPdfBackend()
    Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
    w, h = z[2] - z[0], z[3] - z[1]
    page = layout.Page(w, h, layout.Units.mm, margins=layout.Margins.all(0))
    settings = layout.Settings(fit_page=False, scale=1.0, max_stroke_width=0.02,
                               min_stroke_width=0.05, fixed_stroke_width=0.15)
    png = backend.get_pixmap_bytes(page, fmt="png", settings=settings, dpi=dpi,
                                   render_box=BoundingBox2d([(z[0], z[1]), (z[2], z[3])]))
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return im, {"zona_mm": [round(v, 2) for v in z], "dpi": dpi}


def cad_pdf(percorso_o_doc, zona=None, testi_hershey: bool = True) -> bytes:
    """PDF vettoriale (1:1 in mm) dello spazio modello di un DXF/DWG, senza
    SPAC: per guardare un DWG con qualsiasi lettore PDF."""
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing import config as dcfg
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    from ezdxf.math import BoundingBox2d
    doc = _doc(percorso_o_doc)
    if testi_hershey:
        from . import hershey
        hershey.esplodi_testi(doc)
    z = _zona(zona, 0, 0) if zona else estensione(doc)
    # sfondo bianco trasparente: il colore 7 resta nero, ma nella pagina non c'e' un rettangolo di sfondo
    # (altrimenti chi rilegge il PDF, come pdf_a_dwg, lo vede come un riempimento a tutta pagina)
    cfg = dcfg.Configuration(background_policy=dcfg.BackgroundPolicy.CUSTOM, custom_bg_color="#ffffff00",
                             color_policy=dcfg.ColorPolicy.COLOR,
                             lineweight_policy=dcfg.LineweightPolicy.ABSOLUTE,
                             lineweight_scaling=1.0)
    backend = PyMuPdfBackend()
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(doc.modelspace(),
                                                                  finalize=True)
    page = layout.Page(z[2] - z[0], z[3] - z[1], layout.Units.mm,
                       margins=layout.Margins.all(0))
    settings = layout.Settings(fit_page=False, scale=1.0, max_stroke_width=0.02,
                               min_stroke_width=0.05, fixed_stroke_width=0.15)
    return backend.get_pdf_bytes(page, settings=settings,
                                 render_box=BoundingBox2d([(z[0], z[1]), (z[2], z[3])]))


def cad_pdf_pagine(percorso_o_doc, pagine: list, testi_hershey: bool = True) -> list[bytes]:
    """Piu' pagine PDF dallo stesso disegno, letto e preparato una volta sola.

    pagine = [(zona o None, [layer da spegnere])]: per i fogli affiancati basta la zona, per i multifogli a
    layer si spengono i layer degli altri fogli."""
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing import config as dcfg
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    from ezdxf.math import BoundingBox2d
    doc = _doc(percorso_o_doc)
    if testi_hershey:
        from . import hershey
        hershey.esplodi_testi(doc)
    cfg = dcfg.Configuration(background_policy=dcfg.BackgroundPolicy.CUSTOM, custom_bg_color="#ffffff00",
                             color_policy=dcfg.ColorPolicy.COLOR,
                             lineweight_policy=dcfg.LineweightPolicy.ABSOLUTE,
                             lineweight_scaling=1.0)
    settings = layout.Settings(fit_page=False, scale=1.0, max_stroke_width=0.02,
                               min_stroke_width=0.05, fixed_stroke_width=0.15)
    out = []
    for zona, spenti in pagine:
        ctx = RenderContext(doc)
        for nome in spenti or []:
            for k in (nome, str(nome).lower()):
                if k in ctx.layers:
                    ctx.layers[k].is_visible = False
        backend = PyMuPdfBackend()
        Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
        z = _zona(zona, 0, 0) if zona else estensione(doc)
        page = layout.Page(z[2] - z[0], z[3] - z[1], layout.Units.mm, margins=layout.Margins.all(0))
        out.append(backend.get_pdf_bytes(page, settings=settings,
                                         render_box=BoundingBox2d([(z[0], z[1]), (z[2], z[3])])))
    return out


def unisci_pdf(pagine_pdf: list[bytes], pdf_out: str) -> str:
    """Unisce PDF (bytes) in un unico file."""
    import pymupdf
    out = pymupdf.open()
    for b in pagine_pdf:
        with pymupdf.open("pdf", b) as src:
            out.insert_pdf(src)
    os.makedirs(os.path.dirname(os.path.abspath(pdf_out)) or ".", exist_ok=True)
    out.save(pdf_out, deflate=True)
    return pdf_out


def immagini_in_pdf(immagini: list, pdf_out: str, larghezza_mm: float = 297.0,
                    altezza_mm: float = 210.0) -> str:
    """Immagini PIL, una per pagina, in un PDF (es. sovrapposizioni)."""
    import pymupdf
    out = pymupdf.open()
    k = 72 / 25.4
    for im in immagini:
        pg = out.new_page(width=larghezza_mm * k, height=altezza_mm * k)
        pg.insert_image(pg.rect, stream=png_bytes(im))
    os.makedirs(os.path.dirname(os.path.abspath(pdf_out)) or ".", exist_ok=True)
    out.save(pdf_out, deflate=True)
    return pdf_out


# ---------------------------------------------------------------------------
# confronto
# ---------------------------------------------------------------------------

def _maschera(im, soglia: int = 170) -> np.ndarray:
    return np.asarray(im.convert("L")) < soglia


def _dilata(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for _ in range(r):
        d = out.copy()
        d[1:, :] |= out[:-1, :]
        d[:-1, :] |= out[1:, :]
        d[:, 1:] |= out[:, :-1]
        d[:, :-1] |= out[:, 1:]
        out = d
    return out


def confronta_pdf(pdf_a: str, pag_a: int, pdf_b: str, pag_b: int, zona=None,
                  dpi: int = 0, lato_max_px: int = 1600, **kw) -> dict:
    """Come confronta() ma tra due PDF (es. originale e DWG stampato da SPAC)."""
    im_a, info = pdf_png(pdf_a, pag_a, zona, dpi, lato_max_px)
    im_b, _ = pdf_png(pdf_b, pag_b, info["zona_mm"], info["dpi"])
    return _sovrapponi(im_a, im_b, info["zona_mm"], info["dpi"], **kw)


def confronta(pdf: str, pagina: int, cad, zona=None, dpi: int = 0,
              lato_max_px: int = 1600, celle_mm: float = 6.0,
              tolleranza_px: int = 2) -> dict:
    """Sovrappone PDF e DWG/DXF della stessa zona e misura le differenze."""
    im_pdf, info = pdf_png(pdf, pagina, zona, dpi, lato_max_px)
    z = info["zona_mm"]
    dpi = info["dpi"]
    im_cad, _ = cad_png(cad, z, dpi)
    return _sovrapponi(im_pdf, im_cad, z, dpi, celle_mm, tolleranza_px)


def _sovrapponi(im_pdf, im_cad, z, dpi, celle_mm: float = 6.0,
                tolleranza_px: int = 2) -> dict:
    from PIL import Image
    if im_cad.size != im_pdf.size:
        im_cad = im_cad.resize(im_pdf.size)
    a, b = _maschera(im_pdf), _maschera(im_cad)
    da, db = _dilata(a, tolleranza_px), _dilata(b, tolleranza_px)
    manca = a & ~db
    extra = b & ~da
    tot = max(1, int(a.sum() + b.sum()))
    somiglianza = 1.0 - (manca.sum() + extra.sum()) / tot
    g_pdf = np.asarray(im_pdf.convert("L"))
    g_cad = np.asarray(im_cad.convert("L"))
    rgb = np.stack([g_cad, g_pdf, g_pdf], axis=-1)
    overlay = Image.fromarray(rgb.astype(np.uint8))
    # zone con differenze (griglia in mm)
    px_mm = dpi / 25.4
    cs = max(4, int(celle_mm * px_mm))
    H, W = a.shape
    zone = []
    for yy in range(0, H, cs):
        for xx in range(0, W, cs):
            m = int(manca[yy:yy + cs, xx:xx + cs].sum())
            e = int(extra[yy:yy + cs, xx:xx + cs].sum())
            if m + e >= max(6, 0.004 * cs * cs):
                x0 = z[0] + xx / px_mm
                y1 = z[3] - yy / px_mm
                zone.append({"zona_mm": [round(x0, 1), round(y1 - cs / px_mm, 1),
                                         round(x0 + cs / px_mm, 1), round(y1, 1)],
                             "manca_px": m, "extra_px": e})
    zone.sort(key=lambda r: -(r["manca_px"] + r["extra_px"]))
    return {"overlay": overlay, "pdf": im_pdf, "cad": im_cad,
            "somiglianza": round(float(somiglianza), 4),
            "pixel_mancanti": int(manca.sum()), "pixel_in_piu": int(extra.sum()),
            "zona_mm": z, "dpi": dpi, "zone_diverse": zone[:25]}


def png_bytes(im, lato_max_px: int = 0) -> bytes:
    if lato_max_px and max(im.size) > lato_max_px:
        im = im.copy()
        im.thumbnail((lato_max_px, lato_max_px))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def affianca(immagini: list, gap: int = 12):
    from PIL import Image
    w = sum(i.size[0] for i in immagini) + gap * (len(immagini) - 1)
    h = max(i.size[1] for i in immagini)
    out = Image.new("RGB", (w, h), (255, 255, 255))
    x = 0
    for i in immagini:
        out.paste(i, (x, 0))
        x += i.size[0] + gap
    return out
