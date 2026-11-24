"""Leggere, vedere e modificare DWG/multifogli SPAC (headless, SPAC chiuso OK).

Scoperta sul formato: in un multifoglio SPAC tutti i fogli stanno nello
stesso spazio modello, separati per LAYER (foglio 01 -> layer "1", ...).
La mappa foglio->layer e' in dbSPAC.PlotSequence(layer); i layer "N_REV_xx"
sono le revisioni del foglio N.

Flusso: DWG -> DXF (accoreconsole di SPAC oppure ODA, con cache) -> ezdxf
per lettura strutturata, anteprime e modifiche; le modifiche tornano DWG
nativo con lo stesso convertitore. Gli originali vengono sovrascritti solo
se richiesto, e sempre dopo una copia di backup.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time

CACHE_DIR = os.path.join(tempfile.gettempdir(), "spac_dxf_cache")


def cartella_backup() -> str:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = os.path.join(base, "etcad", "backup")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_dxf(dwg_path: str) -> str:
    """DWG -> DXF con cache su (percorso, dimensione, data). DXF: diretto."""
    from . import dwg_io
    if not os.path.isfile(dwg_path):
        raise FileNotFoundError(dwg_path)
    if dwg_path.lower().endswith(".dxf"):
        return dwg_path
    st = os.stat(dwg_path)
    key = hashlib.md5(
        f"{os.path.abspath(dwg_path)}|{st.st_size}|{st.st_mtime}".encode()
    ).hexdigest()
    os.makedirs(CACHE_DIR, exist_ok=True)
    dxf = os.path.join(CACHE_DIR, key + ".dxf")
    if not os.path.isfile(dxf):
        dwg_io.dwg_a_dxf(dwg_path, dxf)
    return dxf


def foglio_layer(db_path: str, multifoglio: str, foglio: str) -> str:
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro",
                          uri=True)
    try:
        r = con.execute("SELECT layer FROM PlotSequence "
                        "WHERE multifoglio=? AND foglio=?",
                        (multifoglio, foglio)).fetchone()
    finally:
        con.close()
    if not r or not r[0]:
        raise ValueError(f"foglio {foglio} non in PlotSequence per {multifoglio}")
    return str(r[0])


def _sheet_layers(doc, layer: str, includi_rev: bool = False) -> set[str]:
    # Verita' SPAC (provata sulle stampe PDF): il foglio mostra il layer base.
    # I layer N_REV_xx sono snapshot storici congelati: esclusi di default.
    # Il layer 0 contiene legenda/tabelle globali: vedi _keep_entity.
    names = {l.dxf.name for l in doc.layers}
    keep = set()
    if layer in names:
        keep.add(layer)
    if includi_rev:
        for n in names:
            if n.startswith(layer + "_REV_"):
                keep.add(n)
    return keep


def _keep_entity(e, keep: set[str]) -> bool:
    if e.dxf.layer in keep:
        return True
    return e.dxf.layer == "0" and e.dxftype() == "INSERT"


# ---------------------------------------------------------------------------
# descrizione entita' (per l'AI: handle, posizione, testo)
# ---------------------------------------------------------------------------

def _r(v) -> float:
    return round(float(v), 2)


def descrivi(e, geometria: bool = True) -> dict:
    """Riassunto compatto di un'entita' (handle, tipo, layer, posizione)."""
    t = e.dxftype()
    d = {"handle": e.dxf.handle, "tipo": t, "layer": e.dxf.layer}
    try:
        if t in ("TEXT", "ATTRIB", "ATTDEF"):
            p = e.dxf.insert
            d.update({"testo": e.dxf.text, "x": _r(p[0]), "y": _r(p[1]),
                      "altezza": _r(e.dxf.height), "rotazione": _r(e.dxf.get("rotation", 0)),
                      "stile": e.dxf.get("style", "")})
        elif t == "MTEXT":
            p = e.dxf.insert
            d.update({"testo": e.text[:300], "x": _r(p[0]), "y": _r(p[1]),
                      "altezza": _r(e.dxf.char_height)})
        elif t == "INSERT":
            p = e.dxf.insert
            d.update({"blocco": e.dxf.name, "x": _r(p[0]), "y": _r(p[1]),
                      "rotazione": _r(e.dxf.get("rotation", 0)),
                      "scala": _r(e.dxf.get("xscale", 1))})
            if e.attribs:
                d["attributi"] = {a.dxf.tag: a.dxf.text[:200] for a in e.attribs}
                d["attributi_handle"] = {a.dxf.tag: a.dxf.handle for a in e.attribs}
        elif geometria and t == "LINE":
            d.update({"p0": [_r(e.dxf.start[0]), _r(e.dxf.start[1])],
                      "p1": [_r(e.dxf.end[0]), _r(e.dxf.end[1])]})
        elif geometria and t == "LWPOLYLINE":
            pts = [[_r(p[0]), _r(p[1])] for p in e.get_points("xy")]
            d.update({"punti": pts[:60], "n_punti": len(pts), "chiusa": e.closed})
        elif geometria and t in ("CIRCLE", "ARC"):
            c = e.dxf.center
            d.update({"centro": [_r(c[0]), _r(c[1])], "raggio": _r(e.dxf.radius)})
            if t == "ARC":
                d.update({"a0": _r(e.dxf.start_angle), "a1": _r(e.dxf.end_angle)})
        if e.dxf.hasattr("linetype") and e.dxf.linetype.upper() not in ("BYLAYER",):
            d["tipo_linea"] = e.dxf.linetype
        lw = e.dxf.get("lineweight", -1)
        if lw >= 0:
            d["spessore_mm"] = lw / 100
    except Exception:  # noqa: BLE001 - entita' con dati incompleti
        pass
    return d


def _bbox_entita(e):
    try:
        from ezdxf import bbox
        b = bbox.extents([e], fast=True)
        if b.has_data:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:  # noqa: BLE001
        pass
    return None


def _in_zona(b, zona) -> bool:
    if not zona:
        return True
    if b is None:
        return False
    return not (b[2] < zona[0] or b[0] > zona[2] or b[3] < zona[1] or b[1] > zona[3])


def entita_dwg(dwg_path: str, zona=None, tipi: str = "", layer: str = "",
               testo: str = "", limite: int = 300, layout: str = "Model") -> dict:
    """Entita' di un DWG/DXF filtrate per zona (mm), tipo, layer, testo."""
    import ezdxf
    from .render import _zona
    doc = ezdxf.readfile(ensure_dxf(dwg_path))
    lay = doc.modelspace() if layout in ("", "Model") else doc.layouts.get(layout)
    z = _zona(zona, 0, 0) if zona else None
    tset = {t.strip().upper() for t in tipi.split(",") if t.strip()}
    out, tot = [], 0
    for e in lay:
        if tset and e.dxftype() not in tset:
            continue
        if layer and e.dxf.layer != layer:
            continue
        if testo:
            s = descrivi(e, False)
            blob = (s.get("testo", "") + " " + " ".join(s.get("attributi", {}).values())).lower()
            if testo.lower() not in blob:
                continue
        if z and not _in_zona(_bbox_entita(e), z):
            continue
        tot += 1
        if len(out) < limite:
            out.append(descrivi(e))
    return {"dwg": dwg_path, "zona_mm": z, "trovate": tot, "entita": out,
            "troncato": tot > len(out)}


