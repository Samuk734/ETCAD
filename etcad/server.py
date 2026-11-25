"""Server MCP dual-mode per SPAC Automazione CAD (stdio).

Avvio:  .venv/Scripts/python -m etcad.server
Trasporto stdio: compatibile con Claude Desktop, Claude Code, Cline,
Continue, MCP Inspector e qualsiasi client MCP.

I tool che durano (conversioni DWG, PDF, anteprime) girano in un thread
separato: il server resta reattivo mentre accoreconsole/ODA lavorano.
I tool di "visione" restituiscono l'immagine direttamente all'AI.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import re

try:
    import anyio
    from mcp.server.fastmcp import Context, FastMCP, Image
except ImportError as e:  # pragma: no cover
    raise SystemExit("Pacchetto 'mcp' mancante: attiva .venv e fai pip install -e .") from e

from . import batch, config, dbtools, dwg_io, lettura, live, modifica, norms, progetta
from . import multifoglio as mf
from . import pdf2dwg, render

mcp = FastMCP("etcad")

import logging  # noqa: E402

for _nome in ("ezdxf", "RapidOCR", "fontTools", "PIL"):
    logging.getLogger(_nome).setLevel(logging.ERROR)  # solo errori su stderr


def strumento(fn):
    """Registra un tool: output non strutturato, esecuzione in un thread."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    wrapper.__signature__ = inspect.signature(fn)
    mcp.tool(structured_output=False)(wrapper)
    return fn


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def _img(im, lato_max_px: int = 1600) -> Image:
    return Image(data=render.png_bytes(im, lato_max_px), format="png")


def _zona(zona: str):
    """'x0,y0,x1,y1' in mm (origine in basso a sinistra) o vuoto."""
    return [float(v) for v in zona.replace(";", ",").split(",")] if zona.strip() else None


def _db(commessa: str, nome: str = "dbSPAC.db") -> str:
    root = config.resolve_commessa(commessa)
    p = os.path.join(root, nome)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"DB non trovato: {p}")
    return p


def _file(commessa: str, dwg: str) -> str:
    """Percorso assoluto, oppure nome file dentro la commessa."""
    if os.path.isfile(dwg):
        return os.path.abspath(dwg)
    if not commessa:
        raise FileNotFoundError(dwg)
    return os.path.join(config.resolve_commessa(commessa), dwg)


def _dwg(commessa: str, multifoglio: str) -> str:
    root = config.resolve_commessa(commessa)
    p = os.path.join(root, multifoglio + ".dwg")
    if not os.path.isfile(p):
        cands = [os.path.join(root, f) for f in os.listdir(root)
                 if f.lower() == (multifoglio + ".dwg").lower()]
        if not cands:
            raise FileNotFoundError(f"DWG non trovato: {p}")
        p = cands[0]
    return p


# ---------------------------------------------------------------------------
# stato e diagnostica
# ---------------------------------------------------------------------------

@strumento
def spac_status() -> str:
    """Stato: installazioni SPAC, SPAC aperto/chiuso, convertitori DWG, OCR."""
    from . import pdf_testi
    inst = [{"anno": i.year, "root": i.root, "cad": i.cad_exe, "console": i.has_console,
             "commesse": i.commesse} for i in config.find_installs()]
    return _j({"installazioni": inst, "commesse": config.radici_commesse(),
               "convertitori_dwg": dwg_io.backend_disponibili(),
               "ocr": pdf_testi.motore_ocr() is not None,
               "live": live.status(), "processi": config.is_spac_running()})


@strumento
def spac_diagnostica(prova_conversione: bool = True) -> str:
    """Verifica l'ambiente: accoreconsole (tempo di avvio e di una conversione
    DXF->DWG->DXF di prova), ODA, OCR, dipendenze, ProgID COM. Da lanciare
    sul PC con SPAC se qualcosa non funziona."""
    import tempfile
    import time
    import ezdxf
    from . import pdf_testi
    out: dict = {"convertitori": dwg_io.backend_disponibili(),
                 "ocr_rapidocr": pdf_testi.motore_ocr() is not None,
                 "progid_live": live.progid_candidati()[:8],
                 "variabili": {k: os.environ.get(k, "") for k in
                               ("SPAC_ROOT", "SPAC_COMMESSE", "SPAC_PROGID",
                                "ODA_FILE_CONVERTER", "SPAC_LINGUA")}}
    try:
        import shapely  # noqa: F401
        out["shapely"] = True
    except ImportError:
        out["shapely"] = False
    if prova_conversione:
        tmp = tempfile.mkdtemp(prefix="spac_diag_")
        doc = ezdxf.new("R2018")
        doc.modelspace().add_line((0, 0), (100, 50))
        doc.modelspace().add_text("PROVA", height=3.5).set_placement((10, 10))
        dxf = os.path.join(tmp, "prova.dxf")
        doc.saveas(dxf)
        for b, disp in dwg_io.backend_disponibili().items():
            if not disp:
                continue
            t0 = time.time()
            try:
                r = dwg_io.dxf_a_dwg([dxf], os.path.join(tmp, b), backend=b, timeout=300)
                ok = r["ok"]
                rit = ""
                if ok:
                    back = os.path.join(tmp, b, "ritorno.dxf")
                    dwg_io.dwg_a_dxf(r["dwg"][0], back, backend=b, timeout=300)
                    rit = [e.dxftype() for e in ezdxf.readfile(back).modelspace()]
                out[f"prova_{b}"] = {"ok": ok, "secondi": round(time.time() - t0, 1),
                                     "entita_rilette": rit, "log": r.get("log", "")}
            except Exception as e:  # noqa: BLE001
                out[f"prova_{b}"] = {"ok": False, "errore": str(e),
                                     "secondi": round(time.time() - t0, 1)}
    return _j(out)


# ---------------------------------------------------------------------------
# commesse e database
# ---------------------------------------------------------------------------

@strumento
def commessa_list() -> str:
    """Elenca le commesse (progetti) con DWG e DB presenti."""
    out = [{"nome": c.name, "path": c.path, "dwg": c.dwgs, "db": c.dbs}
           for c in config.list_commesse()]
    return _j(out)


@strumento
def commessa_info(commessa: str) -> str:
    """Dettaglio commessa: multifogli, fogli, tabelle DB, conteggi."""
    root = config.resolve_commessa(commessa)
    info: dict = {"nome": os.path.basename(root), "path": root}
    try:
        db = _db(commessa)
        info["tabelle"] = dbtools.list_tables(db)
        dwgs = dbtools.query(db, "SELECT Nome, Ordine, Scandito FROM Dwg ORDER BY Ordine")
        info["multifogli"] = dwgs["rows"]
        info["fogli"] = {m["Nome"]: mf.fogli_list(db, m["Nome"]) for m in dwgs["rows"]}
    except Exception as e:  # noqa: BLE001 - info parziali se DB assente
        info["db_errore"] = str(e)
    return _j(info)


@strumento
def db_schema(commessa: str, tabella: str = "") -> str:
    """Schema DB commessa: tabelle+righe, o colonne di una tabella."""
    db = _db(commessa)
    if tabella:
        return _j(dbtools.table_schema(db, tabella))
    return _j(dbtools.list_tables(db))


@strumento
def db_query(commessa: str, sql: str, limite: int = 200) -> str:
    """Query SOLO lettura sul DB di commessa (SELECT/WITH)."""
    return _j(dbtools.query(_db(commessa), sql, limite))


@strumento
def fogli_list(commessa: str, multifoglio: str) -> str:
    """Fogli di un multifoglio con descrizione e conteggio elementi."""
    return _j(mf.fogli_list(_db(commessa), multifoglio))


@strumento
def multifoglio_scompatta(commessa: str, multifoglio: str, cartella_out: str,
                          fogli: str = "") -> str:
    """Scompatta (headless): dati DB per-foglio in JSON + manifest.

    fogli: lista separata da virgole, vuoto=tutti. Non tocca l'originale.
    Per la geometria usa multifoglio_scompatta_dxf.
    """
    lst = [f.strip() for f in fogli.split(",") if f.strip()] or None
    return _j(mf.scompatta_db(_db(commessa), multifoglio, lst, cartella_out))


