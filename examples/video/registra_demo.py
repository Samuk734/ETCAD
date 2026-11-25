"""Registra i video e le immagini della README usando la vista vera (pagina + server di ETCAD).

    pip install playwright imageio-ffmpeg        # solo per questo script; usa il Chrome gia' installato
    python examples/video/registra_demo.py video  # docs/media/demo.mp4 + docs/media/demo.gif
    python examples/video/registra_demo.py foto   # docs/img/*.png

Il video e' una replica scritta di una sessione: Playwright scrive nella chat della pagina come farebbe l'utente,
e la parte di Claude usa le stesse chiamate della vista che Claude Code fa con i tool MCP di ETCAD
(vista_pensa, vista_mostra...). Tutto gira in locale: server su 127.0.0.1, dati in una cartella temporanea.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(os.path.dirname(QUI))
MEDIA = os.path.join(RADICE, "docs", "media")
IMG = os.path.join(RADICE, "docs", "img")
PORTA = "8791"
TMP = tempfile.mkdtemp(prefix="etcad_video_")
os.environ.update(ETCAD_VISTA_PORTA=PORTA, ETCAD_VISTA_DATI=os.path.join(TMP, "dati"),
                  ETCAD_VISTA_SESSIONI=os.path.join(TMP, "sessioni"))
sys.path.insert(0, RADICE)

from etcad import vista  # noqa: E402
from etcad.vista import demo  # noqa: E402

CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
LARGO, ALTO = 1600, 900
FOGLIO = "Cavo sensore M12"


def avvia_server():
    os.makedirs(os.environ["ETCAD_VISTA_SESSIONI"], exist_ok=True)
    # indicatore del modello: come se la sessione di Claude Code avesse appena risposto
    with open(os.path.join(os.environ["ETCAD_VISTA_SESSIONI"], "demo.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5"}, "effort": "high"}) + "\n")
    env = dict(os.environ, PYTHONPATH=RADICE, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "etcad.vista", "--no-browser"], env=env, cwd=RADICE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if vista.attiva():
            return p
        time.sleep(0.2)
    p.kill()
    raise SystemExit("la vista non parte")


class Registratore:
    """Screencast di Chrome (CDP): un JPEG per ogni cambiamento della pagina, con il suo istante."""

    def __init__(self, page, cartella):
        self.cdp = page.context.new_cdp_session(page)
        self.cart = cartella
        self.fotogrammi = []
        os.makedirs(cartella, exist_ok=True)
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, ev):
        n = len(self.fotogrammi)
        f = os.path.join(self.cart, f"f{n:05d}.jpg")
        with open(f, "wb") as fh:
            fh.write(base64.b64decode(ev["data"]))
        self.fotogrammi.append((ev["metadata"].get("timestamp") or time.time(), f))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:  # noqa: BLE001 - a fine registrazione la sessione puo' essere chiusa
            pass

    def avvia(self):
        self.t0 = time.time()
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": LARGO,
                                               "maxHeight": ALTO, "everyNthFrame": 1})

    def ferma(self):
        self.t1 = time.time()
        self.cdp.send("Page.stopScreencast")

    def mp4(self, uscita, fps=30):
        """Fotogrammi a tempo variabile -> MP4 a 30 fps (ogni JPEG resta finche' non arriva il successivo)."""
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        lista = os.path.join(self.cart, "lista.txt")
        fr = sorted(self.fotogrammi)
        with open(lista, "w", encoding="utf-8") as fh:
            fh.write("ffconcat version 1.0\n")
            for k, (t, f) in enumerate(fr):
                durata = (fr[k + 1][0] if k + 1 < len(fr) else t + 2.0) - t
                fh.write(f"file '{os.path.basename(f)}'\nduration {max(durata, 0.001):.4f}\n")
            fh.write(f"file '{os.path.basename(fr[-1][1])}'\n")
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
                        "-vf", f"fps={fps},scale={LARGO}:{ALTO}:flags=lanczos,format=yuv420p", "-c:v", "libx264",
                        "-crf", "24", "-preset", "slow", "-movflags", "+faststart", uscita], check=True, cwd=self.cart)
        return uscita


def gif(mp4, uscita, inizio=0.0, durata=None, velocita=1.6, larghezza=1000, fps=10):
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    taglio = ["-ss", f"{inizio:.2f}"] + (["-t", f"{durata:.2f}"] if durata else [])
    filtro = (f"setpts=PTS/{velocita},fps={fps},scale={larghezza}:-1:flags=lanczos,split[a][b];"
              f"[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run([ff, "-y", "-loglevel", "error", *taglio, "-i", mp4, "-filter_complex", filtro, uscita], check=True)
    return uscita


def apri_pagina(p, tema_chiaro=False):
    exe = next((c for c in CHROME if os.path.exists(c)), None)
    browser = p.chromium.launch(executable_path=exe, headless=True) if exe else p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": LARGO, "height": ALTO}, device_scale_factor=1,
                              color_scheme="light" if tema_chiaro else "dark", locale="it-IT")
    page = ctx.new_page()
    page.goto(vista.url())
    page.wait_for_timeout(1500)
    return browser, page