def leggi_foglio(dwg_path: str, db_path: str, multifoglio: str,
                 foglio: str, limite: int = 500,
                 includi_rev: bool = False, geometria: bool = False) -> dict:
    """Contenuto leggibile del foglio: testi, simboli (con handle e
    posizione), conteggi per tipo; geometria=True aggiunge linee/cerchi.

    Default = vista SPAC (layer base + cornice); includi_rev=True aggiunge
    gli snapshot storici N_REV_xx. Ritorna anche il dettaglio per layer.
    """
    import ezdxf
    layer = foglio_layer(db_path, multifoglio, foglio)
    doc = ezdxf.readfile(ensure_dxf(dwg_path))
    keep = _sheet_layers(doc, layer, includi_rev)
    testi, simboli, geo = [], [], []
    tipi, per_layer = {}, {}
    for e in doc.modelspace():
        if not _keep_entity(e, keep):
            continue
        t = e.dxftype()
        tipi[t] = tipi.get(t, 0) + 1
        per_layer[e.dxf.layer] = per_layer.get(e.dxf.layer, 0) + 1
        if len(testi) + len(simboli) + len(geo) >= limite:
            continue
        if t in ("MTEXT", "TEXT"):
            testi.append(descrivi(e, False))
        elif t == "INSERT":
            simboli.append(descrivi(e, False))
        elif geometria and t in ("LINE", "LWPOLYLINE", "CIRCLE", "ARC"):
            geo.append(descrivi(e, True))
    out = {"multifoglio": multifoglio, "foglio": foglio, "layer": layer,
           "layers_inclusi": sorted(keep),
           "entita_totali": sum(tipi.values()), "per_tipo": tipi,
           "per_layer": per_layer,
           "testi": testi, "simboli": simboli,
           "troncato": len(testi) + len(simboli) + len(geo) >= limite}
    if geometria:
        out["geometria"] = geo
    return out


