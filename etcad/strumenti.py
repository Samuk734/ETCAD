"""Strumenti di lavoro sui disegni: confronti, sostituzioni, cartiglio, controlli, fili, posti liberi, copie, consegna e distinta.

- confronto del contenuto fra due DWG o due cartelle (l'MD5 dei DWG di ODA
  cambia a ogni conversione anche a contenuto uguale);
- cerca e sostituisci nei testi, attributi e blocchi, su un file o una cartella;
- cartiglio: attributi per tag (DATA, DISEGNATO...) e pulizia di una zona;
- controllo grafico su file (testi sovrapposti o fuori cella), anche per foglio;
- tracciamento dei fili da un punto (capi, testi vicini, lunghezza);
- posto libero per una scritta nuova;
- copia di una zona da un disegno all'altro, anche allineata sulla cornice;
- consegna di una cartella (copia + MD5 + cosa e' cambiato davvero);
- larghezza di un testo nel suo font; distinta dei materiali da una scheda cavo.

Tutte le scritture passano da lettura._riedita_dwg: DWG nativo e backup.
Uso da riga di comando: python -m etcad.strumenti <comando> --help
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import math
import os
import re
import shutil
from collections import Counter, defaultdict

from . import hershey

CODICI = {"Ø": "%%c", "°": "%%d", "±": "%%p"}


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------

def leggi(percorso: str):
    """DWG/DXF -> documento ezdxf (DWG convertito con cache)."""
    import ezdxf
    from .lettura import ensure_dxf
    return ezdxf.readfile(ensure_dxf(os.path.abspath(percorso)))


def _r(v, n=2) -> float:
    v = round(float(v), n)
    return 0.0 if v == 0 else v


def _bbox(e):
    try:
        from ezdxf import bbox
        b = bbox.extents([e], fast=True)
        if b.has_data:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:  # noqa: BLE001
        pass
    return None


def _dentro(b, z, tol=0.0) -> bool:
    return b is not None and b[0] >= z[0] - tol and b[1] >= z[1] - tol and b[2] <= z[2] + tol and b[3] <= z[3] + tol


def _tocca(b, z) -> bool:
    return b is not None and not (b[2] < z[0] or b[0] > z[2] or b[3] < z[1] or b[1] > z[3])


def _font_stile(doc, nome: str) -> str:
    try:
        return (doc.styles.get(nome).dxf.font or "").lower()
    except Exception:  # noqa: BLE001
        from .pdf2dwg import STILI_SHX
        return STILI_SHX.get((nome or "").upper(), "romans.shx")


def _shx(font: str) -> bool:
    return not font.lower().endswith((".ttf", ".otf", ".ttc"))


def larghezza_testo(testo: str, altezza: float, stile: str = "ROMANS", wf: float = 1.0, doc=None,
                    font: str = "") -> float:
    """Larghezza in mm di un testo nel suo font: SHX con le larghezze Hershey (ISOCP x0,87),
    TrueType con il font vero di Windows. stile = nome dello stile (o font = file del font)."""
    from .pdf2dwg import STILI_SHX, TTF
    from .vista.conversione import famiglia, larghezza_ttf
    f = (font or (_font_stile(doc, stile) if doc is not None else "")).lower()
    if not f:
        f = STILI_SHX.get((stile or "").upper(), "")
        if not f:
            f = next((ttf for _, st, ttf in TTF if st == (stile or "").upper()), "romans.shx")
    t = testo
    for k, v in CODICI.items():
        t = t.replace(v, k).replace(v.upper(), k)
    if _shx(f):
        nome = f if f.endswith(".shx") else f + ".shx"
        return hershey.larghezza(t, altezza, wf * hershey.SCALA_X.get(nome, 1.0))
    fam, bold = famiglia(f)
    return larghezza_ttf(t, altezza, wf, fam, bold)


def _testo_entita(e) -> str:
    t = e.dxftype()
    if t == "MTEXT":
        return e.text
    return e.dxf.get("text", "")


def _testi(doc, blocchi: bool = True):
    """(entita', dove, insert) per ogni TEXT/MTEXT/ATTRIB/ATTDEF modificabile."""
    msp = doc.modelspace()
    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            yield e, "modello", None
        elif t == "INSERT":
            for a in e.attribs:
                yield a, "attributo", e
    if blocchi:
        for b in doc.blocks:
            if b.name.startswith("*") or b.is_any_layout:
                continue
            for e in b:
                if e.dxftype() in ("TEXT", "MTEXT", "ATTDEF"):
                    yield e, f"blocco {b.name}", None


# ---------------------------------------------------------------------------
# confronto del contenuto
# ---------------------------------------------------------------------------

def firma(e, n: int = 2) -> str:
    """Firma del contenuto di un'entita' (non dipende dall'handle)."""
    t = e.dxftype()
    d = e.dxf
    base = [t, d.get("layer", "0"), d.get("color", 256), d.get("linetype", "BYLAYER"), d.get("lineweight", -1)]
    try:
        if t == "LINE":
            p = sorted([(_r(d.start[0], n), _r(d.start[1], n)), (_r(d.end[0], n), _r(d.end[1], n))])
            base.append(p)
        elif t == "LWPOLYLINE":
            base.append([(_r(x, n), _r(y, n), _r(b, 3)) for x, y, b in e.get_points("xyb")])
            base.append(bool(e.closed))
        elif t in ("CIRCLE", "ARC"):
            base += [_r(d.center[0], n), _r(d.center[1], n), _r(d.radius, n)]
            if t == "ARC":
                base += [_r(d.start_angle, 1), _r(d.end_angle, 1)]
        elif t in ("TEXT", "ATTRIB", "ATTDEF"):
            base += [d.get("text", ""), _r(d.insert[0], n), _r(d.insert[1], n), _r(d.get("height", 0), n),
                     _r(d.get("rotation", 0), 1), d.get("style", ""), d.get("halign", 0), d.get("valign", 0),
                     _r(d.get("width", 1), 3), d.get("tag", "") if t != "TEXT" else ""]   # TEXT non ha "tag"
            if d.get("halign", 0) or d.get("valign", 0):
                ap = d.get("align_point", d.insert)
                base += [_r(ap[0], n), _r(ap[1], n)]
        elif t == "MTEXT":
            base += [e.text, _r(d.insert[0], n), _r(d.insert[1], n), _r(d.get("char_height", 0), n),
                     d.get("attachment_point", 1), d.get("style", "")]
        elif t == "INSERT":
            base += [d.name, _r(d.insert[0], n), _r(d.insert[1], n), _r(d.get("xscale", 1), 4),
                     _r(d.get("yscale", 1), 4), _r(d.get("rotation", 0), 2),
                     sorted((a.dxf.tag, a.dxf.text) for a in e.attribs)]
        elif t == "IMAGE":
            base += [[_r(v, n) for v in d.insert][:2], [_r(v, 3) for v in d.u_pixel][:2],
                     [_r(v, 3) for v in d.v_pixel][:2], (e.image_def.dxf.filename if e.image_def else "")]
        else:
            b = _bbox(e)
            base.append([_r(v, n) for v in b] if b else None)
    except Exception:  # noqa: BLE001 - entita' incompleta: basta il tipo
        pass
    return hashlib.md5(json.dumps(base, default=str, ensure_ascii=False).encode()).hexdigest()


def _punto(e):
    try:
        p = e.dxf.insert
        return (float(p[0]), float(p[1]))
    except Exception:  # noqa: BLE001
        b = _bbox(e)
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else None


def _contenitori(doc):
    """('modello', entita') e ('blocco NOME', entita' della definizione)."""
    yield "modello", list(doc.modelspace())
    for b in doc.blocks:
        if b.name.startswith("*") or b.is_any_layout:
            continue
        yield f"blocco {b.name}", list(b)


def confronta_doc(a, b, n: int = 2, max_righe: int = 150) -> dict:
    """Cosa cambia da a a b (documenti ezdxf): per contenitore (modello, blocchi), entita'
    aggiunte e tolte (con la firma del contenuto) e testi riscritti nello stesso punto."""
    from .lettura import descrivi
    ca, cb = dict(_contenitori(a)), dict(_contenitori(b))
    out = {"uguali": True, "contenitori": {}, "testi_cambiati": [], "aggiunte": [], "tolte": [],
           "per_layer": {}}
    per_layer = defaultdict(Counter)
    for nome in sorted(set(ca) | set(cb)):
        ea, eb = ca.get(nome, []), cb.get(nome, [])
        fa, fb = defaultdict(list), defaultdict(list)
        for e in ea:
            fa[firma(e, n)].append(e)
        for e in eb:
            fb[firma(e, n)].append(e)
        tolte, aggiunte = [], []
        for k in set(fa) | set(fb):
            d = len(fb.get(k, [])) - len(fa.get(k, []))
            if d > 0:
                aggiunte += fb[k][:d]
            elif d < 0:
                tolte += fa[k][:-d]
        if not tolte and not aggiunte:
            if nome not in ca or nome not in cb:
                out["contenitori"][nome] = {"solo_in": "a" if nome in ca else "b"}
                out["uguali"] = False
            continue
        out["uguali"] = False
        # testi riscritti: stesso tipo, stesso punto
        testi_t = {}
        for e in tolte:
            if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB") and _punto(e):
                p = _punto(e)
                testi_t.setdefault((e.dxftype(), round(p[0], 1), round(p[1], 1)), []).append(e)
        via = set()
        rimaste = []
        # blocchi con gli attributi cambiati (cartiglio): stesso blocco nello stesso punto
        ins_t = {}
        for e in tolte:
            if e.dxftype() == "INSERT":
                ins_t.setdefault((e.dxf.name, round(e.dxf.insert[0], 1), round(e.dxf.insert[1], 1)), []).append(e)
        for e in list(aggiunte):
            if e.dxftype() != "INSERT":
                continue
            lst = ins_t.get((e.dxf.name, round(e.dxf.insert[0], 1), round(e.dxf.insert[1], 1)))
            if not lst:
                continue
            v = lst.pop(0)
            prima = {a.dxf.tag: a.dxf.text for a in v.attribs}
            dopo = {a.dxf.tag: a.dxf.text for a in e.attribs}
            diff = {k: [prima.get(k, ""), dopo.get(k, "")] for k in sorted(set(prima) | set(dopo))
                    if prima.get(k) != dopo.get(k)}
            if not diff:
                continue                                # cambia altro (posizione, scala): resta fra aggiunte/tolte
            via.add(id(v))
            aggiunte.remove(e)
            for tag, (pa, pd) in diff.items():
                out["testi_cambiati"].append({"dove": nome, "layer": e.dxf.get("layer", "0"), "blocco": e.dxf.name,
                                              "tag": tag, "x": _r(e.dxf.insert[0]), "y": _r(e.dxf.insert[1]),
                                              "prima": pa, "dopo": pd})
            per_layer[e.dxf.get("layer", "0")]["testi_cambiati"] += len(diff)
        for e in aggiunte:
            p = _punto(e) if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB") else None
            lst = testi_t.get((e.dxftype(), round(p[0], 1), round(p[1], 1))) if p else None
            if lst:
                v = lst.pop(0)
                via.add(id(v))
                out["testi_cambiati"].append({"dove": nome, "layer": e.dxf.get("layer", "0"),
                                              "x": _r(p[0]), "y": _r(p[1]), "prima": _testo_entita(v),
                                              "dopo": _testo_entita(e)})
                per_layer[e.dxf.get("layer", "0")]["testi_cambiati"] += 1
            else:
                rimaste.append(e)
        tolte = [e for e in tolte if id(e) not in via]
        out["contenitori"][nome] = {"aggiunte": len(rimaste), "tolte": len(tolte),
                                    "testi_cambiati": sum(1 for t in out["testi_cambiati"] if t["dove"] == nome)}
        for e in rimaste:
            per_layer[e.dxf.get("layer", "0")]["aggiunte"] += 1
            if len(out["aggiunte"]) < max_righe:
                out["aggiunte"].append({"dove": nome, **descrivi(e)})
        for e in tolte:
            per_layer[e.dxf.get("layer", "0")]["tolte"] += 1
            if len(out["tolte"]) < max_righe:
                out["tolte"].append({"dove": nome, **descrivi(e)})
    out["per_layer"] = {k: dict(v) for k, v in sorted(per_layer.items())}
    out["troncato"] = len(out["aggiunte"]) >= max_righe or len(out["tolte"]) >= max_righe
    return out


def confronta_file(a: str, b: str, n: int = 2) -> dict:
    r = confronta_doc(leggi(a), leggi(b), n)
    return {"a": a, "b": b, **r}


def confronta(a: str, b: str, filtro: str = "*.dwg", n: int = 2) -> dict:
    """Due file o due cartelle (file abbinati per nome)."""
    if os.path.isfile(a) and os.path.isfile(b):
        return confronta_file(a, b, n)
    if not (os.path.isdir(a) and os.path.isdir(b)):
        raise FileNotFoundError("servono due file o due cartelle esistenti")
    fa = {os.path.basename(p).lower(): p for p in glob.glob(os.path.join(a, filtro))}
    fb = {os.path.basename(p).lower(): p for p in glob.glob(os.path.join(b, filtro))}
    out = {"a": a, "b": b, "uguali": [], "diversi": [], "solo_in_a": sorted(set(fa) - set(fb)),
           "solo_in_b": sorted(set(fb) - set(fa))}
    for k in sorted(set(fa) & set(fb)):
        try:
            r = confronta_file(fa[k], fb[k], n)
        except Exception as ex:  # noqa: BLE001
            out["diversi"].append({"file": k, "errore": str(ex)})
            continue
        if r["uguali"]:
            out["uguali"].append(k)
        else:
            out["diversi"].append({"file": k, "contenitori": r["contenitori"], "per_layer": r["per_layer"],
                                   "testi_cambiati": r["testi_cambiati"][:30]})
    return out


# ---------------------------------------------------------------------------
# cerca e sostituisci
# ---------------------------------------------------------------------------

def _schema(cerca: str, regex: bool, maiuscole: bool, intera: bool):
    if regex:
        pat = cerca
    else:
        pat = ""
        for c in cerca:
            if c in CODICI:
                pat += f"(?:{re.escape(c)}|{re.escape(CODICI[c])}|{re.escape(CODICI[c].upper())})"
            else:
                pat += re.escape(c)
    if intera:
        pat = rf"(?<!\w){pat}(?!\w)"
    return re.compile(pat, 0 if maiuscole else re.IGNORECASE)


def sostituisci_doc(doc, cerca: str, sostituisci: str, regex: bool = False, maiuscole: bool = False,
                    intera: bool = False, zona=None, layer: str = "", blocchi: bool = True,
                    applica: bool = True) -> list[dict]:
    """Sostituisce in TEXT, MTEXT, attributi e testi dei blocchi (es. la cornice).
    Senza regex "Ø", "°", "±" trovano anche %%c, %%d, %%p; nei font SHX il nuovo testo li usa."""
    rx = _schema(cerca, regex, maiuscole, intera)
    out = []
    for e, dove, ins in _testi(doc, blocchi):
        vecchio = _testo_entita(e)
        if not vecchio or not rx.search(vecchio):
            continue
        if layer and e.dxf.get("layer", "0") != layer and not (ins is not None and ins.dxf.layer == layer):
            continue
        p = _punto(e)
        if zona and (dove.startswith("blocco") or p is None or
                     not (zona[0] <= p[0] <= zona[2] and zona[1] <= p[1] <= zona[3])):
            continue                                       # con una zona: solo lo spazio modello
        rep = sostituisci
        if _shx(_font_stile(doc, e.dxf.get("style", "Standard"))):
            for k, v in CODICI.items():
                rep = rep.replace(k, v)
        nuovo = rx.sub(rep if regex else rep.replace("\\", "\\\\"), vecchio)
        if nuovo == vecchio:
            continue
        r = {"handle": e.dxf.handle, "tipo": e.dxftype(), "dove": dove, "layer": e.dxf.get("layer", "0"),
             "prima": vecchio, "dopo": nuovo}
        if p:
            r.update(x=_r(p[0]), y=_r(p[1]))
        if e.dxftype() in ("ATTRIB", "ATTDEF"):
            r["tag"] = e.dxf.get("tag", "")
        if applica:
            if e.dxftype() == "MTEXT":
                e.text = nuovo
            else:
                e.dxf.text = nuovo
        out.append(r)
    return out


def _file_cad(percorso: str, filtro: str) -> list[str]:
    if os.path.isdir(percorso):
        return sorted(p for p in glob.glob(os.path.join(percorso, "*"))
                      if fnmatch.fnmatch(os.path.basename(p).lower(), filtro.lower())
                      and p.lower().endswith((".dwg", ".dxf")))
    return [percorso]


def sostituisci(percorso: str, cerca: str, sostituisci_con: str, regex: bool = False, maiuscole: bool = False,
                intera: bool = False, zona=None, layer: str = "", blocchi: bool = True, applica: bool = False,
                filtro: str = "*.dwg", out_dwg: str = "") -> dict:
    """Su un file o su tutti i DWG di una cartella. applica=False: solo anteprima."""
    from .lettura import _riedita_dwg
    file = _file_cad(percorso, filtro)
    res, tot = [], 0
    for f in file:
        kw = dict(regex=regex, maiuscole=maiuscole, intera=intera, zona=zona, layer=layer, blocchi=blocchi)
        cambi = sostituisci_doc(leggi(f), cerca, sostituisci_con, applica=False, **kw)
        if cambi and applica:
            r = _riedita_dwg(f, lambda doc: sostituisci_doc(doc, cerca, sostituisci_con, applica=True, **kw),
                             out_dwg if len(file) == 1 else "")
            res.append({"file": f, "n": len(cambi), "cambi": cambi, "backup": r.get("backup", "")})
        elif cambi:
            res.append({"file": f, "n": len(cambi), "cambi": cambi})
        tot += len(cambi)
    return {"applicato": applica, "file_esaminati": len(file), "sostituzioni": tot, "file": res}


# ---------------------------------------------------------------------------
# cartiglio
# ---------------------------------------------------------------------------

SINONIMI = {
    "DATA": ["DATA", "DATE", "DATA_EMISSIONE"],
    "DISEGNATO": ["DISEGNATO", "DISEGNATORE", "DISEGN", "DIS", "DRAWN", "DISEGNATO_DA"],
    "FOGLIO": ["FOGLIO", "NUMERO_DI_FOGLIO", "FG", "SHEET"],
    "SEGUE": ["SEGUE_FOGLIO", "FOGLIO_SEGUENTE", "FS"],
    "COMMESSA": ["COMMESSA", "JOB"],
    "CONTROLLATO": ["CONTROLLATO", "VISTO", "CHECKED"],
    "APPROVATO": ["APPROVATO", "APPR", "APPROVED"],
}


def _chiavi(tag: str) -> set[str]:
    t = tag.upper().replace("-", "_").replace(".", "").strip()
    out = {t}
    for k, lst in SINONIMI.items():
        if t == k or t in lst:
            out.add(k)
    return out


def attributi_doc(doc, blocco: str = "") -> list[dict]:
    """Gli INSERT con attributi (cartigli, cornici): blocco, posizione, {tag: valore}."""
    out = []
    for e in doc.modelspace().query("INSERT"):
        if blocco and e.dxf.name.upper() != blocco.upper():
            continue
        if not e.attribs:
            continue
        out.append({"handle": e.dxf.handle, "blocco": e.dxf.name, "layer": e.dxf.layer,
                    "x": _r(e.dxf.insert[0]), "y": _r(e.dxf.insert[1]),
                    "attributi": {a.dxf.tag: a.dxf.text for a in e.attribs}})
    return out


def imposta_attributi(doc, valori: dict, blocco: str = "", solo_vuoti: bool = False) -> list[dict]:
    """Attributi per tag su tutti gli INSERT (o di un blocco). Le chiavi accettano i sinonimi
    (DISEGNATO = DISEGNATORE = DISEGN...)."""
    voluti = {}
    for k, v in valori.items():
        for c in _chiavi(k):
            voluti[c] = str(v)
    out = []
    for e in doc.modelspace().query("INSERT"):
        if blocco and e.dxf.name.upper() != blocco.upper():
            continue
        for a in e.attribs:
            k = next((c for c in _chiavi(a.dxf.tag) if c in voluti), None)
            if k is None or (solo_vuoti and a.dxf.text.strip()):
                continue
            if a.dxf.text != voluti[k]:
                out.append({"handle": a.dxf.handle, "insert": e.dxf.handle, "blocco": e.dxf.name, "tag": a.dxf.tag,
                            "prima": a.dxf.text, "dopo": voluti[k]})
                a.dxf.text = voluti[k]
    return out


def togli_zona(doc, zona, blocchi: bool = True, layer: str = "") -> list[dict]:
    """Elimina le entita' tutte dentro la zona: nello spazio modello e, se blocchi, anche
    nelle definizioni dei blocchi inseriti senza scala/rotazione (la cornice)."""
    from .lettura import descrivi
    out = []
    msp = doc.modelspace()
    for e in list(msp):
        if e.dxftype() == "INSERT" or (layer and e.dxf.layer != layer):
            continue
        if _dentro(_bbox(e), zona):
            out.append({"dove": "modello", **descrivi(e, False)})
            msp.delete_entity(e)
    if blocchi:
        fatti = set()
        for ins in msp.query("INSERT"):
            nome = ins.dxf.name
            if nome in fatti or abs(ins.dxf.get("xscale", 1) - 1) > 1e-6 or abs(ins.dxf.get("rotation", 0)) > 1e-6:
                continue
            fatti.add(nome)
            ox, oy = ins.dxf.insert[0], ins.dxf.insert[1]
            zb = (zona[0] - ox, zona[1] - oy, zona[2] - ox, zona[3] - oy)
            blk = doc.blocks.get(nome)
            for e in list(blk):
                if e.dxftype() in ("ATTDEF", "INSERT"):
                    continue
                if _dentro(_bbox(e), zb):
                    out.append({"dove": f"blocco {nome}", **descrivi(e, False)})
                    blk.delete_entity(e)
    return out


def cartiglio(dwg: str, valori: dict | None = None, blocco: str = "", togli: list | None = None,
              solo_vuoti: bool = False, applica: bool = False, out_dwg: str = "") -> dict:
    """Senza valori: elenca gli attributi dei cartigli. Con valori (e/o togli = zona da svuotare,
    es. la riga delle revisioni vecchie) li imposta; applica=False e' l'anteprima."""
    from .lettura import _riedita_dwg
    doc = leggi(dwg)
    if not valori and not togli:
        return {"dwg": dwg, "cartigli": attributi_doc(doc, blocco)}

    def lavora(d):
        r = {"attributi": imposta_attributi(d, valori or {}, blocco, solo_vuoti) if valori else []}
        if togli:
            r["tolte"] = togli_zona(d, togli)
        return [r]
    if not applica:
        return {"dwg": dwg, "applicato": False, **lavora(doc)[0]}
    res = _riedita_dwg(dwg, lavora, out_dwg)
    return {"dwg": res["dwg"], "applicato": True, "backup": res.get("backup", ""), **res["dettaglio"][0]}


# ---------------------------------------------------------------------------
# controllo grafico su file
# ---------------------------------------------------------------------------

def controlla(dwg: str, zona=None, foglio: str = "", per_foglio: bool = True) -> dict:
    """Testi sovrapposti, testi toccati da linee (anche fuori dalla cella), testi doppi.
    Un multifoglio SPAC si controlla foglio per foglio (foglio = numero, o tutti)."""
    from .vista import conversione as cv
    doc = leggi(dwg)
    out = {"dwg": dwg, "fogli": []}
    r = cv.fogli_doc(doc, dwg) if per_foglio else None
    if r and (len(r[1]) >= 2 or r[0] == "spac"):
        for f in r[1]:
            if foglio and str(f["foglio"]) != str(foglio):
                continue
            pr = cv.controlla(f["oggetti"], zona)
            out["fogli"].append({"foglio": f["foglio"], "descrizione": f.get("descrizione", ""), "n": len(pr),
                                 "problemi": [{"tipo": p["tipo"], "msg": p["msg"], "b": p["b"]} for p in pr]})
    else:
        pr = cv.controlla(cv.da_doc(doc, cartella=os.path.dirname(os.path.abspath(dwg))), zona)
        out["fogli"].append({"foglio": "", "n": len(pr),
                             "problemi": [{"tipo": p["tipo"], "msg": p["msg"], "b": p["b"]} for p in pr]})
    out["totale"] = sum(f["n"] for f in out["fogli"])
    return out


# ---------------------------------------------------------------------------
# tracciamento dei fili
# ---------------------------------------------------------------------------

def _polilinee_filo(doc, layer: str = ""):
    """Entita' lineari dello spazio modello -> (entita', [punti]) (archi e bulge appiattiti)."""
    from ezdxf import path as ezpath
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE"):
            continue
        if layer and e.dxf.layer != layer:
            continue
        if t in ("LWPOLYLINE", "POLYLINE") and getattr(e, "is_closed", False):
            continue                                   # contorni chiusi: non sono fili
        try:
            pts = [(p.x, p.y) for p in ezpath.make_path(e).flattening(0.05)]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) >= 2 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) > 1e-6:
            out.append((e, pts))
    return out


