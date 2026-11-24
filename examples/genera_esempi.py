"""Genera i disegni di esempio di ETCAD (tutti inventati, nessun dato reale).

    python examples/genera_esempi.py

Uscite (accanto a questo script):
- cavo_m12/      Cavo sensore M12.dwg               la scheda della demo, un foglio
- set_cablaggi/  Set cablaggi di esempio.dwg        6 fogli A3 affiancati, cornice a blocco con attributi
                 Set cablaggi di esempio.pdf        PDF vettoriale con i testi "esplosi" in tratti, come lo
                                                    stampano molti CAD: l'ingresso per la demo PDF -> DWG
                 anteprime/foglio_N.png             anteprime dei fogli
Il DWG richiede ODA File Converter (gratuito) o SPAC; senza, resta il DXF.

ODA File Converter scrive nel DWG (sezione SummaryInfo, campo "last saved by") il nome dell'utente di Windows:
nei file d'esempio lo sostituisco con un nome neutro, ricalcolando i checksum della pagina (`togli_utente`).
"""
from __future__ import annotations

import getpass
import os
import shutil
import struct
import sys
import tempfile

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))

from etcad import render  # noqa: E402
from etcad.vista import demo  # noqa: E402

PASSO = 440.0      # distanza fra i fogli affiancati (A3 = 420 mm + 20 di aria)

SET = "Set cablaggi di esempio"
TOT = "6"

M12_4P = dict(demo.DEMO, foglio="2", di=TOT)

M12_8P = {
    "titolo": "CAVO ISOLA VALVOLE M12 - 8 POLI",
    "sottotitolo": "Connettore -XS3 dell'isola valvole verso morsettiera -X5",
    "codice": "CAV-M12-8P-5000", "nota_codice": "L = 5000 mm",
    "sinistra": {"tipo": "circolare", "sigla": "-XS3", "descrizione": "M12 femmina 8 poli, codifica A", "poli": 8},
    "destra": {"tipo": "morsetti", "sigla": "-X5"},
    "cavo": "Cavo PUR 8x0,25  %%c6,4", "sezione": "PUR 8x0,25",
    "fili": [("1", "WH", "valvola Y1", "1"), ("2", "BN", "valvola Y2", "2"), ("3", "GN", "valvola Y3", "3"),
             ("4", "YE", "valvola Y4", "4"), ("5", "GY", "valvola Y5", "5"), ("6", "PK", "valvola Y6", "6"),
             ("7", "BU", "0 V comune", "7"), ("8", "RD", "riserva", "8")],
    "lunghezza": 5000, "tolleranza": 20,
    "distinta": [("1", "1", "Connettore M12 femmina 8 poli diritto", "M12-F8-D"),
                 ("2", "5 m", "Cavo PUR 8x0,25 %%c6,4", "PUR-8X025"),
                 ("3", "8", "Puntale isolato 0,25", "PI-025"),
                 ("4", "1", "Etichetta cavo stampata", "ET-CAV")],
    "note": ["Riserva (RD) isolata e piegata", "Etichetta a 100 mm dal connettore", "Puntali su tutti gli 8 fili",
             "Collaudo continuita' 100%"],
    "foglio": "3", "di": TOT,
}

Y_M12_M8 = {
    "titolo": "SDOPPIATORE A Y  M12 / 2x M8",
    "sottotitolo": "Due sensori su un ingresso doppio del modulo I/O",
    "codice": "CAV-Y-M12-2M8", "nota_codice": "Tronco 1000 mm, rami 300 mm",
    "cavo": "Cavo PUR 4x0,34  %%c5,2",
    "distinta": [("1", "1", "Connettore M12 maschio 4 poli", "M12-M4-D"),
                 ("2", "2", "Connettore M8 femmina 3 poli", "M8-F3-D"),
                 ("3", "1", "Corpo stampato a Y", "Y-M12-M8"),
                 ("4", "1,6 m", "Cavo PUR 4x0,34 / 3x0,34", "PUR-X034")],
    "note": ["Ingresso A sul pin 4, ingresso B sul pin 2", "+24 V e 0 V comuni ai due rami",
             "Corpo stampato IP67", "Collaudo continuita' 100%"],
    "foglio": "4", "di": TOT,
}