def doc_foglio(dwg_path: str, db_path: str, multifoglio: str, foglio: str,
               includi_rev: bool = False):
    """Documento ezdxf con solo il foglio richiesto visibile (per anteprime)."""
    import ezdxf
    layer = foglio_layer(db_path, multifoglio, foglio)
    doc = ezdxf.readfile(ensure_dxf(dwg_path))
    keep = _sheet_layers(doc, layer, includi_rev)
    for name in keep | {"0"}:  # SPAC salva i layer foglio congelati: scongela
        try:
            doc.layers.get(name).on()
            doc.layers.get(name).thaw()
        except Exception:  # noqa: BLE001
            pass
    msp = doc.modelspace()
    for e in list(msp):
        if not _keep_entity(e, keep):
            msp.delete_entity(e)
    return doc, layer


def anteprima_foglio(dwg_path: str, db_path: str, multifoglio: str,
                     foglio: str, png_out: str = "", dpi: int = 0,
                     includi_rev: bool = False, zona=None,
                     lato_max_px: int = 1600) -> dict:
    """Render PNG del foglio come lo mostra SPAC (sfondo bianco, testi con
    glifi Hershey se mancano i font SHX). Ritorna percorso e byte PNG."""
    from . import render
    doc, layer = doc_foglio(dwg_path, db_path, multifoglio, foglio, includi_rev)
    im, info = render.cad_png(doc, zona=zona, dpi=dpi, lato_max_px=lato_max_px)
    if not png_out:
        fd, png_out = tempfile.mkstemp(prefix=f"spac_{foglio}_", suffix=".png")
        os.close(fd)
    im.save(png_out)
    return {"multifoglio": multifoglio, "foglio": foglio, "layer": layer,
            "png": png_out, "bytes": os.path.getsize(png_out),
            "pixel": list(im.size), **info, "_immagine": im}


def verifica_scrivibile(path: str) -> None:
    """Fallisce subito se il file non esiste o e' bloccato (es. aperto in SPAC)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    try:
        with open(path, "r+b"):
            pass
    except OSError as ex:
        raise RuntimeError(f"file bloccato (chiudere in SPAC?): {path}") from ex


def _riedita_dwg(dwg_path: str, edit_fn, out_dwg: str = "") -> dict:
    """Motore di scrittura: DXF -> modifica -> DWG nativo. edit_fn(doc)->report.

    Se out_dwg e' vuoto sovrascrive l'originale dopo averne fatto una copia
    in %LOCALAPPDATA%\\etcad\\backup (percorso nel risultato).
    """
    import ezdxf
    from . import dwg_io
    verifica_scrivibile(dwg_path)
    if out_dwg and os.path.isfile(out_dwg):
        verifica_scrivibile(out_dwg)
    dxf = ensure_dxf(dwg_path)
    tmp = tempfile.mkdtemp(prefix="spac_edit_")
    try:
        nome = os.path.splitext(os.path.basename(out_dwg or dwg_path))[0]
        dxf_edit = os.path.join(tmp, "edit.dxf")
        shutil.copy2(dxf, dxf_edit)
        doc = ezdxf.readfile(dxf_edit)
        report = edit_fn(doc)
        doc.saveas(dxf_edit)
        finale = out_dwg or dwg_path
        if finale.lower().endswith(".dxf"):
            shutil.copy2(dxf_edit, finale)
            return {"dwg": finale, "dettaglio": report, "n": len(report)}
        conv = dwg_io.dxf_a_dwg([dxf_edit], tmp, "2018")
        nuovo = os.path.join(tmp, "edit.dwg")
        if not conv["ok"] or not os.path.isfile(nuovo) or os.path.getsize(nuovo) == 0:
            raise RuntimeError(f"riconversione DWG fallita: {conv}")
        backup = ""
        if os.path.isfile(finale):
            verifica_scrivibile(finale)
            backup = os.path.join(cartella_backup(),
                                  f"{nome}_{time.strftime('%Y%m%d_%H%M%S')}.dwg")
            shutil.copy2(finale, backup)
        shutil.move(nuovo, finale)
        # Nota: la cache DXF si invalida da sola (chiave su size/mtime).
        return {"dwg": finale, "dettaglio": report, "n": len(report),
                "backup": backup, "backend": conv.get("backend")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def modifica_testi_dwg(dwg_path: str, modifiche: list[dict],
                       out_dwg: str = "") -> dict:
    """Modifica testi nel DWG: via DXF (ezdxf) + riconversione DWG nativa.

    modifiche = [{"handle": "D41", "tag": "N", "testo": "CAVO A 8cm"}]
    - INSERT: serve "tag" dell'attributo; TEXT/MTEXT/ATTRIB: "handle"+"testo".
    - out_dwg vuoto = sovrascrive l'originale (backup automatico).
    Ritorna report con vecchi/nuovi valori per ogni handle.
    """
    from . import modifica
    ops = [{"op": "testo", **m} for m in modifiche]
    res = _riedita_dwg(dwg_path, lambda doc: modifica.applica(doc, ops), out_dwg)
    return {"dwg": res["dwg"], "modifiche": res["dettaglio"], "n": res["n"],
            "backup": res.get("backup", "")}


def elimina_entita_dwg(dwg_path: str, handles: list[str],
                       out_dwg: str = "") -> dict:
    """Elimina entita' dal DWG per handle (backup automatico se sovrascrive)."""
    from . import modifica
    ops = [{"op": "elimina", "handle": h} for h in handles]
    return _riedita_dwg(dwg_path, lambda doc: modifica.applica(doc, ops), out_dwg)


