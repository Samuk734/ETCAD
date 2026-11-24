"""Test di commessa_cfg e controlli_db su una commessa sintetica in tmp_path.

Le tabelle hanno le stesse colonne dei dbSPAC.db reali (SPAC 2025, db 35.0.3;
Componenti per il formato 33.x).
"""
import os
import sqlite3

import pytest

from etcad import commessa_cfg as cc
from etcad import controlli_db as cd

COL_COMP = ("Dwg Handle IDQuadro NomeBlocco Foglio X Y Supporto Raggio Famiglia NumI NumO "
            "NumM Livello NoteM NoteV Utenza Rip Costruttore Tipo Sezione Descrizione "
            "BLK_RIENTRO Nome Pres Diramazione CRS Funzione1 Funzione2 MADRE Libreria "
            "FmtSigla SpacIgnora Codice Attributi XData QRCodeHandle IDModuloRack").split()
SCHEMA = {
    "MDBINFO": "tagName tagValue".split(),
    "Dwg": "Nome Ordine Scandito DaAggiornare Prefisso Version Locked Vars".split(),
    "PlotSequence": "ID riga multifoglio foglio descrizione layer".split(),
    "Structure": ("DaAggiornare Dwg Handle Foglio NomeBlocco Attributi Lista XRecords "
                  "DaElaborare").split(),
    "Struttura_Quadri": ("IDQuadro Ubicazione Quadro GruppoFunzione Descrizione QuadriUtenza "
                         "Principale Ordine BlkPlantDiagram CodiceDistinta").split(),
    "Morsettiere": ("IDQuadro Nome Tipologia Descrizione Costruttore Tipo BLK_RIENTRO SpacIgnora "
                    "BlkPlantDiagram IDQuadroAssemblato NomeAssemblato CavoConnettorizzato "
                    "NomeConnAssemblato").split(),
    "SegnaliniDatiCavo": ("Dwg Handle Foglio NomeCavo NomeVena Costruttore Modello Formazione "
                          "Note Quadro_C NumeroFilo NomeBlocco XData X Y XMLData "
                          "VoltageName").split(),
    "Elementi": COL_COMP + ["AttributiMultilinea"],
    "MorsConn": COL_COMP + ["AttributiMultilinea"],
    "Rimandi": COL_COMP + ["AttributiMultilinea"],
    "Cbl_Harness": ["Dwg", "Foglio", "Handle"],
    "Jumpers": ["Dwg", "Foglio", "Handle"],
}
Q = "#+QG#"


def _connetti_veloce(path):
    # senza journal: su Windows ogni file -journal passa dall'antivirus (~1 s)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    return con


def _crea_db(path, tabelle, righe):
    con = _connetti_veloce(path)
    for t in tabelle:
        cols = SCHEMA.get(t, COL_COMP)
        con.execute(f'CREATE TABLE "{t}" ({", ".join(f"{c!s} TEXT" for c in cols)})')
    for t, lst in righe.items():
        for r in lst:
            k = list(r)
            con.execute(f'INSERT INTO "{t}" ({", ".join(k)}) VALUES ({", ".join("?" * len(k))})',
                        [r[c] for c in k])
    con.commit()
    con.close()


def _rim(dwg, fg, nome, pres, reg, blocco=None, h=""):
    blocco = blocco or ("_SIGN_0" if pres == "P" else "_SIGN_1")
    return {"Dwg": dwg, "Handle": h, "NomeBlocco": blocco, "Foglio": fg, "Nome": nome,
            "Pres": pres, "Attributi": f'(("#LINEA" . "")("PRES" . "{pres}")'
                                      f'("REG" . "{reg}")("SEGNALE" . "{nome}"))'}


def _el(dwg, fg, nome, pres=None, madre=None, q=Q, blocco="KA1", lib="IEC"):
    return {"Dwg": dwg, "Handle": f"E{fg}{nome}{pres}", "IDQuadro": q, "NomeBlocco": blocco,
            "Foglio": fg, "Nome": nome, "Pres": pres, "MADRE": madre, "Libreria": lib}


