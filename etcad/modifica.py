"""Operazioni di modifica su un disegno (documento ezdxf), in batch.

Usate da dwg_modifica (DWG -> DXF -> operazioni -> DWG nativo) e dalle
correzioni dopo la conversione PDF -> DWG. Coordinate in mm del disegno.

Operazioni (campo "op"):
  testo            handle, testo [, tag]    TEXT/MTEXT/ATTRIB, o attributo di un INSERT
  elimina          handle
  sposta           handle, dx, dy
  proprieta        handle [, layer, colore (ACI), spessore (mm), tipo_linea,
                   altezza, rotazione, stile]
  aggiungi_testo   testo, x, y [, altezza=2.5, rotazione=0, stile=ROMANS,
                   allineamento=L|C|R, layer=TESTI, spessore]
  aggiungi_linea   p0 [x,y], p1 [x,y] [, layer, spessore, tipo_linea]
  aggiungi_polilinea punti [[x,y],...] [, chiusa, layer, spessore]
  aggiungi_cerchio centro [x,y], raggio [, layer, spessore]
  aggiungi_arco    centro, raggio, a0, a1 (gradi, antiorario)
  aggiungi_blocco  nome, x, y [, scala, rotazione, attributi {tag: valore},
                   da_file (DWG/DXF da cui importare il blocco), layer]
  aggiungi_etichetta testo, x, y [, altezza, stile, larghezza (fattore), punta [x,y],
                   sottolinea (true), sotto (mm fra base e sottolineatura), freccia (mm), layer]
                   testo + sottolineatura + richiamo fino alla punta + freccia piena
  aggiungi_quota   p1 [x,y], p2 [x,y], distanza (mm, + a sinistra di p1->p2) [, testo, altezza=2.5,
                   decimali=1, layer=QUOTE, stile=ROMANS]   quota allineata vera (DIMENSION)
  copia            handle, dx, dy                 copia dell'entita' spostata (anche blocchi con attributi)
  layer            nome [, congela (bool), colore, crea (bool)]
"""
from __future__ import annotations

LW = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100,
      106, 120, 140, 158, 200, 211]


def _lw(mm) -> int:
    if mm is None:
        return -1
    v = float(mm) * 100
    return min(LW, key=lambda s: abs(s - v))


def _attr(op: dict, layer_def: str) -> dict:
    a = {"layer": op.get("layer", layer_def)}
    if op.get("spessore") is not None:
        a["lineweight"] = _lw(op["spessore"])
    if op.get("colore") is not None:
        a["color"] = int(op["colore"])
    if op.get("tipo_linea"):
        a["linetype"] = op["tipo_linea"]
    return a


def _entita(doc, h: str):
    e = doc.entitydb.get(str(h).upper())
    if e is None:
        raise ValueError(f"handle {h} non trovato")
    return e


def _layer(doc, nome: str) -> None:
    if nome and nome not in doc.layers:
        doc.layers.add(nome)


def _stile(doc, nome: str) -> None:
    from .pdf2dwg import STILI_SHX
    if nome and nome not in doc.styles:
        doc.styles.add(nome, font=STILI_SHX.get(nome.upper(), "romans.shx"))


def _importa_blocco(doc, nome: str, da_file: str) -> None:
    import ezdxf
    from ezdxf.addons import Importer
    from .lettura import ensure_dxf
    if nome in doc.blocks:
        return
    src = ezdxf.readfile(ensure_dxf(da_file))
    if nome not in src.blocks:
        raise ValueError(f"blocco {nome} assente in {da_file}")
    imp = Importer(src, doc)
    imp.import_block(nome)
    imp.finalize()


