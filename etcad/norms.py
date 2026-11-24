"""Controlli dati ispirati alle norme degli impianti elettrici.

Fonti: CEI EN 60204-1 (sicurezza macchine, equipaggiamento elettrico),
IEC 81346 (sigle di riferimento), CEI EN 61439-1/2 (quadri),
CEI EN 61082 (documentazione).

Sono controlli di coerenza sui DATI di commessa, non certificazione.
Ogni controllo ritorna: esito pass/warn/fail, dettaglio, riferimenti riga.
"""
from __future__ import annotations

import re
import sqlite3

SIGLA_RE = re.compile(r"^[+\-=][A-Za-z0-9_.:/+-]+$")


def _ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def check_sigle(db_path: str) -> dict:
    """IEC 81346: una sigla = un solo apparecchio per quadro.

    Lo stesso apparecchio compare su piu' fogli (poli/contatti): e' normale.
    Conflitto vero solo se la stessa sigla ha Costruttore/Tipo diversi.
    """
    con = _ro(db_path)
    try:
        rows = con.execute(
            "SELECT IDQuadro, Nome, Foglio, Costruttore, Tipo FROM Elementi").fetchall()
    finally:
        con.close()
    malformate, per_sigla = [], {}
    for r in rows:
        sigla = (r["Nome"] or "").strip()
        if not sigla:
            continue
        if not SIGLA_RE.match(sigla):
            malformate.append({"sigla": sigla, "foglio": r["Foglio"]})
        ident = ((r["Costruttore"] or "").strip(), (r["Tipo"] or "").strip())
        per_sigla.setdefault((r["IDQuadro"], sigla), set()).add(ident)
    conflitti = [{"sigla": s, "quadro": q, "varianti": sorted(v - {("", "")})}
                 for (q, s), v in per_sigla.items()
                 if len(v - {("", "")}) > 1]
    esito = "fail" if conflitti else ("warn" if malformate else "pass")
    return {"controllo": "sigle_iec81346", "esito": esito,
            "sigle_totali": len(per_sigla), "conflitti": conflitti[:50],
            "non_standard": malformate[:50],
            "nota": "non_standard = nomi descrittivi ammessi da SPAC, verificare vs IEC 81346"}


def check_num_fili(db_path: str) -> dict:
    """CEI EN 60204-1: stesso numero = stesso filo sullo stesso foglio.

    Lo stesso filo continua su piu' fogli (rimandi): e' normale.
    Conflitto vero: stesso NUMEROFILO per fili diversi sullo STESSO foglio.
    """
    con = _ro(db_path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "SegnaliniDatiCavo" not in tables:
            return {"controllo": "numerazione_fili", "esito": "warn",
                    "dettaglio": "tabella SegnaliniDatiCavo assente"}
        tot = con.execute("SELECT COUNT(*) FROM SegnaliniDatiCavo").fetchone()[0]
        conflitti = con.execute(
            "SELECT DWG, FOGLIO, NUMEROFILO, "
            "COUNT(DISTINCT NOMECAVO||'|'||NOMEVENA) AS fili "
            "FROM SegnaliniDatiCavo WHERE NUMEROFILO<>'' "
            "GROUP BY DWG, FOGLIO, NUMEROFILO HAVING fili>1 LIMIT 50").fetchall()
        cols = {r[1].upper() for r in con.execute("PRAGMA table_info(SegnaliniDatiCavo)")}
        # i punti di giunzione (blocco PUNTO) non hanno mai numero: non sono segnalini anonimi
        no_punti = " AND COALESCE(NOMEBLOCCO,'')<>'PUNTO'" if "NOMEBLOCCO" in cols else ""
        anonimi = con.execute(
            "SELECT COUNT(*) FROM SegnaliniDatiCavo "
            "WHERE (NUMEROFILO IS NULL OR NUMEROFILO='') "
            "AND (NOMEVENA IS NULL OR NOMEVENA='')" + no_punti).fetchone()[0]
    finally:
        con.close()
    esito = "fail" if conflitti else ("warn" if anonimi else "pass")
    return {"controllo": "numerazione_fili", "esito": esito,
            "segnalini_totali": tot, "senza_identificativo": anonimi,
            "conflitti_stesso_foglio": [dict(r) for r in conflitti]}


def check_cavi(db_path: str) -> dict:
    """CEI EN 60204-1 §12/§13: cavi con formazione/sezione e vene coerenti."""
    con = _ro(db_path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        cavi = con.execute("SELECT NomeCavo, Formazione FROM Cavi").fetchall() \
            if "Cavi" in tables else []
        vene = {}
        if "Vene" in tables:
            for r in con.execute("SELECT NomeCavo, COUNT(*) c FROM Vene GROUP BY NomeCavo"):
                vene[r[0]] = r[1]
    finally:
        con.close()
    problemi = []
    for c in cavi:
        form = (c["Formazione"] or "")
        m = re.match(r"\s*(\d+)\s*[xX]", form)
        if m and c["NomeCavo"] in vene and vene[c["NomeCavo"]] != int(m.group(1)):
            problemi.append({"cavo": c["NomeCavo"], "formazione": form,
                             "vene_db": vene[c["NomeCavo"]]})
        if not form:
            problemi.append({"cavo": c["NomeCavo"], "formazione": "",
                             "nota": "formazione mancante"})
    esito = "warn" if problemi else "pass"
    return {"controllo": "cavi_formazione", "esito": esito,
            "cavi_totali": len(cavi), "problemi": problemi[:50]}


def check_quadri_distinta(db_path: str) -> dict:
    """CEI EN 61439: ogni quadro in Struttura_Quadri ha voci in distinta."""
    con = _ro(db_path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"Struttura_Quadri", "Distinta"} <= tables:
            return {"controllo": "quadri_distinta", "esito": "warn",
                    "dettaglio": "tabelle Struttura_Quadri/Distinta assenti"}
        quadri = con.execute("SELECT IDQuadro, Quadro FROM Struttura_Quadri").fetchall()
        coperti = {r[0] for r in con.execute("SELECT DISTINCT IDQuadro FROM Distinta")}
    finally:
        con.close()
    vuoti = [{"quadro": q["Quadro"], "id": q["IDQuadro"]} for q in quadri
             if q["IDQuadro"] not in coperti]
    return {"controllo": "quadri_distinta", "esito": "warn" if vuoti else "pass",
            "quadri_totali": len(quadri), "senza_distinta": vuoti}


def run_all(db_path: str) -> dict:
    checks = []
    for fn in (check_sigle, check_num_fili, check_cavi, check_quadri_distinta):
        try:
            checks.append(fn(db_path))
        except sqlite3.Error as e:  # schema diverso da quello atteso
            checks.append({"controllo": fn.__name__, "esito": "warn",
                           "dettaglio": f"non eseguibile: {e}"})
    rank = {"pass": 0, "warn": 1, "fail": 2}
    esito = max(checks, key=lambda c: rank[c["esito"]])["esito"]
    return {"esito": esito, "controlli": checks,
            "nota": "Controlli di coerenza dati, non certificazione di conformita'."}
