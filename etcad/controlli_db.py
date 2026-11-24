"""Controlli sul dbSPAC.db di una commessa, simili a quelli che fa SPAC.

Complementari a ``norms.py``, che controlla gia': formato delle sigle IEC 81346
e sigle con costruttore/tipo diversi, numeri filo doppi sullo stesso foglio,
formazione dei cavi, quadri senza distinta. Qui: rimandi, contatti e
componenti madre, morsetti/connettori, sequenza di stampa.

Struttura reale del db (commesse reali, MDBINFO.VERSIONE 33.0.2-35.0.3):

- dalla 34/35 le tabelle ``Elementi``, ``MorsConn`` e ``Rimandi`` (stesse 39
  colonne); nella 33 c'e' solo ``Componenti`` e i rimandi sono i blocchi
  ``_SIGN_*``/``_ALIM_*``, i morsetti i blocchi ``_MORS*``;
- Rimandi: ``Pres`` P (partenza) / A (arrivo), ``Nome`` = segnale (numero
  filo), ``Attributi`` LISP con ``("REG" . "16.0A")`` = foglio 16, colonna 0,
  riga A della controparte; ``/./`` o vuoto = rimando non collegato;
- Elementi: ``Pres`` M = componente madre, F* (FA, FL, FAP, FC, FT, FA2...) =
  contatto figlio con la sigla della madre in ``Nome``, R = riferimento
  incrociato con la sigla in ``MADRE``; vuoto/NULL = componente semplice o
  accessorio (puntali, faston, connettori delle schede cavi);
- MorsConn: ``Nome`` = morsettiera/connettore, ``NumM`` = morsetto/pin,
  ``NumI``/``NumO`` = fili ai due lati (``?`` = filo non numerato),
  ``Livello``/``Rip`` per i morsetti a piu' piani o ripetuti;
- PlotSequence (multifoglio, foglio, layer) puo' mancare o essere vecchia;
  ``Structure`` contiene una copia della tabella dei layer del DWG (righe con
  ``Lista`` che inizia con ``(0 . "LAYER")``): un layer per foglio.

``Cbl_Harness``, ``Jumpers``, ``Cavi`` e ``Vene`` sono vuote in tutte le
commesse viste: nessun controllo su quelle tabelle.

Tutto in sola lettura (``file:...?mode=ro``).
"""
from __future__ import annotations

import inspect
import os
import re
import sqlite3
from collections import Counter, defaultdict

MAX_RIGHE = 50
REG_RE = re.compile(r'\(\s*"REG"\s*\.\s*"([^"]*)"\s*\)')
LAYER_NON_FOGLI = re.compile(r"^(0|defpoints)$|_rev_", re.IGNORECASE)
COLONNE = ("Dwg", "Handle", "IDQuadro", "NomeBlocco", "Foglio", "Nome", "Pres",
           "MADRE", "NumM", "NumI", "NumO", "Rip", "Livello", "Famiglia",
           "Libreria", "Descrizione", "Attributi")


def _ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _spiegazione(fn) -> str:
    """Primo paragrafo del docstring: cosa significa e perche' e' un problema."""
    return inspect.cleandoc(fn.__doc__ or "").split("\n\n")[0].replace("\n", " ")


