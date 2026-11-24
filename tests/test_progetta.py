"""Progettazione: calcoli cavi, colori norma, disegno fogli, distinta Excel."""
import json
import os

import pytest

from etcad import progetta

SPEC = {"titolo": "Quadro QG - test",
        "riga2": "400V 3F+N+PE",
        "partenze": [{"nome": "M1", "potenza_kw": 5.5, "cosphi": 0.85,
                      "lunghezza_m": 30, "cavo": "-W1"}]}


def test_calcola_cavo_motore():
    r = progetta.calcola_cavo(5.5, 30)
    assert r["ib_A"] == pytest.approx(9.34, abs=0.05)
    assert r["sezione_mm2"] == 1.5
    assert r["iz_A"] >= r["ib_A"]
    assert r["ib_A"] <= r["protezione_A"] <= r["iz_A"]
    assert r["dv_perc"] < 4.0
    assert r["formazione"] == "5x1.5"


def test_calcola_cavo_lunga_distanza_aumenta_sezione():
    vicino = progetta.calcola_cavo(15, 10)
    lontano = progetta.calcola_cavo(15, 150)
    assert lontano["sezione_mm2"] > vicino["sezione_mm2"]
    assert lontano["dv_perc"] <= 4.0


def test_calcola_cavo_impossibile():
    with pytest.raises(ValueError):
        progetta.calcola_cavo(500, 500)


def test_colori_filo():
    assert progetta.colori_filo("PE")["colore"] == "giallo/verde"
    assert progetta.colori_filo("neutro")["colore"] == "blu chiaro"
    assert progetta.colori_filo("potenza")["colore"] == "nero"
    with pytest.raises(ValueError):
        progetta.colori_filo("fantasma")


def test_progetta_foglio(tmp_path):
    dxf = str(tmp_path / "foglio.dxf")
    png = str(tmp_path / "foglio.png")
    res = progetta.progetta_foglio(SPEC, dxf, png)
    assert os.path.isfile(dxf) and os.path.isfile(png)
    assert res["partenze"] == 1 and len(res["cavi"]) == 1
    assert len(res["componenti"]) == 3
    import ezdxf
    doc = ezdxf.readfile(dxf)
    assert len(doc.modelspace()) > 30
    assert "FILI_PE" in doc.layers


def test_distinta_excel(tmp_path):
    res = progetta.progetta_foglio(SPEC, str(tmp_path / "f.dxf"))
    xlsx = progetta.distinta_excel(res["componenti"], res["cavi"],
                                   str(tmp_path / "distinta.xlsx"))
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    assert wb["Componenti"].max_row == 4  # header + 3
    assert wb["Cavi"].max_row == 2
    assert wb["Cavi"]["B2"].value == res["cavi"][0]["formazione"]


def test_server_tool_storici():
    import etcad.server as srv
    tools = srv.mcp._tool_manager._tools
    assert len(tools) >= 25
    for t in ("dwg_modifica_testi", "dwg_elimina", "dwg_layer",
              "dwg_collegamenti", "db_coerenza"):
        assert t in tools