MOTORE = {
    "titolo": "CAVO MOTORE SCHERMATO 4G1,5",
    "sottotitolo": "Motore -M1 0,75 kW verso morsettiera di potenza -X1",
    "codice": "CAV-MOT-4G15-SH", "nota_codice": "L = 10000 mm",
    "sinistra": {"tipo": "morsetti", "sigla": "-M1", "descrizione": "scatola morsetti motore"},
    "destra": {"tipo": "morsetti", "sigla": "-X1"},
    "cavo": "Cavo motore schermato 4G1,5  %%c9,8", "sezione": "4G1,5 + schermo",
    "schermo": "Schermo a 360%%d con pressacavo EMC",
    "fili": [("U1", "BK", "anima 1 - fase U", "U"), ("V1", "BK", "anima 2 - fase V", "V"),
             ("W1", "BK", "anima 3 - fase W", "W"), ("PE", "GNYE", "terra", "PE")],
    "intestazione": ("MOTORE", "ANIMA", "FUNZIONE", "QUADRO"),
    "lunghezza": 10000, "tolleranza": 50,
    "distinta": [("1", "10 m", "Cavo schermato 4G1,5 %%c9,8", "SH-4G15"),
                 ("2", "2", "Pressacavo EMC M20", "PG-EMC-M20"),
                 ("3", "3", "Puntale isolato 1,5", "PI-15"),
                 ("4", "2", "Capocorda ad occhiello M5 per PE", "CO-M5-15")],
    "note": ["Schermo collegato su entrambi i lati", "PE con capocorda ad occhiello M5", "Coppia morsetti 0,6 Nm",
             "Prova di isolamento a 500 V"],
    "foglio": "5", "di": TOT,
}

INGRESSI = {
    "titolo": "SCHEMA INGRESSI DIGITALI",
    "sottotitolo": "Sensori -B1...-B8 sul modulo di ingresso -A1",
    "codice": "SCH-IN-A1", "nota_codice": "Alimentazione 24 V DC",
    "sensori": [("-B1", "Pezzo|in posizione"), ("-B2", "Cilindro A|avanti"), ("-B3", "Cilindro A|indietro"),
                ("-B4", "Porta|chiusa"), ("-B5", "Livello|minimo"), ("-B6", "Livello|massimo"),
                ("-B7", "Pressione|aria OK"), ("-B8", "Riserva|")],
    "rimando": "cavo CAV-M12-4P-2000|(foglio 2)",
    "note": ["Sensori PNP a 3 fili, 10-30 V DC", "Fusibile -F2 2 A ritardato", "Numerazione fili 1001-1008"],
    "foglio": "6", "di": TOT,
}

COPERTINA = {"titolo": "SET CABLAGGI DI ESEMPIO", "sottotitolo": "Copertina e lista fogli", "codice": "SET-ESEMPIO-01",
             "grande": "SET CABLAGGI", "medio": "Macchina di esempio", "foglio": "1", "di": TOT}


def fogli():
    return [
        (COPERTINA, "copertina"),
        (M12_4P, "cavo"),
        (M12_8P, "cavo"),
        (Y_M12_M8, "sdoppiatore"),
        (MOTORE, "cavo"),
        (INGRESSI, "ingressi"),
    ]


def _checksum(seed: int, dati: bytes) -> int:
    """Checksum delle pagine dei DWG R2004+ (Open Design Specification for .dwg files)."""
    s1, s2 = seed & 0xFFFF, seed >> 16
    for i in range(0, len(dati), 0x15B0):
        for b in dati[i:i + 0x15B0]:
            s1 += b
            s2 += s1
        s1 %= 0xFFF1
        s2 %= 0xFFF1
    return (s2 << 16) | (s1 & 0xFFFF)


