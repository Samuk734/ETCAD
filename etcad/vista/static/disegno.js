/* ETCAD Vista: l'utente disegna e modifica, stile AutoCAD.
 *
 * Le modifiche restano in una "bozza" per foglio (f.bozza, lista di operazioni) e si vedono subito sul disegno;
 * "Salva DWG" le manda al server (/api/salva_bozza), che le applica al DWG vero con backup (vista/bozza.py).
 * Lo "schizzo" (f.schizzo) e' rosso, non va mai nel DWG: si manda a Claude insieme a una richiesta.
 * Riga di comando con alias AutoCAD (L, PL, REC, C, A, T, DIM, M, CO, E, ED...), coordinate x,y @dx,dy @d<ang,
 * distanza diretta, ORTO (F8 o Maiusc), OSNAP (F3), Annulla/Ripeti, menu del tasto destro.
 * Usa le funzioni globali di app.js (S, $, mondo, schermo, prepara, disegnaOggetto, pillola...).
 */
"use strict";

const ACI = { 1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff", 5: "#0000ff", 6: "#ff00ff", 7: null, 8: "#808080", 9: "#c0c0c0", 30: "#ff7f00" };
const COL_SCHIZZO = "#ff2d78";
const D = { cmd: null, ultimo: null, seq: 1, gruppo: 1, colore: 256, h: 2.5, schizzo: false, storia: [], iStoria: 0, sugg: [], iSugg: -1, input: null };

const COMANDI = [
  { n: "LINEA", a: ["L", "LINE"], d: "Linea per punti", g: "Disegno" },
  { n: "POLILINEA", a: ["PL", "PLINE"], d: "Polilinea (Invio per finire, C per chiudere)", g: "Disegno" },
  { n: "RETTANGOLO", a: ["REC", "RECT", "RECTANG"], d: "Rettangolo da due angoli", g: "Disegno" },
  { n: "CERCHIO", a: ["C", "CIRCLE"], d: "Cerchio: centro e raggio", g: "Disegno" },
  { n: "ARCO", a: ["A", "ARC"], d: "Arco per tre punti", g: "Disegno" },
  { n: "TESTO", a: ["T", "DT", "TEXT", "DTEXT"], d: "Testo su una riga", g: "Annotazione" },
  { n: "QUOTA", a: ["DIM", "DLI", "DAL", "QUO"], d: "Quota allineata (vera, modificabile in qualsiasi CAD)", g: "Annotazione" },
  { n: "ETICHETTA", a: ["ET", "LE", "QLEADER"], d: "Etichetta sottolineata con freccia", g: "Annotazione" },
  { n: "SPOSTA", a: ["M", "MOVE"], d: "Sposta gli oggetti selezionati", g: "Modifica" },
  { n: "COPIA", a: ["CO", "CP", "COPY"], d: "Copia gli oggetti selezionati", g: "Modifica" },
  { n: "CANCELLA", a: ["E", "ERASE", "CANC"], d: "Cancella gli oggetti selezionati (tasto Canc)", g: "Modifica" },
  { n: "MODTESTO", a: ["ED", "DDEDIT", "TEXTEDIT"], d: "Modifica un testo (o doppio clic sul testo)", g: "Modifica" },
  { n: "PROPRIETA", a: ["PR", "PROPERTIES"], d: "Proprietà e modifica di layer, colore, testo", g: "Modifica" },
  { n: "SELEZIONA", a: ["SELALL", "TUTTO"], d: "Seleziona tutto il foglio (Ctrl+A)", g: "Modifica" },
  { n: "ANNULLA", a: ["U", "UNDO"], d: "Annulla l'ultima modifica (Ctrl+Z)", g: "Modifiche" },
  { n: "RIPETI", a: ["REDO"], d: "Ripeti (Ctrl+Y)", g: "Modifiche" },
  { n: "SALVA", a: ["QSAVE", "SAVE"], d: "Salva le modifiche nel DWG (Ctrl+S)", g: "Modifiche" },
  { n: "MATITA", a: ["MAT", "SK", "SKETCH"], d: "Schizzo a mano libera per Claude", g: "Claude" },
  { n: "NUVOLA", a: ["NUV", "REVCLOUD"], d: "Nuvola di revisione (schizzo per Claude)", g: "Claude" },
  { n: "SCHIZZO", a: ["SCH"], d: "Disegna come schizzo per Claude: sì / no", g: "Claude" },
  { n: "CHIEDI", a: ["CH"], d: "Seleziona un'area e chiedi a Claude", g: "Claude" },
  { n: "NOTA", a: ["NO"], d: "Nota su un punto per Claude", g: "Claude" },
  { n: "CHAT", a: [], d: "Apri la chat con Claude", g: "Claude" },
  { n: "DISTANZA", a: ["DI", "DIST", "MISURA"], d: "Distanza fra due punti", g: "Strumenti" },
  { n: "AREA", a: ["AA"], d: "Area e perimetro (Invio per finire)", g: "Strumenti" },
  { n: "ID", a: ["COORD"], d: "Coordinate di un punto", g: "Strumenti" },
  { n: "FILO", a: ["FI"], d: "Segui un filo", g: "Strumenti" },
  { n: "TROVA", a: ["FIND"], d: "Trova un testo (Ctrl+F)", g: "Strumenti" },
  { n: "ZOOM", a: ["Z", "ZE"], d: "Zoom estensioni", g: "Vista" },
  { n: "ZOOMF", a: ["ZF", "ZW"], d: "Zoom finestra", g: "Vista" },
  { n: "PAN", a: ["P"], d: "Panoramica", g: "Vista" },
  { n: "ORTO", a: ["ORTHO"], d: "Ortogonale sì / no (F8; Maiusc lo inverte)", g: "Vista" },
  { n: "OSNAP", a: ["OS"], d: "Aggancio sì / no (F3)", g: "Vista" },
  { n: "GRIGLIA", a: ["GRID"], d: "Griglia sì / no (F7)", g: "Vista" },
  { n: "AIUTO", a: ["?", "HELP"], d: "Elenco dei comandi", g: "Vista" },
];
const CMD_DISEGNO = new Set(["LINEA", "POLILINEA", "RETTANGOLO", "CERCHIO", "ARCO", "TESTO", "QUOTA", "ETICHETTA", "MATITA", "NUVOLA", "AREA", "ID"]);
const CMD_SELEZIONE = new Set(["SPOSTA", "COPIA", "CANCELLA"]);
const PROMPT = {
  LINEA: (c) => c.punti.length ? `Punto successivo o ${opz(c.punti.length > 2 ? ["Chiudi", "Annulla"] : ["Annulla"])}` : "Primo punto:",
  POLILINEA: (c) => c.punti.length ? `Punto successivo o ${opz(c.punti.length > 2 ? ["Chiudi", "Annulla", "Fine"] : ["Annulla", "Fine"])}` : "Punto iniziale:",
  RETTANGOLO: (c) => c.punti.length ? "Angolo opposto (o @dx,dy):" : "Primo angolo:",
  NUVOLA: (c) => c.punti.length ? "Angolo opposto della nuvola:" : "Primo angolo della nuvola:",
  CERCHIO: (c) => c.punti.length ? "Raggio (clic o numero):" : "Centro:",
  ARCO: (c) => ["Punto iniziale dell'arco:", "Secondo punto:", "Punto finale:"][c.punti.length] || "",
  TESTO: (c) => c.punti.length ? "Scrivi il testo e premi Invio:" : `Punto d'inserimento (altezza ${fmtN(D.h)}):`,
  QUOTA: (c) => ["Primo punto d'origine:", "Secondo punto d'origine:", "Posizione della linea di quota:"][c.punti.length] || "",
  ETICHETTA: (c) => ["Punta della freccia:", "Dove inizia la sottolineatura del testo:", "Scrivi il testo e premi Invio:"][c.punti.length] || "",
  SPOSTA: (c) => c.fase === "sel" ? "Seleziona gli oggetti e premi Invio:" : c.base ? "Secondo punto (o distanza):" : "Punto base:",
  COPIA: (c) => c.fase === "sel" ? "Seleziona gli oggetti e premi Invio:" : c.base ? `Secondo punto o ${opz(["Esci"])}` : "Punto base:",
  CANCELLA: () => "Seleziona gli oggetti e premi Invio:",
  MODTESTO: () => "Clic sul testo da modificare:",
  MATITA: () => `Trascina per disegnare a mano libera; Invio per finire ${opz(["Fine"])}`,
  AREA: (c) => c.punti.length < 3 ? "Vertici dell'area:" : `Vertice successivo; Invio per il risultato ${opz(["Fine"])}`,
  ID: () => "Punto:",
};
function opz(v) { return "[" + v.map((x) => `<button class="opzCmd" data-opz="${x[0]}">${x}</button>`).join("/") + "]"; }
function fmtN(v) { return String(Math.round(v * 100) / 100); }
function dist(a, b) { return Math.hypot(b[0] - a[0], b[1] - a[1]); }

/* ------------------------------------------------------------- utilita' */
function trovaComando(t) {
  const q = String(t || "").trim().toUpperCase().replace(/^_+/, "");
  return COMANDI.find((c) => c.n === q || c.a.includes(q)) || null;
}
function fDisegno() {
  if (D.cmd) return S.fogli.get(D.cmd.foglio) || null;
  const r = riquadroFuoco();
  return r && r.foglio ? S.fogli.get(r.foglio) : null;
}
function riqCmd() { return (D.cmd && S.riquadri.find((r) => r.foglio === D.cmd.foglio)) || riquadroFuoco(); }
function preparaBozza(f) { if (!f.bozza) { f.bozza = []; f.schizzo = []; f.rifai = []; } return f; }
function layerFoglio(f) {
  const m = (f && f.meta) || {};
  if ((m.tipo === "spac" || m.tipo === "layer") && m.layer !== undefined && m.layer !== null) return String(m.layer);
  return $("#selLayer").value || "0";
}
function coloreAci(aci, ly, f) {
  aci = +aci || 256;
  if (aci === 256) {
    if (f) for (const o of f.ogg.values()) if (o.ly === ly) return o.c || null;
    return null;
  }
  return ACI[aci] === undefined ? null : ACI[aci];
}
function larghezzaT(s, h) { return S.glifi ? larghezzaHershey(s || " ", h, 1) : String(s || " ").length * h * 0.75; }
function bboxP(o) {
  let b = null;
  const agg = (q) => { for (let i = 0; i < q.length; i += 2) b = unione(b, [q[i], q[i + 1], q[i], q[i + 1]]); };
  for (const q of o.p || []) agg(q);
  for (const q of o.f || []) agg(q);
  if (o.x) {
    const x = o.x, w = larghezzaT(x.s, x.h) * (x.wf || 1), dx = x.al === "C" ? -w / 2 : x.al === "R" ? -w : 0;
    const ca = Math.cos((x.r || 0) * Math.PI / 180), sa = Math.sin((x.r || 0) * Math.PI / 180);
    for (const [u, v] of [[dx, -0.35 * x.h], [dx + w, -0.35 * x.h], [dx + w, x.h], [dx, x.h]]) {
      const X = x.x + u * ca - v * sa, Y = x.y + u * sa + v * ca;
      b = unione(b, [X, Y, X, Y]);
    }
  }
  return b || [0, 0, 0, 0];
}
function grezzo(t, ly, c, extra = {}) { return { t, ly, c, lw: extra.lw || 0, d: null, p: [], f: [], ...extra }; }
function finito(o) { o.b = bboxP(o); return o; }
function cerchioQ(c, r, a0 = 0, a1 = 360) {
  let sw = a1 - a0;
  if (sw <= 0) sw += 360;
  const n = Math.max(16, Math.min(180, Math.ceil(sw / 4))), q = [];
  for (let i = 0; i <= n; i++) {
    const a = (a0 + sw * i / n) * Math.PI / 180;
    q.push(c[0] + r * Math.cos(a), c[1] + r * Math.sin(a));
  }
  return q;
}
function arco3(p1, p2, p3) {
  const ax = p1[0], ay = p1[1], bx = p2[0], by = p2[1], cx = p3[0], cy = p3[1];
  const dd = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(dd) < 1e-9) return null;
  const ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / dd;
  const uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / dd;
  const ang = (p) => (Math.atan2(p[1] - uy, p[0] - ux) * 180 / Math.PI + 360) % 360;
  const a1 = ang(p1), a2 = ang(p2), a3 = ang(p3);
  const sw = (a3 - a1 + 360) % 360, s2 = (a2 - a1 + 360) % 360;
  return { centro: [ux, uy], raggio: Math.hypot(ax - ux, ay - uy), a0: s2 <= sw ? a1 : a3, a1: s2 <= sw ? a3 : a1 };
}
function rettangoloP(a, b) { return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]; }
function nuvolaP(a, b) {
  const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
  const giro = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];         // antiorario: l'esterno e' a destra
  const per = 2 * (x1 - x0 + y1 - y0), passo = Math.max(1.5, Math.min(10, per / 28)), out = [];
  for (let k = 0; k < 4; k++) {
    const A = giro[k], B = giro[k + 1], L = dist(A, B);
    if (L < 1e-9) continue;
    const n = Math.max(1, Math.round(L / passo)), ux = (B[0] - A[0]) / L, uy = (B[1] - A[1]) / L;
    for (let i = 0; i < n; i++) {
      const P = [A[0] + ux * L * i / n, A[1] + uy * L * i / n], Q = [A[0] + ux * L * (i + 1) / n, A[1] + uy * L * (i + 1) / n];
      const M = [(P[0] + Q[0]) / 2, (P[1] + Q[1]) / 2], r = L / n / 2, a0 = Math.atan2(P[1] - M[1], P[0] - M[0]);
      for (let j = 0; j <= 8; j++) {                                        // mezza circonferenza verso l'esterno (orario)
        const a = a0 - Math.PI * j / 8;
        out.push([M[0] + r * Math.cos(a), M[1] + r * Math.sin(a)]);
      }
    }
  }
  return out;
}
function testoGrezzo(s, x, y, h, ly, c, al = "L", r = 0) {
  return finito(grezzo("TEXT", ly, c, { x: { s, x, y, h, r, wf: 1, ob: 0, al, va: 0, shx: true } }));
}
function quotaGrezza(p1, p2, pos, h, ly, c, testo) {
  const dx = p2[0] - p1[0], dy = p2[1] - p1[1], L = Math.hypot(dx, dy);
  if (L < 1e-9) return [];
  const u = [dx / L, dy / L], n = [-u[1], u[0]];
  const d = (pos[0] - p1[0]) * n[0] + (pos[1] - p1[1]) * n[1], sg = d >= 0 ? 1 : -1;
  const a = [p1[0] + n[0] * d, p1[1] + n[1] * d], b = [p2[0] + n[0] * d, p2[1] + n[1] * d];
  const exo = h * 0.3, exe = h * 0.5, asz = h * 0.8, out = [];
  for (const [P, Q] of [[p1, a], [p2, b]]) {
    if (Math.abs(d) > exo) out.push(finito(grezzo("LINE", ly, c, { p: [[P[0] + n[0] * sg * exo, P[1] + n[1] * sg * exo, Q[0] + n[0] * sg * exe, Q[1] + n[1] * sg * exe]] })));
  }
  out.push(finito(grezzo("LINE", ly, c, { p: [[a[0], a[1], b[0], b[1]]] })));
  for (const [T, v] of [[a, u], [b, [-u[0], -u[1]]]]) {
    const B = [T[0] + v[0] * asz, T[1] + v[1] * asz], w = asz / 6;
    out.push(finito(grezzo("SOLID", ly, c, { f: [[T[0], T[1], B[0] + n[0] * w, B[1] + n[1] * w, B[0] - n[0] * w, B[1] - n[1] * w]] })));
  }
  const t = u[0] > 1e-9 || (Math.abs(u[0]) <= 1e-9 && u[1] > 0) ? u : [-u[0], -u[1]];
  const su = [-t[1], t[0]], M = [(a[0] + b[0]) / 2 + su[0] * h * 0.3, (a[1] + b[1]) / 2 + su[1] * h * 0.3];
  out.push(testoGrezzo(testo || fmtN(Math.round(L * 10) / 10), M[0], M[1], h, ly, c, "C", Math.atan2(t[1], t[0]) * 180 / Math.PI));
  return out;
}
function etichettaGrezza(punta, x, y, testo, h, ly, c) {
  const w = larghezzaT(testo, h), sotto = Math.max(0.6, 0.3 * h), yu = y - sotto;
  const u0 = [x - 0.4, yu], u1 = [x + w + 0.4, yu];
  const out = [testoGrezzo(testo, x, y, h, ly, c), finito(grezzo("LINE", ly, c, { p: [[u0[0], u0[1], u1[0], u1[1]]] }))];
  if (punta) {
    const da = dist(u0, punta) <= dist(u1, punta) ? u0 : u1, L = dist(da, punta);
    out.push(finito(grezzo("LINE", ly, c, { p: [[da[0], da[1], punta[0], punta[1]]] })));
    const lf = Math.min(1.8, 0.45 * L);
    if (lf > 0 && L > 1e-6) {
      const vx = (punta[0] - da[0]) / L, vy = (punta[1] - da[1]) / L, bx = punta[0] - vx * lf, by = punta[1] - vy * lf, hw = lf * 0.28;
      out.push(finito(grezzo("SOLID", ly, c, { f: [[punta[0], punta[1], bx - vy * hw, by + vx * hw, bx + vy * hw, by - vx * hw]] })));
    }
  }
  return out;
}
function xEtichetta(punta, pos, testo, h) {              // pos = estremo della sottolineatura vicino alla freccia
  const w = larghezzaT(testo, h);
  return [pos[0] >= punta[0] ? pos[0] + 0.4 : pos[0] - 0.4 - w, pos[1] + Math.max(0.6, 0.3 * h)];
}
/* operazione "add" -> oggetti grezzi, nel formato del server */
function oggettiDi(op, f, schizzo) {
  const ly = schizzo ? "SCHIZZO" : (op.layer || layerFoglio(f));
  const c = schizzo ? COL_SCHIZZO : coloreAci(op.colore, ly, f);
  const lw = schizzo ? 0.5 : 0;
  const L = (q, t = "LWPOLYLINE") => finito(grezzo(t, ly, c, { p: [q], lw }));
  switch (op.tipo) {
    case "linea": return [L([...op.p0, ...op.p1], "LINE")];
    case "polilinea": case "rettangolo": case "nuvola": case "matita": {
      const q = op.punti.flat();
      if (op.chiusa || op.tipo === "rettangolo") q.push(op.punti[0][0], op.punti[0][1]);
      return [L(q)];
    }
    case "cerchio": return [L(cerchioQ(op.centro, op.raggio), "CIRCLE")];
    case "arco": return [L(cerchioQ(op.centro, op.raggio, op.a0, op.a1), "ARC")];
    case "testo": return [testoGrezzo(op.testo, op.x, op.y, op.altezza, ly, c, op.allineamento || "L", op.rotazione || 0)];
    case "etichetta": return etichettaGrezza(op.punta, op.x, op.y, op.testo, op.altezza, ly, c);
    case "quota": return quotaGrezza(op.p1, op.p2, op.pos, op.altezza, ly, c, op.testo);
  }
  return [];
}
function trasla(o, dx, dy) {
  const sp = (q) => q.map((v, i) => v + (i % 2 ? dy : dx));
  const n = { ...o, p: o.p.map(sp), f: (o.f || []).map(sp), b: [o.b[0] + dx, o.b[1] + dy, o.b[2] + dx, o.b[3] + dy] };
  if (o.x) n.x = { ...o.x, x: o.x.x + dx, y: o.x.y + dy };
  if (o.img) n.img = { ...o.img, q: sp(o.img.q) };
  return n;
}
function cambiaTesto(og, op) {
  let fatto = false;
  return og.map((o) => {
    if (fatto || !o.x || (op.tag ? o.tag !== op.tag : false)) return o;
    fatto = true;
    const n = { ...o, x: { ...o.x, s: op.testo } };
    n.b = bboxP(n);
    return n;
  });
}