def _mo(dwg, fg, nome, num, ni="1", no="1", q=Q, liv=None, rip=None, fam="MORSETTO"):
    return {"Dwg": dwg, "Handle": f"M{fg}{nome}{num}{liv}{rip}", "IDQuadro": q,
            "NomeBlocco": "_MORSZ0", "Foglio": fg, "Nome": nome, "NumM": num, "NumI": ni,
            "NumO": no, "Livello": liv, "Rip": rip, "Famiglia": fam}


def _layer(dwg, nome):
    return {"Dwg": dwg, "Foglio": nome, "Handle": "10",
            "Lista": f'((0 . "LAYER")(5 . "10")(2 . "{nome}")(70 . 0)(62 . 7))'}


@pytest.fixture
def commessa(tmp_path):
    """Commessa finta: dbSPAC.db + CFG con i casi di ogni controllo."""
    base = tmp_path / "Commessa" / "Lavori" / "Progetto"
    cfg = base / "CFG"
    cfg.mkdir(parents=True)
    rimandi = [
        _rim("MF1", "1", "100", "P", "2.0A"), _rim("MF1", "2", "100", "A", "1.9A"),  # ok
        _rim("MF1", "1", "101", "P", "9.0A"),                    # foglio 9 inesistente
        _rim("MF1", "3", "102", "A", "2.5C"),                    # nessuna partenza su 2
        _rim("MF1", "3", "103", "P", "/./"),                     # non collegato
        _rim("MF1", "2", "104", "P", "/./"),                     # non collegato + indizio
        _rim("MF1", "3", "104", "A", "2.18", blocco="_ALIM_1"),  # alimentazione: "foglio.n"
        _rim("MF1", "1", "", "P", "2.0A"),                       # senza segnale
    ]
    elementi = [
        _el("MF1", "1", "K1", "M"), _el("MF1", "2", "K1", "FA"),
        _el("MF1", "1", "K1", "R", madre="K1", blocco="RIF1_22"),
        _el("MF1", "1", "K2", "M"), _el("MF1", "2", "K2", "M"),  # sigla doppia
        _el("MF1", "3", "K9", "FA"),                              # contatto orfano
        _el("MF1", "3", "KX", "R", madre="K8"),                   # riferimento orfano
        _el("MF1", "3", "", "M", blocco="S4"),                    # madre senza sigla
        _el("MF1", "3", "", None, blocco="PUN_01", lib="BLK"),    # accessorio: normale
        _el("MF1", "4", "K3", "M"),                               # foglio 4 non in sequenza
        _el("MF2", "1", "K3", "M"),                               # copia in altro multifoglio
    ]
    morsetti = [
        _mo("MF1", "1", "X1", "1"), _mo("MF1", "1", "X1", "2"),
        _mo("MF1", "1", "X1", "3"), _mo("MF1", "2", "X1", "3"),   # doppio
        _mo("MF1", "1", "X1", "4", liv="1"), _mo("MF1", "1", "X1", "4", liv="2"),  # piani
        _mo("MF1", "1", "X1", "5"), _mo("MF1", "2", "X1", "5", rip="1"),           # ripetuto
        _mo("MF1", "2", "", "1", fam="SPINA"),                    # senza morsettiera
        _mo("MF1", "2", "", "", ni="", no="", q="", fam=None),    # blocco vuoto
        _mo("MF1", "2", "X1", ""),                                # senza numero
        _mo("MF1", "3", "X2", "1", ni="?", no="?"),               # filo non numerato, X2 non def.
    ]
    _crea_db(str(base / "dbSPAC.db"), SCHEMA, {
        "MDBINFO": [{"tagName": "VERSIONE", "tagValue": "35.0.3"}],
        "Dwg": [{"Nome": "MF1"}, {"Nome": "MF2"}],
        "PlotSequence": [{"ID": i, "riga": i, "multifoglio": "MF1", "foglio": f, "layer": f}
                         for i, f in enumerate(["1", "2", "3", "5"])],
        "Structure": [_layer("MF1", n) for n in ("0", "1", "2", "3", "4", "Defpoints", "1_REV_00")],
        "Struttura_Quadri": [{"IDQuadro": Q, "Quadro": "+QG"}],
        "Morsettiere": [{"IDQuadro": Q, "Nome": "X1", "Tipologia": "MORSETTIERA"}],
        "Rimandi": rimandi, "Elementi": elementi, "MorsConn": morsetti,
    })
    ini = "\r\n".join([
        "; -----------------------------------------------",
        "; Configurazione di prova, visibilità dei TAG",
        "[Configurazione_Simboli]",
        "Nome_file_PRE=IEC.PRE",
        "[Disponibilità_Phoenix_Contact]",
        "Abilita=1",
        "[Numerazione_Fogli]",
        "XY_Numero_Foglio=(402.5 30.0)",
        "XY_Foglio_Seguente=(402.5 15)",
        "XY_Totale_Fogli=(382.5 15.0)",
        "XY_Foglio_Precedente=(382.5 30.0)",
        "Altezza_Numero_Foglio=3.5",
        "Altezza_Foglio_Seguente=",
        "what=5",
        "Pref_Fg_In_Mf=1",
        "Opz_Numero_Fogli=ult",
        "riga senza uguale",
        "[Siglatura_Simboli]",
        "No_Sigle_Doppie=1",
        "Separatore=.",
        "[Preferenze]",
        "Attiva SelectionPreview=1",
        "AbilitaCheckSezioniColl=0",
        "[Numerazione_Filo_Morsetti]",
        "Evidenzia_Fili_Non_Num=1",
        "evidenzia_fili_non_num=0",
        'Colori_Ignorati=("NOT" "5")',
        'Abbinamento_Tag_Blocchi=(("INP*" . "INPUT") ("OUT*" . "OUTPUT"))',
        "[Configurazione_Ricerca_Rami]",
        "Max_Fili_Non_Num=500",
        "[Cross_Reference]",
        'Separators=("." "-" "/")',
        "Fg_Cat_Separator=.",
        "On_Line_Cross=1",
        "Use_Pins=0",
        "[Scheme_Layout]",
        "DL_point=(20 40)",
        "UR_point=(410 275)",
        "Divisions=(10 6)",
        "[Distinta_Lista_Sigla]",
        "Campi=Sigla[V];Codice[V]",
        "[Output_Commesse_File]",
        "Rotazione=90",
        "",
    ])
    (cfg / "spac_cfg.ini").write_bytes(ini.encode("cp1252"))
    (cfg / "DCL_CFG.INI").write_bytes(
        b"[ARX_NEW_PLOT]\r\nListaLayerNotSelected=MF1|1:MF1|2:mf2|10\r\n"
        b"[ARX_TRAD]\r\nListaLayerNotSelected=\r\n[CablesMaterial]\r\nSupplierFilterEnabled=0\r\n")
    xml = ('<SpAcDbTableDBCMaterialsConfiguration AcadTableStyleName="Standard" Name="IT">'
           '<vColumnsLayout><XMLPairCStringBool bool="True" CString="Quantita"/>'
           '<XMLPairCStringBool bool="False" CString="Descrizione"/></vColumnsLayout>'
           '<vTitleLayout><XMLPairCStringBool bool="True" CString="Sigla"/></vTitleLayout>'
           '</SpAcDbTableDBCMaterialsConfiguration>')
    con = _connetti_veloce(str(cfg / "Tables_Configurations.db"))
    con.execute('CREATE TABLE DBCenter ("Name" TEXT(255), "Data" TEXT, "State" TEXT(1))')
    con.executemany("INSERT INTO DBCenter VALUES (?,?,?)",
                    [("Default", xml, ""), ("IT - 1 Livello", xml, "M")])
    con.commit()
    con.close()
    (base / "MF1.dwg").write_bytes(b"")
    (base / "MF2.dwg").write_bytes(b"")
    return base


