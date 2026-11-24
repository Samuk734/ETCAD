"""Progettazione: calcoli cavi, colori norma, disegno fogli, distinte Excel.

AVVISO: le tabelle di portata sono SIMPLIFICATE (rame, 30 C, 3 conduttori
caricati, derivazione da CEI-UNEL 35024/1). I risultati vanno sempre validati
dal progettista qualificato; ogni calcolo dichiara le ipotesi usate.
Riferimenti: CEI EN 60204-1 (colori, c.d.t.), CEI-UNEL 35024/1 (portate).
"""
from __future__ import annotations

import math
import os

try:
    from ezdxf.enums import TextEntityAlignment
    _MID = TextEntityAlignment.MIDDLE_CENTER
except ImportError:  # pragma: no cover
    _MID = 4

# Portata Iz (A) rame, 3 conduttori caricati. Posa B1 = tubo incassato,
# posa C = passerella/cavidotto. Valori indicativi -> validare.
PORTATA = {
    ("PVC", "B1"): {1.5: 15.5, 2.5: 21, 4: 28, 6: 36, 10: 50, 16: 66,
                    25: 84, 35: 104, 50: 125, 70: 160, 95: 191, 120: 220},
    ("EPR", "B1"): {1.5: 20, 2.5: 27, 4: 36, 6: 47, 10: 64, 16: 85,
                    25: 110, 35: 135, 50: 164, 70: 208, 95: 250, 120: 287},
    ("PVC", "C"): {1.5: 19.5, 2.5: 26, 4: 35, 6: 45, 10: 62, 16: 82,
                   25: 105, 35: 130, 50: 155, 70: 195, 95: 235, 120: 270},
    ("EPR", "C"): {1.5: 24, 2.5: 32, 4: 42, 6: 54, 10: 74, 16: 97,
                   25: 124, 35: 153, 50: 183, 70: 232, 95: 280, 120: 320},
}
K_TEMP = {"PVC": {30: 1.0, 35: 0.94, 40: 0.87, 45: 0.79, 50: 0.71},
          "EPR": {30: 1.0, 35: 0.96, 40: 0.91, 45: 0.87, 50: 0.82}}
K_GRUPPO = {1: 1.0, 2: 0.8, 3: 0.7, 4: 0.65, 5: 0.6, 6: 0.57}
INTERRUTTORI = [6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160]
SEZIONI = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120]

# CEI EN 60204-1 art. 13.2
COLORI_FILO = {
    "PE": "giallo/verde",
    "N": "blu chiaro",
    "L_potenza_AC": "nero",
    "comando_AC": "rosso",
    "comando_DC": "blu",
    "esterno": "arancione",
}
ACI = {"PE": 3, "N": 5, "L_potenza_AC": 7, "comando_AC": 1,
       "comando_DC": 5, "esterno": 30}


def colori_filo(funzione: str) -> dict:
    f = funzione.strip()
    alias = {"terra": "PE", "neutro": "N", "fase": "L_potenza_AC",
             "potenza": "L_potenza_AC", "ausiliari_ac": "comando_AC",
             "ausiliari_dc": "comando_DC"}
    f = alias.get(f.lower(), f)
    if f not in COLORI_FILO:
        raise ValueError(f"funzione ignota: {funzione}. Valide: "
                         f"{sorted(COLORI_FILO)}")
    return {"funzione": f, "colore": COLORI_FILO[f],
            "norma": "CEI EN 60204-1 art. 13.2"}


def _k_temp(isolante: str, tamb: float) -> float:
    """Fattore di temperatura: scaglione tabellato SUPERIORE (conservativo).

    Es. 37 C -> si usa 40 C. Oltre l'ultimo scaglione: errore esplicito.
    """
    tab = K_TEMP[isolante]
    sopra = [t for t in tab if t >= tamb]
    if not sopra:
        raise ValueError(f"temperatura {tamb} C oltre la tabella ({max(tab)} C)")
    return tab[min(sopra)]