/* ------------------------------------------------------------- bozza */
function ricalcolaBozza(f) {
  preparaBozza(f);
  const via = new Set(), mappa = new Map(), perHd = new Map();
  if (f.bozza.some((o) => o.hd)) for (const o of f.ogg.values()) if (o.hd) { if (!perHd.has(o.hd)) perHd.set(o.hd, []); perHd.get(o.hd).push(o); }
  const deriva = (ops, schizzo) => {
    const cur = new Map();
    const prendi = (op, modifica) => {
      const k = op.rif ? "r:" + op.rif : "h:" + op.hd;
      if (cur.has(k)) return [k, cur.get(k)];
      if (op.rif) return [k, []];
      const orig = perHd.get(op.hd) || [];
      if (!modifica) return [k, orig];
      for (const o of orig) via.add(o.id);
      const cl = orig.map((o) => ({ ...o }));
      cur.set(k, cl);
      return [k, cl];
    };
    for (const op of ops) {
      if (op.k === "add") { cur.set("r:" + op.id, oggettiDi(op, f, schizzo)); continue; }
      if (op.k === "copy") { const [, og] = prendi(op, false); cur.set("r:" + op.id, og.map((o) => trasla(o, op.dx, op.dy))); continue; }
      const [k, og] = prendi(op, true);
      if (op.k === "del") cur.set(k, []);
      else if (op.k === "move") cur.set(k, og.map((o) => trasla(o, op.dx, op.dy)));
      else if (op.k === "text") cur.set(k, cambiaTesto(og, op));
      else if (op.k === "prop") cur.set(k, og.map((o) => {
        const ly = op.layer || o.ly;
        const n = { ...o, ly, c: op.colore ? coloreAci(op.colore, ly, f) : o.c };
        if (op.altezza && n.x) { n.x = { ...n.x, h: +op.altezza }; n.b = bboxP(n); }
        return n;
      }));
    }
    const out = [];
    for (const [k, og] of cur) {
      const rif = k.startsWith("r:") ? k.slice(2) : null;
      og.forEach((o, i) => {
        const id = rif ? `${rif}#${i}` : `${o.id}~`;
        const x = prepara({ ...o, id, rif: rif || undefined, hd: rif ? undefined : o.hd, schizzo: schizzo || undefined, anim: null, evid: 0 });
        out.push(x); mappa.set(id, x);
      });
    }
    return out;
  };
  const ogg = deriva(f.bozza, false), sk = deriva(f.schizzo, true);
  if (!f.bozza.length && !f.schizzo.length) { f.bz = null; f.sk = null; f.bzMappa = null; }
  else { f.bz = { via, ogg }; f.sk = sk; f.bzMappa = mappa; }
  if (S.selFoglio === f.nome && S.sel.size) {            // la selezione segue gli oggetti rifatti dalla bozza (id~)
    S.sel = new Set([...S.sel].map((id) => (via.has(id) && mappa.has(id + "~") ? id + "~" : id)).filter((id) => oggDi(f, id) && !via.has(id)));
  }
  aggiornaStatoBozza();
  richiediMiniatura(f.nome);
  salvaBozzaLocale();
}
/* le modifiche non salvate restano nel browser (per foglio e file) se la pagina si ricarica */
const CHIAVE_BOZZA = "etcadVistaBozza";
function salvaBozzaLocale() {
  try {
    const d = {};
    for (const f of S.fogli.values()) {
      if ((f.bozza && f.bozza.length) || (f.schizzo && f.schizzo.length)) d[f.nome] = { file: f.meta.file || "", bozza: f.bozza, schizzo: f.schizzo };
    }
    localStorage.setItem(CHIAVE_BOZZA, JSON.stringify(d));
  } catch (e) { /* memoria del browser non disponibile: la bozza resta solo in questa pagina */ }
}
function ripristinaBozza(f) {
  let x = null;
  try { x = (JSON.parse(localStorage.getItem(CHIAVE_BOZZA) || "{}") || {})[f.nome]; } catch (e) { return; }
  if (!x || (x.file || "") !== (f.meta.file || "")) return;
  f.bozza = x.bozza || []; f.schizzo = x.schizzo || []; f.rifai = [];
  for (const op of [...f.bozza, ...f.schizzo]) {
    D.gruppo = Math.max(D.gruppo, (op.g || 0) + 1);
    const n = +String(op.id || "").slice(1);
    if (n) D.seq = Math.max(D.seq, n + 1);
  }
  ricalcolaBozza(f);
  if (f.bozza.length) avviso(`Su <b>${esc(etichettaFoglio(f))}</b> ci sono ancora <b>${f.bozza.length}</b> modifiche non salvate.`, 6000);
}
function aggiungiOps(f, ops, descr) {
  preparaBozza(f);
  const g = D.gruppo++;
  for (const op of ops) {
    op.g = g;
    const id = op.k === "add" ? op.id : op.rif || "";
    (String(id).startsWith("s") ? f.schizzo : f.bozza).push(op);
  }
  f.rifai = [];
  ricalcolaBozza(f);
  if (descr) rigaComando(descr);
}
function nuovoId(schizzo) { return (schizzo ? "s" : "u") + D.seq++; }
function aggiungiOggetto(f, dati, sk) {
  const op = { k: "add", id: nuovoId(sk), ...dati };
  if (!sk) { op.layer = layerFoglio(f); if (D.colore !== 256) op.colore = D.colore; }
  aggiungiOps(f, [op]);
  return op;
}
function annulla() {
  const c = D.cmd;
  if (c && ["POLILINEA", "AREA"].includes(c.nome) && c.punti.length) { c.punti.pop(); aggiornaPrompt(); return; }
  const f = fDisegno();
  if (!f || !f.bozza) { rigaComando("Niente da annullare.", "nota"); return; }
  const g = Math.max(0, ...f.bozza.map((o) => o.g), ...f.schizzo.map((o) => o.g));
  if (!g) { rigaComando("Niente da annullare.", "nota"); return; }
  f.rifai.push({ g, bozza: f.bozza.filter((o) => o.g === g), schizzo: f.schizzo.filter((o) => o.g === g) });
  f.bozza = f.bozza.filter((o) => o.g !== g); f.schizzo = f.schizzo.filter((o) => o.g !== g);
  if (c && c.nome === "LINEA" && c.punti.length > 1) c.punti.pop();
  ricalcolaBozza(f); aggiornaPrompt();
  rigaComando("<b>_U</b>  annullata l'ultima modifica");
}
function ripeti() {
  const f = fDisegno();
  const x = f && f.rifai && f.rifai.pop();
  if (!x) { rigaComando("Niente da ripetere.", "nota"); return; }
  f.bozza.push(...x.bozza); f.schizzo.push(...x.schizzo);
  ricalcolaBozza(f);
  rigaComando("<b>_REDO</b>");
}
function docConBozza(d) { return fogliDoc(d).some((f) => f.bozza && f.bozza.length); }
function aggiornaStatoBozza() {
  const f = fDisegno();
  const n = f && f.bozza ? f.bozza.length : 0, ns = f && f.schizzo ? f.schizzo.length : 0;
  const tot = [...S.fogli.values()].reduce((a, x) => a + (x.bozza ? x.bozza.length : 0), 0);
  $("#badgeBozza").hidden = !n; $("#badgeBozza").textContent = n;
  const sb = $("#stBozza");
  sb.hidden = !tot;
  sb.textContent = tot === n ? `${n} ${n === 1 ? "modifica" : "modifiche"} da salvare` : `${n} qui · ${tot} in tutto da salvare`;
  $("#btnAnnulla").disabled = !(n || ns);
  $("#btnRipeti").disabled = !(f && f.rifai && f.rifai.length);
  $("#btnScarta").disabled = !n;
  $("#btnInviaSchizzo").disabled = !(f && f.sk && f.sk.length);
  $("#btnTogliSchizzo").disabled = !(f && f.sk && f.sk.length);
  for (const el of $$(".schedaDoc")) el.classList.toggle("nonSalvato", docConBozza(el.dataset.doc));
}
function scarta() {
  const f = fDisegno();
  if (!f || !f.bozza || !f.bozza.length) return;
  modale(`<h3>Scartare le modifiche?</h3><p>Le <b>${f.bozza.length}</b> modifiche non salvate di <b>${esc(etichettaFoglio(f))}</b> si perdono. Il DWG non cambia.</p>`,
    [{ t: "Annulla" }, { t: "Scarta", pericolo: true, fn: () => { f.bozza = []; f.rifai = []; ricalcolaBozza(f); rigaComando("Modifiche scartate."); } }]);
}