# ---------------------------------------------------------------- commessa_cfg

def test_interpreta_valore_lisp():
    iv = cc.interpreta_valore
    assert iv("(402.5 30.0)") == [402.5, 30.0]
    assert iv("(10 6)") == [10, 6]
    assert iv('("NOT" "5")') == ["NOT", "5"]
    assert iv('(("INP*" . "INPUT") ("OUT*" . "OUTPUT"))') == [("INP*", "INPUT"),
                                                               ("OUT*", "OUTPUT")]
    assert iv('(("QUADRO" ";;0;;"))') == [["QUADRO", ";;0;;"]]
    assert iv("((50 260 0) (380 260 0))") == [[50, 260, 0], [380, 260, 0]]
    assert iv("Sigla[V];Codice[V]") == "Sigla[V];Codice[V]"
    assert iv("(rotta") == "(rotta"          # lista non chiusa: testo com'e'
    assert iv("") == "" and iv(None) is None


def test_leggi_ini_tollerante(commessa):
    ini = cc.leggi_ini(str(commessa / "CFG" / "spac_cfg.ini"))
    assert ini["codifica"] == "cp1252"
    sez = ini["sezioni"]
    assert "Disponibilità_Phoenix_Contact" in sez
    assert sez["Preferenze"]["Attiva SelectionPreview"] == "1"
    assert sez["Distinta_Lista_Sigla"]["Campi"] == "Sigla[V];Codice[V]"  # ';' non e' commento
    # chiave doppia con maiuscole diverse: vale la prima, la seconda e' annotata
    assert sez["Numerazione_Filo_Morsetti"]["Evidenzia_Fili_Non_Num"] == "1"
    assert [d["chiave"] for d in ini["duplicati"]] == ["evidenzia_fili_non_num"]
    assert [r["testo"] for r in ini["righe_ignorate"]] == ["riga senza uguale"]


