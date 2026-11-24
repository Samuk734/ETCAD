"""PDF -> DWG su un PDF sintetico (nessun SPAC richiesto).

Il PDF imita un plot SPAC/AutoCAD: cornice comune a tutte le pagine,
testi SHX "esplosi" in tratti (disegnati con i glifi Hershey come fa il
plotter con romans.shx), cerchi a 4 Bezier, pieni, tratteggi a segmenti.
"""
import os

import pytest

pymupdf = pytest.importorskip("pymupdf")
ezdxf = pytest.importorskip("ezdxf")

from etcad import dwg_io, hershey, pdf2dwg, pdf_testi, render  # noqa: E402

K = 72 / 25.4           # mm -> pt
W, H = 297.0, 210.0     # A4 orizzontale in mm


def _pt(x, y):
    return pymupdf.Point(x * K, (H - y) * K)


def _testo_tratti(page, testo, x, y, h):
    """Testo come tratti di penna, un path per tratto (come pdfplot)."""
    for pts in hershey.polilinee(testo, x, y, h):
        page.draw_polyline([_pt(*p) for p in pts], color=(0, 0, 0), width=0.4)


def _pagina(doc, n_foglio: str, con_testi: bool = True):
    page = doc.new_page(width=W * K, height=H * K)
    dx = 7.0 * int(n_foglio)  # contenuto diverso da pagina a pagina (non cornice)
    # cornice: bordo + 24 tacche + etichetta (identici su ogni pagina)
    page.draw_rect(pymupdf.Rect(_pt(5, 205), _pt(292, 5)), color=(0, 0, 0), width=0.7)
    for i in range(24):
        x = 5 + i * 12
        page.draw_line(_pt(x, 200), _pt(x, 205), color=(0, 0, 0), width=0.4)
    page.draw_line(_pt(200, 5), _pt(200, 25), color=(0, 0, 0), width=0.7)
    _testo_tratti(page, "FOGLIO", 210, 18, 2.5)
    _testo_tratti(page, n_foglio, 212, 9, 3.5)       # varia: attributo
    # contenuto
    page.draw_line(_pt(20 + dx, 100), _pt(120 + dx, 100), color=(0, 0, 0), width=0.7)
    page.draw_circle(_pt(150 + dx, 100), 3 * K, color=(0, 0, 0), width=0.4)
    tri = [_pt(60 + dx, 120), _pt(66 + dx, 120), _pt(63 + dx, 126), _pt(60 + dx, 120)]
    page.draw_polyline(tri, color=(0, 0, 0), fill=(0, 0, 0), width=0)
    for i in range(12):  # tratteggio: segmenti singoli da 3 mm, passo 5 mm
        page.draw_line(_pt(20 + dx + 5 * i, 80), _pt(23 + dx + 5 * i, 80), color=(0, 0, 0),
                       width=0.4)
    page.insert_text(_pt(20 + dx, 60), "PROVA 24VDC", fontsize=10)
    if con_testi:
        for k, t in enumerate(["KM1 24VDC", "MOTORE M1", "CAVO 3G1,5", "X1 TERMINALE",
                               "QF1 16A", "SEZIONATORE Q0", "PULSANTE S1", "LAMPADA H1"]):
            _testo_tratti(page, t, 140 + dx, 180 - 9 * k, 2.5)
    return page


@pytest.fixture(scope="module")
def pdf_sintetico(tmp_path_factory):
    d = tmp_path_factory.mktemp("pdf")
    fp = str(d / "sintetico.pdf")
    doc = pymupdf.open()
    for n in ("1", "2", "3"):
        _pagina(doc, n)
    doc.save(fp)
    return fp


def test_info_pdf(pdf_sintetico):
    info = pdf2dwg.info_pdf(pdf_sintetico)
    assert info["pagine_totali"] == 3
    p = info["pagine"][0]
    assert p["tipo"] == "vettoriale" and abs(p["mm"][0] - W) < 0.5
    assert p["testo_esploso_probabile"] in (True, False)


