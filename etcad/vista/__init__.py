"""Vista dal vivo: una finestra stile AutoCAD dove si vede Claude disegnare.

Server locale (127.0.0.1, nessun servizio esterno) + pagina web. Gli script e
i tool MCP mandano lo stato del disegno; la pagina anima le differenze:
cavi tracciati, oggetti spostati, testi riscritti, cancellazioni, e il
pannello "Claude sta facendo" con il ragionamento passo per passo.

Piu' agenti possono lavorare insieme (ognuno con il suo nome e colore, di
solito su fogli diversi dello stesso multifoglio): la pagina li mostra in
riquadri separati o uno alla volta. L'utente risponde dalla pagina: zone
selezionate, note su un punto, oggetti, chat.

Uso dagli script:

    from etcad import vista
    vista.apri()                                   # avvia il server (e il browser)
    vista.carica(r"...\\MULTIFOGLIO.dwg")         # multifoglio: una scheda per foglio
    vista.pensa("Sposto la quota in basso per fare posto ai ponticelli")
    reg = vista.Registro("Foglio 17", lambda: per[18] + cor.entita)   # liste di Ent
    reg.inizio("Stato di partenza")
    ...modifiche...
    reg.passo("Disegno i ponticelli")
    for r in vista.richieste():                    # cosa ha indicato l'utente
        ...
    vista.rispondi(r["id"], "Fatto: quota spostata di 5 mm")

L'agente di un processo si sceglie con agente="..." o con la variabile
d'ambiente ETCAD_VISTA_AGENTE. Tutte le funzioni sono silenziose se la vista
non e' aperta: uno script non si ferma mai per colpa della vista.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

PORTA = int(os.environ.get("ETCAD_VISTA_PORTA", "8765"))


def url() -> str:
    return f"http://127.0.0.1:{PORTA}/"


def _agente(agente: str | None) -> str | None:
    return agente or os.environ.get("ETCAD_VISTA_AGENTE") or None


def _post(azione: str, dati: dict, timeout: float = 30.0, errori: bool = False) -> dict | None:
    corpo = json.dumps(dati, ensure_ascii=False).encode()
    req = urllib.request.Request(url() + "api/" + azione, data=corpo, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        if errori:
            try:
                msg = json.loads(ex.read()).get("errore", str(ex))
            except Exception:  # noqa: BLE001
                msg = str(ex)
            raise RuntimeError(msg) from None
        return None
    except Exception:  # noqa: BLE001 - vista chiusa o lenta: si va avanti
        if errori:
            raise
        return None


def _get(percorso: str, attesa: float = 30.0, **q):
    u = url() + "api/" + percorso
    if q:
        u += "?" + urllib.parse.urlencode({k: v for k, v in q.items() if v not in (None, "")})
    with urllib.request.urlopen(u, timeout=attesa) as r:
        return json.loads(r.read() or b"null")


def attiva(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(url() + "api/stato", timeout=timeout) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def apri(browser: bool = True, attesa: float = 10.0) -> str:
    """Avvia il server se non c'e' gia' (processo staccato) e apre il browser."""
    if not attiva():
        log = os.path.join(tempfile.gettempdir(), "spac_vista.log")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        with open(log, "ab") as f:
            subprocess.Popen([sys.executable, "-m", "etcad.vista", "--porta", str(PORTA), "--no-browser"],
                             stdout=f, stderr=f, stdin=subprocess.DEVNULL, creationflags=flags,
                             close_fds=True)
        fine = time.time() + attesa
        while time.time() < fine and not attiva():
            time.sleep(0.2)
        if not attiva():
            raise RuntimeError(f"La vista non parte: vedi {log}")
    if browser:
        webbrowser.open(url())
    return url()


def chiudi() -> None:
    _post("chiudi", {})


def stato() -> dict | None:
    return _get("stato") if attiva() else None


# -- contenuto -------------------------------------------------------------

