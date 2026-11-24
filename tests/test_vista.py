"""Vista dal vivo: conversione in oggetti scena, confronto, server locale (senza SPAC)."""
import http.client
import json
import math
import os
import threading
import time

import ezdxf
import pytest

from etcad.pdf2dwg import Ent
from etcad.vista import conversione as cv
from etcad.vista import server


def test_bulge_quarto_di_cerchio():
    # da (0,2) a (2,0) in senso orario: centro (0,0), raggio 2
    pts = cv.polilinea_bulge([(0, 2, -math.tan(math.pi / 8)), (2, 0, 0)])
    assert pts[0] == (0, 2)
    assert abs(pts[-1][0] - 2) < 1e-9 and abs(pts[-1][1]) < 1e-9
    assert all(abs(math.hypot(x, y) - 2) < 1e-6 for x, y in pts)
    assert all(x >= -1e-9 and y >= -1e-9 for x, y in pts)       # resta nel primo quadrante


def test_da_ent():
    ents = [
        Ent("LINE", {"p0": (0, 0, 0), "p1": (10, 0, 0)}, "GEOMETRIA", 15, None, "TRATT_2p79_2p19"),
        Ent("LWPOLYLINE", {"pts": [(0, 2, -0.4142), (2, 0, 0), (5, 0, 0)], "chiusa": False}, "GEOMETRIA", 25),
        Ent("HATCH", {"anelli": [[(0, 0), (1, 0), (1, 1), (0, 1)]]}, "RIEMPIMENTI", 0),
        Ent("TEXT", {"testo": "1508", "x": 95.96, "y": 189.22, "h": 1.98, "rot": 0.0, "stile": "ROMANS"}, "TESTI", 25),
        Ent("TEXT", {"testo": "fast-on rossi", "x": 60, "y": 10, "h": 2.47, "rot": 0.0, "stile": "ISOCPEUR",
                     "allinea": "R", "xr": 81.6}, "TESTI", 25),
        Ent("TEXT", {"testo": "Ø8", "x": 0, "y": 0, "h": 2, "rot": 0.0, "stile": "ISOCP"}, "TESTI", 25),
    ]
    o = cv.da_ent(ents)
    assert [x["t"] for x in o] == ["LINE", "LWPOLYLINE", "HATCH", "TEXT", "TEXT", "TEXT"]
    assert o[0]["d"] == [2.79, 2.19] and o[0]["lw"] == 0.15
    assert len(o[1]["p"][0]) > 6                                  # l'arco e' appiattito
    assert o[2]["f"] and not o[2]["p"]
    assert o[3]["x"]["shx"] and o[3]["x"]["s"] == "1508"
    assert not o[4]["x"]["shx"] and o[4]["x"]["al"] == "R" and o[4]["x"]["x"] == 81.6
    assert o[4]["x"]["fam"] == "ISOCPEUR"
    assert o[5]["x"]["s"] == "Ø8" and abs(o[5]["x"]["wf"] - 0.87) < 1e-6   # isocp piu' stretto
    assert len({x["id"] for x in o}) == len(o)


def _linea(x0, y0, x1, y1):
    return cv.oggetto("LINE", [[x0, y0, x1, y1]])


def _testo(s, x, y):
    return cv.oggetto("TEXT", x=cv.testo(s, x, y, 2.22, font="isocp.shx"))


def test_confronta():
    fermo = _linea(0, 0, 100, 0)
    quota = _linea(0, 185.77, 50, 185.77)
    via = _linea(0, 50, 10, 50)
    guaina = _testo("Guaina PVC D10", 20, 30)
    prima = [fermo, quota, via, guaina]
    dopo = [fermo, _linea(0, 175.87, 50, 175.87), _testo("Guaina PVC D12", 20, 30), _linea(0, 60, 30, 60)]
    d = cv.confronta(prima, dopo)
    assert [s["dy"] for s in d["spostati"]] == [-9.9] and d["spostati"][0]["da"] == quota["id"]
    assert len(d["modificati"]) == 1 and d["modificati"][0]["a"]["x"]["s"] == "Guaina PVC D12"
    assert [o["p"][0] for o in d["aggiunti"]] == [[0, 60, 30, 60]]
    assert d["tolti"] == [via["id"]]
    # stesso stato: nessuna differenza
    vuoto = cv.confronta(dopo, list(dopo))
    assert not any(vuoto.values())