/* ------------------------------------------------------------- salvataggio */
function salva() {
  const f = fDisegno();
  if (!f || !f.bozza || !f.bozza.length) { avviso("Non ci sono modifiche da salvare su questo foglio."); return; }
  const file = f.meta && f.meta.file;
  if (!file) { avviso("Questo foglio non viene da un file DWG (è solo nella vista): non c'è niente da salvare."); return; }
  const rete = /^\\\\|^[Zz]:/.test(file);
  modale(`<h3>Salva le modifiche nel DWG</h3>
    <p><b>${f.bozza.length}</b> ${f.bozza.length === 1 ? "modifica" : "modifiche"} su <b>${esc(etichettaFoglio(f))}</b></p>
    <div class="percorso">${esc(file)}</div>
    ${rete ? `<p class="attenzione">Il file è in una cartella condivisa: lo vedono anche gli altri.</p>` : ""}
    <p class="piccolo"><b>Salva una copia</b> scrive <code>${esc(nomeFile(file).replace(/(\.\w+)$/, "_modificato$1"))}</code> accanto all'originale e la apre in una scheda.
    <b>Sovrascrivi</b> cambia l'originale, dopo averne fatto una copia di sicurezza in <code>%LOCALAPPDATA%\\etcad\\backup</code>.</p>`,
  [{ t: "Annulla" }, { t: "Sovrascrivi l'originale", fn: () => inviaSalvataggio(f, false) }, { t: "Salva una copia", primario: true, fn: () => inviaSalvataggio(f, true) }]);
}
function nomeFile(p) { return String(p || "").split(/[\\/]/).pop(); }
async function inviaSalvataggio(f, copia) {
  const ops = f.bozza.map(({ g, ...o }) => o);
  const n = ops.length;
  $("#btnSalva").disabled = true;
  avviso(`Salvataggio di ${n} modifiche in corso…`, 3000);
  try {
    const r = await posta("salva_bozza", { foglio: f.nome, ops, copia });
    f.bozza = []; f.rifai = []; ricalcolaBozza(f);
    avviso(`Salvato in <b>${esc(nomeFile(r.file))}</b>${r.backup ? `<br><span class="piccolo">Copia dell'originale: ${esc(r.backup)}</span>` : ""}${copia ? "<br>Aperto in una scheda nuova." : ""}`, 9000);
  } catch (e) {
    avviso(`Salvataggio non riuscito: ${esc(e.message)}`, 10000);
  } finally { $("#btnSalva").disabled = false; }
}

/* ------------------------------------------------------------- comandi */
function aggiornaPrompt() {
  const el = $("#promptCmd"), c = D.cmd;
  el.hidden = !c;
  if (c) el.innerHTML = `<b>${c.nome}</b> ${(PROMPT[c.nome] || (() => ""))(c)}`;
  $$("[data-comando]").forEach((b) => b.classList.toggle("inCorso", !!c && b.dataset.comando === c.nome));
  $("#cmdInput").placeholder = c ? "" : "Digitare un comando (? per l'elenco)";
  if (c) $("#modoTesto").textContent = c.sk ? `${c.nome} · SCHIZZO` : c.nome;
  else if (S.modo === "sel") $("#modoTesto").textContent = "";
}
function eseguiComando(t) {
  const c = trovaComando(t);
  chiudiMenuContesto(); nascondiSugg();
  if (!c) { rigaComando(`Comando sconosciuto «${esc(t)}». Digita <b>?</b> per l'elenco.`, "err"); return; }
  if (!["ANNULLA", "RIPETI", "AIUTO", "ORTO", "OSNAP", "GRIGLIA"].includes(c.n)) D.ultimo = c.n;
  const r = riquadroFuoco();
  switch (c.n) {
    case "ANNULLA": annulla(); return;
    case "RIPETI": ripeti(); return;
    case "SALVA": salva(); return;
    case "AIUTO": aiuto(); return;
    case "ORTO": S.orto = !S.orto; interruttori(); salvaImp(); rigaComando(`<b>ORTO</b> ${S.orto ? "attivo" : "disattivo"}`); return;
    case "OSNAP": S.snap = !S.snap; interruttori(); salvaImp(); rigaComando(`<b>OSNAP</b> ${S.snap ? "attivo" : "disattivo"}`); return;
    case "GRIGLIA": S.griglia = !S.griglia; interruttori(); return;
    case "SCHIZZO": D.schizzo = !D.schizzo; $("#btnSchizzo").classList.toggle("acceso", D.schizzo);
      rigaComando(D.schizzo ? "Quello che disegni ora è uno <b>schizzo per Claude</b> (rosso, non va nel DWG)." : "Si disegna di nuovo <b>nel DWG</b>."); return;
    case "CHIEDI": annullaComando(true); impostaModo("area"); return;
    case "NOTA": annullaComando(true); impostaModo("punto"); return;
    case "DISTANZA": annullaComando(true); impostaModo("misura"); return;
    case "FILO": annullaComando(true); impostaModo("filo"); return;
    case "PAN": annullaComando(true); impostaModo("pan"); return;
    case "ZOOMF": annullaComando(true); impostaModo("zoomf"); return;
    case "ZOOM": if (r) adattaR(r, true); return;
    case "TROVA": $$("#schedeRibbon button").find((b) => b.dataset.scheda === "strumenti").click(); $("#trovaTesto").focus(); return;
    case "CHAT": apriTab("chat"); return;
    case "PROPRIETA": apriTab("proprieta"); return;
    case "SELEZIONA": selezionaTutto(); return;
  }
  if (!r || !r.foglio) { avviso("Apri prima un foglio."); return; }
  avvia(c.n, r);
}
function avvia(nome, r) {
  annullaComando(true);
  const sk = D.schizzo || nome === "MATITA" || nome === "NUVOLA";
  D.cmd = { nome, foglio: r.foglio, punti: [], dati: {}, sk, fase: "punto" };
  rigaComando(`<b>_${nome}</b>${sk ? "  (schizzo per Claude)" : ""}`);
  if (CMD_SELEZIONE.has(nome)) {
    const haSel = S.selFoglio === r.foglio && S.sel.size;
    if (nome === "CANCELLA" && haSel) { cancellaSelezione(); D.cmd = null; aggiornaPrompt(); return; }
    if (!haSel) { D.cmd.fase = "sel"; impostaModo("sel"); aggiornaPrompt(); return; }
  }
  if (nome === "MODTESTO") {
    const f = S.fogli.get(r.foglio), sel = S.selFoglio === r.foglio ? [...S.sel].map((id) => oggDi(f, id)).filter((o) => o && o.x) : [];
    if (sel.length === 1) { D.cmd = null; aggiornaPrompt(); modificaTesto(r, sel[0]); return; }
    D.cmd.fase = "sceglitesto";
  }
  impostaModo("disegna");
  aggiornaPrompt();
}
function fine() {
  chiudiInputTesto();
  D.cmd = null;
  if (S.modo === "disegna") impostaModo("sel");
  aggiornaPrompt();
}
function annullaComando(silenzioso = false) {
  if (!D.cmd) return;
  const n = D.cmd.nome;
  fine();
  if (!silenzioso) rigaComando(`<b>${n}</b> *Annulla*`, "nota");
}
function fermaComandoPerModo(m) {
  if (D.cmd && !(m === "sel" && D.cmd.fase === "sel")) annullaComando(true);
}
function vuolePunto() { return !!D.cmd && !["sel", "sceglitesto"].includes(D.cmd.fase) && !D.input; }
function ultimoPunto() {
  const c = D.cmd;
  if (!c) return null;
  if (c.nome === "SPOSTA" || c.nome === "COPIA") return c.base || null;
  if (c.nome === "RETTANGOLO" || c.nome === "NUVOLA" || c.nome === "MATITA") return null;
  return c.punti.length ? c.punti[c.punti.length - 1] : null;
}
function puntiComando(r) { return D.cmd && r && D.cmd.foglio === r.foglio ? D.cmd.punti : []; }
function puntoCursore(r) {
  if (!S.mouse || S.mouseRiq !== r) return D.cursore || [0, 0];
  if (S.snapPt) return [S.snapPt[0], S.snapPt[1]];
  let p = mondo(r, ...S.mouse);
  const L = ultimoPunto();
  if (L && D.cmd && D.cmd.nome !== "CERCHIO" && (!!S.orto !== !!S.shift)) {
    p = Math.abs(p[0] - L[0]) >= Math.abs(p[1] - L[1]) ? [p[0], L[1]] : [L[0], p[1]];
  }
  D.cursore = p;
  return p;
}
function punto(p) {
  const c = D.cmd;
  if (!c) return;
  const f = S.fogli.get(c.foglio);
  if (!f) { annullaComando(true); return; }
  const sk = c.sk, h = D.h;
  switch (c.nome) {
    case "LINEA":
      if (c.punti.length) { const a = c.punti[c.punti.length - 1]; if (dist(a, p) > 1e-9) aggiungiOggetto(f, { tipo: "linea", p0: a, p1: p }, sk); }
      c.punti.push(p); break;
    case "POLILINEA": case "AREA": c.punti.push(p); break;
    case "RETTANGOLO": case "NUVOLA":
      if (!c.punti.length) { c.punti.push(p); break; }
      if (dist(c.punti[0], p) < 1e-6) break;
      aggiungiOggetto(f, c.nome === "NUVOLA" ? { tipo: "nuvola", punti: nuvolaP(c.punti[0], p), chiusa: true } : { tipo: "rettangolo", punti: rettangoloP(c.punti[0], p) }, sk);
      fine(); return;
    case "CERCHIO":
      if (!c.punti.length) { c.punti.push(p); break; }
      if (dist(c.punti[0], p) > 1e-6) aggiungiOggetto(f, { tipo: "cerchio", centro: c.punti[0], raggio: dist(c.punti[0], p) }, sk);
      fine(); return;
    case "ARCO": {
      c.punti.push(p);
      if (c.punti.length < 3) break;
      const a = arco3(...c.punti);
      if (!a) { rigaComando("I tre punti sono allineati: nessun arco.", "err"); c.punti.pop(); break; }
      aggiungiOggetto(f, { tipo: "arco", ...a }, sk);
      fine(); return;
    }
    case "TESTO":
      c.punti.push(p);
      apriInputTesto(riqCmd(), p, "", h, (t) => { if (t) aggiungiOggetto(f, { tipo: "testo", testo: t, x: p[0], y: p[1], altezza: h, stile: "ROMANS" }, sk); fine(); });
      break;
    case "QUOTA":
      c.punti.push(p);
      if (c.punti.length < 3) break;
      aggiungiOggetto(f, { tipo: "quota", p1: c.punti[0], p2: c.punti[1], pos: p, altezza: h }, sk);
      fine(); return;
    case "ETICHETTA": {
      c.punti.push(p);
      if (c.punti.length < 2) break;
      const [punta, pos] = c.punti;
      apriInputTesto(riqCmd(), pos, "", h, (t) => {
        if (t) { const [x, y] = xEtichetta(punta, pos, t, h); aggiungiOggetto(f, { tipo: "etichetta", testo: t, x, y, punta, altezza: h, stile: "ROMANS" }, sk); }
        fine();
      });
      break;
    }
    case "SPOSTA": case "COPIA": {
      if (!c.base) { c.base = p; break; }
      const dx = p[0] - c.base[0], dy = p[1] - c.base[1];
      const { out } = bersagli(f);
      if (!out.length) { fine(); return; }
      if (c.nome === "SPOSTA") {
        aggiungiOps(f, out.map((t) => ({ k: "move", ...t, dx, dy })), `<b>_MOVE</b>  ${out.length} oggetti, Δx ${fmt(dx)} Δy ${fmt(dy)}`);
        S.sel.clear(); aggiornaSelezione(); fine(); return;
      }
      aggiungiOps(f, out.map((t) => ({ k: "copy", ...t, dx, dy, id: nuovoId(String(t.rif || "").startsWith("s")) })), `<b>_COPY</b>  ${out.length} oggetti, Δx ${fmt(dx)} Δy ${fmt(dy)}`);
      break;                                                       // come AutoCAD: si continua a copiare finche' Invio
    }
    case "ID":
      rigaComando(`<b>_ID</b>  X = ${fmt(p[0])}   Y = ${fmt(p[1])}`);
      avviso(`Punto: X = <b>${fmt(p[0])}</b>  Y = <b>${fmt(p[1])}</b>`);
      fine(); return;
  }
  aggiornaPrompt();
}
function invio() {
  const c = D.cmd;
  if (!c) return;
  const f = S.fogli.get(c.foglio);
  if (c.fase === "sel") {
    if (!(S.selFoglio === c.foglio && S.sel.size)) { rigaComando("Nessun oggetto selezionato.", "nota"); return; }
    if (c.nome === "CANCELLA") { cancellaSelezione(); fine(); return; }
    c.fase = "punto"; impostaModo("disegna"); aggiornaPrompt(); return;
  }
  if (c.nome === "POLILINEA" && c.punti.length >= 2) aggiungiOggetto(f, { tipo: "polilinea", punti: c.punti.slice() }, c.sk);
  if (c.nome === "AREA") risultatoArea(c.punti);
  fine();
}
function opzione(t) {
  const c = D.cmd, f = S.fogli.get(c.foglio);
  const o = t.trim().toUpperCase();
  if ((o === "C" || o === "CHIUDI") && c.punti.length > 2) {
    if (c.nome === "LINEA") { aggiungiOggetto(f, { tipo: "linea", p0: c.punti[c.punti.length - 1], p1: c.punti[0] }, c.sk); fine(); return; }
    if (c.nome === "POLILINEA") { aggiungiOggetto(f, { tipo: "polilinea", punti: c.punti.slice(), chiusa: true }, c.sk); fine(); return; }
  }
  if (o === "U" || o === "ANNULLA") { annulla(); return; }
  if (o === "F" || o === "FINE" || o === "E" || o === "ESCI") { invio(); return; }
  if (trovaComando(o)) { eseguiComando(o); return; }
  rigaComando(`Punto o opzione non valida: «${esc(t)}»`, "err");
}
function numero(v) {
  const c = D.cmd;
  if (c.nome === "CERCHIO" && c.punti.length === 1) {
    if (v > 0) aggiungiOggetto(S.fogli.get(c.foglio), { tipo: "cerchio", centro: c.punti[0], raggio: v }, c.sk);
    fine(); return;
  }
  const L = ultimoPunto();
  if (!L) { rigaComando("Serve un punto: x,y oppure un clic.", "err"); return; }
  const r = riqCmd(), q = puntoCursore(r);
  let dx = q[0] - L[0], dy = q[1] - L[1];
  const n = Math.hypot(dx, dy);
  if (n < 1e-9) { dx = 1; dy = 0; } else { dx /= n; dy /= n; }
  punto([L[0] + dx * v, L[1] + dy * v]);
}
function leggiPunto(t, L) {
  let s = t.replace(/\s+/g, "");
  const rel = s.startsWith("@");
  if (rel) s = s.slice(1);
  const base = rel ? (L || [0, 0]) : [0, 0];
  let m = s.match(/^(-?\d*\.?\d+)<(-?\d*\.?\d+)$/);
  if (m) { const d = +m[1], a = +m[2] * Math.PI / 180; return [base[0] + d * Math.cos(a), base[1] + d * Math.sin(a)]; }
  m = s.match(/^(-?\d*\.?\d+)[,;](-?\d*\.?\d+)$/);
  if (m) return [base[0] + +m[1], base[1] + +m[2]];
  return null;
}
function interpreta(testo) {
  const t = String(testo || "").trim();
  if (!D.cmd) {
    if (!t) { if (D.ultimo) eseguiComando(D.ultimo); return; }
    eseguiComando(t); return;
  }
  if (!t) { invio(); return; }
  if (D.cmd.fase === "sel" || D.cmd.fase === "sceglitesto") { opzione(t); return; }
  if (D.cmd.nome === "CERCHIO" && D.cmd.punti.length === 1 && /^\d+,\d+$/.test(t)) { numero(+t.replace(",", ".")); return; }
  const p = leggiPunto(t, ultimoPunto());
  if (p) { punto(p); return; }
  if (/^-?\d*\.?\d+$/.test(t)) { numero(+t); return; }
  opzione(t);
}
function risultatoArea(pp) {
  if (pp.length < 3) { rigaComando("Servono almeno tre vertici.", "nota"); return; }
  let A = 0, P = 0;
  for (let i = 0; i < pp.length; i++) {
    const a = pp[i], b = pp[(i + 1) % pp.length];
    A += a[0] * b[1] - b[0] * a[1]; P += dist(a, b);
  }
  A = Math.abs(A) / 2;
  rigaComando(`<b>_AREA</b>  Area = ${fmt(A)} mm² (${fmt(A / 100)} cm²)   Perimetro = ${fmt(P)} mm`);
  avviso(`Area <b>${fmt(A)}</b> mm² · perimetro <b>${fmt(P)}</b> mm`, 7000);
}

/* ------------------------------------------------------------- selezione e modifiche */
function bersagli(f) {
  const visti_ = new Set(), out = [], blocchi = new Set();
  let nonMod = 0;
  for (const id of S.sel) {
    const o = oggDi(f, id);
    if (!o) continue;
    const k = o.rif ? "r:" + o.rif : o.hd ? "h:" + o.hd : null;
    if (!k) { nonMod++; continue; }
    if (o.blk && !o.rif) blocchi.add(o.blk);
    if (visti_.has(k)) continue;
    visti_.add(k);
    out.push(o.rif ? { rif: o.rif } : { hd: o.hd });
  }
  if (nonMod) avviso(`${nonMod} oggetti non sono modificabili: il foglio non viene da un DWG.`);
  return { out, blocchi };
}
function cancellaSelezione() {
  const f = S.fogli.get(S.selFoglio);
  if (!f || !S.sel.size) { avviso("Seleziona prima gli oggetti da cancellare."); return; }
  const { out, blocchi } = bersagli(f);
  if (!out.length) return;
  const fai = () => { aggiungiOps(f, out.map((t) => ({ k: "del", ...t })), `<b>_ERASE</b>  ${out.length} oggetti`); S.sel.clear(); aggiornaSelezione(); };
  if (blocchi.size) {
    modale(`<h3>Cancellare anche i blocchi?</h3><p>La selezione comprende parti di ${blocchi.size === 1 ? "un blocco" : "blocchi"}
      (<b>${[...blocchi].slice(0, 4).map(esc).join(", ")}</b>). Cancellandoli sparisce <b>tutto il blocco</b>, come in SPAC.</p>`,
    [{ t: "Annulla" }, { t: "Cancella tutto", pericolo: true, fn: fai }]);
  } else fai();
}
function selezionaTutto() {
  const r = riquadroFuoco();
  const f = r && S.fogli.get(r.foglio);
  if (!f) return;
  S.sel = new Set([...visti(f)].map((o) => o.id)); S.selFoglio = f.nome;
  aggiornaSelezione();
  rigaComando(`${S.sel.size} oggetti selezionati.`);
}
function modificaTesto(r, o) {
  const f = S.fogli.get(r.foglio);
  if (!o.hd && !o.rif) { avviso("Questo testo non è modificabile: il foglio non viene da un DWG."); return; }
  if (o.blk && !o.tag && !o.rif) { avviso(`Il testo fa parte del blocco <b>${esc(o.blk)}</b>: non si modifica da qui.`); return; }
  apriInputTesto(r, [o.x.x, o.x.y], o.x.s, o.x.h, (t) => {
    if (t && t !== o.x.s) aggiungiOps(f, [{ k: "text", ...(o.rif ? { rif: o.rif } : { hd: o.hd }), tag: o.tag || undefined, testo: t }], `<b>_TEXTEDIT</b>  «${esc(t)}»`);
  });
}
function doppioClicDisegno(r, px, py) {
  if (D.cmd) return true;
  const o = trovaOggetto(r, px, py);
  if (o && o.x) { modificaTesto(r, o); return true; }
  return false;
}

/* ------------------------------------------------------------- mouse */
function premiDisegno(r, p, e) {
  const c = D.cmd;
  if (c && c.nome === "MATITA" && e.button === 0) c.tratto = [p];
}
function trascinaDisegno(r, p) {
  const c = D.cmd;
  if (!c || c.nome !== "MATITA" || !c.tratto) return;
  const L = c.tratto[c.tratto.length - 1];
  if (dist(L, p) * r.vista.s > 2) c.tratto.push(p);
}
function cursoreDa(r, e) {           // il punto del clic, anche se prima non e' arrivato un mousemove
  const b = r.tela.getBoundingClientRect();
  S.mouse = [e.clientX - b.left, e.clientY - b.top]; S.mouseRiq = r; S.shift = e.shiftKey;
  S.snapPt = vuolePunto() ? puntoAgganciato(r, ...S.mouse) : null;
}
function rilasciaDisegno(r, p, e, d) {
  const c = D.cmd;
  cursoreDa(r, e);
  if (!c) { impostaModo("sel"); return; }
  if (c.foglio !== r.foglio) { annullaComando(true); return; }
  if (c.nome === "MATITA") {
    const t = c.tratto; c.tratto = null;
    if (t && t.length > 1) aggiungiOggetto(S.fogli.get(c.foglio), { tipo: "matita", punti: t }, true);
    return;
  }
  if (d.mosso) return;
  if (c.fase === "sceglitesto") {
    const b = r.tela.getBoundingClientRect();
    const o = trovaOggetto(r, e.clientX - b.left, e.clientY - b.top);
    if (o && o.x) { fine(); modificaTesto(r, o); } else rigaComando("Lì non c'è un testo.", "nota");
    return;
  }
  if (D.input) { confermaInputTesto(true); return; }
  punto(puntoCursore(r));
}
function tastoDestro(r, px, py, e) {
  if (D.cmd) { invio(); return; }
  const f = S.fogli.get(r.foglio);
  const sel = f && S.selFoglio === r.foglio ? S.sel.size : 0;
  const ogg = sel === 1 ? oggDi(f, [...S.sel][0]) : null;
  const voci = [];
  if (D.ultimo) voci.push({ t: `Ripeti ${D.ultimo}`, k: "Invio", fn: () => eseguiComando(D.ultimo) });
  if (sel) {
    voci.push(null, { t: "Sposta", k: "M", fn: () => eseguiComando("SPOSTA") }, { t: "Copia", k: "CO", fn: () => eseguiComando("COPIA") },
      { t: "Cancella", k: "Canc", fn: () => eseguiComando("CANCELLA") });
    if (ogg && ogg.x) voci.push({ t: "Modifica testo", k: "ED", fn: () => modificaTesto(r, ogg) });
    voci.push({ t: "Proprietà", k: "PR", fn: () => apriTab("proprieta") }, { t: "Chiedi a Claude…", fn: chiediSuSelezione, claude: true },
      null, { t: "Deseleziona", k: "Esc", fn: () => { S.sel.clear(); aggiornaSelezione(); } });
  } else {
    voci.push(null, { t: "Linea", k: "L", fn: () => eseguiComando("LINEA") }, { t: "Testo", k: "T", fn: () => eseguiComando("TESTO") },
      { t: "Etichetta", k: "ET", fn: () => eseguiComando("ETICHETTA") }, { t: "Quota", k: "DIM", fn: () => eseguiComando("QUOTA") },
      null, { t: "Seleziona area per Claude", k: "CH", fn: () => eseguiComando("CHIEDI"), claude: true }, { t: "Matita (schizzo)", k: "MAT", fn: () => eseguiComando("MATITA"), claude: true },
      null, { t: "Zoom estensioni", k: "Z", fn: () => adattaR(r, true) });
  }
  if (f && f.bozza && f.bozza.length) voci.push(null, { t: "Annulla", k: "Ctrl+Z", fn: annulla }, { t: `Salva DWG (${f.bozza.length})`, k: "Ctrl+S", fn: salva });
  apriMenuContesto(r, px, py, voci);
}
function apriMenuContesto(r, px, py, voci) {
  const m = $("#menuContesto");
  while (voci.length && voci[0] === null) voci.shift();
  m.innerHTML = voci.map((v, i) => v === null ? `<div class="sepMenu"></div>`
    : `<button data-i="${i}" class="${v.claude ? "claude" : ""}"><span>${esc(v.t)}</span>${v.k ? `<kbd>${esc(v.k)}</kbd>` : ""}</button>`).join("");
  m.hidden = false;
  const vp = $("#viewport").getBoundingClientRect(), rb = r.el.getBoundingClientRect();
  let x = rb.left - vp.left + px + 4, y = rb.top - vp.top + py + 4;
  if (x + m.offsetWidth > vp.width - 8) x -= m.offsetWidth + 8;
  if (y + m.offsetHeight > vp.height - 8) y = Math.max(8, vp.height - m.offsetHeight - 8);
  m.style.left = `${x}px`; m.style.top = `${y}px`;
  m.querySelectorAll("button").forEach((b) => { b.onclick = () => { chiudiMenuContesto(); voci[+b.dataset.i].fn(); }; });
}
function chiudiMenuContesto() { const m = $("#menuContesto"); if (m) m.hidden = true; }

/* ------------------------------------------------------------- campo di testo sul disegno */
function apriInputTesto(r, p, valore, h, conferma) {
  const inp = $("#inputTesto");
  const vp = $("#viewport").getBoundingClientRect(), rb = r.el.getBoundingClientRect();
  const [X, Y] = schermo(r, p[0], p[1]);
  inp.style.left = `${rb.left - vp.left + X}px`;
  inp.style.top = `${rb.top - vp.top + Y + 8}px`;
  inp.value = valore || "";
  inp.hidden = false;
  D.input = { conferma, p, h, r };
  if (D.cmd) D.cmd.dati.testo = inp.value;
  inp.focus(); inp.select();
}
function chiudiInputTesto() { const inp = $("#inputTesto"); inp.hidden = true; D.input = null; }
function confermaInputTesto(ok) {
  const x = D.input;
  if (!x) return;
  const v = $("#inputTesto").value.replace(/\s+$/, "");
  chiudiInputTesto();
  if (ok) x.conferma(v); else if (D.cmd) fine();
}

/* ------------------------------------------------------------- anteprima sul canvas */
function anteprima(c, p, f) {
  const ly = c.sk ? "SCHIZZO" : layerFoglio(f);
  const col = c.sk ? COL_SCHIZZO : coloreAci(D.colore, ly, f);
  const lw = c.sk ? 0.5 : 0;
  const L = (q, t = "LWPOLYLINE") => grezzo(t, ly, col, { p: [q], lw });
  const P = c.punti, h = D.h;
  switch (c.nome) {
    case "LINEA": return P.length ? [L([...P[P.length - 1], ...p])] : [];
    case "POLILINEA": return P.length ? [L([...P.flat(), ...p])] : [];
    case "AREA": return P.length ? [L([...P.flat(), ...p, ...P[0]])] : [];
    case "RETTANGOLO": return P.length ? [L([...rettangoloP(P[0], p).flat(), ...P[0]])] : [];
    case "NUVOLA": return P.length && dist(P[0], p) > 1e-6 ? [L(nuvolaP(P[0], p).flat())] : [];
    case "CERCHIO": return P.length ? [L(cerchioQ(P[0], Math.max(1e-6, dist(P[0], p)))), L([...P[0], ...p])] : [];
    case "ARCO": {
      if (P.length === 1) return [L([...P[0], ...p])];
      if (P.length === 2) { const a = arco3(P[0], P[1], p); return a ? [L(cerchioQ(a.centro, a.raggio, a.a0, a.a1))] : [L([...P[0], ...p])]; }
      return [];
    }
    case "QUOTA":
      if (P.length === 1) return [L([...P[0], ...p])];
      if (P.length === 2) return quotaGrezza(P[0], P[1], p, h, ly, col);
      return [];
    case "ETICHETTA": {
      if (!P.length) return [];
      const punta = P[0], pos = P.length > 1 ? P[1] : p, t = (c.dati.testo || "") || "Testo";
      const [x, y] = xEtichetta(punta, pos, t, h);
      return etichettaGrezza(punta, x, y, t, h, ly, col);
    }
    case "TESTO": return P.length && c.dati.testo ? [testoGrezzo(c.dati.testo, P[0][0], P[0][1], h, ly, col)] : [];
    case "MATITA": return c.tratto && c.tratto.length > 1 ? [L(c.tratto.flat())] : [];
  }
  return [];
}
function disegnaAnteprimaCmd(r, f, g, ora) {
  const c = D.cmd;
  if (!c || c.foglio !== r.foglio) return;
  const s = r.vista.s;
  const p = S.mouseRiq === r && S.mouse ? puntoCursore(r) : (D.input ? D.input.p : null);
  if ((c.nome === "SPOSTA" || c.nome === "COPIA") && c.base && p) {          // oggetti "fantasma" che seguono il cursore
    const dx = p[0] - c.base[0], dy = p[1] - c.base[1];
    g.save(); g.translate(dx, dy); g.globalAlpha = 0.75;
    g.strokeStyle = HL; g.fillStyle = HL; g.lineWidth = 1.5 / s;
    for (const id of S.sel) { const o = oggDi(f, id); if (!o) continue; if (o.linee.length) g.stroke(o.path); if (o.pieno) g.fill(o.pieno, "evenodd"); }
    g.restore();
    g.save(); g.strokeStyle = HL; g.lineWidth = 1 / s; g.setLineDash([4 / s, 3 / s]);
    g.beginPath(); g.moveTo(c.base[0], c.base[1]); g.lineTo(p[0], p[1]); g.stroke(); g.restore();
  }
  if (p || c.nome === "TESTO" || c.nome === "ETICHETTA") {
    for (const o of anteprima(c, p || [0, 0], f)) { o.id = "anteprima"; finito(o); disegnaOggetto(g, prepara(o), ora, s); }
  }
  // suggerimento vicino al cursore (input dinamico)
  if (S.mouseRiq !== r || !S.mouse || D.input || !vuolePunto()) return;
  const L = ultimoPunto();
  let t = (PROMPT[c.nome] || (() => ""))(c).replace(/<[^>]+>/g, "");
  if (L && p) {
    const a = (Math.atan2(p[1] - L[1], p[0] - L[0]) * 180 / Math.PI + 360) % 360;
    t = `${fmt(dist(L, p))}  < ${a.toFixed(0)}°`;
  } else if (c.nome === "CERCHIO" && c.punti.length && p) t = `R ${fmt(dist(c.punti[0], p))}`;
  g.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  pillola(g, S.mouse[0] + 18, S.mouse[1] + 14, t, c.sk ? COL_SCHIZZO : null, { font: `500 11px ${FONT_MONO}`, h: 20 });
}

/* ------------------------------------------------------------- tastiera e riga di comando */
function tastoDisegno(e, scrive) {
  const id = e.target && e.target.id;
  if (id === "cmdInput" || id === "inputTesto") return false;
  if (!$("#velo").hidden) {
    if (e.key === "Escape") { chiudiModale(); return true; }
    return false;
  }
  if (scrive) return false;
  const k = e.key;
  if (k === "F3" || k === "F7" || k === "F8") { e.preventDefault(); eseguiComando({ F3: "OSNAP", F7: "GRIGLIA", F8: "ORTO" }[k]); return true; }
  if ((e.ctrlKey || e.metaKey) && !e.altKey) {
    const c = k.toLowerCase();
    if (c === "z") { e.preventDefault(); annulla(); return true; }
    if (c === "y") { e.preventDefault(); ripeti(); return true; }
    if (c === "s") { e.preventDefault(); salva(); return true; }
    if (c === "a") { e.preventDefault(); selezionaTutto(); return true; }
    return false;
  }
  if (e.altKey) return false;
  if (k === "Escape") {
    chiudiMenuContesto();
    if (D.cmd) { annullaComando(); return true; }
    return false;
  }
  if (k === "Shift") return false;
  if (k === "Delete") { e.preventDefault(); eseguiComando("CANCELLA"); return true; }
  if (k === "Enter") { e.preventDefault(); if (D.cmd) invio(); else if (D.ultimo) eseguiComando(D.ultimo); return true; }
  if (e.code === "Space" && D.cmd) { e.preventDefault(); invio(); return true; }
  if (k.length === 1 && D.input) {                       // si sta scrivendo un testo sul disegno
    e.preventDefault();
    const it = $("#inputTesto");
    it.focus(); it.value += k; if (D.cmd) D.cmd.dati.testo = it.value;
    return true;
  }
  if (k.length === 1 && /[\w@<.,\-?]/.test(k)) {
    e.preventDefault();
    const inp = $("#cmdInput");
    inp.focus(); inp.value += k;
    aggiornaSugg();
    return true;
  }
  return false;
}
function nascondiSugg() { $("#suggerimenti").hidden = true; D.sugg = []; D.iSugg = -1; }
function aggiornaSugg() {
  const v = $("#cmdInput").value.trim().toUpperCase();
  const ul = $("#suggerimenti");
  if (!v || (D.cmd && !/^[A-Z?]{2,}$/.test(v)) || /^[-@\d.]/.test(v)) { nascondiSugg(); return; }
  const punteggio = (c) => c.a.includes(v) || c.n === v ? 0 : c.a.some((a) => a.startsWith(v)) ? 1 : c.n.startsWith(v) ? 2
    : c.d.toUpperCase().includes(v) ? 3 : 9;
  D.sugg = v === "?" ? COMANDI.slice() : COMANDI.map((c) => [punteggio(c), c]).filter((x) => x[0] < 9).sort((a, b) => a[0] - b[0]).map((x) => x[1]).slice(0, 8);
  D.iSugg = D.sugg.length ? 0 : -1;
  if (!D.sugg.length) { nascondiSugg(); return; }
  ul.innerHTML = D.sugg.map((c, i) => `<li data-i="${i}" class="${i === D.iSugg ? "corrente" : ""}"><b>${c.n}</b><span class="alias">${c.a.slice(0, 2).join(" ")}</span><span class="descr">${esc(c.d)}</span></li>`).join("");
  ul.hidden = false;
  ul.querySelectorAll("li").forEach((li) => { li.onmousedown = (e) => { e.preventDefault(); const c = D.sugg[+li.dataset.i]; $("#cmdInput").value = ""; nascondiSugg(); $("#cmdInput").blur(); eseguiComando(c.n); }; });
}
function evidenziaSugg() { $$("#suggerimenti li").forEach((li, i) => li.classList.toggle("corrente", i === D.iSugg)); }
function collegaRigaComando() {
  const inp = $("#cmdInput");
  inp.addEventListener("input", aggiornaSugg);
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {                     // come in AutoCAD lo spazio vale Invio
      e.preventDefault();
      const c = D.iSugg >= 0 && !$("#suggerimenti").hidden ? D.sugg[D.iSugg] : null;
      const v = c && !D.cmd ? c.n : inp.value;
      if (inp.value.trim()) { D.storia.push(inp.value.trim()); D.iStoria = D.storia.length; }
      inp.value = ""; nascondiSugg(); inp.blur();
      interpreta(v);
    } else if (e.key === "Escape") {
      e.preventDefault(); inp.value = ""; nascondiSugg(); inp.blur(); annullaComando();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const su = e.key === "ArrowUp";
      if (!$("#suggerimenti").hidden && D.sugg.length) { D.iSugg = (D.iSugg + (su ? -1 : 1) + D.sugg.length) % D.sugg.length; evidenziaSugg(); }
      else if (D.storia.length) { D.iStoria = Math.max(0, Math.min(D.storia.length, D.iStoria + (su ? -1 : 1))); inp.value = D.storia[D.iStoria] || ""; }
    } else if (e.key === "Tab" && D.iSugg >= 0 && !$("#suggerimenti").hidden) {
      e.preventDefault(); inp.value = D.sugg[D.iSugg].n; nascondiSugg();
    } else if (e.key === "Backspace" && !inp.value) { inp.blur(); }
  });
  inp.addEventListener("blur", () => setTimeout(nascondiSugg, 120));
  $("#promptCmd").addEventListener("click", (e) => { const o = e.target.dataset && e.target.dataset.opz; if (o) opzione(o); });
  const it = $("#inputTesto");
  it.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confermaInputTesto(true); }
    else if (e.key === "Escape") { e.preventDefault(); confermaInputTesto(false); }
  });
  it.addEventListener("input", () => { if (D.cmd) D.cmd.dati.testo = it.value; });
  it.addEventListener("blur", () => setTimeout(() => { if (D.input && document.activeElement !== it) confermaInputTesto(true); }, 150));
}

