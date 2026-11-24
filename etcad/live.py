"""Modalita' live: automazione di SPAC aperto via COM (pywin32).

SPAC Automazione CAD e' un AutoCAD OEM: registra un proprio ProgID COM
(non "AutoCAD.Application"). Il ProgID si ricava dal registro cercando i
server COM (LocalServer32) che puntano all'eseguibile Scad*.exe di SPAC;
si puo' forzare con la variabile d'ambiente SPAC_PROGID.
Se SPAC e' chiuso ogni funzione restituisce un errore esplicito che invita
a usare la modalita' headless.
"""
from __future__ import annotations

import os
import tempfile
import time

PROGIDS_NOTI = [
    "AutoCAD.Application.25",
    "AutoCAD.Application.24",
    "AutoCAD.Application",
]


def progid_candidati() -> list[str]:
    """ProgID COM di SPAC: variabile SPAC_PROGID, registro, poi generici."""
    out = []
    env = os.environ.get("SPAC_PROGID", "")
    if env:
        out += [p.strip() for p in env.split(";") if p.strip()]
    try:
        import winreg
        from .config import find_installs
        radici = [os.path.normcase(i.root) for i in find_installs()]
        exe = [os.path.normcase(os.path.basename(i.cad_exe)) for i in find_installs() if i.cad_exe]
        k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID")
        n = winreg.QueryInfoKey(k)[0]
        for i in range(n):
            try:
                clsid = winreg.EnumKey(k, i)
                ls = winreg.OpenKey(k, clsid + r"\LocalServer32")
                srv = os.path.normcase(str(winreg.QueryValueEx(ls, "")[0]))
            except OSError:
                continue
            if not any(r in srv for r in radici) and not any(e and e in srv for e in exe):
                continue
            for sub in ("ProgID", "VersionIndependentProgID"):
                try:
                    pk = winreg.OpenKey(k, clsid + "\\" + sub)
                    pid = winreg.QueryValueEx(pk, "")[0]
                    if pid and pid not in out:
                        out.append(pid)
                except OSError:
                    continue
    except Exception:  # noqa: BLE001 - registro non leggibile / non Windows
        pass
    return out + [p for p in PROGIDS_NOTI if p not in out]


def _app():
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise RuntimeError("pywin32 non installato nel .venv") from e
    last = None
    for pid in progid_candidati():
        try:
            return win32com.client.GetActiveObject(pid)
        except Exception as e:  # noqa: BLE001 - prova il ProgID successivo
            last = e
    raise RuntimeError(
        "SPAC non raggiungibile via COM (e' aperto?). ProgID provati: "
        f"{progid_candidati()[:6]}. Usa la modalita' headless oppure apri SPAC "
        "(o imposta SPAC_PROGID).") from last


def status() -> dict:
    """Stato live: visibile, documenti aperti, disegno attivo."""
    try:
        app = _app()
    except RuntimeError as e:
        return {"live": False, "errore": str(e)}
    try:
        docs = [{"nome": d.Name, "percorso": d.FullName} for d in app.Documents]
        try:
            active = app.ActiveDocument.FullName
        except Exception:  # noqa: BLE001
            active = ""
        return {"live": True, "visibile": bool(app.Visible),
                "documenti": docs, "attivo": active,
                "versione": str(app.Version)}
    except Exception as e:  # noqa: BLE001
        return {"live": False, "errore": f"COM errore: {e}"}


def send_command(cmd: str) -> dict:
    """Invia un comando al SPAC aperto (equivale a digitarlo). Asincrono."""
    app = _app()
    doc = app.ActiveDocument
    doc.SendCommand(cmd if cmd.endswith((" ", "\n")) else cmd + "\n")
    return {"ok": True, "inviato": cmd}


def open_dwg(path: str, read_only: bool = False) -> dict:
    app = _app()
    doc = app.Documents.Open(os.path.abspath(path), read_only)
    return {"ok": True, "attivo": doc.FullName}


def exec_lisp(expr: str, timeout: float = 30.0) -> dict:
    """Valuta un'espressione AutoLISP nel disegno attivo e ne ritorna il valore.

    SendCommand e' asincrono: il risultato viene scritto su un file
    temporaneo dal LISP stesso e letto qui con attesa.
    """
    app = _app()
    fd, out = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    os.unlink(out)
    p = out.replace("\\", "/")
    lisp = ('(progn (setq *mcp-f* (open "%s" "w")) '
            '(write-line (vl-prin1-to-string %s) *mcp-f*) '
            '(close *mcp-f*) (princ))\n' % (p, expr))
    app.ActiveDocument.SendCommand(lisp)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(out):
            time.sleep(0.1)
            try:
                with open(out, encoding="cp1252", errors="replace") as f:
                    val = f.read().strip()
                os.unlink(out)
                return {"ok": True, "valore": val}
            except OSError:
                pass
        time.sleep(0.2)
    return {"ok": False, "errore": f"nessun risultato entro {timeout}s "
            "(comando in corso o SPAC occupato?)"}