class _Dati:
    """Righe del db lette una volta sola (solo le colonne che servono)."""

    def __init__(self, db_path: str, multifoglio: str | None, sep_catenaria: str = "."):
        self.db = db_path
        self.sep = sep_catenaria or "."
        con = _ro(db_path)
        try:
            self.tabelle = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.versione = ""
            if "MDBINFO" in self.tabelle:
                r = con.execute("SELECT tagValue FROM MDBINFO WHERE tagName='VERSIONE'").fetchone()
                self.versione = r[0] if r else ""
            self.dwg = [r[0] for r in con.execute("SELECT Nome FROM Dwg")] \
                if "Dwg" in self.tabelle else []
            if {"Elementi", "MorsConn", "Rimandi"} & self.tabelle:
                self.struttura = "Elementi/MorsConn/Rimandi"
                self.elementi = self._righe(con, "Elementi")
                self.morsetti = self._righe(con, "MorsConn")
                self.rimandi = self._righe(con, "Rimandi")
            else:
                self.struttura = "Componenti"
                self.elementi, self.morsetti, self.rimandi = [], [], []
                for r in self._righe(con, "Componenti"):
                    b = _s(r["NomeBlocco"]).upper()
                    if b.startswith(("_SIGN_", "_ALIM_")):
                        self.rimandi.append(r)
                    elif b.startswith("_MORS"):
                        self.morsetti.append(r)
                    else:
                        self.elementi.append(r)
            self.segnalini = self._righe(con, "SegnaliniDatiCavo")
            self.plot: dict[str, list[str]] = defaultdict(list)
            if "PlotSequence" in self.tabelle:
                for r in con.execute("SELECT multifoglio, foglio FROM PlotSequence ORDER BY riga"):
                    self.plot[_s(r[0])].append(_s(r[1]))
            self.layer: dict[str, set[str]] = defaultdict(set)
            if "Structure" in self.tabelle:
                for r in con.execute("SELECT Dwg, Foglio, Lista FROM Structure "
                                     "WHERE Lista LIKE '((0 . \"LAYER\")%'"):
                    fg = _s(r[1])
                    if fg and not LAYER_NON_FOGLI.search(fg):
                        self.layer[_s(r[0])].add(fg)
            self.morsettiere: set[tuple[str, str]] = set()
            if "Morsettiere" in self.tabelle:
                self.morsettiere = {(_s(r[0]), _s(r[1])) for r in con.execute(
                    "SELECT IDQuadro, Nome FROM Morsettiere")}
        finally:
            con.close()
        # le madri si cercano in tutta la commessa, prima del filtro
        self.madri_tutte = {(_s(r["Nome"])) for r in self.elementi if _s(r["Pres"]) == "M"}
        self.mf = multifoglio
        if multifoglio is not None:
            k = multifoglio.casefold()
            noti = {_s(d).casefold() for d in self.dwg} | {
                _s(r["Dwg"]).casefold() for r in self.elementi + self.morsetti + self.rimandi}
            if k not in noti:
                raise ValueError(f"Multifoglio non trovato: {multifoglio} "
                                 f"(nel db: {', '.join(self.dwg) or 'nessuno'})")
            filtro = lambda rr: [r for r in rr if _s(r["Dwg"]).casefold() == k]  # noqa: E731
            self.elementi_tutti, self.morsetti_tutti = self.elementi, self.morsetti
            self.elementi, self.morsetti = filtro(self.elementi), filtro(self.morsetti)
            self.rimandi, self.segnalini = filtro(self.rimandi), filtro(self.segnalini)
        else:
            self.elementi_tutti, self.morsetti_tutti = self.elementi, self.morsetti

    def _righe(self, con, tabella: str) -> list[dict]:
        if tabella not in self.tabelle:
            return []
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{tabella}")')}
        sel = ", ".join(f'"{c}"' if c in cols else f'NULL AS "{c}"' for c in COLONNE)
        return [dict(r) for r in con.execute(f'SELECT {sel} FROM "{tabella}"')]

    def fogli_noti(self, dwg: str) -> tuple[set[str], bool]:
        """Fogli esistenti del multifoglio e se l'elenco viene da PlotSequence/layer.

        PlotSequence puo' essere incompleta o vecchia, la copia dei layer puo'
        mancare: si uniscono le due fonti piu' i fogli che hanno dati.
        """
        k = dwg.casefold()
        fogli = set()
        for mf, fg in self.plot.items():
            if mf.casefold() == k:
                fogli |= set(fg)
        for mf, fg in self.layer.items():
            if mf.casefold() == k:
                fogli |= fg
        affidabile = bool(fogli)
        for r in self.elementi_tutti + self.morsetti_tutti + self.rimandi + self.segnalini:
            if _s(r["Dwg"]).casefold() == k:
                fogli.add(_s(r["Foglio"]))
        return fogli, affidabile

    def reg(self, r: dict) -> str | None:
        m = REG_RE.search(_s(r["Attributi"]))
        return m.group(1).strip() if m else None

    def foglio_reg(self, reg: str | None) -> str | None:
        """Foglio di un riferimento "16.0A" -> "16"; None se non collegato."""
        if not reg or reg.startswith("/"):
            return None
        return reg.rsplit(self.sep, 1)[0] if self.sep in reg else reg


def _esito(n: int, grave: bool = False) -> str:
    return "pass" if n == 0 else ("fail" if grave else "warn")


