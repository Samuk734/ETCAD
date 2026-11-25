"""Altre operazioni sui disegni.

- cerca: un testo (o una regex) in molti DWG/DXF, con file, foglio, layer, handle e posizione;
- rinumera: numeri di fili, morsetti o pin in ordine di lettura, con lo stesso numero dove era uguale;
- unisci: piu' disegni in un file solo, affiancati o un layer per disegno (multifoglio);
- pulisci: doppioni, linee di lunghezza zero, testi vuoti, blocchi/layer/stili/tipi di linea inutilizzati;
- converti_versione: un DWG in un'altra versione (2000-2018) o in DXF;
- leggi_tabella: una tabella disegnata con linee e testi -> righe (ed Excel);
- crea_scheda: una scheda cavo o uno schema ingressi da una specifica (lo stesso codice degli esempi);
- pdf_fogli: un PDF con una pagina per foglio di un multifoglio o di fogli affiancati.

Le funzioni che scrivono hanno applica=False per l'anteprima e usano lettura._riedita_dwg (backup e DWG nativo).
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import tempfile

from . import strumenti as st


# ---------------------------------------------------------------------------
# fogli di un documento
# ---------------------------------------------------------------------------

def zone_fogli(doc, percorso: str = "") -> list[dict]:
    """Fogli del documento: [{"foglio", "descrizione", "zona"} | {"foglio", "layer"}], nell'ordine dei fogli.
    Multifoglio SPAC (dbSPAC.db accanto) o layer numerati -> per layer; fogli affiancati -> per cornice."""
    from .vista import conversione as cv
    righe = cv.sequenza_spac(percorso, "") if percorso else None
    if not righe:
        righe = cv._fogli_per_layer(doc)
    if righe:
        return [{"foglio": r["foglio"], "descrizione": r.get("descrizione", ""), "layer": r["layer"]} for r in righe]
    righe = cv._fogli_per_cornice(doc) or []
    return [{"foglio": r["foglio"], "descrizione": r.get("descrizione", ""), "zona": [float(v) for v in r["zona"]]}
            for r in righe]


def foglio_di(zone: list[dict], x: float, y: float, layer: str = "") -> str:
    for z in zone:
        if "layer" in z:
            if z["layer"] == layer:
                return z["foglio"]
        else:
            x0, y0, x1, y1 = z["zona"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return z["foglio"]
    return ""


def _punto_testo(e):
    d = e.dxf
    if e.dxftype() in ("TEXT", "ATTRIB", "ATTDEF") and (d.get("halign", 0) or d.get("valign", 0)) and d.hasattr("align_point"):
        p = d.align_point
    else:
        p = d.get("insert", (0, 0, 0))
    return float(p[0]), float(p[1])


def _file_cad(percorsi, filtro: str = "*", ricorsivo: bool = True) -> list[str]:
    if isinstance(percorsi, str):
        percorsi = [x.strip() for x in percorsi.split(";") if x.strip()]
    out = []
    for p in percorsi:
        if os.path.isdir(p):
            for radice, _cart, file in (os.walk(p) if ricorsivo else [(p, [], os.listdir(p))]):
                for f in file:
                    if f.lower().endswith((".dwg", ".dxf")) and fnmatch.fnmatch(f.lower(), filtro.lower()):
                        out.append(os.path.join(radice, f))
        elif os.path.isfile(p):
            out.append(p)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# cerca
# ---------------------------------------------------------------------------

def cerca(percorsi, testo: str, regex: bool = False, maiuscole: bool = False, intera: bool = False,
          filtro: str = "*", blocchi: bool = True, max_risultati: int = 500) -> dict:
    """Cerca un testo in uno o piu' DWG/DXF (file o cartelle, anche separati da ';', sottocartelle comprese)."""
    pat = st._schema(testo, regex, maiuscole, intera)
    file = _file_cad(percorsi, filtro)
    trovati, per_file, errori = [], {}, []
    for f in file:
        try:
            doc = st.leggi(f)
        except Exception as ex:  # noqa: BLE001 - un file illeggibile non ferma la ricerca
            errori.append({"file": f, "errore": str(ex)[:200]})
            continue
        zone = zone_fogli(doc, f)
        n = 0
        for e, dove, ins in st._testi(doc, blocchi):
            s = st._testo_entita(e)
            if not pat.search(s or ""):
                continue
            n += 1
            if len(trovati) >= max_risultati:
                continue
            r = {"file": f, "testo": s, "dove": dove, "layer": e.dxf.get("layer", "0"), "handle": e.dxf.handle}
            if dove != "modello" and ins is not None:
                r["handle_insert"] = ins.dxf.handle
            if not dove.startswith("blocco"):
                x, y = _punto_testo(e)
                r.update(x=st._r(x), y=st._r(y), foglio=foglio_di(zone, x, y, r["layer"]))
            trovati.append(r)
        if n:
            per_file[f] = n
    return {"cercato": testo, "file_esaminati": len(file), "file_con_risultati": len(per_file),
            "risultati": sum(per_file.values()), "per_file": per_file, "trovati": trovati,
            "troncato": sum(per_file.values()) > len(trovati), "errori": errori}