def imposta_layer_dwg(dwg_path: str, layer: str, congela: bool | None = None,
                      colore: int | None = None, out_dwg: str = "") -> dict:
    """Congela/scongela o ricolora un layer nel DWG."""
    from . import modifica
    op = {"op": "layer", "nome": layer}
    if congela is not None:
        op["congela"] = congela
    if colore is not None:
        op["colore"] = colore
    return _riedita_dwg(dwg_path, lambda doc: modifica.applica(doc, [op]), out_dwg)


def collegamenti_dwg(dwg_path: str) -> dict:
    """Collegamenti dati (Excel) + tabelle del DWG, con stato aggiornamento.

    Fondamentale prima di toccare un Excel di commessa: se un foglio Excel
    e' collegato, va rispettata la struttura (foglio/intervallo) attesa.
    """
    import ezdxf
    doc = ezdxf.readfile(ensure_dxf(dwg_path))
    base = os.path.dirname(os.path.abspath(dwg_path))
    links = []
    try:
        dlink = doc.rootdict.get("ACAD_DATALINK")
        nomi_handle = {}
        for nome in list(dlink.keys()):
            try:
                h = getattr(getattr(dlink.get(nome), "dxf", None),
                            "handle", "")
                if h:
                    nomi_handle[str(h).upper()] = nome
            except Exception:  # noqa: BLE001 - voce dizionario illeggibile
                continue
    except Exception:  # noqa: BLE001
        nomi_handle = {}
    for o in doc.objects:
        if o.dxftype() != "DATALINK":
            continue
        try:  # DXFTagStorage espone xtags (proprieta'), non tags
            raw = getattr(o, "xtags", None)
            if raw is None:
                raw = getattr(o, "tags", [])
            tags = list(raw() if callable(raw) else raw)
        except Exception:  # noqa: BLE001
            tags = []
        val = {}
        for t in tags:  # prima occorrenza: i DATAMAP ripetono i codici vuoti
            if t.code in (1, 300, 301, 302) and t.code not in val:
                val[t.code] = t.value
        h = str(o.dxf.handle).upper()
        links.append({"handle": o.dxf.handle,
                      "nome": nomi_handle.get(h, ""),
                      "provider": val.get(1, ""),
                      "descrizione": str(val.get(301, ""))[:220],
                      "riferimento": str(val.get(302, ""))[:220]})
    # stato file collegati: piu' recenti del DWG = tabelle da aggiornare
    try:
        dwg_mtime = os.stat(dwg_path).st_mtime
    except OSError:
        dwg_mtime = 0
    for lk in links:
        rif = lk.get("riferimento", "")
        parte = rif.split("!")[0].replace("./", "").replace(".\\", "").strip("\"'")
        cand = parte if os.path.isabs(parte) else os.path.join(base, parte)
        lk["file"] = cand
        if os.path.isfile(cand):
            lk["file_mtime"] = os.stat(cand).st_mtime
            lk["da_aggiornare"] = os.stat(cand).st_mtime > dwg_mtime
        else:
            lk["da_aggiornare"] = None
    tabelle = []
    for e in doc.modelspace():
        if e.dxftype() == "ACAD_TABLE":
            try:
                loc = e.get_insert_location()
                pos = [round(float(v), 1) for v in loc]
            except Exception:  # noqa: BLE001
                pos = []
            tabelle.append({"handle": e.dxf.handle, "layer": e.dxf.layer,
                            "pos": pos})
    return {"dwg": dwg_path, "collegamenti": links, "tabelle": tabelle}


