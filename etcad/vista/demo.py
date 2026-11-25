"""Demo della vista dal vivo: Claude disegna da zero la scheda di un cavo, un passo alla volta.

    python -m etcad.vista demo [cartella_uscita]

Le funzioni di disegno stanno in etcad.schede (qui sono riesportate per comodita').
"""
from __future__ import annotations

from ..schede import *  # noqa: F401,F403
from ..schede import DEMO, Disegno, passi, salva  # noqa: F401

FOGLIO = "Demo - Cavo sensore M12"


def esegui(cartella: str = "") -> dict:
    """Disegna la demo nella vista (se aperta) e salva DXF e DWG nella cartella (se data)."""
    from . import attiva, fuoco, nota, pensa, scena, svuota
    d = Disegno()
    viva = attiva()
    if viva:
        svuota(FOGLIO)
        pensa("Disegno da zero la scheda di un cavo: sensore M12 a 4 poli collegato alla morsettiera -X2, "
              "con tabella dei collegamenti, distinta e quota della lunghezza.")
    n = 0
    for pensiero, titolo in passi(d):
        n += 1
        if viva:
            pensa(pensiero)
            scena(FOGLIO, d.doc, titolo)
    out = {"passi": n, "entita": len(d.msp)}
    if cartella:
        out.update(salva(d.doc, cartella, "Demo cavo sensore M12"))
        if viva:
            nota("Salvato il DWG R2018" if "dwg" in out else "Salvato il DXF")
    if viva:
        pensa("Fatto: la scheda e' completa. Ogni oggetto e' un'entita' vera del DWG, modificabile in qualsiasi CAD.")
        fuoco(FOGLIO, (0, 0, 420, 297))
    return out