def scrivi(page, testo, ritardo=38):
    page.click("#testoChat")
    page.locator("#testoChat").press_sequentially(testo, delay=ritardo)
    page.wait_for_timeout(500)
    page.click("#inviaChat")


CODA = queue.Queue()


def ascoltatore(stop):
    """Come Claude Code con vista_ascolta sempre armato: la pagina mostra "in ascolto" per tutta la sessione."""
    while not stop.is_set():
        for ev in vista.ascolta(timeout=4) or []:
            if ev.get("tipo") == "chat" or ev.get("testo"):
                CODA.put(ev)


def aspetta_messaggio(timeout=90.0):
    try:
        return CODA.get(timeout=timeout)
    except queue.Empty:
        return None


# -- la parte di Claude ----------------------------------------------------------------------------------------
def claude(stato):
    """Risponde come farebbe Claude Code con i tool della vista. stato: dict condiviso con il copione."""
    msg = aspetta_messaggio()
    stato["ricevuto1"] = msg
    time.sleep(1.2)
    vista.pensa("L'utente vuole la scheda di un cavo sensore M12 a 4 poli verso la morsettiera -X2. "
                "Pinout standard IEC 61076-2-101: 1 BN +24 V, 2 WH, 3 BU 0 V, 4 BK uscita.")
    time.sleep(1.6)
    vista.pensa("Mi mancano due dati che cambiano il disegno: la lunghezza del cavo e il tipo di connettore. "
                "Meglio chiedere prima di disegnare.")
    time.sleep(1.6)
    vista.chat("Certo! Prima di disegnarla ho due domande:\n\n1. **Quanto deve essere lungo** il cavo?\n"
               "2. Il connettore M12 lo vuoi **diritto o a 90°**?")
    msg = aspetta_messaggio()
    stato["ricevuto2"] = msg
    time.sleep(1.0)
    vista.pensa("2 m e connettore diritto: M12 femmina 4 poli codifica A, cavo PUR 4x0,34. "
                "Lo disegno su un A3 con tabella dei collegamenti, distinta e quota.")
    time.sleep(1.2)
    vista.chat("Perfetto: **2 m, M12 femmina diritto**. Lo disegno adesso, lo vedi comparire passo per passo.")
    time.sleep(1.5)
    d = demo.Disegno()
    for k, (pensiero, titolo) in enumerate(demo.passi(d, demo.DEMO)):
        vista.pensa(pensiero)
        if k == 0:
            stato["disegno"] = True
        time.sleep(0.9)
        vista.scena(FOGLIO, d.doc, titolo)
        time.sleep(3.4)
    out = demo.salva(d.doc, os.path.join(TMP, "uscita"), "Cavo sensore M12")
    vista.nota("Salvato " + os.path.basename(out.get("dwg") or out["dxf"]))
    time.sleep(1.0)
    vista.fuoco(FOGLIO, (0, 0, 420, 297))
    vista.chat("Fatto ✅ La scheda è salvata come **Cavo sensore M12.dwg** (DWG R2018, 175 entità vere: testi, "
               "linee, tratteggi). Vuoi cambiare qualcosa?")
    msg = aspetta_messaggio()
    stato["ricevuto3"] = msg
    time.sleep(1.0)
    vista.pensa("Cambia solo la lunghezza: 5000 mm. Aggiorno quota, codice, nota del cartiglio e la quantità del cavo "
                "in distinta. La geometria resta uguale, quindi nella vista si accendono solo i testi modificati.")
    time.sleep(1.4)
    spec = dict(demo.DEMO, lunghezza=5000, codice="CAV-M12-4P-5000", nota_codice="L = 5000 mm",
                distinta=[demo.DEMO["distinta"][0], ("2", "5 m", "Cavo PUR 4x0,34 %%c5,2", "PUR-4X034"),
                          *demo.DEMO["distinta"][2:]])
    d2 = demo.Disegno()
    demo.scheda_cavo(d2, spec)
    vista.scena(FOGLIO, d2.doc, "Lunghezza 5000 mm")
    time.sleep(4.0)
    vista.fuoco(FOGLIO, (0, 0, 420, 297))
    vista.chat("Aggiornato: **L = 5000 mm**, codice **CAV-M12-4P-5000** e 5 m di cavo in distinta. "
               "Sono cambiati 4 testi, il resto è identico.")
    stato["fine"] = True