def togli_utente(dwg: str, utente: str = "") -> bool:
    """Sostituisce il nome utente di Windows nella pagina SummaryInfo di un DWG R2004+ (stessa lunghezza), poi
    ricalcola il checksum dei dati e dell'intestazione della pagina. Si ferma senza toccare il file se la pagina
    non e' quella attesa o se i checksum originali non tornano."""
    utente = utente or getpass.getuser()
    d = bytearray(open(dwg, "rb").read())
    vecchio = utente.encode("utf-16-le")
    nuovo = ("esempio" if len(utente) == 7 else ("ETCAD" + "_" * len(utente))[:len(utente)]).encode("utf-16-le")
    pos = d.find(vecchio)
    if pos < 0:
        return False
    off = 0x100                                                 # prima pagina dati: SummaryInfo, non compressa
    h = [v ^ (0x4164536B ^ off) for v in struct.unpack_from("<8I", d, off)]
    if h[0] != 0x4163043B or not (off + 32 <= pos < off + 32 + h[2]):
        return False
    dati = bytes(d[off + 32: off + 32 + h[2]])
    senza = list(h)
    senza[6] = 0
    if _checksum(0, dati) != h[7] or _checksum(h[7], struct.pack("<8I", *senza)) != h[6]:
        return False
    d[pos:pos + len(vecchio)] = nuovo
    dati = bytes(d[off + 32: off + 32 + h[2]])
    h[7] = _checksum(0, dati)
    h[6] = 0
    h[6] = _checksum(h[7], struct.pack("<8I", *h))
    struct.pack_into("<8I", d, off, *[v ^ (0x4164536B ^ off) for v in h])
    open(dwg, "wb").write(bytes(d))
    return True


def _elenco():
    return [{"foglio": s["foglio"], "codice": s.get("codice", ""), "titolo": s["titolo"]} for s, _ in fogli()]


def disegna_foglio(d, spec, tipo):
    if tipo == "copertina":
        demo.copertina(d, spec, _elenco())
    elif tipo == "cavo":
        demo.scheda_cavo(d, spec, blocco=True)
    elif tipo == "sdoppiatore":
        demo.sdoppiatore(d, spec)
    else:
        demo.schema_ingressi(d, spec)


def set_cablaggi():
    """Documento con i 6 fogli affiancati; ogni foglio ha la cornice come INSERT di CORNICE_A3."""
    d = demo.Disegno()
    for k, (spec, tipo) in enumerate(fogli()):
        d.ox = k * PASSO
        disegna_foglio(d, spec, tipo)
    return d.doc


def foglio_singolo(k):
    """Un foglio del set da solo (per il PDF e le anteprime: niente contenuto degli altri fogli)."""
    d = demo.Disegno()
    disegna_foglio(d, *fogli()[k])
    return d.doc


def main():
    out = {}
    # 1) la scheda della demo
    d = demo.Disegno()
    demo.scheda_cavo(d, demo.DEMO)
    cart = os.path.join(QUI, "cavo_m12")
    r = demo.salva(d.doc, cart, "Cavo sensore M12")
    im, _ = render.cad_png(r["dxf"], zona=(0, 0, 420, 297), lato_max_px=2000)
    im.save(os.path.join(cart, "Cavo sensore M12.png"))
    out["cavo_m12"] = r
    # 2) il set di 6 fogli
    doc = set_cablaggi()
    cart = os.path.join(QUI, "set_cablaggi")
    r = demo.salva(doc, cart, SET)
    os.makedirs(os.path.join(cart, "anteprime"), exist_ok=True)
    pagine = []
    tmp = tempfile.mkdtemp(prefix="etcad_esempi_")
    try:
        for k in range(len(fogli())):
            dxf = os.path.join(tmp, f"foglio_{k + 1}.dxf")
            foglio_singolo(k).saveas(dxf)
            pagine.append(render.cad_pdf(dxf, zona=(0, 0, 420, 297)))
            im, _ = render.cad_png(dxf, zona=(0, 0, 420, 297), lato_max_px=1800)
            im.save(os.path.join(cart, "anteprime", f"foglio_{k + 1}.png"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    render.unisci_pdf(pagine, os.path.join(cart, SET + ".pdf"))
    out["set"] = r
    # senza convertitore resta il DXF; con il DWG il DXF non serve
    for v in out.values():
        if v.get("dwg") and os.path.exists(v["dxf"]):
            os.remove(v["dxf"])
        if v.get("dwg"):
            v["utente_tolto"] = togli_utente(v["dwg"])
    for k, v in out.items():
        print(k, "->", v.get("dwg") or v.get("dxf"), v.get("errore_dwg", ""),
              "(nome utente tolto)" if v.get("utente_tolto") else "")


if __name__ == "__main__":
    main()