def test_leggi_ini_bom_e_sezione_vuota(tmp_path):
    p = tmp_path / "x.ini"
    p.write_bytes("\ufeffa=1\r\n[S]\r\nb=è\r\n[s]\r\nc=2\r\n".encode("utf-8"))
    ini = cc.leggi_ini(str(p))
    assert ini["codifica"] == "utf-8-sig"
    assert ini["sezioni"][""] == {"a": "1"}
    assert ini["sezioni"]["S"] == {"b": "è", "c": "2"}  # sezioni ripetute unite


def test_trova_cfg_e_db(commessa, tmp_path):
    cfg = str(commessa / "CFG")
    for p in (commessa, commessa / "MF1.dwg", commessa / "dbSPAC.db", commessa / "CFG",
              commessa / "CFG" / "spac_cfg.ini", tmp_path / "Commessa"):
        assert cc.trova_cfg(str(p)) == cfg, p
    assert cc.trova_db(str(commessa / "MF1.dwg")) == str(commessa / "dbSPAC.db")
    assert cc.trova_db(cfg) == str(commessa / "dbSPAC.db")
    vuota = tmp_path / "vuota"
    vuota.mkdir()
    assert cc.trova_cfg(str(vuota)) is None
    assert cc.trova_cfg(str(commessa / "non_esiste.dwg")) is None
    with pytest.raises(FileNotFoundError):
        cc.leggi_config(str(vuota))
    # due commesse sotto la stessa cartella: nessuna scelta arbitraria
    altra = tmp_path / "Commessa" / "Lavori" / "Altro" / "CFG"
    altra.mkdir(parents=True)
    (altra / "spac_cfg.ini").write_text("[A]\nb=1\n")
    with pytest.raises(ValueError):
        cc.trova_cfg(str(tmp_path / "Commessa"))