def copione_video(page, stato):
    page.click('button[data-tab="chat"]')
    page.wait_for_timeout(1500)
    scrivi(page, "Mi disegni la scheda del cavo per il sensore -B1? M12 a 4 poli, verso la morsettiera -X2.")
    while "ricevuto2" not in stato:
        page.wait_for_timeout(200)
        if page.locator("#messaggi li.msg").count() >= 2 and "domanda" not in stato:
            stato["domanda"] = True
            page.wait_for_timeout(2500)
            scrivi(page, "2 metri, connettore diritto")
    while "ricevuto3" not in stato:
        page.wait_for_timeout(250)
        if stato.get("disegno") and not stato.get("cot_aperto"):
            stato["cot_aperto"] = True
            page.wait_for_timeout(400)
            page.locator("#messaggi li.cot").last.locator("summary").click()
        if stato.get("pronto3") is None and page.locator("#messaggi li.msg").count() >= 5:
            stato["pronto3"] = True
            page.wait_for_timeout(3500)
            scrivi(page, "Portalo a 5 metri")
    while "fine" not in stato:
        page.wait_for_timeout(250)
    page.wait_for_timeout(4500)


def video():
    from playwright.sync_api import sync_playwright
    srv = avvia_server()
    stato = {}
    try:
        with sync_playwright() as p:
            browser, page = apri_pagina(p)
            reg = Registratore(page, os.path.join(TMP, "fotogrammi"))
            reg.avvia()
            stop = threading.Event()
            threading.Thread(target=ascoltatore, args=(stop,), daemon=True).start()
            th = threading.Thread(target=claude, args=(stato,), daemon=True)
            th.start()
            copione_video(page, stato)
            stop.set()
            reg.ferma()
            os.makedirs(MEDIA, exist_ok=True)
            os.makedirs(IMG, exist_ok=True)
            page.screenshot(path=os.path.join(IMG, "vista_scura.png"))
            browser.close()
        mp4 = reg.mp4(os.path.join(MEDIA, "demo.mp4"))
        durata = reg.t1 - reg.t0
        gif(mp4, os.path.join(MEDIA, "demo.gif"), velocita=1.8, larghezza=900, fps=9)
        print(json.dumps({"mp4": mp4, "secondi": round(durata, 1), "fotogrammi": len(reg.fotogrammi)}))
    finally:
        srv.kill()


# -- immagini della README ------------------------------------------------------------------------------------
SET = "Set cablaggi di esempio"


def px(page, x, y, riq=0):
    """Coordinate del disegno (mm) -> pixel della pagina."""
    return page.evaluate("""([x, y, i]) => { const r = S.riquadri[i]; const b = r.tela.getBoundingClientRect();
        const p = schermo(r, x, y); return [b.left + p[0], b.top + p[1]]; }""", [x, y, riq])


def comando(page, testo):
    page.click("#cmdInput")
    page.keyboard.type(testo, delay=60)
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)


def clic(page, x, y):
    a, b = px(page, x, y)
    page.mouse.move(a, b, steps=8)
    page.wait_for_timeout(150)
    page.mouse.click(a, b)
    page.wait_for_timeout(300)


def mostra(page, chiave):
    """Porta il foglio nel riquadro principale e lo adatta alla finestra."""
    page.evaluate("(k) => mostraFoglio(riquadroFuoco(), k, true)", chiave)
    page.wait_for_timeout(600)
    page.click("#btnAdatta2")
    page.wait_for_timeout(1200)


def ascolta_agente(nome, stop):
    while not stop.is_set():
        vista.ascolta(timeout=4, agente=nome)