def _etichetta(doc, msp, op: dict) -> list[str]:
    """Testo + sottolineatura + richiamo fino a `punta` + freccia piena, come nelle schede cavo."""
    import math
    from .strumenti import larghezza_testo
    a = _attr(op, "TESTI")
    _layer(doc, a["layer"])
    stile = op.get("stile", "ROMANS")
    _stile(doc, stile)
    h, wf = float(op.get("altezza", 2.5)), float(op.get("larghezza", 1.0))
    x, y = float(op["x"]), float(op["y"])
    t = msp.add_text(op["testo"], height=h, dxfattribs={**a, "style": stile, "width": wf})
    t.set_placement((x, y))
    out = [t.dxf.handle]
    w = larghezza_testo(op["testo"], h, stile, wf, doc=doc)
    yu = y - float(op.get("sotto", max(0.6, 0.3 * h)))
    u0, u1 = (x - 0.4, yu), (x + w + 0.4, yu)
    if op.get("sottolinea", True):
        out.append(msp.add_line(u0, u1, dxfattribs=a).dxf.handle)
    if op.get("punta"):
        px, py = float(op["punta"][0]), float(op["punta"][1])
        da = min((u0, u1), key=lambda q: math.hypot(q[0] - px, q[1] - py))
        out.append(msp.add_line(da, (px, py), dxfattribs=a).dxf.handle)
        L = math.hypot(px - da[0], py - da[1])
        lf = float(op.get("freccia", min(1.8, 0.45 * L)))
        if lf > 0 and L > 1e-6:
            vx, vy = (px - da[0]) / L, (py - da[1]) / L
            bx, by, hw = px - vx * lf, py - vy * lf, lf * 0.28
            ha = {k: v for k, v in a.items() if k != "linetype"}
            hatch = msp.add_hatch(color=int(a.get("color", 256)), dxfattribs=ha)
            hatch.paths.add_polyline_path([(px, py), (bx - vy * hw, by + vx * hw), (bx + vy * hw, by - vx * hw)],
                                          is_closed=True)
            out.append(hatch.dxf.handle)
    return out