def _doc_prova(cartella):
    from PIL import Image
    png = os.path.join(cartella, "logo.png")
    Image.new("RGB", (20, 10), "red").save(png)
    doc = ezdxf.new("R2018")
    doc.styles.add("ROMANS", font="romans.shx")
    doc.styles.add("ARIAL", font="arial.ttf")
    blk = doc.blocks.new("CORNICE")
    blk.add_line((0, 0), (420, 0))
    blk.add_attdef("CODICE", (300, 10), dxfattribs={"height": 2.5, "style": "ROMANS"})
    msp = doc.modelspace()
    ins = msp.add_blockref("CORNICE", (0, 0))
    ins.add_auto_attribs({"CODICE": "CAVO-ALIM-A01"})
    msp.add_text("1508", height=1.98, dxfattribs={"style": "ARIAL", "insert": (95.96, 189.22)})
    msp.add_lwpolyline([(0, 0, 0.5), (0, 10, 0)], format="xyb")
    h = msp.add_hatch(color=7)
    h.paths.add_polyline_path([(0, 0), (5, 0), (5, 5)], is_closed=True)
    d = doc.add_image_def(filename="logo.png", size_in_pixel=(20, 10))
    msp.add_image(d, insert=(130, 20), size_in_units=(50, 12))
    return doc


def test_da_doc(tmp_path):
    doc = _doc_prova(str(tmp_path))
    o = cv.da_doc(doc, cartella=str(tmp_path))
    tipi = sorted(x["t"] for x in o)
    assert tipi == ["HATCH", "IMAGE", "LINE", "LWPOLYLINE", "TEXT", "TEXT"]
    testi = {x["x"]["s"]: x["x"] for x in o if x.get("x")}
    assert testi["CAVO-ALIM-A01"]["shx"] and not testi["1508"]["shx"]
    img = [x for x in o if x["t"] == "IMAGE"][0]
    assert img["img"]["file"] == str(tmp_path / "logo.png") and img["img"]["q"] == [130, 20, 180, 32]
    assert not cv.ERRORI or all("IMAGE" not in e for e in cv.ERRORI)


def test_demo_schema(tmp_path):
    from etcad.vista import demo
    d = demo.Disegno()
    passi = list(demo.passi(d))
    assert len(passi) == 8 and all(p and t for p, t in passi)
    f = str(tmp_path / "demo.dxf")
    d.doc.saveas(f)
    o = cv.da_file(f)
    assert len(o) == len(d.msp)                                 # ogni entita' convertita
    testi = {x["x"]["s"] for x in o if x.get("x")}
    assert {"-XS1", "-X2", "CAV-M12-4P-2000", "L = 2000 mm ±10", "PIN", "BN  +24 V"} <= testi
    assert "#286ee6" in {x["c"] for x in o}                      # filo blu con il suo colore vero


def test_glifi():
    g = cv.glifi()["glifi"]
    assert "Ø" in g and "A" in g and g["A"][2]
    assert abs(g["A"][3] - 18) < 3


