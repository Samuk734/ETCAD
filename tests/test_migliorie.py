"""Migliorie: lock check, collegamenti dati, coerenza DB, layer/elimina."""
import os
import shutil
import sqlite3

import pytest

from etcad import config, lettura

ESEMPIO = "SPAC Esempio 2025 - IEC"
MULTI = "ELECTRICAL DIAGRAM"


def test_verifica_scrivibile(tmp_path):
    with pytest.raises(FileNotFoundError):
        lettura.verifica_scrivibile(str(tmp_path / "nope.dwg"))
    f = tmp_path / "ok.dwg"
    f.write_bytes(b"x")
    lettura.verifica_scrivibile(str(f))  # non solleva


def test_coerenza_trova_riga_fake():
    try:
        root = config.resolve_commessa(ESEMPIO)
    except FileNotFoundError:
        pytest.skip("commessa esempio non presente")
    tmp = os.path.join(os.path.dirname(root), "__coerenza_test__")
    os.makedirs(tmp, exist_ok=True)
    try:
        db = os.path.join(tmp, "dbSPAC.db")
        shutil.copy2(os.path.join(root, "dbSPAC.db"), db)
        c = sqlite3.connect(db)
        c.execute("INSERT INTO Elementi (Dwg, Foglio, Handle, Nome) VALUES "
                  "(?, '01', 'DEADBEEF', '-TEST')", (MULTI,))
        c.commit()
        c.close()
        dwg = os.path.join(root, MULTI + ".dwg")
        res = lettura.coerenza_db(db, dwg, MULTI)
        assert "DEADBEEF" in res["dangling"]["Elementi"]["handle"]
        assert res["tabelle_controllate"] > 5
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