def calcola_cavo(potenza_kw: float, lunghezza_m: float, fasi: int = 3,
                 tensione_v: float = 400.0, cosphi: float = 0.85,
                 posa: str = "B1", isolante: str = "PVC", tamb: float = 30,
                 raggruppati: int = 1, con_neutro: bool = True,
                 max_dv_perc: float = 4.0) -> dict:
    """Dimensiona un cavo: Ib, sezione, Iz, protezione, caduta di tensione."""
    isolante, posa = isolante.upper(), posa.upper()
    if (isolante, posa) not in PORTATA:
        raise ValueError(f"coppia non tabellata: {isolante}/{posa}. "
                         f"Disponibili: {sorted(set(PORTATA))}")
    if fasi == 3:
        ib = potenza_kw * 1000 / (math.sqrt(3) * tensione_v * cosphi)
    elif fasi == 1:
        ib = potenza_kw * 1000 / (tensione_v * cosphi)
    else:
        raise ValueError("fasi: 1 o 3")
    tab = PORTATA[(isolante, posa)]
    kt, kg = _k_temp(isolante, tamb), K_GRUPPO.get(raggruppati, 0.5)
    scelta = None
    for s in SEZIONI:
        iz = tab[s] * kt * kg
        if iz < ib:
            continue
        prot = [i for i in INTERRUTTORI if ib <= i <= iz]
        if not prot:
            continue
        k = math.sqrt(3) if fasi == 3 else 2.0
        dv = k * 100 * ib * lunghezza_m * cosphi / (56 * s * tensione_v)
        scelta = {"sezione_mm2": s, "iz_A": round(iz, 1),
                   "protezione_A": prot[0], "dv_perc": round(dv, 2)}
        if dv <= max_dv_perc:
            break
        scelta = None  # c.d.t. oltre limite: prova sezione maggiore
    if scelta is None:
        raise ValueError(f"nessuna sezione fino a {SEZIONI[-1]}mm2 soddisfa "
                         f"Ib={ib:.1f}A e dV<={max_dv_perc}% "
                         f"(L={lunghezza_m}m). Rivedere percorso/posa.")
    n_cond = (5 if con_neutro else 4) if fasi == 3 else 3
    scelta.update({
        "ib_A": round(ib, 2), "formazione": f"{n_cond}x{scelta['sezione_mm2']}",
        "isolante": isolante, "posa": posa, "k_temp": kt, "k_gruppo": kg,
        "ipotesi": "rame, 3 conduttori caricati, 30C base, tabelle semplificate"})
    return scelta


def _layers(doc):
    for nome, colore in [("CORNICE", 7), ("FILI_L", 7), ("FILI_N", 5),
                         ("FILI_PE", 3), ("SIMBOLI", 7), ("TESTI", 2)]:
        if nome not in doc.layers:
            doc.layers.add(nome, color=colore)


def _cornice(msp, titolo: str, riga2: str = ""):
    msp.add_lwpolyline([(5, 5), (415, 5), (415, 292), (5, 292)],
                       close=True, dxfattribs={"layer": "CORNICE"})
    msp.add_line((5, 30), (415, 30), dxfattribs={"layer": "CORNICE"})
    msp.add_text(titolo, height=6, dxfattribs={"layer": "TESTI"}).set_placement(
        (15, 18))
    if riga2:
        msp.add_text(riga2, height=3.5, dxfattribs={"layer": "TESTI"}).set_placement(
            (15, 9))


def _simbolo(msp, tipo: str, x: float, y: float, sigla: str) -> None:
    """Simboli parametrici semplificati (quadri di potenza/comando)."""
    g = {"layer": "SIMBOLI"}
    t = tipo.lower()
    if t in ("interruttore", "qf"):
        msp.add_lwpolyline([(x - 6, y - 6), (x + 6, y - 6), (x + 6, y + 6),
                            (x - 6, y + 6)], close=True, dxfattribs=g)
        msp.add_line((x - 6, y - 6), (x + 6, y + 6), dxfattribs=g)
    elif t in ("contattore", "km"):
        msp.add_lwpolyline([(x - 7, y - 4), (x + 7, y - 4), (x + 7, y + 4),
                            (x - 7, y + 4)], close=True, dxfattribs=g)
        msp.add_line((x - 7, y), (x - 3, y), dxfattribs=g)
        msp.add_line((x + 3, y), (x + 7, y), dxfattribs=g)
    elif t in ("motore", "m"):
        msp.add_circle((x, y), 7, dxfattribs=g)
        msp.add_text("M", height=5, dxfattribs=g).set_placement((x, y), align=_MID)
    elif t in ("morsetto", "x"):
        msp.add_lwpolyline([(x - 4, y - 2.5), (x + 4, y - 2.5), (x + 4, y + 2.5),
                            (x - 4, y + 2.5)], close=True, dxfattribs=g)
    elif t in ("lampada", "h"):
        msp.add_circle((x, y), 4, dxfattribs=g)
        msp.add_line((x - 3, y - 3), (x + 3, y + 3), dxfattribs=g)
        msp.add_line((x - 3, y + 3), (x + 3, y - 3), dxfattribs=g)
    else:
        msp.add_circle((x, y), 4, dxfattribs=g)
        msp.add_text("?", height=4, dxfattribs=g).set_placement((x, y), align=_MID)
    msp.add_text(sigla, height=3.2, dxfattribs={"layer": "TESTI"}).set_placement(
        (x + 9, y - 1))


