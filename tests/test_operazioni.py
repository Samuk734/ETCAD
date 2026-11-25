"""Test di etcad.operazioni su disegni generati al volo (DXF: non serve un convertitore DWG)."""
import os

import ezdxf
import pytest

from etcad import operazioni as op
from etcad import schede as demo


def _set_due_fogli(percorso):
    d = demo.Disegno()
    for k, (cod, lung) in enumerate((("CAV-A", 1000), ("CAV-B", 3000))):
        d.ox = k * 440
        demo.scheda_cavo(d, dict(demo.DEMO, codice=cod, lunghezza=lung, foglio=str(k + 1), di="2"), blocco=True)
    d.doc.saveas(percorso)
    return percorso


def test_cerca_con_foglio(tmp_path):
    f = _set_due_fogli(str(tmp_path / "set.dxf"))
    assert op.cerca(str(tmp_path), "-X2")["risultati"] == 4      # sigla e sottotitolo del cartiglio, 2 fogli
    r = op.cerca(str(tmp_path), "^-X2$", regex=True)
    assert r["file_esaminati"] == 1 and r["risultati"] == 2
    assert sorted(t["foglio"] for t in r["trovati"]) == ["1", "2"]
    r = op.cerca(f, r"CAV-[AB]", regex=True)                    # attributi del cartiglio
    assert {t["testo"] for t in r["trovati"]} == {"CAV-A", "CAV-B"} and all(t["dove"] == "attributo" for t in r["trovati"])
    assert op.cerca(f, "%%c5,2")["risultati"] == op.cerca(f, "Ø5,2")["risultati"] > 0


def test_rinumera_stesso_numero_ai_due_capi():
    doc = ezdxf.new()
    msp = doc.modelspace()
    for s, x, y in (("1001", 10, 100), ("1002", 50, 100), ("1001", 200, 100), ("1003", 10, 50), ("ABC", 0, 0)):
        msp.add_text(s, height=2.5, dxfattribs={"insert": (x, y)})
    cambi = op.rinumera_doc(doc, r"\d{4}", inizio=2001, applica=True)
    assert [t.dxf.text for t in msp.query("TEXT")] == ["2001", "2002", "2001", "2003", "ABC"]
    assert len(cambi) == 4
    msp.add_text("W07", height=2.5, dxfattribs={"insert": (0, 10)})
    op.rinumera_doc(doc, r"W\d+", inizio=1, applica=True)
    assert msp.query("TEXT")[-1].dxf.text == "W01"                # zeri iniziali mantenuti


def test_unisci_affiancati_e_a_layer(tmp_path):
    file = []
    for k in (1, 2):
        doc = ezdxf.new()
        b = doc.blocks.new("CORNICE")
        b.add_line((0, 0), (100 * k, 0))
        doc.modelspace().add_blockref("CORNICE", (0, 0))
        doc.modelspace().add_text(f"FOGLIO {k}", height=2.5, dxfattribs={"insert": (5, 5)})
        p = str(tmp_path / f"f{k}.dxf")
        doc.saveas(p)
        file.append(p)
    r = op.unisci(file, str(tmp_path / "tutti.dxf"))
    out = ezdxf.readfile(r["file"])
    assert {"CORNICE", "CORNICE_2"} <= {b.name for b in out.blocks}
    ins = sorted(out.modelspace().query("INSERT"), key=lambda e: e.dxf.insert.x)
    assert [e.dxf.name for e in ins] == ["CORNICE", "CORNICE_2"] and ins[1].dxf.insert.x == pytest.approx(120)
    r = op.unisci(file, str(tmp_path / "multi.dxf"), modo="layer")
    out = ezdxf.readfile(r["file"])
    assert {e.dxf.layer for e in out.modelspace()} == {"1", "2"}


def test_pulisci():
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_line((0, 0), (10, 0))                                 # doppione
    msp.add_line((5, 5), (5, 5))                                  # lunghezza zero
    msp.add_text("  ", dxfattribs={"insert": (0, 0)})              # testo vuoto
    doc.blocks.new("MAI_USATO").add_line((0, 0), (1, 1))
    doc.layers.add("VUOTO")
    rep = op.pulisci_doc(doc, applica=True)
    assert (len(rep["doppioni"]), len(rep["lunghezza_zero"]), len(rep["testi_vuoti"])) == (1, 1, 1)
    assert "MAI_USATO" in rep["blocchi"] and "VUOTO" in rep["layer"]
    assert len(msp) == 1 and "MAI_USATO" not in doc.blocks and "VUOTO" not in doc.layers


def test_leggi_tabella(tmp_path):
    d = demo.Disegno()
    demo.tabella(d, 20, 100, [16, 20, 46, 18], [("PIN", "COLORE", "FUNZIONE", "MORS."), ("1", "BN", "+24 V", "1"),
                                                ("2", "WH", "uscita 2", "2")])
    r = op.leggi_tabella_doc(d.doc, (15, 70, 130, 105))
    assert r["righe"] == [["PIN", "COLORE", "FUNZIONE", "MORS."], ["1", "BN", "+24 V", "1"], ["2", "WH", "uscita 2", "2"]]
    p = str(tmp_path / "t.dxf")
    d.doc.saveas(p)
    pytest.importorskip("openpyxl")
    r = op.leggi_tabella(p, (15, 70, 130, 105), str(tmp_path / "t.xlsx"))
    assert os.path.isfile(r["xlsx"])


def test_crea_scheda_e_pdf_fogli(tmp_path):
    spec = {"titolo": "CAVO PROVA", "codice": "CAV-PROVA", "lunghezza": 1500,
            "fili": [["1", "BN", "+24 V", "11"], ["3", "BU", "0 V", "12"], ["4", "BK", "segnale", "13"]],
            "sinistra": {"tipo": "circolare", "sigla": "-XS9", "descrizione": "M8 femmina 3 poli", "poli": 3,
                         "pin": ["1", "3", "4"]}}
    r = op.crea_scheda(spec, str(tmp_path / "prova.dxf"), anteprima=False)
    doc = ezdxf.readfile(r["file"])
    testi = {e.dxf.text for e in doc.modelspace().query("TEXT")}
    assert {"-XS9", "BK  segnale", "L = 1500 mm %%p10"} <= testi
    assert r.get("controllo_grafico") == 0
    f = _set_due_fogli(str(tmp_path / "set.dxf"))
    r = op.pdf_fogli(f, str(tmp_path / "set.pdf"))
    import pymupdf
    assert r["pagine"] == 2 and len(pymupdf.open(r["pdf"])) == 2


def test_converti_versione(tmp_path):
    from etcad import dwg_io
    if not dwg_io.trova_oda():
        pytest.skip("serve ODA File Converter")
    src = os.path.join(os.path.dirname(__file__), "..", "examples", "cavo_m12", "Cavo sensore M12.dwg")
    if not os.path.isfile(src):
        pytest.skip("esempio non presente")
    r = op.converti_versione(src, "2013", str(tmp_path / "vecchio.dwg"))
    assert r["codice_file"] == "AC1027"
