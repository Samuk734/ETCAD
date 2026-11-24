"""Vista dal vivo da riga di comando.

Server:   python -m etcad.vista [--porta 8765] [--no-browser] [--osserva] [file o cartelle...]

Comandi (con la vista gia' aperta; --agente NOME o ETCAD_VISTA_AGENTE per parlare come un agente):
  pensa "testo" | nota "testo"          ragionamento / azione nel pannello di Claude
  carica FILE [FOGLIO] [NOTA]           un DWG/DXF (multifoglio SPAC: una scheda per foglio)
  osserva FILE_O_CARTELLA [FILTRO]      ricarica a ogni salvataggio
  fogli                                 fogli aperti, agenti, richieste da leggere
  ascolta [--continuo] [--timeout S]    aspetta chat e richieste dalla pagina, una riga per evento
  scrivi "testo" [--rif N]              messaggio nella chat della pagina
  richieste [--tutte] [--non-segnare]   zone/punti/oggetti indicati dall'utente (JSON)
  rispondi ID "testo" [--aperta]        risposta sotto la richiesta (e in chat)
  evidenzia FOGLIO x0 y0 x1 y1 "testo"  indica una zona all'utente;  pulisci [FOGLIO]
  immagine FOGLIO [x0 y0 x1 y1] [--pxmm 8]   PNG della zona (stampa il percorso)
  controlla FOGLIO [x0 y0 x1 y1]        testi sovrapposti / toccati da linee
  cerca "testo" [--foglio F] [--mostra] testi nei fogli aperti
  agente NOME [--stato S] [--foglio F] [--attivita T]
  sfondo FOGLIO PDF [PAGINA] [--dx --dy --scala --opacita]   pagina del PDF sotto il foglio (senza PDF: toglie)
  svuota | chiudi | stato | apri | demo [cartella]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser

AZIONI = {"pensa", "nota", "carica", "osserva", "svuota", "chiudi", "stato", "apri", "demo", "fogli", "ascolta",
          "scrivi", "richieste", "rispondi", "evidenzia", "pulisci", "immagine", "controlla", "cerca", "agente",
          "sfondo"}


def _stampa(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str), flush=True)


def _riga(ev: dict) -> str:
    """Un evento della pagina su una riga (per il monitor di Claude Code)."""
    def piatto(s):
        return " ⏎ ".join(x.strip() for x in (s or "").splitlines() if x.strip())
    if ev["tipo"] == "chat":
        a = f" → {ev['a']}" if ev.get("a") else ""
        rif = f" (rif. richiesta #{ev['rif']})" if ev.get("rif") else ""
        return f"CHAT #{ev['id']}{a}{rif}: {piatto(ev['testo'])}"
    m = ev.get("meta") or {}
    dove = ev.get("foglio") or "?"
    if m.get("desc"):
        dove += f" «{m['desc']}»"
    z = ev.get("zona")
    zona = f", zona {z[0]:.1f},{z[1]:.1f} - {z[2]:.1f},{z[3]:.1f}" if z else ""
    n = (ev.get("contenuto") or {}).get("oggetti", 0)
    a = f" → {ev['agente']}" if ev.get("agente") else ""
    sk = f", schizzo dell'utente: {len(ev['schizzo'])} segni" if ev.get("schizzo") else ""
    return (f"RICHIESTA #{ev['id']}{a} ({ev.get('genere', ev.get('tipo'))}, {dove}{zona}, {n} oggetti{sk}): "
            f"{piatto(ev.get('testo')) or '(senza testo)'}  [dettagli: python -m etcad.vista richieste --tutte]")


def _comando(argv: list[str]) -> int:
    from . import (agente, apri, ascolta, attiva, carica, cerca, chat, chiudi, controlla, evidenzia, immagine,
                   nota, osserva, pensa, pulisci_evidenze, richieste, rispondi, sfondo, stato, svuota)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    az, arg = argv[0], argv[1:]
    ap = argparse.ArgumentParser(prog=f"python -m etcad.vista {az}")
    ap.add_argument("--agente", default=None)
    if az == "apri":
        ap.add_argument("--no-browser", action="store_true")
        a = ap.parse_args(arg)
        print(apri(browser=not a.no_browser))
        return 0
    if not attiva():
        print("La vista non e' aperta: python -m etcad.vista", file=sys.stderr)
        return 1
    if az in ("pensa", "nota", "scrivi"):
        ap.add_argument("testo", nargs="+")
        ap.add_argument("--rif", type=int, default=None)
        a = ap.parse_args(arg)
        t = " ".join(a.testo)
        if az == "scrivi":
            chat(t, a.agente, a.rif)
        else:
            (pensa if az == "pensa" else nota)(t, a.agente)
    elif az == "carica":
        ap.add_argument("file")
        ap.add_argument("foglio", nargs="?", default="")
        ap.add_argument("nota", nargs="*")
        ap.add_argument("--singolo", action="store_true", help="non dividere in fogli")
        a = ap.parse_args(arg)
        _stampa(carica(a.file, a.foglio, " ".join(a.nota), multifoglio=not a.singolo and "auto", agente=a.agente))
    elif az == "osserva":
        ap.add_argument("percorso")
        ap.add_argument("filtro", nargs="?", default="")
        a = ap.parse_args(arg)
        _stampa(osserva(a.percorso, "", a.filtro))
    elif az in ("fogli", "stato"):
        _stampa(stato())
    elif az == "ascolta":
        ap.add_argument("--continuo", action="store_true", help="non uscire dopo il primo evento")
        ap.add_argument("--timeout", type=float, default=0, help="secondi (0 = senza limite)")
        a = ap.parse_args(arg)
        fine = time.time() + a.timeout if a.timeout else None
        giu = False
        while True:
            resto = 25.0 if fine is None else max(0.0, min(25.0, fine - time.time()))
            try:
                if not attiva(2.0):
                    raise ConnectionError("vista chiusa")
                ev = ascolta(resto, a.agente)
                if giu:
                    print("(vista di nuovo raggiungibile: ascolto ripreso)", flush=True)
                    giu = False
            except Exception as ex:  # noqa: BLE001 - vista riavviata: si riprova, un solo avviso
                if not giu:
                    print(f"(vista non raggiungibile, riprovo: {ex})", flush=True)
                    giu = True
                time.sleep(3)
                ev = []
            for e in ev:
                print(_riga(e), flush=True)
            if (ev and not a.continuo) or (fine is not None and time.time() >= fine):
                break
    elif az == "richieste":
        ap.add_argument("--tutte", action="store_true")
        ap.add_argument("--non-segnare", action="store_true")
        a = ap.parse_args(arg)
        _stampa(richieste(a.tutte, not a.non_segnare))
    elif az == "rispondi":
        ap.add_argument("id", type=int)
        ap.add_argument("testo", nargs="+")
        ap.add_argument("--aperta", action="store_true", help="non segnarla come fatta")
        a = ap.parse_args(arg)
        _stampa(rispondi(a.id, " ".join(a.testo), not a.aperta, a.agente))
    elif az == "evidenzia":
        ap.add_argument("foglio")
        ap.add_argument("zona", nargs=4, type=float)
        ap.add_argument("testo", nargs="*")
        a = ap.parse_args(arg)
        _stampa(evidenzia(a.foglio, a.zona, " ".join(a.testo), a.agente))
    elif az == "pulisci":
        ap.add_argument("foglio", nargs="?", default="")
        a = ap.parse_args(arg)
        pulisci_evidenze(a.foglio)
    elif az in ("immagine", "controlla"):
        ap.add_argument("foglio")
        ap.add_argument("zona", nargs="*", type=float)
        ap.add_argument("--pxmm", type=float, default=8.0)
        ap.add_argument("--zitto", action="store_true", help="controlla: non mostrare i risultati nella pagina")
        a = ap.parse_args(arg)
        zona = a.zona if len(a.zona) == 4 else None
        if az == "immagine":
            _stampa(immagine(a.foglio, zona, a.pxmm))
        else:
            r = controlla(a.foglio, zona, not a.zitto, a.agente)
            _stampa({"foglio": r["foglio"], "n": len(r["problemi"]),
                     "problemi": [{"msg": p["msg"], "b": p["b"]} for p in r["problemi"]]})
    elif az == "cerca":
        ap.add_argument("testo", nargs="+")
        ap.add_argument("--foglio", default="")
        ap.add_argument("--mostra", action="store_true")
        a = ap.parse_args(arg)
        _stampa(cerca(" ".join(a.testo), a.foglio, a.mostra))
    elif az == "agente":
        ap.add_argument("nome")
        ap.add_argument("--stato", default=None)
        ap.add_argument("--foglio", default=None)
        ap.add_argument("--attivita", default=None)
        ap.add_argument("--modello", default=None, help="es. claude-sonnet-5-5")
        ap.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
        ap.add_argument("--compito", default=None)
        a = ap.parse_args(arg)
        _stampa(agente(a.nome, a.stato, a.foglio, a.attivita, a.modello, a.effort, a.compito))
    elif az == "sfondo":
        ap.add_argument("foglio")
        ap.add_argument("pdf", nargs="?", default="")
        ap.add_argument("pagina", nargs="?", type=int, default=1)
        for k, d in (("--dx", 0.0), ("--dy", 0.0), ("--scala", 1.0), ("--opacita", 0.45)):
            ap.add_argument(k, type=float, default=d)
        a = ap.parse_args(arg)
        _stampa(sfondo(a.foglio, a.pdf, a.pagina, a.dx, a.dy, a.scala, a.opacita))
    elif az == "demo":
        from .demo import esegui
        _stampa(esegui(arg[0] if arg else ""))
    elif az == "svuota":
        svuota(arg[0] if arg else "")
    elif az == "chiudi":
        chiudi()
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in AZIONI:
        sys.exit(_comando(sys.argv[1:]))
    from . import server
    ap = argparse.ArgumentParser(description="ETCAD vista dal vivo (server locale)")
    ap.add_argument("--porta", type=int, default=int(os.environ.get("ETCAD_VISTA_PORTA", server.PORTA)))
    ap.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    ap.add_argument("--osserva", action="store_true", help="ricarica i file quando cambiano")
    ap.add_argument("file", nargs="*", help="DXF/DWG (o cartelle) da mostrare subito")
    a = ap.parse_args()
    srv = server.avvia(a.porta)
    for f in a.file:
        if a.osserva or os.path.isdir(f):
            server.STATO.osserva(f)
        else:
            server.STATO.carica(f, anima=False)
    url = f"http://127.0.0.1:{a.porta}/"
    print(f"ETCAD vista dal vivo: {url}", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