def _risultato(fn, esito: str, n: int, dettaglio: list, **extra) -> dict:
    out = {"controllo": fn.__name__.removeprefix("controlla_"), "esito": esito, "n": n,
           "dettaglio": dettaglio[:MAX_RIGHE], "spiegazione": _spiegazione(fn)}
    if len(dettaglio) > MAX_RIGHE:
        out["dettaglio_troncato"] = len(dettaglio)
    out.update(extra)
    return out


def _rimando(r: dict, dati: _Dati, **extra) -> dict:
    return {"multifoglio": _s(r["Dwg"]), "foglio": _s(r["Foglio"]), "segnale": _s(r["Nome"]),
            "tipo": {"P": "partenza", "A": "arrivo"}.get(_s(r["Pres"]), _s(r["Pres"])),
            "blocco": _s(r["NomeBlocco"]), "handle": _s(r["Handle"]),
            "riferimento": dati.reg(r), **extra}


# ---------------------------------------------------------------- rimandi

def controlla_rimandi_non_collegati(dati: _Dati) -> dict:
    """Rimandi senza controparte: SPAC scrive "/./" (o niente) al posto del
    riferimento quando non trova l'arrivo di una partenza o la partenza di un
    arrivo con lo stesso segnale. Sul foglio stampato il rimando non dice dove
    continua il filo. Spesso e' una partenza lasciata sull'ultimo foglio di una
    catena copiata; anche i rimandi senza segnale finiscono qui.

    Esito warn: puo' essere voluto, ma va guardato. ``indizio``: un rimando
    opposto con lo stesso segnale che punta proprio a questo foglio (es.
    partenza _SIGN_ e arrivo _ALIM_: tipi diversi, SPAC non li accoppia)."""
    per_segnale: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in dati.rimandi:
        per_segnale[(_s(r["Dwg"]).casefold(), _s(r["Nome"]))].append(r)
    det = []
    for r in dati.rimandi:
        if not _s(r["Nome"]):
            det.append(_rimando(r, dati, motivo="rimando senza segnale"))
        elif dati.reg(r) is None:
            det.append(_rimando(r, dati, motivo="attributo REG assente"))
        elif dati.foglio_reg(dati.reg(r)) is None:
            extra = {}
            opposto = {"P": "A", "A": "P"}.get(_s(r["Pres"]))
            verso_qui = [x for x in per_segnale[(_s(r["Dwg"]).casefold(), _s(r["Nome"]))]
                         if _s(x["Pres"]) == opposto
                         and dati.foglio_reg(dati.reg(x)) == _s(r["Foglio"])]
            if verso_qui:
                x = verso_qui[0]
                cosa = "l'arrivo" if opposto == "A" else "la partenza"
                fam = lambda b: _s(b).upper()[:5]  # noqa: E731  _SIGN / _ALIM
                perche = ("tipi diversi (segnale/alimentazione), SPAC non li accoppia"
                          if fam(x["NomeBlocco"]) != fam(r["NomeBlocco"])
                          else "riferimenti da aggiornare")
                extra["indizio"] = (f"{cosa} {_s(x['NomeBlocco'])} sul foglio {_s(x['Foglio'])} "
                                    f"punta a questo foglio ({dati.reg(x)}): {perche}")
            det.append(_rimando(r, dati, motivo="riferimento vuoto o '/./'", **extra))
    det.sort(key=lambda d: (d["multifoglio"], d["segnale"], d["foglio"]))
    return _risultato(controlla_rimandi_non_collegati, _esito(len(det)), len(det), det)