/* ------------------------------------------------------------- finestre */
function modale(html, bottoni) {
  const m = $("#modale");
  m.innerHTML = `<div class="corpoModale">${html}</div><div class="piedeModale">${bottoni.map((b, i) =>
    `<button data-i="${i}" class="${b.primario ? "primario" : ""}${b.pericolo ? " pericolo" : ""}">${esc(b.t)}</button>`).join("")}</div>`;
  $("#velo").hidden = false;
  m.querySelectorAll(".piedeModale button").forEach((x) => { x.onclick = () => { chiudiModale(); const b = bottoni[+x.dataset.i]; if (b.fn) b.fn(); }; });
  setTimeout(() => { const b = m.querySelector("button.primario") || m.querySelector(".piedeModale button:last-child"); if (b) b.focus(); }, 0);
}
function chiudiModale() { $("#velo").hidden = true; $("#modale").classList.remove("larga"); }
function aiuto() {
  const gruppi = [...new Set(COMANDI.map((c) => c.g))];
  modale(`<h3>Comandi</h3><p class="piccolo">Scrivi il nome o l'alias e premi Invio (o Spazio). Durante un comando: <code>x,y</code> punto,
    <code>@dx,dy</code> relativo, <code>@d&lt;ang</code> polare, un numero = distanza lungo il cursore. Invio o tasto destro = fine, Esc = annulla.</p>
    <div class="grigliaAiuto">${gruppi.map((g) => `<section><h4>${esc(g)}</h4>${COMANDI.filter((c) => c.g === g).map((c) =>
      `<div class="vocAiuto"><b>${c.n}</b>${c.a.length ? `<kbd>${esc(c.a[0])}</kbd>` : ""}<span>${esc(c.d)}</span></div>`).join("")}</section>`).join("")}</div>
    <p class="piccolo">Tasti: <kbd>F3</kbd> OSNAP · <kbd>F7</kbd> griglia · <kbd>F8</kbd> ORTO (o tieni Maiusc) · <kbd>Canc</kbd> cancella ·
    <kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd> annulla / ripeti · <kbd>Ctrl+S</kbd> salva · <kbd>Ctrl+A</kbd> seleziona tutto · doppio clic su un testo = modifica.</p>`,
  [{ t: "Chiudi", primario: true }]);
  $("#modale").classList.add("larga");
}