def _sbarra(msp, x1: float, x2: float, y: float) -> dict:
    ys = {}
    for i, (nome, layer) in enumerate([("L1", "FILI_L"), ("L2", "FILI_L"),
                                       ("L3", "FILI_L"), ("N", "FILI_N"),
                                       ("PE", "FILI_PE")]):
        yy = y - i * 5
        msp.add_line((x1, yy), (x2, yy), dxfattribs={"layer": layer})
        msp.add_text(nome, height=2.5, dxfattribs={"layer": "TESTI"}).set_placement(
            (x1 - 9, yy - 1))
        ys[nome] = yy
    return ys


def progetta_foglio(spec: dict, dxf_out: str, png_out: str = "",
                    dwg_out: str = "") -> dict:
    """Disegna un foglio da specifica: sbarra, partenze, simboli, sigle cavi.

    spec = {"titolo": str, "riga2": str,
            "partenze": [{"nome": "M1", "potenza_kw": 5.5, "cosphi": 0.85,
                          "lunghezza_m": 30, "tensione_v": 400, "fasi": 3,
                          "posa": "B1", "isolante": "PVC",
                          "protezione": "-QF1", "avviatore": "-KM1",
                          "motore": "-M1", "cavo": "-W1"}],
            "componenti": [{"sigla": "-H1", "tipo": "lampada",
                            "descrizione": "...", "x": 60, "y": 60}]}
    """
    import ezdxf
    doc = ezdxf.new("R2018")
    _layers(doc)
    msp = doc.modelspace()
    _cornice(msp, spec.get("titolo", "Schema elettrico"),
             spec.get("riga2", "generato da ETCAD"))
    partenze = spec.get("partenze", [])
    x2 = 60 + max(1, len(partenze)) * 70
    ys = _sbarra(msp, 40, x2 + 20, 270)
    cavi, componenti, num_filo = [], [], 1
    for i, p in enumerate(partenze):
        x = 70 + i * 70
        calc = calcola_cavo(p["potenza_kw"], p.get("lunghezza_m", 20),
                            fasi=p.get("fasi", 3),
                            tensione_v=p.get("tensione_v", 400),
                            cosphi=p.get("cosphi", 0.85),
                            posa=p.get("posa", "B1"),
                            isolante=p.get("isolante", "PVC"))
        nome_cavo = p.get("cavo", f"-W{i + 1}")
        cavi.append({"nome": nome_cavo, **calc,
                     "lunghezza_m": p.get("lunghezza_m", 20),
                     "utenza": p.get("nome", f"M{i + 1}")})
        # calata: QF -> KM -> morsettiera -> motore
        for nome in ("L1", "L2", "L3", "PE"):
            xx = x + 4 if nome == "PE" else x
            msp.add_line((xx, ys[nome]), (xx, 90),
                         dxfattribs={"layer": "FILI_PE" if nome == "PE"
                                     else "FILI_L"})
        _simbolo(msp, "qf", x, 240, p.get("protezione", f"-QF{i + 1}"))
        _simbolo(msp, "km", x, 205, p.get("avviatore", f"-KM{i + 1}"))
        _simbolo(msp, "x", x, 150, f"-X{i + 1}")
        _simbolo(msp, "m", x, 95, p.get("motore", f"-M{i + 1}"))
        msp.add_text(f"{nome_cavo} {calc['formazione']} L={p.get('lunghezza_m', 20)}m",
                     height=2.8, dxfattribs={"layer": "TESTI"}).set_placement((x + 11, 118))
        msp.add_text(f"In={calc['protezione_A']}A dV={calc['dv_perc']}%", height=2.8, dxfattribs={"layer": "TESTI"}).set_placement((x + 11, 112))
        for yy in (228, 192, 168):
            msp.add_text(str(num_filo), height=2.5,
                         dxfattribs={"layer": "TESTI"}).set_placement((x + 3, yy))
            num_filo += 1
        componenti += [
            {"sigla": p.get("protezione", f"-QF{i + 1}"), "tipo": "interruttore",
             "descrizione": f"Interruttore {calc['protezione_A']}A", "qta": 1,
             "riferimento": p.get("nome", "")},
            {"sigla": p.get("avviatore", f"-KM{i + 1}"), "tipo": "contattore",
             "descrizione": f"Contattore motore {p.get('potenza_kw')}kW",
             "qta": 1, "riferimento": p.get("nome", "")},
            {"sigla": p.get("motore", f"-M{i + 1}"), "tipo": "motore",
             "descrizione": f"Motore {p.get('potenza_kw')}kW", "qta": 1,
             "riferimento": p.get("nome", "")}]
    for c in spec.get("componenti", []):
        _simbolo(msp, c.get("tipo", "?"), c.get("x", 60), c.get("y", 60),
                 c.get("sigla", "-?"))
        componenti.append({"sigla": c.get("sigla", "-?"),
                           "tipo": c.get("tipo", "?"),
                           "descrizione": c.get("descrizione", ""),
                           "qta": c.get("qta", 1), "riferimento": ""})
    doc.saveas(dxf_out)
    res = {"dxf": dxf_out, "partenze": len(partenze),
           "componenti": componenti, "cavi": cavi}
    if png_out:
        res["png"] = _render(doc, png_out)
    if dwg_out:
        import os
        import shutil
        import tempfile
        from . import dwg_io
        tmp = tempfile.mkdtemp(prefix="progetta_")
        try:
            conv = dwg_io.dxf_a_dwg([dxf_out], tmp)
            if conv["dwg"]:
                os.makedirs(os.path.dirname(os.path.abspath(dwg_out)) or ".", exist_ok=True)
                shutil.move(conv["dwg"][0], dwg_out)
                res["dwg"] = dwg_out
            else:
                res["dwg_errore"] = conv
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


