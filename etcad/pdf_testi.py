"""Riconoscimento dei testi "esplosi" nei PDF plottati da AutoCAD/SPAC.

I font SHX (romans, isocp, ...) arrivano nel PDF come tratti di penna, uno
per path, emessi nell'ordine di disegno: tutti i tratti di un TEXT sono
contigui nella sequenza. Pipeline:

1. raggruppa(): tratti piccoli e contigui nella sequenza -> gruppi-testo;
2. segmentazione in glifi (sovrapposizione lungo la direzione di lettura);
3. OCR (RapidOCR, opzionale) su un rendering pulito del solo gruppo;
4. dizionario glifi: forme normalizzate raggruppate in cluster ed
   etichettate a maggioranza (OCR) o dall'AI -> corregge O/0, maiuscole,
   e riconosce i testi di un solo carattere senza falsi positivi;
5. modello di spaziatura imparato dai dati (bearing per glifo + larghezza
   dello spazio per font) -> spazi esatti, testi adiacenti separati;
6. per ogni testo: stringa, altezza, punto base, rotazione, obliquo, font.

Senza OCR si usa il flusso AI: tavola dei glifi (immagine con ID) ->
etichette fornite dall'AI -> decodifica di tutto il documento.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import numpy as np

from .pdf_vettori import Tratto, appiattisci, cerchio_da_tratto

# Limiti (mm) per considerare un tratto come parte di un testo
DIM_MAX_TRATTO = 12.0
SEG_SINGOLO_MAX = 6.0
ALTEZZE_STD = [1.0, 1.25, 1.5, 1.8, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0,
               7.0, 8.0, 10.0, 12.0, 14.0]
CARATTERI_OK = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    " .,:;-_+=/\\()[]{}<>#%&*'\"!?@$|~^°±ÀÈÉÌÒÙàèéìòùç²³µΩ")
DISCENDENTI = set("gjpqy,;()[]{}_|")
ASCENDENTI = set("bdfhkl")
MINUSCOLE_X = set("acemnorsuvwxz")
ASTE_VERTICALI = set("BDEFHIKLMNPRTbdhklmnpqru14")
SOLO_TRATTINI = set("-_.·|=~")
# altezza delle minuscole / altezza maiuscole: romans (Hershey) 14/21,
# ISO 3098 (isocp) 7/10
RAPPORTO_X = {"ROMANS": 14 / 21, "ISOCP": 0.70}


@dataclass
class Glifo:
    tratti: list                    # indici locali nel gruppo (ordine pls)
    x0: float
    x1: float
    y0: float
    y1: float                       # coordinate di lettura (mm)
    firma: np.ndarray | None = None
    cluster: int = -1
    etichetta: str = ""
    inchiostro: float = 0.0         # lunghezza tratti / dimensione glifo


@dataclass
class GruppoTesto:
    idx: list                       # indici in pagina.tratti
    penna: float
    colore: tuple | None
    bbox: tuple
    rot: float = 0.0                # rotazione di lettura (gradi)
    glifi: list = field(default_factory=list)
    pls: list = field(default_factory=list)   # polilinee in coord. lettura
    base: float = 0.0
    altezza: float = 0.0
    x_ins: float = 0.0
    y_ins: float = 0.0
    obliquo: float = 0.0
    font: str = "ROMANS"
    testo: str = ""
    conf: float = 0.0
    fonte: str = ""                 # dizionario | ocr | ocr_grezzo | ai
    ocr: str = ""
    ocr_conf: float = 0.0
    accettato: bool = False
    nota: str = ""
    id: str = ""                    # "p<pagina>-<n>" assegnato a fine analisi


# ---------------------------------------------------------------------------
# 1. raggruppamento per sequenza
# ---------------------------------------------------------------------------

def _piccolo(t: Tratto) -> bool:
    if t.tipo != "s" or t.dim > DIM_MAX_TRATTO:
        return False
    if len(t.items) == 1 and t.items[0][0] == "l":
        (x0, y0), (x1, y1) = t.items[0][1], t.items[0][2]
        if math.hypot(x1 - x0, y1 - y0) > SEG_SINGOLO_MAX:
            return False
    return True


def _raggio_cerchio(t: Tratto) -> float:
    """Raggio se il tratto e' un cerchio perfetto, altrimenti 0."""
    if not t.ha_curve:
        return 0.0
    c = cerchio_da_tratto(t)
    return c[1] if c else 0.0


def _gap(a: tuple, b: tuple) -> float:
    gx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(gx, gy)