# ---------------------------------------------------------------------------
# rinumera
# ---------------------------------------------------------------------------

def _nuovo_testo(vecchio: str, n: int, formato: str) -> str:
    if formato:
        return formato.format(n=n)
    m = re.search(r"\d+", vecchio)
    if not m:
        return str(n)
    cifre = m.group(0)
    num = str(n).zfill(len(cifre)) if cifre.startswith("0") and len(cifre) > 1 else str(n)
    return vecchio[:m.start()] + num + vecchio[m.end():]


def rinumera_doc(doc, schema: str = r"\d+", inizio: int = 1, passo: int = 1, formato: str = "",
                 ordine: str = "righe", zona=None, layer: str = "", uguali_insieme: bool = True,
                 percorso: str = "", applica: bool = False) -> list[dict]:
    """Rinumera i TEXT/MTEXT dello spazio modello che corrispondono per intero alla regex `schema`.

    Ordine di lettura: foglio per foglio, poi per righe (dall'alto, da sinistra) o per colonne. Con
    uguali_insieme lo stesso numero vecchio prende lo stesso numero nuovo (un filo ha il numero ai due capi).
    formato: es. "W{n}" o "{n:04d}"; vuoto = sostituisce solo la parte numerica, con gli zeri iniziali."""
    pat = re.compile(schema)
    zone = zone_fogli(doc, percorso)
    ordine_fogli = {z["foglio"]: i for i, z in enumerate(zone)}
    cand = []
    for e in doc.modelspace().query("TEXT MTEXT"):
        s = st._testo_entita(e).strip()
        if not s or not pat.fullmatch(s):
            continue
        ly = e.dxf.get("layer", "0")
        if layer and ly != layer:
            continue
        x, y = _punto_testo(e)
        if zona and not (zona[0] <= x <= zona[2] and zona[1] <= y <= zona[3]):
            continue
        f = foglio_di(zone, x, y, ly)
        cand.append((ordine_fogli.get(f, 0), x, y, s, e, f))
    if ordine == "colonne":
        cand.sort(key=lambda c: (c[0], round(c[1] / 2.0), -c[2]))
    else:
        cand.sort(key=lambda c: (c[0], -round(c[2] / 1.5), c[1]))
    nuovi, cambi, k = {}, [], 0
    for _fi, x, y, s, e, f in cand:
        if uguali_insieme and s in nuovi:
            nuovo = nuovi[s]
        else:
            nuovo = _nuovo_testo(s, inizio + k * passo, formato)
            k += 1
            nuovi[s] = nuovo
        if nuovo != s:
            cambi.append({"handle": e.dxf.handle, "foglio": f, "x": st._r(x), "y": st._r(y), "prima": s, "dopo": nuovo})
            if applica:
                if e.dxftype() == "MTEXT":
                    e.text = nuovo
                else:
                    e.dxf.text = nuovo
    return cambi