/* ------------------------------------------------------------- schizzo per Claude */
function datiSchizzo(f) {
  return (f.sk || []).map((o) => ({ t: o.t, punti: o.p.map((q) => q.map((v) => Math.round(v * 100) / 100)), testo: o.x ? o.x.s : undefined }));
}
function togliSchizzo(f, dire = true) {
  if (!f || !f.schizzo || !f.schizzo.length) return;
  f.schizzo = []; ricalcolaBozza(f);
  if (dire) rigaComando("Schizzo cancellato.");
}
function inviaSchizzo() {
  const r = riquadroFuoco();
  const f = r && S.fogli.get(r.foglio);
  if (!f || !f.sk || !f.sk.length) { avviso("Disegna prima qualcosa con la <b>Matita</b> o la <b>Nuvola</b> (o accendi «Disegna come schizzo»)."); return; }
  let z = null;
  for (const o of f.sk) z = unione(z, o.b);
  apriDialogo({ tipo: "schizzo", foglio: f.nome, zona: margine(z, 0.15, 4), r });
}

/* ------------------------------------------------------------- proprieta' modificabili */
function pannelloModifica(box, ogg, f) {
  const mod = ogg.filter((o) => o.hd || o.rif);
  if (!mod.length) return;
  const testo = ogg.length === 1 && ogg[0].x && !(ogg[0].blk && !ogg[0].tag && !ogg[0].rif) ? ogg[0] : null;
  const layers = [...new Set([...f.ogg.values()].map((o) => o.ly))].sort((a, b) => a.localeCompare(b, "it", { numeric: true }));
  const sez = document.createElement("div");
  sez.className = "modificaProp";
  sez.innerHTML = `<div class="titoloModProp">Modifica ${mod.length > 1 ? `${mod.length} oggetti` : ""}</div>
    ${testo ? `<label>Testo<input id="pmTesto" value="${esc(testo.x.s)}"></label>
      <label>Altezza<input id="pmAltezza" type="number" step="0.1" min="0.1" value="${fmtN(testo.x.h)}"></label>` : ""}
    <label>Layer<select id="pmLayer"><option value="">invariato</option>${layers.map((l) => `<option>${esc(l)}</option>`).join("")}</select></label>
    <label>Colore<select id="pmColore"><option value="">invariato</option>${$("#selColore").innerHTML}</select></label>
    <div class="azioniRich"><span class="metaRich">Va nel DWG quando salvi</span><button id="pmApplica" class="primario">Applica</button></div>`;
  const dove = box.querySelector("#datiSpac");
  box.insertBefore(sez, dove);
  $("#pmApplica").onclick = () => {
    const ops = [];
    const tg = (o) => (o.rif ? { rif: o.rif } : { hd: o.hd });
    if (testo) {
      const t = $("#pmTesto").value, h = +$("#pmAltezza").value;
      if (t !== testo.x.s) ops.push({ k: "text", ...tg(testo), tag: testo.tag || undefined, testo: t });
      if (h > 0 && Math.abs(h - testo.x.h) > 1e-6 && !testo.tag) ops.push({ k: "prop", ...tg(testo), altezza: h });
    }
    const ly = $("#pmLayer").value, col = $("#pmColore").value;
    if (ly || col) {
      const fatti = new Set();
      for (const o of mod) {
        const k = o.rif || o.hd;
        if (fatti.has(k)) continue;
        fatti.add(k);
        ops.push({ k: "prop", ...tg(o), layer: ly || undefined, colore: col ? +col : undefined });
      }
    }
    if (!ops.length) { avviso("Nessuna modifica."); return; }
    aggiungiOps(f, ops, `<b>_PROPERTIES</b>  ${ops.length} modifiche`);
    aggiornaSelezione();
  };
}