def _multifoglio(cartella, con_db=True):
    """DXF come un multifoglio SPAC: fogli sovrapposti su layer 1/2/5, cornice condivisa sul layer 0."""
    import sqlite3
    doc = ezdxf.new("R2018")
    doc.styles.add("ROMANS", font="romans.shx")
    blk = doc.blocks.new("CORNICE_A3")
    blk.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    for tag, x in (("NUMERO_DI_FOGLIO", 400), ("FOGLIO_SEGUENTE", 410), ("COMMESSA", 300)):
        blk.add_attdef(tag, (x, 10), dxfattribs={"height": 2.5, "style": "ROMANS"})
    msp = doc.modelspace()
    ins = msp.add_blockref("CORNICE_A3", (0, 0))
    ins.add_auto_attribs({"NUMERO_DI_FOGLIO": "", "FOGLIO_SEGUENTE": "", "COMMESSA": "P-001"})
    for ly in ("1", "2", "5", "2_REV_01"):
        doc.layers.add(ly)
    msp.add_text("COPERTINA", height=5, dxfattribs={"layer": "1", "style": "ROMANS", "insert": (100, 150)})
    msp.add_line((50, 100), (300, 100), dxfattribs={"layer": "2"})
    msp.add_text("ELENCO", height=3, dxfattribs={"layer": "2", "style": "ROMANS", "insert": (60, 120)})
    msp.add_text("VECCHIO", height=3, dxfattribs={"layer": "2_REV_01", "style": "ROMANS", "insert": (60, 130)})
    msp.add_text("MOTORE", height=3, dxfattribs={"layer": "5", "style": "ROMANS", "insert": (60, 140)})
    f = os.path.join(cartella, "MF-ESEMPIO.dxf")
    doc.saveas(f)
    if con_db:
        con = sqlite3.connect(os.path.join(cartella, "dbSPAC.db"))
        con.execute("CREATE TABLE PlotSequence (ID INTEGER, riga INTEGER, multifoglio TEXT, foglio TEXT, "
                    "descrizione TEXT, layer TEXT)")
        con.executemany("INSERT INTO PlotSequence VALUES (?,?,?,?,?,?)",
                        [(1, 0, "MF-ESEMPIO", "1", "COPERTINA", "1"), (2, 1, "MF-ESEMPIO", "2", "ELENCO", "2"),
                         (3, 2, "MF-ESEMPIO", "5", "MOTORI", "5"), (4, 0, "ALTRO", "1", "X", "1")])
        con.commit()
        con.close()
    return f


def test_multifoglio(tmp_path):
    f = _multifoglio(str(tmp_path))
    tipo, fogli = cv.fogli_doc(cv.leggi_doc(f), f)
    assert tipo == "spac" and [x["foglio"] for x in fogli] == ["1", "2", "5"]
    testi = [{o["x"]["s"] for o in x["oggetti"] if o.get("x")} for x in fogli]
    assert "COPERTINA" in testi[0] and "ELENCO" not in testi[0]
    assert "ELENCO" in testi[1] and "VECCHIO" not in testi[1]            # revisioni escluse, come in SPAC
    numeri = [{o["tag"]: o["x"]["s"] for o in x["oggetti"] if o.get("tag")} for x in fogli]
    assert numeri[0] == {"NUMERO_DI_FOGLIO": "1", "FOGLIO_SEGUENTE": "2", "COMMESSA": "P-001"}
    assert numeri[2]["NUMERO_DI_FOGLIO"] == "5" and "FOGLIO_SEGUENTE" not in numeri[2]
    assert all(o.get("hd") for x in fogli for o in x["oggetti"])          # ogni oggetto sa da quale entita' viene
    # senza database: i layer numerici bastano
    sub = tmp_path.parent / (tmp_path.name + "_senza")     # il db si cerca anche nella cartella sopra
    sub.mkdir()
    g = _multifoglio(str(sub), con_db=False)
    tipo2, fogli2 = cv.fogli_doc(cv.leggi_doc(g), g)
    assert tipo2 == "layer" and [x["foglio"] for x in fogli2] == ["1", "2", "5"]


def _testo_romans(s, x, y, h=2.5):
    return cv.oggetto("TEXT", x=cv.testo(s, x, y, h, font="romans.shx"))


def test_controlla_cerca_contenuto():
    ogg = [_testo_romans("PROGETTO-ESEMPIO-01", 200, 10), _linea(220, 5, 220, 20),         # esce dalla cella
           _testo_romans("A", 50, 50), _testo_romans("B", 50.4, 50.2),                   # sovrapposti
           _testo_romans("pulito", 100, 100), _linea(95, 99.5, 130, 99.5),               # sottolineatura: va bene
           _testo_romans("doppio", 10, 80), _testo_romans("doppio", 10, 80)]
    pr = cv.controlla(ogg)
    tipi = sorted(p["tipo"] for p in pr)
    assert tipi == ["doppio", "linea", "testi"], pr
    assert not any("pulito" in p["msg"] for p in pr)
    assert [r["s"] for r in cv.cerca(ogg, "esempio-01")] == ["PROGETTO-ESEMPIO-01"]
    c = cv.contenuto(ogg, zona=[40, 40, 60, 60])
    assert c["oggetti"] == 2 and [t["s"] for t in c["testi"]] == ["B", "A"]