def controlla_rimandi_riferimenti(dati: _Dati) -> dict:
    """Riferimenti dei rimandi sbagliati: il rimando dice "continua al foglio X"
    ma sul foglio X non c'e' un rimando opposto (arrivo per una partenza,
    partenza per un arrivo) con lo stesso segnale, oppure il foglio X non
    esiste nel multifoglio. Sul disegno il riferimento stampato porta a un
    foglio sbagliato: di solito fogli cancellati o rinumerati senza aggiornare
    i rimandi.

    Esito fail: il riferimento stampato e' sbagliato."""
    per_segnale: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in dati.rimandi:
        per_segnale[(_s(r["Dwg"]).casefold(), _s(r["Nome"]))].append(r)
    cache_fogli: dict[str, tuple[set[str], bool]] = {}
    det = []
    for (dwg_k, segnale), lst in per_segnale.items():
        if not segnale:
            continue
        for r in lst:
            fdest = dati.foglio_reg(dati.reg(r))
            if fdest is None:
                continue  # gia' in rimandi_non_collegati
            pres = _s(r["Pres"])
            opposto = {"P": "A", "A": "P"}.get(pres)
            dwg = _s(r["Dwg"])
            if dwg_k not in cache_fogli:
                cache_fogli[dwg_k] = dati.fogli_noti(dwg)
            fogli, affidabile = cache_fogli[dwg_k]
            if affidabile and fdest not in fogli:
                det.append(_rimando(r, dati, motivo=f"il foglio {fdest} non esiste"))
                continue
            if opposto and not any(_s(x["Pres"]) == opposto and _s(x["Foglio"]) == fdest
                                   for x in lst):
                cosa = "nessun arrivo" if opposto == "A" else "nessuna partenza"
                det.append(_rimando(r, dati,
                                    motivo=f"{cosa} '{segnale}' sul foglio {fdest}"))
    det.sort(key=lambda d: (d["multifoglio"], d["segnale"], d["foglio"]))
    return _risultato(controlla_rimandi_riferimenti, _esito(len(det), grave=True), len(det), det)


# ---------------------------------------------------------------- componenti

def controlla_componenti_sigla_doppia(dati: _Dati, no_sigle_doppie: bool | None = None) -> dict:
    """Piu' componenti madre (Pres M) con la stessa sigla nello stesso
    multifoglio e quadro. Ogni sigla dovrebbe essere un solo apparecchio: con
    due madri i contatti figli e i riferimenti incrociati si agganciano a una
    sola, e in distinta l'apparecchio compare due volte. Con l'opzione
    No_Sigle_Doppie=1 SPAC non dovrebbe permetterlo. Diverso da norms
    (sigle_iec81346), che segnala la stessa sigla con costruttore/tipo diversi.

    La stessa sigla in multifogli diversi della commessa e' contata a parte
    (``in_piu_multifogli``, informativo): nelle commesse viste sono copie o
    revisioni dello stesso schema (es. "-REV01")."""
    gruppi: dict[tuple, list[dict]] = defaultdict(list)
    for r in dati.elementi:
        if _s(r["Pres"]) == "M" and _s(r["Nome"]):
            gruppi[(_s(r["Dwg"]), _s(r["IDQuadro"]), _s(r["Nome"]))].append(r)
    det = [{"multifoglio": d, "quadro": q, "sigla": s, "volte": len(v),
            "fogli": sorted({_s(x["Foglio"]) for x in v}),
            "blocchi": sorted({_s(x["NomeBlocco"]) for x in v}),
            "handle": [_s(x["Handle"]) for x in v][:10]}
           for (d, q, s), v in sorted(gruppi.items()) if len(v) > 1]
    extra = {}
    if dati.mf is None:
        dwg_per_sigla: dict[tuple, set] = defaultdict(set)
        for (d, q, s) in gruppi:
            dwg_per_sigla[(q, s)].add(d.casefold())
        extra["in_piu_multifogli"] = sum(1 for v in dwg_per_sigla.values() if len(v) > 1)
    if no_sigle_doppie is not None:
        extra["opzione_no_sigle_doppie"] = no_sigle_doppie
    return _risultato(controlla_componenti_sigla_doppia, _esito(len(det)), len(det), det, **extra)


def controlla_contatti_senza_madre(dati: _Dati) -> dict:
    """Contatti figli (Pres F*: FA, FL, FAP, FC...) e riferimenti incrociati
    (Pres R) la cui sigla non corrisponde a nessun componente madre (Pres M)
    della commessa. SPAC non puo' scrivere sotto la bobina/madre dove sono i
    suoi contatti, ne' accanto al contatto dove sta la madre: di solito la
    madre e' stata cancellata o rinominata. La madre si cerca in tutti i
    multifogli della commessa."""
    det = []
    for r in dati.elementi:
        pres = _s(r["Pres"]).upper()
        if not (pres.startswith("F") or pres == "R"):
            continue
        sigla = _s(r["MADRE"]) if pres == "R" and _s(r["MADRE"]) else _s(r["Nome"])
        if not sigla or sigla in dati.madri_tutte:
            continue
        det.append({"multifoglio": _s(r["Dwg"]), "foglio": _s(r["Foglio"]), "sigla": sigla,
                    "pres": _s(r["Pres"]), "blocco": _s(r["NomeBlocco"]),
                    "handle": _s(r["Handle"])})
    det.sort(key=lambda d: (d["multifoglio"], d["sigla"], d["foglio"]))
    return _risultato(controlla_contatti_senza_madre, _esito(len(det)), len(det), det)


