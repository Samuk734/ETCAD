"""Modifiche disegnate dall'utente nella vista -> operazioni di modifica.py sul DWG.

La pagina tiene una lista ordinata di operazioni ("bozza") per foglio:

  {"k": "add", "id": "u3", "tipo": "linea" | "polilinea" | "rettangolo" | "cerchio" | "arco" | "testo"
              | "quota" | "etichetta", ...geometria..., "layer"?, "colore"? (ACI)}
  {"k": "del",  "hd" | "rif"}                      elimina (entita' del DWG o oggetto della bozza)
  {"k": "move", "hd" | "rif", "dx", "dy"}
  {"k": "copy", "hd" | "rif", "dx", "dy", "id"}    la copia diventa "id" per le operazioni dopo
  {"k": "text", "hd" | "rif", "testo", "tag"?}     testo, MTEXT o attributo (tag) di un blocco
  {"k": "prop", "hd" | "rif", "layer"?, "colore"?, "altezza"?}

"hd" e' l'handle di un'entita' gia' nel DWG; "rif" l'id di un oggetto creato prima nella stessa bozza:
gli handle veri si conoscono solo mentre si applica, quindi si risolvono qui, in ordine.
Coordinate in mm del disegno (quelle della vista).
"""
from __future__ import annotations

import math

from .. import modifica


TTF = ("ARIAL", "CALIBRI", "TIMES", "COUR", "SWISS")


def shx(testo: str, stile: str = "ROMANS") -> str:
    """Nei font SHX Ø ° ± si scrivono %%c %%d %%p (come fa SPAC)."""
    if any(stile.upper().startswith(x) for x in TTF):
        return testo
    return testo.replace("Ø", "%%c").replace("ø", "%%c").replace("°", "%%d").replace("±", "%%p")


def _add(op: dict, layer_def: str) -> dict:
    t = op["tipo"]
    if "testo" in op and op["testo"]:
        op = {**op, "testo": shx(op["testo"], op.get("stile", "ROMANS"))}
    base = {"layer": op.get("layer") or layer_def}
    if op.get("colore") not in (None, "", 256):
        base["colore"] = int(op["colore"])
    if t == "linea":
        return {"op": "aggiungi_linea", "p0": op["p0"], "p1": op["p1"], **base}
    if t in ("polilinea", "rettangolo", "nuvola", "matita"):
        return {"op": "aggiungi_polilinea", "punti": op["punti"], "chiusa": bool(op.get("chiusa", t == "rettangolo")),
                **base}
    if t == "cerchio":
        return {"op": "aggiungi_cerchio", "centro": op["centro"], "raggio": op["raggio"], **base}
    if t == "arco":
        return {"op": "aggiungi_arco", "centro": op["centro"], "raggio": op["raggio"], "a0": op["a0"], "a1": op["a1"],
                **base}
    if t == "testo":
        return {"op": "aggiungi_testo", "testo": op["testo"], "x": op["x"], "y": op["y"],
                "altezza": op.get("altezza", 2.5), "rotazione": op.get("rotazione", 0), "stile": op.get("stile", "ROMANS"),
                "allineamento": op.get("allineamento", "L"), **base}
    if t == "etichetta":
        return {"op": "aggiungi_etichetta", "testo": op["testo"], "x": op["x"], "y": op["y"], "punta": op.get("punta"),
                "altezza": op.get("altezza", 2.5), "stile": op.get("stile", "ROMANS"), **base}
    if t == "quota":
        p1, p2, pos = op["p1"], op["p2"], op.get("pos") or op["p2"]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        L = math.hypot(dx, dy) or 1.0
        dist = ((pos[0] - p1[0]) * -dy + (pos[1] - p1[1]) * dx) / L     # + a sinistra di p1 -> p2
        return {"op": "aggiungi_quota", "p1": p1, "p2": p2, "distanza": dist, "testo": op.get("testo") or "",
                "altezza": op.get("altezza", 2.5), "stile": op.get("stile", "ROMANS"), **base}
    raise ValueError(f"tipo di oggetto sconosciuto: {t}")


def applica(doc, ops: list[dict], layer_def: str = "0") -> list[dict]:
    """Applica la bozza al documento; ritorna il report di modifica.applica, operazione per operazione."""
    mappa: dict[str, list[str]] = {}
    report = []

    def handles(op):
        if op.get("rif"):
            if op["rif"] not in mappa:
                raise ValueError(f"oggetto {op['rif']} della bozza non trovato (gia' eliminato?)")
            return list(mappa[op["rif"]])
        return [str(op["hd"])]

    def esegui(mo):
        r = modifica.applica(doc, [mo])[0]
        report.append(r)
        return r

    for op in ops:
        k = op.get("k")
        if k == "add":
            h = esegui(_add(op, layer_def))["handle"]
            mappa[op["id"]] = h if isinstance(h, list) else [h]
        elif k == "del":
            for h in handles(op):
                esegui({"op": "elimina", "handle": h})
            if op.get("rif"):
                mappa.pop(op["rif"], None)
        elif k == "move":
            for h in handles(op):
                esegui({"op": "sposta", "handle": h, "dx": op["dx"], "dy": op["dy"]})
        elif k == "copy":
            mappa[op["id"]] = [esegui({"op": "copia", "handle": h, "dx": op["dx"], "dy": op["dy"]})["handle"]
                               for h in handles(op)]
        elif k == "text":
            hs = handles(op)
            if op.get("rif"):              # etichetta: il primo handle e' il testo
                hs = [h for h in hs if doc.entitydb.get(h.upper()) is not None
                      and doc.entitydb[h.upper()].dxftype() in ("TEXT", "MTEXT")][:1] or hs[:1]
            e = doc.entitydb.get(hs[0].upper())
            st = (e.dxf.get("style", "ROMANS") if e is not None and e.dxf.is_supported("style") else "ROMANS")
            esegui({"op": "testo", "handle": hs[0], "testo": shx(op["testo"], st), "tag": op.get("tag", "")})
        elif k == "prop":
            for h in handles(op):
                mo = {"op": "proprieta", "handle": h}
                for x in ("layer", "colore", "altezza"):
                    if op.get(x) not in (None, ""):
                        mo[x] = op[x]
                esegui(mo)
        else:
            raise ValueError(f"operazione della bozza sconosciuta: {k}")
    return report