def rinumera(dwg: str, schema: str = r"\d+", inizio: int = 1, passo: int = 1, formato: str = "",
             ordine: str = "righe", zona=None, layer: str = "", uguali_insieme: bool = True,
             applica: bool = False, out_dwg: str = "") -> dict:
    kw = dict(schema=schema, inizio=inizio, passo=passo, formato=formato, ordine=ordine, zona=zona, layer=layer,
              uguali_insieme=uguali_insieme, percorso=dwg)
    cambi = rinumera_doc(st.leggi(dwg), applica=False, **kw)
    out = {"applicato": False, "testi_cambiati": len(cambi), "cambi": cambi[:400]}
    if applica and cambi:
        from .lettura import _riedita_dwg
        r = _riedita_dwg(dwg, lambda doc: rinumera_doc(doc, applica=True, **kw), out_dwg)
        out.update(applicato=True, dwg=r.get("dwg", out_dwg or dwg), backup=r.get("backup", ""))
    return out


# ---------------------------------------------------------------------------
# unisci
# ---------------------------------------------------------------------------

def _rinomina_blocchi(src, dst, suffisso: str):
    """Blocchi con lo stesso nome nei due documenti: quelli del sorgente prendono un suffisso (e i loro INSERT)."""
    nomi = {}
    for b in list(src.blocks):
        if b.is_any_layout or b.name.startswith("*") or b.name not in dst.blocks:
            continue
        nuovo = f"{b.name}_{suffisso}"
        while nuovo in dst.blocks or nuovo in src.blocks:
            nuovo += "_"
        nomi[b.name] = nuovo
    if not nomi:
        return {}
    for vecchio, nuovo in nomi.items():
        src.blocks.rename_block(vecchio, nuovo)
    contenitori = [src.modelspace()] + [b for b in src.blocks]
    for c in contenitori:
        for ins in c.query("INSERT"):
            if ins.dxf.name in nomi:
                ins.dxf.name = nomi[ins.dxf.name]
    return nomi


def unisci(file, uscita: str, modo: str = "affiancati", distanza: float = 20.0) -> dict:
    """Unisce piu' DWG/DXF in un file. modo "affiancati": uno accanto all'altro (come i _tutti_i_fogli);
    modo "layer": tutti all'origine, ogni disegno sul layer "1", "2"... (come un multifoglio SPAC)."""
    import ezdxf
    from ezdxf import bbox
    from ezdxf.addons import Importer
    from ezdxf.math import Matrix44
    if isinstance(file, str):
        file = [x.strip() for x in file.split(";") if x.strip()]
    file = [f for x in file for f in (_file_cad(x, "*", False) if os.path.isdir(x) else [x])]
    if not file:
        raise ValueError("nessun file da unire")
    dst = ezdxf.new("R2018")
    dst.header["$INSUNITS"] = 4
    msp = dst.modelspace()
    x = 0.0
    fogli = []
    for k, f in enumerate(file, 1):
        src = st.leggi(f)
        rinominati = _rinomina_blocchi(src, dst, str(k))
        prima = {e.dxf.handle for e in msp}
        imp = Importer(src, dst)
        imp.import_modelspace()
        imp.finalize()
        nuove = [e for e in msp if e.dxf.handle not in prima]
        b = bbox.extents(nuove, fast=True)
        if not b.has_data:
            fogli.append({"file": f, "entita": 0})
            continue
        if modo == "layer":
            ly = str(k)
            if ly not in dst.layers:
                dst.layers.add(ly)
            for e in nuove:
                e.dxf.layer = ly
            dx, dy = -b.extmin.x, -b.extmin.y
        else:
            dx, dy = x - b.extmin.x, -b.extmin.y
            x += b.size.x + distanza
        m = Matrix44.translate(dx, dy, 0)
        for e in nuove:
            try:
                e.transform(m)
            except Exception:  # noqa: BLE001 - entita' che non si spostano (rare) restano dove sono
                pass
        fogli.append({"file": f, "foglio": k, "entita": len(nuove), "zona": [st._r(b.extmin.x + dx), 0.0,
                                                                              st._r(b.extmax.x + dx), st._r(b.size.y)],
                      "blocchi_rinominati": rinominati})
    out = _salva(dst, uscita)
    out.update(modo=modo, fogli=fogli)
    return out