def controlla_componenti_senza_sigla(dati: _Dati) -> dict:
    """Componenti madre, contatti o riferimenti (Pres valorizzato) senza
    sigla: non compaiono correttamente in distinta, i contatti non si
    agganciano alla madre e i riferimenti incrociati restano vuoti.

    Gli elementi senza sigla e senza Pres (puntali, faston, connettori delle
    schede cavi, blocchi descrittivi) sono normali: contati in ``accessori``,
    non cambiano l'esito."""
    det, accessori = [], 0
    for r in dati.elementi:
        if _s(r["Nome"]):
            continue
        if not _s(r["Pres"]):
            accessori += 1
            continue
        det.append({"multifoglio": _s(r["Dwg"]), "foglio": _s(r["Foglio"]),
                    "pres": _s(r["Pres"]), "blocco": _s(r["NomeBlocco"]),
                    "descrizione": _s(r["Descrizione"])[:80], "handle": _s(r["Handle"])})
    det.sort(key=lambda d: (d["multifoglio"], d["foglio"]))
    return _risultato(controlla_componenti_senza_sigla, _esito(len(det)), len(det), det,
                      accessori=accessori)


# ---------------------------------------------------------------- morsetti

def _per_foglio(righe: list[dict], **campi_extra) -> list[dict]:
    """Raggruppa per multifoglio/foglio/blocco (le righe possono essere centinaia)."""
    g: dict[tuple, list[dict]] = defaultdict(list)
    for r in righe:
        g[(_s(r["Dwg"]), _s(r["Foglio"]), _s(r["NomeBlocco"]), _s(r["Nome"]))].append(r)
    out = []
    for (d, f, b, n), v in sorted(g.items()):
        riga = {"multifoglio": d, "foglio": f, "blocco": b, "morsetti": len(v),
                "numeri": sorted({_s(x["NumM"]) for x in v if _s(x["NumM"])})[:20],
                "handle": [_s(x["Handle"]) for x in v][:10]}
        if n:
            riga["morsettiera"] = n
        riga.update(campi_extra)
        out.append(riga)
    return out


def controlla_morsetti_senza_morsettiera(dati: _Dati) -> dict:
    """Morsetti o pin di connettore senza nome di morsettiera/connettore
    (MorsConn.Nome vuoto). Non finiscono nelle liste morsetti/connettori, non
    si possono controllare i doppi e il cablaggio non sa a quale connettore
    appartengono. ``vuoti`` = blocchi morsetto senza alcun dato (ne' quadro,
    ne' famiglia, ne' numero), di solito inseriti come semplice disegno.

    Dettaglio raggruppato per foglio e blocco."""
    righe = [r for r in dati.morsetti if not _s(r["Nome"])]
    vuoti = sum(1 for r in righe if not (_s(r["IDQuadro"]) or _s(r["Famiglia"]) or _s(r["NumM"])))
    return _risultato(controlla_morsetti_senza_morsettiera, _esito(len(righe)), len(righe),
                      _per_foglio(righe), vuoti=vuoti)


def controlla_morsetti_doppi(dati: _Dati) -> dict:
    """Stesso numero di morsetto/pin due volte nella stessa morsettiera o
    connettore (stesso multifoglio, quadro e livello). Nella lista morsetti
    compare due volte, oppure due fili diversi finiscono sullo stesso
    morsetto. Esclusi i morsetti con Rip valorizzato (ripetizioni volute).

    Come per le sigle, i doppi tra multifogli diversi (copie dello schema)
    sono solo contati in ``in_piu_multifogli``. Esito fail."""
    gruppi: dict[tuple, list[dict]] = defaultdict(list)
    for r in dati.morsetti:
        if _s(r["Nome"]) and _s(r["NumM"]) and not _s(r["Rip"]):
            gruppi[(_s(r["Dwg"]), _s(r["IDQuadro"]), _s(r["Nome"]), _s(r["NumM"]),
                    _s(r["Livello"]))].append(r)
    det = [{"multifoglio": d, "quadro": q, "morsettiera": n, "morsetto": m, "livello": lv,
            "volte": len(v), "fogli": sorted({_s(x["Foglio"]) for x in v}),
            "fili": sorted({f"{_s(x['NumI'])}/{_s(x['NumO'])}" for x in v}),
            "handle": [_s(x["Handle"]) for x in v][:10]}
           for (d, q, n, m, lv), v in sorted(gruppi.items()) if len(v) > 1]
    extra = {}
    if dati.mf is None:
        dwg_per: dict[tuple, set] = defaultdict(set)
        for (d, q, n, m, lv) in gruppi:
            dwg_per[(q, n, m, lv)].add(d.casefold())
        extra["in_piu_multifogli"] = sum(1 for v in dwg_per.values() if len(v) > 1)
    return _risultato(controlla_morsetti_doppi, _esito(len(det), grave=True), len(det), det,
                      **extra)


