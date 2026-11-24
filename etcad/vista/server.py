"""Server locale della vista dal vivo (solo 127.0.0.1, nessun servizio esterno).

Il browser riceve gli eventi con Server-Sent Events (/api/eventi); gli script,
i tool MCP e gli agenti mandano scene, note e comandi con POST JSON su
/api/<azione>. Dalla pagina arrivano le richieste dell'utente (zone
selezionate, note su un punto, oggetti) e i messaggi della chat: Claude li
riceve con /api/chat/attendi (vedi `python -m etcad.vista ascolta`).
Solo libreria standard: gira uguale sul PC SPAC.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import itertools
import json
import mimetypes
import os
import queue
import re
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import conversione as scena

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORTA = 8765
AGENTE_BASE = "Claude"
COLORI_AGENTI = ["#5cb8ff", "#ffb057", "#6fd49a", "#f47fa8", "#b892ff", "#4fd1c5", "#ff8a7a", "#d4e157"]
ASCOLTO_GRAZIA = 12.0       # s: fra due attese dello stesso ascoltatore resta "in ascolto"


def cartella_dati() -> str:
    """Richieste e chat restano qui fra un avvio e l'altro (solo su questo PC)."""
    d = os.environ.get("ETCAD_VISTA_DATI") or os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "etcad", "vista")
    os.makedirs(d, exist_ok=True)
    return d


MODELLI = {"fable": "Fable", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}


def nome_modello(mid: str | None) -> str:
    """claude-opus-5-5 -> "Opus 5.5" (id sconosciuti restano come sono)."""
    m = re.match(r"claude-([a-z]+)-(\d+)-(\d+)", str(mid or ""))
    return f"{MODELLI.get(m.group(1), m.group(1).title())} {m.group(2)}.{m.group(3)}" if m else str(mid or "")


def _cartella_sessioni() -> str:
    """Dove Claude Code tiene i registri delle sessioni: ETCAD_VISTA_SESSIONI (una cartella di progetto) oppure
    ~/.claude/projects (si guarda la sessione scritta per ultima, cioe' quella che sta lavorando)."""
    if os.environ.get("ETCAD_VISTA_SESSIONI"):
        return os.environ["ETCAD_VISTA_SESSIONI"]
    conf = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(conf, "projects")


_CACHE_SESSIONE: dict = {}


def modello_sessione(cartella: str | None = None) -> dict | None:
    """Modello ed effort con cui sta rispondendo la sessione di Claude Code del progetto: l'app li scrive in ogni
    risposta del registro della sessione (.jsonl). Si legge solo la coda del file piu' recente; niente esce dal PC."""
    cart = cartella or _cartella_sessioni()
    try:
        file = max(glob.glob(os.path.join(cart, "*.jsonl")) + glob.glob(os.path.join(cart, "*", "*.jsonl")),
                   key=os.path.getmtime)
        st = os.stat(file)
    except (OSError, ValueError):
        return None
    chiave = (file, st.st_mtime, st.st_size)
    if _CACHE_SESSIONE.get("chiave") == chiave:
        return _CACHE_SESSIONE.get("valore")
    with open(file, "rb") as fh:
        fh.seek(max(0, st.st_size - 400_000))
        righe = fh.read().decode("utf-8", "replace").splitlines()
    val = _CACHE_SESSIONE.get("valore")
    for r in reversed(righe):
        if '"assistant"' not in r or '"model"' not in r:
            continue
        try:
            d = json.loads(r)
        except ValueError:
            continue
        mod = (d.get("message") or {}).get("model")
        if d.get("type") != "assistant" or d.get("isSidechain") or not mod or mod.startswith("<"):
            continue
        val = {"modello": mod, "nome": nome_modello(mod), "effort": d.get("effort") or d.get("perTurnEffort"),
               "sessione": os.path.splitext(os.path.basename(file))[0]}
        break
    _CACHE_SESSIONE.update(chiave=chiave, valore=val)
    return val


def _png(s) -> bytes | None:
    if not s:
        return None
    m = re.match(r"data:image/\w+;base64,(.*)$", s, re.S)
    try:
        return base64.b64decode(m.group(1) if m else s)
    except (ValueError, TypeError):
        return None


def _nome_file(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s)[:60]