def _salva(doc, uscita: str) -> dict:
    """Salva come DXF o DWG (R2018) secondo l'estensione."""
    uscita = os.path.abspath(uscita)
    os.makedirs(os.path.dirname(uscita), exist_ok=True)
    if uscita.lower().endswith(".dxf"):
        doc.saveas(uscita)
        return {"file": uscita, "entita": len(doc.modelspace())}
    from . import dwg_io
    tmp = tempfile.mkdtemp(prefix="etcad_salva_")
    try:
        dxf = os.path.join(tmp, os.path.splitext(os.path.basename(uscita))[0] + ".dxf")
        doc.saveas(dxf)
        r = dwg_io.dxf_a_dwg([dxf], tmp, "2018")
        prodotto = os.path.splitext(dxf)[0] + ".dwg"
        if not r.get("ok") or not os.path.isfile(prodotto):
            raise RuntimeError(f"conversione DWG fallita: {r.get('errori') or r.get('log')}")
        shutil.move(prodotto, uscita)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"file": uscita, "entita": len(doc.modelspace())}


# ---------------------------------------------------------------------------
# pulisci
# ---------------------------------------------------------------------------

def _usati(doc):
    blocchi, layer, stili, tipi = set(), {"0", "Defpoints", doc.header.get("$CLAYER", "0")}, {"Standard"}, set()
    contenitori = list(doc.blocks)                    # comprende spazio modello e layout (*Model_Space...)
    for c in contenitori:
        for e in c:
            layer.add(e.dxf.get("layer", "0"))
            lt = e.dxf.get("linetype", "")
            if lt:
                tipi.add(lt)
            if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                stili.add(e.dxf.get("style", "Standard"))
            if e.dxftype() == "INSERT":
                blocchi.add(e.dxf.name)
                for a in e.attribs:
                    stili.add(a.dxf.get("style", "Standard"))
                    layer.add(a.dxf.get("layer", "0"))
            if e.dxftype() == "DIMENSION" and e.dxf.hasattr("geometry"):
                blocchi.add(e.dxf.geometry)
    # blocchi annidati: un blocco usato rende usati i blocchi che inserisce
    da_vedere = list(blocchi)
    while da_vedere:
        n = da_vedere.pop()
        if n in doc.blocks:
            for e in doc.blocks.get(n).query("INSERT"):
                if e.dxf.name not in blocchi:
                    blocchi.add(e.dxf.name)
                    da_vedere.append(e.dxf.name)
    for ly in doc.layers:
        tipi.add(ly.dxf.get("linetype", "Continuous"))
    for ds in doc.dimstyles:
        try:
            stili.add(ds.dxf.get("dimtxsty", "Standard"))
        except Exception:  # noqa: BLE001
            pass
    return blocchi, layer, stili, tipi


def pulisci_doc(doc, duplicati: bool = True, zero: bool = True, inutilizzati: bool = True, n: int = 2,
                applica: bool = False) -> dict:
    msp = doc.modelspace()
    via = []
    rep = {"doppioni": [], "lunghezza_zero": [], "testi_vuoti": [], "blocchi": [], "layer": [], "stili": [],
           "tipi_linea": []}
    if duplicati:
        visti = set()
        for e in msp:
            try:
                f = st.firma(e, n) + "|" + e.dxf.get("layer", "0")
            except Exception:  # noqa: BLE001
                continue
            if f in visti:
                via.append(e)
                rep["doppioni"].append(e.dxf.handle)
            else:
                visti.add(f)
    if zero:
        gia = {id(e) for e in via}
        for e in msp:
            if id(e) in gia:
                continue
            t = e.dxftype()
            if t == "LINE" and (e.dxf.start - e.dxf.end).magnitude < 1e-6:
                via.append(e)
                rep["lunghezza_zero"].append(e.dxf.handle)
            elif t == "LWPOLYLINE" and len({(round(p[0], 6), round(p[1], 6)) for p in e.get_points("xy")}) < 2:
                via.append(e)
                rep["lunghezza_zero"].append(e.dxf.handle)
            elif t in ("TEXT", "MTEXT") and not st._testo_entita(e).strip():
                via.append(e)
                rep["testi_vuoti"].append(e.dxf.handle)
    if applica:
        for e in via:
            msp.delete_entity(e)
    if inutilizzati:
        blocchi, layer, stili, tipi = _usati(doc)
        for b in list(doc.blocks):
            if not b.is_any_layout and not b.name.startswith("*") and b.name not in blocchi:
                rep["blocchi"].append(b.name)
        rep["layer"] = [ly.dxf.name for ly in doc.layers if ly.dxf.name not in layer]
        rep["stili"] = [s.dxf.name for s in doc.styles if s.dxf.name not in stili and s.dxf.name
                        and not s.dxf.name.startswith("*")]
        rep["tipi_linea"] = [t.dxf.name for t in doc.linetypes
                             if t.dxf.name.lower() not in {x.lower() for x in tipi} | {"bylayer", "byblock", "continuous"}]
        if applica:
            for nome in rep["blocchi"]:
                try:
                    doc.blocks.delete_block(nome, safe=False)
                except Exception:  # noqa: BLE001
                    pass
            for nome in rep["layer"]:
                try:
                    doc.layers.remove(nome)
                except Exception:  # noqa: BLE001
                    pass
            for nome in rep["stili"]:
                try:
                    doc.styles.remove(nome)
                except Exception:  # noqa: BLE001
                    pass
            for nome in rep["tipi_linea"]:
                try:
                    doc.linetypes.remove(nome)
                except Exception:  # noqa: BLE001
                    pass
    rep["totale"] = sum(len(v) for v in rep.values() if isinstance(v, list))
    return rep


