"""Conversione DXF <-> DWG nativo senza GUI.

Backend (in ordine di preferenza con backend="auto"):
- "spac": accoreconsole.exe dell'installazione SPAC (motore AutoCAD OEM
  di SPAC: DWG garantito compatibile con SPAC);
- "oda":  ODA File Converter (gratuito, www.opendesign.com), usato quando
  SPAC non e' installato (es. PC di sviluppo).

Tutte le conversioni lavorano in cartelle temporanee con percorsi ASCII
(accoreconsole e i file .scr non gestiscono bene accenti e caratteri
speciali) e spostano i risultati solo alla fine: gli originali non vengono
mai aperti in scrittura.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import time

VERSIONI_ODA = {"2000": "ACAD2000", "2004": "ACAD2004", "2007": "ACAD2007",
                "2010": "ACAD2010", "2013": "ACAD2013", "2018": "ACAD2018"}


def trova_oda() -> str:
    """Percorso di ODAFileConverter.exe ('' se assente).

    Ordine: variabile ODA_FILE_CONVERTER, Program Files\\ODA\\*,
    %LOCALAPPDATA%\\Programs\\ODAFileConverter*, cartella tools del progetto.
    """
    env = os.environ.get("ODA_FILE_CONVERTER", "")
    if env and os.path.isfile(env):
        return env
    cand = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        cand += glob.glob(os.path.join(pf, "ODA", "*", "ODAFileConverter.exe"))
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        cand += glob.glob(os.path.join(la, "Programs", "ODAFileConverter*",
                                       "ODAFileConverter.exe"))
        cand += glob.glob(os.path.join(la, "Programs", "ODA", "*",
                                       "ODAFileConverter.exe"))
    qui = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand += glob.glob(os.path.join(qui, "tools", "ODA*", "ODAFileConverter.exe"))
    cand = [c for c in cand if os.path.isfile(c)]
    return sorted(cand)[-1] if cand else ""


def trova_console() -> str:
    from .config import default_install
    inst = default_install()
    return inst.console_exe if inst and inst.has_console else ""


def backend_disponibili() -> dict:
    return {"spac": trova_console(), "oda": trova_oda()}


def _scegli(backend: str) -> str:
    disp = backend_disponibili()
    if backend == "auto":
        for b in ("spac", "oda"):
            if disp[b]:
                return b
        raise RuntimeError(
            "Nessun convertitore DWG: serve SPAC (accoreconsole.exe) oppure "
            "ODA File Converter (gratuito). Imposta ODA_FILE_CONVERTER o "
            "SPAC_ROOT se installati in percorsi non standard.")
    if not disp.get(backend):
        raise RuntimeError(f"backend '{backend}' non disponibile: {disp}")
    return backend


def _tmpdir(prefisso: str) -> str:
    base = os.environ.get("ETCAD_TEMP") or tempfile.gettempdir()
    try:
        base.encode("ascii")
    except UnicodeEncodeError:
        base = os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "etcad_tmp")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefisso, dir=base)


def _oda(exe: str, in_dir: str, out_dir: str, versione: str, tipo: str,
         filtro: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [exe, in_dir, out_dir, versione, tipo, "0", "1", filtro],
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def dxf_a_dwg(dxf: list[str] | str, cartella_out: str, versione: str = "2018",
              backend: str = "auto", timeout: int = 600) -> dict:
    """Converte uno o piu' DXF in DWG nativi (stessi nomi, estensione .dwg)."""
    files = [dxf] if isinstance(dxf, str) else list(dxf)
    os.makedirs(cartella_out, exist_ok=True)
    b = _scegli(backend)
    tmp = _tmpdir("dxf2dwg_")
    t0 = time.time()
    out, errori = [], []
    try:
        in_dir = os.path.join(tmp, "in")
        o_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir)
        os.makedirs(o_dir)
        nomi = {}
        for i, f in enumerate(files):
            nome = f"f{i:03d}.dxf"
            shutil.copy2(f, os.path.join(in_dir, nome))
            nomi[nome] = os.path.splitext(os.path.basename(f))[0] + ".dwg"
        if b == "oda":
            ver = VERSIONI_ODA.get(str(versione), "ACAD2018")
            r = _oda(trova_oda(), in_dir, o_dir, ver, "DWG", "*.DXF", timeout)
            log = (r.stdout or "") + (r.stderr or "")
        else:
            from . import batch
            log = ""
            for nome in nomi:
                dst = os.path.join(o_dir, nome[:-4] + ".dwg")
                res = batch.run_script(os.path.join(in_dir, nome),
                                       batch.script_saveas_dwg(dst, str(versione)),
                                       timeout=timeout)
                log += res.get("stdout", "")[-500:]
        for nome, finale in nomi.items():
            src = os.path.join(o_dir, nome[:-4] + ".dwg")
            if os.path.isfile(src) and os.path.getsize(src) > 0:
                dst = os.path.join(cartella_out, finale)
                shutil.move(src, dst)
                out.append(dst)
            else:
                errori.append(finale)
        return {"ok": not errori, "backend": b, "dwg": out, "errori": errori,
                "secondi": round(time.time() - t0, 1),
                "log": log[-800:] if errori else ""}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def dwg_a_dxf(dwg: str, dxf_out: str, versione: str = "2018",
              backend: str = "auto", timeout: int = 900) -> str:
    """Converte un DWG in DXF (per lettura/modifica con ezdxf)."""
    if not os.path.isfile(dwg):
        raise FileNotFoundError(dwg)
    b = _scegli(backend)
    tmp = _tmpdir("dwg2dxf_")
    try:
        in_dir = os.path.join(tmp, "in")
        o_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir)
        os.makedirs(o_dir)
        src = os.path.join(in_dir, "d.dwg")
        shutil.copy2(dwg, src)
        dst = os.path.join(o_dir, "d.dxf")
        if b == "oda":
            ver = VERSIONI_ODA.get(str(versione), "ACAD2018")
            _oda(trova_oda(), in_dir, o_dir, ver, "DXF", "*.DWG", timeout)
        else:
            from . import batch
            batch.run_script(src, batch.script_dxf_out(dst), timeout=timeout)
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            raise RuntimeError(f"conversione DWG->DXF fallita ({b})")
        os.makedirs(os.path.dirname(os.path.abspath(dxf_out)) or ".", exist_ok=True)
        shutil.move(dst, dxf_out)
        return dxf_out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