def applica(doc, operazioni: list[dict]) -> list[dict]:
    """Applica le operazioni in ordine; ritorna un report per operazione."""
    from ezdxf.enums import TextEntityAlignment
    msp = doc.modelspace()
    report = []
    for n, op in enumerate(operazioni):
        tipo = op.get("op", "")
        r = {"n": n, "op": tipo}
        if tipo == "testo":
            e = _entita(doc, op["handle"])
            t, tag = e.dxftype(), op.get("tag", "")
            if t == "INSERT":
                trov = [a for a in e.attribs if a.dxf.tag == tag] if tag else []
                if not trov:
                    raise ValueError(f"handle {op['handle']}: attributo '{tag}' assente "
                                     f"(presenti: {[a.dxf.tag for a in e.attribs]})")
                r["vecchio"] = trov[0].dxf.text
                trov[0].dxf.text = op["testo"]
            elif t in ("TEXT", "ATTRIB", "ATTDEF"):
                r["vecchio"] = e.dxf.text
                e.dxf.text = op["testo"]
            elif t == "MTEXT":
                r["vecchio"] = e.text
                e.text = op["testo"]
            else:
                raise ValueError(f"handle {op['handle']}: tipo {t} senza testo")
            r.update({"handle": op["handle"], "tag": tag, "nuovo": op["testo"]})
        elif tipo == "elimina":
            e = _entita(doc, op["handle"])
            r.update({"handle": op["handle"], "tipo": e.dxftype(), "layer": e.dxf.layer})
            lay = e.get_layout()
            if lay is not None:
                lay.delete_entity(e)
            else:
                e.destroy()
        elif tipo == "sposta":
            e = _entita(doc, op["handle"])
            e.translate(float(op.get("dx", 0)), float(op.get("dy", 0)), 0)
            r.update({"handle": op["handle"]})
        elif tipo == "proprieta":
            e = _entita(doc, op["handle"])
            if op.get("layer"):
                _layer(doc, op["layer"])
                e.dxf.layer = op["layer"]
            if op.get("colore") is not None:
                e.dxf.color = int(op["colore"])
            if op.get("spessore") is not None:
                e.dxf.lineweight = _lw(op["spessore"])
            if op.get("tipo_linea"):
                e.dxf.linetype = op["tipo_linea"]
            for k_in, k_out in (("altezza", "height"), ("rotazione", "rotation"),
                                ("stile", "style"), ("larghezza", "width"),
                                ("obliquo", "oblique")):
                if op.get(k_in) is not None and e.dxf.is_supported(k_out):
                    if k_out == "style":
                        _stile(doc, op[k_in])
                    e.dxf.set(k_out, op[k_in])
            r.update({"handle": op["handle"]})
        elif tipo == "aggiungi_testo":
            a = _attr(op, "TESTI")
            _layer(doc, a["layer"])
            stile = op.get("stile", "ROMANS")
            _stile(doc, stile)
            a.update({"style": stile, "oblique": float(op.get("obliquo", 0)),
                      "width": float(op.get("larghezza", 1.0))})
            e = msp.add_text(op["testo"], height=float(op.get("altezza", 2.5)),
                             rotation=float(op.get("rotazione", 0)), dxfattribs=a)
            al = {"L": TextEntityAlignment.LEFT, "C": TextEntityAlignment.CENTER,
                  "R": TextEntityAlignment.RIGHT,
                  "M": TextEntityAlignment.MIDDLE_CENTER}.get(
                      str(op.get("allineamento", "L")).upper()[:1], TextEntityAlignment.LEFT)
            e.set_placement((float(op["x"]), float(op["y"])), align=al)
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_linea":
            a = _attr(op, "GEOMETRIA")
            _layer(doc, a["layer"])
            e = msp.add_line(tuple(op["p0"]), tuple(op["p1"]), dxfattribs=a)
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_polilinea":
            a = _attr(op, "GEOMETRIA")
            _layer(doc, a["layer"])
            e = msp.add_lwpolyline([tuple(p) for p in op["punti"]],
                                   close=bool(op.get("chiusa", False)), dxfattribs=a)
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_cerchio":
            a = _attr(op, "GEOMETRIA")
            _layer(doc, a["layer"])
            e = msp.add_circle(tuple(op["centro"]), float(op["raggio"]), dxfattribs=a)
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_arco":
            a = _attr(op, "GEOMETRIA")
            _layer(doc, a["layer"])
            e = msp.add_arc(tuple(op["centro"]), float(op["raggio"]),
                            float(op["a0"]), float(op["a1"]), dxfattribs=a)
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_blocco":
            nome = op["nome"]
            if op.get("da_file"):
                _importa_blocco(doc, nome, op["da_file"])
            if nome not in doc.blocks:
                raise ValueError(f"blocco {nome} non definito (usa da_file)")
            a = _attr(op, "0")
            _layer(doc, a["layer"])
            s = float(op.get("scala", 1.0))
            a.update({"xscale": s, "yscale": s, "zscale": s,
                      "rotation": float(op.get("rotazione", 0))})
            e = msp.add_blockref(nome, (float(op["x"]), float(op["y"])), dxfattribs=a)
            if op.get("attributi"):
                e.add_auto_attribs({str(k): str(v) for k, v in op["attributi"].items()})
            r["handle"] = e.dxf.handle
        elif tipo == "aggiungi_etichetta":
            r["handle"] = _etichetta(doc, msp, op)
        elif tipo == "aggiungi_quota":
            a = _attr(op, "QUOTE")
            _layer(doc, a["layer"])
            h = float(op.get("altezza", 2.5))
            stile = op.get("stile", "ROMANS")
            _stile(doc, stile)
            ds = op.get("stile_quota") or ("Standard" if "Standard" in doc.dimstyles else "EZDXF")
            if ds not in doc.dimstyles:
                doc.dimstyles.new(ds)
            ov = {"dimtxt": h, "dimasz": h * 0.8, "dimexe": h * 0.5, "dimexo": h * 0.3, "dimgap": h * 0.3,
                  "dimtad": 1, "dimdec": int(op.get("decimali", 1)), "dimzin": 8, "dimtxsty": stile, "dimtih": 0,
                  "dimtoh": 0}
            dim = msp.add_aligned_dim(p1=tuple(op["p1"][:2]), p2=tuple(op["p2"][:2]),
                                      distance=float(op.get("distanza", 5)), text=op.get("testo") or "<>",
                                      dimstyle=ds, override=ov, dxfattribs=a)
            dim.render()
            r["handle"] = dim.dimension.dxf.handle
        elif tipo == "copia":
            e = _entita(doc, op["handle"])
            c = e.copy()
            msp.add_entity(c)
            c.translate(float(op.get("dx", 0)), float(op.get("dy", 0)), 0)
            r.update({"da": op["handle"], "handle": c.dxf.handle})
        elif tipo == "layer":
            nome = op["nome"]
            if nome not in doc.layers:
                if not op.get("crea", False):
                    raise ValueError(f"layer {nome} assente (crea=true per crearlo)")
                doc.layers.add(nome)
            lay = doc.layers.get(nome)
            if op.get("congela") is True:
                lay.freeze()
            elif op.get("congela") is False:
                lay.on()
                lay.thaw()
            if op.get("colore") is not None:
                lay.color = int(op["colore"])
            r.update({"layer": nome, "congelato": lay.is_frozen(), "colore": lay.color})
        else:
            raise ValueError(f"operazione {n}: '{tipo}' sconosciuta")
        report.append(r)
    return report


def modifica_dwg(dwg_path: str, operazioni: list[dict], out_dwg: str = "") -> dict:
    """Applica le operazioni al DWG (o DXF) e salva DWG nativo."""
    from .lettura import _riedita_dwg
    return _riedita_dwg(dwg_path, lambda doc: applica(doc, operazioni), out_dwg)