def _png_piccolo() -> str:
    import base64
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (4, 3), "white").save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    os.environ["ETCAD_VISTA_DATI"] = str(tmp_path_factory.mktemp("dati_vista"))
    s = server.avvia(0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s.server_address[1]
    s.shutdown()


def _post(porta, azione, dati):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    c.request("POST", "/api/" + azione, json.dumps(dati), {"Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def _evento(resp, salta=("agente", "ascolto")):
    """Legge il prossimo evento SSE (salta i ping e gli aggiornamenti degli agenti)."""
    while True:
        riga = resp.fp.readline().decode()
        if riga.startswith("data: "):
            resp.fp.readline()
            ev = json.loads(riga[6:])
            if ev["tipo"] not in salta:
                return ev


def _get(porta, url):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=40)
    c.request("GET", url)
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_server_scena_ed_eventi(srv):
    _post(srv, "svuota", {})
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=10)
    c.request("GET", "/api/eventi")
    resp = c.getresponse()
    assert resp.status == 200 and "text/event-stream" in resp.getheader("Content-Type")
    assert _evento(resp)["tipo"] == "stato"
    a = [_linea(0, 0, 10, 0), _testo("D10", 5, 5)]
    st, r = _post(srv, "scena", {"foglio": "prova", "oggetti": a, "anima": False})
    assert st == 200 and r["nuovo"]
    ev = _evento(resp)
    assert ev["tipo"] == "foglio" and ev["foglio"] == "prova" and len(ev["oggetti"]) == 2
    b = [_linea(0, -9.9, 10, -9.9), _testo("D12", 5, 5)]
    st, r = _post(srv, "scena", {"foglio": "prova", "oggetti": b, "nota": "sposto"})
    assert (r["spostati"], r["modificati"]) == (1, 1)
    ev = _evento(resp)
    assert ev["tipo"] == "passo" and ev["nota"] == "sposto" and ev["spostati"][0]["dy"] == -9.9
    _post(srv, "nota", {"testo": "ragiono", "tipo": "pensiero"})
    ev = _evento(resp)
    assert ev["tipo"] == "nota" and ev["genere"] == "pensiero"
    c.close()
    # pagina e file statici
    for u in ("/", "/static/app.js", "/static/style.css", "/api/glifi"):
        c = http.client.HTTPConnection("127.0.0.1", srv, timeout=10)
        c.request("GET", u)
        assert c.getresponse().status == 200, u
    st, r = _post(srv, "sconosciuta", {})
    assert st == 404


def test_osservatore_e_immagine(srv, tmp_path):
    doc = _doc_prova(str(tmp_path))
    f = str(tmp_path / "foglio.dxf")
    doc.saveas(f)
    st, r = _post(srv, "osserva", {"percorso": f})
    assert st == 200 and r["osservati"] == ["foglio.dxf"]
    n0 = len(server.STATO.fogli["foglio"]["oggetti"])
    # immagine servita dal server locale
    img = [o for o in server.STATO.fogli["foglio"]["oggetti"] if o.get("img")][0]["img"]
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=10)
    c.request("GET", img["src"])
    rr = c.getresponse()
    assert rr.status == 200 and rr.read()[:4] == b"\x89PNG"
    # salvataggio con una linea in piu': l'osservatore lo ricarica
    time.sleep(0.05)
    doc.modelspace().add_line((0, 100), (50, 100))
    doc.saveas(f)
    fine = time.time() + 6
    while time.time() < fine and len(server.STATO.fogli["foglio"]["oggetti"]) == n0:
        time.sleep(0.2)
    assert len(server.STATO.fogli["foglio"]["oggetti"]) == n0 + 1
    _post(srv, "smetti", {})