def controlla_morsetti_senza_numero(dati: _Dati) -> dict:
    """Morsetti o pin con la morsettiera indicata ma senza numero (NumM
    vuoto): nella lista morsetti e nelle etichette non si sa quale sia."""
    righe = [r for r in dati.morsetti if _s(r["Nome"]) and not _s(r["NumM"])]
    return _risultato(controlla_morsetti_senza_numero, _esito(len(righe)), len(righe),
                      _per_foglio(righe))


def controlla_fili_non_numerati(dati: _Dati, evidenzia: bool | None = None) -> dict:
    """Morsetti o pin collegati a un filo senza numero: SPAC scrive "?" in
    NumI/NumO quando il filo che arriva al morsetto non e' numerato. Nelle
    liste morsetti/cablaggio il collegamento resta senza numero di filo
    (l'opzione Evidenzia_Fili_Non_Num li evidenzia nel disegno).

    Dettaglio raggruppato per foglio e morsettiera."""
    righe = [r for r in dati.morsetti if "?" in _s(r["NumI"]) or "?" in _s(r["NumO"])]
    extra = {} if evidenzia is None else {"opzione_evidenzia_fili_non_num": evidenzia}
    return _risultato(controlla_fili_non_numerati, _esito(len(righe)), len(righe),
                      _per_foglio(righe), **extra)


def controlla_morsettiere_non_definite(dati: _Dati) -> dict:
    """Morsettiere/connettori usati nei morsetti ma assenti dalla tabella
    Morsettiere della commessa: manca la definizione (tipologia, blocco di
    rientro, costruttore), quindi le tabelle morsettiere e i rientri non si
    generano o escono incompleti."""
    usate: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in dati.morsetti:
        if _s(r["Nome"]):
            usate[(_s(r["IDQuadro"]), _s(r["Nome"]))].append(r)
    nomi_definiti = {n for _, n in dati.morsettiere}
    det = []
    for (q, n), v in sorted(usate.items()):
        if (q, n) in dati.morsettiere or (not q and n in nomi_definiti):
            continue
        det.append({"quadro": q, "morsettiera": n, "morsetti": len(v),
                    "multifogli": sorted({_s(x["Dwg"]) for x in v}),
                    "fogli": sorted({_s(x["Foglio"]) for x in v}),
                    "definita_in_altro_quadro": n in nomi_definiti})
    return _risultato(controlla_morsettiere_non_definite, _esito(len(det)), len(det), det)


# ---------------------------------------------------------------- fogli

def controlla_sequenza_stampa(dati: _Dati) -> dict:
    """Sequenza di stampa (PlotSequence) non allineata al multifoglio:
    fogli con componenti, morsetti o rimandi che non sono in sequenza (la
    stampa del multifoglio li salta) e fogli in sequenza senza layer nel DWG
    (fogli cancellati: la sequenza e' vecchia).

    I multifogli senza nessuna riga in PlotSequence non sono segnalati: e'
    normale (sequenza mai impostata); sono elencati in ``senza_sequenza``."""
    det = []
    nomi: dict[str, str] = {}
    for mf in [_s(r["Dwg"]) for r in dati.elementi + dati.morsetti + dati.rimandi] + (
            [dati.mf] if dati.mf else list(dati.dwg)):
        if mf:
            nomi.setdefault(mf.casefold(), mf)
    senza = []
    for mf in sorted(nomi.values()):
        k = mf.casefold()
        seq = set()
        for nome, fg in dati.plot.items():
            if nome.casefold() == k:
                seq |= set(fg)
        if not seq:
            senza.append(mf)
            continue
        usati = {_s(r["Foglio"]) for r in dati.elementi + dati.morsetti + dati.rimandi
                 if _s(r["Dwg"]).casefold() == k}
        for fg in sorted(usati - seq, key=_chiave_foglio):
            det.append({"multifoglio": mf, "foglio": fg, "motivo": "foglio con dati non in sequenza"})
        layer = set()
        for nome, fg in dati.layer.items():
            if nome.casefold() == k:
                layer |= fg
        if layer:
            for fg in sorted(seq - layer, key=_chiave_foglio):
                det.append({"multifoglio": mf, "foglio": fg,
                            "motivo": "in sequenza ma senza layer nel DWG"})
    return _risultato(controlla_sequenza_stampa, _esito(len(det)), len(det), det,
                      senza_sequenza=senza)


