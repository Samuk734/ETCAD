"""Rilevamento installazioni SPAC e percorsi commesse.

Ordine di ricerca dell'installazione:
1. variabile d'ambiente SPAC_ROOT (cartella di installazione);
2. registro di Windows (programmi installati con "SPAC" nel nome);
3. percorsi noti (C:\\SPAC Automazione CAD 20xx, Program Files, C:\\SPAC20xx).

Cartella commesse: SPAC_COMMESSE (anche piu' cartelle separate da ';'),
altrimenti <installazione>\\Commesse.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

ANNI = ("2026", "2025", "2024", "2023", "2022", "2021", "2020")


@dataclass
class SpacInstall:
    year: str
    root: str
    cad_exe: str = ""
    console_exe: str = ""
    commesse: str = ""

    @property
    def has_console(self) -> bool:
        return bool(self.console_exe) and os.path.isfile(self.console_exe)


def _da_registro() -> list[str]:
    """Cartelle di installazione SPAC dal registro (sola lettura)."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non Windows
        return []
    out = []
    radici = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
              (winreg.HKEY_LOCAL_MACHINE,
               r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
              (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in radici:
        try:
            k = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(k)[0]):
            try:
                sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                nome = winreg.QueryValueEx(sk, "DisplayName")[0]
                if "spac" not in str(nome).lower():
                    continue
                loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                if loc and os.path.isdir(loc):
                    out.append(loc.rstrip("\\/"))
            except OSError:
                continue
    return out


def _candidati() -> list[str]:
    c = []
    env = os.environ.get("SPAC_ROOT", "")
    if env:
        c += [p for p in env.split(";") if p.strip()]
    c += _da_registro()
    c += [f"C:\\SPAC Automazione CAD {y}" for y in ANNI]
    pf = os.environ.get("ProgramFiles", "C:\\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    for base in (pf, pf86):
        c += [os.path.join(base, f"SPAC Automazione CAD {y}") for y in ANNI]
        c += glob.glob(os.path.join(base, "SDProget*", "*SPAC*"))
    c += [f"C:\\SPAC{y}" for y in ANNI] + ["C:\\SPAC"]
    visti, out = set(), []
    for p in c:
        k = os.path.normcase(os.path.abspath(p))
        if k not in visti and os.path.isdir(p):
            visti.add(k)
            out.append(p)
    return out


def _cerca_exe(root: str, anno: str) -> tuple[str, str]:
    """(Scad*.exe, accoreconsole.exe) sotto root, cartella Cad o radice."""
    cad, console = "", ""
    for sub in ("Cad", ""):
        d = os.path.join(root, sub) if sub else root
        if not os.path.isdir(d):
            continue
        if not cad:
            p = os.path.join(d, f"Scad{anno}.exe")
            if os.path.isfile(p):
                cad = p
            else:
                try:
                    cands = sorted(f for f in os.listdir(d)
                                   if f.lower().startswith("scad") and f.lower().endswith(".exe"))
                except OSError:
                    cands = []
                cad = os.path.join(d, cands[0]) if cands else ""
        if not console and os.path.isfile(os.path.join(d, "accoreconsole.exe")):
            console = os.path.join(d, "accoreconsole.exe")
    return cad, console


def find_installs() -> list[SpacInstall]:
    """Trova le installazioni SPAC Automazione CAD (la piu' recente prima)."""
    found: list[SpacInstall] = []
    for root in _candidati():
        m = re.search(r"(20\d\d)", root)
        anno = m.group(1) if m else ""
        cad, console = _cerca_exe(root, anno)
        if not cad and not console:
            continue
        found.append(SpacInstall(year=anno, root=root, cad_exe=cad,
                                 console_exe=console,
                                 commesse=os.path.join(root, "Commesse")))
    found.sort(key=lambda i: i.year, reverse=True)
    return found


def default_install() -> SpacInstall | None:
    inst = find_installs()
    return inst[0] if inst else None


def radici_commesse() -> list[str]:
    env = os.environ.get("SPAC_COMMESSE", "")
    out = [p for p in env.split(";") if p.strip() and os.path.isdir(p)]
    inst = default_install()
    if inst and os.path.isdir(inst.commesse):
        out.append(inst.commesse)
    return out


def is_spac_running() -> list[dict]:
    """Processi Scad*.exe in esecuzione (senza dipendenze esterne)."""
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Scad*", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("scad"):
            procs.append({"exe": parts[0], "pid": parts[1]})
    return procs


@dataclass
class Commessa:
    name: str
    path: str
    dwgs: list[str] = field(default_factory=list)
    dbs: list[str] = field(default_factory=list)


def list_commesse(commesse_root: str | None = None) -> list[Commessa]:
    radici = [commesse_root] if commesse_root else radici_commesse()
    out: list[Commessa] = []
    for root in radici:
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            try:
                files = os.listdir(p)
            except OSError:
                continue
            out.append(Commessa(
                name=name, path=p,
                dwgs=sorted(f for f in files if f.lower().endswith(".dwg")),
                dbs=sorted(f for f in files if f.lower().endswith((".db", ".sqlite"))),
            ))
    return out


def resolve_commessa(name_or_path: str) -> str:
    """Risolve nome commessa o percorso in directory esistente."""
    if os.path.isdir(name_or_path):
        return os.path.abspath(name_or_path)
    if os.path.isfile(name_or_path):
        return os.path.dirname(os.path.abspath(name_or_path))
    for root in radici_commesse():
        p = os.path.join(root, name_or_path)
        if os.path.isdir(p):
            return p
    raise FileNotFoundError(f"Commessa non trovata: {name_or_path} "
                            f"(cartelle cercate: {radici_commesse() or 'nessuna'})")
