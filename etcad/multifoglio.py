"""Multifoglio: scompattare e ricompattare i fogli di uno schema SPAC.

Un multifoglio SPAC e' un singolo .dwg che contiene N fogli di schema.
La mappatura fogli e' nel DB (PlotSequence + tabelle con colonne Dwg/Foglio).

Livello DB (completo, headless, testato): scompatta/ricompatta i dati.
Livello DWG (geometria): vedi lettura.scompatta_dxf (un DXF/DWG per foglio).
"""
from __future__ import annotations

import json
import os
import sqlite3

from . import dbtools

TABLES_DWG_FOGLIO = [
    "Elementi", "MorsConn", "Rimandi", "Cbl_Harness", "Jumpers",
    "Ordinamento_Morsettiere", "Distinta", "SegnaliniDatiCavo",
    "Structure", "Layout", "PLANT_DIAGRAM", "TabelleOutputs",
    "Unused_Componenti", "BIT_DWG", "MODULI_DWG", "tesysMod_DWG",
]


def fogli_list(db_path: str, multifoglio: str) -> list[dict]:
    """Fogli del multifoglio con descrizione e conteggi elementi."""
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        seq = con.execute(
            "SELECT foglio, descrizione FROM PlotSequence "
            "WHERE multifoglio=? ORDER BY riga", (multifoglio,)).fetchall()
        out = []
        for r in seq:
            n = con.execute(
                "SELECT COUNT(*) FROM Elementi WHERE Dwg=? AND Foglio=?",
                (multifoglio, r["foglio"])).fetchone()[0]
            out.append({"foglio": r["foglio"], "descrizione": r["descrizione"],
                        "elementi": n})
        return out
    finally:
        con.close()


def _table_cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def scompatta_db(db_path: str, multifoglio: str, fogli: list[str] | None,
                 out_dir: str) -> dict:
    """Scompatta: estrae i dati per-foglio in JSON. Originale mai toccato."""
    os.makedirs(out_dir, exist_ok=True)
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        avail = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if fogli is None:
            fogli = [r[0] for r in con.execute(
                "SELECT foglio FROM PlotSequence WHERE multifoglio=? ORDER BY riga",
                (multifoglio,))]
        manifest = {"multifoglio": multifoglio, "db": os.path.basename(db_path),
                    "fogli": []}
        for fg in fogli:
            pack: dict = {"foglio": fg, "tabelle": {}}
            for t in TABLES_DWG_FOGLIO:
                if t not in avail:
                    continue
                cols = _table_cols(con, t)
                dwgc = "Dwg" if "Dwg" in cols else ("DWG" if "DWG" in cols else None)
                fogc = ("Foglio" if "Foglio" in cols
                        else ("FOGLIO" if "FOGLIO" in cols else None))
                if not dwgc or not fogc:
                    continue
                rows = con.execute(
                    f'SELECT * FROM "{t}" WHERE "{dwgc}"=? AND "{fogc}"=?',
                    (multifoglio, fg)).fetchall()
                pack["tabelle"][t] = [dict(r) for r in rows]
            # riga di sequenza di stampa del foglio
            seq = con.execute(
                "SELECT * FROM PlotSequence WHERE multifoglio=? AND foglio=?",
                (multifoglio, fg)).fetchall()
            pack["plot_sequence"] = [dict(r) for r in seq]
            fp = os.path.join(out_dir, f"foglio_{fg}.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(pack, f, ensure_ascii=False, indent=1, default=str)
            manifest["fogli"].append({"foglio": fg, "file": os.path.basename(fp),
                                      "righe": sum(len(v) for v in pack["tabelle"].values())})
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        return manifest
    finally:
        con.close()


def ricompatta_db(db_template: str, pack_dir: str, out_db: str,
                  multifoglio: str) -> dict:
    """Ricompatta: fonde i JSON per-foglio in un nuovo DB copia del template.

    Il template fornisce lo schema; le righe dei fogli nel manifest vengono
    sostituite con quelle dei package. Il DB originale non viene mai toccato.
    """
    import shutil
    with open(os.path.join(pack_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    shutil.copy2(db_template, out_db)
    con = sqlite3.connect(out_db)
    try:
        stats = {"multifoglio": multifoglio, "fogli": [], "out_db": out_db}
        for entry in manifest["fogli"]:
            fg = entry["foglio"]
            with open(os.path.join(pack_dir, entry["file"]), encoding="utf-8") as f:
                pack = json.load(f)
            for t, rows in pack["tabelle"].items():
                cols = _table_cols(con, t)
                dwgc = "Dwg" if "Dwg" in cols else ("DWG" if "DWG" in cols else None)
                fogc = ("Foglio" if "Foglio" in cols
                        else ("FOGLIO" if "FOGLIO" in cols else None))
                if not dwgc or not fogc:
                    continue
                con.execute(f'DELETE FROM "{t}" WHERE "{dwgc}"=? AND "{fogc}"=?',
                            (multifoglio, fg))
                for r in rows:
                    r.pop("rowid", None)
                    keys = [k for k in r.keys() if k in cols]
                    ph = ",".join("?" for _ in keys)
                    cn = ",".join(f'"{k}"' for k in keys)
                    con.execute(f'INSERT INTO "{t}" ({cn}) VALUES ({ph})',
                                [r[k] for k in keys])
            stats["fogli"].append({"foglio": fg,
                                   "righe": sum(len(v) for v in pack["tabelle"].values())})
        con.commit()
        return stats
    finally:
        con.close()