def test_leggi_config_riassunto(commessa):
    c = cc.leggi_config(str(commessa))
    assert c["cartella_cfg"] == str(commessa / "CFG")
    assert c["db"] == str(commessa / "dbSPAC.db")
    assert set(c["file"]) == {"DCL_CFG.INI", "spac_cfg.ini"}
    r = c["riassunto"]
    nf = r["numerazione_fogli"]
    assert nf["numero_foglio"] == (402.5, 30.0) and nf["foglio_seguente"] == (402.5, 15.0)
    assert nf["altezze"]["numero_foglio"] == 3.5 and nf["altezze"]["foglio_seguente"] is None
    assert nf["what"] == 5 and nf["prefisso_foglio_in_mf"] is True
    assert nf["rotazione"] is None        # non presa da [Output_Commesse_File]
    assert nf["opzione_numero_fogli"] == "ult"
    k = r["controlli"]
    assert k["no_sigle_doppie"] is True and k["evidenzia_fili_non_num"] is True
    assert k["max_fili_non_num"] == 500 and k["check_sezioni_collegamenti"] is False
    x = r["cross_reference"]
    assert x["separatori"] == [".", "-", "/"] and x["separatore_foglio_catenaria"] == "."
    assert x["on_line_cross"] is True and x["use_pins"] is False
    lay = r["layout_schema"]
    assert lay["divisioni"] == (10, 6) and lay["larghezza_colonna"] == 39.0
    assert abs(lay["altezza_riga"] - 235 / 6) < 1e-3
    assert r["numerazione_fili"]["colori_ignorati"] == ["NOT", "5"]
    assert r["simboli"]["file_pre"] == "IEC.PRE"
    assert r["fogli_non_selezionati"]["stampa"] == {"MF1": ["1", "2"], "mf2": ["10"]}
    assert r["fogli_non_selezionati"]["traduzione"] == {}
    stili = c["stili_tabelle"]
    assert [(s["nome"], s["in_uso"]) for s in stili] == [("Default", False),
                                                         ("IT - 1 Livello", True)]
    assert stili[0]["colonne"] == ["Quantita"] and stili[0]["titolo"] == ["Sigla"]
    assert "Data" not in stili[0]            # niente XML intero


def test_fogli_esclusi_stampa(commessa):
    assert cc.fogli_esclusi_stampa(str(commessa), "mf1") == ["1", "2"]
    assert cc.fogli_esclusi_stampa(str(commessa / "MF2.dwg")) == ["10"]  # dal nome del DWG
    assert cc.fogli_esclusi_stampa(str(commessa), "NESSUNO") == []
    with pytest.raises(ValueError):
        cc.fogli_esclusi_stampa(str(commessa))   # due multifogli: quale?
    assert cc.fogli_esclusi_per_multifoglio(str(commessa)) == {"MF1": ["1", "2"],
                                                               "mf2": ["10"]}
    with pytest.raises(ValueError):
        cc.fogli_esclusi_per_multifoglio(str(commessa), "boh")


# ---------------------------------------------------------------- controlli_db

def _per_nome(ris):
    return {c["controllo"]: c for c in ris["controlli"]}


def test_controlli_db_sintetico(commessa):
    ris = cd.esegui(str(commessa / "dbSPAC.db"))
    assert ris["versione_db"] == "35.0.3" and ris["struttura"] == "Elementi/MorsConn/Rimandi"
    assert ris["cartella_cfg"] == str(commessa / "CFG")
    assert ris["esito"] == "fail"
    c = _per_nome(ris)
    assert set(c) == {f.__name__.removeprefix("controlla_") for f in cd.CONTROLLI}
    for v in c.values():
        assert v["esito"] in ("pass", "warn", "fail") and v["spiegazione"]
        assert len(v["dettaglio"]) <= cd.MAX_RIGHE

    nc = c["rimandi_non_collegati"]
    assert (nc["n"], nc["esito"]) == (3, "warn")
    assert sorted(d["segnale"] for d in nc["dettaglio"]) == ["", "103", "104"]
    p104 = next(d for d in nc["dettaglio"] if d["segnale"] == "104")
    assert "tipi diversi" in p104["indizio"]

    rif = c["rimandi_riferimenti"]
    assert (rif["n"], rif["esito"]) == (2, "fail")
    motivi = {d["segnale"]: d["motivo"] for d in rif["dettaglio"]}
    assert motivi["101"] == "il foglio 9 non esiste"
    assert motivi["102"] == "nessuna partenza '102' sul foglio 2"

    sd = c["componenti_sigla_doppia"]
    assert sd["n"] == 1 and sd["dettaglio"][0]["sigla"] == "K2"
    assert sd["in_piu_multifogli"] == 1 and sd["opzione_no_sigle_doppie"] is True

    orf = c["contatti_senza_madre"]
    assert sorted(d["sigla"] for d in orf["dettaglio"]) == ["K8", "K9"]

    ss = c["componenti_senza_sigla"]
    assert (ss["n"], ss["accessori"]) == (1, 1) and ss["dettaglio"][0]["blocco"] == "S4"

    sm = c["morsetti_senza_morsettiera"]
    assert (sm["n"], sm["vuoti"]) == (2, 1)

    dop = c["morsetti_doppi"]
    assert (dop["n"], dop["esito"]) == (1, "fail")
    assert dop["dettaglio"][0]["morsetto"] == "3" and dop["dettaglio"][0]["fogli"] == ["1", "2"]

    assert c["morsetti_senza_numero"]["n"] == 1
    fnn = c["fili_non_numerati"]
    assert fnn["n"] == 1 and fnn["opzione_evidenzia_fili_non_num"] is True
    nd = c["morsettiere_non_definite"]
    assert [d["morsettiera"] for d in nd["dettaglio"]] == ["X2"]

    seq = c["sequenza_stampa"]
    assert sorted((d["foglio"], d["motivo"]) for d in seq["dettaglio"]) == [
        ("4", "foglio con dati non in sequenza"), ("5", "in sequenza ma senza layer nel DWG")]
    assert seq["senza_sequenza"] == ["MF2"]


