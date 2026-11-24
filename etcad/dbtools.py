"""Accesso ai database SQLite di commessa (dbSPAC.db, MCables-Data.db)."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile

ALLOW = re.compile(r"^\s*(SELECT|WITH|PRAGMA\s+table_info|EXPLAIN)\b", re.IGNORECASE)


def _connect_ro(path: str) -> sqlite3.Connection:
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def list_tables(db_path: str) -> list[dict]:
    con = _connect_ro(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        out = []
        for r in rows:
            n = con.execute(f'SELECT COUNT(*) FROM "{r["name"]}"').fetchone()[0]
            out.append({"table": r["name"], "rows": n})
        return out
    finally:
        con.close()


def table_schema(db_path: str, table: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_]+", table):
        raise ValueError(f"Nome tabella non valido: {table}")
    con = _connect_ro(db_path)
    try:
        cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])}
                for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]
        if not cols:
            raise ValueError(f"Tabella assente: {table}")
        n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return {"table": table, "rows": n, "columns": cols}
    finally:
        con.close()


def query(db_path: str, sql: str, limit: int = 200) -> dict:
    """Solo lettura. Permette SELECT/WITH/EXPLAIN; BLOCCA INSERT/UPDATE/DELETE."""
    if not ALLOW.match(sql):
        raise ValueError("Solo query di lettura (SELECT/WITH) consentite.")
    if ";" in sql.strip().rstrip(";"):
        raise ValueError("Una sola istruzione per query.")
    limit = max(1, min(int(limit), 2000))
    con = _connect_ro(db_path)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(r) for r in cur.fetchmany(limit + 1)]
        truncated = len(rows) > limit
        return {"columns": cols, "rows": rows[:limit], "truncated": truncated}
    finally:
        con.close()


def work_copy(db_path: str) -> str:
    """Copia di lavoro scrivibile in temp. L'originale non viene mai toccato."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    fd, tmp = tempfile.mkstemp(prefix="spac_", suffix=".db")
    os.close(fd)
    shutil.copy2(db_path, tmp)
    return tmp