def _render(doc, png_out: str, dpi: int = 150) -> str:
    from . import render
    im, _ = render.cad_png(doc, dpi=dpi)
    im.save(png_out)
    return png_out


def distinta_excel(componenti: list[dict], cavi: list[dict], xlsx_out: str,
                   titolo: str = "Distinta") -> str:
    """Genera distinta Excel: fogli Componenti e Cavi."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Componenti"
    bold = Font(bold=True)
    head = ["Pos", "Sigla", "Tipo", "Descrizione", "Qta", "UM", "Riferimento"]
    ws.append(head)
    for c in ws[1]:
        c.font = bold
    for i, r in enumerate(componenti, 1):
        ws.append([i, r.get("sigla", ""), r.get("tipo", ""),
                   r.get("descrizione", ""), r.get("qta", 1),
                   r.get("um", "pz"), r.get("riferimento", "")])
    for col, w in zip("ABCDEFG", (6, 14, 14, 44, 7, 6, 16)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    wc = wb.create_sheet("Cavi")
    headc = ["Nome", "Formazione", "Sezione mm2", "Lunghezza m", "Posa",
             "Isolante", "Ib A", "Iz A", "Prot. A", "dV %", "Utenza"]
    wc.append(headc)
    for c in wc[1]:
        c.font = bold
    for r in cavi:
        wc.append([r.get("nome", ""), r.get("formazione", ""),
                   r.get("sezione_mm2", ""), r.get("lunghezza_m", ""),
                   r.get("posa", ""), r.get("isolante", ""), r.get("ib_A", ""),
                   r.get("iz_A", ""), r.get("protezione_A", ""),
                   r.get("dv_perc", ""), r.get("utenza", "")])
    for col, w in zip("ABCDEFGHIJK", (10, 12, 12, 12, 8, 10, 9, 9, 9, 8, 14)):
        wc.column_dimensions[col].width = w
    wc.freeze_panes = "A2"
    ws.sheet_properties.tabColor = "4472C4"
    wb.properties.title = titolo
    wb.save(xlsx_out)
    return xlsx_out