def test_geometria_e_cornice(pdf_sintetico, tmp_path):
    r = pdf2dwg.converti_pdf(pdf_sintetico, str(tmp_path), usa_ocr=False,
                             solo_dxf=True, combinato=True)
    assert r["cornice"] and r["cornice"]["blocco"] == "CORNICE"
    assert r["cornice"]["entita"] >= 20
    doc = ezdxf.readfile(r["fogli"][0]["dxf"])
    msp = doc.modelspace()
    tipi = [e.dxftype() for e in msp]
    assert "INSERT" in tipi                              # cornice come blocco
    cerchi = [e for e in msp.query("CIRCLE")]
    assert any(abs(c.dxf.radius - 3.0) < 0.05 for c in cerchi)
    assert msp.query("HATCH"), "il triangolo pieno deve diventare HATCH"
    tratt = [e for e in msp.query("LINE") if e.dxf.linetype.startswith("TRATT_")]
    assert tratt, "i segmenti a passo regolare devono diventare una linea tratteggiata"
    L = tratt[0].dxf.end.x - tratt[0].dxf.start.x
    assert abs(abs(L) - 58.0) < 0.2
    testi = [e.dxf.text for e in msp.query("TEXT")]
    assert "PROVA 24VDC" in testi                        # testo vero del PDF
    assert os.path.isfile(r["rapporto_json"])
    assert any(f.endswith("_tutti_i_fogli.dxf") for f in r["dxf"])


@pytest.mark.skipif(pdf_testi.motore_ocr() is None, reason="OCR (rapidocr) non installato")
def test_testi_esplosi_ricostruiti(pdf_sintetico, tmp_path):
    r = pdf2dwg.converti_pdf(pdf_sintetico, str(tmp_path), usa_ocr=True, solo_dxf=True,
                             combinato=False)
    doc = ezdxf.readfile(r["fogli"][1]["dxf"])
    testi = {e.dxf.text: e for e in doc.modelspace().query("TEXT")}
    for atteso in ("KM1 24VDC", "MOTORE M1", "QF1 16A", "PULSANTE S1"):
        assert atteso in testi, (atteso, sorted(testi))
    t = testi["KM1 24VDC"]
    assert abs(t.dxf.height - 2.5) < 0.05 and t.dxf.style == "ROMANS"
    assert abs(t.dxf.insert.y - 180.0) < 0.1
    # il numero di foglio varia da pagina a pagina: attributo della cornice
    assert "FOGLIO" in r["cornice"]["attributi"]
    ins = [e for e in doc.modelspace().query("INSERT") if e.dxf.name == "CORNICE"][0]
    assert {a.dxf.tag: a.dxf.text for a in ins.attribs}.get("FOGLIO") == "2"


@pytest.mark.skipif(pdf_testi.motore_ocr() is None, reason="OCR (rapidocr) non installato")
def test_correzioni_ai(pdf_sintetico, tmp_path):
    r = pdf2dwg.converti_pdf(
        pdf_sintetico, str(tmp_path), pagine="1", usa_ocr=True, solo_dxf=True,
        combinato=False, cornice=False,
        sostituzioni=[{"pagina": 1, "cerca": "MOTORE", "testo": "MOTOR"}],
        testi_aggiunti=[{"pagina": 1, "testo": "AGGIUNTO", "x": 30, "y": 150,
                         "altezza": 3.5}])
    doc = ezdxf.readfile(r["fogli"][0]["dxf"])
    testi = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert "MOTOR M1" in testi and "AGGIUNTO" in testi


def test_confronto_render(pdf_sintetico, tmp_path):
    r = pdf2dwg.converti_pdf(pdf_sintetico, str(tmp_path), pagine="1", usa_ocr=False,
                             solo_dxf=True, combinato=False, cornice=False)
    c = render.confronta(pdf_sintetico, 1, r["fogli"][0]["dxf"], zona=(0, 40, 160, 130),
                         dpi=150)
    assert c["somiglianza"] > 0.9
    assert c["overlay"].size[0] > 100


@pytest.mark.skipif(not any(dwg_io.backend_disponibili().values()),
                    reason="nessun convertitore DWG (SPAC o ODA)")
def test_dwg_nativo(pdf_sintetico, tmp_path):
    r = pdf2dwg.converti_pdf(pdf_sintetico, str(tmp_path), pagine="1-2", usa_ocr=False)
    assert r["conversione"]["ok"], r["conversione"]
    dwg = r["fogli"][0]["dwg"]
    with open(dwg, "rb") as f:
        assert f.read(6) == b"AC1032"                    # DWG 2018
    back = str(tmp_path / "ritorno.dxf")
    dwg_io.dwg_a_dxf(dwg, back)
    doc = ezdxf.readfile(back)
    assert "CORNICE" in doc.blocks