def test_carica_multifoglio(srv, tmp_path):
    f = _multifoglio(str(tmp_path))
    st, r = _post(srv, "carica", {"percorso": f, "agente": "Agente 2"})
    assert st == 200 and r["multifoglio"] == "MF-ESEMPIO" and len(r["fogli"]) == 3
    k = "MF-ESEMPIO/2"
    m = server.STATO.fogli[k]["meta"]
    assert {x: m[x] for x in ("doc", "num", "desc", "layer", "tipo", "n_fogli")} == {
        "doc": "MF-ESEMPIO", "num": "2", "desc": "ELENCO", "layer": "2", "tipo": "spac", "n_fogli": 3}
    assert server.STATO.agenti["Agente 2"]["fogli"][:1] == ["MF-ESEMPIO/1"]
    st, trovati = _get(srv, "/api/cerca?q=motore")
    assert [t["foglio"] for t in trovati] == ["MF-ESEMPIO/5"]


def test_richieste_chat_ascolto(srv):
    _post(srv, "scena", {"foglio": "rich", "oggetti": [_testo_romans("Guaina PVC D10", 20, 30), _linea(0, 0, 100, 0)],
                         "anima": False})
    server.STATO.attendi("prova", timeout=0)                   # svuota cio' che c'era prima
    st, q = _post(srv, "richiesta", {"tipo": "area", "foglio": "rich", "zona": [10, 25, 60, 40],
                                     "testo": "metti D12", "png": _png_piccolo()})
    assert st == 200 and q["n_oggetti"] == 1 and q["img"]
    _post(srv, "chat", {"testo": "e controlla le quote", "da": "utente"})
    st, ev = _get(srv, "/api/chat/attendi?chi=prova&timeout=2")
    assert [e["tipo"] for e in ev] == ["richiesta", "chat"]
    assert ev[0]["contenuto"]["testi"][0]["s"] == "Guaina PVC D10" and os.path.isfile(ev[0]["png"])
    assert ev[1]["testo"] == "e controlla le quote"
    t0 = time.time()                                           # gia' consegnati: si aspetta il timeout
    st, ev2 = _get(srv, "/api/chat/attendi?chi=prova&timeout=0.5")
    assert ev2 == [] and time.time() - t0 >= 0.4
    st, rr = _get(srv, "/api/richieste?tutte=1&segna=0")
    assert any(r["id"] == q["id"] and r["stato"] == "letta" for r in rr)
    st, r = _post(srv, "rispondi", {"id": q["id"], "testo": "Fatto: D12"})
    assert r["stato"] == "fatta" and server.STATO.chat[-1]["da"] == "Claude" and server.STATO.chat[-1]["rif"] == q["id"]
    # un messaggio per un agente preciso non sveglia gli altri agenti
    _post(srv, "chat", {"testo": "solo per te", "da": "utente", "a": "Agente 2"})
    testi3 = [e.get("testo") for e in server.STATO.attendi("Agente 3", agente="Agente 3", timeout=0)]
    testi2 = [e.get("testo") for e in server.STATO.attendi("Agente 2", agente="Agente 2", timeout=0)]
    assert "solo per te" not in testi3 and "e controlla le quote" in testi3      # i messaggi a tutti arrivano a tutti
    assert testi2[-1] == "solo per te"


def test_immagine_dal_browser_e_evidenze(srv):
    _post(srv, "scena", {"foglio": "img", "oggetti": [_linea(0, 0, 50, 30)], "anima": False})
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=20)
    c.request("GET", "/api/eventi")
    resp = c.getresponse()
    assert _evento(resp)["tipo"] == "stato"

    def browser():                                              # finto browser: disegna quando il server chiede
        while True:
            ev = _evento(resp)
            if ev["tipo"] == "rendi":
                assert ev["oggetti"] and ev["zona"] == [0.0, 0.0, 50.0, 30.0]
                _post(srv, "immagine_pronta", {"rid": ev["rid"], "png": _png_piccolo()})
                return
    t = threading.Thread(target=browser, daemon=True)
    t.start()
    st, r = _post(srv, "immagine", {"foglio": "img", "zona": [0, 0, 50, 30], "timeout": 10})
    assert st == 200 and os.path.isfile(r["png"]) and r["byte"] > 20
    t.join(5)
    st, r = _post(srv, "evidenzia", {"foglio": "img", "zona": [0, 0, 10, 10], "testo": "qui?", "agente": "Agente 2"})
    assert r["evidenza"]["colore"] == server.STATO.agenti["Agente 2"]["colore"]
    ev = _evento(resp)
    assert ev["tipo"] == "evidenze" and ev["nuova"] == r["evidenza"]["id"]
    _post(srv, "evidenzia", {"pulisci": True})
    assert server.STATO.evidenze == []
    st, r = _post(srv, "controlla", {"foglio": "img", "mostra": False})
    assert st == 200 and r["problemi"] == []
    c.close()