def coerenza_db(db_path: str, dwg_path: str, multifoglio: str,
                max_righe: int = 20) -> dict:
    """Righe DB con handle non piu' presenti nel DWG (entita' cancellate).

    Solo lettura. Utile dopo modifiche al DWG, prima di farlo aprire a SPAC.
    """
    import ezdxf
    from .multifoglio import TABLES_DWG_FOGLIO
    doc = ezdxf.readfile(ensure_dxf(dwg_path))

    def _vivo(h):
        try:
            return doc.entitydb.get(h) is not None  # get non solleva
        except Exception:  # noqa: BLE001
            return False

    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro",
                          uri=True)
    try:
        disp = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        fuori = {}
        for t in TABLES_DWG_FOGLIO:
            if t not in disp:
                continue
            cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            dwgc = "Dwg" if "Dwg" in cols else ("DWG" if "DWG" in cols else None)
            hc = "Handle" if "Handle" in cols else ("HANDLE" if "HANDLE" in cols else None)
            if not dwgc or not hc:
                continue
            rows = con.execute(f'SELECT "{hc}" FROM "{t}" WHERE "{dwgc}"=?',
                               (multifoglio,)).fetchall()
            morti = sorted({r[0] for r in rows if r[0] and not _vivo(r[0])})
            if morti:
                fuori[t] = {"n": len(morti), "handle": morti[:max_righe]}
        return {"multifoglio": multifoglio, "dangling": fuori,
                "tabelle_controllate": len([t for t in TABLES_DWG_FOGLIO
                                            if t in disp])}
    finally:
        con.close()


def scompatta_dxf(dwg_path: str, db_path: str, multifoglio: str,
                  fogli: list[str] | None, out_dir: str,
                  includi_rev: bool = False, dwg: bool = False) -> dict:
    """Split geometrico: un DXF (o DWG con dwg=True) per foglio."""
    import ezdxf
    from ezdxf.addons import Importer
    os.makedirs(out_dir, exist_ok=True)
    if fogli is None:
        con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro",
                              uri=True)
        try:
            fogli = [r[0] for r in con.execute(
                "SELECT foglio FROM PlotSequence WHERE multifoglio=? ORDER BY riga",
                (multifoglio,))]
        finally:
            con.close()
    base = os.path.splitext(os.path.basename(dwg_path))[0]
    src = ezdxf.readfile(ensure_dxf(dwg_path))  # letto una sola volta
    msp_src = src.modelspace()
    files = []
    for fg in fogli:
        layer = foglio_layer(db_path, multifoglio, fg)
        keep = _sheet_layers(src, layer, includi_rev)
        tgt = ezdxf.new("R2018")
        imp = Importer(src, tgt)
        imp.import_tables("layers")
        imp.import_tables("styles")
        imp.import_tables("linetypes")
        imp.import_entities([e for e in msp_src if _keep_entity(e, keep)])
        imp.finalize()
        for name in keep | {"0"}:  # apribile subito in CAD: scongela
            try:
                tgt.layers.get(name).on()
                tgt.layers.get(name).thaw()
            except Exception:  # noqa: BLE001
                pass
        fp = os.path.join(out_dir, f"{base}_foglio_{fg}.dxf")
        tgt.saveas(fp)
        files.append({"foglio": fg, "layer": layer, "file": fp,
                      "entita": len(tgt.modelspace())})
    if dwg and files:
        from . import dwg_io
        conv = dwg_io.dxf_a_dwg([f["file"] for f in files], out_dir)
        per_nome = {os.path.splitext(os.path.basename(d))[0]: d for d in conv["dwg"]}
        for f in files:
            f["dwg"] = per_nome.get(os.path.splitext(os.path.basename(f["file"]))[0], "")
    return {"multifoglio": multifoglio, "cartella": out_dir, "fogli": files}