def pulisci(dwg: str, duplicati: bool = True, zero: bool = True, inutilizzati: bool = True,
            applica: bool = False, out_dwg: str = "") -> dict:
    kw = dict(duplicati=duplicati, zero=zero, inutilizzati=inutilizzati)
    rep = pulisci_doc(st.leggi(dwg), applica=False, **kw)
    out = {"applicato": False, **{k: (v if not isinstance(v, list) else {"n": len(v), "elenco": v[:60]})
                                  for k, v in rep.items()}}
    if applica and rep["totale"]:
        from .lettura import _riedita_dwg
        r = _riedita_dwg(dwg, lambda doc: pulisci_doc(doc, applica=True, **kw), out_dwg)
        out.update(applicato=True, dwg=r.get("dwg", out_dwg or dwg), backup=r.get("backup", ""))
    return out


# ---------------------------------------------------------------------------
# versione del DWG
# ---------------------------------------------------------------------------

def converti_versione(dwg: str, versione: str = "2013", uscita: str = "") -> dict:
    """Riscrive un DWG in un'altra versione (2000, 2004, 2007, 2010, 2013, 2018) o in DXF ("dxf")."""
    from . import dwg_io
    from .lettura import ensure_dxf
    versione = str(versione).lower().replace("r", "").replace("acad", "")
    base = os.path.splitext(os.path.abspath(dwg))[0]
    if versione == "dxf":
        uscita = uscita or base + ".dxf"
        shutil.copy2(ensure_dxf(os.path.abspath(dwg)), uscita)
        return {"file": uscita, "versione": "DXF R2018"}
    if versione not in dwg_io.VERSIONI_ODA:
        raise ValueError(f"versione non valida: {versione} (scegli fra {', '.join(dwg_io.VERSIONI_ODA)} o dxf)")
    uscita = os.path.abspath(uscita or f"{base}_R{versione}.dwg")
    if os.path.abspath(dwg) == uscita:
        raise ValueError("indica un file di uscita diverso dall'originale")
    tmp = tempfile.mkdtemp(prefix="etcad_ver_")
    try:
        dxf = os.path.join(tmp, os.path.splitext(os.path.basename(uscita))[0] + ".dxf")
        shutil.copy2(ensure_dxf(os.path.abspath(dwg)), dxf)
        r = dwg_io.dxf_a_dwg([dxf], tmp, versione)
        prodotto = os.path.splitext(dxf)[0] + ".dwg"
        if not r.get("ok") or not os.path.isfile(prodotto):
            raise RuntimeError(f"conversione fallita: {r.get('errori') or r.get('log')}")
        os.makedirs(os.path.dirname(uscita), exist_ok=True)
        shutil.move(prodotto, uscita)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(uscita, "rb") as fh:
        firma_file = fh.read(6).decode("ascii", "replace")
    return {"file": uscita, "versione": versione, "codice_file": firma_file, "backend": r.get("backend")}


# ---------------------------------------------------------------------------
# tabelle disegnate
# ---------------------------------------------------------------------------