def _dist_seg(px, py, x0, y0, x1, y1) -> float:
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _dist_poli(p, pts) -> float:
    return min(_dist_seg(p[0], p[1], *pts[i - 1], *pts[i]) for i in range(1, len(pts)))


def _testi_vicini(doc, p, raggio: float, layer: str = "") -> list[dict]:
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        cand = []
        if t in ("TEXT", "MTEXT") and (not layer or e.dxf.layer == layer):
            cand = [e]
        elif t == "INSERT" and (not layer or e.dxf.layer in (layer, "0")):
            cand = list(e.attribs)
        for x in cand:
            s = _testo_entita(x)
            b = _bbox(x)
            if not s.strip() or b is None:
                continue
            d = math.hypot(max(b[0] - p[0], 0, p[0] - b[2]), max(b[1] - p[1], 0, p[1] - b[3]))
            if d <= raggio:
                out.append({"testo": s, "distanza": _r(d), "handle": x.dxf.handle,
                            "x": _r((b[0] + b[2]) / 2), "y": _r((b[1] + b[3]) / 2)})
    return sorted(out, key=lambda r: r["distanza"])[:8]


def traccia_fili(dwg_o_doc, punto, tolleranza: float = 0.15, modo: str = "estremi", layer: str = "",
                 raggio_testi: float = 4.0, cerca: float = 1.5, max_entita: int = 2000) -> dict:
    """Segue un filo dal punto: le entita' collegate, i capi liberi con i testi vicini (pin,
    colori, numeri), la lunghezza. modo "estremi": collegate solo se si toccano agli estremi
    (schede cavo: non si entra nei disegni dei connettori); "giunzioni": anche a T (schemi)."""
    doc = leggi(dwg_o_doc) if isinstance(dwg_o_doc, str) else dwg_o_doc
    fili = _polilinee_filo(doc, layer)
    if not fili:
        return {"trovato": False, "motivo": "nessuna linea"}
    p0 = (float(punto[0]), float(punto[1]))
    k0 = min(range(len(fili)), key=lambda i: _dist_poli(p0, fili[i][1]))
    if _dist_poli(p0, fili[k0][1]) > cerca:
        return {"trovato": False, "motivo": f"nessun filo entro {cerca} mm dal punto"}
    # indice degli estremi a griglia
    g = max(tolleranza, 0.05) * 2
    griglia = defaultdict(list)
    for i, (_, pts) in enumerate(fili):
        for q in (pts[0], pts[-1]):
            griglia[(int(q[0] // g), int(q[1] // g))].append(i)

    def vicini_estremi(q):
        cx, cy = int(q[0] // g), int(q[1] // g)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in griglia.get((cx + dx, cy + dy), []):
                    pts = fili[j][1]
                    if min(math.hypot(pts[0][0] - q[0], pts[0][1] - q[1]),
                           math.hypot(pts[-1][0] - q[0], pts[-1][1] - q[1])) <= tolleranza:
                        yield j
    visti, coda = {k0}, [k0]
    while coda and len(visti) < max_entita:
        i = coda.pop()
        pts = fili[i][1]
        for q in (pts[0], pts[-1]):
            for j in vicini_estremi(q):
                if j not in visti:
                    visti.add(j)
                    coda.append(j)
            if modo == "giunzioni":                   # un estremo che cade a meta' di un altro filo
                for j, (_, pj) in enumerate(fili):
                    if j not in visti and _dist_poli(q, pj) <= tolleranza:
                        visti.add(j)
                        coda.append(j)
        if modo == "giunzioni":                       # estremi di altri fili che cadono su questo
            for j, (_, pj) in enumerate(fili):
                if j not in visti and (_dist_poli(pj[0], pts) <= tolleranza or _dist_poli(pj[-1], pts) <= tolleranza):
                    visti.add(j)
                    coda.append(j)
    # capi: estremi che non toccano altri fili del collegamento
    conta = Counter()
    estremi = []
    for i in visti:
        pts = fili[i][1]
        for q in (pts[0], pts[-1]):
            estremi.append(q)
    for q in estremi:
        conta[(round(q[0] / tolleranza), round(q[1] / tolleranza))] += 1
    capi = []
    for i in visti:
        pts = fili[i][1]
        for q in (pts[0], pts[-1]):
            libero = sum(1 for e2 in estremi if math.hypot(e2[0] - q[0], e2[1] - q[1]) <= tolleranza) == 1
            if libero and (modo != "giunzioni" or not any(_dist_poli(q, fili[j][1]) <= tolleranza
                                                          for j in visti if j != i)):
                capi.append({"x": _r(q[0]), "y": _r(q[1]), "handle": fili[i][0].dxf.handle,
                             "testi": _testi_vicini(doc, q, raggio_testi, layer)})
    lung = sum(sum(math.hypot(p[k][0] - p[k - 1][0], p[k][1] - p[k - 1][1]) for k in range(1, len(p)))
               for _, p in (fili[i] for i in visti))
    xs = [q[0] for i in visti for q in fili[i][1]]
    ys = [q[1] for i in visti for q in fili[i][1]]
    return {"trovato": True, "entita": len(visti), "handle": sorted(fili[i][0].dxf.handle for i in visti),
            "layer": sorted({fili[i][0].dxf.layer for i in visti}), "lunghezza_mm": _r(lung),
            "estensione": [_r(min(xs)), _r(min(ys)), _r(max(xs)), _r(max(ys))], "capi": capi,
            "troncato": len(visti) >= max_entita}


# ---------------------------------------------------------------------------
# posto libero
# ---------------------------------------------------------------------------

def _ostacoli(doc, zona, layer: str = ""):
    from shapely.geometry import LineString, box
    from .vista import conversione as cv

    def tieni(e):
        if layer and not (e.dxf.layer == layer or (e.dxf.layer == "0" and e.dxftype() == "INSERT")):
            return False
        return _tocca(_bbox(e), zona)
    geo = []
    for o in cv.da_doc(doc, tieni=tieni):
        if not _tocca(o["b"], zona):
            continue
        if o.get("x"):
            p = cv._poligono_testo(o["x"])
            geo.append(p if p is not None else box(*o["b"]))
        for q in o["p"]:
            if len(q) >= 4:
                geo.append(LineString(list(zip(q[0::2], q[1::2]))))
        for q in o["f"]:
            geo.append(box(min(q[0::2]), min(q[1::2]), max(q[0::2]), max(q[1::2])))
    return geo


def libero(dwg_o_doc, riquadro, layer: str = "", margine: float = 0.3) -> dict:
    """Il riquadro [x0,y0,x1,y1] tocca linee o testi? (margine in mm)."""
    from shapely.geometry import box
    doc = leggi(dwg_o_doc) if isinstance(dwg_o_doc, str) else dwg_o_doc
    z = [riquadro[0] - margine, riquadro[1] - margine, riquadro[2] + margine, riquadro[3] + margine]
    b = box(*z)
    tocca = [g for g in _ostacoli(doc, [z[0] - 5, z[1] - 5, z[2] + 5, z[3] + 5], layer) if g.intersects(b)]
    return {"libero": not tocca, "ostacoli": len(tocca)}


def posto_libero(dwg_o_doc, punto, larghezza: float = 0.0, altezza: float = 2.5, testo: str = "",
                 stile: str = "ROMANS", raggio: float = 25.0, passo: float = 0.5, margine: float = 0.6,
                 layer: str = "", n: int = 5) -> dict:
    """Posti liberi vicino al punto per una scritta (o un riquadro largo x alto mm):
    i piu' vicini al punto, con il punto d'inserimento del testo (allineato a sinistra)."""
    from shapely.geometry import box
    from shapely.strtree import STRtree
    doc = leggi(dwg_o_doc) if isinstance(dwg_o_doc, str) else dwg_o_doc
    w = larghezza or (larghezza_testo(testo, altezza, stile, doc=doc) if testo else 10.0)
    h_tot = altezza * 1.35 if testo else altezza
    px, py = float(punto[0]), float(punto[1])
    zona = [px - raggio - w, py - raggio - h_tot, px + raggio + w, py + raggio + h_tot]
    geo = _ostacoli(doc, zona, layer)
    albero = STRtree(geo) if geo else None
    cand = []
    k = int(raggio / passo)
    for i in range(-k, k + 1):
        for j in range(-k, k + 1):
            cx, cy = px + i * passo, py + j * passo
            d = math.hypot(i * passo, j * passo)
            if d > raggio:
                continue
            cand.append((d, cx, cy))
    cand.sort()
    out = []
    for d, cx, cy in cand:
        x0, y0 = cx - w / 2, cy - h_tot / 2
        b = box(x0 - margine, y0 - margine, x0 + w + margine, y0 + h_tot + margine)
        if albero is not None and any(geo[int(t)].intersects(b) for t in albero.query(b)):
            continue
        if any(abs(o["centro"][0] - cx) < w * 0.6 and abs(o["centro"][1] - cy) < h_tot * 0.6 for o in out):
            continue
        r = {"riquadro": [_r(x0), _r(y0), _r(x0 + w), _r(y0 + h_tot)], "centro": [_r(cx), _r(cy)], "distanza": _r(d)}
        if testo:
            r["inserimento"] = [_r(x0), _r(y0 + 0.35 * altezza)]      # TEXT allineato a sinistra, linea di base
        out.append(r)
        if len(out) >= n:
            break
    return {"larghezza": _r(w), "altezza": _r(h_tot), "posti": out}


# ---------------------------------------------------------------------------
# copia di una zona
# ---------------------------------------------------------------------------

def _matrice(zona, dx, dy, scala, cornice_src, cornice_dst):
    from ezdxf.math import Matrix44
    if cornice_src and cornice_dst:
        sx = (cornice_dst[2] - cornice_dst[0]) / (cornice_src[2] - cornice_src[0])
        sy = (cornice_dst[3] - cornice_dst[1]) / (cornice_src[3] - cornice_src[1])
        s = (sx + sy) / 2
        return Matrix44.scale(s, s, 1) @ Matrix44.translate(cornice_dst[0] - s * cornice_src[0],
                                                             cornice_dst[1] - s * cornice_src[1], 0), s
    return Matrix44.scale(scala, scala, 1) @ Matrix44.translate(dx, dy, 0), scala


def copia_zona(sorgente: str, zona, destinazione: str, dx: float = 0.0, dy: float = 0.0, scala: float = 1.0,
               cornice_src=None, cornice_dst=None, svuota=None, layer_src: str = "", layer_dst: str = "",
               applica: bool = False, out_dwg: str = "") -> dict:
    """Copia le entita' tutte dentro `zona` di `sorgente` in `destinazione`, spostate (dx, dy, scala)
    o allineate da cornice_src a cornice_dst ([x0,y0,x1,y1] delle due cornici). svuota = zona della
    destinazione da liberare prima (sostituzione di un disegno). layer_dst: layer delle copie
    (es. il layer del foglio in un multifoglio). applica=False: solo cosa verrebbe copiato."""
    from ezdxf.addons import Importer
    from .lettura import _riedita_dwg, descrivi
    stesso = os.path.abspath(sorgente) == os.path.abspath(destinazione)
    src = leggi(sorgente)
    scelte = [e for e in src.modelspace() if (not layer_src or e.dxf.layer == layer_src)
              and _dentro(_bbox(e), zona, 0.01)]
    m, s = _matrice(zona, dx, dy, scala, cornice_src, cornice_dst)
    riass = {"sorgente": sorgente, "destinazione": destinazione, "entita": len(scelte), "scala": round(s, 5),
             "per_tipo": dict(Counter(e.dxftype() for e in scelte))}
    if not applica:
        return {**riass, "applicato": False, "esempio": [descrivi(e, False) for e in scelte[:15]]}

    def lavora(doc):
        msp = doc.modelspace()
        tolte = togli_zona(doc, svuota, blocchi=False, layer=layer_dst) if svuota else []
        prima = {e.dxf.handle for e in msp}
        if stesso:
            for e in [doc.entitydb.get(x.dxf.handle) for x in scelte]:
                if e is not None:
                    msp.add_entity(e.copy())
        else:
            imp = Importer(src, doc)
            imp.import_entities(scelte, msp)
            imp.finalize()
        nuove = [e for e in msp if e.dxf.handle not in prima]
        for e in nuove:
            try:
                e.transform(m)
            except Exception:  # noqa: BLE001 - entita' che non si trasformano: restano dove sono
                pass
            if layer_dst:
                e.dxf.layer = layer_dst
        return [{"copiate": len(nuove), "tolte": len(tolte), "handle": [e.dxf.handle for e in nuove][:200]}]
    res = _riedita_dwg(destinazione, lavora, out_dwg)
    return {**riass, "applicato": True, "dwg": res["dwg"], "backup": res.get("backup", ""), **res["dettaglio"][0]}


# ---------------------------------------------------------------------------
# consegna
# ---------------------------------------------------------------------------

def _md5(p: str) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def consegna(sorgente: str, destinazione: str, filtro: str = "*", confronta_cad: bool = True,
             forza: bool = False, applica: bool = False) -> dict:
    """Copia i file di una cartella in un'altra (non ricorsiva, non cancella nulla) e controlla
    l'MD5. I DWG/DXF gia' presenti si confrontano per contenuto: se sono uguali non si
    ricopiano (ODA cambia l'MD5 a ogni conversione). applica=False: solo il piano."""
    file = sorted(p for p in glob.glob(os.path.join(sorgente, "*"))
                  if os.path.isfile(p) and fnmatch.fnmatch(os.path.basename(p).lower(), filtro.lower()))
    piano = []
    for p in file:
        nome = os.path.basename(p)
        d = os.path.join(destinazione, nome)
        voce = {"file": nome, "azione": "nuovo"}
        if os.path.isfile(d):
            if _md5(p) == _md5(d):
                voce["azione"] = "uguale"
            elif confronta_cad and nome.lower().endswith((".dwg", ".dxf")):
                try:
                    r = confronta_file(d, p)
                    voce["azione"] = "uguale (contenuto)" if r["uguali"] else "cambiato"
                    if not r["uguali"]:
                        voce["cambi"] = {"contenitori": r["contenitori"],
                                         "testi_cambiati": r["testi_cambiati"][:20]}
                except Exception as ex:  # noqa: BLE001
                    voce.update(azione="cambiato", errore_confronto=str(ex))
            else:
                voce["azione"] = "cambiato"
        if forza and voce["azione"].startswith("uguale"):
            voce["azione"] = "ricopia"
        piano.append(voce)
    if applica:
        os.makedirs(destinazione, exist_ok=True)
        for v in piano:
            if v["azione"].startswith("uguale"):
                continue
            s, d = os.path.join(sorgente, v["file"]), os.path.join(destinazione, v["file"])
            shutil.copy2(s, d)
            v["md5_ok"] = _md5(s) == _md5(d)
    return {"sorgente": sorgente, "destinazione": destinazione, "applicato": applica,
            "da_copiare": sum(1 for v in piano if not v["azione"].startswith("uguale")),
            "uguali": sum(1 for v in piano if v["azione"].startswith("uguale")),
            "md5_ok": all(v.get("md5_ok", True) for v in piano), "file": piano}


# ---------------------------------------------------------------------------
# distinta dalle schede cavo
# ---------------------------------------------------------------------------

_COLORI = r"(ROSS[OIAE]|NER[OIAE]|BIANC[OHIAE]+|BLU|MARRON[EI]|GIALL[OIAE]|VERD[EI]|ARANCI[OIAE]*|ARANCION[EI]|" \
          r"GRIGI[OAE]*|VIOLA|ROSA|AZZURR[OIAE])"
CATEGORIE = [
    ("guaina", re.compile(r"guaina|sleeve|heat-?shrink", re.I)),
    ("gommino", re.compile(r"gommin|\bseal\b|tenuta", re.I)),
    ("puntale", re.compile(r"puntal|terminale|fast-?on|faston|capicorda|ferrule|\blug\b", re.I)),
    ("contatto", re.compile(r"contatt|\bcontact", re.I)),
    ("connettore", re.compile(r"connettore|connector|\bM(?:8|12|23)\b|D-?SUB|AMP\s*SS|superseal|MODU\b|"
                              r"MINI-?FIT|MICRO-?FIT", re.I)),
    ("etichetta", re.compile(r"etichett|marker|\blabel|PAT-?G\s*\d|grafoplast", re.I)),
    ("cavo", re.compile(r"\bcavo\b|\bcable\b|multicavo|\bsez\.?\s*\d|\d+x\d+[.,]?\d*", re.I)),
    ("lunghezza", re.compile(r"\bL\s*=?\s*\d+[.,]?\d*\s*cm", re.I)),
]


def _valori(s: str) -> dict:
    v = {}
    m = re.search(r"\bL\s*=?\s*(\d+[.,]?\d*)\s*cm", s, re.I)
    if m:
        v["lunghezza_cm"] = float(m.group(1).replace(",", "."))
    m = re.search(r"(?:Ø|%%c|\bD)\s*(\d+[.,]?\d*)", s, re.I)
    if m:
        v["diametro"] = float(m.group(1).replace(",", "."))
    m = re.search(r"\bsez\.?\s*(\d+[.,]?\d*)|(\d+)x(\d+[.,]?\d*)", s, re.I)
    if m:
        v["sezione"] = (m.group(1) or m.group(3)).replace(",", ".")
        if m.group(2):
            v["poli"] = int(m.group(2))
    m = re.search(_COLORI, s, re.I)
    if m:
        v["colore"] = m.group(1).upper()
    return v


def distinta(percorsi, xlsx_out: str = "", filtro: str = "*.dwg") -> dict:
    """Dai testi delle schede cavo: guaine, gommini, puntali, contatti, connettori, etichette,
    cavi e lunghezze (euristica sulle parole), per file/foglio. Con xlsx_out scrive l'Excel
    (foglio "Righe" + foglio "Riepilogo"). Da controllare: legge i testi, non il disegno."""
    from .vista import conversione as cv
    if isinstance(percorsi, str):
        percorsi = [percorsi]
    file = [f for p in percorsi for f in _file_cad(p, filtro)]
    righe = []
    for f in file:
        doc = leggi(f)
        r = cv.fogli_doc(doc, f)
        fogli = [(x["foglio"], x["oggetti"]) for x in r[1]] if r and (len(r[1]) >= 2 or r[0] == "spac") \
            else [("", cv.da_doc(doc, cartella=os.path.dirname(f)))]
        for num, ogg in fogli:
            for o in ogg:
                x = o.get("x")
                if not x:
                    continue
                s = re.sub(r"\s+", " ", x["s"]).strip()
                for cat, rx in CATEGORIE:
                    if rx.search(s):
                        righe.append({"file": os.path.basename(f), "foglio": num, "categoria": cat, "testo": s,
                                      "x": x["x"], "y": x["y"], **_valori(s)})
                        break
    riep = Counter((r["categoria"], re.sub(r"\s+", " ", r["testo"].upper())) for r in righe)
    riepilogo = [{"categoria": c, "testo": t, "quante": n} for (c, t), n in sorted(riep.items())]
    if xlsx_out:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Righe"
        col = ["file", "foglio", "categoria", "testo", "lunghezza_cm", "diametro", "sezione", "poli", "colore", "x", "y"]
        ws.append(col)
        for r in righe:
            ws.append([r.get(c, "") for c in col])
        ws2 = wb.create_sheet("Riepilogo")
        ws2.append(["categoria", "testo", "quante"])
        for r in riepilogo:
            ws2.append([r["categoria"], r["testo"], r["quante"]])
        for w in (ws, ws2):
            w.freeze_panes = "A2"
            for c in w.columns:
                w.column_dimensions[c[0].column_letter].width = min(60, max(10, max(len(str(v.value or "")) for v in c) + 2))
        wb.save(xlsx_out)
    return {"file": len(file), "righe": len(righe), "per_categoria": dict(Counter(r["categoria"] for r in righe)),
            "xlsx": xlsx_out, "riepilogo": riepilogo[:200], "esempio": righe[:40]}


# ---------------------------------------------------------------------------
# riga di comando (sul PC senza MCP registrato)
# ---------------------------------------------------------------------------

def _zona(s: str):
    return [float(v) for v in s.replace(";", ",").split(",")] if s else None


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="python -m etcad.strumenti")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("confronta", help="contenuto di due DWG o due cartelle")
    s.add_argument("a"); s.add_argument("b"); s.add_argument("--filtro", default="*.dwg")
    s = sub.add_parser("sostituisci", help="cerca e sostituisci nei testi (anteprima senza --applica)")
    s.add_argument("percorso"); s.add_argument("cerca"); s.add_argument("con")
    s.add_argument("--regex", action="store_true"); s.add_argument("--maiuscole", action="store_true")
    s.add_argument("--intera", action="store_true"); s.add_argument("--zona", default="")
    s.add_argument("--filtro", default="*.dwg"); s.add_argument("--applica", action="store_true")
    s = sub.add_parser("cartiglio", help="attributi del cartiglio (elenco, o --valori JSON)")
    s.add_argument("dwg"); s.add_argument("--valori", default=""); s.add_argument("--togli", default="")
    s.add_argument("--blocco", default=""); s.add_argument("--applica", action="store_true")
    s = sub.add_parser("controlla", help="testi sovrapposti o toccati da linee")
    s.add_argument("dwg"); s.add_argument("--zona", default=""); s.add_argument("--foglio", default="")
    s = sub.add_parser("fili", help="segue il filo dal punto x,y")
    s.add_argument("dwg"); s.add_argument("punto"); s.add_argument("--modo", default="estremi")
    s.add_argument("--layer", default=""); s.add_argument("--tolleranza", type=float, default=0.15)
    s = sub.add_parser("posto", help="posto libero vicino a x,y per un testo")
    s.add_argument("dwg"); s.add_argument("punto"); s.add_argument("--testo", default="")
    s.add_argument("--altezza", type=float, default=2.5); s.add_argument("--stile", default="ROMANS")
    s.add_argument("--larghezza", type=float, default=0.0); s.add_argument("--layer", default="")
    s = sub.add_parser("larghezza", help="larghezza di un testo in mm")
    s.add_argument("testo"); s.add_argument("--altezza", type=float, default=2.5)
    s.add_argument("--stile", default="ROMANS"); s.add_argument("--wf", type=float, default=1.0)
    s = sub.add_parser("consegna", help="copia una cartella con MD5 (piano senza --applica)")
    s.add_argument("sorgente"); s.add_argument("destinazione"); s.add_argument("--filtro", default="*")
    s.add_argument("--applica", action="store_true"); s.add_argument("--forza", action="store_true")
    s = sub.add_parser("distinta", help="materiali dalle schede cavo")
    s.add_argument("percorsi", nargs="+"); s.add_argument("--xlsx", default="")
    a = ap.parse_args(argv)
    if a.cmd == "confronta":
        r = confronta(a.a, a.b, a.filtro)
    elif a.cmd == "sostituisci":
        r = sostituisci(a.percorso, a.cerca, a.con, a.regex, a.maiuscole, a.intera, _zona(a.zona),
                        applica=a.applica, filtro=a.filtro)
    elif a.cmd == "cartiglio":
        r = cartiglio(a.dwg, json.loads(a.valori) if a.valori else None, a.blocco, _zona(a.togli), applica=a.applica)
    elif a.cmd == "controlla":
        r = controlla(a.dwg, _zona(a.zona), a.foglio)
    elif a.cmd == "fili":
        r = traccia_fili(a.dwg, _zona(a.punto), a.tolleranza, a.modo, a.layer)
    elif a.cmd == "posto":
        r = posto_libero(a.dwg, _zona(a.punto), a.larghezza, a.altezza, a.testo, a.stile, layer=a.layer)
    elif a.cmd == "larghezza":
        r = {"larghezza_mm": round(larghezza_testo(a.testo, a.altezza, a.stile, a.wf), 3)}
    elif a.cmd == "consegna":
        r = consegna(a.sorgente, a.destinazione, a.filtro, forza=a.forza, applica=a.applica)
    else:
        r = distinta(a.percorsi, a.xlsx)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
