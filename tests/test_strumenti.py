"""Strumenti di lavoro sui disegni (etcad.strumenti), su DXF sintetici: girano ovunque."""
import os

import ezdxf
import pytest

from etcad import modifica
from etcad import strumenti as S


def _doc():
    doc = ezdxf.new("R2018")
    doc.styles.add("ROMANS", font="romans.shx")
    doc.styles.add("ISOCP", font="isocp.shx")
    doc.layers.add("TESTI")
    blk = doc.blocks.new("CORNICE")
    blk.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    blk.add_text("01 Prima emissione 01/01/25", height=2.2, dxfattribs={"style": "ROMANS", "insert": (10, 21)})
    blk.add_attdef("DATA", (10, 12), dxfattribs={"height": 1.5, "style": "ROMANS"})
    blk.add_attdef("DISEGNATORE", (40, 12), dxfattribs={"height": 1.5, "style": "ROMANS"})
    msp = doc.modelspace()
    ins = msp.add_blockref("CORNICE", (0, 0))
    ins.add_auto_attribs({"DATA": "01/01/25", "DISEGNATORE": "XXX"})
    msp.add_text("Guaina PVC %%c10 L=200 cm", height=2.5, dxfattribs={"style": "ROMANS", "insert": (100, 100),
                                                                        "layer": "TESTI"})
    msp.add_text("Cavo 1.5 L=230 cm", height=2.5, dxfattribs={"style": "ROMANS", "insert": (100, 80), "layer": "TESTI"})
    return doc


def test_confronta_doc():
    a, b = _doc(), _doc()
    assert S.confronta_doc(a, b)["uguali"]                       # handle diversi, contenuto uguale
    next(e for e in b.modelspace().query("TEXT") if "Cavo" in e.dxf.text).dxf.text = "Cavo 1.5 L=232 cm"
    next(iter(b.modelspace().query("INSERT"))).get_attrib("DATA").dxf.text = "01/02/2026"
    b.modelspace().add_line((0, 0), (10, 10))
    r = S.confronta_doc(a, b)
    assert not r["uguali"]
    cambi = {(t.get("tag"), t["prima"], t["dopo"]) for t in r["testi_cambiati"]}
    assert (None, "Cavo 1.5 L=230 cm", "Cavo 1.5 L=232 cm") in cambi and ("DATA", "01/01/25", "01/02/2026") in cambi
    assert [x["tipo"] for x in r["aggiunte"]] == ["LINE"] and r["tolte"] == []


def test_sostituisci_doc():
    doc = _doc()
    r = S.sostituisci_doc(doc, "Ø10", "Ø12")                     # Ø trova anche %%c; nei font SHX resta %%c
    assert [(x["prima"], x["dopo"]) for x in r] == [("Guaina PVC %%c10 L=200 cm", "Guaina PVC %%c12 L=200 cm")]
    r = S.sostituisci_doc(doc, r"L=(\d+) cm", r"L = \1 cm", regex=True, applica=False)
    assert len(r) == 2
    r = S.sostituisci_doc(doc, "230", "232", zona=[0, 70, 200, 90])
    assert len(r) == 1 and r[0]["dopo"] == "Cavo 1.5 L=232 cm"
    r = S.sostituisci_doc(doc, "01/01/25", "01/02/2026")       # attributo e testo dentro il blocco
    assert sorted(x["dove"] for x in r) == ["attributo", "blocco CORNICE"]
    assert S.sostituisci_doc(doc, "prima", "x", maiuscole=True) == []


def test_cartiglio():
    doc = _doc()
    r = S.imposta_attributi(doc, {"DISEGNATO": "A.B.", "data": "01/02/2026"})
    assert {(x["tag"], x["dopo"]) for x in r} == {("DISEGNATORE", "A.B."), ("DATA", "01/02/2026")}
    tolte = S.togli_zona(doc, [5, 18, 120, 26])                # riga delle revisioni, dentro la cornice
    assert [t["dove"] for t in tolte] == ["blocco CORNICE"]
    assert not [e for e in doc.blocks.get("CORNICE") if e.dxftype() == "TEXT"]
    assert S.attributi_doc(doc)[0]["attributi"] == {"DATA": "01/02/2026", "DISEGNATORE": "A.B."}


def _fili():
    doc = ezdxf.new("R2018")
    doc.styles.add("ROMANS", font="romans.shx")
    msp = doc.modelspace()
    msp.add_line((10, 50), (60, 50))                            # filo in due tratti + polilinea
    msp.add_lwpolyline([(60, 50), (80, 50), (80, 30)])
    msp.add_line((80, 30), (120, 30))
    msp.add_line((70, 40), (70, 60))                            # incrocia senza toccare gli estremi
    msp.add_line((100, 30), (100, 10))                          # a T sul tratto finale
    msp.add_text("JP8-3", height=2, dxfattribs={"style": "ROMANS", "insert": (2, 49)})
    msp.add_text("Marrone", height=2, dxfattribs={"style": "ROMANS", "insert": (122, 29)})
    return doc


