"""Lettura fogli esistenti: mappa foglio->layer, contenuto, anteprima, split DXF."""
import os

import pytest

from etcad import config, lettura

ESEMPIO = "SPAC Esempio 2025 - IEC"
MULTI = "ELECTRICAL DIAGRAM"


@pytest.fixture()
def commessa():
    try:
        root = config.resolve_commessa(ESEMPIO)
    except FileNotFoundError:
        pytest.skip("commessa esempio non presente")
    dwg = os.path.join(root, MULTI + ".dwg")
    db = os.path.join(root, "dbSPAC.db")
    if not os.path.isfile(dwg):
        pytest.skip("DWG esempio non presente")
    return dwg, db


def test_foglio_layer_da_plotsequence(commessa):
    _, db = commessa
    assert lettura.foglio_layer(db, MULTI, "04") == "4"
    assert lettura.foglio_layer(db, MULTI, "01") == "1"
    with pytest.raises(ValueError):
        lettura.foglio_layer(db, MULTI, "ZZ")


def test_leggi_foglio_struttura(commessa):
    dwg, db = commessa
    res = lettura.leggi_foglio(dwg, db, MULTI, "04", limite=60)
    assert res["foglio"] == "04" and res["layer"] == "4"
    assert res["entita_totali"] > 50
    assert res["simboli"], "foglio 04 deve contenere simboli"
    assert any("TITOLO" in s["blocco"] for s in res["simboli"])


def test_anteprima_png(commessa, tmp_path):
    dwg, db = commessa
    png = str(tmp_path / "foglio04.png")
    res = lettura.anteprima_foglio(dwg, db, MULTI, "04", png_out=png, dpi=80)
    assert os.path.isfile(res["png"]) and res["bytes"] > 10000


def test_foglio_solo_base_vs_rev(commessa):
    dwg, db = commessa
    base = lettura.leggi_foglio(dwg, db, MULTI, "13", limite=50)
    assert set(base["per_layer"]) <= {"13", "0"}
    assert "13_REV_00" not in base["per_layer"]
    full = lettura.leggi_foglio(dwg, db, MULTI, "13", limite=50,
                                includi_rev=True)
    assert "13_REV_00" in full["per_layer"]
    assert full["entita_totali"] > base["entita_totali"]


def test_scompatta_dxf_un_foglio(commessa, tmp_path):
    dwg, db = commessa
    res = lettura.scompatta_dxf(dwg, db, MULTI, ["04"], str(tmp_path))
    fp = res["fogli"][0]["file"]
    assert os.path.isfile(fp)
    import ezdxf
    doc = ezdxf.readfile(fp)
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert "4" in layers
    altri_fogli = {str(i) for i in range(1, 51)} - {"4"}
    assert not (layers & altri_fogli), "layer di altri fogli nel DXF"
