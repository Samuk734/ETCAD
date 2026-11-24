"use strict";
/* ETCAD Vista dal vivo: disegna i fogli ricevuti dal server locale e anima le
   modifiche degli agenti (tracciamento, spostamenti, cancellazioni, testi
   riscritti). Piu' riquadri, piu' agenti, e gli strumenti con cui l'utente
   indica le zone a Claude e gli scrive in chat. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const VEL = [0.35, 0.6, 1, 2, 4];
const HL = "#5cb8ff", ROSSO = "#ff6b6b", ARANCIO = "#ffb057", VERDE = "#5fd394";
const FONDO_SCURO = "#1a1f26";     // uguale a --vp in style.css
const DUR_EVID = 2200;           // ms di evidenziazione dopo il tracciamento
const BASE = "Claude";

const S = {
  fogli: new Map(),              // chiave -> {nome, ogg: Map(id -> oggetto), b, meta, ver}
  glifi: null,
  agenti: new Map(),             // nome -> agente (coda propria: lavorano in parallelo)
  agenteSel: null, agenteSelUtente: false,
  riquadri: [], fuoco: 0, layout: "singola",
  vt: 0, ultimo: performance.now(), vel: 1, pausa: false, salta: false,
  timer: [], attese: [],
  segui: true, lwt: true, scuro: true, griglia: false, snap: true, anima: true, mostraSfondo: true,
  dpr: window.devicePixelRatio || 1,
  modo: "sel", drag: null, mouse: null, mouseRiq: null, snapPt: null,
  sel: new Set(), selFoglio: null,
  richieste: [], evidenze: [], risultati: null, misura: null, dialogo: null,
  chat: [], nonLetti: 0, ascolto: {},
  nascosti: new Map(),           // doc -> Set(layer spenti nella vista)
  gruppo: "doc", pannelloFogli: false, pannelloAuto: true,
  ultimoDoc: new Map(), miniature: new Set(), tab: "claude", gruppiChiusi: new Set(),
};

/* ------------------------------------------------------------------ util */
function bg() { return S.scuro ? FONDO_SCURO : "#ffffff"; }
function hex2rgb(h) { const n = parseInt(h.slice(1, 7), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function mix(a, b, k) {
  const x = hex2rgb(a), y = hex2rgb(b);
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * k)).join(",")})`;
}
function colore(o, scuro = S.scuro) {
  if (!o.c) return scuro ? "#ffffff" : "#000000";
  if (!scuro) {
    const [r, g, b] = hex2rgb(o.c);
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    if (lum > 140) return mix(o.c, "#000000", Math.min(0.75, (lum - 90) / 220));   // giallo/grigio chiaro su carta
  }
  return o.c;
}
function fmt(v) { return (Math.round(v * 100) / 100).toFixed(2); }
function unione(a, b) {
  if (!a) return b ? b.slice() : null;
  if (!b) return a;
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}
function tocca(b, z) { return !(b[2] < z[0] || b[0] > z[2] || b[3] < z[1] || b[1] > z[3]); }
function dentro(b, z) { return b[0] >= z[0] && b[1] >= z[1] && b[2] <= z[2] && b[3] <= z[3]; }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function corto(s, n = 46) { s = String(s || "").replace(/\s+/g, " "); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function attendi(ms) {            // attesa in tempo "virtuale" (rispetta pausa e velocità)
  if (ms <= 0) return Promise.resolve();
  const fine = S.vt + ms;
  return new Promise((r) => S.attese.push({ t: fine, r }));
}
function alTempo(t, fn) { if (t <= S.vt) fn(); else S.timer.push({ t, fn }); }
function distSeg(px, py, x0, y0, x1, y1) {
  const dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x0) * dx + (py - y0) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}
async function posta(azione, dati) {
  const r = await fetch("/api/" + azione, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dati) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.errore || r.statusText);
  return j;
}
function avviso(testo, ms = 4200) {
  const d = document.createElement("div");
  d.className = "avviso"; d.innerHTML = testo;
  $("#avvisi").appendChild(d);
  setTimeout(() => d.classList.add("via"), ms);
  setTimeout(() => d.remove(), ms + 600);
}
function salvaImp() {
  try {
    localStorage.setItem("etcadVista", JSON.stringify({ layout: S.layout, gruppo: S.gruppo, pannelloFogli: S.pannelloFogli,
      anima: S.anima, segui: S.segui, scuro: S.scuro, snap: S.snap, lwt: S.lwt, tab: S.tab, orto: S.orto, largPalette: S.largPalette }));
  } catch (e) { /* memoria del browser non disponibile: si usano i valori predefiniti */ }
}
function caricaImp() {
  try {
    const v = JSON.parse(localStorage.getItem("etcadVista") || "{}");
    for (const k of ["layout", "gruppo", "pannelloFogli", "anima", "segui", "scuro", "snap", "lwt", "tab", "orto", "largPalette"]) if (k in v) S[k] = v[k];
    if ("pannelloFogli" in v) S.pannelloAuto = false;
  } catch (e) { /* idem */ }
}

/* --------------------------------------------------------- testi Hershey */
function larghezzaHershey(s, h, wf) {
  let w = 0;
  for (const c of s) { const g = S.glifi[c] || S.glifi["?"]; w += g[3]; }
  return w * h / 21 * wf;
}
function trattiTesto(x) {
  if (!S.glifi) return [];
  const out = [], h = x.h, s = h / 21, t = Math.tan(x.ob * Math.PI / 180), wf = x.wf;
  const ca = Math.cos(x.r * Math.PI / 180), sa = Math.sin(x.r * Math.PI / 180);
  const dy0 = { 0: 0, 1: 0.35 * h, 2: -h / 2, 3: -h }[x.va] || 0;
  x.s.split("\n").forEach((riga, k) => {
    const L0 = larghezzaHershey(riga, h, wf);
    const dx0 = x.al === "C" ? -L0 / 2 : x.al === "R" ? -L0 : 0;
    const dyr = -k * 1.667 * h;
    let pen = 0;
    for (const c of riga) {
      const g = S.glifi[c] || S.glifi["?"];
      const [L, , tr, adv] = g;
      for (const st of tr) {
        const pts = [];
        for (let i = 0; i < st.length; i += 2) {
          let v = (9 - st[i + 1]) * s;
          let u = ((st[i] - L) * s + v * t) * wf + pen + dx0;
          v += dy0 + dyr;
          pts.push(x.x + u * ca - v * sa, x.y + u * sa + v * ca);
        }
        if (pts.length >= 4) out.push(pts);
      }
      pen += adv * s * wf;
    }
  });
  return out;
}

/* ------------------------------------------------------- oggetti di scena */
function prepara(o) {
  o.linee = o.p.slice();
  if (o.x && o.x.shx) o.linee = o.linee.concat(trattiTesto(o.x));
  o.L = 0;
  o.path = new Path2D();
  for (const q of o.linee) {
    o.path.moveTo(q[0], q[1]);
    for (let i = 2; i < q.length; i += 2) {
      o.L += Math.hypot(q[i] - q[i - 2], q[i + 1] - q[i - 1]);
      o.path.lineTo(q[i], q[i + 1]);
    }
  }
  if (o.f && o.f.length) {
    o.pieno = new Path2D();
    for (const q of o.f) {
      o.pieno.moveTo(q[0], q[1]);
      for (let i = 2; i < q.length; i += 2) o.pieno.lineTo(q[i], q[i + 1]);
      o.pieno.closePath();
    }
  }
  if (o.img && o.img.src) {
    o.im = new Image();
    o.im.onload = () => { for (const f of S.fogli.values()) if (f.ogg.get(o.id) === o) { f.ver++; richiediMiniatura(f.nome); } };
    o.im.src = o.img.src;
  }
  return o;
}
function foglio(nome, meta) {
  let f = S.fogli.get(nome);
  if (!f) {
    f = { nome, ogg: new Map(), b: null, meta: meta || { doc: nome }, ver: 0 };
    S.fogli.set(nome, f);
    cambioFogli();
  } else if (meta) { f.meta = meta; }
  return f;
}
function docDi(nome) { const f = S.fogli.get(nome); return (f && f.meta && f.meta.doc) || nome; }
function fogliDoc(doc) {
  return [...S.fogli.values()].filter((f) => (f.meta.doc || f.nome) === doc)
    .sort((a, b) => (a.meta.ordine ?? 1e9) - (b.meta.ordine ?? 1e9));
}
function docs() { const d = []; for (const f of S.fogli.values()) { const k = f.meta.doc || f.nome; if (!d.includes(k)) d.push(k); } return d; }
function etichettaFoglio(f, lungo = true) {
  if (!f) return "";
  const m = f.meta || {};
  if (m.num !== undefined && m.num !== null && m.num !== "") return `Foglio ${m.num}${lungo && m.desc ? " · " + m.desc : ""}`;
  return f.nome;
}
function zonaFoglio(f) { return (f.meta && f.meta.zona) || f.b; }
function estensione(f) {
  let b = null;
  for (const o of f.ogg.values()) if (o.b && (o.b[2] > o.b[0] || o.b[3] > o.b[1])) b = unione(b, o.b);
  f.b = b;
  return b;
}
function riempi(nome, oggetti, meta) {
  const f = foglio(nome, meta);
  f.ogg = new Map();
  for (const o of oggetti) f.ogg.set(o.id, prepara(o));
  estensione(f);
  if (!f.bozza && typeof ripristinaBozza === "function") ripristinaBozza(f);
  if (f.bozza && f.bozza.length) ricalcolaBozza(f);
  f.ver++; richiediMiniatura(nome);
  return f;
}
function preparaSfondo(f) {
  const sf = f && f.sfondo;
  if (!sf || !sf.src) return;
  sf.im = new Image();
  sf.im.src = sf.src;
}
function* visti(f) {             // oggetti che si vedono: DWG meno quelli toccati dalla bozza, piu' bozza e schizzo
  const via = f.bz && f.bz.via;
  for (const o of f.ogg.values()) if (!via || !via.has(o.id)) yield o;
  if (f.bz) yield* f.bz.ogg;
  if (f.sk) yield* f.sk;
}
function oggDi(f, k) { return f.ogg.get(k) || (f.bzMappa && f.bzMappa.get(k)) || null; }
function vivi(f) { let n = 0; for (const o of f.ogg.values()) if (!(o.anim && o.anim.tipo === "via")) n++; return n; }
function pulisciMorti(f) {
  for (const [k, o] of f.ogg) if (o.morto || (o.anim && o.anim.tipo === "via" && S.vt >= o.anim.t0 + o.anim.dur)) f.ogg.delete(k);
}

/* ---------------------------------------------------------------- agenti */
function agente(nome) {
  nome = nome || BASE;
  let a = S.agenti.get(nome);
  if (!a) {
    a = { nome, colore: HL, coda: [], lavora: false, foglio: null, fogli: [], pensiero: "", genere: "pensiero", stato: "attesa",
      finePasso: null, t0: 0, penna: null, pennaFoglio: null, pennaT: 0, ultimo: 0 };
    S.agenti.set(nome, a);
    disegnaAgenti();
  }
  return a;
}
function agenteScelto() { return S.agenti.get(S.agenteSel) || S.agenti.get(BASE) || S.agenti.values().next().value || null; }
function agentiAttivi() {
  return [...S.agenti.values()].filter((a) => a.foglio || a.lavora).sort((x, y) => (y.lavora - x.lavora) || (x.primo || 0) - (y.primo || 0));
}
function riquadriCheSeguono(a) {
  return S.riquadri.filter((r) => r.legame === "agente:" + a.nome || (r.legame === "auto" && S.segui && S.agenteSel === a.nome));
}
function riquadriSu(k) { return S.riquadri.filter((r) => r.foglio === k); }
function avviaAgente(a, chiave) {
  if (!a.primo) a.primo = performance.now();
  if (!S.agenteSelUtente && (!S.agenteSel || !(S.agenti.get(S.agenteSel) || {}).lavora || S.agenteSel === a.nome)) {
    if (S.agenteSel !== a.nome) { S.agenteSel = a.nome; disegnaAgenti(); mostraPensieroScelto(); }
  }
  if (chiave) {
    a.foglio = chiave;
    if (!a.fogli.includes(chiave)) { a.fogli.push(chiave); cambioFogli(); }
    for (const r of riquadriCheSeguono(a)) if (r.foglio !== chiave) mostraFoglio(r, chiave);
    for (const r of S.riquadri) if (!r.foglio) mostraFoglio(r, chiave);
  }
  if (S.layout === "agenti" && S.riquadri.length !== Math.max(1, Math.min(4, agentiAttivi().length))) impostaLayout("agenti", false);
}
function accoda(ev) {
  const a = agente(ev.agente);
  a.coda.push(ev);
  lavoraAgente(a);
}
async function lavoraAgente(a) {
  if (a.lavora) return;
  a.lavora = true; aggiornaAgente(a); disegnaSchedeDoc();
  while (a.coda.length) {
    const ev = a.coda.shift();
    try { await esegui(ev, a); } catch (e) { console.error(e); rigaComando(esc(String(e)), "err", a); }
  }
  a.lavora = false; a.finePasso = null; a.ultimo = performance.now();
  aggiornaAgente(a); disegnaSchedeDoc();
  $$("#passi li.corrente").forEach((x) => { if (x.dataset.ag === a.nome) x.classList.remove("corrente"); });
}

/* -------------------------------------------------------------- riquadri */
function nuovoRiquadro(i) {
  const el = document.createElement("div");
  el.className = "riquadro";
  el.innerHTML = `<canvas></canvas><div class="testaRiq"><span class="agenteRiq" hidden></span>
    <select title="Cosa mostra questo riquadro"></select><span class="etichettaVista">[-][Superiore][Wireframe 2D]</span></div>`;
  const r = { i, el, tela: el.querySelector("canvas"), selEl: el.querySelector("select"), agEl: el.querySelector(".agenteRiq"),
    legame: "auto", foglio: null, vista: { s: 2, tx: 0, ty: 0 }, cam: null, viste: new Map(), w: 0, h: 0 };
  r.ctx = r.tela.getContext("2d");
  r.selEl.addEventListener("mousedown", (e) => e.stopPropagation());
  r.selEl.onchange = () => scegliLegame(r, r.selEl.value);
  collegaMouse(r);
  new ResizeObserver(() => ridimensiona(r)).observe(el);
  return r;
}
function impostaLayout(layout, salva = true) {
  S.layout = layout;
  const n = layout === "singola" ? 1 : layout === "2" ? 2 : layout === "4" ? 4 : Math.max(1, Math.min(4, agentiAttivi().length || 1));
  const box = $("#riquadri");
  while (S.riquadri.length < n) { const r = nuovoRiquadro(S.riquadri.length); S.riquadri.push(r); box.appendChild(r.el); }
  while (S.riquadri.length > n) S.riquadri.pop().el.remove();
  box.className = `n${n}${n > 1 ? " riquadri-multi" : ""}`;
  const ag = agentiAttivi();
  S.riquadri.forEach((r, i) => {
    if (layout === "agenti") r.legame = ag[i] ? "agente:" + ag[i].nome : "auto";
    else if (layout === "singola") { if (!r.scelto) r.legame = "auto"; }
    else if (!r.scelto) r.legame = i === 0 ? "auto" : (ag[i] ? "agente:" + ag[i].nome : "auto");
    const a = r.legame.startsWith("agente:") ? S.agenti.get(r.legame.slice(7)) : null;
    if (a && a.foglio) mostraFoglio(r, a.foglio);
    if (!r.foglio) {
      const att = riquadroFuoco() && riquadroFuoco().foglio;
      const tutti = [...S.fogli.keys()];
      const lib = tutti.filter((k) => !S.riquadri.some((x) => x.foglio === k));
      const k = i === 0 ? (att || tutti[0]) : (lib[0] || att || tutti[0]);
      if (k) mostraFoglio(r, k);
    }
  });
  if (S.fuoco >= n) S.fuoco = 0;
  aggiornaRiquadri();
  $$(".sceltaLayout").forEach((b) => b.classList.toggle("acceso", b.dataset.layout === layout));
  $$('#menuImpostazioni input[name="layout"]').forEach((x) => { x.checked = x.value === layout; });
  if (salva) salvaImp();
}
function riquadroFuoco() { return S.riquadri[S.fuoco] || S.riquadri[0]; }
function scegliLegame(r, v) {
  r.scelto = true;
  if (v.startsWith("agente:")) {
    r.legame = v;
    const a = S.agenti.get(v.slice(7));
    if (a && a.foglio) mostraFoglio(r, a.foglio);
  } else if (v.startsWith("foglio:")) {
    r.legame = S.riquadri.length > 1 ? v : "auto";      // nei riquadri multipli il foglio scelto resta fisso
    mostraFoglio(r, v.slice(7), true);
  }
  aggiornaRiquadri();
}
function mostraFoglio(r, chiave, utente = false) {
  if (!S.fogli.has(chiave)) return;
  if (r.foglio === chiave) { if (utente) r.cam = null; return; }
  if (r.foglio) r.viste.set(r.foglio, { ...r.vista });
  r.foglio = chiave;
  const f = S.fogli.get(chiave);
  S.ultimoDoc.set(docDi(chiave), chiave);
  const v = r.viste.get(chiave);
  if (v) r.vista = v; else adattaR(r, false);
  r.cam = null;
  aggiornaRiquadri();
  if (r === riquadroFuoco()) aggiornaFuoco();
  void f;
}
function aggiornaRiquadri() {
  for (const r of S.riquadri) {
    r.el.classList.toggle("fuoco", S.riquadri.length > 1 && r === riquadroFuoco());
    const a = r.legame.startsWith("agente:") ? S.agenti.get(r.legame.slice(7)) : null;
    const fr = S.fogli.get(r.foglio);
    r.agEl.hidden = !a || !fr;
    if (a && fr) r.agEl.innerHTML = `<span class="puntoAg" style="background:${a.colore}"></span>${esc(etichettaFoglio(fr))}`;
    // menu: segui un agente o scegli un foglio
    let h = "";
    if (S.agenti.size) {
      h += `<optgroup label="Segui un agente">`;
      for (const x of S.agenti.values()) h += `<option value="agente:${esc(x.nome)}">● ${esc(x.nome)}</option>`;
      h += `</optgroup>`;
    }
    for (const d of docs()) {
      h += `<optgroup label="${esc(d)}">`;
      for (const f of fogliDoc(d)) h += `<option value="foglio:${esc(f.nome)}">${esc(etichettaFoglio(f))}</option>`;
      h += `</optgroup>`;
    }
    r.selEl.innerHTML = h;
    r.selEl.value = a ? r.legame : (r.foglio ? "foglio:" + r.foglio : "");
    if (r.legame.startsWith("foglio:") && r.legame.slice(7) !== r.foglio && S.fogli.has(r.legame.slice(7))) mostraFoglio(r, r.legame.slice(7));
    r.selEl.hidden = !S.fogli.size;
  }
  disegnaSchedeDoc();
}
function aggiornaFuoco() {
  const r = riquadroFuoco();
  const f = r && S.fogli.get(r.foglio);
  $("#nomeFoglio").textContent = f ? (f.meta.num !== undefined ? `${f.meta.doc} — ${etichettaFoglio(f)}` : f.nome) : "nessun disegno";
  $("#contaOggetti").textContent = f ? `${f.ogg.size} oggetti` : "";
  const lista = f ? fogliDoc(docDi(f.nome)) : [];
  $("#navFoglio").hidden = lista.length < 2;
  if (lista.length > 1) {
    const i = lista.indexOf(f);
    $("#navTesto").textContent = `${f.meta.num !== undefined ? "Fg " + f.meta.num : f.nome}  (${i + 1}/${lista.length})`;
  }
  $$(".riquadro").forEach((el, i) => el.classList.toggle("fuoco", S.riquadri.length > 1 && i === S.fuoco));
  disegnaSchedeDoc(); evidenziaFogli();
  if (S.tab === "layer") disegnaLayer();
  if (typeof aggiornaRibbonDisegno === "function") aggiornaRibbonDisegno();
}
function foglioVicino(passo) {
  const r = riquadroFuoco();
  if (!r || !r.foglio) return;
  const lista = fogliDoc(docDi(r.foglio));
  const i = lista.findIndex((f) => f.nome === r.foglio);
  const j = Math.max(0, Math.min(lista.length - 1, i + passo));
  if (j !== i) mostraFoglio(r, lista[j].nome, true);
}

/* ---------------------------------------------------------------- vista */
function ridimensiona(r) {
  const b = r.el.getBoundingClientRect();
  const primo = !r.w;
  if (!primo && b.width && b.height && (b.width !== r.w || b.height !== r.h)) {   // resta centrato sullo stesso punto
    const c = mondo(r, r.w / 2, r.h / 2), k = Math.min(b.width / r.w, b.height / r.h);
    r.vista.s *= k; r.vista.tx = b.width / 2 - c[0] * r.vista.s; r.vista.ty = b.height / 2 + c[1] * r.vista.s;
  }
  r.w = b.width; r.h = b.height;
  S.dpr = window.devicePixelRatio || 1;
  r.tela.width = Math.max(1, Math.round(r.w * S.dpr));
  r.tela.height = Math.max(1, Math.round(r.h * S.dpr));
  if (primo && r.foglio) adattaR(r, false);
}
function riserva(r) { return r.el.offsetTop + r.h >= $("#riquadri").clientHeight - 2 ? 40 : 6; }
function adattaA(r, b, margine = 1.08) {
  const w = r.w || 800, h = (r.h || 600) - riserva(r);
  const bw = Math.max(b[2] - b[0], 1), bh = Math.max(b[3] - b[1], 1);
  const s = Math.min(w / (bw * margine), h / (bh * margine));
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  return { s, tx: w / 2 - cx * s, ty: h / 2 + cy * s };
}
function vaiA(r, v, durata = 650) { r.cam = { da: { ...r.vista }, a: v, t0: performance.now(), dur: durata }; }
function adattaR(r, anima = true) {
  const f = S.fogli.get(r.foglio);
  const z = f && zonaFoglio(f);
  if (!z) return;
  const v = adattaA(r, z, 1.04);
  if (anima) vaiA(r, v); else r.vista = v;
}
function mondo(r, px, py) { return [(px - r.vista.tx) / r.vista.s, (r.vista.ty - py) / r.vista.s]; }
function mondoDa(v, px, py) { return [(px - v.tx) / v.s, (v.ty - py) / v.s]; }
function schermo(r, x, y) { return [x * r.vista.s + r.vista.tx, r.vista.ty - y * r.vista.s]; }
function visibileIn(r, b) {
  const [x0, y1] = mondo(r, 0, 0), [x1, y0] = mondo(r, r.w, r.h - riserva(r));
  return b[0] >= x0 && b[2] <= x1 && b[1] >= y0 && b[3] <= y1;
}
function seguiBoxR(r, b) {
  const f = S.fogli.get(r.foglio);
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  let w = Math.max((b[2] - b[0]) * 1.5, 140), h = Math.max((b[3] - b[1]) * 1.5, 90);
  const z = f && zonaFoglio(f);
  if (z) { w = Math.min(w, (z[2] - z[0]) * 1.05); h = Math.min(h, (z[3] - z[1]) * 1.05); }
  const v = adattaA(r, [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1.0);
  const k = v.s / r.vista.s;
  if (visibileIn(r, b) && k > 0.6 && k < 1.8) return;
  vaiA(r, v, 700);
}
function inseguiR(r, b) {
  const w = r.w, hv = r.h - riserva(r);
  const sLeggibile = Math.min(w / 130, hv / 85);
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  const v = r.cam ? r.cam.a : r.vista;
  const [x0, y1] = mondoDa(v, 0, 0), [x1, y0] = mondoDa(v, w, hv);
  const mx = (x1 - x0) * 0.12, my = (y1 - y0) * 0.12;
  const den = b[0] >= x0 + mx && b[2] <= x1 - mx && b[1] >= y0 + my && b[3] <= y1 - my;
  let s = v.s < sLeggibile * 0.7 ? sLeggibile : v.s;
  const bw = Math.max(b[2] - b[0], 1), bh = Math.max(b[3] - b[1], 1);
  s = Math.min(s, w / (bw * 1.25), hv / (bh * 1.25));
  if (den && Math.abs(s / v.s - 1) < 0.05) return;
  vaiA(r, { s, tx: w / 2 - cx * s, ty: hv / 2 + cy * s }, 480);
}
function seguiBox(a, chiave, b) {
  if (!b || !S.segui) return;
  for (const r of riquadriCheSeguono(a)) if (r.foglio === chiave) seguiBoxR(r, b);
}
function insegui(a, chiave, b) {
  if (!b || !S.segui) return;
  for (const r of riquadriCheSeguono(a)) if (r.foglio === chiave) inseguiR(r, b);
}
function vaiAZona(chiave, b, minimo = [60, 40]) {
  if (!S.fogli.has(chiave)) return;
  const r = riquadroFuoco();
  mostraFoglio(r, chiave, true);
  if (!b) { adattaR(r); return; }
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  const w = Math.max(b[2] - b[0], minimo[0]), h = Math.max(b[3] - b[1], minimo[1]);
  vaiA(r, adattaA(r, [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1.35));
}

/* -------------------------------------------------------------- disegno */
function tracciaParziale(g, o, frac) {
  let resto = o.L * frac;
  g.beginPath();
  let punta = null;
  for (let k = 0; k < o.linee.length && resto > 0; k++) {
    const q = o.linee[k];
    g.moveTo(q[0], q[1]);
    punta = [q[0], q[1]];
    for (let i = 2; i < q.length; i += 2) {
      const l = Math.hypot(q[i] - q[i - 2], q[i + 1] - q[i - 1]);
      if (l >= resto) {
        const k2 = l > 0 ? resto / l : 1;
        const x = q[i - 2] + (q[i] - q[i - 2]) * k2, y = q[i - 1] + (q[i + 1] - q[i - 1]) * k2;
        g.lineTo(x, y); punta = [x, y]; resto = 0; break;
      }
      g.lineTo(q[i], q[i + 1]); punta = [q[i], q[i + 1]]; resto -= l;
    }
  }
  g.stroke();
  return punta;
}
function testoTTF(g, o, frac) {
  const x = o.x;
  let n = Math.ceil(x.s.length * frac);
  if (n <= 0) return null;
  g.save();
  g.translate(x.x, x.y);
  g.rotate(x.r * Math.PI / 180);
  g.scale(x.wf, -1);
  if (x.ob) g.transform(1, 0, -Math.tan(x.ob * Math.PI / 180), 1, 0, 0);
  const k = x.h / 0.716 / 100;
  g.scale(k, k);
  const fam = x.fam === "ISOCPEUR" ? '"ISOCPEUR", "Arial Narrow", Arial' : `"${x.fam}", Arial`;
  g.font = `${x.bold ? "bold " : ""}100px ${fam}`;
  g.textAlign = x.al === "C" ? "center" : x.al === "R" ? "right" : "left";
  g.textBaseline = x.va === 2 ? "middle" : x.va === 3 ? "top" : x.va === 1 ? "bottom" : "alphabetic";
  let punta = null;
  x.s.split("\n").forEach((riga, i) => {
    if (n <= 0) return;
    const parte = riga.slice(0, n); n -= riga.length + 1;
    g.fillText(parte, 0, i * 119.4);
    if (frac < 1) punta = g.measureText(parte).width;
  });
  g.restore();
  if (punta === null) return null;
  const xr = x.al === "L" ? punta : x.al === "C" ? punta / 2 : 0;
  const ca = Math.cos(x.r * Math.PI / 180), sa = Math.sin(x.r * Math.PI / 180);
  const u = xr * k * x.wf;
  return [x.x + u * ca, x.y + u * sa];
}
function disegnaOggetto(g, o, ora, s) {
  let col = colore(o), alpha = 1, frac = 1, dx = 0, dy = 0;
  const a = o.anim;
  if (a) {
    const t = (S.vt - a.t0) / a.dur;
    if (t < 0 && a.tipo === "traccia") return;
    const tt = Math.min(1, Math.max(0, t));
    if (a.tipo === "traccia") { frac = tt; col = a.col || HL; }
    else if (a.tipo === "via") { col = ROSSO; alpha = 1 - tt * tt; }
    else if (a.tipo === "sposta") {
      const e = tt < .5 ? 2 * tt * tt : 1 - Math.pow(-2 * tt + 2, 2) / 2;
      dx = -a.dx * (1 - e); dy = -a.dy * (1 - e); col = a.col || HL;
    }
    if (t >= 1) {
      if (a.tipo === "via") { o.morto = true; return; }
      o.evidCol = a.col || HL; o.anim = null; o.evid = ora + DUR_EVID;
    }
  }
  if (!a && o.evid) {
    const k = (o.evid - ora) / DUR_EVID;
    if (k <= 0) o.evid = 0; else col = mix(o.c && S.scuro ? o.c : (S.scuro ? "#ffffff" : "#000000"), o.evidCol || HL, Math.min(1, k * 1.4));
  }
  g.globalAlpha = alpha;
  g.strokeStyle = col; g.fillStyle = col;
  g.lineWidth = (S.lwt ? Math.max(o.lw * s, 1) : 1) / s;
  if (dx || dy) { g.save(); g.translate(dx, dy); }
  if (o.im && o.im.complete && o.im.naturalWidth) {
    const q = o.img.q;
    g.save(); g.translate(q[0], q[3]); g.scale(1, -1);
    g.globalAlpha = alpha * (frac < 1 ? frac : 1);
    g.drawImage(o.im, 0, 0, q[2] - q[0], q[3] - q[1]);
    g.restore();
  }
  if (o.pieno) { g.globalAlpha = alpha * (frac < 1 ? frac : 1); g.fill(o.pieno, "evenodd"); g.globalAlpha = alpha; }
  let p = null;
  if (o.linee.length) {
    g.setLineDash(o.d ? o.d : []);
    if (frac >= 1) g.stroke(o.path); else p = tracciaParziale(g, o, frac);
    if (o.d) g.setLineDash([]);
  }
  if (o.x && !o.x.shx) { const q = testoTTF(g, o, frac); if (frac < 1 && q) p = q; }
  if (p && a && a.ag) { const A = S.agenti.get(a.ag); if (A) { A.penna = p; A.pennaT = ora; } }
  if (dx || dy) g.restore();
  g.globalAlpha = 1;
}
function disegnaStatico(g, o, s, scuro) {
  if (o.anim && o.anim.tipo === "via") return;
  const col = colore(o, scuro);
  g.strokeStyle = col; g.fillStyle = col;
  g.lineWidth = Math.max(o.lw * s, 0.9) / s;
  if (o.im && o.im.complete && o.im.naturalWidth) {
    const q = o.img.q;
    g.save(); g.translate(q[0], q[3]); g.scale(1, -1); g.drawImage(o.im, 0, 0, q[2] - q[0], q[3] - q[1]); g.restore();
  }
  if (o.pieno) g.fill(o.pieno, "evenodd");
  if (o.linee.length) { g.setLineDash(o.d ? o.d : []); g.stroke(o.path); g.setLineDash([]); }
  if (o.x && !o.x.shx) testoTTF(g, o, 1);
}
function rendiStatico(oggetti, zona, pxmm, maxLato, scuro = false) {
  const w = Math.max(zona[2] - zona[0], 1), h = Math.max(zona[3] - zona[1], 1);
  let s = pxmm;
  if (Math.max(w, h) * s > maxLato) s = maxLato / Math.max(w, h);
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w * s)); c.height = Math.max(1, Math.round(h * s));
  const g = c.getContext("2d");
  g.fillStyle = scuro ? FONDO_SCURO : "#ffffff"; g.fillRect(0, 0, c.width, c.height);
  g.setTransform(s, 0, 0, -s, -zona[0] * s, zona[3] * s);
  g.lineCap = "round"; g.lineJoin = "round";
  for (const o of oggetti) if (tocca(o.b, zona)) disegnaStatico(g, o, s, scuro);
  return c;
}
function margine(z, k = 0.06, min = 2) {
  const m = Math.max(min, k * Math.max(z[2] - z[0], z[3] - z[1]));
  return [z[0] - m, z[1] - m, z[2] + m, z[3] + m];
}

function disegnaGriglia(r) {
  const g = r.ctx;
  let passo = 1;
  while (passo * r.vista.s < 14) passo *= 10;
  const [x0, y1] = mondo(r, 0, 0), [x1, y0] = mondo(r, r.w, r.h);
  g.lineWidth = 1;
  for (const [p, c] of [[passo, S.scuro ? "#20262e" : "#f0f2f5"], [passo * 10, S.scuro ? "#28303a" : "#e1e6ec"]]) {
    g.strokeStyle = c; g.beginPath();
    for (let x = Math.floor(x0 / p) * p; x <= x1; x += p) { const X = x * r.vista.s + r.vista.tx; g.moveTo(X, 0); g.lineTo(X, r.h); }
    for (let y = Math.floor(y0 / p) * p; y <= y1; y += p) { const Y = r.vista.ty - y * r.vista.s; g.moveTo(0, Y); g.lineTo(r.w, Y); }
    g.stroke();
  }
}
/* elementi grafici sopra il disegno (coordinate di schermo) */
const FONT_UI = "'Segoe UI', system-ui, sans-serif", FONT_MONO = "'Cascadia Code', Consolas, monospace";
function tondo(g, x, y, w, h, rg) {
  g.beginPath();
  if (g.roundRect) g.roundRect(x, y, w, h, rg); else g.rect(x, y, w, h);
}
function pillola(g, x, y, testo, col, opz = {}) {
  // etichetta arrotondata con ombra; col = puntino colorato a sinistra (null = senza). Restituisce [w, h].
  g.save();
  g.font = opz.font || `600 11px ${FONT_UI}`;
  const h = opz.h || 20, conPunto = !!col, tw = g.measureText(testo).width, w = Math.ceil(tw + (conPunto ? 26 : 18));
  if (opz.destra) x -= w;
  g.shadowColor = S.scuro ? "rgba(0,0,0,.45)" : "rgba(16,24,40,.18)"; g.shadowBlur = 10; g.shadowOffsetY = 2;
  g.fillStyle = opz.fondo || (S.scuro ? "rgba(17,21,27,.93)" : "rgba(255,255,255,.97)");
  tondo(g, x, y, w, h, h / 2); g.fill();
  g.shadowColor = "transparent";
  g.strokeStyle = S.scuro ? "rgba(255,255,255,.11)" : "rgba(16,24,40,.13)"; g.lineWidth = 1; g.stroke();
  let tx = x + 9;
  if (conPunto) {
    g.fillStyle = col; g.beginPath(); g.arc(x + 11, y + h / 2, 3.5, 0, 7); g.fill();
    tx = x + 19;
  }
  g.fillStyle = opz.testo || (S.scuro ? "#eef2f6" : "#1b222a"); g.textBaseline = "middle";
  g.fillText(testo, tx, y + h / 2 + 0.5);
  g.restore();
  return [w, h];
}
function disegnaUCS(r) {
  const g = r.ctx, x = 24, y = r.h - (riserva(r) > 10 ? 132 : 32);
  const cx = S.scuro ? "#ff7b7b" : "#d93636", cy = S.scuro ? "#6fdc9f" : "#1f9d55";
  g.save();
  g.globalAlpha = 0.9; g.lineWidth = 1.6; g.lineCap = "round";
  const freccia = (col, dx, dy) => {
    const L = 36;
    g.strokeStyle = col; g.fillStyle = col;
    g.beginPath(); g.moveTo(x, y); g.lineTo(x + dx * L, y + dy * L); g.stroke();
    const px = x + dx * (L + 7), py = y + dy * (L + 7), nx = -dy, ny = dx;
    g.beginPath(); g.moveTo(px, py); g.lineTo(px - dx * 9 + nx * 3.5, py - dy * 9 + ny * 3.5); g.lineTo(px - dx * 9 - nx * 3.5, py - dy * 9 - ny * 3.5); g.closePath(); g.fill();
  };
  freccia(cx, 1, 0); freccia(cy, 0, -1);
  g.fillStyle = S.scuro ? "rgba(255,255,255,.75)" : "rgba(16,24,40,.6)";
  g.fillRect(x - 3, y - 3, 6, 6);
  g.font = `600 10px ${FONT_UI}`; g.textBaseline = "middle";
  g.fillStyle = cx; g.fillText("X", x + 49, y + 0.5);
  g.fillStyle = cy; g.textAlign = "center"; g.fillText("Y", x, y - 51);
  g.restore();
}
function disegnaCursore(r) {
  if (!S.mouse) return;
  const g = r.ctx, [mx, my] = S.mouse, L = Math.max(r.w, r.h) * 0.06;
  g.save();
  g.strokeStyle = S.scuro ? "rgba(255,255,255,.88)" : "rgba(16,24,40,.85)"; g.lineWidth = 1;
  g.beginPath();
  g.moveTo(mx - L, my + .5); g.lineTo(mx - 6, my + .5); g.moveTo(mx + 6, my + .5); g.lineTo(mx + L, my + .5);
  g.moveTo(mx + .5, my - L); g.lineTo(mx + .5, my - 6); g.moveTo(mx + .5, my + 6); g.lineTo(mx + .5, my + L);
  g.stroke();
  if (S.modo === "sel") g.strokeRect(mx - 4.5, my - 4.5, 10, 10);
  if (S.modo === "area" || S.modo === "punto" || S.modo === "misura" || S.modo === "filo") {
    const col = S.modo === "misura" ? HL : S.modo === "filo" ? "#e070ff" : ARANCIO;
    g.fillStyle = col; g.strokeStyle = S.scuro ? "rgba(0,0,0,.5)" : "#fff"; g.lineWidth = 1.5;
    g.beginPath(); g.arc(mx + 13, my - 13, 3.5, 0, 7); g.stroke(); g.fill();
  }
  g.restore();
}
function disegnaPenne(r, ora) {
  const g = r.ctx;
  for (const a of S.agenti.values()) {
    if (!a.penna || !a.lavora || a.pennaFoglio !== r.foglio || ora - a.pennaT > 400) continue;
    const [X, Y] = schermo(r, a.penna[0], a.penna[1]);
    const puls = (Math.sin(ora / 140) + 1) / 2;
    g.save();
    const alone = g.createRadialGradient(X, Y, 0, X, Y, 20);
    alone.addColorStop(0, a.colore + "66"); alone.addColorStop(1, a.colore + "00");
    g.fillStyle = alone; g.beginPath(); g.arc(X, Y, 20, 0, 7); g.fill();
    g.strokeStyle = a.colore; g.globalAlpha = 0.35 + 0.5 * (1 - puls); g.lineWidth = 1.5;
    g.beginPath(); g.arc(X, Y, 7 + puls * 5, 0, 7); g.stroke();
    g.globalAlpha = 1;
    g.fillStyle = a.colore; g.strokeStyle = S.scuro ? "#0e1115" : "#ffffff"; g.lineWidth = 1.5;
    g.beginPath(); g.arc(X, Y, 3.5, 0, 7); g.fill(); g.stroke();
    g.restore();
    pillola(g, X + 12, Y - 32, a.nome, a.colore);
  }
}
function rettangolo(r, z, col, opz = {}) {
  const g = r.ctx;
  const [X0, Y0] = schermo(r, z[0], z[3]), [X1, Y1] = schermo(r, z[2], z[1]);
  g.save();
  g.strokeStyle = col; g.lineWidth = opz.lw || 1.5; g.setLineDash(opz.tratt || []);
  if (opz.fill) { g.fillStyle = opz.fill; tondo(g, X0, Y0, X1 - X0, Y1 - Y0, 3); g.fill(); }
  if (opz.alone) { g.shadowColor = col; g.shadowBlur = opz.alone; }
  tondo(g, X0 + .5, Y0 + .5, X1 - X0, Y1 - Y0, 3); g.stroke();
  g.restore();
  if (opz.etichetta) pillola(g, X0, Y0 > 28 ? Y0 - 26 : Y1 + 6, opz.etichetta, col);
  return [X0, Y0, X1, Y1];
}
function disegnaSegni(r, ora) {
  const g = r.ctx, k = r.foglio;
  // richieste dell'utente
  for (const q of S.richieste) {
    if (q.foglio !== k) continue;
    const col = q.stato === "fatta" ? VERDE : q.stato === "letta" ? HL : ARANCIO;
    if (q.tipo === "punto" && q.punto) {
      const [X, Y] = schermo(r, q.punto[0], q.punto[1]);
      g.save();
      g.shadowColor = "rgba(0,0,0,.35)"; g.shadowBlur = 6; g.shadowOffsetY = 2;
      g.fillStyle = col; g.beginPath(); g.moveTo(X, Y); g.arc(X, Y - 16, 8.5, Math.PI * 0.78, Math.PI * 2.22); g.closePath(); g.fill();
      g.shadowColor = "transparent";
      g.strokeStyle = "rgba(255,255,255,.85)"; g.lineWidth = 1.2; g.stroke();
      g.fillStyle = "#1d1200"; g.font = `700 9.5px ${FONT_UI}`; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(q.id, X, Y - 16);
      g.restore();
    } else if (q.zona) {
      rettangolo(r, q.zona, col, { tratt: [6, 4], etichetta: `#${q.id} ${corto(q.testo, 34)}`, fill: q.stato === "nuova" ? "rgba(255,167,38,.06)" : null,
        lw: q.stato === "fatta" ? 1 : 1.5 });
    }
  }
  // evidenze degli agenti
  for (const e of S.evidenze) {
    if (e.foglio !== k) continue;
    const giovane = ora - (e.t0 || 0) < 3000;
    rettangolo(r, e.zona, e.colore || HL, { tratt: [8, 4], lw: 2, alone: giovane ? 10 + 6 * Math.sin(ora / 150) : 0,
      etichetta: `${e.agente}: ${corto(e.testo, 60)}` });
  }
  // risultati (trova, controllo)
  if (S.risultati) {
    const col = S.risultati.genere === "controllo" ? ROSSO : "#ffd54f";
    S.risultati.voci.forEach((v, i) => {
      if (v.foglio !== k) return;
      const cur = i === S.risultati.i;
      rettangolo(r, margine(v.b, 0.08, 0.6), col, { lw: cur ? 2.2 : 1, alone: cur ? 8 : 0, etichetta: cur ? corto(v.testo, 50) : "" });
    });
  }
  // misura
  if (S.misura && S.misura.foglio === k) {
    const p0 = S.misura.p0, p1 = S.misura.p1 || (S.mouseRiq === r ? (S.snapPt || mondo(r, ...S.mouse)) : null);
    if (p1) {
      const [X0, Y0] = schermo(r, p0[0], p0[1]), [X1, Y1] = schermo(r, p1[0], p1[1]);
      const cm = S.scuro ? HL : "#1f7ae0";
      g.save();
      g.strokeStyle = cm; g.lineWidth = 1.5; g.setLineDash([6, 4]);
      g.beginPath(); g.moveTo(X0, Y0); g.lineTo(X1, Y1); g.stroke(); g.setLineDash([]);
      g.fillStyle = S.scuro ? FONDO_SCURO : "#fff";
      for (const [X, Y] of [[X0, Y0], [X1, Y1]]) { g.beginPath(); g.arc(X, Y, 4, 0, 7); g.fill(); g.stroke(); }
      g.restore();
      pillola(g, (X0 + X1) / 2 + 10, (Y0 + Y1) / 2 - 30, testoMisura(p0, p1), cm, { font: `600 11.5px ${FONT_MONO}`, h: 22 });
    }
  }
  // riquadro che si sta trascinando
  const d = S.drag;
  if (d && d.r === r && d.mosso && (d.tipo === "finestra" || d.tipo === "area" || d.tipo === "zoomf")) {
    const [x0, y0] = d.w0, [x1, y1] = mondo(r, ...S.mouse);
    const z = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    if (d.tipo === "area") rettangolo(r, z, ARANCIO, { tratt: [6, 4], fill: "rgba(255,176,87,.10)", lw: 1.5 });
    else if (d.tipo === "zoomf") rettangolo(r, z, HL, { tratt: [4, 3], fill: "rgba(92,184,255,.08)", lw: 1 });
    else if (x1 >= x0) rettangolo(r, z, "#5cb8ff", { fill: "rgba(92,184,255,.13)", lw: 1 });
    else rettangolo(r, z, VERDE, { fill: "rgba(95,211,148,.12)", tratt: [5, 3], lw: 1 });
  }
  // aggancio
  if (S.snapPt && S.mouseRiq === r && (S.modo === "misura" || S.modo === "punto" || S.modo === "disegna")) {
    const [X, Y] = schermo(r, S.snapPt[0], S.snapPt[1]);
    g.save();
    g.strokeStyle = S.scuro ? "#8dff5a" : "#2f9e1f"; g.lineWidth = 2;
    g.shadowColor = g.strokeStyle; g.shadowBlur = 6;
    g.beginPath();
    if (S.snapPt.tipo === "medio") { g.moveTo(X, Y - 7); g.lineTo(X + 6.5, Y + 5); g.lineTo(X - 6.5, Y + 5); g.closePath(); }
    else if (S.snapPt.tipo === "centro") g.arc(X, Y, 6, 0, 7);
    else if (S.snapPt.tipo === "ins") { g.moveTo(X - 6, Y); g.lineTo(X, Y - 6); g.lineTo(X + 6, Y); g.lineTo(X, Y + 6); g.closePath(); }
    else tondo(g, X - 5.5, Y - 5.5, 11, 11, 2);
    g.stroke();
    g.restore();
  }
}
function disegnaSelezione(r, f) {
  const g = r.ctx, s = r.vista.s;
  g.save();
  const cs = S.scuro ? "#5cb8ff" : "#1f7ae0";
  g.strokeStyle = cs; g.fillStyle = cs;
  g.lineWidth = 2 / s; g.setLineDash([4 / s, 3 / s]);
  for (const id of S.sel) {
    const o = oggDi(f, id);
    if (!o) continue;
    if (o.linee.length) g.stroke(o.path);
    if (o.x || o.img || (o.pieno && !o.linee.length)) g.strokeRect(o.b[0], o.b[1], o.b[2] - o.b[0], o.b[3] - o.b[1]);
  }
  g.restore();
  // grip
  if (S.sel.size <= 60) {
    g.save(); g.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    g.fillStyle = "#3b8cf2"; g.strokeStyle = "#ffffff"; g.lineWidth = 1.2;
    for (const id of S.sel) {
      const o = oggDi(f, id);
      if (!o) continue;
      const pts = o.x ? [[o.x.x, o.x.y]] : o.p.length === 1 && o.p[0].length <= 40 ? o.p[0].reduce((a, v, i) => (i % 2 ? a[a.length - 1].push(v) : a.push([v]), a), []) : [];
      for (const [x, y] of pts) { const [X, Y] = schermo(r, x, y); tondo(g, X - 4, Y - 4, 8, 8, 2); g.fill(); g.stroke(); }
    }
    g.restore();
  }
}
function testoMisura(p0, p1) {
  const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
  const ang = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
  return `L ${fmt(Math.hypot(dx, dy))}  Δx ${fmt(dx)}  Δy ${fmt(dy)}  ∠ ${ang.toFixed(1)}°`;
}
function disegnaRiquadro(r, ora) {
  if (!r.w) return;
  const g = r.ctx, d = S.dpr;
  if (r.cam) {
    const t = Math.min(1, (ora - r.cam.t0) / r.cam.dur), e = 1 - Math.pow(1 - t, 3);
    const hv = r.h - riserva(r);
    const c0 = mondoDa(r.cam.da, r.w / 2, hv / 2), c1 = mondoDa(r.cam.a, r.w / 2, hv / 2);
    const s = Math.exp(Math.log(r.cam.da.s) + (Math.log(r.cam.a.s) - Math.log(r.cam.da.s)) * e);
    const cx = c0[0] + (c1[0] - c0[0]) * e, cy = c0[1] + (c1[1] - c0[1]) * e;
    r.vista = { s, tx: r.w / 2 - cx * s, ty: hv / 2 + cy * s };
    if (t >= 1) r.cam = null;
  }
  g.setTransform(d, 0, 0, d, 0, 0);
  g.fillStyle = bg(); g.fillRect(0, 0, r.w, r.h);
  if (S.griglia) disegnaGriglia(r);
  const f = S.fogli.get(r.foglio);
  if (f) {
    const v = r.vista;
    g.setTransform(d * v.s, 0, 0, -d * v.s, d * v.tx, d * v.ty);
    g.lineCap = "round"; g.lineJoin = "round";
    const sf = f.sfondo;
    if (sf && S.mostraSfondo && sf.im && sf.im.complete && sf.im.naturalWidth) {      // PDF originale sotto il disegno
      const q = sf.q;
      g.save(); g.globalAlpha = sf.opacita ?? 0.45; if (S.scuro) g.filter = "invert(1)";
      g.translate(q[0], q[3]); g.scale(1, -1); g.drawImage(sf.im, 0, 0, q[2] - q[0], q[3] - q[1]); g.restore();
    }
    const nasc = S.nascosti.get(docDi(r.foglio));
    const via = f.bz && f.bz.via;
    for (const o of f.ogg.values()) {
      if (nasc && nasc.size && nasc.has(o.ly)) continue;
      if (via && via.has(o.id)) continue;
      disegnaOggetto(g, o, ora, v.s);
      if (o.morto) f.morti = true;
    }
    if (f.bz) for (const o of f.bz.ogg) if (!(nasc && nasc.has(o.ly))) disegnaOggetto(g, o, ora, v.s);
    if (f.sk) for (const o of f.sk) disegnaOggetto(g, o, ora, v.s);
    if (S.selFoglio === r.foglio && S.sel.size) disegnaSelezione(r, f);
    disegnaAnteprimaCmd(r, f, g, ora);
    g.setTransform(d, 0, 0, d, 0, 0);
    for (const a of S.agenti.values()) if (a.penna && a.foglio === r.foglio) a.pennaFoglio = r.foglio;
    disegnaSegni(r, ora);
  }
  disegnaUCS(r);
  disegnaPenne(r, ora);
  if (S.mouseRiq === r) disegnaCursore(r);
}
function frame(ora) {
  const dt = Math.min(100, ora - S.ultimo); S.ultimo = ora;
  if (!S.pausa) S.vt += dt * S.vel;
  if (S.salta) {
    let m = 0;
    for (const a of S.agenti.values()) if (a.finePasso) m = Math.max(m, a.finePasso);
    if (m) S.vt = Math.max(S.vt, m);
    S.salta = false;
  }
  if (S.timer.length) { const pronti = S.timer.filter((x) => x.t <= S.vt); if (pronti.length) { S.timer = S.timer.filter((x) => x.t > S.vt); pronti.sort((a, b) => a.t - b.t).forEach((x) => x.fn()); } }
  if (S.attese.length) { const pronte = S.attese.filter((x) => x.t <= S.vt); if (pronte.length) { S.attese = S.attese.filter((x) => x.t > S.vt); pronte.forEach((x) => x.r()); } }
  for (const r of S.riquadri) disegnaRiquadro(r, ora);
  for (const f of S.fogli.values()) if (f.morti) { f.morti = false; for (const [k, o] of f.ogg) if (o.morto) f.ogg.delete(k); }
  // barre di avanzamento
  for (const a of S.agenti.values()) {
    const pct = a.finePasso ? Math.min(100, (S.vt - a.t0) / Math.max(1, a.finePasso - a.t0) * 100) : 0;
    if (a.elBarra) a.elBarra.style.width = `${a.lavora ? pct : 0}%`;
    if (a.nome === S.agenteSel || (!S.agenteSel && a.nome === BASE)) $("#barraPasso").style.width = `${a.lavora ? pct : 0}%`;
  }
  const rf = riquadroFuoco();
  if (rf) $("#zoomPerc").textContent = `zoom ${Math.round(rf.vista.s / 3.78 * 100)}%`;
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------ riga di comando */
const storia = $("#storiaComandi");
let spegniStoria = null;
function rigaComando(html, classe = "cmd", a = null) {
  const div = document.createElement("div");
  div.className = classe;
  const tag = a && S.agenti.size > 1 ? `<span class="tagAg" style="color:${a.colore}">${esc(a.nome)}</span>` : "";
  div.innerHTML = tag + html;
  storia.appendChild(div);
  while (storia.children.length > 60) storia.removeChild(storia.firstChild);
  storia.classList.remove("spento");
  clearTimeout(spegniStoria);
  spegniStoria = setTimeout(function spegni() {
    if ([...S.agenti.values()].some((x) => x.lavora)) { spegniStoria = setTimeout(spegni, 2000); return; }
    storia.classList.add("spento");
  }, 6000);
}
function promptCorrente(testo) {
  const el = $("#comandoCorrente");
  el.hidden = !testo;
  el.innerHTML = testo ? esc(testo) + '<span class="cursore"></span>' : "";
}
function lampeggiaBottone(cmd, ms = 700) {
  const b = document.querySelector(`[data-cmd="${cmd}"]`);
  if (!b) return;
  b.classList.add("eseguendo");
  clearTimeout(b._t); b._t = setTimeout(() => b.classList.remove("eseguendo"), ms);
}
function comandoDi(o) {
  const p = (q, i) => `${fmt(q[i])},${fmt(q[i + 1])}`;
  const q = o.p[0];
  switch (o.t) {
    case "LINE": return ["LINE", `<b>_LINE</b>  Specificare primo punto: ${p(q, 0)}  Punto successivo: ${p(q, 2)}`];
    case "LWPOLYLINE": case "POLYLINE": return ["PLINE", `<b>_PLINE</b>  Punto iniziale: ${p(q, 0)}  …  ${q.length / 2} vertici`];
    case "CIRCLE": {
      const xs = q.filter((_, i) => i % 2 === 0), ys = q.filter((_, i) => i % 2);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      return ["CIRCLE", `<b>_CIRCLE</b>  Centro: ${fmt(cx)},${fmt(cy)}  Raggio: ${fmt((Math.max(...xs) - Math.min(...xs)) / 2)}`];
    }
    case "ARC": return ["ARC", `<b>_ARC</b>  Punto iniziale: ${p(q, 0)}  Punto finale: ${p(q, q.length - 2)}`];
    case "SPLINE": return ["SPLINE", `<b>_SPLINE</b>  Primo punto: ${p(q, 0)}`];
    case "TEXT": case "MTEXT": case "ATTRIB": {
      const x = o.x;
      const gius = x.al === "C" ? "  Giustifica: Centro" : x.al === "R" ? "  Giustifica: Destra" : "";
      return ["TEXT", `<b>_TEXT</b>${gius}  Punto: ${fmt(x.x)},${fmt(x.y)}  Altezza: ${fmt(x.h)}  Rotazione: ${Math.round(x.r)}  Testo: «${esc(x.s)}»`];
    }
    case "HATCH": case "SOLID": return ["HATCH", `<b>_HATCH</b>  Modello: SOLID  ${o.f.length || o.p.length} contorni`];
    case "IMAGE": return ["IMAGE", `<b>_IMAGEATTACH</b>  ${esc((o.img.file || "").split(/[\\/]/).pop())}`];
    default: return ["", `<b>_${o.t}</b>`];
  }
}

/* ------------------------------------------------- palette: Claude e passi */
const digitazioni = new Map();
function mostraPensiero(a, testo, genere = "pensiero") {
  a.pensiero = testo; a.genere = genere;
  aggiornaAgente(a);
  if (a !== agenteScelto()) return;
  const el = $("#pensiero");
  el.className = `pensiero genere-${genere}`;
  $("#agenteSta").textContent = S.agenti.size > 1 ? `— ${a.nome}` : "";
  $("#agenteSta").style.color = a.colore;
  clearInterval(digitazioni.get("p"));
  let i = 0;
  el.textContent = "";
  const cur = document.createElement("span"); cur.className = "cursore";
  const passo = Math.max(2, Math.ceil(testo.length / 60));     // i testi lunghi si scrivono piu' in fretta
  digitazioni.set("p", setInterval(() => {
    i += passo;
    el.textContent = testo.slice(0, i); el.appendChild(cur);
    if (i >= testo.length) { clearInterval(digitazioni.get("p")); el.textContent = testo; }
  }, 14));
}
function mostraPensieroScelto() {
  const a = agenteScelto();
  if (!a) return;
  const el = $("#pensiero");
  clearInterval(digitazioni.get("p"));
  el.className = a.pensiero ? `pensiero genere-${a.genere}` : "pensiero vuoto";
  el.textContent = a.pensiero || "In attesa di istruzioni…";
  $("#agenteSta").textContent = S.agenti.size > 1 ? `— ${a.nome}` : "";
  $("#agenteSta").style.color = a.colore;
}
function voce(ev, classe = "", a = null) {
  const li = document.createElement("li");
  if (classe) li.className = classe;
  if (a) { li.dataset.ag = a.nome; li.style.setProperty("--colAg", a.colore); }
  const tag = a && S.agenti.size > 1 ? `<span class="tagAg" style="color:${a.colore}">${esc(a.nome)}</span>` : "";
  li.innerHTML = `<div class="riga1"><span class="ora">${esc(ev.ora || "")}</span>${tag}<span class="testo">${esc(ev.nota || ev.testo || "")}</span></div>`;
  const lista = $("#passi");
  const giu = lista.scrollTop + lista.clientHeight >= lista.scrollHeight - 30;
  lista.appendChild(li);
  while (lista.children.length > 400) lista.removeChild(lista.firstChild);
  if (giu) lista.scrollTop = lista.scrollHeight;
  $("#nPassi").textContent = `(${lista.children.length})`;
  return li;
}
function conteggi(li, d, chiave) {
  const parti = [];
  if (d.aggiunti) parti.push(`<span class="piu">+${d.aggiunti}</span>`);
  if (d.tolti) parti.push(`<span class="meno">−${d.tolti}</span>`);
  if (d.spostati) parti.push(`<span class="sp">↔${d.spostati}</span>`);
  if (d.modificati) parti.push(`<span class="mod">✎${d.modificati}</span>`);
  if (parti.length) li.insertAdjacentHTML("beforeend", `<div class="conta">${parti.join("")}</div>`);
  if (chiave && S.fogli.size > 1) li.insertAdjacentHTML("beforeend", `<div class="foglioLi">${esc(docDi(chiave))} · ${esc(etichettaFoglio(S.fogli.get(chiave), false))}</div>`);
}
function disegnaAgenti() {
  const ul = $("#agenti");
  ul.innerHTML = "";
  for (const a of S.agenti.values()) {
    const li = document.createElement("li");
    li.className = (a.lavora ? "lavora " : "") + (a.nome === S.agenteSel ? "scelto" : "");
    li.innerHTML = `<span class="puntoAg attivita" style="background:${a.colore}"></span><span class="nomeAg">${esc(a.nome)}</span>
      <span class="statoAg"></span><span class="doveAg"></span><div class="barraAg"><div style="background:${a.colore}"></div></div>`;
    li.title = "Clic: segui questo agente e scrivigli in chat";
    li.onclick = () => scegliAgente(a.nome);
    a.el = li; a.elBarra = li.querySelector(".barraAg div");
    ul.appendChild(li);
    aggiornaAgente(a);
  }
  $("#nAgenti").textContent = S.agenti.size ? `(${S.agenti.size})` : "";
  aggiornaDestinatari();
}
function aggiornaAgente(a) {
  if (!a.el) return;
  a.el.classList.toggle("lavora", a.lavora);
  a.el.querySelector(".attivita").classList.toggle("lavora", a.lavora);
  const md = modelloDi(a.nome);
  a.el.querySelector(".statoAg").textContent = (a.lavora ? "sta disegnando" : a.stato === "fatto" ? "ha finito" : "in attesa")
    + (md ? " · " + md.nome : "");
  a.el.querySelector(".statoAg").title = md ? testoModello(md) : "";
  if (S.filo === a.nome) { disegnaTestaFilo(); aggiornaVivo(); }
  disegnaFili();
  const f = S.fogli.get(a.foglio);
  a.el.querySelector(".doveAg").textContent = (f ? `${docDi(a.foglio)} · ${etichettaFoglio(f, false)} — ` : "") + (a.pensiero || "");
  for (const el of $$(".schedaDoc")) {
    const lav = [...S.agenti.values()].some((x) => x.lavora && docDi(x.foglio) === el.dataset.doc);
    el.classList.toggle("lavora", lav);
  }
  evidenziaFogli();
}
function scegliAgente(nome) {
  S.agenteSel = nome; S.agenteSelUtente = true;
  const a = S.agenti.get(nome);
  disegnaAgenti(); mostraPensieroScelto();
  if (a && a.foglio) {
    const r = riquadroFuoco();
    if (S.riquadri.length === 1 || r.legame === "auto") mostraFoglio(r, a.foglio, true);
    else { const rr = S.riquadri.find((x) => x.legame === "agente:" + nome); if (rr) S.fuoco = rr.i; }
  }
  S.filo = nome; disegnaFilo();
  aggiornaFuoco();
}

/* ------------------------------------------------- schede e gestione fogli */
let cambioProgrammato = false;
function cambioFogli() {
  if (cambioProgrammato) return;
  cambioProgrammato = true;
  requestAnimationFrame(() => {
    cambioProgrammato = false;
    $("#vuoto").hidden = S.fogli.size > 0;
    disegnaElencoFogli();
    if (S.pannelloAuto && !S.pannelloFogli && [...S.fogli.values()].some((f) => f.meta.n_fogli > 1)) {
      S.pannelloAuto = false; mostraPannelloFogli(true, false);
    }
    aggiornaRiquadri();
  });
}
function disegnaSchedeDoc() {
  const box = $("#schedeDoc");
  const r = riquadroFuoco();
  const att = r && r.foglio ? docDi(r.foglio) : null;
  box.innerHTML = "";
  for (const d of docs()) {
    const n = fogliDoc(d).length;
    const b = document.createElement("button");
    b.className = "schedaDoc" + (d === att ? " attiva" : "") + (typeof docConBozza === "function" && docConBozza(d) ? " nonSalvato" : "");
    b.dataset.doc = d; b.setAttribute("role", "tab");
    const lav = [...S.agenti.values()].find((x) => x.lavora && docDi(x.foglio) === d);
    b.innerHTML = `<span class="attivita${lav ? " lavora" : ""}" style="background:${lav ? lav.colore : "transparent"}"></span><span>${esc(d)}</span>${n > 1 ? `<span class="nf">${n} fogli</span>` : ""}`;
    b.onclick = () => { const k = S.ultimoDoc.get(d) || fogliDoc(d)[0].nome; mostraFoglio(riquadroFuoco(), k, true); };
    box.appendChild(b);
  }
}
function mostraPannelloFogli(si, salva = true) {
  S.pannelloFogli = si;
  $("#pannelloFogli").hidden = !si;
  $("#btnGestioneFogli").classList.toggle("acceso", si);
  $("#impFogli").checked = si;
  if (si) { disegnaElencoFogli(); for (const k of S.fogli.keys()) richiediMiniatura(k); }
  if (salva) salvaImp();
}
function voceFoglio(f) {
  const div = document.createElement("div");
  div.className = "voceFoglio";
  div.dataset.foglio = f.nome;
  const m = f.meta;
  div.innerHTML = `<img class="mini" alt="" ${f.mini ? `src="${f.mini}"` : ""}><div class="etFoglio">
    <span class="num">${m.num !== undefined && m.num !== null ? esc(m.num) : ""}</span><span class="desc">${esc(m.desc || (m.num !== undefined ? "" : f.nome))}</span>
    ${m.stampa === false ? `<span class="noStampa" title="Deselezionato nell'ultima stampa di SPAC (CFG\\DCL_CFG.INI)">non in stampa</span>` : ""}</div>
    <span class="segni"></span>`;
  div.onclick = () => mostraFoglio(riquadroFuoco(), f.nome, true);
  return div;
}
let elencoProgrammato = false;
function disegnaElencoFogli() {
  if (!S.pannelloFogli || elencoProgrammato) return;
  elencoProgrammato = true;
  requestAnimationFrame(() => {
    elencoProgrammato = false;
    const box = $("#elencoFogli");
    const scorri = box.scrollTop;
    box.innerHTML = "";
    const gruppi = [];
    if (S.gruppo === "agente" && S.agenti.size) {
      for (const a of S.agenti.values()) gruppi.push({ chiave: "ag:" + a.nome, titolo: `<span class="testaAgente"><span class="puntoAg" style="background:${a.colore}"></span>${esc(a.nome)}</span>`, fogli: a.fogli.map((k) => S.fogli.get(k)).filter(Boolean), agente: a });
      const senza = [...S.fogli.values()].filter((f) => ![...S.agenti.values()].some((a) => a.fogli.includes(f.nome)));
      if (senza.length) gruppi.push({ chiave: "ag:-", titolo: "Nessun agente", fogli: senza });
    } else {
      for (const d of docs()) gruppi.push({ chiave: "doc:" + d, titolo: esc(d), fogli: fogliDoc(d) });
    }
    for (const g of gruppi) {
      const det = document.createElement("details");
      det.className = "gruppoFogli"; det.dataset.chiave = g.chiave;
      det.open = !S.gruppiChiusi.has(g.chiave);
      det.addEventListener("toggle", () => { if (det.open) S.gruppiChiusi.delete(g.chiave); else S.gruppiChiusi.add(g.chiave); });
      det.innerHTML = `<summary>${g.titolo}<span class="nf">${g.fogli.length}</span></summary>`;
      if (g.agente) det.querySelector("summary").addEventListener("click", (e) => { if (e.detail === 2) scegliAgente(g.agente.nome); });
      for (const f of g.fogli) det.appendChild(voceFoglio(f));
      box.appendChild(det);
    }
    box.scrollTop = scorri;
    evidenziaFogli();
  });
}
function evidenziaFogli() {
  if (!S.pannelloFogli) return;
  const r = riquadroFuoco();
  for (const el of $$(".voceFoglio")) {
    const k = el.dataset.foglio;
    el.classList.toggle("attivo", !!r && r.foglio === k);
    const lav = [...S.agenti.values()].filter((a) => a.lavora && a.foglio === k);
    el.classList.toggle("lavora", lav.length > 0);
    if (lav.length) el.style.setProperty("--colAg", lav[0].colore);
    const ag = [...S.agenti.values()].filter((a) => a.fogli.includes(k));
    const nr = S.richieste.filter((q) => q.foglio === k && q.stato !== "fatta").length;
    el.querySelector(".segni").innerHTML = ag.map((a) => `<span class="puntoAg${a.lavora && a.foglio === k ? " attivita lavora" : ""}" title="${esc(a.nome)}" style="background:${a.colore}"></span>`).join("") +
      (nr ? `<span class="badge" title="Richieste aperte">${nr}</span>` : "");
  }
}
function richiediMiniatura(k) {
  S.miniature.add(k);
  if (!S.giroMini) S.giroMini = setTimeout(giroMiniature, 120);
}
function giroMiniature() {
  S.giroMini = null;
  if (!S.pannelloFogli || !S.miniature.size) return;
  const k = S.miniature.values().next().value;
  S.miniature.delete(k);
  const f = S.fogli.get(k);
  const occupato = [...S.agenti.values()].some((a) => a.lavora && a.foglio === k);
  if (f && occupato) S.miniature.add(k);                 // si rifa' alla fine del passo
  else if (f && zonaFoglio(f)) {
    f.mini = rendiStatico(visti(f), zonaFoglio(f), 2, 300, S.scuro).toDataURL("image/png");
    for (const im of $$(`.voceFoglio[data-foglio="${CSS.escape(k)}"] img`)) im.src = f.mini;
  }
  if (S.miniature.size) S.giroMini = setTimeout(giroMiniature, occupato ? 400 : 30);
}

/* ------------------------------------------------------- esecuzione passi */
async function eseguiPasso(ev, a, tutto = false) {
  const f = foglio(ev.foglio, ev.meta);
  avviaAgente(a, ev.foglio);
  const vis = riquadriSu(ev.foglio).length > 0;
  const k = !S.anima || !vis ? 0 : 1 / (1 + 0.6 * a.coda.length);   // coda lunga: piu' veloce; foglio non visibile: subito
  const D = (ms) => Math.max(1, ms * k);
  const li = voce(ev.nota ? ev : { ...ev, nota: tutto ? `Disegno ${etichettaFoglio(f)}` : "Modifica" }, "", a);
  $$("#passi li.corrente").forEach((x) => { if (x.dataset.ag === a.nome) x.classList.remove("corrente"); });
  li.classList.add("corrente");
  if (ev.nota) mostraPensiero(a, ev.nota, "azione");
  const agg = ev.aggiunti || [], tolti = ev.tolti || [], sp = ev.spostati || [], mod = ev.modificati || [];
  conteggi(li, { aggiunti: agg.length, tolti: tolti.length, spostati: sp.length, modificati: mod.length }, ev.foglio);
  voceAttivita(a.nome, ev, { aggiunti: agg.length, tolti: tolti.length, spostati: sp.length, modificati: mod.length });
  let bb = null, bm = null;
  for (const o of agg) bb = unione(bb, o.b);
  for (const s of sp) { bm = unione(bm, s.a.b); const v = f.ogg.get(s.da); if (v) bm = unione(bm, v.b); }
  for (const m of mod) bm = unione(bm, m.a.b);
  for (const id of tolti) { const v = oggDi(f, id); if (v) bm = unione(bm, v.b); }
  bb = unione(bb, bm);
  li.onclick = () => vaiAZona(ev.foglio, bb, [80, 55]);
  if (tutto) { estensione(f); f.b = unione(f.b, bb); for (const r of riquadriSu(ev.foglio)) adattaR(r, false); }
  else if (bm && k) seguiBox(a, ev.foglio, bm);
  const col = S.agenti.size > 1 ? a.colore : HL;
  const t0 = S.vt;
  let fine = t0;
  a.t0 = t0;
  if (k === 0) {                       // subito, senza animazione
    for (const id of tolti) f.ogg.delete(id);
    for (const s of sp) { f.ogg.delete(s.da); f.ogg.set(s.a.id, prepara(s.a)); }
    for (const m of mod) { f.ogg.delete(m.da); f.ogg.set(m.a.id, prepara(m.a)); }
    for (const o of agg) f.ogg.set(o.id, prepara(o));
    if (tolti.length) rigaComando(`<b>_ERASE</b>  ${tolti.length} oggetti`, "cmd", a);
    if (sp.length) rigaComando(`<b>_MOVE</b>  ${sp.length} oggetti`, "cmd", a);
    if (mod.length) rigaComando(`<b>_TEXTEDIT</b>  ${mod.length} testi`, "cmd", a);
    if (agg.length) rigaComando(`${agg.length} oggetti nuovi`, "cmd", a);
  } else {
    // 1) cancellazioni, 2) spostamenti, 3) testi riscritti, 4) oggetti nuovi: le fasi si accavallano
    if (tolti.length) {
      rigaComando(`<b>_ERASE</b>  Selezionare oggetti: ${tolti.length} trovati`, "cmd", a);
      promptCorrente("ERASE"); lampeggiaBottone("ERASE", 900);
      for (const id of tolti) { const o = oggDi(f, id); if (o) o.anim = { tipo: "via", t0, dur: D(450) }; }
      fine = t0 + D(450);
    }
    const tm = t0 + (tolti.length ? D(120) : 0);
    const gruppi = new Map();
    for (const s of sp) { const g = `${s.dx},${s.dy}`; if (!gruppi.has(g)) gruppi.set(g, []); gruppi.get(g).push(s); }
    for (const [g, lst] of gruppi) {
      const [dx, dy] = g.split(",").map(Number);
      alTempo(tm, () => {
        rigaComando(`<b>_MOVE</b>  Selezionare oggetti: ${lst.length} trovati  Punto base: 0,0  Secondo punto: @${fmt(dx)},${fmt(dy)}`, "cmd", a);
        promptCorrente(`MOVE  @${fmt(dx)},${fmt(dy)}`); lampeggiaBottone("MOVE", 900);
      });
      for (const s of lst) {
        f.ogg.delete(s.da);
        const o = prepara(s.a);
        o.anim = { tipo: "sposta", t0: tm, dur: D(700), dx: s.dx, dy: s.dy, ag: a.nome, col };
        f.ogg.set(o.id, o);
      }
      fine = Math.max(fine, tm + D(700));
    }
    mod.forEach((m, i) => {
      const ti = tm + D(90 * i);
      const v = f.ogg.get(m.da);
      alTempo(ti, () => {
        rigaComando(`<b>_TEXTEDIT</b>  «${esc(v && v.x ? v.x.s : "")}» → «${esc(m.a.x.s)}»`, "cmd", a);
        promptCorrente("TEXTEDIT"); lampeggiaBottone("TEXTEDIT", 800);
      });
      if (v) v.anim = { tipo: "via", t0: ti, dur: D(280) };
      const o = prepara(m.a);
      o.anim = { tipo: "traccia", t0: ti + D(200), dur: D(Math.min(700, 50 + 35 * m.a.x.s.length)), ag: a.nome, col };
      f.ogg.set(o.id, o);
      fine = Math.max(fine, o.anim.t0 + o.anim.dur);
    });
    if (agg.length) {
      const dur = agg.map((o) => {
        if (o.x) return Math.min(800, 80 + 35 * o.x.s.length);
        if (o.img) return 450;
        if (o.f && o.f.length && !o.p.length) return 200;
        let L = 0; for (const q of o.p) for (let i = 2; i < q.length; i += 2) L += Math.hypot(q[i] - q[i - 2], q[i + 1] - q[i - 1]);
        return Math.max(100, Math.min(1000, L * 5));
      });
      const tot = dur.reduce((x, y) => x + y, 0);
      const budget = tutto ? 18000 : 10000;
      const kk = tot > budget ? budget / tot : 1;
      let t = t0 + ((tolti.length || sp.length || mod.length) ? D(200) : 0);
      const maxRighe = 45;
      agg.forEach((o, i) => {
        prepara(o);
        const d = D(dur[i] * kk);
        o.anim = { tipo: "traccia", t0: t, dur: d, ag: a.nome, col };
        f.ogg.set(o.id, o);
        const [cmd, html] = comandoDi(o);
        alTempo(t, () => {
          if (!tutto) insegui(a, ev.foglio, o.b);
          a.pennaFoglio = ev.foglio;
          if (i < maxRighe) { rigaComando(html, "cmd", a); promptCorrente(cmd); lampeggiaBottone(cmd, Math.max(250, d)); }
          else if (i === maxRighe) rigaComando(`… e altri ${agg.length - maxRighe} oggetti`, "nota", a);
          else { promptCorrente(cmd); lampeggiaBottone(cmd, Math.max(250, d)); }
        });
        t += d;
      });
      fine = Math.max(fine, t);
    }
    a.finePasso = fine;
    await attendi(fine - S.vt + 40);
    a.finePasso = null;
  }
  pulisciMorti(f);
  estensione(f);
  if (!tutto && bb && agg.length && k) seguiBox(a, ev.foglio, bb);     // alla fine: il risultato del passo
  promptCorrente("");
  f.ver++; richiediMiniatura(ev.foglio);
  if (f.bozza && f.bozza.length) ricalcolaBozza(f);
  if (riquadroFuoco() && riquadroFuoco().foglio === ev.foglio) aggiornaFuoco();
  if (ev.totale !== undefined && ev.totale !== vivi(f)) risincronizza(ev.foglio);
}
async function risincronizza(k) {
  try {
    const r = await fetch("/api/foglio?nome=" + encodeURIComponent(k));
    if (!r.ok) return;
    const d = await r.json();
    riempi(k, d.oggetti, d.meta);
  } catch (e) { console.warn("risincronizzazione", e); }
}

async function esegui(ev, a) {
  switch (ev.tipo) {
    case "nota": {
      const li = voce(ev, ev.genere === "pensiero" ? "pensiero-li" : "", a);
      rigaComando(`${esc(a.nome)}: ${esc(ev.testo)}`, "nota");
      if (ev.foglio && S.fogli.has(ev.foglio)) avviaAgente(a, ev.foglio);
      mostraPensiero(a, ev.testo, ev.genere || "pensiero");
      vocePensiero(a.nome, ev);
      li.onclick = () => { if (a.foglio) vaiAZona(a.foglio, null); };
      await attendi(S.anima ? 120 : 0);             // il disegno parte subito: il testo si scrive mentre disegna
      break;
    }
    case "comando": rigaComando(esc(ev.testo), "cmd", a); break;
    case "fuoco":
      if (ev.foglio && S.fogli.has(ev.foglio)) avviaAgente(a, ev.foglio);
      if (ev.zona) for (const r of riquadriCheSeguono(a)) if (r.foglio === ev.foglio || !ev.foglio) vaiA(r, adattaA(r, ev.zona, 1.1));
      await attendi(S.anima ? 300 : 0);
      break;
    case "attiva": if (S.fogli.has(ev.foglio)) mostraFoglio(riquadroFuoco(), ev.foglio, true); break;
    case "foglio":
      if (ev.anima) {
        foglio(ev.foglio, ev.meta);
        await eseguiPasso({ ...ev, aggiunti: ev.oggetti, tolti: [], spostati: [], modificati: [] }, a, true);
      } else {
        const nuovo = !S.fogli.has(ev.foglio);
        riempi(ev.foglio, ev.oggetti, ev.meta);
        if (!a.fogli.includes(ev.foglio)) a.fogli.push(ev.foglio);
        if (ev.attiva) avviaAgente(a, ev.foglio);
        for (const r of S.riquadri) {
          if (!r.foglio) mostraFoglio(r, ev.foglio);
          else if (r.foglio === ev.foglio && nuovo) adattaR(r, false);
        }
        if (ev.nota) { voce(ev, "", a); rigaComando(`${esc(a.nome)}: ${esc(ev.nota)}`, "nota"); }
        if (ev.attiva || !ev.meta || ev.meta.ordine === undefined) rigaComando(`<b>_OPEN</b>  ${esc(ev.meta && ev.meta.n_fogli ? ev.meta.doc + " (" + ev.meta.n_fogli + " fogli)" : ev.foglio)}  (${ev.oggetti.length} oggetti)`, "cmd", a);
        cambioFogli();
        if (riquadroFuoco() && riquadroFuoco().foglio === ev.foglio) aggiornaFuoco();
      }
      break;
    case "passo": await eseguiPasso(ev, a); break;
  }
}

/* ------------------------------------------------------------- eventi SSE */
function stato(ev) {
  S.fogli.clear(); S.timer = []; S.attese.forEach((x) => x.r()); S.attese = [];
  for (const a of S.agenti.values()) a.coda = [];
  S.agenti.clear();
  $("#passi").innerHTML = ""; storia.innerHTML = "";
  for (const x of ev.agenti || []) Object.assign(agente(x.nome), { colore: x.colore, fogli: x.fogli || [], foglio: x.foglio, pensiero: x.attivita || "", stato: x.stato,
    modello: x.modello, effort: x.effort, compito: x.compito });
  S.sessione = ev.sessione || null;
  for (const f of ev.fogli) { const x = riempi(f.foglio, f.oggetti, f.meta); x.sfondo = f.sfondo || null; preparaSfondo(x); }
  for (const r of S.riquadri) { r.foglio = null; r.viste.clear(); }
  const att = ev.attivo && S.fogli.has(ev.attivo) ? ev.attivo : S.fogli.keys().next().value;
  impostaLayout(S.layout, false);
  if (att) { mostraFoglio(riquadroFuoco(), att); adattaR(riquadroFuoco(), false); }
  for (const e of ev.storia || []) {
    const a = agente(e.agente);
    if (e.tipo === "nota") { voce(e, e.genere === "pensiero" ? "pensiero-li" : "", a); a.pensiero = e.testo; a.genere = e.genere; }
    else if (e.tipo === "comando") rigaComando(esc(e.testo), "cmd", a);
  }
  S.richieste = ev.richieste || [];
  S.evidenze = (ev.evidenze || []).map((e) => ({ ...e, t0: 0 }));
  S.chat = []; S.voci = new Map(); S.nonLettiFilo = new Map(); $("#messaggi").innerHTML = "";
  const oggi = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const linea = [...(ev.chat || []).map((m) => ({ k: `${m.data || oggi} ${m.ora}`, m })),
    ...(ev.storia || []).filter((e) => e.tipo === "nota").map((e) => ({ k: `${oggi} ${e.ora}`, e }))].sort((x, y) => (x.k < y.k ? -1 : x.k > y.k ? 1 : 0));
  for (const x of linea) if (x.m) aggiungiMessaggio(x.m, false); else vocePensiero(agente(x.e.agente).nome, x.e);
  S.ascolto = ev.ascolto || {};
  if (S.filo !== BASE && !S.agenti.has(S.filo)) S.filo = BASE;
  disegnaFilo();
  disegnaAgenti(); mostraPensieroScelto(); disegnaRichieste(); disegnaAscolto(); cambioFogli(); aggiornaFuoco();
}
function eventoImmediato(ev) {
  switch (ev.tipo) {
    case "stato": stato(ev); return true;
    case "agente": {
      const x = ev.agente, a = agente(x.nome);
      const nuovo = !a.el;
      const cambiaColore = a.colore !== x.colore;
      Object.assign(a, { colore: x.colore, stato: x.stato, modello: x.modello, effort: x.effort, compito: x.compito });
      for (const k of x.fogli || []) if (!a.fogli.includes(k)) a.fogli.push(k);
      disegnaFili(); if (S.filo === a.nome) { disegnaTestaFilo(); disegnaAscolto(); }
      if (nuovo || cambiaColore) disegnaAgenti(); else aggiornaAgente(a);
      aggiornaRiquadri(); disegnaElencoFogli();
      return true;
    }
    case "svuota": {
      if (ev.foglio) S.fogli.delete(ev.foglio); else S.fogli.clear();
      for (const r of S.riquadri) if (!S.fogli.has(r.foglio)) { r.foglio = null; const k = S.fogli.keys().next().value; if (k) mostraFoglio(r, k); }
      if (!ev.foglio) { S.evidenze = []; }
      cambioFogli(); aggiornaFuoco();
      return true;
    }
    case "richieste": S.richieste = ev.richieste || []; disegnaRichieste(); evidenziaFogli(); return true;
    case "chat": aggiungiMessaggio(ev.messaggio, true); return true;
    case "ascolto": S.ascolto = ev.ascolto || {}; disegnaAscolto(); return true;
    case "sessione": S.sessione = ev.sessione || null; disegnaFili(); disegnaTestaFilo(); { const a = S.agenti.get(BASE); if (a) aggiornaAgente(a); } return true;
    case "evidenze": {
      const vecchi = new Map(S.evidenze.map((e) => [e.id, e.t0]));
      S.evidenze = (ev.evidenze || []).map((e) => ({ ...e, t0: vecchi.get(e.id) ?? performance.now() }));
      const n = S.evidenze.find((e) => e.id === ev.nuova);
      if (n && S.segui) {
        const a = agente(n.agente);
        const rr = riquadriCheSeguono(a);
        const r = rr.length ? rr[0] : riquadroFuoco();
        mostraFoglio(r, n.foglio);
        const z = n.zona, cx = (z[0] + z[2]) / 2, cy = (z[1] + z[3]) / 2;
        const w = Math.max(z[2] - z[0], 70), h = Math.max(z[3] - z[1], 45);
        vaiA(r, adattaA(r, [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1.4));
        rigaComando(`${esc(n.agente)} indica: ${esc(n.testo)}`, "nota");
      }
      return true;
    }
    case "risultati": mostraRisultati(ev.titolo, ev.voci, ev.genere); return true;
    case "rendi": rendiPerServer(ev); return true;
    case "sfondo": {
      const f = S.fogli.get(ev.foglio);
      if (f) { f.sfondo = ev.sfondo; preparaSfondo(f); if (S.tab === "layer") disegnaLayer(); if (ev.sfondo) avviso(`Sotto <b>${esc(etichettaFoglio(f))}</b>: ${esc(ev.sfondo.pdf)}, pagina ${ev.sfondo.pagina}. Si spegne nella scheda Layer.`); }
      return true;
    }
  }
  return false;
}
async function rendiPerServer(ev) {
  try {
    const ogg = (ev.oggetti || []).map(prepara);
    await Promise.race([Promise.all(ogg.filter((o) => o.im).map((o) => new Promise((r) => { if (o.im.complete) r(); else { o.im.onload = r; o.im.onerror = r; } }))),
      new Promise((r) => setTimeout(r, 1500))]);
    const c = rendiStatico(ogg, ev.zona, ev.pxmm || 8, ev.max || 1800, false);
    await posta("immagine_pronta", { rid: ev.rid, png: c.toDataURL("image/png") });
  } catch (e) { console.error("rendi", e); }
}
function collega() {
  const es = new EventSource("/api/eventi");
  es.onopen = () => { $("#pallino").className = "pallino ok"; $("#statoTesto").textContent = "Collegato"; };
  es.onerror = () => { $("#pallino").className = "pallino ko"; $("#statoTesto").textContent = "Disconnesso, riprovo…"; };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (eventoImmediato(ev)) return;
    accoda(ev);
  };
}

/* ---------------------------------------------------------- chat: principale (Claude) e una per ogni agente */
const EFFORT = { low: "basso", medium: "medio", high: "alto", xhigh: "molto alto", max: "massimo" };
S.filo = BASE;                    // chat aperta: BASE = principale, altrimenti il nome dell'agente
S.voci = new Map();               // filo -> [{tipo: "msg", m} | {tipo: "pens"|"passo", testo, ora}]
S.nonLettiFilo = new Map();
S.sessione = null;                // modello ed effort di questa sessione di Claude Code, letti dall'app
function nomeModello(id) {
  const m = String(id || "").match(/claude-([a-z]+)-(\d+)-(\d+)/);
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : String(id || "");
}
function modelloDi(nome) {
  if (nome === BASE) return S.sessione ? { nome: S.sessione.nome, effort: S.sessione.effort, app: true } : null;
  const a = S.agenti.get(nome);
  return a && a.modello ? { nome: nomeModello(a.modello), effort: a.effort } : null;
}
function testoModello(x) { return x ? `${x.nome}${x.effort ? " · " + (EFFORT[x.effort] || x.effort) : ""}` : ""; }
function inAscolto(nome) { return Object.values(S.ascolto).includes(nome); }

/* testo con markdown leggero */
function inLinea(h) {
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>");
  h = h.replace(/(^|[\s(])#(\d+)\b/g, '$1<span class="rif" data-rif="$2">#$2</span>');
  return h;
}
function formatta(s) {
  const righe = String(s || "").replace(/\r/g, "").split("\n");
  let h = "", lista = null, par = [];
  const chiudiPar = () => { if (par.length) { h += `<p>${par.map((x) => inLinea(esc(x))).join("<br>")}</p>`; par = []; } };
  const chiudiLista = () => { if (lista) { h += `</${lista}>`; lista = null; } };
  for (let i = 0; i < righe.length; i++) {
    const r = righe[i];
    if (/^```/.test(r)) {
      chiudiPar(); chiudiLista();
      const cod = [];
      for (i++; i < righe.length && !/^```/.test(righe[i]); i++) cod.push(righe[i]);
      h += `<pre><code>${esc(cod.join("\n"))}</code></pre>`;
      continue;
    }
    let m;
    if ((m = r.match(/^\s*[-*•]\s+(.*)$/))) { chiudiPar(); if (lista !== "ul") { chiudiLista(); h += "<ul>"; lista = "ul"; } h += `<li>${inLinea(esc(m[1]))}</li>`; continue; }
    if ((m = r.match(/^\s*(\d+)[.)]\s+(.*)$/))) { chiudiPar(); if (lista !== "ol") { chiudiLista(); h += `<ol start="${m[1]}">`; lista = "ol"; } h += `<li>${inLinea(esc(m[2]))}</li>`; continue; }
    if ((m = r.match(/^(#{1,3})\s+(.*)$/))) { chiudiPar(); chiudiLista(); h += `<h4>${inLinea(esc(m[2]))}</h4>`; continue; }
    if (!r.trim()) { chiudiPar(); chiudiLista(); continue; }
    chiudiLista(); par.push(r);
  }
  chiudiPar(); chiudiLista();
  return h;
}
function oraBreve(o) { const m = String(o || "").match(/(\d{1,2}:\d{2})/); return m ? m[1] : ""; }
function iniziale(nome) { return esc(String(nome || "?").trim().charAt(0).toUpperCase() || "?"); }
function filoDi(m) { return m.da === "utente" ? (m.a || BASE) : m.da; }
function vociDi(filo) { if (!S.voci.has(filo)) S.voci.set(filo, []); return S.voci.get(filo); }

/* elementi della conversazione */
function elMessaggio(m) {
  const li = document.createElement("li");
  const ut = m.da === "utente";
  const a = ut ? null : agente(m.da);
  li.className = "msg " + (ut ? "utente" : "agente");
  if (a) li.style.setProperty("--colAg", a.colore);
  const rif = m.rif ? `<span class="rif" data-rif="${m.rif}">richiesta #${m.rif}</span>` : "";
  if (ut) {
    li.innerHTML = `<div class="fumetto">${formatta(m.testo)}</div>
      <div class="piedeMsg">${rif}<span class="oraMsg">${esc(oraBreve(m.ora))}</span></div>`;
  } else {
    li.innerHTML = `<div class="testaMsg"><span class="avatar" style="background:${a.colore}">${iniziale(m.da)}</span>
      <b>${esc(m.da)}</b>${rif}<span class="oraMsg">${esc(oraBreve(m.ora))}</span></div><div class="corpoMsg">${formatta(m.testo)}</div>`;
  }
  return li;
}
function elRagionamento(filo) {
  const a = S.agenti.get(filo) || agente(BASE);
  const li = document.createElement("li");
  li.className = "cot";
  li.style.setProperty("--colAg", a.colore);
  li.innerHTML = `<details><summary><span class="icoCot"></span><span class="titCot">Ragionamento</span>
    <span class="ultCot"></span><span class="nCot"></span></summary><ol class="vociCot"></ol></details>`;
  return li;
}
function aggiungiACot(li, v) {
  const ol = li.querySelector(".vociCot");
  const x = document.createElement("li");
  x.className = v.tipo === "passo" ? "passoCot" : v.genere === "azione" ? "azioneCot" : "";
  x.innerHTML = `<span class="oraCot">${esc(oraBreve(v.ora))}</span><span class="tCot">${v.tipo === "passo" ? v.testo : esc(v.testo)}</span>`;
  ol.appendChild(x);
  const n = ol.children.length;
  if (v.tipo !== "passo") li.querySelector(".ultCot").textContent = corto(v.testo, 90);
  li.querySelector(".nCot").textContent = n > 1 ? `${n} passi` : "";
}
function ultimoElemento() {
  const kids = [...$("#messaggi").children].filter((x) => x.id !== "attesaChat");
  return kids[kids.length - 1] || null;
}
function aggiungiVoce(filo, v, nuovo = true) {
  vociDi(filo).push(v);
  const lista = vociDi(filo);
  if (lista.length > 600) lista.splice(0, lista.length - 600);
  if (filo !== S.filo) {
    if (nuovo && (v.tipo === "msg" && v.m.da !== "utente")) { S.nonLettiFilo.set(filo, (S.nonLettiFilo.get(filo) || 0) + 1); disegnaFili(); }
    return;
  }
  const ol = $("#messaggi");
  if (v.tipo === "msg") ol.appendChild(elMessaggio(v.m));
  else {
    let li = ultimoElemento();
    if (!li || !li.classList.contains("cot")) { li = elRagionamento(filo); ol.appendChild(li); }
    aggiungiACot(li, v);
  }
  $("#chatVuota").hidden = true;
  aggiornaVivo(); aggiornaAttesa();
  const b = $("#flussoChat");
  if (b.scrollHeight - b.scrollTop - b.clientHeight < 160 || (v.tipo === "msg" && v.m.da === "utente")) scorriChat();
}
function disegnaFilo() {
  const ol = $("#messaggi");
  ol.innerHTML = "";
  let cot = null;
  for (const v of vociDi(S.filo)) {
    if (v.tipo === "msg") { cot = null; ol.appendChild(elMessaggio(v.m)); }
    else { if (!cot) { cot = elRagionamento(S.filo); ol.appendChild(cot); } aggiungiACot(cot, v); }
  }
  $("#chatVuota").hidden = vociDi(S.filo).length > 0;
  S.nonLettiFilo.delete(S.filo);
  disegnaAscolto(); aggiornaVivo(); scorriChat();
  const nome = S.filo === BASE ? "Claude" : S.filo;
  $("#testoChat").placeholder = `Scrivi a ${nome}…`;
  $(".saluto").textContent = S.filo === BASE ? "Su cosa lavoriamo?" : `Cosa deve fare ${nome}?`;
}
function aggiornaVivo() {
  // il blocco di ragionamento in fondo "respira" mentre l'agente lavora
  const a = S.agenti.get(S.filo);
  const ultimo = ultimoElemento();
  $$("#messaggi .cot").forEach((x) => x.classList.toggle("vivo", !!(a && a.lavora) && x === ultimo));
}
function scegliFilo(nome) {
  S.filo = nome;
  disegnaFilo();
  if (S.tab !== "chat") apriTab("chat");
  setTimeout(() => $("#testoChat").focus(), 0);
}

/* intestazione e schede delle chat */
function disegnaFili() {
  const box = $("#filiChat");
  if (!box) return;
  const nomi = [BASE, ...[...S.agenti.keys()].filter((n) => n !== BASE)];
  box.innerHTML = nomi.map((n) => {
    const a = S.agenti.get(n);
    const nl = S.nonLettiFilo.get(n) || 0;
    const md = modelloDi(n);
    return `<button class="filo${n === S.filo ? " attivo" : ""}${a && a.lavora ? " lavora" : ""}" data-filo="${esc(n)}" role="tab"
      title="${esc(n === BASE ? "Chat principale" : n)}${md ? " · " + esc(testoModello(md)) : ""}">
      <span class="avatarMini" style="background:${a ? a.colore : HL}">${iniziale(n)}</span><span class="nomeFilo">${esc(n)}</span>${nl ? `<span class="badge">${nl}</span>` : ""}</button>`;
  }).join("");
  box.querySelectorAll("[data-filo]").forEach((b) => { b.onclick = () => scegliFilo(b.dataset.filo); });
  const tot = [...S.nonLettiFilo.values()].reduce((x, y) => x + y, 0);
  S.nonLetti = S.tab === "chat" ? tot : Math.max(S.nonLetti, tot);
  aggiornaBadgeChat();
}
function disegnaTestaFilo() {
  const el = $("#infoFilo");
  if (!el) return;
  const n = S.filo, a = agente(n === BASE ? BASE : n), md = modelloDi(n);
  const f = a.foglio && S.fogli.get(a.foglio);
  let stato;
  if (a.lavora) stato = `sta lavorando${f ? " su " + etichettaFoglio(f, false) : ""}`;
  else if (inAscolto(n)) stato = "in ascolto";
  else stato = a.stato === "fatto" ? "ha finito" : "non in ascolto";
  const titMod = md && md.app ? "Modello ed effort di questa sessione di Claude Code, letti dall'app: si cambiano dal menu del modello dell'app e qui si aggiornano da soli"
    : "Modello ed effort con cui lavora l'agente";
  el.innerHTML = `<span class="avatar" style="background:${a.colore}">${iniziale(n)}</span>
    <div class="datiFilo"><div class="rigaFilo"><b>${esc(n)}</b></div>
      <div class="statoFilo${a.lavora ? " lavora" : inAscolto(n) ? " vivo" : ""}"><span class="pallino${inAscolto(n) || a.lavora ? " ok" : ""}"></span>${n === BASE ? "principale · " : ""}${esc(stato)}</div></div>
    ${md ? `<span class="pillModello" title="${esc(titMod)}">${md.app ? '<span class="icoApp"></span>' : ""}${esc(md.nome)}<span class="effortPill">${esc(EFFORT[md.effort] || md.effort || "")}</span></span>` : ""}
    ${a.compito && n !== BASE ? `<div class="compitoFilo" title="${esc(a.compito)}">${esc(a.compito)}</div>` : ""}`;
  const mc = $("#modelloComposer");
  if (mc) { mc.textContent = md ? testoModello(md) : ""; mc.title = titMod; }
}
function disegnaAscolto() {
  const el = $("#statoAscolto");
  const n = S.filo, a = S.agenti.get(n);
  let t = "";
  if (!inAscolto(n)) t = n === BASE ? "Claude non è in ascolto: il messaggio resta in coda e lo legge appena riapre la chat (o scrivigli in Claude Code)."
    : `<b>${esc(n)}</b> non è in ascolto: il messaggio resta in coda finché non lo legge.`;
  el.hidden = !t;
  el.innerHTML = t ? `<span class="pallino"></span><span>${t}</span>` : "";
  disegnaTestaFilo(); disegnaFili(); aggiornaAttesa();
}
function aggiornaAttesa() {
  // tre puntini dopo il tuo ultimo messaggio, finche' chi e' in ascolto non risponde
  const voci = vociDi(S.filo).filter((v) => v.tipo === "msg");
  const ultimo = voci.length ? voci[voci.length - 1].m : null;
  const aspetta = ultimo && ultimo.da === "utente" && inAscolto(S.filo) && Date.now() - (ultimo._t || 0) < 10 * 60 * 1000;
  let el = $("#attesaChat");
  if (!aspetta) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("li");
    el.id = "attesaChat"; el.className = "msg agente attesa";
    const a = agente(S.filo);
    el.style.setProperty("--colAg", a.colore);
    el.innerHTML = `<div class="testaMsg"><span class="avatar" style="background:${a.colore}">${iniziale(a.nome)}</span><b>${esc(a.nome)}</b></div>
      <div class="puntini"><span></span><span></span><span></span></div>`;
  }
  $("#messaggi").appendChild(el);
}

/* messaggi, pensieri e passi che arrivano */
function aggiungiMessaggio(m, nuovo) {
  if (!m || S.chat.some((x) => x.id === m.id)) return;
  if (nuovo) m._t = Date.now();
  S.chat.push(m);
  aggiungiVoce(filoDi(m), { tipo: "msg", m }, nuovo);
  if (nuovo && m.da !== "utente") {
    if (S.tab !== "chat") { S.nonLetti++; aggiornaBadgeChat(); }
    rigaComando(`${esc(m.da)} (chat): ${esc(corto(String(m.testo).replace(/[*`#]/g, ""), 120))}`, "nota");
  }
}
function vocePensiero(nome, ev) { aggiungiVoce(nome, { tipo: "pens", testo: ev.testo || ev.nota || "", ora: ev.ora || "", genere: ev.genere || "pensiero" }); }
function voceAttivita(nome, ev, d) {
  const f = S.fogli.get(ev.foglio);
  const parti = [];
  if (d.aggiunti) parti.push(`<span class="piu">+${d.aggiunti}</span>`);
  if (d.tolti) parti.push(`<span class="meno">−${d.tolti}</span>`);
  if (d.spostati) parti.push(`<span class="sp">↔${d.spostati}</span>`);
  if (d.modificati) parti.push(`<span class="mod">✎${d.modificati}</span>`);
  if (!parti.length) return;
  aggiungiVoce(nome, { tipo: "passo", testo: `${esc(f ? etichettaFoglio(f, false) : ev.foglio || "")} ${parti.join(" ")}${ev.nota ? " · " + esc(corto(ev.nota, 60)) : ""}`, ora: ev.ora || "" });
}
function aggiornaBadgeChat() {
  for (const id of ["#badgeChat", "#badgeTabChat"]) { const b = $(id); if (b) { b.hidden = !S.nonLetti; b.textContent = S.nonLetti; } }
}
function aggiornaDestinatari() {
  const s = $("#destRichiesta");
  if (!s) return;
  const v = s.value;
  s.innerHTML = `<option value="">Claude (principale)</option>` + [...S.agenti.keys()].filter((n) => n !== BASE).map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  if ([...s.options].some((o) => o.value === v)) s.value = v;
}
function adattaComposer() {
  const t = $("#testoChat");
  t.style.height = "auto";
  t.style.height = `${Math.min(180, t.scrollHeight)}px`;
  $("#inviaChat").disabled = !t.value.trim();
}
function scorriChat() { const b = $("#flussoChat"); b.scrollTop = b.scrollHeight; }
async function inviaChat() {
  const t = $("#testoChat").value.trim();
  if (!t) return;
  $("#testoChat").value = ""; adattaComposer();
  try {
    const m = await posta("chat", { testo: t, da: "utente", a: S.filo === BASE ? null : S.filo });
    const x = S.chat.find((y) => y.id === (m && m.id));
    if (x) { x._t = Date.now(); aggiornaAttesa(); scorriChat(); }
  } catch (e) { avviso("Messaggio non inviato: " + esc(e.message)); $("#testoChat").value = t; adattaComposer(); }
}
function apriTab(t) {
  S.tab = t;
  $$(".schedePalette button").forEach((b) => b.classList.toggle("attiva", b.dataset.tab === t));
  $$(".tabPalette").forEach((s) => { s.hidden = s.dataset.tab !== t; });
  if (t === "chat") { S.nonLetti = 0; S.nonLettiFilo.delete(S.filo); aggiornaBadgeChat(); disegnaFili(); setTimeout(() => { $("#testoChat").focus(); scorriChat(); }, 0); }
  if (t === "layer") disegnaLayer();
  if (t === "proprieta") disegnaProprieta();
  $("#btnChatTitolo").classList.toggle("acceso", t === "chat");
  salvaImp();
}

/* ---------------------------------------------------------- richieste */
function disegnaRichieste() {
  const ol = $("#richieste");
  ol.innerHTML = "";
  const aperte = S.richieste.filter((q) => q.stato === "nuova").length;
  $("#badgeRich").hidden = !aperte; $("#badgeRich").textContent = aperte;
  for (const q of [...S.richieste].reverse()) {
    const li = document.createElement("li");
    li.className = "rich";
    const st = { nuova: ["inviata", ""], letta: ["Claude l'ha vista", "letta"], fatta: ["fatta", "fatta"] }[q.stato] || [q.stato, ""];
    const dove = `${q.doc && q.num ? q.doc + " · Fg " + q.num : q.foglio || ""}${q.desc ? " · " + q.desc : ""}`;
    li.innerHTML = `<div class="testaRich"><span class="idRich">#${q.id}</span><span class="dove" title="${esc(dove)}">${esc(dove)}</span>
      <span class="chipStato ${st[1]}">${st[0]}</span></div>
      ${q.img ? `<img src="${q.img}" alt="Zona della richiesta ${q.id}" loading="lazy">` : ""}
      <div class="testoRich">${esc(q.testo || "(senza testo)")}</div>
      ${q.risposta ? `<div class="risposta">${formatta(q.risposta)}</div>` : ""}
      <div class="azioniRich"><span class="metaRich">${esc(q.tipo)} · ${q.n_oggetti || 0} oggetti${q.n_schizzo ? ` · ${q.n_schizzo} segni` : ""}${q.agente ? " · per " + esc(q.agente) : ""}</span>
      <button data-az="via" title="Togli la richiesta">Togli</button></div>`;
    li.onclick = (e) => {
      if (e.target.dataset.az === "via") { e.stopPropagation(); posta("richiesta_via", { id: q.id }); return; }
      vaiAZona(q.foglio, q.zona || (q.punto ? [q.punto[0] - 10, q.punto[1] - 8, q.punto[0] + 10, q.punto[1] + 8] : null));
    };
    ol.appendChild(li);
  }
}
function apriDialogo(d) {
  const f = S.fogli.get(d.foglio);
  if (!f) return;
  S.dialogo = d;
  const dlg = $("#dialogoRichiesta");
  let n = 0, nt = 0;
  if (d.ids) { n = d.ids.length; nt = d.ids.filter((id) => (oggDi(f, id) || {}).x).length; }
  else if (d.zona) for (const o of visti(f)) if (tocca(o.b, d.zona)) { n++; if (o.x) nt++; }
  const z = d.zona;
  const dim = z ? `${fmt(z[2] - z[0])} × ${fmt(z[3] - z[1])} mm` : d.punto ? `punto ${fmt(d.punto[0])}, ${fmt(d.punto[1])}` : "";
  $("#titoloDialogo").textContent = { area: "Chiedi a Claude su questa zona", punto: "Nota su questo punto", oggetti: "Chiedi a Claude su questi oggetti",
    schizzo: "Manda lo schizzo a Claude" }[d.tipo];
  $("#infoRichiesta").innerHTML = `<b>${esc(docDi(d.foglio))}</b> · ${esc(etichettaFoglio(f))}<br>${dim}${d.tipo !== "punto" ? ` · <b>${n}</b> oggetti, ${nt} testi` : ""}`;
  $("#destRichiesta").value = S.agenteSelUtente && S.agenteSel !== BASE ? S.agenteSel : "";
  dlg.hidden = false;
  // vicino alla zona, dentro la vista
  const r = d.r || riquadroFuoco();
  const vp = $("#viewport").getBoundingClientRect(), rb = r.el.getBoundingClientRect();
  const zz = z || [d.punto[0], d.punto[1], d.punto[0], d.punto[1]];
  const [X0, Y0] = schermo(r, zz[0], zz[3]), [X1, Y1] = schermo(r, zz[2], zz[1]);
  const ox = rb.left - vp.left, oy = rb.top - vp.top, W = dlg.offsetWidth, H = dlg.offsetHeight;
  let x = ox + X1 + 14, y = oy + Y0;
  if (x + W > vp.width - 44) x = ox + X0 - W - 14;
  if (x < 8) x = Math.max(8, Math.min(vp.width - W - 44, ox + (X0 + X1) / 2 - W / 2));
  y = Math.max(8, Math.min(vp.height - H - 110, y));
  dlg.style.left = `${x}px`; dlg.style.top = `${y}px`;
  $("#testoRichiesta").value = "";
  setTimeout(() => $("#testoRichiesta").focus(), 0);
}
function chiudiDialogo() { $("#dialogoRichiesta").hidden = true; S.dialogo = null; }
async function inviaRichiesta() {
  const d = S.dialogo;
  if (!d) return;
  const f = S.fogli.get(d.foglio);
  const testo = $("#testoRichiesta").value.trim();
  const ag = $("#destRichiesta").value || null;
  const b = $("#inviaRichiesta"); b.disabled = true;
  try {
    let z = d.zona;
    if (!z && d.ids) { z = null; for (const id of d.ids) { const o = oggDi(f, id); if (o) z = unione(z, o.b); } }
    if (!z && d.punto) z = [d.punto[0] - 12, d.punto[1] - 9, d.punto[0] + 12, d.punto[1] + 9];
    const zm = margine(z, 0.08, 3);
    const lato = Math.max(zm[2] - zm[0], zm[3] - zm[1]);
    const png = rendiStatico(visti(f), zm, Math.min(14, Math.max(3, 1400 / lato)), 1600, false).toDataURL("image/png");
    const r = await posta("richiesta", { tipo: d.tipo, foglio: d.foglio, zona: d.tipo === "punto" ? null : (d.zona || null), punto: d.punto || null,
      ids: d.ids || null, testo, agente: ag, png, schizzo: d.tipo === "schizzo" ? datiSchizzo(f) : null });
    if (d.tipo === "schizzo") togliSchizzo(f, false);
    chiudiDialogo();
    const vivo = Object.keys(S.ascolto).length > 0;
    avviso(`Richiesta <b>#${r.id}</b> inviata${ag ? " a " + esc(ag) : " a Claude"}.` + (vivo ? " Claude la sta ricevendo." :
      ` Claude non è in ascolto adesso: in chat scrivigli <b>«guarda la richiesta ${r.id}»</b>.`), vivo ? 4000 : 9000);
    if (d.tipo === "oggetti") { S.sel.clear(); aggiornaSelezione(); }
  } catch (e) { avviso("Richiesta non inviata: " + esc(e.message)); }
  finally { b.disabled = false; }
}

/* ---------------------------------------------------------- risultati */
function mostraRisultati(titolo, voci, genere = "elenco") {
  S.risultati = { titolo, voci: voci || [], i: 0, genere };
  $("#titoloRis").textContent = titolo;
  const ol = $("#voceRis"); ol.innerHTML = "";
  S.risultati.voci.forEach((v, i) => {
    const li = document.createElement("li");
    const f = S.fogli.get(v.foglio);
    li.innerHTML = `<span class="dove">${esc(f && f.meta.num !== undefined ? "Fg " + f.meta.num : corto(v.foglio, 14))}</span><span>${esc(corto(v.testo, 60))}</span>`;
    li.onclick = () => vaiRisultato(i);
    ol.appendChild(li);
  });
  $("#risultati").hidden = false;
  if (!S.risultati.voci.length) { avviso(esc(titolo)); setTimeout(() => { if (S.risultati && !S.risultati.voci.length) chiudiRisultati(); }, 2500); }
  else vaiRisultato(0);
}
function vaiRisultato(i) {
  const R = S.risultati;
  if (!R || !R.voci.length) return;
  R.i = (i + R.voci.length) % R.voci.length;
  $$("#voceRis li").forEach((li, j) => li.classList.toggle("corrente", j === R.i));
  const li = $$("#voceRis li")[R.i]; if (li) li.scrollIntoView({ block: "nearest" });
  const v = R.voci[R.i];
  vaiAZona(v.foglio, v.b, [50, 32]);
}
function chiudiRisultati() { S.risultati = null; $("#risultati").hidden = true; }
function normalizza(s) { return String(s || "").replace(/\s+/g, " ").trim().toLowerCase(); }
function trova(q) {
  q = normalizza(q);
  if (!q) { chiudiRisultati(); return; }
  const voci = [];
  const r = riquadroFuoco();
  const ordine = [...S.fogli.values()].sort((a, b) => (a.nome === (r && r.foglio) ? -1 : 0) - (b.nome === (r && r.foglio) ? -1 : 0));
  for (const f of ordine) for (const o of f.ogg.values()) if (o.x && normalizza(o.x.s).includes(q)) voci.push({ foglio: f.nome, b: o.b, testo: o.x.s, ids: [o.id] });
  mostraRisultati(`Trova «${q}»: ${voci.length}`, voci.slice(0, 300), "trova");
}

/* ---------------------------------------------------------- selezione e proprieta */
function trovaOggetto(r, px, py) {
  const f = S.fogli.get(r.foglio);
  if (!f) return null;
  const [x, y] = mondo(r, px, py), tol = 6 / r.vista.s;
  const nasc = S.nascosti.get(docDi(r.foglio));
  let best = null, bd = Infinity;
  for (const o of visti(f)) {
    if (nasc && nasc.has(o.ly)) continue;
    const b = o.b;
    if (x < b[0] - tol || x > b[2] + tol || y < b[1] - tol || y > b[3] + tol) continue;
    let d = Infinity;
    if (o.x && x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3]) d = 0;
    else {
      for (const q of o.p) for (let i = 2; i < q.length; i += 2) d = Math.min(d, distSeg(x, y, q[i - 2], q[i - 1], q[i], q[i + 1]));
      if (d > tol && (o.img || (o.pieno && !o.p.length)) && x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3]) d = tol * 0.9;
    }
    if (d <= tol && d <= bd) { bd = d; best = o; }
  }
  return best;
}
function aggiornaSelezione() {
  const bar = $("#barraSel");
  const f = S.fogli.get(S.selFoglio);
  const r = S.riquadri.find((x) => x.foglio === S.selFoglio);
  if (!S.sel.size || !f || !r) { bar.hidden = true; if (S.tab === "proprieta") disegnaProprieta(); return; }
  let b = null;
  for (const id of S.sel) { const o = oggDi(f, id); if (o) b = unione(b, o.b); }
  $("#contaSel").textContent = `${S.sel.size} ${S.sel.size === 1 ? "oggetto" : "oggetti"}`;
  bar.hidden = false;
  const vp = $("#viewport").getBoundingClientRect(), rb = r.el.getBoundingClientRect();
  const [X0, Y0] = schermo(r, b[0], b[3]);
  const x = Math.max(6, Math.min(vp.width - bar.offsetWidth - 44, rb.left - vp.left + X0));
  const y = Math.max(30, rb.top - vp.top + Y0 - 34);
  bar.style.left = `${x}px`; bar.style.top = `${y}px`;
  if (S.tab === "proprieta" || S.sel.size) disegnaProprieta();
}
function riga(k, v, cl = "") { return `<tr><td>${esc(k)}</td><td class="${cl}">${esc(v)}</td></tr>`; }
function lunghezza(o) { let L = 0; for (const q of o.p) for (let i = 2; i < q.length; i += 2) L += Math.hypot(q[i] - q[i - 2], q[i + 1] - q[i - 1]); return L; }
function disegnaProprieta() {
  const box = $("#proprieta");
  const f = S.fogli.get(S.selFoglio);
  if (!S.sel.size || !f) { box.innerHTML = `<div class="introTab">Nessun oggetto selezionato. Clic su un oggetto o trascina una finestra (da sinistra: oggetti dentro; da destra: oggetti toccati).</div>`; return; }
  const ogg = [...S.sel].map((id) => oggDi(f, id)).filter(Boolean);
  let h = "";
  if (ogg.length === 1) {
    const o = ogg[0];
    h += `<table><tr><th colspan="2">${esc(o.t)}</th></tr>`;
    h += riga("Layer", o.ly) + riga("Colore", o.c || "DaLayer / primo piano") + riga("Spessore", `${o.lw} mm`);
    if (o.d) h += riga("Tipo di linea", `tratteggio ${o.d.join(" / ")} mm`);
    if (o.hd) h += riga("Handle", o.hd, "val");
    if (o.blk) h += riga("Blocco", o.blk);
    if (o.tag) h += riga("Attributo", o.tag);
    if (o.x) {
      const x = o.x;
      h += `<tr><th colspan="2">Testo</th></tr>` + riga("Contenuto", x.s, "val") + riga("Altezza", fmt(x.h)) + riga("Rotazione", `${x.r}°`) +
        riga("Fattore larghezza", x.wf) + riga("Obliquo", `${x.ob}°`) + riga("Allineamento", { L: "Sinistra", C: "Centro", R: "Destra" }[x.al] || x.al) +
        riga("Font", x.shx ? "SHX (disegnato con glifi Hershey)" : `${x.fam}${x.bold ? " grassetto" : ""}`) + riga("Punto", `${fmt(x.x)}, ${fmt(x.y)}`);
    } else if (o.p.length) {
      const q = o.p[0];
      h += `<tr><th colspan="2">Geometria</th></tr>` + riga("Inizio", `${fmt(q[0])}, ${fmt(q[1])}`) + riga("Fine", `${fmt(q[q.length - 2])}, ${fmt(q[q.length - 1])}`) +
        riga("Vertici", o.p.reduce((n, q2) => n + q2.length / 2, 0)) + riga("Lunghezza", `${fmt(lunghezza(o))} mm`);
    }
    h += riga("Estensione", o.b.map(fmt).join(", ")) + "</table>";
  } else {
    const per = (k) => { const m = new Map(); for (const o of ogg) m.set(o[k], (m.get(o[k]) || 0) + 1); return [...m].sort((a, b) => b[1] - a[1]); };
    h += `<table><tr><th colspan="2">${ogg.length} oggetti</th></tr>`;
    for (const [t, n] of per("t")) h += riga(t, n);
    h += `<tr><th colspan="2">Layer</th></tr>`;
    for (const [t, n] of per("ly")) h += riga(t, n);
    const testi = ogg.filter((o) => o.x);
    if (testi.length) {
      h += `<tr><th colspan="2">Testi</th></tr>`;
      for (const o of testi.slice(0, 40)) h += riga(`${fmt(o.x.x)}, ${fmt(o.x.y)}`, o.x.s, "val");
      if (testi.length > 40) h += riga("…", `altri ${testi.length - 40}`);
    }
    h += "</table>";
  }
  h += `<div id="datiSpac"></div><div class="azioniRich"><button class="primario" id="propChiedi">Chiedi a Claude…</button></div>`;
  box.innerHTML = h;
  $("#propChiedi").onclick = chiediSuSelezione;
  pannelloModifica(box, ogg, f);
  if (ogg.length === 1 && ogg[0].hd && f.meta && f.meta.db) datiSpac(S.selFoglio, ogg[0].hd);
}
const cacheSpac = new Map();
async function datiSpac(k, hd) {
  const chiave = `${k}|${hd}`;
  let d = cacheSpac.get(chiave);
  if (!d) {
    try { d = await (await fetch(`/api/spac?foglio=${encodeURIComponent(k)}&hd=${encodeURIComponent(hd)}`)).json(); }
    catch (e) { return; }
    cacheSpac.set(chiave, d);
  }
  const box = $("#datiSpac");
  if (!box || !d || !d.tabelle) return;
  const t = Object.entries(d.tabelle);
  if (!t.length) { box.innerHTML = `<table><tr><th colspan="2">Dati SPAC</th></tr>${riga("Database", "nessuna riga per questo handle")}</table>`; return; }
  let h = "<table>";
  for (const [nome, righe] of t) {
    righe.slice(0, 3).forEach((rr, i) => {
      h += `<tr><th colspan="2">SPAC · ${esc(nome)}${righe.length > 1 ? ` (${i + 1}/${righe.length})` : ""}</th></tr>`;
      for (const [c, v] of Object.entries(rr)) h += riga(c, String(v), "val");
    });
  }
  box.innerHTML = h + "</table>";
}
function chiediSuSelezione() {
  if (!S.sel.size) { avviso("Seleziona prima qualche oggetto (clic o finestra)."); return; }
  const r = S.riquadri.find((x) => x.foglio === S.selFoglio) || riquadroFuoco();
  const f = S.fogli.get(S.selFoglio);
  let z = null; for (const id of S.sel) { const o = oggDi(f, id); if (o) z = unione(z, o.b); }
  apriDialogo({ tipo: "oggetti", foglio: S.selFoglio, ids: [...S.sel], zona: z, r });
}
function disegnaLayer() {
  const r = riquadroFuoco();
  const f = r && S.fogli.get(r.foglio);
  const ul = $("#layer"); ul.innerHTML = "";
  if (!f) { $("#introLayer").textContent = "Nessun foglio."; return; }
  const doc = docDi(f.nome);
  const nasc = S.nascosti.get(doc) || new Set();
  const m = new Map();
  for (const o of f.ogg.values()) { const x = m.get(o.ly) || { n: 0, c: o.c }; x.n++; m.set(o.ly, x); }
  $("#introLayer").innerHTML = `Layer di <b>${esc(etichettaFoglio(f, false))}</b>. Spegnerli qui cambia solo la vista, non il DWG.`;
  if (f.sfondo) {
    const li = document.createElement("li");
    li.className = S.mostraSfondo ? "" : "spento";
    li.innerHTML = `<button class="occhio" title="Accendi / spegni lo sfondo">${S.mostraSfondo ? "◉" : "◌"}</button><span class="camp" style="background:#fff"></span>
      <span class="nomeLy">PDF: ${esc(f.sfondo.pdf)} p. ${f.sfondo.pagina}</span>
      <input type="range" min="0.1" max="1" step="0.05" value="${f.sfondo.opacita ?? 0.45}" title="Opacità" style="--p:${((f.sfondo.opacita ?? 0.45) - 0.1) / 0.9 * 100}%">`;
    li.querySelector(".occhio").onclick = () => { S.mostraSfondo = !S.mostraSfondo; disegnaLayer(); };
    li.querySelector("input").oninput = (e) => { f.sfondo.opacita = +e.target.value; };
    ul.appendChild(li);
  }
  for (const [ly, x] of [...m].sort((a, b) => a[0].localeCompare(b[0], "it", { numeric: true }))) {
    const li = document.createElement("li");
    li.className = nasc.has(ly) ? "spento" : "";
    li.innerHTML = `<button class="occhio" title="Accendi / spegni">${nasc.has(ly) ? "◌" : "◉"}</button><span class="camp" style="background:${x.c || (S.scuro ? "#fff" : "#000")}"></span>
      <span class="nomeLy">${esc(ly)}</span><span class="nLy">${x.n}</span>`;
    li.querySelector(".occhio").onclick = () => {
      if (!S.nascosti.has(doc)) S.nascosti.set(doc, new Set());
      const s = S.nascosti.get(doc);
      if (s.has(ly)) s.delete(ly); else s.add(ly);
      disegnaLayer();
    };
    ul.appendChild(li);
  }
}

/* ------------------------------------------------------------- segui filo */
function estremiDi(o) {
  const out = [];
  for (const q of o.p) {
    const n = q.length;
    if (n < 4) continue;
    if (Math.hypot(q[0] - q[n - 2], q[1] - q[n - 1]) < 1e-6) continue;      // contorno chiuso
    out.push([q[0], q[1]], [q[n - 2], q[n - 1]]);
  }
  return out;
}
function suSegmenti(o, x, y, tol) {
  for (const q of o.p) for (let i = 2; i < q.length; i += 2) if (distSeg(x, y, q[i - 2], q[i - 1], q[i], q[i + 1]) <= tol) return true;
  return false;
}
function seguiFilo(r, px, py, giunzioni = false) {
  const f = S.fogli.get(r.foglio);
  const o0 = trovaOggetto(r, px, py);
  if (!f || !o0 || o0.x || !o0.p.length) { avviso("Clic su un filo (una linea o una polilinea)."); return; }
  const tol = 0.15, g = 0.4;
  const lin = [...visti(f)].filter((o) => !o.x && o.p.length && !(o.anim && o.anim.tipo === "via") && estremiDi(o).length);
  const griglia = new Map();
  const chiave = (x, y) => `${Math.floor(x / g)},${Math.floor(y / g)}`;
  for (const o of lin) for (const [x, y] of estremiDi(o)) { const k = chiave(x, y); if (!griglia.has(k)) griglia.set(k, []); griglia.get(k).push(o); }
  const visti = new Set([o0.id]), coda = [o0];
  while (coda.length && visti.size < 3000) {
    const o = coda.pop();
    for (const [x, y] of estremiDi(o)) {
      const gx = Math.floor(x / g), gy = Math.floor(y / g);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const v of griglia.get(`${gx + dx},${gy + dy}`) || []) {
          if (visti.has(v.id)) continue;
          if (estremiDi(v).some(([a, b]) => Math.hypot(a - x, b - y) <= tol)) { visti.add(v.id); coda.push(v); }
        }
      }
      if (giunzioni) for (const v of lin) if (!visti.has(v.id) && suSegmenti(v, x, y, tol)) { visti.add(v.id); coda.push(v); }
    }
    if (giunzioni) for (const v of lin) if (!visti.has(v.id) && estremiDi(v).some(([a, b]) => suSegmenti(o, a, b, tol))) { visti.add(v.id); coda.push(v); }
  }
  S.sel = new Set(visti); S.selFoglio = r.foglio;
  let L = 0;
  for (const id of visti) L += lunghezza(oggDi(f, id));
  aggiornaSelezione();
  if (S.tab !== "chat") apriTab("proprieta");
  rigaComando(`<b>Filo</b>  ${visti.size} tratti, lunghezza ${fmt(L)} mm${giunzioni ? " (con giunzioni)" : ""}`);
}

/* ------------------------------------------------------------- mouse */
function puntoAgganciato(r, px, py) {
  // aggancio stile OSNAP: estremi e vertici (quadrato), punti medi (triangolo), centri dei cerchi (cerchio),
  // punto d'inserimento dei testi; gli estremi vincono a parita' di distanza
  const [x, y] = mondo(r, px, py);
  if (!S.snap) return null;
  const f = S.fogli.get(r.foglio);
  if (!f) return null;
  const tol = 10 / r.vista.s;
  let best = null, bd = tol;
  const prova = (X, Y, tipo, peso = 1) => { const d = Math.hypot(X - x, Y - y) * peso; if (d < bd) { bd = d; best = [X, Y]; best.tipo = tipo; } };
  for (const o of visti(f)) {
    const b = o.b;
    if (x < b[0] - tol || x > b[2] + tol || y < b[1] - tol || y > b[3] + tol) continue;
    if (o.x) { prova(o.x.x, o.x.y, "ins"); continue; }
    if (o.t === "CIRCLE" || o.t === "ARC") prova((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, "centro", o.t === "ARC" ? 9 : 1);
    for (const q of o.p) {
      const n = q.length, curva = n > 80;
      const passo = curva ? Math.floor(n / 40) * 2 : 2;
      for (let i = 0; i < n; i += passo) prova(q[i], q[i + 1], "fine", curva ? 1.2 : 0.9);
      prova(q[n - 2], q[n - 1], "fine", 0.9);
      if (!curva) for (let i = 2; i < n; i += 2) prova((q[i - 2] + q[i]) / 2, (q[i - 1] + q[i + 1]) / 2, "medio");
    }
  }
  for (const p of puntiComando(r)) prova(p[0], p[1], "fine", 0.9);
  return best;
}
function impostaModo(m) {
  if (m !== "disegna" && typeof fermaComandoPerModo === "function") fermaComandoPerModo(m);
  S.modo = m;
  $$(".attrezzo").forEach((b) => b.classList.toggle("acceso", b.dataset.modo === m));
  $("#modoTesto").textContent = { sel: "", pan: "PANORAMICA", area: "SELEZIONA AREA PER CLAUDE", punto: "NOTA SU UN PUNTO", misura: "DISTANZA",
    filo: "SEGUI FILO", zoomf: "ZOOM FINESTRA", disegna: "" }[m] || "";
  if (m !== "misura") S.misura = null;
  const msg = { area: "Trascina un riquadro sulla zona da far guardare a Claude.", punto: "Clic sul punto dove vuoi lasciare una nota.",
    misura: "Clic sul primo punto, poi sul secondo (aggancio: OSNAP).", zoomf: "Trascina un riquadro sulla zona da ingrandire.",
    filo: "Clic su un filo: si seleziona tutto il collegamento (Maiusc+clic: anche le giunzioni a T)." }[m];
  if (msg) rigaComando(msg, "nota");
}
function collegaMouse(r) {
  const t = r.tela;
  t.addEventListener("contextmenu", (e) => e.preventDefault());
  t.addEventListener("mousemove", (e) => {
    const b = t.getBoundingClientRect();
    S.mouse = [e.clientX - b.left, e.clientY - b.top]; S.mouseRiq = r;
    const [x, y] = mondo(r, ...S.mouse);
    S.shift = e.shiftKey;
    S.snapPt = (S.modo === "misura" || S.modo === "punto" || (S.modo === "disegna" && vuolePunto())) ? puntoAgganciato(r, ...S.mouse) : null;
    const p = S.modo === "disegna" ? puntoCursore(r) : (S.snapPt || [x, y]);
    $("#coord").textContent = `${p[0].toFixed(4)}, ${p[1].toFixed(4)}, 0.0000`;
    const d = S.drag;
    if (d && d.r === r) {
      if (Math.hypot(e.clientX - d.cx, e.clientY - d.cy) > 4) d.mosso = true;
      if (d.tipo === "pan") { r.cam = null; r.vista.tx = d.tx + (e.clientX - d.cx); r.vista.ty = d.ty + (e.clientY - d.cy); }
      if (d.tipo === "disegna") trascinaDisegno(r, [x, y]);
    }
  });
  t.addEventListener("mouseleave", () => { if (S.mouseRiq === r) { S.mouse = null; S.mouseRiq = null; } });
  t.addEventListener("mousedown", (e) => {
    e.preventDefault();
    // preventDefault lascia il fuoco nei campi di testo: lo tolgo, cosi' la tastiera torna alla riga di comando
    const att = document.activeElement;
    if (att && att.id === "inputTesto") { confermaInputTesto(true); return; }      // clic fuori dal testo: lo conferma
    if (att && ["INPUT", "TEXTAREA", "SELECT"].includes(att.tagName)) att.blur();
    chiudiMenuContesto();
    if (S.fuoco !== r.i) { S.fuoco = r.i; aggiornaFuoco(); }
    const b = t.getBoundingClientRect();
    const px = e.clientX - b.left, py = e.clientY - b.top;
    const pan = e.button === 1 || e.button === 2 || (e.button === 0 && S.modo === "pan");
    S.drag = { r, cx: e.clientX, cy: e.clientY, px, py, tx: r.vista.tx, ty: r.vista.ty, w0: mondo(r, px, py), mosso: false, bottone: e.button,
      tipo: pan ? "pan" : S.modo === "area" ? "area" : S.modo === "zoomf" ? "zoomf" : S.modo === "sel" ? "finestra"
        : S.modo === "disegna" ? "disegna" : "clic", shift: e.shiftKey };
    if (S.drag.tipo === "disegna") premiDisegno(r, mondo(r, px, py), e);
  });
  t.addEventListener("dblclick", (e) => {
    const b = t.getBoundingClientRect();
    if (doppioClicDisegno(r, e.clientX - b.left, e.clientY - b.top)) return;
    r.cam = null; adattaR(r, true);
  });
  t.addEventListener("wheel", (e) => {
    e.preventDefault();
    r.cam = null;
    const b = t.getBoundingClientRect();
    const px = e.clientX - b.left, py = e.clientY - b.top;
    const [x, y] = mondo(r, px, py);
    const k = Math.pow(1.0015, -e.deltaY);
    r.vista.s = Math.min(400, Math.max(0.05, r.vista.s * k));
    r.vista.tx = px - x * r.vista.s; r.vista.ty = py + y * r.vista.s;
    if (S.sel.size) aggiornaSelezione();
  }, { passive: false });
}
window.addEventListener("mouseup", (e) => {
  const d = S.drag;
  S.drag = null;
  if (!d) return;
  const r = d.r;
  const b = r.tela.getBoundingClientRect();
  const px = e.clientX - b.left, py = e.clientY - b.top;
  if (d.tipo === "pan") {
    if (d.bottone === 2 && !d.mosso) tastoDestro(r, px, py, e);      // clic destro: Invio nei comandi, altrimenti menu
    if (S.sel.size) aggiornaSelezione();
    return;
  }
  const p1 = mondo(r, px, py);
  if (!r.foglio) return;
  const z = [Math.min(d.w0[0], p1[0]), Math.min(d.w0[1], p1[1]), Math.max(d.w0[0], p1[0]), Math.max(d.w0[1], p1[1])];
  if (d.tipo === "disegna") { rilasciaDisegno(r, p1, e, d); return; }
  if (d.tipo === "zoomf") {
    if (d.mosso) { r.cam = null; vaiA(r, adattaA(r, z, 1.02), 350); }
    impostaModo("sel");
    return;
  }
  if (d.tipo === "area") {
    if (!d.mosso) { rigaComando("Trascina un riquadro: tieni premuto e allarga sulla zona.", "nota"); return; }
    apriDialogo({ tipo: "area", foglio: r.foglio, zona: z, r });
    return;
  }
  if (d.tipo === "finestra") {
    const f = S.fogli.get(r.foglio);
    if (S.selFoglio !== r.foglio || !d.shift) S.sel.clear();
    S.selFoglio = r.foglio;
    if (!d.mosso) {
      const o = trovaOggetto(r, px, py);
      if (o) { if (d.shift && S.sel.has(o.id)) S.sel.delete(o.id); else S.sel.add(o.id); }
    } else {
      const incrocio = p1[0] < d.w0[0];
      const nasc = S.nascosti.get(docDi(r.foglio));
      for (const o of visti(f)) {
        if (nasc && nasc.has(o.ly)) continue;
        if (incrocio ? tocca(o.b, z) : dentro(o.b, z)) S.sel.add(o.id);
      }
    }
    aggiornaSelezione();
    if (S.sel.size && S.tab !== "proprieta" && S.tab !== "chat") apriTab("proprieta");
    return;
  }
  if (d.tipo === "clic" && !d.mosso) {
    const p = S.snapPt || p1;
    if (S.modo === "punto") apriDialogo({ tipo: "punto", foglio: r.foglio, punto: p, r });
    else if (S.modo === "filo") seguiFilo(r, px, py, d.shift);
    else if (S.modo === "misura") {
      if (!S.misura || S.misura.p1 || S.misura.foglio !== r.foglio) S.misura = { foglio: r.foglio, p0: p, p1: null };
      else { S.misura.p1 = p; rigaComando(`<b>_DIST</b>  ${esc(testoMisura(S.misura.p0, p))}`); }
    }
  }
});

/* ------------------------------------------------------------- comandi */
function pausa() {
  S.pausa = !S.pausa;
  $("#btnPausa").classList.toggle("in-pausa", S.pausa);
  $("#lblPausa").textContent = S.pausa ? "Riprendi" : "Pausa";
}
function interruttori() {
  $("#btnSegui").classList.toggle("acceso", S.segui); $("#stSegui").classList.toggle("acceso", S.segui); $("#impSegui").checked = S.segui;
  $("#btnLwt").classList.toggle("acceso", S.lwt); $("#stLwt").classList.toggle("acceso", S.lwt);
  $("#stSnap").classList.toggle("acceso", S.snap); $("#impSnap").checked = S.snap;
  $("#stGriglia").classList.toggle("acceso", S.griglia); $("#btnGriglia").classList.toggle("acceso", S.griglia);
  $("#stOrto").classList.toggle("acceso", !!S.orto);
  $("#impAnima").checked = S.anima; $("#impScuro").checked = S.scuro;
  document.body.classList.toggle("chiaro", !S.scuro);
  $$('#menuImpostazioni input[name="gruppo"]').forEach((x) => { x.checked = x.value === S.gruppo; });
  $$("#pannelloFogli [data-gruppo]").forEach((b) => b.classList.toggle("acceso", b.dataset.gruppo === S.gruppo));
}
function cambiaTema() {
  S.scuro = !S.scuro; interruttori(); salvaImp();
  for (const k of S.fogli.keys()) richiediMiniatura(k);
}
function scegliGruppo(g) { S.gruppo = g; interruttori(); salvaImp(); disegnaElencoFogli(); }
function salvaPng() {
  const r = riquadroFuoco();
  if (!r || !r.foglio) return;
  r.tela.toBlob((b) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `spac_vista_${String(r.foglio).replace(/[^\w.-]+/g, "_")}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  });
}
async function controllaFoglio() {
  const r = riquadroFuoco();
  if (!r || !r.foglio) return;
  rigaComando(`Controllo sovrapposizioni su ${esc(etichettaFoglio(S.fogli.get(r.foglio)))}…`, "nota");
  try { await posta("controlla", { foglio: r.foglio, mostra: true }); }
  catch (e) { avviso("Controllo non riuscito: " + esc(e.message)); }
}
function pulisciSegni() {
  chiudiRisultati(); S.misura = null;
  if (S.evidenze.length) posta("evidenzia", { pulisci: true }).catch(() => {});
}

function collegaUI() {
  // ribbon
  $$("#schedeRibbon button").forEach((b) => { b.onclick = () => {
    $$("#schedeRibbon button").forEach((x) => x.classList.toggle("attiva", x === b));
    $$("#ribbon .pannello").forEach((p) => { p.hidden = p.dataset.scheda !== b.dataset.scheda; });
  }; });
  $$(".attrezzo").forEach((b) => { b.onclick = () => impostaModo(b.dataset.modo); });
  $$(".sceltaLayout").forEach((b) => { b.onclick = () => impostaLayout(b.dataset.layout); });
  $("#btnAdatta").onclick = $("#btnAdatta2").onclick = () => { const r = riquadroFuoco(); if (r) adattaR(r, true); };
  $("#btnSegui").onclick = $("#stSegui").onclick = () => { S.segui = !S.segui; interruttori(); salvaImp(); };
  $("#btnLwt").onclick = $("#stLwt").onclick = () => { S.lwt = !S.lwt; interruttori(); salvaImp(); };
  $("#stSnap").onclick = () => { S.snap = !S.snap; interruttori(); salvaImp(); };
  $("#stGriglia").onclick = $("#btnGriglia").onclick = () => { S.griglia = !S.griglia; interruttori(); };
  $("#btnTema").onclick = cambiaTema;
  $("#btnPausa").onclick = pausa;
  $("#btnSalta").onclick = () => { S.salta = true; };
  $("#btnDemo").onclick = async () => {
    const b = $("#btnDemo");
    if (b.disabled) return;
    b.disabled = true; setTimeout(() => { b.disabled = false; }, 8000);
    try { await posta("demo", {}); } catch (e) { rigaComando(esc(String(e)), "err"); }
  };
  $("#velocita").oninput = (e) => { S.vel = VEL[+e.target.value]; $("#velValore").textContent = `${S.vel}×`; };
  // i cursori (velocita', opacita' dello sfondo) colorano la parte gia' percorsa
  const pieno = (el) => el.style.setProperty("--p", `${(el.value - el.min) / ((el.max - el.min) || 1) * 100}%`);
  document.addEventListener("input", (e) => { if (e.target.type === "range") pieno(e.target); });
  pieno($("#velocita"));
  $("#btnGestioneFogli").onclick = () => mostraPannelloFogli(!S.pannelloFogli);
  $("#chiudiFogli").onclick = () => mostraPannelloFogli(false);
  $$("#pannelloFogli [data-gruppo]").forEach((b) => { b.onclick = () => scegliGruppo(b.dataset.gruppo); });
  $("#btnFoglioPrec").onclick = $("#navPrec").onclick = () => foglioVicino(-1);
  $("#btnFoglioSucc").onclick = $("#navSucc").onclick = () => foglioVicino(1);
  $("#btnSalvaPng").onclick = salvaPng;
  $("#btnControlla").onclick = controllaFoglio;
  $("#btnPulisci").onclick = pulisciSegni;
  $("#btnChiediSel").onclick = $("#chiediSel").onclick = chiediSuSelezione;
  $("#annullaSel").onclick = () => { S.sel.clear(); aggiornaSelezione(); };
  $("#btnChatTitolo").onclick = () => apriTab(S.tab === "chat" ? "claude" : "chat");
  $("#btnChatRibbon").onclick = () => apriTab("chat");
  $("#btnRichieste").onclick = () => apriTab("richieste");
  $$(".schedePalette button").forEach((b) => { b.onclick = () => apriTab(b.dataset.tab); });
  $("#inviaChat").onclick = inviaChat;
  $("#testoChat").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); inviaChat(); } });
  $("#testoChat").addEventListener("input", adattaComposer);
  $$(".suggerimentiChat button").forEach((b) => { b.onclick = () => { $("#testoChat").value = b.dataset.sugg; adattaComposer(); $("#testoChat").focus(); }; });
  // il pannello di destra si allarga trascinando il bordo sinistro (doppio clic: larghezza normale)
  const pal = $("#palette");
  if (S.largPalette) pal.style.width = `${S.largPalette}px`;
  $("#maniglia").addEventListener("mousedown", (e) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = pal.offsetWidth;
    document.body.classList.add("trascina");
    const muovi = (ev) => { pal.style.width = `${Math.max(260, Math.min(window.innerWidth * 0.6, w0 + x0 - ev.clientX))}px`; };
    const fine = () => {
      window.removeEventListener("mousemove", muovi); window.removeEventListener("mouseup", fine);
      document.body.classList.toggle("trascina", false); S.largPalette = pal.offsetWidth; salvaImp();
    };
    window.addEventListener("mousemove", muovi); window.addEventListener("mouseup", fine);
  });
  $("#maniglia").addEventListener("dblclick", () => { pal.style.width = ""; S.largPalette = null; salvaImp(); });
  $("#inviaRichiesta").onclick = inviaRichiesta;
  $("#annullaRichiesta").onclick = $("#annullaRichiesta2").onclick = chiudiDialogo;
  $("#testoRichiesta").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); inviaRichiesta(); } });
  $("#trovaTesto").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); if (S.risultati && S.risultati.genere === "trova" && S.risultati.q === e.target.value) vaiRisultato(S.risultati.i + (e.shiftKey ? -1 : 1)); else { trova(e.target.value); if (S.risultati) S.risultati.q = e.target.value; } }
    if (e.key === "Escape") { e.target.blur(); chiudiRisultati(); }
  });
  $("#risPrec").onclick = () => vaiRisultato(S.risultati.i - 1);
  $("#risSucc").onclick = () => vaiRisultato(S.risultati.i + 1);
  $("#risChiudi").onclick = chiudiRisultati;
  $("#messaggi").addEventListener("click", (e) => { const id = e.target.dataset && e.target.dataset.rif; if (id) { const q = S.richieste.find((x) => x.id === +id); if (q) vaiAZona(q.foglio, q.zona); } });
  // menu impostazioni
  const menu = $("#menuImpostazioni");
  $("#btnImpostazioni").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; $("#btnImpostazioni").classList.toggle("acceso", !menu.hidden); };
  document.addEventListener("mousedown", (e) => { if (!menu.hidden && !menu.contains(e.target) && !$("#btnImpostazioni").contains(e.target)) { menu.hidden = true; $("#btnImpostazioni").classList.remove("acceso"); } });
  $$('#menuImpostazioni input[name="layout"]').forEach((x) => { x.onchange = () => impostaLayout(x.value); });
  $$('#menuImpostazioni input[name="gruppo"]').forEach((x) => { x.onchange = () => { scegliGruppo(x.value); if (!S.pannelloFogli) mostraPannelloFogli(true); }; });
  $("#impFogli").onchange = (e) => mostraPannelloFogli(e.target.checked);
  $("#impAnima").onchange = (e) => { S.anima = e.target.checked; salvaImp(); };
  $("#impSegui").onchange = (e) => { S.segui = e.target.checked; interruttori(); salvaImp(); };
  $("#impScuro").onchange = (e) => { if (e.target.checked !== S.scuro) cambiaTema(); };
  $("#impSnap").onchange = (e) => { S.snap = e.target.checked; interruttori(); salvaImp(); };
  // tastiera
  window.addEventListener("keydown", (e) => {
    const scrive = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
    if (tastoDisegno(e, scrive)) return;          // comandi, Canc, Ctrl+Z/Y/S, F3/F7/F8, lettere verso la riga di comando
    if (e.key === "Escape") {
      if (!$("#dialogoRichiesta").hidden) { chiudiDialogo(); return; }
      if (scrive) { e.target.blur(); return; }
      if (S.drag) { S.drag = null; return; }
      if (S.misura) { S.misura = null; return; }
      if (S.modo !== "sel") { impostaModo("sel"); return; }
      if (S.sel.size) { S.sel.clear(); aggiornaSelezione(); return; }
      S.salta = true; return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
      e.preventDefault();
      $$("#schedeRibbon button").find((b) => b.dataset.scheda === "strumenti").click();
      $("#trovaTesto").focus(); $("#trovaTesto").select(); return;
    }
    if (scrive || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.code === "Space") { e.preventDefault(); pausa(); return; }
    if (e.key === "PageDown") { e.preventDefault(); foglioVicino(1); }
    else if (e.key === "PageUp") { e.preventDefault(); foglioVicino(-1); }
  });
}

/* ---------------------------------------------------------------- avvio */
(async function avvio() {
  caricaImp();
  collegaUI();
  interruttori();
  agente(BASE).colore = HL;
  impostaLayout(S.layout, false);
  mostraPannelloFogli(S.pannelloFogli, false);
  apriTab(S.tab || "claude");
  disegnaAscolto();
  try {
    const r = await fetch("/api/glifi");
    S.glifi = (await r.json()).glifi;
  } catch (e) { console.error("glifi", e); }
  $("#vuoto").hidden = false;
  requestAnimationFrame((t) => { S.ultimo = t; frame(t); });
  collega();
})();