def foto():
    from playwright.sync_api import sync_playwright
    srv = avvia_server()
    stop = threading.Event()
    threading.Thread(target=ascoltatore, args=(stop,), daemon=True).start()
    os.makedirs(IMG, exist_ok=True)
    dwg = os.path.join(RADICE, "examples", "set_cablaggi", SET + ".dwg")
    chiave = lambda n: f"{SET}/{n}"                                    # noqa: E731
    try:
        with sync_playwright() as p:
            browser, page = apri_pagina(p)
            r = vista.carica(dwg, anima=False)
            print("fogli:", [f["foglio"] for f in r["fogli"]])
            page.wait_for_timeout(3000)
            # 1) multifoglio: gestione fogli con le miniature, foglio 4
            mostra(page, chiave(4))
            if not page.locator("#pannelloFogli").is_visible():
                page.click('button[data-scheda="vista"]')
                page.click("#btnGestioneFogli")
                page.click('button[data-scheda="inizio"]')
            page.wait_for_timeout(4000)
            page.screenshot(path=os.path.join(IMG, "multifoglio.png"))
            page.click("#chiudiFogli")
            # 2) quattro riquadri
            page.evaluate("""(k) => { impostaLayout("4", false);
                S.riquadri.forEach((r, i) => scegliLegame(r, "foglio:" + k + "/" + [2, 3, 4, 6][i])); }""", SET)
            page.wait_for_timeout(2500)
            page.screenshot(path=os.path.join(IMG, "quattro_fogli.png"))
            page.evaluate("""() => impostaLayout("singola", false)""")
            page.wait_for_timeout(800)
            # 3) un agente aperto dal Claude principale, con modello, effort e ragionamento
            page.click('button[data-tab="chat"]')
            mostra(page, chiave(3))
            scrivi(page, "Apri un agente che controlli la grafica dei fogli 2-6", ritardo=0)
            aspetta_messaggio(20)
            vista.pensa("Il controllo grafico e' un compito a se': lo affido a un agente. Basta Sonnet con effort medio.")
            ag = "Agente controllo"
            threading.Thread(target=ascolta_agente, args=(ag, stop), daemon=True).start()
            vista.agente(ag, stato="lavora", foglio=chiave(3), modello="claude-sonnet-5-5", effort="medium",
                         compito="Controllo grafico dei fogli 2-6")
            vista.chat(f"Ho aperto **{ag}** (Sonnet 5.5, effort medio): controlla i fogli 2-6 e ti scrive nella "
                       "sua chat.")
            page.wait_for_timeout(1500)
            vista.pensa("Parto dal foglio 3: 8 fili ravvicinati, e' quello con piu' rischio di testi sovrapposti.",
                        agente=ag)
            tot = 0
            for n in (2, 3, 4, 5, 6):
                res = vista.controlla(chiave(n), mostra=False, agente=ag) or {}
                k = len(res.get("problemi") or [])
                tot += k
                vista.pensa(f"Foglio {n}: " + (f"{k} segnalazioni da guardare" if k else
                                                 "nessun testo sovrapposto o toccato da linee"), agente=ag)
                page.wait_for_timeout(500)
            vista.pensa("Controllo anche le quote: le linee di richiamo partono a 2 mm dagli oggetti, nessuna "
                        "attraversa una scritta. Riporto il risultato senza toccare i fogli.", agente=ag)
            vista.chat(f"Controllo finito sui fogli 2-6: **{tot} segnalazioni**. Nessun testo sovrapposto ad altri "
                       "testi o toccato da linee, quote leggibili. Non ho modificato nulla.", agente=ag)
            vista.agente(ag, stato="fatto")
            page.wait_for_timeout(1500)
            page.locator("#filiChat").get_by_text(ag).first.click()
            page.wait_for_timeout(800)
            page.locator("#messaggi li.cot").last.locator("summary").click()
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(IMG, "agenti.png"))
            print("segnalazioni:", tot)
            # 4) l'utente disegna: rettangolo, testo, e una linea in corso
            page.locator("#filiChat button").first.click()
            mostra(page, chiave(2))
            o = 440.0
            comando(page, "REC")
            clic(page, o + 20, 52)
            clic(page, o + 132, 80)
            comando(page, "T")
            clic(page, o + 24, 68)
            page.keyboard.type("Verificare la lunghezza con il quadro", delay=20)
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            comando(page, "L")
            clic(page, o + 132, 66)
            a, b = px(page, o + 175, 92)
            page.mouse.move(a, b, steps=10)
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(IMG, "disegno_utente.png"))
            page.keyboard.press("Escape")
            page.keyboard.press("Escape")
            # 5) tema chiaro
            page.click('button[data-scheda="vista"]')
            page.click("#btnTema")
            page.click('button[data-scheda="inizio"]')
            mostra(page, chiave(2))
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(IMG, "vista_chiara.png"))
            # 6) interfaccia in inglese, con il menu delle lingue aperto
            page.click('button[data-scheda="vista"]')
            page.click("#btnTema")
            page.click('button[data-scheda="inizio"]')
            page.evaluate("() => impostaLingua('en')")
            mostra(page, chiave(4))
            page.click('button[data-tab="claude"]')
            page.click("#btnLingua")
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(IMG, "lingue.png"))
            page.evaluate("() => impostaLingua('it')")
            browser.close()
    finally:
        stop.set()
        srv.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cosa", choices=["video", "foto"])
    a = ap.parse_args()
    try:
        if a.cosa == "video":
            video()
        else:
            foto()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
