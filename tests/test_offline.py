"""Test senza SPAC: script accoreconsole, modifiche batch, riconoscimento,
registro tool del server."""
import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

from etcad import batch, hershey, modifica, pdf_testi, progetta  # noqa: E402


def test_script_senza_righe_vuote_finali():
    # una riga vuota finale ripeteva DXFOUT e appendeva la console
    s = batch.script_dxf_out("C:/tmp/x.dxf")
    assert not s.endswith("\n\n") and "DXFOUT" in s and s.rstrip().endswith("QUIT")
    s = batch.script_saveas_dwg("C:/tmp/x.dwg")
    assert "SAVEAS" in s and "2018" in s and '"C:/tmp/x.dwg"' in s
    assert "FILEDIA" not in batch.PREAMBOLO     # variabile di registro: mai toccarla
    p = batch.script_plot_pdf("C:/tmp/x.pdf", 297, 210)
    assert "DWG To PDF.pc3" in p and "1:1" in p and "A4" in p


def test_decodifica_utf16():
    testo = "Comando: _.DXFOUT àè"
    assert batch._decodifica(testo.encode("utf-16-le")) == testo
    assert batch._decodifica("ok".encode("cp1252")) == "ok"


def test_modifica_batch():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    t = msp.add_text("VECCHIO", height=2.5)
    t.set_placement((10, 10))
    blk = doc.blocks.new("SIMB")
    blk.add_attdef("N", (0, 0), text="?", height=2)
    ins = msp.add_blockref("SIMB", (50, 50))
    ins.add_auto_attribs({"N": "1"})
    rep = modifica.applica(doc, [
        {"op": "testo", "handle": t.dxf.handle, "testo": "NUOVO"},
        {"op": "testo", "handle": ins.dxf.handle, "tag": "N", "testo": "X9"},
        {"op": "sposta", "handle": t.dxf.handle, "dx": 5, "dy": 0},
        {"op": "aggiungi_linea", "p0": [0, 0], "p1": [10, 0], "spessore": 0.5},
        {"op": "aggiungi_testo", "testo": "-KM1", "x": 1, "y": 2, "stile": "ISOCP"},
        {"op": "aggiungi_blocco", "nome": "SIMB", "x": 70, "y": 70, "attributi": {"N": "5"}},
        {"op": "layer", "nome": "NUOVO", "crea": True, "colore": 3},
    ])
    assert rep[0]["vecchio"] == "VECCHIO" and t.dxf.text == "NUOVO"
    assert [a.dxf.text for a in ins.attribs] == ["X9"]
    assert abs(t.dxf.insert.x - 15) < 1e-6
    testi = [e.dxf.text for e in msp.query("TEXT")]
    assert "-KM1" in testi and "ISOCP" in doc.styles
    assert len(msp.query("INSERT")) == 2
    h = rep[3]["handle"]
    modifica.applica(doc, [{"op": "elimina", "handle": h}])
    assert doc.entitydb.get(h) is None or not doc.entitydb.get(h).is_alive
    with pytest.raises(ValueError):
        modifica.applica(doc, [{"op": "testo", "handle": "FFFFFF", "testo": "x"}])


def test_hershey_larghezze_apprese():
    # spazio e trattino basso misurati sui PDF SPAC (piu' larghi di Hershey)
    assert hershey.avanzamento(" ") > 18
    assert abs(hershey.larghezza("E", 21.0) - hershey.avanzamento("E")) < 1e-9
    pl = hershey.polilinee("A", 0, 0, 2.5)
    ys = [p[1] for tr in pl for p in tr]
    assert abs(max(ys) - 2.5) < 0.01 and abs(min(ys)) < 0.01   # altezza maiuscola
    ruotato = hershey.polilinee("A", 0, 0, 2.5, rot=90)
    xs = [p[0] for tr in ruotato for p in tr]
    assert min(xs) < -2.4                                       # ruotato di 90 gradi


def test_firme_glifi_distinguono_forme():
    def glifo(c):
        pls = [[pts] for pts in hershey.polilinee(c, 0, 0, 3.0)]
        g = pdf_testi.segmenta_glifi(pls)[0]
        g.tratti = list(range(len(pls)))
        g.x0 = min(p[0] for pl in pls for pts in pl for p in pts)
        g.x1 = max(p[0] for pl in pls for pts in pl for p in pts)
        g.y0 = min(p[1] for pl in pls for pts in pl for p in pts)
        g.y1 = max(p[1] for pl in pls for pts in pl for p in pts)
        return pdf_testi.firma_glifo(g, pls)
    assert pdf_testi.distanza_firme(glifo("R"), glifo("R")) < 0.01
    assert pdf_testi.distanza_firme(glifo("P"), glifo("R")) > 0.06
    assert pdf_testi.distanza_firme(glifo("C"), glifo("G")) > 0.06


def test_allineamento_ocr_virgolette():
    g = pdf_testi.GruppoTesto(idx=[], penna=0.1, colore=None, bbox=(0, 0, 1, 1))
    g.glifi = [pdf_testi.Glifo([i], i, i + 0.5, 0, 1) for i in range(4)]
    g.ocr = 'A"B'   # le virgolette sono due tratti: 4 glifi, 3 caratteri
    assert pdf_testi.allinea_ocr(g) == ["A", '"', "", "B"]


def test_k_temp_conservativo():
    assert progetta._k_temp("PVC", 37) == progetta.K_TEMP["PVC"][40]
    assert progetta._k_temp("PVC", 20) == 1.0
    with pytest.raises(ValueError):
        progetta._k_temp("PVC", 70)


def test_server_tool():
    import etcad.server as srv
    tools = srv.mcp._tool_manager._tools
    assert len(tools) >= 40
    for t in ("pdf_a_dwg", "pdf_confronta", "pdf_correggi", "pdf_testi", "pdf_glifi",
              "dwg_modifica", "dwg_entita", "dwg_anteprima", "spac_diagnostica",
              "multifoglio_ricompatta", "foglio_anteprima", "live_lisp"):
        assert t in tools


def test_ricompatta_non_si_rompe(tmp_path):
    """Regressione: il tool usava 'mf' sia come modulo sia come stringa."""
    import inspect
    import etcad.server as srv
    src = inspect.getsource(srv.multifoglio_ricompatta)
    assert "mf.ricompatta_db" in src and "mf = " not in src


def test_angoli_bulge_arco():
    from etcad import pdf2dwg
    # quarto di cerchio r=10 da (10,0) a (0,10) come Bezier
    k = 0.5522847498 * 10
    run = [("c", (10, 0), (10, k), (k, 10), (0, 10))]
    b = pdf2dwg._bulge_run(run)
    assert abs(b - math.tan(math.radians(90) / 4)) < 1e-3
