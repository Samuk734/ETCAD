"""Roundtrip scompatta/ricompatta su copia del DB esempio (non tocca l'originale)."""
import os
import shutil
import sqlite3
import tempfile

import pytest

from etcad import config, dbtools, multifoglio

ESEMPIO = "SPAC Esempio 2025 - IEC"
MULTI = "ELECTRICAL DIAGRAM"


@pytest.fixture()
def db_copy():
    try:
        root = config.resolve_commessa(ESEMPIO)
    except FileNotFoundError:
        pytest.skip("commessa esempio non presente")
    src = os.path.join(root, "dbSPAC.db")
    tmp = tempfile.mkdtemp(prefix="spac_test_")
    dst = os.path.join(tmp, "dbSPAC.db")
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp, ignore_errors=True)


def test_scompatta_ricompatta_roundtrip(db_copy):
    pack = os.path.join(os.path.dirname(db_copy), "pack")
    man = multifoglio.scompatta_db(db_copy, MULTI, None, pack)
    assert len(man["fogli"]) > 0
    out_db = os.path.join(os.path.dirname(db_copy), "ricompattato.db")
    multifoglio.ricompatta_db(db_copy, pack, out_db, MULTI)
    c1 = sqlite3.connect(db_copy)
    c2 = sqlite3.connect(out_db)
    for t in ("Elementi", "MorsConn", "Rimandi"):
        q1 = c1.execute(f'SELECT COUNT(*) FROM "{t}" WHERE Dwg=?', (MULTI,)).fetchone()[0]
        q2 = c2.execute(f'SELECT COUNT(*) FROM "{t}" WHERE Dwg=?', (MULTI,)).fetchone()[0]
        assert q1 == q2, t


def test_db_query_solo_lettura(db_copy):
    with pytest.raises(ValueError):
        dbtools.query(db_copy, "DELETE FROM Elementi")
    res = dbtools.query(db_copy, "SELECT COUNT(*) AS n FROM Elementi")
    assert res["rows"][0]["n"] > 0