def test_sfondo_pdf_e_dati_spac(srv, tmp_path):
    import sqlite3
    fitz = pytest.importorskip("fitz")
    f = _multifoglio(str(tmp_path))
    _post(srv, "carica", {"percorso": f})
    k = "MF-ESEMPIO/2"
    hd = next(o["hd"] for o in server.STATO.fogli[k]["oggetti"] if o.get("x") and o["x"]["s"] == "ELENCO")
    con = sqlite3.connect(str(tmp_path / "dbSPAC.db"))
    con.execute("CREATE TABLE Elementi (Dwg TEXT, Foglio TEXT, Handle TEXT, Sigla TEXT, Codice TEXT)")
    con.execute("INSERT INTO Elementi VALUES (?,?,?,?,?)", ("MF-ESEMPIO", "2", hd, "-KM1", "LC1D09"))
    con.commit()
    con.close()
    st, d = _get(srv, f"/api/spac?foglio={k}&hd={hd}")
    assert d["tabelle"]["Elementi"][0]["Sigla"] == "-KM1"
    pdf = str(tmp_path / "p.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=420 * 72 / 25.4, height=297 * 72 / 25.4)
    pg.draw_line((10, 10), (200, 200))
    doc.save(pdf)
    doc.close()
    st, r = _post(srv, "sfondo", {"foglio": k, "pdf": pdf, "pagina": 1})
    assert st == 200 and [round(v) for v in r["sfondo"]["q"]] == [0, 0, 420, 297]
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=10)
    c.request("GET", r["sfondo"]["src"])
    assert c.getresponse().read()[:4] == b"\x89PNG"
    _post(srv, "scena", {"foglio": k, "oggetti": server.STATO.fogli[k]["oggetti"][:-1]})
    assert server.STATO.fogli[k]["sfondo"]                       # resta dopo un passo
    st, r = _post(srv, "sfondo", {"foglio": k, "pdf": ""})
    assert r["sfondo"] is None

def test_bozza_utente_in_operazioni():
    """Modifiche disegnate nella pagina -> entita' vere (riferimenti alla bozza risolti in ordine)."""
    from etcad.vista import bozza
    doc = ezdxf.new()
    msp = doc.modelspace()
    h = msp.add_text("VECCHIO", height=2.5).dxf.handle
    ops = [
        {"k": "add", "id": "u1", "tipo": "linea", "p0": [0, 0], "p1": [10, 0]},
        {"k": "add", "id": "u2", "tipo": "rettangolo", "punti": [[0, 0], [5, 0], [5, 5], [0, 5]]},
        {"k": "add", "id": "u3", "tipo": "quota", "p1": [0, 0], "p2": [10, 0], "pos": [5, -6]},
        {"k": "add", "id": "u4", "tipo": "etichetta", "testo": "Guaina", "x": 20, "y": 20, "punta": [30, 10]},
        {"k": "text", "rif": "u4", "testo": "Guaina PVC Ø8"},
        {"k": "move", "rif": "u1", "dx": 0, "dy": 3},
        {"k": "copy", "rif": "u2", "dx": 10, "dy": 0, "id": "u5"},
        {"k": "del", "rif": "u5"},
        {"k": "text", "hd": h, "testo": "NUOVO"},
        {"k": "copy", "hd": h, "dx": 0, "dy": -5, "id": "u6"},
        {"k": "prop", "rif": "u6", "colore": 1},
        {"k": "add", "id": "u7", "tipo": "testo", "testo": "L=200 ±1 30°", "x": 1, "y": 1, "layer": "7"},
    ]
    rep = bozza.applica(doc, ops, "5")
    assert len(rep) == len(ops)
    linee = msp.query("LINE")
    assert any(e.dxf.layer == "5" and abs(e.dxf.start.y - 3) < 1e-9 for e in linee)     # layer del foglio, spostata
    assert len(msp.query("LWPOLYLINE")) == 1                                              # la copia e' stata cancellata
    d = msp.query("DIMENSION")[0]
    assert abs(d.get_measurement() - 10) < 1e-9 and abs(d.dxf.defpoint.y + 6) < 1e-9
    testi = sorted(e.dxf.text for e in msp.query("TEXT"))
    assert testi == ["Guaina PVC %%c8", "L=200 %%p1 30%%d", "NUOVO", "NUOVO"]
    assert [e.dxf.color for e in msp.query("TEXT") if e.dxf.insert.y < -4] == [1]
    assert {e.dxf.layer for e in msp.query("TEXT") if e.dxf.text.startswith("L=")} == {"7"}