def test_traccia_fili():
    doc = _fili()
    r = S.traccia_fili(doc, (30, 50.05))
    assert r["trovato"] and r["entita"] == 3 and abs(r["lunghezza_mm"] - 130) < 0.01
    capi = {(c["x"], c["y"]): [t["testo"] for t in c["testi"]] for c in r["capi"]}
    assert capi == {(10.0, 50.0): ["JP8-3"], (120.0, 30.0): ["Marrone"]}
    r = S.traccia_fili(doc, (30, 50), modo="giunzioni")
    assert r["entita"] == 4 and len(r["capi"]) == 3
    assert not S.traccia_fili(doc, (200, 200))["trovato"]


def test_posto_libero_e_larghezza():
    doc = _fili()
    w_r = S.larghezza_testo("Gommino ritenuta", 2.5, "ROMANS")
    w_i = S.larghezza_testo("Gommino ritenuta", 2.5, "ISOCP")
    assert w_r > 20 and abs(w_i / w_r - 0.87) < 1e-6
    r = S.posto_libero(doc, (70, 50), testo="Gommino", altezza=2.5)
    assert r["posti"]
    for p in r["posti"]:
        assert S.libero(doc, p["riquadro"], margine=0.2)["libero"]
    assert not S.libero(doc, [50, 45, 70, 55])["libero"]


def test_etichetta(tmp_path):
    f = str(tmp_path / "e.dxf")
    _fili().saveas(f)
    r = modifica.modifica_dwg(f, [{"op": "aggiungi_etichetta", "testo": "Guaina PVC Ø10", "x": 20, "y": 70,
                                   "altezza": 2.5, "punta": [40, 50]}], f)
    hs = r["dettaglio"][0]["handle"]
    doc = ezdxf.readfile(f)
    assert sorted(doc.entitydb.get(h).dxftype() for h in hs) == ["HATCH", "LINE", "LINE", "TEXT"]


def test_copia_zona(tmp_path):
    a, b = str(tmp_path / "a.dxf"), str(tmp_path / "b.dxf")
    _fili().saveas(a)
    d = ezdxf.new("R2018")
    d.modelspace().add_line((0, 0), (5, 5))
    d.saveas(b)
    r = S.copia_zona(a, [0, 40, 65, 60], b, cornice_src=[0, 0, 100, 100], cornice_dst=[10, 10, 60, 60],
                     layer_dst="16", applica=True)
    assert r["entita"] == 2 and r["copiate"] == 2 and abs(r["scala"] - 0.5) < 1e-9
    doc = ezdxf.readfile(b)
    linee = [e for e in doc.modelspace().query("LINE") if e.dxf.layer == "16"]
    assert len(linee) == 1 and abs(linee[0].dxf.start.x - 15) < 1e-6 and abs(linee[0].dxf.start.y - 35) < 1e-6
    assert S.copia_zona(a, [0, 40, 70, 60], b)["applicato"] is False


def test_consegna(tmp_path):
    src, dst = tmp_path / "out", tmp_path / "Z"
    src.mkdir()
    (src / "a.txt").write_text("uno")
    (src / "b.txt").write_text("due")
    r = S.consegna(str(src), str(dst))
    assert r["da_copiare"] == 2 and not dst.exists()            # solo il piano
    r = S.consegna(str(src), str(dst), applica=True)
    assert r["md5_ok"] and sorted(os.listdir(dst)) == ["a.txt", "b.txt"]
    (src / "b.txt").write_text("tre")
    r = S.consegna(str(src), str(dst))
    assert {f["file"]: f["azione"] for f in r["file"]} == {"a.txt": "uguale", "b.txt": "cambiato"}


def test_distinta(tmp_path):
    pytest.importorskip("openpyxl")
    doc = ezdxf.new("R2018")
    doc.styles.add("ROMANS", font="romans.shx")
    msp = doc.modelspace()
    for i, s in enumerate(["Guaina PVC %%c10 L=200 cm", "Gommino ritenuta VERDE", "Puntalini GIALLI",
                           "Connettore femmina 4 poli", "Etichetta 1/15", "Cavo 1.5 L=300 cm", "nota qualsiasi"]):
        msp.add_text(s, height=2.5, dxfattribs={"style": "ROMANS", "insert": (10, 10 + 5 * i)})
    f = str(tmp_path / "scheda.dxf")
    doc.saveas(f)
    r = S.distinta([f], str(tmp_path / "d.xlsx"))
    assert r["per_categoria"] == {"guaina": 1, "gommino": 1, "puntale": 1, "connettore": 1, "etichetta": 1, "cavo": 1}
    g = next(x for x in r["esempio"] if x["categoria"] == "guaina")
    assert g["diametro"] == 10 and g["lunghezza_cm"] == 200
    assert os.path.isfile(tmp_path / "d.xlsx")