def scena(foglio: str, oggetti, nota: str = "", anima: bool = True, agente: str | None = None,
          meta: dict | None = None) -> dict | None:
    """Stato completo di un foglio: lista di oggetti scena, di Ent di pdf2dwg o un documento ezdxf.
    meta: {"doc": nome del multifoglio, "num": numero del foglio, "desc": titolo}."""
    from . import conversione as sc
    if not attiva():
        return None
    if hasattr(oggetti, "modelspace"):
        ogg = sc.da_doc(oggetti)
    else:
        ogg = list(oggetti)
        if ogg and not isinstance(ogg[0], dict):
            ogg = sc.da_ent(ogg)
    return _post("scena", {"foglio": foglio, "oggetti": ogg, "nota": nota, "anima": anima,
                           "agente": _agente(agente), "meta": meta}, timeout=120)


def carica(percorso: str, foglio: str = "", nota: str = "", anima: bool = True, multifoglio="auto",
           db: str = "", agente: str | None = None) -> dict | None:
    """Mostra un DXF/DWG; se il foglio c'e' gia', anima le differenze.
    Multifoglio SPAC (dbSPAC.db accanto, o db=...): una scheda per foglio, "<nome>/<foglio>"."""
    if not attiva():
        return None
    return _post("carica", {"percorso": os.path.abspath(percorso), "foglio": foglio, "nota": nota,
                            "anima": anima, "multifoglio": multifoglio, "db": db, "agente": _agente(agente)},
                 timeout=600)


def osserva(percorso: str, foglio: str = "", filtro: str = "") -> dict | None:
    """Ricarica (e anima) un file, o i file di una cartella, ogni volta che cambiano."""
    if not attiva():
        return None
    return _post("osserva", {"percorso": os.path.abspath(percorso), "foglio": foglio, "filtro": filtro},
                 timeout=600)


def pensa(testo: str, agente: str | None = None) -> None:
    """Ragionamento in corso, nel pannello "Claude sta facendo"."""
    if attiva():
        _post("nota", {"testo": testo, "tipo": "pensiero", "agente": _agente(agente)})


def nota(testo: str, agente: str | None = None) -> None:
    """Azione o informazione (come pensa, ma senza corsivo)."""
    if attiva():
        _post("nota", {"testo": testo, "tipo": "azione", "agente": _agente(agente)})


def comando(testo: str, agente: str | None = None) -> None:
    """Riga libera nella riga di comando."""
    if attiva():
        _post("comando", {"testo": testo, "agente": _agente(agente)})


def fuoco(foglio: str, zona, agente: str | None = None) -> None:
    """Porta la vista sulla zona (x0, y0, x1, y1) in mm."""
    if attiva():
        _post("fuoco", {"foglio": foglio, "zona": list(zona), "agente": _agente(agente)})


def svuota(foglio: str = "") -> None:
    if attiva():
        _post("svuota", {"foglio": foglio} if foglio else {})


def agente(nome: str | None = None, stato: str | None = None, foglio: str | None = None,
           attivita: str | None = None, modello: str | None = None, effort: str | None = None,
           compito: str | None = None) -> dict | None:
    """Registra o aggiorna un agente (stato: "lavora", "attesa", "fatto"; modello es. "claude-sonnet-5-5",
    effort low/medium/high/xhigh/max: la pagina li mostra nella chat dell'agente)."""
    if not attiva():
        return None
    return _post("agente", {"nome": _agente(nome), "stato": stato, "foglio": foglio, "attivita": attivita,
                            "modello": modello, "effort": effort, "compito": compito})


# -- collaborazione con l'utente --------------------------------------------

def richieste(tutte: bool = False, segna: bool = True) -> list[dict]:
    """Zone, punti e oggetti indicati dall'utente nella pagina (con i testi, gli handle
    del DWG e l'immagine PNG della zona). segna=True: diventano "lette"."""
    if not attiva():
        return []
    return _get("richieste", tutte="1" if tutte else "", segna="1" if segna else "0")


def rispondi(id_richiesta: int, testo: str, fatta: bool = True, agente: str | None = None) -> dict | None:
    """Risposta sotto la richiesta (e nella chat della pagina)."""
    if not attiva():
        return None
    return _post("rispondi", {"id": id_richiesta, "testo": testo, "fatta": fatta, "agente": _agente(agente)},
                 errori=True)