def _segmenti(doc, zona):
    """Segmenti orizzontali e verticali dentro la zona (LINE, polilinee, anche dentro i blocchi inseriti)."""
    import ezdxf.path as ep
    x0, y0, x1, y1 = zona
    oriz, vert = [], []

    def aggiungi(a, b):
        (ax, ay), (bx, by) = a, b
        if not (min(ax, bx) <= x1 and max(ax, bx) >= x0 and min(ay, by) <= y1 and max(ay, by) >= y0):
            return
        if abs(ay - by) < 0.05 and abs(ax - bx) > 0.5:
            oriz.append(((ay + by) / 2, min(ax, bx), max(ax, bx)))
        elif abs(ax - bx) < 0.05 and abs(ay - by) > 0.5:
            vert.append(((ax + bx) / 2, min(ay, by), max(ay, by)))

    def entita(c):
        for e in c:
            t = e.dxftype()
            if t == "INSERT":
                yield from entita(e.virtual_entities())
            elif t in ("LINE", "LWPOLYLINE", "POLYLINE"):
                yield e
    for e in entita(doc.modelspace()):
        try:
            pts = [(v.x, v.y) for v in ep.make_path(e).flattening(0.1)]
        except Exception:  # noqa: BLE001
            continue
        for a, b in zip(pts, pts[1:]):
            aggiungi(a, b)
    return oriz, vert


def _unici(valori, tol):
    out = []
    for v in sorted(valori):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def leggi_tabella_doc(doc, zona, tol: float = 0.3) -> dict:
    x0, y0, x1, y1 = zona
    oriz, vert = _segmenti(doc, zona)
    ys = sorted(_unici([y for y, _a, _b in oriz if y0 - tol <= y <= y1 + tol], tol), reverse=True)
    if len(ys) < 2:
        return {"righe": [], "avviso": "nella zona non ci sono almeno due linee orizzontali"}
    testi = []
    for e, dove, _ins in st._testi(doc, blocchi=False):
        if dove != "modello" and dove != "attributo":
            continue
        b = st._bbox(e)
        cx, cy = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else _punto_testo(e)
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            testi.append((cx, cy, st._testo_entita(e).strip()))
    righe, colonne = [], []
    for alto, basso in zip(ys, ys[1:]):
        if alto - basso < 1.0:
            continue
        yc = (alto + basso) / 2
        xs = _unici([x for x, a, b in vert if a - tol <= yc <= b + tol and x0 - tol <= x <= x1 + tol], tol)
        if len(xs) < 2:
            continue
        celle = [[] for _ in range(len(xs) - 1)]
        for cx, cy, s in testi:
            if basso <= cy <= alto:
                for j, (a, b) in enumerate(zip(xs, xs[1:])):
                    if a <= cx <= b:
                        celle[j].append((-cy, cx, s))
                        break
        righe.append([" ".join(s for _y, _x, s in sorted(c)) for c in celle])
        colonne.append([st._r(v) for v in xs])
    return {"righe": righe, "colonne": colonne, "n_righe": len(righe)}


def leggi_tabella(dwg: str, zona, xlsx: str = "") -> dict:
    res = leggi_tabella_doc(st.leggi(dwg), zona)
    if xlsx and res["righe"]:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Tabella"
        for r in res["righe"]:
            ws.append([_numero(v) for v in r])
        wb.save(xlsx)
        res["xlsx"] = os.path.abspath(xlsx)
    return res


def _numero(v: str):
    if re.fullmatch(r"-?\d+", v or ""):
        return int(v)
    if re.fullmatch(r"-?\d+[.,]\d+", v or ""):
        return float(v.replace(",", "."))
    return v


# ---------------------------------------------------------------------------
# schede da specifica
# ---------------------------------------------------------------------------

SPEC_ESEMPIO = {
    "titolo": "CAVO SENSORE M12 - 4 POLI", "codice": "CAV-M12-4P-2000", "lunghezza": 2000,
    "sinistra": {"tipo": "circolare", "sigla": "-XS1", "descrizione": "M12 femmina 4 poli", "poli": 4},
    "destra": {"tipo": "morsetti", "sigla": "-X2"},
    "cavo": "Cavo PUR 4x0,34", "fili": [["1", "BN", "+24 V", "1"], ["2", "WH", "uscita 2", "2"],
                                        ["3", "BU", "0 V", "3"], ["4", "BK", "uscita 1", "4"]],
}