def test_controlli_db_multifoglio(commessa):
    db = str(commessa / "dbSPAC.db")
    c = _per_nome(cd.esegui(db, "mf2", usa_cfg=False))
    assert all(v["n"] == 0 for v in c.values()), {k: v["n"] for k, v in c.items()}
    assert "in_piu_multifogli" not in c["componenti_sigla_doppia"]
    assert "opzione_no_sigle_doppie" not in c["componenti_sigla_doppia"]
    c1 = _per_nome(cd.esegui(db, "MF1"))
    assert c1["rimandi_riferimenti"]["n"] == 2 and c1["morsetti_doppi"]["n"] == 1
    with pytest.raises(ValueError):
        cd.esegui(db, "NON_ESISTE")
    with pytest.raises(FileNotFoundError):
        cd.esegui(str(commessa / "manca.db"))


def test_controlli_db_formato_componenti(tmp_path):
    """db 33.x: rimandi, morsetti ed elementi tutti in Componenti."""
    db = str(tmp_path / "dbSPAC.db")
    righe = [_rim("MF", "1", "200", "P", "/./"),
             {"Dwg": "MF", "NomeBlocco": "_SIGN_1", "Foglio": "1", "Nome": "201", "Pres": "A"},
             _mo("MF", "1", "X1", "1", ni="?", no="?"),
             _el("MF", "1", "K1", "FA")]
    _crea_db(db, ["MDBINFO", "Dwg", "PlotSequence", "Structure", "Morsettiere",
                  "SegnaliniDatiCavo", "Componenti"],
             {"MDBINFO": [{"tagName": "VERSIONE", "tagValue": "33.0.2"}],
              "Dwg": [{"Nome": "MF"}], "Componenti": righe,
              "Morsettiere": [{"IDQuadro": Q, "Nome": "X1"}]})
    ris = cd.esegui(db)
    assert ris["struttura"] == "Componenti" and ris["versione_db"] == "33.0.2"
    c = _per_nome(ris)
    nc = {d["segnale"]: d["motivo"] for d in c["rimandi_non_collegati"]["dettaglio"]}
    assert nc == {"200": "riferimento vuoto o '/./'", "201": "attributo REG assente"}
    assert c["fili_non_numerati"]["n"] == 1
    assert c["contatti_senza_madre"]["n"] == 1
    assert c["morsettiere_non_definite"]["n"] == 0
    assert c["sequenza_stampa"]["senza_sequenza"] == ["MF"]


def test_db_aperto_in_sola_lettura(commessa):
    db = commessa / "dbSPAC.db"
    prima = (os.path.getmtime(db), db.read_bytes())
    cd.esegui(str(db))
    cc.leggi_config(str(commessa))
    assert (os.path.getmtime(db), db.read_bytes()) == prima


# ---------------------------------------------------------------- dati reali