class Stato:
    def __init__(self):
        self.lock = threading.RLock()
        self.cambio = threading.Condition(self.lock)    # chat e richieste nuove per chi ascolta
        self.fogli: dict[str, dict] = {}
        self.attivo: str | None = None
        self.clienti: list[queue.Queue] = []
        self.storia: deque = deque(maxlen=400)
        self.immagini: dict[str, str] = {}
        self.osservati: dict[str, dict] = {}
        self.seq = itertools.count(1)
        self.idn = itertools.count(1)                   # id di richieste, messaggi, evidenze
        self._glifi = None
        self.agenti: dict[str, dict] = {}
        self.richieste: list[dict] = []
        self.chat: list[dict] = []
        self.evidenze: list[dict] = []
        self.ascolto: dict[str, dict] = {}
        self._ascolto_pub: dict = {}
        self.attese_img: dict[str, dict] = {}
        self.dati_letti = False
        self.sessione: dict | None = None

    # -- persistenza ------------------------------------------------------
    def carica_dati(self):
        with self.lock:
            if self.dati_letti:
                return
            self.dati_letti = True
            f = os.path.join(cartella_dati(), "stato.json")
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                d = {}
            self.richieste = d.get("richieste", [])
            self.chat = d.get("chat", [])
            mx = max([x["id"] for x in self.richieste + self.chat] or [0])
            self.idn = itertools.count(mx + 1)

    def _salva(self):
        d = {"richieste": self.richieste[-300:], "chat": self.chat[-500:]}
        f = os.path.join(cartella_dati(), "stato.json")
        tmp = f + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False)
            os.replace(tmp, f)
        except OSError:
            pass

    # -- invio ------------------------------------------------------------
    def invia(self, ev: dict):
        ev["n"] = next(self.seq)
        ev["ora"] = time.strftime("%H:%M:%S")
        dati = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            if ev["tipo"] in ("nota", "comando"):
                self.storia.append(ev)
            for q in list(self.clienti):
                q.put(dati)

    def _immagini(self, oggetti: list[dict]):
        for o in oggetti:
            img = o.get("img")
            if img and img.get("file"):
                k = hashlib.md5(img["file"].encode()).hexdigest()[:12]
                self.immagini[k] = img["file"]
                img["src"] = f"/api/img/{k}"

    # -- agenti -----------------------------------------------------------
    def agente(self, nome: str | None = None, **kw) -> dict:
        nome = (nome or AGENTE_BASE).strip() or AGENTE_BASE
        with self.lock:
            a = self.agenti.get(nome)
            cambiato = a is None
            if a is None:
                a = self.agenti[nome] = {"nome": nome, "colore": COLORI_AGENTI[len(self.agenti) % len(COLORI_AGENTI)],
                                         "foglio": None, "attivita": "", "stato": "attesa", "fogli": [],
                                         "modello": None, "effort": None, "compito": ""}
            for k in ("foglio", "attivita", "stato", "colore", "modello", "effort", "compito"):
                v = kw.get(k)
                if v is not None and a.get(k) != v:
                    a[k] = v
                    cambiato = True
            f = kw.get("foglio")
            if f and f not in a["fogli"]:
                a["fogli"].append(f)
                cambiato = True
            a["ultimo"] = time.time()
            if cambiato:
                self.invia({"tipo": "agente", "agente": dict(a)})
            return dict(a)

    # -- disegni ----------------------------------------------------------
    def scena(self, foglio: str, oggetti: list[dict], nota: str = "", anima: bool = True,
              fonte: str | None = None, meta: dict | None = None, agente: str | None = None,
              attiva: bool | None = None) -> dict:
        self._immagini(oggetti)
        ag = self.agente(agente, foglio=foglio, stato="lavora")["nome"]
        with self.lock:      # tutto sotto lock: osservatore e richieste HTTP non si accavallano
            prec = self.fogli.get(foglio)
            m = dict((prec or {}).get("meta") or {})
            m.update(meta or {})
            m.setdefault("doc", foglio)
            self.fogli[foglio] = {"oggetti": oggetti, "fonte": fonte or (prec or {}).get("fonte"), "meta": m,
                                  "sfondo": (prec or {}).get("sfondo")}
            if self.attivo is None:
                self.attivo = foglio
            base = {"foglio": foglio, "meta": m, "agente": ag, "nota": nota}
            if prec is None or not anima:
                self.invia({"tipo": "foglio", **base, "oggetti": oggetti, "anima": bool(anima and prec is None),
                            "attiva": prec is None if attiva is None else attiva})
                return {"foglio": foglio, "oggetti": len(oggetti), "nuovo": prec is None}
            d = scena.confronta(prec["oggetti"], oggetti)
            if not any(d.values()):
                if nota:
                    self.invia({"tipo": "nota", "testo": nota, "genere": "azione", "agente": ag, "foglio": foglio})
                return {"foglio": foglio, "aggiunti": 0, "tolti": 0, "spostati": 0, "modificati": 0, "invariato": True}
            self.invia({"tipo": "passo", **base, **d, "totale": len(oggetti)})
        return {"foglio": foglio, "aggiunti": len(d["aggiunti"]), "tolti": len(d["tolti"]),
                "spostati": len(d["spostati"]), "modificati": len(d["modificati"])}

    def carica(self, percorso: str, foglio: str = "", nota: str = "", anima: bool = True,
               multifoglio="auto", db: str = "", agente: str | None = None) -> dict:
        """Un DXF/DWG. Se e' un multifoglio SPAC (o ha piu' fogli) ogni foglio diventa una scheda."""
        p = os.path.abspath(percorso)
        stem = os.path.splitext(os.path.basename(p))[0]
        if foglio or multifoglio in (False, "no", "0", 0):
            ogg = scena.da_file(p)
        else:
            doc = scena.leggi_doc(p)
            r = scena.fogli_doc(doc, p, db)
            if r and (len(r[1]) >= 2 or r[0] == "spac"):
                return self._carica_fogli(p, stem, r[0], r[1], nota, anima, agente)
            ogg = scena.da_doc(doc, cartella=os.path.dirname(p))
        nome = foglio or stem
        return self.scena(nome, ogg, nota, anima, fonte=p, meta={"doc": nome, "tipo": "singolo", "file": p}, agente=agente)

    def _carica_fogli(self, p, stem, tipo, fogli, nota, anima, agente) -> dict:
        with self.lock:
            primo = not any(v["meta"].get("doc") == stem for v in self.fogli.values())
            vecchi = {k for k, v in self.fogli.items() if v["meta"].get("doc") == stem}
        res, nuovi, resto_nota = [], set(), nota
        esclusi = set()
        if tipo in ("spac", "layer"):           # fogli deselezionati nell'ultima stampa (CFG\DCL_CFG.INI)
            try:
                from ..commessa_cfg import fogli_esclusi_stampa
                esclusi = {str(x) for x in fogli_esclusi_stampa(p, stem)}
            except Exception:  # noqa: BLE001 - CFG assente o illeggibile: nessun segno
                esclusi = set()
        for k, f in enumerate(fogli):
            chiave = f"{stem}/{f['foglio']}"
            nuovi.add(chiave)
            meta = {"doc": stem, "num": f["foglio"], "desc": f.get("descrizione", ""), "layer": f.get("layer"),
                    "zona": f.get("zona"), "ordine": k, "tipo": tipo, "n_fogli": len(fogli), "db": f.get("db"), "file": p,
                    "stampa": str(f["foglio"]) not in esclusi}
            r = self.scena(chiave, f["oggetti"], resto_nota, anima=anima and not primo, fonte=p, meta=meta,
                           agente=agente, attiva=primo and k == 0)
            if not r.get("invariato"):
                resto_nota = ""
            res.append({"foglio": chiave, "num": f["foglio"], "descrizione": f.get("descrizione", ""),
                        **{x: r[x] for x in ("oggetti", "aggiunti", "tolti", "spostati", "modificati") if x in r}})
        for k in sorted(vecchi - nuovi):                       # fogli tolti dal multifoglio
            self.svuota(k)
        return {"multifoglio": stem, "tipo": tipo, "fogli": res}

    def salva_bozza(self, foglio: str, ops: list[dict], copia: bool = False) -> dict:
        """Modifiche disegnate dall'utente nella pagina -> DWG nativo (backup dell'originale), poi ricarica.

        copia=True scrive <nome>_modificato.dwg accanto all'originale e lo apre in una scheda nuova.
        """
        from ..lettura import _riedita_dwg
        from . import bozza
        with self.lock:
            fo = self.fogli.get(foglio)
            if fo is None:
                raise KeyError(foglio)
            p, meta = fo.get("fonte"), dict(fo.get("meta") or {})
        if not ops:
            raise ValueError("nessuna modifica da salvare")
        if not p or not os.path.isfile(p) or not p.lower().endswith((".dwg", ".dxf")):
            raise ValueError("questo foglio non viene da un file DWG/DXF (e' solo nella vista): non c'e' niente da salvare")
        layer_def = str(meta["layer"]) if meta.get("tipo") in ("spac", "layer") and meta.get("layer") else "0"
        out = ""
        if copia:
            base, ext = os.path.splitext(p)
            out, i = f"{base}_modificato{ext}", 2
            while os.path.exists(out):
                out, i = f"{base}_modificato{i}{ext}", i + 1
        r = _riedita_dwg(p, lambda doc: bozza.applica(doc, ops, layer_def), out)
        finale = r["dwg"]
        nota = f"Modifiche dell'utente salvate in {os.path.basename(finale)} ({len(ops)} operazioni)"
        stem = os.path.splitext(os.path.basename(p))[0]
        if meta.get("tipo") == "singolo" and not copia and foglio != stem:
            self.carica(finale, foglio, nota=nota, anima=False)
        else:
            self.carica(finale, nota=nota, anima=False)
        if finale in self.osservati:            # l'osservatore non lo ricarica una seconda volta
            st = os.stat(finale)
            self.osservati[finale]["firma"] = (st.st_mtime, st.st_size)
        return {"ok": True, "file": finale, "copia": bool(copia), "backup": r.get("backup", ""), "n": r["n"],
                "dettaglio": r["dettaglio"]}

    def svuota(self, foglio: str = ""):
        with self.lock:
            if foglio:
                self.fogli.pop(foglio, None)
                self.evidenze = [e for e in self.evidenze if e.get("foglio") != foglio]
            else:
                self.fogli.clear()
                self.storia.clear()
                self.evidenze.clear()
                self.attivo = None
        self.invia({"tipo": "svuota", "foglio": foglio or None})

    def osserva(self, percorso: str, foglio: str = "", filtro: str = "") -> dict:
        p = os.path.abspath(percorso)
        file = sorted(glob.glob(os.path.join(p, filtro or "*.dxf"))) if os.path.isdir(p) else [p]
        for f in file:
            st = os.stat(f)
            nome = foglio if (foglio and len(file) == 1) else ""
            stem = os.path.splitext(os.path.basename(f))[0]
            if not any(v.get("fonte") == f for v in self.fogli.values()) and (nome or stem) not in self.fogli:
                self.carica(f, nome, anima=False)
            self.osservati[f] = {"foglio": nome, "firma": (st.st_mtime, st.st_size)}
        if os.path.isdir(p):
            self.osservati[p] = {"cartella": True, "filtro": filtro or "*.dxf"}
        return {"osservati": [os.path.basename(f) for f in file]}

    def _sessione(self):
        try:
            s = modello_sessione()
        except Exception:  # noqa: BLE001 - registro illeggibile: si riprova al giro dopo
            return
        if s and s != self.sessione:
            self.sessione = s
            self.invia({"tipo": "sessione", "sessione": s})

    def _giro(self):
        n = 0
        while True:
            time.sleep(0.6)
            self._controlla_ascolto()
            n += 1
            if n % 5 == 1:
                self._sessione()
            for p, info in list(self.osservati.items()):
                try:
                    if info.get("cartella"):
                        for f in glob.glob(os.path.join(p, info["filtro"])):
                            if f not in self.osservati:
                                self.osservati[f] = {"foglio": "", "firma": None}
                        continue
                    st = os.stat(p)
                    fi = (st.st_mtime, st.st_size)
                    if fi == info["firma"]:
                        continue
                    if fi != info.get("vista"):            # aspetta che il file smetta di cambiare
                        info["vista"] = fi
                        continue
                    info["firma"] = fi
                    self.carica(p, info.get("foglio", ""), nota=f"File aggiornato: {os.path.basename(p)}")
                except Exception as ex:  # noqa: BLE001
                    info["errore"] = str(ex)

    # -- richieste dell'utente (zone, punti, oggetti) -----------------------
    def _richiesta_pub(self, r: dict) -> dict:
        c = r.get("contenuto") or {}
        return {k: r.get(k) for k in ("id", "ora", "tipo", "foglio", "zona", "punto", "testo", "stato",
                                      "risposta", "agente")} | {
            "num": (r.get("meta") or {}).get("num"), "desc": (r.get("meta") or {}).get("desc"),
            "doc": (r.get("meta") or {}).get("doc"), "n_oggetti": c.get("oggetti", 0),
            "n_schizzo": len(r.get("schizzo") or []),
            "img": f"/api/richieste/{r['id']}.png" if r.get("png") else ""}

    def _annuncia_richieste(self):
        self.invia({"tipo": "richieste", "richieste": [self._richiesta_pub(r) for r in self.richieste[-120:]]})

    def nuova_richiesta(self, d: dict) -> dict:
        with self.lock:
            rid = next(self.idn)
            fo = self.fogli.get(d.get("foglio")) or {}
            ogg = fo.get("oggetti", [])
            zona, punto, ids = d.get("zona"), d.get("punto"), d.get("ids") or None
            if punto and not zona:
                zona = [punto[0] - 4, punto[1] - 4, punto[0] + 4, punto[1] + 4]
            cont = scena.contenuto(ogg, zona=zona, ids=ids) if ogg and (zona or ids) else {}
            if ids and cont.get("estensione") and not d.get("zona"):
                zona = cont["estensione"]
            r = {"id": rid, "ora": time.strftime("%Y-%m-%d %H:%M:%S"), "tipo": d.get("tipo", "area"),
                 "foglio": d.get("foglio"), "meta": fo.get("meta", {}), "fonte": fo.get("fonte"),
                 "zona": zona, "punto": punto, "ids": ids, "testo": (d.get("testo") or "").strip(),
                 "agente": d.get("agente") or None, "stato": "nuova", "risposta": "", "contenuto": cont}
            if d.get("schizzo"):            # segni disegnati dall'utente per Claude (non vanno nel DWG)
                r["schizzo"] = d["schizzo"][:500]
            png = _png(d.get("png"))
            if png:
                cart = os.path.join(cartella_dati(), "richieste")
                os.makedirs(cart, exist_ok=True)
                r["png"] = os.path.join(cart, f"{rid}.png")
                with open(r["png"], "wb") as fh:
                    fh.write(png)
            self.richieste.append(r)
            del self.richieste[:-300]
            self._salva()
            self.cambio.notify_all()
        self._annuncia_richieste()
        return r

    def elenco_richieste(self, tutte: bool = False, segna: bool = True) -> list[dict]:
        with self.lock:
            out = [r for r in self.richieste if tutte or r["stato"] == "nuova"]
            if segna:
                cambiate = [r for r in out if r["stato"] == "nuova"]
                for r in cambiate:
                    r["stato"] = "letta"
                if cambiate:
                    self._salva()
            out = [dict(r) for r in out]
        if segna and out:
            self._annuncia_richieste()
        return out

    def rispondi(self, rid: int, testo: str, fatta: bool = True, agente: str | None = None) -> dict:
        with self.lock:
            r = next((x for x in self.richieste if x["id"] == int(rid)), None)
            if r is None:
                raise KeyError(f"richiesta {rid} non trovata")
            r["risposta"] = testo
            r["stato"] = "fatta" if fatta else "letta"
            self._salva()
        self._annuncia_richieste()
        if testo:
            self.messaggio(testo, da=agente or AGENTE_BASE, rif=int(rid))
        return self._richiesta_pub(r)

    def togli_richiesta(self, rid: int):
        with self.lock:
            self.richieste = [r for r in self.richieste if r["id"] != int(rid)]
            self._salva()
        self._annuncia_richieste()

    # -- chat ---------------------------------------------------------------
    def messaggio(self, testo: str, da: str = "utente", a: str | None = None, rif=None) -> dict:
        with self.lock:
            m = {"id": next(self.idn), "ora": time.strftime("%H:%M:%S"), "data": time.strftime("%Y-%m-%d"),
                 "da": da or "utente", "a": a or None, "testo": testo, "rif": rif}
            self.chat.append(m)
            del self.chat[:-500]
            self._salva()
            self.cambio.notify_all()
        if m["da"] != "utente":
            self.agente(m["da"])
        self.invia({"tipo": "chat", "messaggio": m})
        return m

    def _da_consegnare(self, chi: str, agente: str | None) -> list[tuple[str, dict]]:
        out = []
        for m in self.chat:
            if m["da"] != "utente" or chi in m.get("consegnato", []):
                continue
            if agente and m.get("a") not in (None, "", agente):
                continue
            out.append(("chat", m))
        for r in self.richieste:
            if chi in r.get("consegnato", []) or r["stato"] not in ("nuova",):
                continue
            if agente and r.get("agente") not in (None, "", agente):
                continue
            out.append(("richiesta", r))
        return sorted(out, key=lambda t: t[1]["id"])

    def attendi(self, chi: str = AGENTE_BASE, agente: str | None = None, timeout: float = 25.0) -> list[dict]:
        """Messaggi della chat e richieste nuove per chi ascolta (aspetta fino a timeout s)."""
        fine = time.time() + max(0.0, timeout)
        with self.cambio:
            self.ascolto[chi] = {"agente": agente, "attivo": True, "fine": None}
            self._controlla_ascolto()
            try:
                while True:
                    ev = self._da_consegnare(chi, agente)
                    resto = fine - time.time()
                    if ev or resto <= 0:
                        break
                    self.cambio.wait(min(5.0, resto))
                for tipo, x in ev:
                    x.setdefault("consegnato", []).append(chi)
                    if tipo == "richiesta" and x["stato"] == "nuova":
                        x["stato"] = "letta"
                if ev:
                    self._salva()
            finally:
                self.ascolto[chi].update(attivo=False, fine=time.time())
        if any(t == "richiesta" for t, _ in ev):
            self._annuncia_richieste()
        out = []
        for tipo, x in ev:
            if tipo == "chat":
                out.append({"tipo": "chat", "id": x["id"], "ora": x["ora"], "a": x.get("a"), "testo": x["testo"],
                            "rif": x.get("rif")})
            else:
                out.append({**{k: v for k, v in x.items() if k != "consegnato"}, "tipo": "richiesta", "genere": x["tipo"]})
        return out

    def _controlla_ascolto(self):
        ora = time.time()
        with self.lock:
            pub = {}
            for chi, a in self.ascolto.items():
                vivo = a["attivo"] or (a["fine"] and ora - a["fine"] < ASCOLTO_GRAZIA)
                if vivo:
                    pub[chi] = a.get("agente") or chi
            if pub != self._ascolto_pub:
                self._ascolto_pub = pub
                self.invia({"tipo": "ascolto", "ascolto": pub})

    # -- evidenze di Claude -------------------------------------------------
    def evidenzia(self, d: dict) -> dict:
        with self.lock:
            nuova = None
            if d.get("pulisci"):
                f = d.get("foglio")
                self.evidenze = [e for e in self.evidenze if f and e["foglio"] != f]
            elif d.get("via"):
                self.evidenze = [e for e in self.evidenze if e["id"] != int(d["via"])]
            else:
                ag = self.agente(d.get("agente"))
                nuova = {"id": next(self.idn), "foglio": d["foglio"], "zona": d["zona"],
                         "testo": d.get("testo", ""), "agente": ag["nome"], "colore": ag["colore"]}
                self.evidenze.append(nuova)
                del self.evidenze[:-60]
            ev = {"tipo": "evidenze", "evidenze": list(self.evidenze), "nuova": nuova and nuova["id"]}
        self.invia(ev)
        return {"ok": True, "evidenza": nuova}

    # -- immagini disegnate dalla pagina ---------------------------------------
    def immagine(self, foglio: str, zona=None, pxmm: float = 8.0, lato_max: int = 1800,
                 timeout: float = 15.0) -> dict:
        with self.lock:
            fo = self.fogli.get(foglio)
            if fo is None:
                raise KeyError(f"foglio non aperto nella vista: {foglio}")
            ogg = fo["oggetti"]
            if not zona:
                b = None
                for o in ogg:
                    if o["b"][2] > o["b"][0] or o["b"][3] > o["b"][1]:
                        b = o["b"] if b is None else [min(b[0], o["b"][0]), min(b[1], o["b"][1]),
                                                      max(b[2], o["b"][2]), max(b[3], o["b"][3])]
                zona = (fo["meta"].get("zona") or b or [0, 0, 420, 297])
            zona = [float(v) for v in zona]
            m = 0.02 * max(zona[2] - zona[0], zona[3] - zona[1])
            vicini = [o for o in ogg if scena._tocca(o["b"], [zona[0] - m, zona[1] - m, zona[2] + m, zona[3] + m])]
            if not self.clienti:
                raise RuntimeError("nessuna pagina della vista aperta: l'immagine la disegna il browser")
            rid = uuid.uuid4().hex[:12]
            att = {"ev": threading.Event(), "png": None}
            self.attese_img[rid] = att
        self.invia({"tipo": "rendi", "rid": rid, "foglio": foglio, "zona": zona, "pxmm": pxmm, "max": lato_max,
                    "oggetti": vicini})
        try:
            if not att["ev"].wait(timeout):
                raise TimeoutError("la pagina della vista non ha risposto")
        finally:
            self.attese_img.pop(rid, None)
        cart = os.path.join(tempfile.gettempdir(), "spac_vista")
        os.makedirs(cart, exist_ok=True)
        f = os.path.join(cart, f"{_nome_file(foglio)}_{rid}.png")
        with open(f, "wb") as fh:
            fh.write(att["png"])
        return {"png": f, "foglio": foglio, "zona": [round(v, 2) for v in zona], "byte": len(att["png"])}

    def immagine_pronta(self, rid: str, png) -> bool:
        att = self.attese_img.get(rid)
        b = _png(png)
        if att is None or not b or att["ev"].is_set():
            return False
        att["png"] = b
        att["ev"].set()
        return True

    # -- sfondo PDF e dati SPAC ---------------------------------------------------
    def sfondo(self, foglio: str, pdf: str = "", pagina: int = 1, dx: float = 0.0, dy: float = 0.0,
               scala: float = 1.0, opacita: float = 0.45, pxmm: float = 6.0) -> dict:
        """Pagina di un PDF sotto il foglio (per confrontare a occhio dopo PDF -> DWG).
        Il PDF e' in mm con l'origine in basso a sinistra, come i DWG di pdf2dwg: dx/dy/scala
        servono per i fogli affiancati o plottati con un'altra origine. pdf vuoto = toglie."""
        with self.lock:
            if foglio not in self.fogli:
                raise KeyError(f"foglio non aperto nella vista: {foglio}")
        info = None
        if pdf:
            import fitz
            d = fitz.open(pdf)
            try:
                p = d[int(pagina) - 1]
                w, h = p.rect.width * 25.4 / 72, p.rect.height * 25.4 / 72
                k = max(0.5, min(pxmm, 9000 / max(w, h))) * 25.4 / 72
                pix = p.get_pixmap(matrix=fitz.Matrix(k, k), alpha=False)
                cart = os.path.join(tempfile.gettempdir(), "spac_vista")
                os.makedirs(cart, exist_ok=True)
                f = os.path.join(cart, f"sfondo_{hashlib.md5(f'{pdf}|{pagina}|{pxmm}'.encode()).hexdigest()[:12]}.png")
                pix.save(f)
            finally:
                d.close()
            k = hashlib.md5(f.encode()).hexdigest()[:12]
            self.immagini[k] = f
            info = {"src": f"/api/img/{k}?v={int(time.time())}", "q": [dx, dy, dx + w * scala, dy + h * scala],
                    "opacita": opacita, "pdf": os.path.basename(pdf), "pagina": int(pagina)}
        with self.lock:
            self.fogli[foglio]["sfondo"] = info
        self.invia({"tipo": "sfondo", "foglio": foglio, "sfondo": info})
        return {"foglio": foglio, "sfondo": info}

    def dati_spac(self, foglio: str, hd: str) -> dict:
        """Righe del dbSPAC.db legate all'handle (Elementi, MorsConn, Rimandi...): sigla, codici."""
        import sqlite3
        from ..multifoglio import TABLES_DWG_FOGLIO
        fo = self.fogli.get(foglio) or {}
        m = fo.get("meta") or {}
        db, nome = m.get("db"), m.get("doc")
        if not db or not hd or not os.path.isfile(db):
            return {"db": None}
        out = {}
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            presenti = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in TABLES_DWG_FOGLIO:
                if t not in presenti:
                    continue
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
                hc = next((c for c in cols if c.lower() == "handle"), None)
                dc = next((c for c in cols if c.lower() == "dwg"), None)
                if not hc:
                    continue
                sql = f'SELECT * FROM "{t}" WHERE "{hc}"=?' + (f' AND "{dc}"=?' if dc else "") + " LIMIT 20"
                righe = con.execute(sql, (hd, nome) if dc else (hd,)).fetchall()
                if righe:
                    out[t] = [{c: (f"<{len(v)} byte>" if isinstance(v, bytes) else v) for c, v in zip(cols, r)
                               if v not in (None, "")} for r in righe]
        finally:
            con.close()
        return {"db": db, "multifoglio": nome, "handle": hd, "tabelle": out}

    # -- analisi --------------------------------------------------------------
    def controlla(self, foglio: str, zona=None, mostra: bool = True, agente: str | None = None) -> dict:
        with self.lock:
            fo = self.fogli.get(foglio)
            if fo is None:
                raise KeyError(f"foglio non aperto nella vista: {foglio}")
            ogg = fo["oggetti"]
        pr = scena.controlla(ogg, zona)
        if mostra:
            self.risultati(f"Controllo sovrapposizioni: {len(pr)} da guardare" if pr else
                           "Controllo sovrapposizioni: nessun problema trovato",
                           [{"foglio": foglio, "b": p["b"], "testo": p["msg"], "tipo": p["tipo"], "ids": p["ids"]}
                            for p in pr], agente, genere="controllo")
        return {"foglio": foglio, "problemi": pr}

    def cerca(self, q: str, foglio: str = "", mostra: bool = False, agente: str | None = None) -> list[dict]:
        out = []
        with self.lock:
            fogli = list(self.fogli.items())
        for k, v in fogli:
            if foglio and k != foglio and v["meta"].get("doc") != foglio:
                continue
            for r in scena.cerca(v["oggetti"], q):
                r["foglio"] = k
                out.append(r)
        out = out[:300]
        if mostra:
            self.risultati(f"Trova «{q}»: {len(out)}", [{"foglio": r["foglio"], "b": r["b"], "testo": r["s"],
                                                         "ids": [r["id"]]} for r in out], agente, genere="trova")
        return out

    def risultati(self, titolo: str, voci: list[dict], agente: str | None = None, genere: str = "elenco"):
        self.invia({"tipo": "risultati", "titolo": titolo, "voci": voci, "agente": agente or AGENTE_BASE,
                    "genere": genere})

    # -- per la pagina -------------------------------------------------------
    def glifi(self) -> bytes:
        if self._glifi is None:
            self._glifi = json.dumps(scena.glifi(), ensure_ascii=False, separators=(",", ":")).encode()
        return self._glifi

    def iniziale(self) -> dict:
        with self.lock:
            return {"tipo": "stato", "attivo": self.attivo,
                    "fogli": [{"foglio": k, "oggetti": v["oggetti"], "meta": v["meta"], "sfondo": v.get("sfondo")}
                              for k, v in self.fogli.items()],
                    "storia": list(self.storia), "agenti": list(self.agenti.values()),
                    "richieste": [self._richiesta_pub(r) for r in self.richieste[-120:]],
                    "chat": self.chat[-200:], "evidenze": list(self.evidenze), "ascolto": self._ascolto_pub,
                    "sessione": self.sessione}

    def sintesi(self) -> dict:
        with self.lock:
            return {"ok": True,
                    "fogli": {k: {"oggetti": len(v["oggetti"]), **{x: v["meta"].get(x) for x in ("doc", "num", "desc")
                                                                    if v["meta"].get(x) is not None}}
                              for k, v in self.fogli.items()},
                    "agenti": {k: {x: a.get(x) for x in ("stato", "foglio", "attivita", "modello", "effort")}
                               for k, a in self.agenti.items()},
                    "sessione": self.sessione,
                    "richieste_nuove": sum(1 for r in self.richieste if r["stato"] == "nuova"),
                    "messaggi_da_leggere": sum(1 for m in self.chat if m["da"] == "utente" and not m.get("consegnato")),
                    "in_ascolto": self._ascolto_pub, "clienti": len(self.clienti),
                    "osservati": len(self.osservati), "errori_conversione": scena.ERRORI[-5:]}