/* ------------------------------------------------------------- ribbon */
function aggiornaRibbonDisegno() {
  const f = fDisegno();
  if (D.cmd && riquadroFuoco() && riquadroFuoco().foglio !== D.cmd.foglio && S.modo !== "sel") annullaComando(true);
  const sel = $("#selLayer");
  const m = (f && f.meta) || {};
  const fisso = (m.tipo === "spac" || m.tipo === "layer") && m.layer !== undefined && m.layer !== null;
  const layers = f ? [...new Set([...f.ogg.values()].map((o) => o.ly))].sort((a, b) => a.localeCompare(b, "it", { numeric: true })) : [];
  const v = sel.value;
  if (fisso) {
    sel.innerHTML = `<option value="${esc(m.layer)}">Foglio ${esc(m.num)} · layer ${esc(m.layer)}</option>`;
    sel.disabled = true;
    sel.title = "Multifoglio SPAC: gli oggetti nuovi vanno sul layer del foglio, altrimenti non ne farebbero parte";
  } else {
    if (!layers.includes("0")) layers.unshift("0");
    sel.innerHTML = layers.map((l) => `<option>${esc(l)}</option>`).join("");
    sel.disabled = !f;
    sel.value = layers.includes(v) ? v : (layers.includes("0") ? "0" : layers[0] || "");
    sel.title = "Layer degli oggetti nuovi";
  }
  aggiornaStatoBozza();
}
function coloreRibbon() {
  D.colore = +$("#selColore").value;
  const c = D.colore === 256 ? null : ACI[D.colore];
  $("#campColore").style.background = c || (D.colore === 7 ? "linear-gradient(135deg,#fff 50%,#000 50%)" : "transparent");
  $("#campColore").classList.toggle("daLayer", D.colore === 256);
}
function collegaDisegno() {
  $$("[data-comando]").forEach((b) => { b.onclick = () => eseguiComando(b.dataset.comando); });
  $("#btnSalva").onclick = salva;
  $("#stBozza").onclick = salva;
  $("#btnAnnulla").onclick = annulla;
  $("#btnRipeti").onclick = ripeti;
  $("#btnScarta").onclick = scarta;
  $("#btnSchizzo").onclick = () => eseguiComando("SCHIZZO");
  $("#btnInviaSchizzo").onclick = inviaSchizzo;
  $("#btnTogliSchizzo").onclick = () => togliSchizzo(fDisegno());
  $("#btnAiuto").onclick = aiuto;
  $("#stOrto").onclick = () => eseguiComando("ORTO");
  $("#selAltezza").onchange = (e) => { D.h = +e.target.value || 2.5; aggiornaPrompt(); };
  $("#selColore").onchange = coloreRibbon;
  $("#velo").addEventListener("mousedown", (e) => { if (e.target === $("#velo")) chiudiModale(); });
  document.addEventListener("mousedown", (e) => { const m = $("#menuContesto"); if (!m.hidden && !m.contains(e.target)) chiudiMenuContesto(); });
  collegaRigaComando();
  coloreRibbon();
  aggiornaRibbonDisegno();
  aggiornaPrompt();
}
collegaDisegno();