def _unione(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def raggruppa(tratti: list[Tratto]) -> list[GruppoTesto]:
    """Tratti piccoli contigui nella sequenza di disegno -> gruppi."""
    gruppi: list[GruppoTesto] = []
    cur: GruppoTesto | None = None
    ultimo = None
    dim_max = 0.0
    for i, t in enumerate(tratti):
        if not _piccolo(t):
            cur = None
            continue
        r = _raggio_cerchio(t)
        if r >= 0.15:
            # cerchio: punto di i/j o simbolo "°" solo se piccolo rispetto al
            # testo che lo precede nella sequenza; altrimenti pin/morsetto
            if cur is None or 2 * r > 0.55 * max(dim_max, 0.8) \
                    or abs(t.larghezza - cur.penna) >= 0.01:
                cur = None
                continue
        if cur is not None and abs(t.larghezza - cur.penna) < 0.01 \
                and t.colore == cur.colore:
            gw = cur.bbox[2] - cur.bbox[0]
            gh = cur.bbox[3] - cur.bbox[1]
            h_ref = max(min(gw, gh), 0.7 * dim_max, 0.8)
            if _gap(ultimo, t.bbox) <= max(1.2, 1.9 * h_ref):
                cur.idx.append(i)
                cur.bbox = _unione(cur.bbox, t.bbox)
                ultimo = t.bbox
                dim_max = max(dim_max, t.dim)
                continue
        cur = GruppoTesto(idx=[i], penna=t.larghezza, colore=t.colore,
                          bbox=t.bbox)
        ultimo = t.bbox
        dim_max = t.dim
        gruppi.append(cur)
    return gruppi


# ---------------------------------------------------------------------------
# 2. geometria di lettura, glifi, firme
# ---------------------------------------------------------------------------

def _ruota(p, rot: float) -> tuple:
    """Coordinate disegno -> coordinate di lettura (testo lungo +x)."""
    if rot == 0:
        return p
    a = math.radians(-rot)
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _ruota_inv(p, rot: float) -> tuple:
    if rot == 0:
        return p
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def polilinee_gruppo(idx: list, tratti: list[Tratto], rot: float):
    """Per ogni tratto: liste di punti in coordinate di lettura."""
    return [[[_ruota(p, rot) for p in pts] for pts in appiattisci(tratti[i].items)]
            for i in idx]


def _bbox_pl(pl) -> tuple:
    xs = [p[0] for pts in pl for p in pts]
    ys = [p[1] for pts in pl for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _orizzontale(pl, tol: float) -> float | None:
    """y se il tratto e' un unico segmento orizzontale, altrimenti None."""
    if len(pl) != 1 or len(pl[0]) != 2:
        return None
    (x0, y0), (x1, y1) = pl[0]
    return y0 if abs(y1 - y0) <= tol and abs(x1 - x0) > tol else None


def segmenta_glifi(pls: list) -> list[Glifo]:
    """Tratti (in ordine) -> glifi: nuovo glifo quando il tratto non si
    sovrappone lungo x al glifo corrente. Caso speciale: trattini bassi o
    trattini consecutivi che si toccano ("__", "--")."""
    glifi: list[Glifo] = []
    orizz: list[float | None] = []   # per glifo: y se fatto solo di un orizzontale
    estremi: list[list] = []         # per glifo: estremi dei tratti
    for k, pl in enumerate(pls):
        x0, y0, x1, y1 = _bbox_pl(pl)
        testa = pl[0][0]
        if glifi:
            g = glifi[-1]
            h = max(g.y1 - g.y0, y1 - y0, 0.3)
            tol = 0.06 * h
            yo = _orizzontale(pl, 0.02)
            unito = x0 <= g.x1 + tol and x1 >= g.x0 - tol
            # continuita': il plotter spezza i tratti al passaggio linea/arco,
            # ma il nuovo tratto riparte da un estremo del glifo corrente
            eps = max(0.04, 0.04 * h)
            if not unito and any(math.dist(testa, e) <= eps for e in estremi[-1]):
                unito = True
            if unito and yo is not None and orizz[-1] is not None \
                    and abs(yo - orizz[-1]) < 0.05 and x0 >= g.x1 - 0.1 * (x1 - x0):
                unito = False  # "__": due tratti allineati, uno dopo l'altro
            if unito:
                g.tratti.append(k)
                g.x0, g.x1 = min(g.x0, x0), max(g.x1, x1)
                g.y0, g.y1 = min(g.y0, y0), max(g.y1, y1)
                orizz[-1] = None
                estremi[-1] += [pts[0] for pts in pl] + [pts[-1] for pts in pl]
                continue
        glifi.append(Glifo([k], x0, x1, y0, y1))
        orizz.append(_orizzontale(pl, 0.02))
        estremi.append([pts[0] for pts in pl] + [pts[-1] for pts in pl])
    return glifi


PASSO_FIRMA = 0.03   # ricampionamento: frazione della dimensione del glifo


def firma_glifo(g: Glifo, pls: list) -> np.ndarray:
    """Punti ricampionati dei tratti, normalizzati sul bbox del glifo.

    I glifi SHX sono geometricamente identici tra un'occorrenza e l'altra
    (a meno della scala): il confronto vettoriale distingue anche P/R,
    C/G, O/0, f/t, cosa che un raster grossolano non fa.
    """
    size = max(g.x1 - g.x0, g.y1 - g.y0, 1e-6)
    passo = PASSO_FIRMA * size
    pts_out = []
    inchiostro = 0.0
    for k in g.tratti:
        for pts in pls[k]:
            if len(pts) == 1:
                pts_out.append(pts[0])
                continue
            for a, b in zip(pts, pts[1:]):
                d = math.hypot(b[0] - a[0], b[1] - a[1])
                inchiostro += d
                n = max(1, int(d / passo))
                for i in range(n + 1):
                    t = i / n
                    pts_out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    arr = np.asarray(pts_out, dtype=np.float64)
    arr[:, 0] = (arr[:, 0] - g.x0) / size
    arr[:, 1] = (arr[:, 1] - g.y0) / size
    if len(arr) > 96:  # limita il costo del confronto
        arr = arr[np.linspace(0, len(arr) - 1, 96).astype(int)]
    g.inchiostro = inchiostro / size
    return arr


def _q95(v: np.ndarray) -> float:
    k = int(0.95 * (len(v) - 1))
    return float(np.partition(v, k)[k])


def distanza_firme(a: np.ndarray, b: np.ndarray) -> float:
    """Distanza simmetrica (95 percentile dei vicini piu' prossimi), 0 = uguali."""
    if a is None or b is None or not len(a) or not len(b):
        return 9.0
    diff = a[:, None, :] - b[None, :, :]
    d2 = np.einsum("ijk,ijk->ij", diff, diff)
    return math.sqrt(max(_q95(d2.min(1)), _q95(d2.min(0))))


def lunghezza_firma(f: np.ndarray) -> float:
    """Inchiostro normalizzato (somma dei passi tra punti consecutivi vicini)."""
    if f is None or len(f) < 2:
        return 0.0
    passi = np.sqrt(((f[1:] - f[:-1]) ** 2).sum(-1))
    return float(passi[passi < 0.2].sum())


def somiglianza(a: np.ndarray, b: np.ndarray) -> float:
    """1 = identici, 0 = molto diversi (per compatibilita' con soglie 0..1)."""
    return max(0.0, 1.0 - distanza_firme(a, b) / 0.25)


# ---------------------------------------------------------------------------
# 3. OCR opzionale (RapidOCR, offline)
# ---------------------------------------------------------------------------

_OCR = None


def motore_ocr():
    """RapidOCR se installato (pip install rapidocr_onnxruntime), altrimenti None."""
    global _OCR
    if _OCR is None:
        try:
            import logging
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
            _OCR = RapidOCR()
        except Exception:  # noqa: BLE001 - OCR non disponibile
            _OCR = False
    return _OCR or None


def rendering_tratti(pls: list, h: float, px_h: int = 40, pad: int = 12,
                     spessore: float = 0.11):
    """Immagine pulita (nero su bianco) dei soli tratti indicati."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = _bbox_pl([p for pl in pls for p in pl])
    s = px_h / max(h, 0.3)
    W = max(int((x1 - x0) * s) + 2 * pad, 16)
    H = max(int((y1 - y0) * s) + 2 * pad, 16)
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    lw = max(2, int(round(spessore * px_h)))
    for pl in pls:
        for pts in pl:
            q = [((p[0] - x0) * s + pad, H - ((p[1] - y0) * s + pad)) for p in pts]
            if len(q) == 1:
                d.ellipse([q[0][0] - lw, q[0][1] - lw, q[0][0] + lw, q[0][1] + lw],
                          fill=0)
            else:
                d.line(q, fill=0, width=lw, joint="curve")
    return im


_NORMALIZZA = str.maketrans({
    "。": ".", "，": ",", "：": ":", "；": ";", "（": "(",
    "）": ")", "—": "-", "–": "-", "−": "-", "·": ".",
    "’": "'", "‘": "'", "´": "'", "`": "'", "“": '"',
    "”": '"', "＋": "+", "＝": "=", "／": "/", "×": "x",
    "º": "°", "˚": "°", "⁰": "°"})


_CACHE_OCR: dict[str, tuple[str, float]] = {}


def carica_cache_ocr(path: str) -> None:
    import json
    import os
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                _CACHE_OCR.update({k: tuple(v) for k, v in json.load(f).items()})
        except (OSError, ValueError):
            pass


def salva_cache_ocr(path: str) -> None:
    import json
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_CACHE_OCR, f, ensure_ascii=False)
    except OSError:
        pass


def leggi_ocr(im) -> tuple[str, float]:
    import hashlib
    eng = motore_ocr()
    if eng is None:
        return "", 0.0
    g = im.convert("L")
    chiave = hashlib.md5(g.tobytes() + str(g.size).encode()).hexdigest()
    if chiave in _CACHE_OCR:
        return _CACHE_OCR[chiave]
    arr = np.asarray(g.convert("RGB"))
    try:
        res, _ = eng(arr, use_det=False, use_cls=False, use_rec=True)
    except Exception:  # noqa: BLE001
        return "", 0.0
    out = ("", 0.0) if not res else (str(res[0][0]).translate(_NORMALIZZA),
                                     float(res[0][1]))
    _CACHE_OCR[chiave] = out
    return out


# ---------------------------------------------------------------------------
# 4. analisi di un gruppo
# ---------------------------------------------------------------------------

def _moda(valori: list[float], tol: float) -> float:
    if not valori:
        return 0.0
    best, nb = valori[0], -1
    for v in valori:
        n = sum(1 for w in valori if abs(w - v) <= tol)
        if n > nb or (n == nb and v < best):
            best, nb = v, n
    return statistics.median([w for w in valori if abs(w - best) <= tol])


def stima_base_altezza(glifi: list[Glifo], etichette: bool = False,
                       x_ratio: float = 0.70):
    """Baseline e altezza maiuscole dai glifi (coordinate di lettura)."""
    if not glifi:
        return 0.0, 0.0
    hmax = max(g.y1 - g.y0 for g in glifi)
    tol = max(0.06 * hmax, 0.05)
    if etichette and all(g.etichetta for g in glifi):
        sul_rigo = [g for g in glifi if g.etichetta[:1] not in DISCENDENTI
                    and g.etichetta[:1] not in "-+=*~^'\"°.:"]
        base = _moda([g.y0 for g in sul_rigo], tol) if sul_rigo else \
            _moda([g.y0 for g in glifi], tol)
        alti = [g.y1 - base for g in glifi
                if len(g.etichetta) == 1 and (g.etichetta.isupper()
                                              or g.etichetta.isdigit()
                                              or g.etichetta in ASCENDENTI)
                and g.etichetta not in "QJ"]
        if alti:
            return base, statistics.median(alti)
        xs = [g.y1 - base for g in glifi if g.etichetta in MINUSCOLE_X]
        if xs:
            return base, statistics.median(xs) / x_ratio
    base = _moda([g.y0 for g in glifi], tol)
    # solo glifi "grandi" (esclude apici, virgolette, punti) e non piu' alti
    # del normale (esclude parentesi/barre che superano le maiuscole)
    grandi = [g for g in glifi if (g.y1 - g.y0) >= 0.45 * hmax]
    alt = sorted(g.y1 - g.y0 for g in grandi)
    p75 = alt[min(len(alt) - 1, int(0.75 * len(alt)))] if alt else hmax
    tops = sorted((g.y1 for g in grandi if (g.y1 - g.y0) <= 1.25 * p75 + 1e-9),
                  reverse=True) or sorted((g.y1 for g in glifi), reverse=True)
    top = tops[0]
    if len(tops) > 2 and (tops[0] - tops[1]) > 0.12 * (tops[1] - base):
        top = tops[1]  # parentesi/barre oltre la linea delle maiuscole
    return base, max(top - base, 0.01)


def altezza_standard(h: float) -> float:
    for s in ALTEZZE_STD:
        if abs(h - s) <= 0.03 * s:
            return s
    return round(h, 2)


def obliquo_da_aste(g: GruppoTesto) -> float:
    """Inclinazione (gradi) misurata sulle aste dei glifi con asta verticale."""
    pend = []
    h = max(g.altezza, 1e-6)
    for gl in g.glifi:
        if gl.etichetta not in ASTE_VERTICALI:
            continue
        for k in gl.tratti:
            for pts in g.pls[k]:
                for a, b in zip(pts, pts[1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    if abs(dy) > 0.55 * h and abs(dx) < 0.45 * abs(dy):
                        pend.append(dx / dy)
    if len(pend) < 2:
        return 0.0
    ang = math.degrees(math.atan(statistics.median(pend)))
    return float(round(ang)) if abs(ang) >= 4 else 0.0


def obliquo_grezzo(g: GruppoTesto) -> float:
    """Inclinazione senza etichette: mediana dei tratti lunghi quasi verticali,
    solo se la maggioranza pende dalla stessa parte (i testi diritti hanno
    diagonali simmetriche: A, V, W, X si compensano)."""
    pend = []
    h = max(g.altezza, 1e-6)
    for pl in g.pls:
        for pts in pl:
            for a, b in zip(pts, pts[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                if abs(dy) > 0.55 * h and abs(dx) < 0.45 * abs(dy):
                    pend.append(dx / dy)
    if len(pend) < 4:
        return 0.0
    m = statistics.median(pend)
    stesso_verso = sum(1 for p in pend if p * m > 0 and abs(p) > 0.05) / len(pend)
    ang = math.degrees(math.atan(m))
    return float(round(ang)) if abs(ang) >= 6 and stesso_verso >= 0.7 else 0.0


def solo_cerchi(idx: list, tratti: list[Tratto]) -> bool:
    return all(cerchio_da_tratto(tratti[i]) is not None for i in idx)


def e_tratteggio(idx: list, tratti: list[Tratto]) -> bool:
    """Linea tratteggiata/punteggiata: segmenti singoli allineati o puntini."""
    if len(idx) < 3:
        return False
    if all(tratti[i].dim < 0.3 for i in idx) and len(idx) >= 5:
        return True
    rif = None
    for i in idx:
        t = tratti[i]
        if len(t.items) != 1 or t.items[0][0] != "l":
            return False
        (x0, y0), (x1, y1) = t.items[0][1], t.items[0][2]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        if rif is None:
            rif = (ux, uy, x0, y0)
            continue
        if abs(ux * rif[0] + uy * rif[1]) < 0.995:
            return False
        # distanza del punto dalla retta di riferimento
        if abs((x0 - rif[2]) * rif[1] - (y0 - rif[3]) * rif[0]) > 0.05:
            return False
    return rif is not None


def _poligono_semplice(pls_glifo: list) -> bool:
    """Un solo tratto chiuso a lati retti (rettangolo, triangolo): simbolo."""
    if len(pls_glifo) != 1 or len(pls_glifo[0]) != 1:
        return False
    pts = pls_glifo[0][0]
    return 4 <= len(pts) <= 6 and math.dist(pts[0], pts[-1]) < 0.02


def _orientamenti(g: GruppoTesto) -> list[float]:
    """Orientamenti naturali (dal rapporto d'aspetto) poi gli alternativi."""
    w = g.bbox[2] - g.bbox[0]
    h = g.bbox[3] - g.bbox[1]
    if len(g.idx) >= 2 and h > 1.6 * w:
        return [90.0, 270.0, 0.0]   # 0: testi orizzontali impilati in colonna
    return [0.0]


ALTERNATIVI = {0.0: [90.0, 270.0], 90.0: [0.0, 270.0], 270.0: [0.0, 90.0]}


def dividi_righe(pls: list) -> list[list[int]]:
    """Indici dei tratti divisi per riga (intervalli y sovrapposti) nel
    sistema di lettura: separa testi impilati o file di testi ruotati."""
    iv = []
    for k, pl in enumerate(pls):
        x0, y0, x1, y1 = _bbox_pl(pl)
        iv.append((y0, y1, k))
    iv.sort()
    # piccoli salti (il plotter spezza i tratti tra linea e arco) non separano
    tol = max(0.02, 0.1 * max((y1 - y0) for y0, y1, _ in iv))
    righe: list[list] = []
    for y0, y1, k in iv:
        if righe and y0 <= righe[-1][1] + tol:
            righe[-1][1] = max(righe[-1][1], y1)
            righe[-1][2].append(k)
        else:
            righe.append([y0, y1, [k]])
    if len(righe) > 1:
        # righe "piatte" (trattini bassi, sottolineature del testo) vanno
        # con la riga vera piu' vicina se entro mezza altezza
        hmax = max(r[1] - r[0] for r in righe)
        vere = [r for r in righe if r[1] - r[0] >= 0.2 * hmax]
        for r in righe:
            if r in vere:
                continue
            dist = [(min(abs(r[0] - v[0]), abs(r[1] - v[0]), abs(r[0] - v[1])), v)
                    for v in vere]
            d, v = min(dist, key=lambda t: t[0])
            if d <= 0.6 * hmax:
                v[2] += r[2]
                v[0], v[1] = min(v[0], r[0]), max(v[1], r[1])
            else:
                vere.append(r)
        righe = vere
    return [sorted(r[2]) for r in sorted(righe, key=lambda r: -r[1])]


def _analizza(idx: list, tratti: list[Tratto], rot: float, usa_ocr: bool):
    """Una riga in un orientamento: (conf, pls, glifi, base, h, testo)."""
    pls = polilinee_gruppo(idx, tratti, rot)
    glifi = segmenta_glifi(pls)
    base, h = stima_base_altezza(glifi)
    txt, conf = ("", 0.0)
    if usa_ocr:
        txt, conf = leggi_ocr(rendering_tratti(pls, h))
    return conf, pls, glifi, base, h, txt.strip()


def analizza_orientamento(g: GruppoTesto, tratti: list[Tratto], rot: float,
                          usa_ocr: bool = True):
    """Un gruppo in un orientamento: righe -> sotto-gruppi con glifi e OCR.

    Ritorna (punteggio OCR medio pesato sui glifi, lista GruppoTesto).
    """
    pls_rot = polilinee_gruppo(g.idx, tratti, rot)
    righe = [[g.idx[k] for k in r] for r in dividi_righe(pls_rot)]
    font = "ISOCP" if any(tratti[i].ha_curve for i in g.idx) else "ROMANS"
    # i tratti di un TEXT escono da sinistra a destra: se la sequenza torna
    # indietro lungo la riga, e' iniziato un altro testo (es. numeri di
    # colonna emessi da destra a sinistra)
    pezzi = []
    for idx in righe:
        idx = sorted(idx, key=lambda i: tratti[i].seq)
        pls = polilinee_gruppo(idx, tratti, rot)
        glifi = segmenta_glifi(pls)
        corrente: list[int] = []
        ultimo_x1 = None
        h_riga = max((gl.y1 - gl.y0 for gl in glifi), default=0.0)
        for gl in glifi:
            # indietro di almeno mezza altezza: i corsivi con grazie si
            # sovrappongono un po', un nuovo testo torna indietro di molto
            if ultimo_x1 is not None and gl.x1 < ultimo_x1 - max(0.05, 0.5 * h_riga) \
                    and corrente:
                pezzi.append(sorted(corrente, key=lambda i: tratti[i].seq))
                corrente, ultimo_x1 = [], None
            corrente += [idx[k] for k in gl.tratti]
            ultimo_x1 = gl.x1 if ultimo_x1 is None else max(ultimo_x1, gl.x1)
        if corrente:
            pezzi.append(sorted(corrente, key=lambda i: tratti[i].seq))
    righe = pezzi
    out, tot, somma = [], 0, 0.0
    for idx in righe:
        conf, pls, glifi, base, h, txt = _analizza(idx, tratti, rot, usa_ocr)
        ng = GruppoTesto(idx=idx, penna=g.penna, colore=g.colore,
                         bbox=_bbox_disegno_idx(idx, tratti), rot=rot, pls=pls,
                         glifi=glifi, base=base, altezza=h, font=font,
                         ocr=txt, ocr_conf=conf)
        for gl in glifi:
            gl.firma = firma_glifo(gl, pls)
        if txt:
            al = allinea_ocr(ng)
            if al is None:
                n = len([c for c in txt if not c.isspace()])
                if n != len(glifi):  # conteggio incoerente: lettura dubbia
                    ng.ocr_conf = conf = conf * 0.85
            elif "" in al:
                unisci_glifi(ng, al)  # es. virgolette: 2 tratti = 1 carattere
                ng.base, ng.altezza = stima_base_altezza(ng.glifi)
        out.append(ng)
        tot += len(ng.glifi)
        somma += conf * len(ng.glifi)
    return (somma / tot if tot else 0.0), out


def prepara_gruppo(g: GruppoTesto, tratti: list[Tratto], usa_ocr: bool = True
                   ) -> list[GruppoTesto]:
    """Primo passaggio: solo gli orientamenti naturali (dal bbox)."""
    migliore = None
    for rot in _orientamenti(g):
        score, righe = analizza_orientamento(g, tratti, rot, usa_ocr)
        if migliore is None or score > migliore[0] + 0.02:
            migliore = (score, righe)
    return migliore[1]


def corrispondenza_dizionario(righe: list[GruppoTesto], diz) -> float:
    """Frazione di glifi che corrispondono a un cluster etichettato e netto."""
    n = ok = 0
    for g in righe:
        for gl in g.glifi:
            n += 1
            c, _ = diz.trova(gl, g, crea=False)
            if c is not None and c.etichetta and c.netto:
                ok += 1
    return ok / n if n else 0.0


# ---------------------------------------------------------------------------
# 5. dizionario glifi (cluster + etichette a maggioranza)
# ---------------------------------------------------------------------------

@dataclass
class ClusterGlifo:
    id: int
    firma: np.ndarray
    rel_w: float
    rel_h: float
    rel_y0: float
    font: str
    inchiostro: float = 0.0
    n: int = 0
    voti: dict = field(default_factory=dict)
    fonti: set = field(default_factory=set)   # testi diversi che hanno votato
    etichetta_ai: str = ""
    esempio: tuple = ()          # (pagina, indice gruppo, indice glifo)
    forma: list = field(default_factory=list)  # tratti di un esempio (in altezze)

    @property
    def etichetta(self) -> str:
        if self.etichetta_ai:
            return self.etichetta_ai
        if not self.voti:
            return ""
        best = max(self.voti.items(), key=lambda kv: kv[1])
        tot = sum(self.voti.values())
        return best[0] if best[1] >= 2 and best[1] / tot >= 0.6 else ""

    @property
    def affidabilita(self) -> float:
        if self.etichetta_ai:
            return 1.0
        if not self.voti or not self.etichetta:
            return 0.0
        tot = sum(self.voti.values())
        return max(self.voti.values()) / tot * min(1.0, tot / 3)

    @property
    def netto(self) -> bool:
        """Etichetta indiscutibile: decide il dizionario anche contro l'OCR.

        Cluster con voti misti (es. l/I identici in ROMANS, O/0 simili)
        non sono netti: li decide l'OCR del contesto parola per parola."""
        if self.etichetta_ai:
            return True
        if not self.voti:
            return False
        tot = sum(self.voti.values())
        # voti da almeno due testi diversi: un simbolo ripetuto (es. 25 pin
        # uguali letti "1") non puo' confermarsi da solo
        return tot >= 3 and max(self.voti.values()) / tot >= 0.8 and len(self.fonti) >= 2


class DizionarioGlifi:
    DIST_MAX = 0.055     # distanza firme (frazione del glifo) per "stesso glifo"
    TOL = 0.12           # tolleranza su larghezza/altezza/quota relative
    PICCOLO = 0.18       # glifi piu' piccoli (punti, virgole): solo proporzioni

    def __init__(self):
        self.cluster: list[ClusterGlifo] = []
        self._cache: dict = {}

    @staticmethod
    def feat(gl: Glifo, g: GruppoTesto):
        h = max(g.altezza, 1e-6)
        return ((gl.x1 - gl.x0) / h, (gl.y1 - gl.y0) / h, (gl.y0 - g.base) / h)

    def trova(self, gl: Glifo, g: GruppoTesto, crea: bool = True):
        rw, rh, ry = self.feat(gl, g)
        piccolo = max(rw, rh) < self.PICCOLO
        ink = gl.inchiostro
        chiave = None
        if not piccolo and gl.firma is not None:
            chiave = (g.font, round(rw, 1), round(rh, 1), round(ry, 1),
                      np.round(gl.firma, 2).tobytes())
            cid = self._cache.get(chiave)
            if cid is not None:
                return self.cluster[cid], 0.0
        cand = []
        for c in self.cluster:
            if c.font != g.font or abs(c.rel_w - rw) > self.TOL \
                    or abs(c.rel_h - rh) > self.TOL or abs(c.rel_y0 - ry) > self.TOL:
                continue
            if not piccolo and abs(c.inchiostro - ink) > 0.18 * max(c.inchiostro, ink, 0.3):
                continue  # quantita' d'inchiostro diversa: glifo diverso
            f = abs(c.rel_w - rw) + abs(c.rel_h - rh) + abs(c.rel_y0 - ry)
            cand.append((f, c))
        cand.sort(key=lambda t: t[0])
        best, bd = None, 9.0
        for f, c in cand:
            if piccolo:
                d = f / 3 if max(c.rel_w, c.rel_h) < self.PICCOLO else 9.0
            else:
                d = distanza_firme(c.firma, gl.firma)
            if d < bd:
                best, bd = c, d
                if d < 0.02:
                    break  # praticamente identico: inutile cercare oltre
        if best is not None and bd <= self.DIST_MAX:
            if chiave is not None:
                self._cache[chiave] = best.id
            return best, bd
        if not crea:
            return None, bd
        c = ClusterGlifo(len(self.cluster), gl.firma, rw, rh, ry, g.font,
                         inchiostro=gl.inchiostro)
        # forma di esempio (coordinate relative a piede sinistro / altezza)
        h = max(g.altezza, 1e-6)
        c.forma = [[((x - gl.x0) / h, (y - g.base) / h) for x, y in pts]
                   for k in gl.tratti if k < len(g.pls) for pts in g.pls[k]]
        self.cluster.append(c)
        return c, 0.0


DUE_GLIFI = set("\"%:;=!?ijàèéìòù±")  # 1 carattere, 2+ tratti separati


def allinea_ocr(g: GruppoTesto) -> list[str] | None:
    """Etichette per glifo dall'OCR.

    Conteggio uguale: 1 a 1. Altrimenti allineamento a programmazione
    dinamica che ammette 1 carattere su 2 glifi (virgolette, %, :, =, i, j)
    e 2 caratteri su 1 glifo ("__", "--"): i glifi di continuazione
    ricevono l'etichetta "" e vanno uniti al precedente (vedi unisci_glifi).
    """
    s = [c for c in g.ocr if not c.isspace()]
    if not s or any(c not in CARATTERI_OK for c in s):
        return None
    n, m = len(g.glifi), len(s)
    if n == m:
        return s
    if abs(n - m) > max(2, 0.15 * m):
        return None
    INF = 1e9
    # dp[i][j]: costo con i glifi e j caratteri consumati
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    da = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c0 = dp[i][j]
            if c0 >= INF:
                continue
            if i < n and j < m and c0 < dp[i + 1][j + 1]:            # 1:1
                dp[i + 1][j + 1], da[i + 1][j + 1] = c0, (i, j, "11")
            if i + 1 < n and j < m:                                    # 1 car : 2 glifi
                costo = c0 + (0.2 if s[j] in DUE_GLIFI else 1.5)
                if costo < dp[i + 2][j + 1]:
                    dp[i + 2][j + 1], da[i + 2][j + 1] = costo, (i, j, "12")
            if i < n and j + 1 < m:                                    # 2 car : 1 glifo
                coppia = s[j] + s[j + 1]
                costo = c0 + (0.2 if coppia in ("__", "--", "..") else 1.5)
                if costo < dp[i + 1][j + 2]:
                    dp[i + 1][j + 2], da[i + 1][j + 2] = costo, (i, j, "21")
    if dp[n][m] > 0.25 * max(1, abs(n - m)) + 0.2 * abs(n - m) + 0.01:
        return None
    etich: list[str] = [""] * n
    i, j = n, m
    while i or j:
        pi, pj, op = da[i][j]
        if op == "11":
            etich[pi] = s[pj]
        elif op == "12":
            etich[pi], etich[pi + 1] = s[pj], ""
        else:
            etich[pi] = s[pj] + s[pj + 1]
        i, j = pi, pj
    return etich


def unisci_glifi(g: GruppoTesto, etich: list[str]) -> list[str]:
    """Unisce i glifi di continuazione (etichetta "") al glifo precedente."""
    if "" not in etich:
        return etich
    nuovi, labs = [], []
    for gl, lab in zip(g.glifi, etich):
        if lab == "" and nuovi:
            p = nuovi[-1]
            p.tratti += gl.tratti
            p.x0, p.x1 = min(p.x0, gl.x0), max(p.x1, gl.x1)
            p.y0, p.y1 = min(p.y0, gl.y0), max(p.y1, gl.y1)
            p.firma = firma_glifo(p, g.pls)
        else:
            nuovi.append(gl)
            labs.append(lab)
    g.glifi = nuovi
    return labs


def spazi_ocr(g: GruppoTesto) -> list[int] | None:
    """Spazi dopo ogni glifo secondo l'OCR (se allineato)."""
    al = allinea_ocr(g)
    if al is None or "" in al:
        return None
    s = g.ocr.strip()
    out = [0] * len(al)
    pos = 0
    for k, lab in enumerate(al):
        while pos < len(s) and s[pos].isspace():
            if k > 0:
                out[k - 1] += 1
            pos += 1
        pos += len(lab)
    return out


class ModelloSpazi:
    """Vuoti tra glifi: gap = R[a] + L[b] (+ k * spazio). Imparato dai
    gruppi con OCR allineato; poi applicato a tutti i gruppi."""

    def __init__(self):
        self.R: dict[int, float] = {}
        self.L: dict[int, float] = {}
        self.spazio: dict[str, float] = {}
        self.base: dict[str, float] = {}

    def impara(self, gruppi: list[GruppoTesto]) -> None:
        coppie, con_spazio = [], []
        for g in gruppi:
            sp = spazi_ocr(g)
            if sp is None or g.ocr_conf < 0.6:
                continue
            h = max(g.altezza, 1e-6)
            for i, (a, b) in enumerate(zip(g.glifi, g.glifi[1:])):
                gap = (b.x0 - a.x1) / h
                if sp[i] == 0:
                    coppie.append((g.font, a.cluster, b.cluster, gap))
                elif sp[i] == 1:
                    con_spazio.append((g.font, a.cluster, b.cluster, gap))
        for font in {c[0] for c in coppie}:
            gaps = [c[3] for c in coppie if c[0] == font]
            self.base[font] = statistics.median(gaps) if gaps else 0.2
        R: dict[int, float] = {}
        L: dict[int, float] = {}
        for _ in range(8):
            accR: dict[int, list] = {}
            accL: dict[int, list] = {}
            for f, a, b, gap in coppie:
                accR.setdefault(a, []).append(gap - L.get(b, self.base[f] / 2))
            R = {k: statistics.median(v) for k, v in accR.items()}
            for f, a, b, gap in coppie:
                accL.setdefault(b, []).append(gap - R.get(a, self.base[f] / 2))
            L = {k: statistics.median(v) for k, v in accL.items()}
        self.R, self.L = R, L
        for font in {c[0] for c in con_spazio}:
            res = [gap - self._rl(f, a, b) for f, a, b, gap in con_spazio if f == font]
            if res:
                self.spazio[font] = max(statistics.median(res), 0.2)

    def _rl(self, font, a, b) -> float:
        b0 = self.base.get(font, 0.2) / 2
        return self.R.get(a, b0) + self.L.get(b, b0)

    def n_spazi(self, g: GruppoTesto, i: int) -> int:
        a, b = g.glifi[i], g.glifi[i + 1]
        h = max(g.altezza, 1e-6)
        gap = (b.x0 - a.x1) / h
        sp = self.spazio.get(g.font, 0.65)
        res = gap - self._rl(g.font, a.cluster, b.cluster)
        if res < 0.5 * sp:
            return 0
        return max(1, int(round(res / sp)))


CONFONDIBILI = [set("O0"), set("S5"), set("B8"), set("Z2"), set("G6"), set("D0"),
                set("Oo"), set("Cc"), set("Ss"), set("Vv"), set("Ww"), set("Xx"), set("Zz")]


def _maggioranza_confondibile(c, lettura: str) -> bool:
    """Il cluster ha una maggioranza chiara (>= 3 voti, >= 70%) su un carattere
    confondibile con quello letto dall'OCR in questa parola (l/I esclusi:
    in ROMANS sono identici, decide il contesto)."""
    lab = c.etichetta
    if not lab or lab == lettura or not c.voti:
        return False
    tot = sum(c.voti.values())
    if tot < 3 or c.voti.get(lab, 0) / tot < 0.7:
        return False
    return any({lab, lettura} <= coppia for coppia in CONFONDIBILI)


def decodifica(g: GruppoTesto, diz: DizionarioGlifi, ms: ModelloSpazi) -> None:
    """Testo finale: per glifo dizionario affidabile > OCR allineato."""
    if not g.glifi:
        return
    al = allinea_ocr(g) if g.ocr_conf >= 0.3 else None
    etich, confs, n_diz = [], [], 0
    for j, gl in enumerate(g.glifi):
        c = diz.cluster[gl.cluster] if gl.cluster >= 0 else None
        if c and c.etichetta and c.netto:
            etich.append(c.etichetta)
            confs.append(c.affidabilita)
            n_diz += 1
        elif al and c and _maggioranza_confondibile(c, al[j]):
            # es. O/0: forme diverse, l'OCR della parola sbaglia, il cluster
            # (voti da piu' testi) no
            etich.append(c.etichetta)
            confs.append(c.affidabilita * 0.9)
            n_diz += 1
        elif al:
            etich.append(al[j])
            confs.append(g.ocr_conf * 0.9)
        elif c and c.etichetta and c.affidabilita >= 0.5:
            etich.append(c.etichetta)
            confs.append(c.affidabilita * 0.8)
            n_diz += 1
        else:
            etich = None
            break
    if etich:
        for gl, lab in zip(g.glifi, etich):
            gl.etichetta = lab
        sp = [ms.n_spazi(g, i) for i in range(len(g.glifi) - 1)] + [0]
        g.testo = "".join(l + " " * n for l, n in zip(etich, sp)).rstrip()
        g.conf = round(min(min(confs), 0.99), 2)
        g.fonte = "dizionario" if n_diz == len(g.glifi) else "ocr"
    elif g.ocr:
        g.testo = g.ocr
        g.conf = round(g.ocr_conf * 0.6, 2)
        g.fonte = "ocr_grezzo"
    else:
        g.testo, g.conf, g.fonte = "", 0.0, ""


def dividi_su_spazi(g: GruppoTesto, ms: ModelloSpazi, soglia: int = 3):
    """Un gruppo con vuoti >= soglia spazi erano testi distinti: separa."""
    if not g.glifi or g.fonte == "ocr_grezzo":
        return [g]
    tagli = [i for i in range(len(g.glifi) - 1) if ms.n_spazi(g, i) >= soglia]
    if not tagli:
        return [g]
    out, inizio = [], 0
    for t in tagli + [len(g.glifi) - 1]:
        parte = g.glifi[inizio:t + 1]
        k_loc = sorted({k for gl in parte for k in gl.tratti})
        remap = {k: n for n, k in enumerate(k_loc)}
        ng = GruppoTesto(idx=[g.idx[k] for k in k_loc], penna=g.penna,
                         colore=g.colore, bbox=g.bbox, rot=g.rot,
                         pls=[g.pls[k] for k in k_loc], base=g.base,
                         altezza=g.altezza, font=g.font, fonte=g.fonte,
                         conf=g.conf, ocr=g.ocr, ocr_conf=g.ocr_conf)
        for gl in parte:
            ng.glifi.append(Glifo([remap[k] for k in gl.tratti], gl.x0, gl.x1,
                                  gl.y0, gl.y1, gl.firma, gl.cluster, gl.etichetta))
        sp = [ms.n_spazi(ng, i) for i in range(len(ng.glifi) - 1)] + [0]
        ng.testo = "".join(gl.etichetta + " " * n
                           for gl, n in zip(ng.glifi, sp)).rstrip()
        out.append(ng)
        inizio = t + 1
    return out


def _margini_hershey() -> dict:
    """Margine sinistro dei glifi Hershey (romans.shx ne deriva), in altezze."""
    try:
        from . import hershey
        out = {}
        for c, (L, R, tratti) in hershey._carica().items():
            xs = [p[0] for t in tratti for p in t]
            if xs:
                out[c] = (min(xs) - L) / 21.0
        return out
    except Exception:  # noqa: BLE001 - dati Hershey assenti
        return {}


class ModelloMetriche:
    """Avanzamento A e margine sinistro B di ogni carattere, per font.

    Da coppie di glifi consecutivi: (x0_b - x0_a)/h = A_a + k*spazio + B_b - B_a.
    B e' definito a meno di una costante: la fissa un riferimento debole
    (margini Hershey per ROMANS, 0.05 h per gli altri). Serve a mettere il
    punto di inserimento dei TEXT dove lo metterebbe SPAC.
    """

    def __init__(self):
        self.B: dict[str, dict[str, float]] = {}

    def impara(self, gruppi: list[GruppoTesto], ms: ModelloSpazi) -> None:
        prior_romans = _margini_hershey()
        per_font: dict = {}
        for g in gruppi:
            if not g.accettato or g.obliquo or g.fonte not in ("dizionario", "ocr"):
                continue
            labs = [gl.etichetta for gl in g.glifi]
            if len(labs) < 2 or any(len(l) != 1 for l in labs):
                continue
            h = max(g.altezza, 1e-6)
            sp = [ms.n_spazi(g, i) for i in range(len(labs) - 1)]
            for i in range(len(labs) - 1):
                per_font.setdefault(g.font, []).append(
                    (labs[i], labs[i + 1], sp[i], (g.glifi[i + 1].x0 - g.glifi[i].x0) / h))
        for font, oss in per_font.items():
            car = sorted({a for a, _, _, _ in oss} | {b for _, b, _, _ in oss})
            if len(oss) < 20:
                continue
            ix = {c: i for i, c in enumerate(car)}
            n = len(car)
            nA, nB = n, n  # incognite: A (n), B (n), spazio (1)
            righe, noti = [], []
            for a, b, k, d in oss:
                r = np.zeros(nA + nB + 1)
                r[ix[a]] += 1
                r[nA + ix[b]] += 1
                r[nA + ix[a]] -= 1
                r[-1] = k
                righe.append(r)
                noti.append(d)
            lam = 0.3
            for c in car:
                r = np.zeros(nA + nB + 1)
                r[nA + ix[c]] = lam
                prior = prior_romans.get(c, 0.05) if font == "ROMANS" else 0.05
                righe.append(r)
                noti.append(lam * prior)
            sol, *_ = np.linalg.lstsq(np.array(righe), np.array(noti), rcond=None)
            self.B[font] = {c: float(sol[nA + ix[c]]) for c in car}

    def margine(self, font: str, c: str) -> float:
        tab = self.B.get(font, {})
        if c in tab:
            return min(max(tab[c], -0.1), 0.5)
        if font == "ROMANS":
            return _margini_hershey().get(c, 0.05)
        return 0.05


class IndiceSegmenti:
    """Segmenti lunghi di una pagina (bordi di tabelle, fili) in una griglia."""

    CELLA = 10.0

    def __init__(self, tratti: list[Tratto], lunghezza_min: float = 2.0):
        self.griglia: dict = {}
        for t in tratti:
            if t.tipo != "s" or t.dim < lunghezza_min:
                continue
            for it in t.items:
                if it[0] != "l":
                    continue
                a, b = it[1], it[2]
                if math.dist(a, b) < lunghezza_min:
                    continue
                x0, x1 = sorted((a[0], b[0]))
                y0, y1 = sorted((a[1], b[1]))
                for gx in range(int(x0 // self.CELLA), int(x1 // self.CELLA) + 1):
                    for gy in range(int(y0 // self.CELLA), int(y1 // self.CELLA) + 1):
                        self.griglia.setdefault((gx, gy), []).append((a, b))

    def vicini(self, bbox: tuple) -> list:
        x0, y0, x1, y1 = bbox
        out, visti = [], set()
        for gx in range(int(x0 // self.CELLA), int(x1 // self.CELLA) + 1):
            for gy in range(int(y0 // self.CELLA), int(y1 // self.CELLA) + 1):
                for s in self.griglia.get((gx, gy), []):
                    if id(s) not in visti:
                        visti.add(id(s))
                        out.append(s)
        return out


def dividi_su_linee(g: GruppoTesto, indice: IndiceSegmenti, ms: ModelloSpazi,
                    tratti: list[Tratto]) -> list[GruppoTesto]:
    """Separa i testi attraversati da una linea tra due glifi (bordi di
    cella: in una tabella ogni cella ha il suo TEXT)."""
    if len(g.glifi) < 2 or not g.testo or not all(gl.etichetta for gl in g.glifi):
        return [g]
    h = max(g.altezza, 0.3)
    segs = [(_ruota(a, g.rot), _ruota(b, g.rot)) for a, b in indice.vicini(g.bbox)]
    tagli = []
    for i, (a, b) in enumerate(zip(g.glifi, g.glifi[1:])):
        if b.x0 - a.x1 < 0.15 * h or ms.n_spazi(g, i) == 0:
            continue  # lettere attaccate: un testo che sborda dalla cella resta unito
        for p, q in segs:
            if abs(q[0] - p[0]) > 0.3 * abs(q[1] - p[1]):
                continue  # non verticale nel sistema di lettura
            xm = (p[0] + q[0]) / 2
            if a.x1 < xm < b.x0 and min(p[1], q[1]) <= g.base + 0.3 * h \
                    and max(p[1], q[1]) >= g.base + 0.7 * h:
                tagli.append(i)
                break
    # non staccare una lettera isolata (es. "x | connettore" attraversato da
    # una freccia): nelle tabelle i pezzi singoli sono cifre
    bordi = [-1] + tagli + [len(g.glifi) - 1]
    buoni = []
    for k, t in enumerate(tagli):
        sx = g.glifi[bordi[k] + 1:t + 1]
        dx = g.glifi[t + 1:bordi[k + 2] + 1]
        if (len(sx) == 1 and not sx[0].etichetta.isdigit()) or \
                (len(dx) == 1 and not dx[0].etichetta.isdigit()):
            continue
        buoni.append(t)
    tagli = buoni
    if not tagli:
        return [g]
    out, inizio = [], 0
    for t in tagli + [len(g.glifi) - 1]:
        parte = g.glifi[inizio:t + 1]
        k_loc = sorted({k for gl in parte for k in gl.tratti})
        remap = {k: n for n, k in enumerate(k_loc)}
        idx = [g.idx[k] for k in k_loc]
        ng = GruppoTesto(idx=idx, penna=g.penna, colore=g.colore,
                         bbox=_bbox_disegno_idx(idx, tratti), rot=g.rot,
                         pls=[g.pls[k] for k in k_loc],
                         base=g.base, altezza=g.altezza, font=g.font, fonte=g.fonte,
                         conf=g.conf, ocr=g.ocr, ocr_conf=g.ocr_conf,
                         obliquo=g.obliquo, accettato=g.accettato, nota="diviso su linea")
        for gl in parte:
            ng.glifi.append(Glifo([remap[k] for k in gl.tratti], gl.x0, gl.x1, gl.y0,
                                  gl.y1, gl.firma, gl.cluster, gl.etichetta,
                                  gl.inchiostro))
        sp = [ms.n_spazi(ng, i) for i in range(len(ng.glifi) - 1)] + [0]
        ng.testo = "".join(gl.etichetta + " " * n for gl, n in zip(ng.glifi, sp)).rstrip()
        out.append(ng)
        inizio = t + 1
    return out


def punto_inserimento(g: GruppoTesto, mm: ModelloMetriche | None = None) -> None:
    """Punto base (sinistra, sulla baseline) in coordinate disegno."""
    if not g.glifi:
        return
    c0 = g.glifi[0].etichetta or (g.testo[:1] if g.testo else "")
    b = mm.margine(g.font, c0) if mm else 0.05
    x = g.glifi[0].x0 - b * g.altezza
    if g.obliquo:  # il piede del primo glifo e' spostato dall'inclinazione
        x -= 0.0
    g.x_ins, g.y_ins = _ruota_inv((x, g.base), g.rot)


def _bbox_disegno_idx(idx: list, tratti: list[Tratto]) -> tuple:
    b = tratti[idx[0]].bbox
    for i in idx[1:]:
        b = _unione(b, tratti[i].bbox)
    return b


def _copertura_pieni(bbox: tuple, pieni: list[tuple]) -> int:
    x0, y0, x1, y1 = bbox
    n = 0
    for p in pieni:
        cx, cy = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            n += 1
    return n


def normalizza_altezze(gruppi: list[GruppoTesto]) -> None:
    """Altezze misurate simili (entro 4%) -> stesso valore nel documento."""
    hs = sorted(g.altezza for g in gruppi if g.altezza > 0)
    if not hs:
        return
    classi, cur = [], [hs[0]]
    for h in hs[1:]:
        if h - cur[0] <= 0.04 * cur[0]:
            cur.append(h)
        else:
            classi.append(cur)
            cur = [h]
    classi.append(cur)
    mappa = {}
    for c in classi:
        v = altezza_standard(statistics.median(c))
        for h in c:
            mappa[h] = v
    for g in gruppi:
        if g.altezza in mappa:
            g.altezza = mappa[g.altezza]


CASO_DA_ALTEZZA = set("cCoOsSvVwWxXzZuU")
CASO_DA_DISCENDENTE = {"p": "P", "y": "Y"}


def correggi_maiuscole(g: GruppoTesto, ms: ModelloSpazi) -> None:
    """Maiuscole/minuscole con la stessa forma (c/C, o/O, v/V, ...): decide
    l'altezza del glifo rispetto a quella delle maiuscole del testo (le
    minuscole arrivano all'altezza x); p/P e y/Y: il discendente."""
    if not g.glifi or not all(gl.etichetta for gl in g.glifi) or g.altezza <= 0:
        return
    h, cambiato = g.altezza, False
    for gl in g.glifi:
        lab = gl.etichetta
        if len(lab) != 1:
            continue
        rel_top = (gl.y1 - g.base) / h
        sotto = (g.base - gl.y0) / h
        nuovo = lab
        if lab in CASO_DA_ALTEZZA:
            if rel_top < 0.83:
                nuovo = lab.lower()
            elif rel_top > 0.92:
                nuovo = lab.upper()
        elif lab.lower() in CASO_DA_DISCENDENTE:
            nuovo = lab.lower() if sotto > 0.15 else lab.upper()
        if nuovo != lab:
            gl.etichetta, cambiato = nuovo, True
    if cambiato:
        sp = [ms.n_spazi(g, i) for i in range(len(g.glifi) - 1)] + [0]
        g.testo = "".join(gl.etichetta + " " * n for gl, n in zip(g.glifi, sp)).rstrip()


def dividi_su_colonne(gruppi: list[GruppoTesto], ms: ModelloSpazi,
                      tratti: list[Tratto]) -> list[GruppoTesto]:
    """Una parola che inizia dove iniziano (entro 0.25 mm) almeno altri due
    testi della pagina e' l'inizio di un'altra colonna: testo separato
    (es. "white/brown" | "EV16" con EV15, EV14... incolonnati sotto)."""
    acc = [g for g in gruppi if g.accettato and g.glifi]
    inizi = [(g.rot, round(g.altezza, 1), g.glifi[0].x0, id(g)) for g in acc]
    out = []
    for g in gruppi:
        if not g.accettato or len(g.glifi) < 2 or " " not in g.testo \
                or not all(gl.etichetta for gl in g.glifi):
            out.append(g)
            continue
        tagli = []
        for i in range(len(g.glifi) - 1):
            if ms.n_spazi(g, i) < 1:
                continue
            x = g.glifi[i + 1].x0
            simili = sum(1 for r, h, x0, k in inizi if k != id(g) and r == g.rot
                         and abs(h - g.altezza) <= 0.15 * g.altezza and abs(x0 - x) <= 0.25)
            if simili >= 2:
                tagli.append(i)
        if not tagli:
            out.append(g)
            continue
        inizio = 0
        for t in tagli + [len(g.glifi) - 1]:
            parte = g.glifi[inizio:t + 1]
            k_loc = sorted({k for gl in parte for k in gl.tratti})
            remap = {k: n for n, k in enumerate(k_loc)}
            idx = [g.idx[k] for k in k_loc]
            ng = GruppoTesto(idx=idx, penna=g.penna, colore=g.colore,
                             bbox=_bbox_disegno_idx(idx, tratti), rot=g.rot,
                             pls=[g.pls[k] for k in k_loc], base=g.base,
                             altezza=g.altezza, font=g.font, fonte=g.fonte, conf=g.conf,
                             ocr=g.ocr, ocr_conf=g.ocr_conf, obliquo=g.obliquo,
                             accettato=True, nota="diviso su colonna")
            for gl in parte:
                ng.glifi.append(Glifo([remap[k] for k in gl.tratti], gl.x0, gl.x1, gl.y0,
                                      gl.y1, gl.firma, gl.cluster, gl.etichetta,
                                      gl.inchiostro))
            sp = [ms.n_spazi(ng, i) for i in range(len(ng.glifi) - 1)] + [0]
            ng.testo = "".join(gl.etichetta + " " * n
                               for gl, n in zip(ng.glifi, sp)).rstrip()
            out.append(ng)
            inizio = t + 1
    return out


def _seq(g: GruppoTesto, tratti: list[Tratto]) -> tuple[int, int]:
    s = [tratti[i].seq for i in g.idx]
    return min(s), max(s)


def unisci_adiacenti(gruppi: list[GruppoTesto], tratti: list[Tratto],
                     ms: ModelloSpazi) -> list[GruppoTesto]:
    """Riunisce pezzi dello stesso TEXT separati dal raggruppamento (es. "a" +
    "vite con plastica", "x" + "connettore a vite"): consecutivi nella
    sequenza del plotter, stessa riga, stessa altezza, distanza di uno spazio.
    Un glifo singolo scartato viene recuperato se e' un carattere alfanumerico."""
    def singolo_ok(g):
        lab = g.testo or (g.glifi[0].etichetta if g.glifi else "") or g.ocr
        return len(g.glifi) == 1 and len(lab.strip()) == 1 and lab.strip().isalnum()

    ordinati = sorted(gruppi, key=lambda g: _seq(g, tratti)[0])
    out: list[GruppoTesto] = []
    for g in ordinati:
        if out and g.glifi and out[-1].glifi:
            a = out[-1]
            ok_a = a.accettato or singolo_ok(a)
            ok_g = g.accettato or singolo_ok(g)
            h = max(a.altezza, g.altezza, 0.3)
            vicini = 0 < _seq(g, tratti)[0] - _seq(a, tratti)[1] <= 2
            # un glifo singolo ha l'altezza di se stesso (minuscola = altezza x)
            if len(a.glifi) == 1 or len(g.glifi) == 1:
                alt_ok = 0.4 * h <= min(a.altezza, g.altezza) and \
                    max(a.altezza, g.altezza) <= 1.15 * max(
                        a.altezza if len(a.glifi) > 1 else 0, g.altezza if len(g.glifi) > 1 else 0,
                        min(a.altezza, g.altezza))
            else:
                alt_ok = abs(a.altezza - g.altezza) <= 0.2 * h
            stessa = (a.rot == g.rot and abs(a.penna - g.penna) < 0.01
                      and abs(a.base - g.base) <= 0.12 * h and alt_ok
                      and 0.2 * h < g.glifi[0].x0 - a.glifi[-1].x1 < 2.2 * h)
            if ok_a and ok_g and vicini and stessa and (a.accettato or g.accettato):
                def _testo(x, altro):
                    t = (x.testo or x.glifi[0].etichetta or x.ocr).strip()
                    if not x.accettato and len(t) == 1 and t.isalpha() \
                            and (x.glifi[0].y1 - x.glifi[0].y0) < 0.8 * altro.altezza:
                        t = t.lower()  # alta come una minuscola: e' minuscola
                    return t
                ta, tg = _testo(a, g), _testo(g, a)
                n0 = len(a.pls)
                m = GruppoTesto(idx=a.idx + g.idx, penna=a.penna, colore=a.colore,
                                bbox=_unione(a.bbox, g.bbox), rot=a.rot, pls=a.pls + g.pls,
                                base=a.base, altezza=max(a.altezza, g.altezza), font=a.font,
                                fonte=a.fonte if a.accettato else g.fonte,
                                conf=min(x.conf for x in (a, g) if x.accettato),
                                ocr=(a.ocr + " " + g.ocr).strip(),
                                ocr_conf=min(a.ocr_conf, g.ocr_conf), obliquo=a.obliquo,
                                accettato=True, nota="unito al pezzo adiacente")
                m.glifi = a.glifi + [Glifo([k + n0 for k in gl.tratti], gl.x0, gl.x1,
                                           gl.y0, gl.y1, gl.firma, gl.cluster,
                                           gl.etichetta, gl.inchiostro) for gl in g.glifi]
                n = max(1, ms.n_spazi(m, len(a.glifi) - 1))
                m.testo = ta + " " * n + tg
                out[-1] = m
                continue
        out.append(g)
    return out


def armonizza_rotazioni(gruppi: list[GruppoTesto], tratti: list[Tratto],
                        diz: DizionarioGlifi, ms: ModelloSpazi) -> None:
    """Testi di un solo carattere: l'orientamento non si deduce dalla forma
    del gruppo. Se i testi vicini (stessa penna) sono ruotati in altro modo
    e il glifo, letto con quella rotazione, e' un carattere noto del
    dizionario, si adotta la rotazione dei vicini (es. numeri pin a 90 gradi)."""
    import collections
    accettati = [g for g in gruppi if g.accettato]
    # font dai vicini: cifre e sigle corte senza archi (1, 7, 17, E, L...) non
    # permettono di distinguere ISOCP da ROMANS dalla sola forma dei tratti
    for g in accettati:
        if g.font != "ROMANS" or len(g.testo.replace(" ", "")) > 3                 or any(tratti[i].ha_curve for i in g.idx):
            continue
        cx, cy = (g.bbox[0] + g.bbox[2]) / 2, (g.bbox[1] + g.bbox[3]) / 2
        fonti = collections.Counter(
            v.font for v in accettati if v is not g
            and abs(v.altezza - g.altezza) <= 0.15 * max(g.altezza, 0.5)
            and math.hypot((v.bbox[0] + v.bbox[2]) / 2 - cx,
                           (v.bbox[1] + v.bbox[3]) / 2 - cy) <= max(8.0, 5 * g.altezza))
        if fonti.get("ISOCP", 0) > fonti.get("ROMANS", 0):
            g.font = "ISOCP"
    for g in gruppi:
        if len(g.glifi) != 1:
            continue
        if not g.accettato and g.nota != "glifo singolo non confermato":
            continue
        cx = (g.bbox[0] + g.bbox[2]) / 2
        cy = (g.bbox[1] + g.bbox[3]) / 2
        raggio = max(6.0, 4 * g.altezza)
        rot_vicini = collections.Counter(
            v.rot for v in accettati if v is not g and len(v.glifi) >= 1
            and abs(v.penna - g.penna) < 0.01
            and math.hypot((v.bbox[0] + v.bbox[2]) / 2 - cx,
                           (v.bbox[1] + v.bbox[3]) / 2 - cy) <= raggio)
        if not rot_vicini:
            continue
        rot, n = rot_vicini.most_common(1)[0]
        if rot == g.rot or n < 2:
            continue
        grezzo = GruppoTesto(idx=g.idx, penna=g.penna, colore=g.colore, bbox=g.bbox)
        _, righe = analizza_orientamento(grezzo, tratti, rot, usa_ocr=False)
        if len(righe) != 1 or len(righe[0].glifi) != 1:
            continue
        r = righe[0]
        c, d = diz.trova(r.glifi[0], r, crea=False)
        if c is None or not c.netto or not c.etichetta:
            continue
        r.glifi[0].cluster = c.id
        r.glifi[0].etichetta = c.etichetta
        r.testo, r.fonte, r.conf = c.etichetta, "dizionario", round(min(c.affidabilita, 0.99), 2)
        r.base, r.altezza = stima_base_altezza(r.glifi, etichette=True,
                                               x_ratio=RAPPORTO_X.get(r.font, 0.70))
        r.accettato, r.ocr, r.ocr_conf, r.nota = True, g.ocr, g.ocr_conf, "rotazione dai vicini"
        g.__dict__.update(r.__dict__)


def riconosci_pagine(pagine: list, usa_ocr: bool = True,
                     diz: DizionarioGlifi | None = None,
                     etichette_ai: dict | None = None):
    """Riconosce i testi esplosi su piu' pagine con un dizionario comune.

    pagine: liste di Tratto (una per pagina, ordinate per sequenza).
    etichette_ai: {id_cluster: carattere} fornite dall'AI (tavola glifi).
    Ritorna (lista gruppi per pagina, dizionario, modello spazi).
    """
    diz = diz or DizionarioGlifi()
    risultati = []
    for tratti in pagine:
        pieni = [t.bbox for t in tratti if t.tipo in ("f", "fs")]
        gruppi = []
        for g in raggruppa(tratti):
            if len(g.idx) == 1 and len(tratti[g.idx[0]].items) == 1 \
                    and tratti[g.idx[0]].items[0][0] == "l":
                continue  # segmento isolato: e' geometria
            if solo_cerchi(g.idx, tratti) or e_tratteggio(g.idx, tratti):
                continue
            g.bbox = _bbox_disegno_idx(g.idx, tratti)
            if _copertura_pieni(g.bbox, pieni) >= 3:
                continue  # contorni di un pieno (logo, marchio): geometria
            gruppi += prepara_gruppo(g, tratti, usa_ocr)
        risultati.append(gruppi)
    # passo 1: cluster + voti OCR (gruppi di almeno 2 glifi, conteggio coerente)
    def _vota(p, k, g, vota=True):
        al = allinea_ocr(g) if (vota and g.ocr_conf >= 0.6 and len(g.glifi) >= 2) else None
        for j, gl in enumerate(g.glifi):
            c, _ = diz.trova(gl, g)
            gl.cluster = c.id
            c.n += 1
            if not c.esempio:
                c.esempio = (p, k, j)
            if al:
                c.voti[al[j]] = c.voti.get(al[j], 0) + 1
                c.fonti.add(g.ocr)

    for p, gruppi in enumerate(risultati):
        for k, g in enumerate(gruppi):
            _vota(p, k, g)
    for cid, lab in (etichette_ai or {}).items():
        cid = int(cid)
        if 0 <= cid < len(diz.cluster):
            diz.cluster[cid].etichetta_ai = lab
    # passo 1b: riorientamento dei gruppi letti male, giudicato sul dizionario
    for p, (tratti, gruppi) in enumerate(zip(pagine, risultati)):
        nuovi = []
        for g in gruppi:
            buono = g.ocr_conf >= 0.75 and allinea_ocr(g) is not None
            base_rate = corrispondenza_dizionario([g], diz) if g.glifi else 1.0
            if buono or base_rate >= 0.9:
                nuovi.append(g)
                continue
            migliore = None
            for rot in ALTERNATIVI.get(g.rot, []):
                grezzo = GruppoTesto(idx=g.idx, penna=g.penna, colore=g.colore,
                                     bbox=g.bbox)
                _, righe = analizza_orientamento(grezzo, tratti, rot, usa_ocr=False)
                rate = corrispondenza_dizionario(righe, diz)
                if migliore is None or rate > migliore[0]:
                    migliore = (rate, rot)
            if migliore and migliore[0] >= 0.75 and migliore[0] > base_rate + 0.25:
                grezzo = GruppoTesto(idx=g.idx, penna=g.penna, colore=g.colore,
                                     bbox=g.bbox)
                _, righe = analizza_orientamento(grezzo, tratti, migliore[1], usa_ocr)
                for r in righe:
                    _vota(p, len(nuovi), r, vota=False)
                    nuovi.append(r)
            else:
                nuovi.append(g)
        risultati[p] = nuovi
    ms = ModelloSpazi()
    ms.impara([g for gs in risultati for g in gs])
    tipiche = proporzioni_tipiche(diz)
    # passo 2: decodifica, divisione, altezza, obliquo, accettazione
    finali = []
    for p, gruppi in enumerate(risultati):
        indice = IndiceSegmenti(pagine[p])
        out = []
        for g in gruppi:
            decodifica(g, diz, ms)
            for ng in [x for y in dividi_su_spazi(g, ms)
                       for x in dividi_su_linee(y, indice, ms, pagine[p])]:
                if ng.testo and ng.fonte != "ocr_grezzo":
                    xr = RAPPORTO_X.get(ng.font, 0.70)
                    ng.base, ng.altezza = stima_base_altezza(ng.glifi, etichette=True,
                                                             x_ratio=xr)
                    prima = ng.testo
                    correggi_maiuscole(ng, ms)
                    if ng.testo != prima:  # una "V" era una "v": ricalcola l'altezza
                        ng.base, ng.altezza = stima_base_altezza(ng.glifi, etichette=True,
                                                                 x_ratio=xr)
                        correggi_maiuscole(ng, ms)
                    ng.obliquo = obliquo_da_aste(ng)
                else:
                    ng.obliquo = obliquo_grezzo(ng)
                ng.accettato = _accetta(ng, diz, tipiche)
                out.append(ng)
        armonizza_rotazioni(out, pagine[p], diz, ms)
        out = unisci_adiacenti(out, pagine[p], ms)
        out = dividi_su_colonne(out, ms, pagine[p])
        finali.append(out)
    tutti = [g for gs in finali for g in gs if g.accettato]
    # testi cortissimi molto piu' alti del normale: quasi sempre simboli
    lunghi = [g.altezza for g in tutti if len(g.testo.replace(" ", "")) >= 3]
    h_tipica = statistics.median(lunghi) if lunghi else 0.0
    for g in tutti:
        corto = len(g.testo.replace(" ", "")) <= 3
        netti = all(diz.cluster[gl.cluster].netto for gl in g.glifi if gl.cluster >= 0)
        if h_tipica and corto and g.altezza > 3 * h_tipica \
                and not (netti and g.ocr_conf >= 0.9):
            g.accettato, g.nota = False, "altezza anomala per un testo corto"
    tutti = [g for g in tutti if g.accettato]
    normalizza_altezze(tutti)
    mm = ModelloMetriche()
    mm.impara(tutti, ms)
    for p, gs in enumerate(finali):
        for g in gs:
            # id stabile tra riconversioni: sequenza del primo tratto
            g.id = f"p{p + 1}-s{min(pagine[p][i].seq for i in g.idx)}"
            punto_inserimento(g, mm)
    return finali, diz, ms


def _glifi_circolari(g: GruppoTesto) -> bool:
    """Tutti i glifi sono cerchi (distanza costante dal centro)."""
    for gl in g.glifi:
        pts = [p for k in gl.tratti for sub in g.pls[k] for p in sub]
        if len(pts) < 6:
            return False
        w, h = gl.x1 - gl.x0, gl.y1 - gl.y0
        if not (0.85 <= w / max(h, 1e-6) <= 1.15):
            return False
        cx, cy = (gl.x0 + gl.x1) / 2, (gl.y0 + gl.y1) / 2
        r = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
        rm = sum(r) / len(r)
        if rm <= 0 or max(abs(v - rm) for v in r) > 0.12 * rm:
            return False
    return True


def proporzioni_tipiche(diz: DizionarioGlifi) -> dict:
    """(font, carattere) -> (larghezza, altezza) relative tipiche dai cluster netti."""
    acc: dict = {}
    for c in diz.cluster:
        if c.netto and c.etichetta and len(c.etichetta) == 1:
            acc.setdefault((c.font, c.etichetta), []).append((c.rel_w, c.rel_h, c.n))
    out = {}
    for k, v in acc.items():
        tot = sum(n for _, _, n in v)
        out[k] = (sum(w * n for w, _, n in v) / tot, sum(h * n for _, h, n in v) / tot)
    return out


def proporzioni_anomale(g: GruppoTesto, tipiche: dict) -> int:
    """Quanti glifi hanno proporzioni incompatibili con il carattere letto
    (es. due linee incrociate lunghe 5 mm lette come "X")."""
    n = 0
    h = max(g.altezza, 1e-6)
    for gl in g.glifi:
        if not gl.etichetta.isalnum():
            continue  # parentesi e punteggiatura variano troppo tra stili
        t = tipiche.get((g.font, gl.etichetta))
        if not t:
            continue
        rw, rh = (gl.x1 - gl.x0) / h, (gl.y1 - gl.y0) / h
        if abs(rw - t[0]) > 0.5 * t[0] + 0.15 or abs(rh - t[1]) > 0.4 * t[1] + 0.12:
            n += 1
    return n


def _accetta(g: GruppoTesto, diz: DizionarioGlifi, tipiche: dict | None = None) -> bool:
    t = g.testo.strip() if g.testo else ""
    if not t:
        g.nota = "nessuna lettura"
        return False
    if any(c not in CARATTERI_OK for c in t):
        g.nota = "caratteri non ammessi"
        return False
    compatto = t.replace(" ", "")
    if set(compatto) <= SOLO_TRATTINI and len(compatto) >= 3:
        g.nota = "tratteggio, non testo"
        return False
    if set(compatto) <= set("()[]{}<>|'\"`"):
        g.nota = "solo parentesi/apici: simbolo, non testo"
        return False
    if set(compatto) <= set("oO°") and _glifi_circolari(g):
        g.nota = "cerchi (pin/morsetti), non lettere"
        return False
    if g.altezza < 0.4:
        g.nota = "altezza nulla"
        return False
    if tipiche and g.glifi and all(gl.etichetta for gl in g.glifi):
        anom = proporzioni_anomale(g, tipiche)
        if anom and (len(g.glifi) <= 4 or anom >= 0.3 * len(g.glifi)):
            g.nota = "proporzioni dei glifi incompatibili con il testo letto"
            return False
    if len(g.glifi) == 1:
        c = diz.cluster[g.glifi[0].cluster]
        confermato = g.fonte == "dizionario" and c.netto and c.etichetta == t
        # una lettera isolata letta solo dall'OCR e' spesso un simbolo (contatti
        # ")-" letti "Y", archi letti "C"): per le lettere serve il dizionario
        ocr_forte = g.ocr_conf >= 0.9 and t.isdigit() and c.etichetta in ("", t) \
            and not _poligono_semplice([g.pls[k] for k in g.glifi[0].tratti])
        if not (confermato or ocr_forte):
            g.nota = "glifo singolo non confermato"
            return False
    if g.fonte == "ocr_grezzo":
        n = len(t.replace(" ", ""))
        if g.conf < 0.45:
            g.nota = "OCR poco affidabile"
            return False
        # font "complex"/corsivi con grazie: piu' tratti (e glifi) per lettera
        minimo = 0.3 if (g.conf >= 0.5 and n >= 3) else 0.6
        if not (minimo * len(g.glifi) <= n <= 1.6 * len(g.glifi) + 1):
            g.nota = "lunghezza incoerente con i glifi"
            return False
    if g.fonte == "ocr" and g.conf < 0.4:
        g.nota = "OCR poco affidabile"
        return False
    return True