@strumento
def multifoglio_ricompatta(commessa: str, cartella_package: str,
                           db_out: str, multifoglio: str = "") -> str:
    """Ricompatta (headless): fonde i JSON per-foglio in un NUOVO DB.

    Il DB originale non viene modificato. multifoglio vuoto = dal manifest.
    """
    with open(os.path.join(cartella_package, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    nome = multifoglio or man["multifoglio"]
    return _j(mf.ricompatta_db(_db(commessa), cartella_package, db_out, nome))


@strumento
def db_coerenza(commessa: str, multifoglio: str) -> str:
    """Righe DB con handle non piu' nel DWG (solo lettura)."""
    return _j(lettura.coerenza_db(_db(commessa), _dwg(commessa, multifoglio), multifoglio))


def _commessa_o_percorso(commessa: str, percorso: str) -> str:
    if percorso:
        return os.path.abspath(percorso)
    return config.resolve_commessa(commessa)


@strumento
def commessa_config(commessa: str = "", percorso: str = "", completo: bool = False) -> str:
    """Configurazione SPAC della commessa (cartella CFG): numerazione fogli (posizioni di foglio,
    seguente, precedente, totale), opzioni di controllo (No_Sigle_Doppie, Evidenzia_Fili_Non_Num...),
    cross reference, layout dello schema (colonne/righe), siglatura, fogli esclusi dalla stampa
    per multifoglio, stili delle tabelle (distinta). percorso: cartella commessa, DWG, dbSPAC.db
    o CFG. completo=True aggiunge tutte le sezioni/chiavi dei .ini."""
    from . import commessa_cfg as cc
    r = cc.leggi_config(_commessa_o_percorso(commessa, percorso))
    if not completo:
        r = {k: v for k, v in r.items() if k != "file"} | {"file_ini": sorted(r.get("file", {}))}
    return _j(r)


@strumento
def db_controlli(commessa: str = "", db: str = "", multifoglio: str = "") -> str:
    """Controlli sul dbSPAC.db come quelli di SPAC (in sola lettura): rimandi non collegati o verso
    fogli inesistenti, sigle doppie, contatti senza componente madre, componenti senza sigla,
    morsetti doppi / senza numero / senza morsettiera, fili non numerati, morsettiere non
    definite, PlotSequence non allineata ai fogli. Ogni controllo ha esito, numero, dettaglio
    e spiegazione. Complementare a norms_check (formato sigle, numerazione fili, cavi, distinta)."""
    from . import controlli_db as cdb
    path = os.path.abspath(db) if db else _db(commessa)
    return _j(cdb.esegui(path, multifoglio or None))


@strumento
def norms_check(commessa: str) -> str:
    """Controlli coerenza dati (CEI EN 60204-1, IEC 81346, CEI EN 61439)."""
    return _j(norms.run_all(_db(commessa)))


# ---------------------------------------------------------------------------
# DWG: lettura e vista
# ---------------------------------------------------------------------------

@strumento
def dwg_info(dwg: str, commessa: str = "") -> str:
    """Info DWG (SPAC chiuso OK): layout, entita' per tipo, layer, blocchi, stili."""
    return _j(batch.dwg_info_headless(_file(commessa, dwg)))


@strumento
def dwg_entita(dwg: str, zona: str = "", tipi: str = "", layer: str = "",
               testo: str = "", limite: int = 300, commessa: str = "") -> str:
    """Entita' di un DWG/DXF con handle, posizione e testo, filtrabili.

    zona: "x0,y0,x1,y1" in mm (origine in basso a sinistra); tipi es.
    "TEXT,INSERT,LINE"; testo: sottostringa (anche negli attributi).
    Gli handle servono a dwg_modifica.
    """
    return _j(lettura.entita_dwg(_file(commessa, dwg), _zona(zona), tipi, layer,
                                 testo, limite))


@strumento
def dwg_anteprima(dwg: str, zona: str = "", lato_max_px: int = 1600,
                  commessa: str = ""):
    """Immagine di un DWG/DXF (intero o zona "x0,y0,x1,y1" in mm).

    Per leggere testi piccoli zooma con zona (es. un quarto del foglio).
    I testi SHX sono disegnati con i glifi Hershey se SPAC non c'e'.
    """
    im, info = render.cad_png(_file(commessa, dwg), zona=_zona(zona),
                              lato_max_px=lato_max_px)
    return [_img(im, lato_max_px), _j({"dwg": dwg, **info, "pixel": list(im.size)})]


@strumento
def dwg_anteprima_pdf(dwg: str, pdf_out: str, zona: str = "", commessa: str = "") -> str:
    """PDF vettoriale 1:1 di un DWG/DXF senza SPAC (da aprire con qualsiasi
    lettore PDF). Piu' DWG separati da ';' = un PDF con una pagina ciascuno.
    zona "x0,y0,x1,y1" mm (vuota = estensione del disegno)."""
    files = [_file(commessa, d.strip()) for d in dwg.split(";") if d.strip()]
    pagine = [render.cad_pdf(f, _zona(zona)) for f in files]
    return _j({"pdf": render.unisci_pdf(pagine, pdf_out), "pagine": len(pagine)})


@strumento
def foglio_leggi(commessa: str, multifoglio: str, foglio: str,
                 limite: int = 500, includi_rev: bool = False,
                 geometria: bool = False) -> str:
    """Legge un foglio come SPAC: testi e simboli con handle, posizione,
    attributi (geometria=True aggiunge linee/cerchi). includi_rev per gli
    snapshot N_REV_xx."""
    return _j(lettura.leggi_foglio(_dwg(commessa, multifoglio), _db(commessa),
                                   multifoglio, foglio, limite, includi_rev, geometria))


@strumento
def foglio_anteprima(commessa: str, multifoglio: str, foglio: str,
                     zona: str = "", png_out: str = "", includi_rev: bool = False,
                     lato_max_px: int = 1600):
    """Immagine del foglio come lo mostra SPAC (zona "x0,y0,x1,y1" in mm)."""
    res = lettura.anteprima_foglio(_dwg(commessa, multifoglio), _db(commessa),
                                   multifoglio, foglio, png_out, 0, includi_rev,
                                   _zona(zona), lato_max_px)
    im = res.pop("_immagine")
    return [_img(im, lato_max_px), _j(res)]


@strumento
def multifoglio_scompatta_dxf(commessa: str, multifoglio: str,
                              cartella_out: str, fogli: str = "",
                              includi_rev: bool = False, dwg: bool = True) -> str:
    """Split geometrico: un file per foglio (DWG con dwg=true, altrimenti DXF)."""
    lst = [f.strip() for f in fogli.split(",") if f.strip()] or None
    return _j(lettura.scompatta_dxf(_dwg(commessa, multifoglio), _db(commessa),
                                    multifoglio, lst, cartella_out, includi_rev, dwg))


@strumento
def dwg_collegamenti(dwg: str, commessa: str = "") -> str:
    """Collegamenti dati Excel + tabelle del DWG, con stato aggiornamento."""
    return _j(lettura.collegamenti_dwg(_file(commessa, dwg)))


# ---------------------------------------------------------------------------
# DWG: modifica e conversione
# ---------------------------------------------------------------------------

@strumento
def dwg_modifica(dwg: str, operazioni: str, out_dwg: str = "",
                 commessa: str = "") -> str:
    """Modifica un DWG con operazioni in batch (JSON), salva DWG nativo.

    operazioni: lista JSON, per esempio
    [{"op":"testo","handle":"2F1","testo":"-KM1"},
     {"op":"testo","handle":"D41","tag":"N","testo":"CAVO A"},   (attributo)
     {"op":"elimina","handle":"3A0"},
     {"op":"sposta","handle":"3A1","dx":5,"dy":0},
     {"op":"aggiungi_testo","testo":"X1","x":100,"y":50,"altezza":2.5,"stile":"ROMANS"},
     {"op":"aggiungi_linea","p0":[0,0],"p1":[50,0],"spessore":0.25},
     {"op":"aggiungi_polilinea","punti":[[0,0],[10,0],[10,5]],"chiusa":false},
     {"op":"aggiungi_cerchio","centro":[20,20],"raggio":1.5},
     {"op":"aggiungi_blocco","nome":"MORSETTO","x":10,"y":10,"attributi":{"N":"1"},
      "da_file":"C:/libreria/simboli.dwg"},
     {"op":"aggiungi_etichetta","testo":"Morsettiera X1","x":120,"y":60,"altezza":2,
      "stile":"ISOCP","punta":[100,70]},     (testo + sottolineatura + richiamo + freccia)
     {"op":"proprieta","handle":"3A2","layer":"TESTI","altezza":3.5},
     {"op":"layer","nome":"3","congela":true}]
    out_dwg vuoto = sovrascrive (backup automatico in %LOCALAPPDATA%\\etcad\\backup).
    Chiudere il file in SPAC prima di sovrascriverlo. Per un posto libero dove mettere
    una scritta: dwg_posto_libero; per la larghezza di un testo: testo_larghezza.
    """
    src = _file(commessa, dwg)
    ops = json.loads(operazioni)
    res = modifica.modifica_dwg(src, ops, out_dwg)
    _vista_in_fondo([(res.get("dwg", ""), _stem(src))], f"dwg_modifica: {len(ops)} operazioni")
    return _j(res)


# ---------------------------------------------------------------------------
# strumenti di lavoro (confronto, sostituzioni, cartiglio, fili, posto libero, copie, consegna)
# ---------------------------------------------------------------------------

def _json_o_vuoto(s: str):
    return json.loads(s) if s and s.strip() else None


def _lista_percorsi(s: str) -> list[str]:
    s = s.strip()
    if s.startswith("["):
        return json.loads(s)
    return [p.strip() for p in re.split(r"[;\n]", s) if p.strip()]


@strumento
def dwg_confronta(a: str, b: str, filtro: str = "*.dwg", commessa: str = "") -> str:
    """Cosa cambia davvero fra due DWG/DXF (o fra due cartelle, file abbinati per nome):
    entita' aggiunte e tolte per spazio modello e per blocco (cornice), testi e attributi
    riscritti (prima/dopo), conteggi per layer (= per foglio in un multifoglio). Serve perche'
    l'MD5 dei DWG cambia a ogni conversione anche a contenuto uguale."""
    from . import strumenti as st
    pa = a if os.path.isdir(a) else _file(commessa, a)
    pb = b if os.path.isdir(b) else _file(commessa, b)
    return _j(st.confronta(pa, pb, filtro))


@strumento
def dwg_sostituisci(percorso: str, cerca: str, sostituisci: str, regex: bool = False, maiuscole: bool = False,
                    parola_intera: bool = False, zona: str = "", layer: str = "", blocchi: bool = True,
                    applica: bool = False, filtro: str = "*.dwg", out_dwg: str = "", commessa: str = "") -> str:
    """Cerca e sostituisci nei testi di un DWG o di tutti i DWG di una cartella: TEXT, MTEXT,
    attributi dei blocchi e testi dentro i blocchi (cornice). Es. "230 cm" -> "232 cm" ovunque,
    o un codice nel riquadro, nel cartiglio e nell'elenco dei fogli. applica=False (predefinito) =
    anteprima con prima/dopo; applica=True scrive DWG nativo con backup. Senza regex "Ø" trova
    anche %%c (e ° / ± i loro codici). zona 'x0,y0,x1,y1' = solo lo spazio modello in quella zona."""
    from . import strumenti as st
    p = percorso if os.path.isdir(percorso) else _file(commessa, percorso)
    res = st.sostituisci(p, cerca, sostituisci, regex, maiuscole, parola_intera, _zona(zona), layer, blocchi,
                         applica, filtro, out_dwg)
    if applica:
        _vista_in_fondo([(f["file"], _stem(f["file"])) for f in res["file"]], f"sostituito «{cerca}» → «{sostituisci}»")
    return _j(res)


@strumento
def dwg_cartiglio(dwg: str, valori: str = "", blocco: str = "", togli_zona: str = "", solo_vuoti: bool = False,
                  applica: bool = False, out_dwg: str = "", commessa: str = "") -> str:
    """Cartiglio e cornice. Senza valori: elenca i blocchi con attributi e i loro valori.
    valori JSON per tag, es. {"DATA":"01/02/2026","DISEGNATO":"A.B."} (sinonimi: DISEGNATO =
    DISEGNATORE = DISEGN...; FOGLIO = NUMERO_DI_FOGLIO...). togli_zona 'x0,y0,x1,y1': elimina
    quello che sta tutto dentro (es. la riga delle revisioni vecchie), anche dentro il blocco
    della cornice. applica=False = anteprima. I testi semplici (non attributi): dwg_sostituisci."""
    from . import strumenti as st
    src = _file(commessa, dwg)
    res = st.cartiglio(src, _json_o_vuoto(valori), blocco, _zona(togli_zona), solo_vuoti, applica, out_dwg)
    if applica:
        _vista_in_fondo([(res.get("dwg", ""), _stem(src))], "cartiglio aggiornato")
    return _j(res)


@strumento
def dwg_controlla(dwg: str, zona: str = "", foglio: str = "", commessa: str = "") -> str:
    """Controllo grafico di un DWG (senza vista): testi sovrapposti, testi toccati da linee
    (anche quelli che escono dalla loro cella), testi doppi. Un multifoglio SPAC si controlla
    foglio per foglio (foglio = numero per uno solo). Euristica: guarda i casi dubbi con
    dwg_anteprima sulla zona indicata."""
    from . import strumenti as st
    return _j(st.controlla(_file(commessa, dwg), _zona(zona), foglio))


@strumento
def dwg_traccia_fili(dwg: str, x: float, y: float, modo: str = "estremi", tolleranza: float = 0.15,
                     layer: str = "", raggio_testi: float = 4.0, commessa: str = "") -> str:
    """Segue un filo partendo dal punto (x, y) in mm: entita' collegate (handle), capi liberi
    con i testi vicini (pin, colori, numeri filo), lunghezza ed estensione. modo "estremi" =
    collegate solo se si toccano agli estremi (schede cavo); "giunzioni" = anche a T (schemi).
    layer: il layer del foglio in un multifoglio. Per verificare che un filo arrivi al pin giusto."""
    from . import strumenti as st
    return _j(st.traccia_fili(_file(commessa, dwg), (x, y), tolleranza, modo, layer, raggio_testi))


@strumento
def dwg_posto_libero(dwg: str, x: float, y: float, testo: str = "", altezza: float = 2.5, stile: str = "ROMANS",
                     larghezza: float = 0.0, raggio: float = 25.0, layer: str = "", controlla_riquadro: str = "",
                     commessa: str = "") -> str:
    """Dove mettere una scritta nuova vicino a (x, y) senza toccare linee o testi: i posti
    liberi piu' vicini con il punto d'inserimento (TEXT allineato a sinistra). La larghezza si
    calcola dal testo nel suo font. controlla_riquadro 'x0,y0,x1,y1': dice solo se e' libero."""
    from . import strumenti as st
    src = _file(commessa, dwg)
    if controlla_riquadro:
        return _j(st.libero(src, _zona(controlla_riquadro), layer))
    return _j(st.posto_libero(src, (x, y), larghezza, altezza, testo, stile, raggio, layer=layer))


@strumento
def dwg_copia_zona(sorgente: str, zona: str, destinazione: str, dx: float = 0.0, dy: float = 0.0, scala: float = 1.0,
                   cornice_sorgente: str = "", cornice_destinazione: str = "", svuota: str = "",
                   layer_sorgente: str = "", layer_destinazione: str = "", applica: bool = False,
                   out_dwg: str = "") -> str:
    """Copia le entita' tutte dentro zona 'x0,y0,x1,y1' di un DWG in un altro (o nello stesso):
    spostate di dx, dy e scalate, oppure allineate dalla cornice_sorgente alla
    cornice_destinazione (riquadri 'x0,y0,x1,y1' delle due cornici: scala e spostamento si
    calcolano da soli, utile fra disegni plottati con cornici diverse). svuota = zona della
    destinazione da liberare prima (sostituisce un disegno). layer_destinazione: per un foglio
    di un multifoglio. applica=False = anteprima di cosa verrebbe copiato."""
    from . import strumenti as st
    res = st.copia_zona(sorgente, _zona(zona), destinazione, dx, dy, scala, _zona(cornice_sorgente),
                        _zona(cornice_destinazione), _zona(svuota), layer_sorgente, layer_destinazione, applica, out_dwg)
    if applica:
        _vista_in_fondo([(res.get("dwg", ""), _stem(destinazione))], f"copiate {res.get('copiate', 0)} entita'")
    return _j(res)


@strumento
def testo_larghezza(testo: str, altezza: float = 2.5, stile: str = "ROMANS", fattore: float = 1.0) -> str:
    """Larghezza in mm di un testo nel suo font (ROMANS/ISOCP/ITALICC con le misure Hershey,
    ARIAL e gli altri TrueType col font vero). Per sapere se una scritta sta in una cella e
    quale fattore di larghezza serve."""
    from . import strumenti as st
    w = st.larghezza_testo(testo, altezza, stile, fattore)
    return _j({"testo": testo, "altezza": altezza, "stile": stile, "fattore": fattore, "larghezza_mm": round(w, 3),
               "fattore_per_10mm": round(fattore * 10 / w, 3) if w else None})


@strumento
def cartella_consegna(sorgente: str, destinazione: str, filtro: str = "*", applica: bool = False,
                      forza: bool = False) -> str:
    """Consegna una cartella di uscita (es. una cartella di rete): copia i file
    nuovi o cambiati, controlla l'MD5 dopo la copia, non cancella nulla. I DWG gia' presenti
    si confrontano per contenuto e si ricopiano solo se cambiati davvero. applica=False = piano."""
    from . import strumenti as st
    return _j(st.consegna(sorgente, destinazione, filtro, True, forza, applica))


@strumento
def cablaggio_distinta(percorsi: str, xlsx_out: str = "", filtro: str = "*.dwg") -> str:
    """Materiali letti dai testi delle schede cavo (uno o piu' DWG o cartelle, separati da ;):
    guaine (diametro, lunghezza), guarnizioni, puntali, contatti, connettori, etichette, cavi
    (sezione, poli, lunghezza). Con xlsx_out scrive l'Excel (righe + riepilogo). Euristica sulle
    parole: va controllata, non sostituisce la distinta di SPAC."""
    from . import strumenti as st
    return _j(st.distinta(_lista_percorsi(percorsi), xlsx_out, filtro))


# ---------------------------------------------------------------------------
# operazioni: cerca, rinumera, unisci, pulisci, versione, tabelle, schede, PDF dei fogli
# ---------------------------------------------------------------------------

@strumento
def dwg_cerca(percorsi: str, testo: str, regex: bool = False, maiuscole: bool = False, parola_intera: bool = False,
              filtro: str = "*", blocchi: bool = True, max_risultati: int = 300) -> str:
    """Cerca un testo in molti DWG/DXF: file o cartelle (anche sottocartelle), separati da ';'. Per ogni
    risultato: file, foglio (multifoglio a layer o fogli affiancati), layer, handle, posizione in mm e se e'
    un testo, un attributo (cartiglio) o un testo dentro un blocco. Es. "in quali disegni compare il cavo
    CAV-M12?", "dove c'e' il morsetto -X2?". Senza regex Ø/°/± trovano anche %%c/%%d/%%p."""
    from . import operazioni as op
    return _j(op.cerca(percorsi, testo, regex, maiuscole, parola_intera, filtro, blocchi, max_risultati))


@strumento
def dwg_rinumera(dwg: str, schema: str = r"\d+", inizio: int = 1, passo: int = 1, formato: str = "",
                 ordine: str = "righe", zona: str = "", layer: str = "", uguali_insieme: bool = True,
                 applica: bool = False, out_dwg: str = "", commessa: str = "") -> str:
    """Rinumera numeri di fili, morsetti o pin: i testi che corrispondono per intero alla regex `schema`
    (es. "\\d{4}" per i numeri filo, "W\\d+", "X2:\\d+") in ordine di lettura, foglio per foglio, per
    "righe" (dall'alto, da sinistra) o "colonne". Con uguali_insieme lo stesso numero vecchio prende lo
    stesso numero nuovo (un filo ha il numero ai due capi). formato es. "W{n}" o "{n:04d}"; vuoto =
    cambia solo le cifre e tiene gli zeri iniziali. zona 'x0,y0,x1,y1'. applica=False = anteprima."""
    from . import operazioni as op
    src = _file(commessa, dwg)
    res = op.rinumera(src, schema, inizio, passo, formato, ordine, _zona(zona), layer, uguali_insieme, applica, out_dwg)
    if res.get("applicato"):
        _vista_in_fondo([(res["dwg"], _stem(src))], f"rinumerati {res['testi_cambiati']} testi")
    return _j(res)


@strumento
def dwg_unisci(file: str, uscita: str, modo: str = "affiancati", distanza: float = 20.0) -> str:
    """Unisce piu' DWG/DXF (separati da ';', o una cartella) in un file solo. modo "affiancati": i disegni
    uno accanto all'altro a `distanza` mm (come un _tutti_i_fogli); modo "layer": tutti all'origine, ogni
    disegno sul layer "1", "2"... (struttura di un multifoglio). I blocchi con lo stesso nome ma
    contenuto diverso (es. due CORNICE) vengono rinominati, cosi' non si mescolano. uscita .dwg o .dxf."""
    from . import operazioni as op
    res = op.unisci(file, uscita, modo, distanza)
    _vista_in_fondo([(res["file"], _stem(res["file"]))], f"uniti {len(res['fogli'])} disegni")
    return _j(res)


@strumento
def dwg_pulisci(dwg: str, doppioni: bool = True, lunghezza_zero: bool = True, inutilizzati: bool = True,
                applica: bool = False, out_dwg: str = "", commessa: str = "") -> str:
    """Pulizia di un DWG: entita' doppie sovrapposte (stessa geometria e layer, tipico dopo copie o
    conversioni), linee e polilinee di lunghezza zero, testi vuoti; con inutilizzati anche blocchi, layer,
    stili di testo e tipi di linea che nessuno usa (come ELIMINA/PURGE). applica=False = elenco di cosa
    sparirebbe; applica=True scrive DWG nativo con backup."""
    from . import operazioni as op
    src = _file(commessa, dwg)
    res = op.pulisci(src, doppioni, lunghezza_zero, inutilizzati, applica, out_dwg)
    if res.get("applicato"):
        _vista_in_fondo([(res["dwg"], _stem(src))], "disegno pulito")
    return _j(res)


@strumento
def dwg_versione(dwg: str, versione: str = "2013", uscita: str = "", commessa: str = "") -> str:
    """Salva un DWG in un'altra versione: 2000, 2004, 2007, 2010, 2013, 2018 (per chi ha un CAD piu'
    vecchio), oppure "dxf". uscita vuota = accanto all'originale con _R<versione>. L'originale non cambia."""
    from . import operazioni as op
    return _j(op.converti_versione(_file(commessa, dwg), versione, uscita))


@strumento
def dwg_tabella(dwg: str, zona: str, xlsx: str = "", commessa: str = "") -> str:
    """Legge una tabella disegnata con linee e testi (distinta, tabella dei pin, lista fogli, legenda) dentro
    la zona 'x0,y0,x1,y1' mm: righe e colonne dalle linee orizzontali e verticali, celle unite comprese
    (ogni riga ha le sue colonne). Con xlsx scrive anche l'Excel. Per la zona: dwg_anteprima o vista."""
    from . import operazioni as op
    return _j(op.leggi_tabella(_file(commessa, dwg), _zona(zona), xlsx))


@strumento
def scheda_cavo_crea(spec: str, uscita: str, tipo: str = "cavo", anteprima: bool = True):
    """Disegna da zero un foglio A3 completo in DWG: tipo "cavo" (scheda di un cavo: connettore circolare
    M8/M12/M23 o morsettiera ai due capi, fili colorati, guaina, schermo, sezione, tabella dei collegamenti,
    distinta, quota, note, cartiglio), "sdoppiatore" (cavo a Y M12 -> 2x M8) o "ingressi" (schema di 8
    sensori PNP su un modulo di ingresso). spec JSON; quello che manca non viene disegnato (connettori
    predefiniti: M12 4 poli a sinistra, morsettiera a destra). Es.:
    {"titolo":"CAVO SENSORE M12 - 4 POLI","codice":"CAV-M12-4P-2000","lunghezza":2000,
     "sinistra":{"tipo":"circolare","sigla":"-XS1","descrizione":"M12 femmina 4 poli","poli":4},
     "destra":{"tipo":"morsetti","sigla":"-X2"},"cavo":"Cavo PUR 4x0,34","sezione":"PUR 4x0,34",
     "fili":[["1","BN","+24 V","1"],["2","WH","uscita 2","2"],["3","BU","0 V","3"],["4","BK","uscita 1","4"]],
     "distinta":[["1","1","Connettore M12 femmina","M12-F4"]],"note":["Puntali isolati"],
     "schermo":"Schermo a 360%%d con pressacavo EMC"}
    fili = [da, colore, funzione, a]; colori BN WH BU BK GY GN YE PK RD VT OG GNYE. Per "ingressi":
    "sensori":[["-B1","Pezzo|in posizione"],...]. Restituisce il DWG, l'anteprima e il controllo grafico."""
    from . import operazioni as op
    res = op.crea_scheda(_json_o_vuoto(spec) or {}, uscita, tipo, anteprima)
    _vista_in_fondo([(res["file"], _stem(res["file"]))], "scheda creata")
    if anteprima and res.get("anteprima"):
        from PIL import Image as PILImage
        return [_img(PILImage.open(res["anteprima"]), 1600), _j(res)]
    return _j(res)


@strumento
def dwg_pdf_fogli(dwg: str, pdf: str = "", commessa: str = "") -> str:
    """PDF vettoriale con una pagina per foglio: multifoglio SPAC o a layer numerati (gli altri fogli spenti,
    la cornice comune resta) oppure fogli affiancati con la cornice (uno per cornice). Senza SPAC; i testi
    SHX disegnati con i glifi Hershey. pdf vuoto = accanto al DWG con _fogli.pdf."""
    from . import operazioni as op
    return _j(op.pdf_fogli(_file(commessa, dwg), pdf))


def _stem(p: str) -> str:
    return os.path.splitext(os.path.basename(p))[0]


@strumento
def dwg_modifica_testi(dwg: str, modifiche: str, out_dwg: str = "",
                       commessa: str = "") -> str:
    """Modifica testi nel DWG (attributi/TEXT/MTEXT per handle).

    modifiche: JSON [{"handle":"D41","tag":"N","testo":"..."}].
    out_dwg vuoto = sovrascrive (backup automatico)."""
    src = _file(commessa, dwg)
    mods = json.loads(modifiche)
    res = lettura.modifica_testi_dwg(src, mods, out_dwg)
    _vista_in_fondo([(res.get("dwg", ""), _stem(src))], f"dwg_modifica_testi: {len(mods)} testi")
    return _j(res)


@strumento
def dwg_elimina(dwg: str, handles: str, out_dwg: str = "", commessa: str = "") -> str:
    """Elimina entita' dal DWG (lista handle JSON). Backup automatico."""
    src = _file(commessa, dwg)
    hs = json.loads(handles)
    res = lettura.elimina_entita_dwg(src, hs, out_dwg)
    _vista_in_fondo([(res.get("dwg", ""), _stem(src))], f"dwg_elimina: {len(hs)} entita'")
    return _j(res)


@strumento
def dwg_layer(dwg: str, layer: str, congela: str = "", colore: int = -1,
              out_dwg: str = "", commessa: str = "") -> str:
    """Congela/scongela ("si"/"no") o ricolora un layer nel DWG."""
    cg = {"si": True, "no": False}.get(congela.strip().lower())
    if congela and cg is None:
        raise ValueError("congela: 'si' o 'no'")
    src = _file(commessa, dwg)
    res = lettura.imposta_layer_dwg(src, layer, cg, colore if colore >= 0 else None, out_dwg)
    _vista_in_fondo([(res.get("dwg", ""), _stem(src))], f"dwg_layer: {layer}")
    return _j(res)


@strumento
def dxf_a_dwg(dxf: str, cartella_out: str = "", versione: str = "2018",
              backend: str = "auto") -> str:
    """Converte DXF in DWG nativo (uno o piu' file separati da ';').

    backend: auto | spac (accoreconsole) | oda (ODA File Converter)."""
    files = [f.strip() for f in dxf.split(";") if f.strip()]
    out = cartella_out or os.path.dirname(os.path.abspath(files[0]))
    return _j(dwg_io.dxf_a_dwg(files, out, versione, backend))


@strumento
def export_batch(dwg: str, script: str, timeout: int = 300, commessa: str = "") -> str:
    """Esegue uno script .scr su un DWG con accoreconsole (SPAC chiuso).

    Righe vuote finali rimosse automaticamente (ripeterebbero l'ultimo
    comando); anteposto EXPERT 5. Chiudere con _.QSAVE se si salva."""
    res = batch.run_script(_file(commessa, dwg), script, timeout=timeout)
    res["stdout"] = res.get("stdout", "")[-2500:]
    return _j(res)


@strumento
def dwg_plot_pdf(dwg: str, pdf_out: str, larghezza_mm: float = 297,
                 altezza_mm: float = 210, x0: float = 0, y0: float = 0,
                 commessa: str = "") -> str:
    """(Solo con SPAC) Stampa il DWG su PDF in scala 1:1 col motore di SPAC
    (font SHX veri), finestra (x0,y0)-(x0+L,y0+H). Poi pdf_confronta_pdf tra
    questo PDF e l'originale = verifica esatta di "identico". Sperimentale."""
    res = batch.run_script(_file(commessa, dwg),
                           batch.script_plot_pdf(pdf_out, larghezza_mm, altezza_mm, x0, y0),
                           timeout=600)
    res["pdf"] = pdf_out if os.path.isfile(pdf_out) else ""
    res["stdout"] = res.get("stdout", "")[-1500:]
    return _j(res)


# ---------------------------------------------------------------------------
# PDF -> DWG con revisione AI
# ---------------------------------------------------------------------------

def _cartella_default(pdf: str) -> str:
    d = os.path.dirname(os.path.abspath(pdf))
    if os.path.basename(d).lower() == "pdf":
        return os.path.join(os.path.dirname(d), "dwg")
    return os.path.join(d, os.path.splitext(os.path.basename(pdf))[0] + "_DWG")


def _rapporto(cartella_out: str, nome_base: str = "") -> tuple[str, dict]:
    import glob
    if nome_base:
        fp = os.path.join(cartella_out, f"{nome_base}_rapporto.json")
    else:
        c = sorted(glob.glob(os.path.join(cartella_out, "*_rapporto.json")),
                   key=os.path.getmtime)
        if not c:
            raise FileNotFoundError(f"nessun rapporto in {cartella_out}: esegui pdf_a_dwg")
        fp = c[-1]
    with open(fp, encoding="utf-8") as f:
        return fp, json.load(f)


def _sintesi(r: dict) -> dict:
    return {"dwg_fogli": [f.get("dwg") for f in r.get("fogli", [])],
            "dwg_combinato": r.get("dwg_combinato", ""),
            "cornice": r.get("cornice"), "testi_totali": r.get("testi_totali"),
            "da_verificare": len(r.get("da_verificare", [])),
            "primi_da_verificare": r.get("da_verificare", [])[:15],
            "scartati_dubbi": len(r.get("scartati_dubbi", [])),
            "conversione": r.get("conversione"), "ocr": r.get("ocr"),
            "rapporto_json": r.get("rapporto_json", "")}


@strumento
def pdf_info(pdf: str) -> str:
    """Pagine, formati, produttore e tipo (vettoriale/raster, testo esploso)."""
    return _j(pdf2dwg.info_pdf(pdf))


@strumento
def pdf_anteprima(pdf: str, pagina: int = 1, zona: str = "", lato_max_px: int = 1600):
    """Immagine di una pagina PDF (o zona "x0,y0,x1,y1" in mm, origine in
    basso a sinistra come nel DWG convertito)."""
    im, info = render.pdf_png(pdf, pagina, _zona(zona), 0, lato_max_px)
    return [_img(im, lato_max_px), _j({"pdf": pdf, "pagina": pagina, **info})]


@mcp.tool(structured_output=False)
async def pdf_a_dwg(pdf: str, cartella_out: str = "", pagine: str = "",
                    nome_base: str = "", cornice: bool = True,
                    attributi_extra: str = "", versione: str = "2018",
                    backend: str = "auto", combinato: bool = True,
                    scala: float = 1.0, ctx: Context | None = None) -> str:
    """Converte un PDF (vettoriale, anche con testi "esplosi" dal plotter
    SPAC/AutoCAD) in DWG apribili da SPAC: un DWG per pagina + uno con tutti
    i fogli affiancati, geometria pulita, testi veri (ROMANS/ISOCP),
    cornice come blocco con attributi.

    cartella_out vuota: cartella "dwg" accanto alla cartella "pdf" (o
    <nome>_DWG). pagine: "1-3,5". attributi_extra: JSON lista di testi fissi
    del cartiglio da rendere attributi (es. ["PROGETTO 01"]).
    Dopo la conversione: pdf_confronta per pagina, pdf_testi per i testi
    dubbi, pdf_correggi per le correzioni (riconverte in pochi secondi).
    """
    out = cartella_out or _cartella_default(pdf)
    extra = json.loads(attributi_extra) if attributi_extra.strip() else []
    nome = nome_base or os.path.splitext(os.path.basename(pdf))[0]
    # correzioni gia' date in una sessione precedente (pdf_correggi)
    stato: dict = {}
    fp = os.path.join(out, f"{nome}_correzioni.json")
    if os.path.isfile(fp):
        with open(fp, encoding="utf-8") as f:
            stato = json.load(f)
        extra = list(dict.fromkeys(extra + stato.get("attributi_extra", [])))

    def avanza(fatto, tot, msg):
        if ctx is not None:
            try:
                anyio.from_thread.run(ctx.report_progress, fatto, tot, msg)
            except Exception:  # noqa: BLE001 - client senza progress
                pass

    r = await anyio.to_thread.run_sync(functools.partial(
        pdf2dwg.converti_pdf, pdf, out, pagine or None, nome, scala, True,
        cornice, extra or None, stato.get("correzioni"), stato.get("testi_aggiunti"),
        stato.get("etichette_glifi"), stato.get("sostituzioni"), versione, backend,
        combinato, avanzamento=avanza, elimina_zone=stato.get("elimina_zone")))
    s = _sintesi(r)
    if stato:
        s["correzioni_applicate"] = fp
    _vista_in_fondo([(f.get("dwg", ""), f"{nome} pag {f.get('pagina')}") for f in r.get("fogli", [])],
                    f"PDF -> DWG: {nome}")
    return _j(s)


@strumento
def pdf_confronta(pdf: str, pagina: int, dwg: str = "", cartella_out: str = "",
                  zona: str = "", modo: str = "sovrapposizione",
                  lato_max_px: int = 1600):
    """Confronta una pagina PDF con il DWG convertito (stessa zona, stessi pixel).

    modo "sovrapposizione": nero = uguale, ROSSO = nel PDF ma non nel DWG,
    CIANO = nel DWG ma non nel PDF; "affiancati": PDF | DWG.
    dwg vuoto: il DWG della pagina nella cartella di pdf_a_dwg.
    Ritorna immagine + somiglianza + zone con differenze (da zoomare con zona).
    """
    if not dwg:
        _, rap = _rapporto(cartella_out or _cartella_default(pdf))
        per_pag = {f["pagina"]: f.get("dwg") for f in rap.get("fogli", [])}
        dwg = per_pag.get(pagina, "")
        if not dwg:
            raise FileNotFoundError(f"DWG della pagina {pagina} non trovato: esegui pdf_a_dwg")
    r = render.confronta(pdf, pagina, dwg, _zona(zona), 0, lato_max_px)
    im = r["overlay"] if modo.startswith("sovr") else render.affianca([r["pdf"], r["cad"]])
    info = {k: r[k] for k in ("somiglianza", "pixel_mancanti", "pixel_in_piu",
                              "zona_mm", "dpi", "zone_diverse")}
    info["dwg"] = dwg
    return [_img(im, lato_max_px if modo.startswith("sovr") else 2 * lato_max_px), _j(info)]


@strumento
def pdf_confronta_pdf(pdf_a: str, pdf_b: str, pagina_a: int = 1, pagina_b: int = 1,
                      zona: str = "", lato_max_px: int = 1600):
    """Sovrappone due PDF (es. originale e DWG stampato da SPAC con
    dwg_plot_pdf): nero = uguale, ROSSO = solo in A, CIANO = solo in B."""
    r = render.confronta_pdf(pdf_a, pagina_a, pdf_b, pagina_b, _zona(zona), 0, lato_max_px)
    info = {k: r[k] for k in ("somiglianza", "pixel_mancanti", "pixel_in_piu",
                              "zona_mm", "dpi", "zone_diverse")}
    return [_img(r["overlay"], lato_max_px), _j(info)]


@strumento
def pdf_testi(pdf: str, cartella_out: str = "", pagina: int = 0, zona: str = "",
              conf_max: float = 1.0, cerca: str = "", limite: int = 400) -> str:
    """Testi ricostruiti dalla conversione (id, testo, posizione, altezza,
    stile, confidenza, fonte) per la revisione. Filtri: pagina, zona mm,
    conf_max (es. 0.8 = solo i dubbi), cerca (sottostringa)."""
    _, rap = _rapporto(cartella_out or _cartella_default(pdf))
    z = _zona(zona)
    out = []
    for t in rap.get("testi", []):
        if pagina and t["pagina"] != pagina:
            continue
        if t.get("conf", 1) > conf_max:
            continue
        if cerca and cerca.lower() not in t["testo"].lower():
            continue
        if z and not (z[0] <= t["x"] <= z[2] and z[1] <= t["y"] <= z[3]):
            continue
        out.append(t)
    scart = [s for s in rap.get("scartati_dubbi", []) if not pagina or s["pagina"] == pagina]
    return _j({"testi": out[:limite], "trovati": len(out),
               "scartati_dubbi": scart[:80],
               "nota": "scartati_dubbi: gruppi di tratti letti come testo ma lasciati "
                       "come geometria; se sono testi veri aggiungili con pdf_correggi."})


@strumento
def pdf_correggi(pdf: str, correzioni: str, cartella_out: str = "") -> str:
    """Applica correzioni alla conversione e riconverte (pochi secondi).

    correzioni: JSON con una o piu' chiavi (si SOMMANO a quelle gia' date):
    {"correzioni": {"p3-s1234": {"testo": "-KM1", "altezza": 2.5,
                                  "stile": "ISOCP", "x": 10, "y": 20,
                                  "rotazione": 90, "elimina": false}},
     "sostituzioni": [{"pagina": 3, "cerca": "SCHEDA_O1", "testo": "SCHEDA_01"}],
     "testi_aggiunti": [{"pagina": 3, "testo": "X1", "x": 100, "y": 50,
                         "altezza": 2.5, "stile": "ROMANS", "rotazione": 0}],
     "etichette_glifi": {"57": "A"},
     "attributi_extra": ["PROGETTO 01"],
     "elimina_zone": [{"pagina": 3, "zona": [10, 20, 15, 25]}],
     "azzera": false}
    Id dei testi: da pdf_testi (anche gli "scartati_dubbi": dando "testo" a un
    gruppo scartato lo si promuove a testo; {"geometria": true} riporta un
    testo a linee). elimina_zone toglie geometria spuria. Glifi: pdf_glifi.
    """
    out = cartella_out or _cartella_default(pdf)
    _, rap = _rapporto(out)
    par = rap.get("parametri", {})
    nome = par.get("nome_base") or os.path.splitext(os.path.basename(pdf))[0]
    fp = os.path.join(out, f"{nome}_correzioni.json")
    stato = {"correzioni": {}, "sostituzioni": [], "testi_aggiunti": [],
             "etichette_glifi": {}, "elimina_zone": [],
             "attributi_extra": list(par.get("attributi_extra", []))}
    if os.path.isfile(fp):
        with open(fp, encoding="utf-8") as f:
            stato.update(json.load(f))
    nuove = json.loads(correzioni)
    if nuove.get("azzera"):
        stato = {"correzioni": {}, "sostituzioni": [], "testi_aggiunti": [],
                 "etichette_glifi": {}, "elimina_zone": [], "attributi_extra": []}
    for k, v in nuove.get("correzioni", {}).items():
        stato["correzioni"].setdefault(k, {}).update(v)
    stato["sostituzioni"] += nuove.get("sostituzioni", [])
    stato["testi_aggiunti"] += nuove.get("testi_aggiunti", [])
    stato.setdefault("elimina_zone", [])
    stato["elimina_zone"] += nuove.get("elimina_zone", [])
    stato["etichette_glifi"].update({str(k): v for k, v in
                                     nuove.get("etichette_glifi", {}).items()})
    for a in nuove.get("attributi_extra", []):
        if a not in stato["attributi_extra"]:
            stato["attributi_extra"].append(a)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(stato, f, ensure_ascii=False, indent=1)
    r = pdf2dwg.converti_pdf(
        par.get("pdf", pdf), out, par.get("pagine"), nome, par.get("scala", 1.0),
        par.get("usa_ocr", True), par.get("cornice", True), stato["attributi_extra"],
        stato["correzioni"], stato["testi_aggiunti"], stato["etichette_glifi"],
        stato["sostituzioni"], par.get("versione", "2018"), par.get("backend", "auto"),
        par.get("combinato", True), elimina_zone=stato.get("elimina_zone"))
    return _j({"correzioni_salvate": fp, **_sintesi(r)})


@strumento
def pdf_glifi(pdf: str, pagine: str = "", tutti: bool = False, max_n: int = 120):
    """Tavola dei glifi riconosciuti (cluster numerati) per etichettarli a
    vista quando l'OCR manca o sbaglia: rispondi con pdf_correggi
    {"etichette_glifi": {"id": "carattere"}}. tutti=false: solo i senza etichetta."""
    pag, gruppi, diz, ms, meta = pdf2dwg.analizza_pdf(pdf, pagine or None)
    im, elenco = pdf2dwg.tavola_glifi(diz, not tutti, max_n)
    return [_img(im, 2000), _j({"cluster_totali": len(diz.cluster),
                                "mostrati": len(elenco), "glifi": elenco})]


# ---------------------------------------------------------------------------
# progettazione
# ---------------------------------------------------------------------------

@strumento
def calcola_cavo(potenza_kw: float, lunghezza_m: float, fasi: int = 3,
                 tensione_v: float = 400.0, cosphi: float = 0.85,
                 posa: str = "B1", isolante: str = "PVC",
                 tamb: float = 30) -> str:
    """Dimensiona un cavo: sezione, portata, protezione, caduta tensione."""
    return _j(progetta.calcola_cavo(potenza_kw, lunghezza_m, fasi, tensione_v, cosphi,
                                    posa, isolante, tamb))


@strumento
def colori_filo(funzione: str) -> str:
    """Colore del filo per funzione (PE, N, potenza, comandi) CEI EN 60204-1."""
    return _j(progetta.colori_filo(funzione))


@strumento
def progetta_foglio(specifica: str, dxf_out: str, png_out: str = "",
                    dwg_out: str = "") -> str:
    """Disegna un foglio da specifica JSON: sbarra, partenze, simboli, cavi.
    dwg_out: salva anche il DWG nativo."""
    res = progetta.progetta_foglio(json.loads(specifica), dxf_out, png_out, dwg_out)
    return _j(res)


@strumento
def distinta_excel(componenti: str, cavi: str, xlsx_out: str,
                   titolo: str = "Distinta") -> str:
    """Genera distinta Excel (Componenti + Cavi) da righe JSON."""
    path = progetta.distinta_excel(json.loads(componenti), json.loads(cavi),
                                   xlsx_out, titolo)
    return _j({"xlsx": path})


# ---------------------------------------------------------------------------
# live (SPAC aperto)
# ---------------------------------------------------------------------------

@strumento
def live_invia_comando(comando: str) -> str:
    """Invia un comando al SPAC APERTO (modalita' live via COM, asincrono)."""
    return _j(live.send_command(comando))


@strumento
def live_apri_dwg(percorso: str) -> str:
    """Apre un DWG nel SPAC APERTO (modalita' live via COM)."""
    return _j(live.open_dwg(percorso))


@strumento
def live_lisp(espressione: str, timeout: float = 30) -> str:
    """Valuta un'espressione AutoLISP nel SPAC APERTO e ne ritorna il valore,
    es. (getvar "DWGNAME") o (sslength (ssget "_X" '((0 . "INSERT"))))."""
    return _j(live.exec_lisp(espressione, timeout))


# ---------------------------------------------------------------------------
# vista dal vivo (finestra stile AutoCAD, server locale 127.0.0.1)
# ---------------------------------------------------------------------------

def _vista_in_fondo(file_e_fogli: list[tuple[str, str]], nota: str = "") -> None:
    """Se la vista e' aperta, mostra i file in un thread (il tool risponde subito)."""
    import threading
    from . import vista
    if not vista.attiva():
        return

    def lavora():
        for k, (percorso, foglio) in enumerate(file_e_fogli):
            if percorso and os.path.isfile(percorso):
                vista.carica(percorso, foglio, nota if k == 0 else "")
    threading.Thread(target=lavora, daemon=True).start()


@strumento
def vista_apri(browser: bool = True) -> str:
    """Apre la vista dal vivo: finestra stile AutoCAD (server locale, nessun
    servizio esterno) dove si vede Claude disegnare: cavi tracciati, oggetti
    spostati, testi riscritti, riga di comando e pannello "Claude sta facendo".
    Dopo l'apertura i tool che modificano DWG (dwg_modifica, dwg_modifica_testi,
    dwg_elimina, dwg_layer, pdf_a_dwg) si vedono in diretta. Restituisce l'URL."""
    from . import vista
    return _j({"url": vista.apri(browser=browser)})


@strumento
def vista_mostra(percorso: str, foglio: str = "", nota: str = "", commessa: str = "",
                 multifoglio: bool = True, agente: str = "") -> str:
    """Mostra un DWG/DXF nella vista dal vivo. Se il foglio (nome) e' gia'
    aperto, anima le differenze rispetto a prima. nota: cosa si sta facendo.
    Un multifoglio SPAC (dbSPAC.db accanto) diventa una scheda per foglio,
    "<multifoglio>/<foglio>"; multifoglio=False lo mostra intero.
    agente: nome dell'agente che lavora (piu' agenti = piu' riquadri)."""
    from . import vista
    if not vista.attiva():
        vista.apri(browser=True)
    return _j(vista.carica(_file(commessa, percorso), foglio, nota, multifoglio=multifoglio and "auto",
                           agente=agente or None))


def _zona_o_none(zona: str):
    z = _zona(zona) if zona else None
    if z is not None and len(z) != 4:
        raise ValueError("zona: 'x0,y0,x1,y1' in mm")
    return z


def _png_file(p: str) -> Image:
    with open(p, "rb") as f:
        return Image(data=f.read(), format="png")


@strumento
def vista_richieste(tutte: bool = False, segna_lette: bool = True):
    """Cosa ha indicato l'utente nella pagina della vista: zone selezionate,
    note su un punto, oggetti scelti, ognuno col suo testo ("sistema qui...").
    Per ogni richiesta: foglio (e multifoglio/numero), zona in mm, testi con
    coordinate e handle del DWG (per dwg_modifica) e l'immagine della zona.
    Rispondi con vista_rispondi quando hai fatto."""
    from . import vista
    rr = vista.richieste(tutte, segna_lette)
    out = []
    for r in rr[-4:]:
        if r.get("png") and os.path.isfile(r["png"]):
            out.append(_png_file(r["png"]))
    out.append(_j([{k: v for k, v in r.items() if k not in ("consegnato",)} for r in rr]))
    return out


@strumento
def vista_rispondi(id_richiesta: int, testo: str, fatta: bool = True, agente: str = "") -> str:
    """Risponde a una richiesta della pagina (compare sotto la richiesta e in chat)."""
    from . import vista
    return _j(vista.rispondi(id_richiesta, testo, fatta, agente or None))


@strumento
def vista_chat(testo: str, agente: str = "") -> str:
    """Scrive nella chat della pagina della vista (come Claude o come l'agente)."""
    from . import vista
    return _j(vista.chat(testo, agente or None))


@strumento
def vista_ascolta(timeout: float = 60.0, agente: str = "") -> str:
    """Aspetta fino a timeout secondi un messaggio della chat della pagina o una
    richiesta nuova (zona/punto/oggetti). agente: solo quelli rivolti a lui."""
    from . import vista
    return _j(vista.ascolta(timeout, agente or None))


@strumento
def vista_evidenzia(foglio: str, zona: str, testo: str = "", agente: str = "") -> str:
    """Indica una zona all'utente nella vista (riquadro con etichetta), es. prima
    di fargli una domanda. zona 'x0,y0,x1,y1' in mm. foglio "" + zona vuota = togli tutte."""
    from . import vista
    if not foglio and not zona:
        vista.pulisci_evidenze()
        return _j({"ok": True})
    return _j(vista.evidenzia(foglio, _zona_o_none(zona), testo, agente or None))


@strumento
def vista_immagine(foglio: str, zona: str = "", pxmm: float = 8.0):
    """Immagine PNG di un foglio (o di una zona 'x0,y0,x1,y1' in mm) come la
    disegna la vista: per controllare a colpo d'occhio una modifica, senza SPAC."""
    from . import vista
    r = vista.immagine(foglio, _zona_o_none(zona), pxmm)
    return [_png_file(r["png"]), _j(r)]


@strumento
def vista_controlla(foglio: str, zona: str = "", mostra: bool = True) -> str:
    """Controllo grafico di un foglio aperto nella vista: testi sovrapposti,
    testi toccati da una linea (anche il testo che esce dalla sua cella), testi
    doppi. mostra=True li evidenzia nella pagina. Euristica: guarda i casi dubbi."""
    from . import vista
    return _j(vista.controlla(foglio, _zona_o_none(zona), mostra))


@strumento
def vista_sfondo(foglio: str, pdf: str = "", pagina: int = 1, dx: float = 0.0, dy: float = 0.0,
                 scala: float = 1.0, opacita: float = 0.45) -> str:
    """Mette la pagina di un PDF sotto un foglio della vista (si accende/spegne nella scheda
    Layer), per confrontare a occhio PDF e DWG dopo pdf_a_dwg. I DWG di pdf2dwg hanno le stesse
    coordinate del PDF (dx=dy=0); per un _tutti_i_fogli usare dx della pagina. pdf vuoto = toglie."""
    from . import vista
    return _j(vista.sfondo(foglio, pdf, pagina, dx, dy, scala, opacita))


@strumento
def vista_cerca(testo: str, foglio: str = "", mostra: bool = False) -> str:
    """Testi che contengono `testo` nei fogli aperti nella vista (foglio: un
    foglio o un multifoglio intero), con coordinate e handle del DWG."""
    from . import vista
    return _j(vista.cerca(testo, foglio, mostra))


@strumento
def vista_osserva(percorso: str, foglio: str = "", filtro: str = "") -> str:
    """Tiene d'occhio un DWG/DXF (o i file di una cartella, filtro es. "*.dwg"):
    a ogni salvataggio, anche da SPAC, la vista anima cosa e' cambiato."""
    from . import vista
    if not vista.attiva():
        vista.apri(browser=True)
    return _j(vista.osserva(percorso, foglio, filtro))


@strumento
def vista_pensa(testo: str, pensiero: bool = True, agente: str = "") -> str:
    """Scrive nel pannello "Claude sta facendo" della vista dal vivo cosa si
    sta facendo o ragionando (pensiero=False: azione, non in corsivo).
    agente: nome dell'agente (ognuno ha il suo colore e il suo riquadro)."""
    from . import vista
    (vista.pensa if pensiero else vista.nota)(testo, agente or None)
    return _j({"ok": vista.attiva()})


# ---------------------------------------------------------------------------
# risorse
# ---------------------------------------------------------------------------

@mcp.resource("spac://norme/riepilogo")
def norme_riepilogo() -> str:
    return (
        "NORME IMPIANTI ELETTRICI (guida operativa, non consulenza legale)\n"
        "CEI EN 60204-1: sicurezza macchine; PE verde/giallo; sezionatori; "
        "numerazione fili; emergenze.\n"
        "IEC 81346: sigle di riferimento -, +, = (prodotto, posizione, funzione).\n"
        "CEI EN 61439-1/2: quadri di potenza e automazione; targhe; prove.\n"
        "CEI EN 61082: preparazione documenti e schemi elettrici.\n"
        "I tool norms_check traducono queste regole in controlli sui dati SPAC."
    )


@mcp.resource("spac://guide/multifoglio")
def guida_multifoglio() -> str:
    return (
        "MULTIFOGLIO SPAC: un .dwg con N fogli nello spazio modello, separati per\n"
        "layer (foglio 01 -> layer 1). Mappatura in dbSPAC.db (PlotSequence).\n"
        "Leggi: fogli_list -> foglio_leggi (handle) -> foglio_anteprima (immagine).\n"
        "Modifica: dwg_modifica con gli handle; poi db_coerenza.\n"
        "Split: multifoglio_scompatta_dxf (un DWG per foglio).\n"
        "Dati: multifoglio_scompatta -> JSON -> multifoglio_ricompatta (nuovo DB)."
    )


@mcp.resource("spac://guide/pdf-dwg")
def guida_pdf_dwg() -> str:
    return (
        "PDF -> DWG IDENTICO (flusso di revisione AI)\n"
        "1. pdf_info: controlla che il PDF sia vettoriale.\n"
        "2. pdf_a_dwg: converte (DWG per pagina + combinato + rapporto).\n"
        "3. Per ogni pagina: pdf_confronta (sovrapposizione). Rosso = manca nel\n"
        "   DWG, ciano = in piu'. Zooma le zone_diverse con zona='x0,y0,x1,y1'.\n"
        "4. pdf_testi con conf_max=0.8: verifica i testi dubbi guardando\n"
        "   pdf_anteprima della stessa zona.\n"
        "5. pdf_correggi: correzioni per id, sostituzioni, testi aggiunti,\n"
        "   etichette glifi; riconverte e ripeti 3-4 finche' identico.\n"
        "6. Modifiche puntuali al DWG finale: dwg_entita + dwg_modifica.\n"
        "Nota: senza SPAC l'anteprima disegna i testi SHX con glifi Hershey;\n"
        "in SPAC i testi usano romans.shx/isocp.shx veri."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