def crea_scheda(spec: dict, uscita: str, tipo: str = "cavo", anteprima: bool = True) -> dict:
    """Disegna una scheda cavo (tipo "cavo") o uno schema ingressi (tipo "ingressi") da una specifica.
    I campi mancanti prendono quelli della scheda di esempio (vista.demo.DEMO)."""
    from . import schede as demo
    # dall'esempio solo la struttura; i contenuti (cavo, distinta, note...) restano vuoti se non indicati
    base = {"titolo": "SCHEDA CAVO", "codice": "", "sottotitolo": "", "cavo": "", "sezione": "", "lunghezza": 1000,
            "sinistra": dict(demo.DEMO["sinistra"]), "destra": dict(demo.DEMO["destra"]),
            "intestazione": demo.DEMO["intestazione"], "fili": [], "distinta": [], "note": [], "foglio": "1", "di": "1"}
    base.update({k: v for k, v in spec.items() if v is not None})
    if not spec.get("sottotitolo") and tipo == "cavo":
        base["sottotitolo"] = f"Da {base['sinistra'].get('sigla', '')} a {base['destra'].get('sigla', '')}"
    base["distinta"] = [tuple(str(x) for x in r) for r in base.get("distinta", [])]
    base["fili"] = [tuple(str(x) for x in f) for f in base.get("fili", [])]
    if tipo == "cavo":
        if not base["fili"] or any(len(f) != 4 for f in base["fili"]):
            raise ValueError("fili: lista di [da, colore, funzione, a]")
        if "nota_codice" not in spec:
            base["nota_codice"] = f"L = {base.get('lunghezza', '')} mm"
    d = demo.Disegno()
    if tipo == "ingressi":
        if not base.get("sensori"):
            raise ValueError("sensori: lista di [sigla, descrizione] (descrizione su due righe con |)")
        base["sensori"] = [tuple(s) for s in base["sensori"]]
        demo.schema_ingressi(d, base, blocco=True)
    elif tipo == "sdoppiatore":
        demo.sdoppiatore(d, base, blocco=True)
    else:
        demo.scheda_cavo(d, base, blocco=True)
    out = _salva(d.doc, uscita)
    if anteprima:
        from . import render
        png = os.path.splitext(out["file"])[0] + ".png"
        im, _ = render.cad_png(out["file"], zona=(0, 0, 420, 297), lato_max_px=1800)
        im.save(png)
        out["anteprima"] = png
    try:
        out["controllo_grafico"] = len(st.controlla(out["file"]).get("problemi", []))
    except Exception:  # noqa: BLE001 - il controllo e' un di piu'
        pass
    return out


# ---------------------------------------------------------------------------
# PDF di tutti i fogli
# ---------------------------------------------------------------------------

def pdf_fogli(dwg: str, pdf_out: str = "") -> dict:
    """Un PDF vettoriale con una pagina per foglio (multifoglio a layer o fogli affiancati con cornice)."""
    from ezdxf import bbox
    from . import render
    doc = st.leggi(dwg)
    zone = zone_fogli(doc, dwg)
    pdf_out = os.path.abspath(pdf_out or os.path.splitext(dwg)[0] + "_fogli.pdf")
    pagine = []
    if not zone:
        pagine.append((None, []))
    elif "layer" in zone[0]:
        strati = [z["layer"] for z in zone]
        msp = doc.modelspace()
        for z in zone:
            ents = [e for e in msp if e.dxf.get("layer", "0") == z["layer"]
                    or (e.dxf.get("layer", "0") == "0" and e.dxftype() == "INSERT")]
            b = bbox.extents(ents, fast=True)
            zona = (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) if b.has_data else None
            pagine.append((zona, [s for s in strati if s != z["layer"]]))
    else:
        pagine = [(tuple(z["zona"]), []) for z in zone]
    pdf = render.cad_pdf_pagine(dwg, pagine)
    render.unisci_pdf(pdf, pdf_out)
    return {"pdf": pdf_out, "pagine": len(pdf), "fogli": [z["foglio"] for z in zone] or ["1"]}
