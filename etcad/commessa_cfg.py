"""Configurazione di una commessa SPAC: la cartella CFG accanto a dbSPAC.db.

Formato verificato su commesse reali di SPAC 2023-2025 (db 33.0.2-35.0.3):

- ``spac_cfg.ini`` (a volte ``Spac_cfg.ini``): INI di Windows, righe CRLF, cp1252
  (le lettere accentate compaiono nei commenti e in un nome di sezione).
  Commenti ``;`` solo a inizio riga: non esistono commenti in coda e ``;`` e'
  un carattere normale dei valori (``Campi=Sigla[V];Codice[V]...``,
  ``(("QUADRO" ";;0;;"))``), quindi non va tolto. Chiavi con spazi
  (``Attiva SelectionPreview``) e maiuscole variabili (``what``/``What``):
  come Windows, le ricerche ignorano le maiuscole. Le liste sono in sintassi
  LISP: ``(402.5 30.0)``, ``("NOT" "5")``, ``(("INP*" . "INPUT") ...)``.
  Le commesse piccole hanno ~180 righe, quelle complete ~2400 con commenti.
- ``DCL_CFG.INI``: impostazioni memorizzate delle finestre di dialogo. Le liste
  di fogli sono ``multifoglio|foglio`` separati da ``:``; il multifoglio a
  volte e' in maiuscolo (va confrontato senza maiuscole). Il valore dopo ``|``
  e' il layer del foglio, che nelle commesse viste coincide col numero di
  foglio (PlotSequence.layer == foglio).
- ``Tables_Configurations.db`` (SQLite UTF-16): stili delle tabelle di SPAC,
  non del cartiglio. ``DBCenter`` = tabelle della distinta materiali,
  ``Bollinatura_Armadio``/``Bollinatura_Pulsantiera``; colonne Name, Data
  (XML), State (``M`` su una sola riga per tabella: lo stile in uso). Le
  commesse vecchie hanno ``Tables_Configurations.mdb`` (Access): non letto.

Tutto in sola lettura: i .ini si aprono in lettura binaria, i .db con
``file:...?mode=ro``.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

NOME_DB = "dbSPAC.db"

# chiavi di DCL_CFG.INI con liste di fogli "multifoglio|foglio:..."
LISTE_FOGLI_DCL = {
    "stampa": ("ARX_NEW_PLOT", "ListaLayerNotSelected"),
    "traduzione": ("ARX_TRAD", "ListaLayerNotSelected"),
    "numerazione": ("ARX_SCEGLI_DWG_LAYERS", "C_NUMFA_ListaLayerNotSelected"),
}


# ---------------------------------------------------------------- ricerca

def _figlio(base: str, nome: str) -> str | None:
    """Sottocartella/file ``nome`` di ``base`` senza badare alle maiuscole."""
    try:
        voci = os.listdir(base)
    except OSError:
        return None
    for v in voci:
        if v.lower() == nome.lower():
            return os.path.join(base, v)
    return None


def _cfg_valida(cartella: str) -> bool:
    try:
        return any(f.lower().endswith(".ini") or f.lower().startswith("tables_configurations")
                   for f in os.listdir(cartella))
    except OSError:
        return False


def trova_cfg(percorso: str) -> str | None:
    """Cartella CFG a partire da cartella commessa, DWG, dbSPAC.db o CFG stessa.

    Cerca nella cartella del percorso e in quella sopra (un DWG in una
    sottocartella). Se non c'e', guarda due livelli sotto (es. la cartella di
    una cartella di lavoro con ``<sottocartella>\\<progetto>\\CFG``). None se non trovata;
    ValueError se sotto ci sono piu' commesse e la scelta sarebbe arbitraria.
    """
    p = os.path.abspath(percorso)
    if not os.path.exists(p):
        return None
    d = p if os.path.isdir(p) else os.path.dirname(p)
    if os.path.basename(d).lower() == "cfg" and _cfg_valida(d):
        return d
    for base in (d, os.path.dirname(d)):
        c = _figlio(base, "CFG")
        if c and os.path.isdir(c) and _cfg_valida(c):
            return c
    trovate = []
    for patt in ("*/CFG", "*/*/CFG"):
        trovate += [c for c in glob.glob(os.path.join(glob.escape(d), patt))
                    if os.path.isdir(c) and _cfg_valida(c)]
        if trovate:
            break
    if len(trovate) > 1:
        raise ValueError("Piu' commesse sotto " + d + ": "
                         + "; ".join(sorted(os.path.dirname(c) for c in trovate)))
    return trovate[0] if trovate else None


def trova_db(percorso: str) -> str | None:
    """dbSPAC.db della commessa (accanto alla cartella CFG o nel percorso)."""
    p = os.path.abspath(percorso)
    if os.path.isfile(p) and os.path.basename(p).lower() == NOME_DB.lower():
        return p
    d = p if os.path.isdir(p) else os.path.dirname(p)
    for base in (d, os.path.dirname(d)):
        f = _figlio(base, NOME_DB)
        if f and os.path.isfile(f):
            return f
    try:
        cfg = trova_cfg(p)
    except ValueError:
        return None
    if cfg:
        f = _figlio(os.path.dirname(cfg), NOME_DB)
        if f and os.path.isfile(f):
            return f
    return None


def _cfg_obbligatoria(percorso: str) -> str:
    cfg = trova_cfg(percorso)
    if not cfg:
        raise FileNotFoundError(f"Cartella CFG non trovata partendo da: {percorso}")
    return cfg


# ---------------------------------------------------------------- valori LISP

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _atomo(s: str):
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?", s):
        return float(s)
    return s


def _tokens(testo: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    testo = testo.rstrip()
    while pos < len(testo):
        m = _TOKEN.match(testo, pos)
        if not m or m.end() == pos:
            raise ValueError(f"sintassi LISP non valida a {pos}: {testo!r}")
        pos = m.end()
        if m.group(1):
            out.append(("(", "("))
        elif m.group(2):
            out.append((")", ")"))
        elif m.group(3) is not None:
            out.append(("str", m.group(3).replace('\\"', '"')))
        else:
            out.append(("sym", m.group(4)))
    return out


def _lista(tok: list[tuple[str, str]], i: int):
    tipo, val = tok[i]
    if tipo == "(":
        elementi, punto, i = [], False, i + 1
        while True:
            if i >= len(tok):
                raise ValueError("parentesi non chiusa")
            if tok[i][0] == ")":
                i += 1
                break
            if tok[i] == ("sym", "."):
                punto, i = True, i + 1
                continue
            el, i = _lista(tok, i)
            elementi.append(el)
        if punto and len(elementi) == 2:
            return (elementi[0], elementi[1]), i   # coppia puntata
        return elementi, i
    if tipo == ")":
        raise ValueError("parentesi chiusa in piu'")
    return (val if tipo == "str" else _atomo(val)), i + 1


def interpreta_valore(testo: str | None):
    """Valore di un .ini SPAC: le liste LISP diventano liste Python.

    ``(402.5 30.0)`` -> [402.5, 30.0]; ``("NOT" "5")`` -> ["NOT", "5"];
    ``(("INP*" . "INPUT"))`` -> [("INP*", "INPUT")]. Il resto resta stringa
    (anche i numeri: il tipo lo decide chi usa la chiave). Se la lista non e'
    ben formata restituisce il testo com'e'.
    """
    if testo is None:
        return None
    s = testo.strip()
    if not s.startswith("("):
        return s
    try:
        tok = _tokens(s)
        val, i = _lista(tok, 0)
        if i != len(tok):
            return s
        return val
    except (ValueError, IndexError):
        return s


# ---------------------------------------------------------------- lettura .ini

def _decodifica(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), "utf-16"
    try:
        testo = raw.decode("utf-8")
        return testo, ("ascii" if testo.isascii() else "utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def leggi_ini(percorso: str) -> dict:
    """Legge un .ini di SPAC in modo tollerante (sola lettura).

    - commenti ``;`` o ``#`` a inizio riga; ``;`` dentro i valori resta;
    - righe senza ``=`` ignorate e annotate in ``righe_ignorate``;
    - chiavi doppie (anche con maiuscole diverse): vale la prima, come
      GetPrivateProfileString di Windows; le altre finiscono in ``duplicati``;
    - sezioni ripetute vengono unite;
    - chiavi prima di ogni sezione sotto la sezione ``""``.
    """
    with open(percorso, "rb") as f:
        raw = f.read()
    testo, codifica = _decodifica(raw)
    sezioni: dict[str, dict[str, str]] = {}
    nomi_sez: dict[str, str] = {}          # minuscolo -> nome della prima volta
    chiavi: dict[str, dict[str, str]] = {}  # sezione -> minuscolo -> nome
    duplicati, ignorate = [], []
    sez = ""
    for n, riga in enumerate(testo.splitlines(), 1):
        s = riga.strip().lstrip("﻿")
        if not s or s[0] in ";#":
            continue
        if s.startswith("[") and "]" in s:
            nome = s[1:s.index("]")].strip()
            sez = nomi_sez.setdefault(nome.lower(), nome)
            sezioni.setdefault(sez, {})
            chiavi.setdefault(sez, {})
            continue
        if "=" not in s:
            ignorate.append({"riga": n, "sezione": sez, "testo": s[:160]})
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        sezioni.setdefault(sez, {})
        noti = chiavi.setdefault(sez, {})
        if k.lower() in noti:
            duplicati.append({"riga": n, "sezione": sez, "chiave": k, "valore": v[:160],
                              "vale": sezioni[sez][noti[k.lower()]][:160]})
            continue
        noti[k.lower()] = k
        sezioni[sez][k] = v
    return {"percorso": percorso, "codifica": codifica, "sezioni": sezioni,
            "duplicati": duplicati, "righe_ignorate": ignorate}


def _get(sezioni: dict, sezione: str, chiave: str, cerca_ovunque: bool = True) -> str | None:
    """Valore grezzo, ignorando le maiuscole; se manca nella sezione attesa
    lo cerca nelle altre (alcune chiavi hanno cambiato sezione tra versioni)."""
    ordine = [s for s in sezioni if s.lower() == sezione.lower()]
    if cerca_ovunque:
        ordine += [s for s in sezioni if s.lower() != sezione.lower()]
    for s in ordine:
        for k, v in sezioni[s].items():
            if k.lower() == chiave.lower():
                return v
    return None


def _bool(v: str | None) -> bool | None:
    if v is None or v.strip() == "":
        return None
    s = v.strip().lower()
    if s in ("1", "t", "true", "on", "si", "yes"):
        return True
    if s in ("0", "f", "false", "off", "no", "nil"):
        return False
    return None


def _num(v: str | None):
    if v is None or v.strip() == "":
        return None
    a = _atomo(v.strip())
    return a if isinstance(a, (int, float)) else None


def _xy(v: str | None) -> tuple | None:
    val = interpreta_valore(v)
    if isinstance(val, list) and val and all(isinstance(x, (int, float)) for x in val):
        return tuple(float(x) for x in val)
    return None


def _flag_sezione(sezioni: dict, sezione: str, salta: set[str]) -> dict:
    """Chiavi di una sezione: 0/1 -> bool, liste LISP -> liste, il resto testo."""
    out = {}
    for s in sezioni:
        if s.lower() != sezione.lower():
            continue
        for k, v in sezioni[s].items():
            if k.lower() in salta:
                continue
            b = _bool(v) if v.strip() in ("0", "1") else None
            out[k.lower()] = b if b is not None else interpreta_valore(v)
    return out


def lista_fogli(valore: str | None) -> dict[str, list[str]]:
    """``"MF|1:MF|2:ALTRO|5"`` -> {"MF": ["1", "2"], "ALTRO": ["5"]} (ordine del file)."""
    out: dict[str, list[str]] = {}
    for pezzo in (valore or "").split(":"):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        mf, _, fg = pezzo.rpartition("|")
        out.setdefault(mf, []).append(fg)
    return out


# ---------------------------------------------------------------- riassunto

def _riassunto(spac: dict, dcl: dict) -> dict:
    # chiavi generiche (Rotazione, Separatore...) esistono in piu' sezioni:
    # si leggono solo dalla sezione giusta; le opzioni di controllo invece si
    # cercano anche altrove, perche' tra le versioni hanno cambiato sezione
    g = lambda sez, k: _get(spac, sez, k, cerca_ovunque=False)  # noqa: E731
    c = lambda sez, k: _get(spac, sez, k, cerca_ovunque=True)  # noqa: E731
    nf = "Numerazione_Fogli"
    numerazione_fogli = {
        "numero_foglio": _xy(g(nf, "XY_Numero_Foglio")),
        "foglio_seguente": _xy(g(nf, "XY_Foglio_Seguente")),
        "foglio_precedente": _xy(g(nf, "XY_Foglio_Precedente")),
        "totale_fogli": _xy(g(nf, "XY_Totale_Fogli")),
        "altezze": {
            "numero_foglio": _num(g(nf, "Altezza_Numero_Foglio")),
            "foglio_seguente": _num(g(nf, "Altezza_Foglio_Seguente")),
            "foglio_precedente": _num(g(nf, "Altezza_Foglio_Precedente")),
            "totale_fogli": _num(g(nf, "Altezza_Totale_Fogli")),
        },
        "rotazione": _num(g(nf, "Rotazione")),
        "what": _num(g(nf, "What")),
        "prefisso_foglio_in_mf": _bool(g(nf, "Pref_Fg_In_Mf")),
        "opzione_numero_fogli": g(nf, "Opz_Numero_Fogli"),
    }
    controlli = {
        "no_sigle_doppie": _bool(c("Siglatura_Simboli", "No_Sigle_Doppie")),
        "sigle_componenti_uguali": _bool(c("Preferenze", "Sigle_Componenti_Uguali")),
        "evidenzia_fili_non_num": _bool(c("Numerazione_Filo_Morsetti", "Evidenzia_Fili_Non_Num")),
        "max_fili_non_num": _num(c("Configurazione_Ricerca_Rami", "Max_Fili_Non_Num")),
        "numf_skip_check": _bool(c("Numerazione_Filo_Morsetti", "Numf_Skip_Check")),
        "check_sezioni_collegamenti": _bool(c("Preferenze", "AbilitaCheckSezioniColl")),
        "filtro_scansione_fogli": _bool(g("Filtro_Scansione_Fogli", "Able_Filter")),
        "avviso_filtro_scansione": _bool(g("Filtro_Scansione_Fogli", "Able_Alert")),
    }
    sep = interpreta_valore(g("Cross_Reference", "Separators"))
    cross = {
        "separatori": sep if isinstance(sep, list) else None,
        "separatore_foglio_catenaria": g("Cross_Reference", "Fg_Cat_Separator"),
    }
    cross.update(_flag_sezione(spac, "Cross_Reference", {"separators", "fg_cat_separator"}))
    sl = "Scheme_Layout"
    dl, ur = _xy(g(sl, "DL_point")), _xy(g(sl, "UR_point"))
    div = interpreta_valore(g(sl, "Divisions"))
    div = tuple(int(x) for x in div) if (isinstance(div, list) and len(div) == 2
                                         and all(isinstance(x, (int, float)) for x in div)) else None
    layout = {
        "basso_sinistra": dl, "alto_destra": ur, "divisioni": div,
        "prima_divisione": _num(g(sl, "First_Division")),
        "modo_divisione": g(sl, "Division_Mode"),
        "passo": _num(g(sl, "Step")),
        "larghezza_colonna": None, "altezza_riga": None,
    }
    if dl and ur and div and div[0] and div[1]:
        layout["larghezza_colonna"] = round((ur[0] - dl[0]) / div[0], 4)
        layout["altezza_riga"] = round((ur[1] - dl[1]) / div[1], 4)
    gm = "Genera_Multifoglio"
    multifoglio = {
        "abilita_fogli_elaborabili": _bool(g(gm, "Abilita_Fg_Elaborabili")),
        "anteprima": _bool(g(gm, "Anteprima")),
    }
    ss = "Siglatura_Simboli"
    siglatura = {"tipo": g(ss, "Tipo_Siglatura"), "composizione": g(ss, "Composizione"),
                 "separatore": g(ss, "Separatore")}
    nfm = "Numerazione_Filo_Morsetti"
    numerazione_fili = {
        "incremento": _num(g(nfm, "Incremento")),
        "riserve_a_foglio": _num(g(nfm, "Riserve_a_Foglio")),
        "stesso_numero": g(nfm, "Stesso_Numero"),
        "colori_ignorati": interpreta_valore(g(nfm, "Colori_Ignorati")),
        "tipo_scansione": g(nfm, "Tipo_Scansione"),
        "numerazione_posizionale": _bool(g(nfm, "Numerazione_Posizionale")),
    }
    df = "Dati_Filo"
    dati_filo = {"tipo": g(df, "Tipo_Filo"), "sezione": g(df, "Sezione_Filo"),
                 "colore": g(df, "Colore_Filo")}
    fogli = {nome: lista_fogli(_get(dcl, sez, k, cerca_ovunque=False))
             for nome, (sez, k) in LISTE_FOGLI_DCL.items()}
    return {"numerazione_fogli": numerazione_fogli, "controlli": controlli,
            "cross_reference": cross, "layout_schema": layout,
            "multifoglio": multifoglio, "siglatura": siglatura,
            "numerazione_fili": numerazione_fili, "dati_filo": dati_filo,
            "simboli": {"file_pre": g("Configurazione_Simboli", "Nome_file_PRE")},
            "fogli_non_selezionati": fogli}


def leggi_config(percorso: str) -> dict:
    """Tutti i .ini della cartella CFG piu' un riassunto gia' interpretato.

    ``percorso``: cartella commessa, DWG, dbSPAC.db o la CFG stessa.
    Restituisce ``{"cartella_cfg", "db", "file": {nome: leggi_ini}, "stili_tabelle",
    "non_letti", "riassunto"}``. Nel riassunto: numerazione fogli (coordinate
    come tuple di float), opzioni di controllo (bool), cross reference,
    layout dello schema (con larghezza colonne e altezza righe), liste dei
    fogli deselezionati nelle finestre di stampa/traduzione/numerazione.
    Nota: in una commessa di prova la griglia dei riferimenti dei rimandi ha le colonne di
    Scheme_Layout ma non le righe; il layout e' quello di default della
    commessa, non per forza quello di ogni cartiglio.
    """
    cfg = _cfg_obbligatoria(percorso)
    file, non_letti = {}, []
    for nome in sorted(os.listdir(cfg), key=str.lower):
        p = os.path.join(cfg, nome)
        if not os.path.isfile(p):
            continue
        if nome.lower().endswith(".ini"):
            file[nome] = leggi_ini(p)
        elif nome.lower() != "tables_configurations.db":
            non_letti.append(nome)
    spac = next((v["sezioni"] for k, v in file.items() if k.lower() == "spac_cfg.ini"), {})
    dcl = next((v["sezioni"] for k, v in file.items() if k.lower() == "dcl_cfg.ini"), {})
    try:
        stili = stili_tabelle(cfg)
    except sqlite3.Error as e:
        stili = []
        non_letti.append(f"Tables_Configurations.db ({e})")
    return {"cartella_cfg": cfg, "db": trova_db(cfg), "file": file,
            "stili_tabelle": stili, "non_letti": non_letti,
            "riassunto": _riassunto(spac, dcl)}


# ---------------------------------------------------------------- fogli esclusi

def fogli_esclusi_per_multifoglio(percorso: str, lista: str = "stampa") -> dict[str, list[str]]:
    """Fogli deselezionati in una finestra di SPAC, per multifoglio.

    ``lista``: "stampa" (ARX_NEW_PLOT), "traduzione" (ARX_TRAD) o
    "numerazione" (ARX_SCEGLI_DWG_LAYERS). E' la selezione memorizzata
    dall'ultima apertura della finestra: puo' capitare che siano esclusi
    tutti i fogli di un multifoglio, cioe' l'utente ha stampato
    solo l'altro multifoglio.
    """
    if lista not in LISTE_FOGLI_DCL:
        raise ValueError(f"lista sconosciuta: {lista} (ammesse: {', '.join(LISTE_FOGLI_DCL)})")
    cfg = _cfg_obbligatoria(percorso)
    f = _figlio(cfg, "DCL_CFG.INI")
    if not f:
        return {}
    sez, chiave = LISTE_FOGLI_DCL[lista]
    return lista_fogli(_get(leggi_ini(f)["sezioni"], sez, chiave, cerca_ovunque=False))


def fogli_esclusi_stampa(percorso: str, multifoglio: str | None = None) -> list[str]:
    """Fogli (numeri, come testo) esclusi dalla stampa per un multifoglio.

    Il multifoglio si confronta senza maiuscole. Se manca: il nome del DWG
    quando ``percorso`` e' un .dwg, altrimenti l'unico multifoglio della lista.
    ValueError se la lista ne contiene piu' d'uno e non si sa quale scegliere.
    """
    per_mf = fogli_esclusi_per_multifoglio(percorso, "stampa")
    if multifoglio is None and percorso.lower().endswith(".dwg"):
        multifoglio = os.path.splitext(os.path.basename(percorso))[0]
    if multifoglio is None:
        if not per_mf:
            return []
        if len(per_mf) == 1:
            return list(next(iter(per_mf.values())))
        raise ValueError("Indicare il multifoglio: " + ", ".join(per_mf))
    out: list[str] = []
    for mf, fogli in per_mf.items():
        if mf.casefold() == multifoglio.casefold():
            out += fogli
    return out


# ---------------------------------------------------------------- stili tabelle

def _descrivi_stile(data: str | None) -> dict:
    """Tipo, stile AutoCAD e colonne visibili dall'XML di uno stile (niente XML intero)."""
    out = {"tipo": None, "stile_tabella": None, "colonne": [], "titolo": []}
    if not data:
        return out
    try:
        radice = ET.fromstring(data)
    except ET.ParseError:
        m = re.match(r"\s*<(\w+)", data)
        out["tipo"] = m.group(1) if m else None
        return out
    out["tipo"] = radice.tag
    out["stile_tabella"] = radice.get("AcadTableStyleName")
    for gruppo, chiave in (("vColumnsLayout", "colonne"), ("vTitleLayout", "titolo")):
        nodo = radice.find(gruppo)
        if nodo is not None:
            out[chiave] = [p.get("CString") for p in nodo
                           if (p.get("bool") or "").lower() == "true" and p.get("CString")]
    return out


def stili_tabelle(percorso: str) -> list[dict]:
    """Stili delle tabelle da Tables_Configurations.db: nomi, stato, colonne.

    ``stato`` "M" = stile in uso (una sola riga per tabella nelle commesse
    viste). Lista vuota se il file manca o e' il vecchio .mdb.
    """
    cfg = _cfg_obbligatoria(percorso)
    f = _figlio(cfg, "Tables_Configurations.db")
    if not f:
        return []
    con = sqlite3.connect("file:" + f.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        tabelle = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = []
        for t in tabelle:
            cols = {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            if "name" not in cols:
                continue
            c_data = f'"{cols["data"]}"' if "data" in cols else "NULL"
            c_stato = f'"{cols["state"]}"' if "state" in cols else "NULL"
            for nome, data, stato in con.execute(
                    f'SELECT "{cols["name"]}", {c_data}, {c_stato} FROM "{t}"'):
                d = _descrivi_stile(data if isinstance(data, str) else None)
                out.append({"tabella": t, "nome": nome, "stato": stato or "",
                            "in_uso": (stato or "").upper() == "M", **d})
        return out
    finally:
        con.close()