def _chiave_foglio(f: str):
    return (0, int(f), "") if f.isdigit() else (1, 0, f)


# ---------------------------------------------------------------- esecuzione

CONTROLLI = (
    controlla_rimandi_non_collegati,
    controlla_rimandi_riferimenti,
    controlla_componenti_sigla_doppia,
    controlla_contatti_senza_madre,
    controlla_componenti_senza_sigla,
    controlla_morsetti_senza_morsettiera,
    controlla_morsetti_doppi,
    controlla_morsetti_senza_numero,
    controlla_fili_non_numerati,
    controlla_morsettiere_non_definite,
    controlla_sequenza_stampa,
)


def _opzioni_cfg(db_path: str) -> dict:
    """Opzioni utili dalla CFG accanto al db (se c'e'); mai errori."""
    try:
        from . import commessa_cfg
        cfg = commessa_cfg.trova_cfg(db_path)
        if not cfg:
            return {}
        ria = commessa_cfg.leggi_config(cfg)["riassunto"]
        return {"cartella_cfg": cfg,
                "separatore_foglio_catenaria": ria["cross_reference"].get(
                    "separatore_foglio_catenaria") or ".",
                "no_sigle_doppie": ria["controlli"].get("no_sigle_doppie"),
                "evidenzia_fili_non_num": ria["controlli"].get("evidenzia_fili_non_num")}
    except Exception:  # noqa: BLE001 - la CFG e' facoltativa
        return {}


def esegui(db_path: str, multifoglio: str | None = None, usa_cfg: bool = True) -> dict:
    """Esegue tutti i controlli sul dbSPAC.db (sola lettura).

    ``multifoglio``: limita i controlli a quel multifoglio (nome del DWG senza
    estensione, senza badare alle maiuscole); le madri dei contatti si cercano
    comunque in tutta la commessa. ``usa_cfg``: legge dalla cartella CFG
    accanto al db il separatore foglio/catenaria dei riferimenti e le opzioni
    No_Sigle_Doppie / Evidenzia_Fili_Non_Num (riportate nei risultati).

    Restituisce ``{"esito", "controlli": [{"controllo", "esito", "n",
    "dettaglio", "spiegazione", ...}], ...}`` come ``norms.run_all``.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    opz = _opzioni_cfg(db_path) if usa_cfg else {}
    dati = _Dati(db_path, multifoglio, opz.get("separatore_foglio_catenaria") or ".")
    checks = []
    for fn in CONTROLLI:
        try:
            if fn is controlla_componenti_sigla_doppia:
                checks.append(fn(dati, opz.get("no_sigle_doppie")))
            elif fn is controlla_fili_non_numerati:
                checks.append(fn(dati, opz.get("evidenzia_fili_non_num")))
            else:
                checks.append(fn(dati))
        except (sqlite3.Error, KeyError, ValueError) as e:  # schema diverso
            checks.append({"controllo": fn.__name__.removeprefix("controlla_"), "esito": "warn",
                           "n": 0, "dettaglio": [f"non eseguibile: {e}"],
                           "spiegazione": _spiegazione(fn)})
    rank = {"pass": 0, "warn": 1, "fail": 2}
    esito = max((c["esito"] for c in checks), key=rank.__getitem__, default="pass")
    conteggi = Counter(c["esito"] for c in checks)
    return {"db": db_path, "multifoglio": multifoglio, "versione_db": dati.versione,
            "struttura": dati.struttura, "multifogli": dati.dwg,
            "cartella_cfg": opz.get("cartella_cfg"), "esito": esito,
            "riepilogo": {k: conteggi.get(k, 0) for k in ("pass", "warn", "fail")},
            "controlli": checks,
            "nota": "Controlli di coerenza dei dati di commessa (sola lettura); "
                    "complementari a norms.run_all."}
