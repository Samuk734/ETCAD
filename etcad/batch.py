"""Modalita' headless DWG: script batch via accoreconsole.exe (SPAC chiuso).

accoreconsole e' la console AutoCAD senza GUI fornita con SPAC (motore
AutoCAD OEM):  accoreconsole.exe /i <disegno> /s <script.scr> [/l it-IT]

Regole per non far "appendere" la console (causa dei ~10 minuti a
conversione della versione precedente):
- stdin = DEVNULL: in un server MCP su stdio lo stdin e' il canale del
  protocollo; se la console lo eredita legge i messaggi JSON-RPC come comandi
  o resta in attesa;
- nessuna riga vuota finale: in uno script una riga vuota e' INVIO, che
  ripete l'ultimo comando (es. DXFOUT) lasciandolo in attesa del nome file;
- EXPERT 5 all'inizio (variabile di sessione, non salvata): niente
  richieste di conferma (sovrascrittura, blocchi ridefiniti). FILEDIA non
  si tocca: e' salvata nel registro e cambierebbe anche la GUI di SPAC;
- script e percorsi ASCII (file temporanei in cartelle ASCII);
- timeout con chiusura dell'intero albero di processi.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time

from .config import default_install

PREAMBOLO = "EXPERT\n5\n"


def _lingua() -> list[str]:
    lang = os.environ.get("SPAC_LINGUA", "it-IT").strip()
    return ["/l", lang] if lang else []


def _decodifica(b: bytes) -> str:
    """accoreconsole scrive in UTF-16LE (con byte nulli); a volte ANSI."""
    if not b:
        return ""
    if b.count(b"\x00") > len(b) // 4:
        try:
            return b.decode("utf-16-le", "replace").replace("\x00", "")
        except Exception:  # noqa: BLE001
            pass
    for enc in ("utf-8", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def percorso_ascii(p: str) -> str:
    """Percorso utilizzabile in uno script .scr: se contiene caratteri non
    ASCII prova il nome corto 8.3 di Windows."""
    try:
        p.encode("ascii")
        return p
    except UnicodeEncodeError:
        pass
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        esiste = os.path.exists(p)
        cartella = p if esiste else os.path.dirname(p)
        if ctypes.windll.kernel32.GetShortPathNameW(cartella, buf, 1024):
            corto = buf.value if esiste else os.path.join(buf.value, os.path.basename(p))
            corto.encode("ascii")
            return corto
    except Exception:  # noqa: BLE001
        pass
    raise ValueError(f"percorso non ASCII non utilizzabile nello script: {p}")


def _uccidi_albero(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def run_script(dwg_path: str, script_text: str, timeout: int = 300,
               read_only: bool = False, lingua: bool = True) -> dict:
    """Esegue uno script su un DWG/DXF a SPAC chiuso. Ritorna log e codici.

    Il testo viene ripulito dalle righe vuote finali (ripeterebbero
    l'ultimo comando) e preceduto dal PREAMBOLO (FILEDIA 0, EXPERT 5).
    """
    inst = default_install()
    if not inst or not inst.has_console:
        raise RuntimeError("accoreconsole.exe non trovata: installazione SPAC assente? "
                           "(imposta SPAC_ROOT se SPAC e' in un percorso non standard)")
    if not os.path.isfile(dwg_path):
        raise FileNotFoundError(dwg_path)
    corpo = PREAMBOLO + script_text.rstrip("\r\n \t") + "\n"
    base = os.environ.get("ETCAD_TEMP") or tempfile.gettempdir()
    fd, scr = tempfile.mkstemp(suffix=".scr", dir=base)
    with os.fdopen(fd, "w", encoding="cp1252", errors="strict", newline="\r\n") as f:
        f.write(corpo)
    cmd = [inst.console_exe, "/i", dwg_path, "/s", percorso_ascii(scr)]
    if lingua:
        cmd += _lingua()
    if read_only:
        cmd.append("/readonly")
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _uccidi_albero(p.pid)
            out, err = p.communicate()
            return {"ok": False, "errore": f"timeout dopo {timeout}s (processo chiuso)",
                    "secondi": round(time.time() - t0, 1),
                    "stdout": _decodifica(out)[-3000:]}
    finally:
        try:
            os.unlink(scr)
        except OSError:
            pass
    testo = _decodifica(out)
    if lingua and p.returncode != 0 and not testo.strip() and _lingua():
        # pacchetto lingua assente: riprova senza /l
        return run_script(dwg_path, script_text, timeout, read_only, lingua=False)
    return {"ok": p.returncode == 0, "codice": p.returncode,
            "secondi": round(time.time() - t0, 1),
            "stdout": testo[-4000:], "stderr": _decodifica(err)[-2000:]}


def _q(p: str) -> str:
    return '"' + percorso_ascii(p).replace("\\", "/") + '"'


def script_dxf_out(dxf_path: str) -> str:
    """Script: salva il disegno aperto come DXF (lettura con ezdxf).

    Stessa sequenza collaudata sulla macchina SPAC (nome file, precisione
    16), senza la riga vuota finale che ripeteva DXFOUT.
    """
    return f"_.DXFOUT\n{_q(dxf_path)}\n16\n_.QUIT\n"


def script_saveas_dwg(dwg_path: str, versione: str = "2018") -> str:
    """Script: salva il disegno aperto (DWG o DXF) come DWG nativo."""
    return f"_.SAVEAS\n{versione}\n{_q(dwg_path)}\n_.QUIT\n"


FORMATI_PDF = {(297, 210): "ISO full bleed A4 (297.00 x 210.00 MM)",
               (420, 297): "ISO full bleed A3 (420.00 x 297.00 MM)",
               (594, 420): "ISO full bleed A2 (594.00 x 420.00 MM)",
               (841, 594): "ISO full bleed A1 (841.00 x 594.00 MM)"}


def script_plot_pdf(pdf_path: str, larghezza: float = 297.0, altezza: float = 210.0,
                    x0: float = 0.0, y0: float = 0.0) -> str:
    """Script (sperimentale): stampa su PDF la finestra (x0,y0)-(x0+L,y0+H)
    dello spazio modello in scala 1:1, come la pagina del PDF originale.

    Serve a confrontare il DWG col PDF usando il motore di SPAC (font SHX
    veri). Richiede il plotter "DWG To PDF.pc3" (standard in SPAC).
    """
    chiave = min(FORMATI_PDF, key=lambda k: abs(k[0] - larghezza) + abs(k[1] - altezza))
    return ("_.-PLOT\n_Y\nModel\nDWG To PDF.pc3\n" + FORMATI_PDF[chiave] +
            "\n_M\n_L\n_N\n_W\n" + f"{x0:.3f},{y0:.3f}\n"
            f"{x0 + larghezza:.3f},{y0 + altezza:.3f}\n1:1\n0,0\n_Y\n"
            "monochrome.ctb\n_Y\n_A\n" + _q(pdf_path) + "\n_N\n_Y\n_.QUIT\n")


def script_audit() -> str:
    """Script: AUDIT con correzione + REGEN + DBSTATUS."""
    return "_.AUDIT\n_Y\n_.REGEN\n_.QSAVE\n_.QUIT\n"


def dwg_info_headless(dwg_path: str) -> dict:
    """Layouts, entita' e layer di un DWG (via DXF con SPAC o ODA)."""
    import ezdxf
    from . import dwg_io
    fd, dxf = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    try:
        dwg_io.dwg_a_dxf(dwg_path, dxf)
        doc = ezdxf.readfile(dxf)
        msp = doc.modelspace()
        tipi: dict = {}
        for e in msp:
            tipi[e.dxftype()] = tipi.get(e.dxftype(), 0) + 1
        return {"ok": True, "dxf_versione": doc.dxfversion,
                "layouts": [{"nome": lay.name} for lay in doc.layouts],
                "entita_model": len(msp), "per_tipo": tipi,
                "layers": [lay.dxf.name for lay in doc.layers][:300],
                "blocchi": [b.name for b in doc.blocks
                            if not b.name.startswith("*")][:300],
                "stili_testo": {s.dxf.name: s.dxf.font for s in doc.styles}}
    finally:
        try:
            os.unlink(dxf)
        except OSError:
            pass