def chat(testo: str, agente: str | None = None, rif=None) -> dict | None:
    """Messaggio di Claude (o dell'agente) nella chat della pagina."""
    if not attiva():
        return None
    return _post("chat", {"testo": testo, "da": _agente(agente) or "Claude", "rif": rif})


def ascolta(timeout: float = 25.0, agente: str | None = None, chi: str = "") -> list[dict]:
    """Aspetta (al massimo timeout s) messaggi della chat e richieste nuove dalla pagina.
    agente: solo quelli rivolti a quell'agente (e a tutti)."""
    if not attiva():
        return []
    ag = _agente(agente)
    return _get("chat/attendi", attesa=timeout + 10, chi=chi or ag or "Claude", agente=ag, timeout=timeout)


def evidenzia(foglio: str, zona, testo: str = "", agente: str | None = None) -> dict | None:
    """Riquadro con un'etichetta per indicare all'utente una zona (es. prima di una domanda)."""
    if not attiva():
        return None
    return _post("evidenzia", {"foglio": foglio, "zona": list(zona), "testo": testo, "agente": _agente(agente)})


def pulisci_evidenze(foglio: str = "") -> None:
    if attiva():
        _post("evidenzia", {"pulisci": True, "foglio": foglio or None})


def immagine(foglio: str, zona=None, pxmm: float = 8.0, lato_max: int = 1800) -> dict | None:
    """PNG di una zona del foglio come la disegna la pagina (sfondo bianco). -> {"png": percorso, ...}"""
    if not attiva():
        return None
    return _post("immagine", {"foglio": foglio, "zona": list(zona) if zona else None, "pxmm": pxmm,
                              "max": lato_max}, timeout=30, errori=True)


def controlla(foglio: str, zona=None, mostra: bool = True, agente: str | None = None) -> dict | None:
    """Testi sovrapposti, testi toccati da linee (anche fuori dalla cella), testi doppi."""
    if not attiva():
        return None
    return _post("controlla", {"foglio": foglio, "zona": list(zona) if zona else None, "mostra": mostra,
                               "agente": _agente(agente)}, timeout=120, errori=True)


def sfondo(foglio: str, pdf: str = "", pagina: int = 1, dx: float = 0.0, dy: float = 0.0, scala: float = 1.0,
           opacita: float = 0.45) -> dict | None:
    """Pagina di un PDF sotto il foglio, per confrontare a occhio (pdf vuoto = toglie)."""
    if not attiva():
        return None
    return _post("sfondo", {"foglio": foglio, "pdf": os.path.abspath(pdf) if pdf else "", "pagina": pagina,
                            "dx": dx, "dy": dy, "scala": scala, "opacita": opacita}, timeout=120, errori=True)


def dati_spac(foglio: str, handle: str) -> dict | None:
    """Righe del dbSPAC.db legate a un handle del foglio (multifoglio SPAC aperto nella vista)."""
    if not attiva():
        return None
    return _get("spac", foglio=foglio, hd=handle)


def cerca(testo: str, foglio: str = "", mostra: bool = False) -> list[dict]:
    """Testi che contengono `testo` nei fogli aperti (foglio: un foglio o un multifoglio)."""
    if not attiva():
        return []
    return _post("cerca", {"q": testo, "foglio": foglio, "mostra": mostra}) or []


class Registro:
    """Per gli script che lavorano su liste di Ent (pdf2dwg): a ogni passo manda lo
    stato attuale del foglio e la vista anima cosa e' cambiato."""

    def __init__(self, foglio: str, stato, agente: str | None = None, meta: dict | None = None):
        self.foglio = foglio
        self.stato = stato          # funzione senza argomenti -> lista di Ent
        self.agente = _agente(agente)
        self.meta = meta
        self.acceso = attiva()

    def inizio(self, nota: str = "") -> None:
        if self.acceso:
            scena(self.foglio, self.stato(), nota, anima=False, agente=self.agente, meta=self.meta)

    def passo(self, nota: str = "") -> None:
        if self.acceso:
            scena(self.foglio, self.stato(), nota, anima=True, agente=self.agente, meta=self.meta)