def test_salva_bozza_dal_server(tmp_path):
    """La pagina salva: copia accanto all'originale (originale intatto) oppure sovrascrive; poi ricarica."""
    f = tmp_path / "scheda.dxf"
    doc = ezdxf.new()
    t = doc.modelspace().add_text("ETI 2/15", height=2.5)
    t.set_placement((10, 10))
    h = t.dxf.handle
    doc.saveas(f)
    st = server.Stato()
    st.carica(str(f), anima=False)
    assert st.fogli["scheda"]["meta"]["file"] == str(f)
    ops = [{"k": "text", "hd": h, "testo": "ETI 1/15"}, {"k": "add", "id": "u1", "tipo": "cerchio", "centro": [0, 0], "raggio": 2}]
    r = st.salva_bozza("scheda", ops, copia=True)
    assert r["file"].endswith("scheda_modificato.dxf") and r["n"] == 2
    copia = ezdxf.readfile(r["file"]).modelspace()
    assert [e.dxf.text for e in copia.query("TEXT")] == ["ETI 1/15"] and len(copia.query("CIRCLE")) == 1
    assert [e.dxf.text for e in ezdxf.readfile(f).modelspace().query("TEXT")] == ["ETI 2/15"]
    assert "scheda_modificato" in st.fogli                                  # la copia si apre in una scheda
    r = st.salva_bozza("scheda", [{"k": "del", "hd": h}], copia=False)
    assert r["file"] == str(f) and not ezdxf.readfile(f).modelspace().query("TEXT")
    with pytest.raises(ValueError):
        st.salva_bozza("scheda", [], copia=True)


def test_agenti_con_modello_e_modello_sessione(tmp_path, monkeypatch):
    """Gli agenti li apre Claude e li registra con modello ed effort; il modello della sessione si legge dal registro."""
    monkeypatch.setenv("ETCAD_VISTA_DATI", str(tmp_path / "dati"))
    st = server.Stato()
    a = st.agente("Agente cavi", stato="lavora", modello="claude-sonnet-5-5", effort="medium", compito="controlla i colori")
    assert (a["modello"], a["effort"], a["compito"]) == ("claude-sonnet-5-5", "medium", "controlla i colori")
    assert st.agente("Agente cavi")["modello"] == "claude-sonnet-5-5"              # resta finche' non cambia
    st.messaggio("ciao agente", "utente", "Agente cavi")
    st.messaggio("ciao Claude", "utente", None)
    assert [m["testo"] for m in st.attendi("Agente cavi", agente="Agente cavi", timeout=0)] == ["ciao agente", "ciao Claude"]
    assert [m["testo"] for m in st.attendi("Claude", timeout=0)] == ["ciao agente", "ciao Claude"]
    # modello ed effort della sessione di Claude Code
    assert server.nome_modello("claude-opus-5-5") == "Opus 5.5" and server.nome_modello("x") == "x"
    cart = tmp_path / "sessioni"
    cart.mkdir()
    righe = [{"type": "user", "message": {"content": "ciao"}},
             {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-5-5"}},
             {"type": "assistant", "message": {"model": "claude-fable-5-2"}, "effort": "high"},
             {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-5-5"}, "effort": "low"}]
    (cart / "abc.jsonl").write_text("\n".join(json.dumps(x) for x in righe) + "\n", encoding="utf-8")
    s = server.modello_sessione(str(cart))
    assert (s["modello"], s["nome"], s["effort"], s["sessione"]) == ("claude-fable-5-2", "Fable 5.2", "high", "abc")
    assert server.modello_sessione(str(tmp_path / "manca")) is None