STATO = Stato()


class Gestore(BaseHTTPRequestHandler):
    server_version = "SpacVista/2.0"

    def log_message(self, *a):  # silenzioso
        pass

    def _json(self, obj, codice: int = 200):
        b = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _file(self, percorso: str, tipo: str | None = None):
        if not percorso or not os.path.isfile(percorso):
            self.send_error(404)
            return
        with open(percorso, "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", tipo or mimetypes.guess_type(percorso)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        sp = urlsplit(self.path)
        u, q = sp.path, {k: v[-1] for k, v in parse_qs(sp.query).items()}
        if u in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if u.startswith("/static/"):
            return self._file(os.path.join(STATIC, os.path.basename(u)))
        if u == "/api/stato":
            return self._json(STATO.sintesi())
        if u == "/api/glifi":
            b = STATO.glifi()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if u.startswith("/api/img/"):
            return self._file(STATO.immagini.get(u.rsplit("/", 1)[-1], ""))
        if u == "/api/eventi":
            return self._eventi()
        if u == "/api/foglio":
            fo = STATO.fogli.get(q.get("nome", ""))
            if fo is None:
                return self._json({"errore": "foglio non trovato"}, 404)
            return self._json({"foglio": q["nome"], "oggetti": fo["oggetti"], "meta": fo["meta"]})
        if u == "/api/richieste":
            return self._json(STATO.elenco_richieste(q.get("tutte") == "1", q.get("segna", "1") == "1"))
        m = re.match(r"^/api/richieste/(\d+)\.png$", u)
        if m:
            r = next((x for x in STATO.richieste if x["id"] == int(m.group(1))), None)
            return self._file((r or {}).get("png", ""), "image/png")
        if u == "/api/chat":
            return self._json(STATO.chat[-int(q.get("n", 100)):])
        if u == "/api/chat/attendi":
            return self._json(STATO.attendi(q.get("chi") or q.get("agente") or AGENTE_BASE, q.get("agente") or None,
                                            float(q.get("timeout", 25))))
        if u == "/api/spac":
            return self._json(STATO.dati_spac(q.get("foglio", ""), q.get("hd", "")))
        if u == "/api/cerca":
            return self._json(STATO.cerca(q.get("q", ""), q.get("foglio", ""), q.get("mostra") == "1"))
        self.send_error(404)

    def _eventi(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        try:
            with STATO.lock:              # stato iniziale e iscrizione insieme: nessun evento perso
                iniz = json.dumps(STATO.iniziale(), ensure_ascii=False, separators=(",", ":"))
                STATO.clienti.append(q)
            self.wfile.write(f"data: {iniz}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    dati = q.get(timeout=15)
                    self.wfile.write(f"data: {dati}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.close_connection = True
            with STATO.lock:
                if q in STATO.clienti:
                    STATO.clienti.remove(q)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            dati = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"errore": "JSON non valido"}, 400)
        azione = urlsplit(self.path).path.split("/api/", 1)[-1]
        ag = dati.get("agente") or None
        try:
            if azione == "scena":
                r = STATO.scena(dati["foglio"], dati["oggetti"], dati.get("nota", ""), dati.get("anima", True),
                                meta=dati.get("meta"), agente=ag)
            elif azione == "carica":
                r = STATO.carica(dati["percorso"], dati.get("foglio", ""), dati.get("nota", ""),
                                 dati.get("anima", True), dati.get("multifoglio", "auto"), dati.get("db", ""), ag)
            elif azione == "osserva":
                r = STATO.osserva(dati["percorso"], dati.get("foglio", ""), dati.get("filtro", ""))
            elif azione == "smetti":
                STATO.osservati.clear()
                r = {"ok": True}
            elif azione in ("nota", "comando", "fuoco", "attiva"):
                ev = {"tipo": azione, **{k: v for k, v in dati.items() if k != "tipo"}}
                if azione in ("nota", "comando", "fuoco"):
                    ev["agente"] = STATO.agente(ag, **({"attivita": dati.get("testo", "")} if azione == "nota"
                                                       else {}))["nome"]
                if azione == "nota":
                    ev["genere"] = dati.get("tipo", "pensiero")
                if azione == "attiva":
                    STATO.attivo = dati.get("foglio")
                STATO.invia(ev)
                r = {"ok": True}
            elif azione == "agente":
                r = STATO.agente(dati.get("nome") or ag, **{k: dati.get(k) for k in ("stato", "foglio", "attivita",
                                                                                     "colore", "modello", "effort",
                                                                                     "compito")})
            elif azione == "svuota":
                STATO.svuota(dati.get("foglio", ""))
                r = {"ok": True}
            elif azione == "richiesta":
                r = STATO._richiesta_pub(STATO.nuova_richiesta(dati))
            elif azione == "richiesta_via":
                STATO.togli_richiesta(dati["id"])
                r = {"ok": True}
            elif azione == "rispondi":
                r = STATO.rispondi(dati["id"], dati.get("testo", ""), dati.get("fatta", True), ag)
            elif azione == "chat":
                r = STATO.messaggio(dati.get("testo", ""), dati.get("da") or ag or "utente", dati.get("a"),
                                    dati.get("rif"))
            elif azione == "evidenzia":
                r = STATO.evidenzia(dati)
            elif azione == "immagine":
                r = STATO.immagine(dati["foglio"], dati.get("zona"), float(dati.get("pxmm", 8)),
                                   int(dati.get("max", 1800)), float(dati.get("timeout", 15)))
            elif azione == "immagine_pronta":
                r = {"ok": STATO.immagine_pronta(dati.get("rid", ""), dati.get("png"))}
            elif azione == "controlla":
                r = STATO.controlla(dati["foglio"], dati.get("zona"), dati.get("mostra", True), ag)
            elif azione == "cerca":
                r = STATO.cerca(dati.get("q", ""), dati.get("foglio", ""), dati.get("mostra", True), ag)
            elif azione == "sfondo":
                r = STATO.sfondo(dati["foglio"], dati.get("pdf", ""), int(dati.get("pagina", 1)),
                                 float(dati.get("dx", 0)), float(dati.get("dy", 0)), float(dati.get("scala", 1)),
                                 float(dati.get("opacita", 0.45)))
            elif azione == "risultati":
                STATO.risultati(dati.get("titolo", ""), dati.get("voci", []), ag)
                r = {"ok": True}
            elif azione == "salva_bozza":  # l'utente ha disegnato nella pagina e preme Salva
                r = STATO.salva_bozza(dati["foglio"], dati.get("ops") or [], bool(dati.get("copia")))
                STATO.invia({"tipo": "nota", "testo": f"L'utente ha salvato {len(dati.get('ops') or [])} modifiche in "
                             f"{os.path.basename(r['file'])}", "genere": "azione", "agente": AGENTE_BASE,
                             "foglio": dati["foglio"]})
            elif azione == "demo":         # pulsante Demo della pagina: Claude disegna lo schema di esempio
                from . import demo
                threading.Thread(target=demo.esegui, kwargs={"cartella": dati.get("cartella", "")},
                                 daemon=True).start()
                r = {"ok": True}
            elif azione == "chiudi":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                r = {"ok": True}
            else:
                return self._json({"errore": f"azione sconosciuta: {azione}"}, 404)
        except KeyError as ex:
            return self._json({"errore": f"non trovato: {ex}"}, 404)
        except Exception as ex:  # noqa: BLE001
            return self._json({"errore": f"{type(ex).__name__}: {ex}"}, 500)
        return self._json(r)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, OSError)):
            return                      # browser chiuso o pagina ricaricata: normale
        super().handle_error(request, client_address)


def avvia(porta: int = PORTA) -> ThreadingHTTPServer:
    STATO.carica_dati()
    srv = _Server(("127.0.0.1", porta), Gestore)
    if not getattr(STATO, "_osservatore", None):
        STATO._osservatore = threading.Thread(target=STATO._giro, daemon=True)
        STATO._osservatore.start()
    return srv
