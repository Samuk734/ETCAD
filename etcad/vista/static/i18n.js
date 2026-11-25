"use strict";
/* Lingue dell'interfaccia: italiano (il testo scritto nella pagina), inglese, francese, spagnolo.

   La pagina e' scritta in italiano. Qui un osservatore traduce i testi e gli attributi (title, placeholder...)
   di tutto quello che compare nel documento, anche quello che app.js e disegno.js aggiungono dopo, e ricorda
   l'originale per poter cambiare lingua in qualsiasi momento. Le chiavi sono le frasi italiane; quelle con {0},
   {1}... valgono per i testi composti con dei valori. Restano come sono le zone marcate .notrad (i messaggi
   della chat, il ragionamento, i dati del disegno). Le traduzioni stanno in i18n_testi.js (TRAD).
   Nel codice: traduci("frase", {0: valore}) per i testi disegnati sul canvas o messi fuori dal DOM, e
   nomeCmd("LINEA") per il nome di un comando nella lingua scelta. */

const LINGUE = [
  { id: "it", nome: "Italiano" },
  { id: "en", nome: "English" },
  { id: "fr", nome: "Français" },
  { id: "es", nome: "Español" },
];

/* Nomi dei comandi come nelle versioni di AutoCAD in quella lingua: si vedono nei suggerimenti e nell'aiuto e
   valgono anche come alias da scrivere (in ogni lingua funzionano tutti). */
const NOMI_CMD = {
  en: { LINEA: "LINE", POLILINEA: "PLINE", RETTANGOLO: "RECTANG", CERCHIO: "CIRCLE", ARCO: "ARC", TESTO: "TEXT",
    QUOTA: "DIMALIGNED", ETICHETTA: "LEADER", SPOSTA: "MOVE", COPIA: "COPY", CANCELLA: "ERASE", MODTESTO: "TEXTEDIT",
    PROPRIETA: "PROPERTIES", SELEZIONA: "SELECTALL", ANNULLA: "UNDO", RIPETI: "REDO", SALVA: "SAVE", MATITA: "SKETCH",
    NUVOLA: "REVCLOUD", SCHIZZO: "SKETCHMODE", CHIEDI: "ASK", NOTA: "NOTE", CHAT: "CHAT", DISTANZA: "DIST", AREA: "AREA",
    ID: "ID", FILO: "WIRE", TROVA: "FIND", ZOOM: "ZOOM", ZOOMF: "ZOOMW", PAN: "PAN", ORTO: "ORTHO", OSNAP: "OSNAP",
    GRIGLIA: "GRID", AIUTO: "HELP" },
  fr: { LINEA: "LIGNE", POLILINEA: "POLYLIGN", RETTANGOLO: "RECTANG", CERCHIO: "CERCLE", ARCO: "ARC", TESTO: "TEXTE",
    QUOTA: "COTALI", ETICHETTA: "REPERE", SPOSTA: "DEPLACER", COPIA: "COPIER", CANCELLA: "EFFACER", MODTESTO: "MODIFTEXTE",
    PROPRIETA: "PROPRIETES", SELEZIONA: "TOUTSEL", ANNULLA: "ANNULER", RIPETI: "RETABLIR", SALVA: "ENREG", MATITA: "ESQUISSE",
    NUVOLA: "NUAGEREV", SCHIZZO: "MODEESQUISSE", CHIEDI: "DEMANDER", NOTA: "NOTE", CHAT: "CHAT", DISTANZA: "DISTANCE",
    AREA: "AIRE", ID: "ID", FILO: "FIL", TROVA: "CHERCHER", ZOOM: "ZOOM", ZOOMF: "ZOOMFEN", PAN: "PANO", ORTO: "ORTHO",
    OSNAP: "ACCROBJ", GRIGLIA: "GRILLE", AIUTO: "AIDE" },
  es: { LINEA: "LINEA", POLILINEA: "POLILINEA", RETTANGOLO: "RECTANG", CERCHIO: "CIRCULO", ARCO: "ARCO", TESTO: "TEXTO",
    QUOTA: "ACOALINEADA", ETICHETTA: "DIRECTRIZ", SPOSTA: "DESPLAZA", COPIA: "COPIA", CANCELLA: "BORRA", MODTESTO: "EDITTEXTO",
    PROPRIETA: "PROPIEDADES", SELEZIONA: "SELTODO", ANNULLA: "DESHACER", RIPETI: "REHACER", SALVA: "GUARDAR", MATITA: "BOCETO",
    NUVOLA: "NUBEREV", SCHIZZO: "MODOBOCETO", CHIEDI: "PREGUNTAR", NOTA: "NOTA", CHAT: "CHAT", DISTANZA: "DIST", AREA: "AREA",
    ID: "ID", FILO: "CABLE", TROVA: "BUSCAR", ZOOM: "ZOOM", ZOOMF: "ZOOMV", PAN: "ENCUADRE", ORTO: "ORTO", OSNAP: "REFENT",
    GRIGLIA: "REJILLA", AIUTO: "AYUDA" },
};

/* Opzioni dei comandi (Chiudi/Annulla/Fine) scritte per intero nelle altre lingue. */
const OPZIONI_CMD = { CHIUDI: ["CLOSE", "FERMER", "CERRAR"], ANNULLA: ["UNDO", "ANNULER", "DESHACER"],
  FINE: ["END", "EXIT", "FIN", "QUITTER", "SALIR"] };

let LINGUA = "it";
const ATTR_TRAD = ["title", "placeholder", "aria-label", "alt", "data-sugg"];
const _origTesto = new WeakMap();          // nodo di testo -> { it, out }
const _origAttr = new WeakMap();           // elemento -> { attributo: { it, out } }
const _schemi = {};                        // lingua -> [{ re, out }] dalle chiavi con {0}, {1}...
let _titoloIt = null;

function _norm(s) { return String(s).replace(/\s+/g, " ").trim(); }
function _escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function schemiDi(l) {
  if (_schemi[l]) return _schemi[l];
  const d = (typeof TRAD !== "undefined" && TRAD[l]) || {};
  _schemi[l] = Object.keys(d).filter((k) => /\{\d+\}/.test(k)).map((k) => {
    const pezzi = k.split(/\{(\d+)\}/);
    const ordine = [];
    let re = "^";
    pezzi.forEach((p, i) => { if (i % 2) { ordine.push(+p); re += "([\\s\\S]+?)"; } else re += _escRe(p); });
    return { re: new RegExp(re + "$"), ordine, out: d[k], lung: k.length };
  }).sort((a, b) => b.lung - a.lung);       // prima gli schemi piu' specifici
  return _schemi[l];
}

/* Traduzione di una frase intera, o null se non c'e'. */
function traduzioneDi(s, l = LINGUA) {
  if (l === "it" || s == null) return null;
  const d = typeof TRAD !== "undefined" && TRAD[l];
  if (!d) return null;
  const n = _norm(s);
  if (!n) return null;
  if (Object.prototype.hasOwnProperty.call(d, n)) return d[n];
  for (const sc of schemiDi(l)) {
    const m = sc.re.exec(n);
    if (m) {
      const val = {};
      sc.ordine.forEach((k, i) => { val[k] = traduzioneDi(m[i + 1], l) ?? m[i + 1]; });   // anche i pezzi noti
      return sc.out.replace(/\{(\d+)\}/g, (x, k) => (k in val ? val[k] : x));
    }
  }
  return null;
}

/* Per il codice: frase italiana -> lingua scelta, con i valori {0}, {nome}... */
function traduci(s, valori) {
  let r = traduzioneDi(s) ?? s;
  if (valori) r = r.replace(/\{(\w+)\}/g, (x, k) => (valori[k] !== undefined ? valori[k] : x));
  return r;
}

function nomeCmd(n) { return (NOMI_CMD[LINGUA] && NOMI_CMD[LINGUA][n]) || n; }
function aliasLingue(n) { return Object.values(NOMI_CMD).map((d) => d[n]).filter(Boolean); }

function _conSpazi(orig, t) {
  const m = /^(\s*)[\s\S]*?(\s*)$/.exec(orig);
  return m[1] + t + m[2];
}
function _salta(el) {
  return !el || !!el.closest(".notrad, [translate='no'], script, style, textarea, code, svg");
}

function _traduciTesto(nodo) {
  const el = nodo.parentElement;
  if (_salta(el)) return;
  const cur = nodo.nodeValue;
  let o = _origTesto.get(nodo);
  if (!o || (cur !== o.out && cur !== o.it)) o = { it: cur, out: cur };     // testo nuovo messo dal codice
  const t = LINGUA === "it" ? null : traduzioneDi(o.it);
  o.out = t == null ? o.it : _conSpazi(o.it, t);
  _origTesto.set(nodo, o);
  if (cur !== o.out) nodo.nodeValue = o.out;
}

function _traduciAttr(el, nome) {
  if (_salta(el) && !(el.matches && el.matches("textarea, input"))) return;
  const cur = el.getAttribute(nome);
  if (cur == null) return;
  const tutti = _origAttr.get(el) || {};
  let o = tutti[nome];
  if (!o || (cur !== o.out && cur !== o.it)) o = { it: cur, out: cur };
  const t = LINGUA === "it" ? null : traduzioneDi(o.it);
  o.out = t == null ? o.it : _conSpazi(o.it, t);
  tutti[nome] = o;
  _origAttr.set(el, tutti);
  if (cur !== o.out) el.setAttribute(nome, o.out);
}

function _traduciAlbero(radice) {
  if (!radice) return;
  if (radice.nodeType === 3) { _traduciTesto(radice); return; }
  if (radice.nodeType !== 1 || _salta(radice) && !radice.matches("textarea, input")) return;
  const sel = ATTR_TRAD.map((a) => `[${a}]`).join(",");
  const els = radice.matches(sel) ? [radice] : [];
  els.push(...radice.querySelectorAll(sel));
  for (const el of els) for (const a of ATTR_TRAD) if (el.hasAttribute(a)) _traduciAttr(el, a);
  const w = document.createTreeWalker(radice, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.trim()) _traduciTesto(n);
}

const _osservatore = new MutationObserver((lista) => {
  for (const m of lista) {
    if (m.type === "characterData") _traduciTesto(m.target);
    else if (m.type === "attributes") _traduciAttr(m.target, m.attributeName);
    else for (const n of m.addedNodes) _traduciAlbero(n);
  }
});

/* ---------------------------------------------------------------- bandierine (SVG: su Windows le emoji no) */
let _nBandiera = 0;
function bandiera(l) {
  const k = ++_nBandiera;
  const tre = (a, b, c) => `<svg class="bandiera" viewBox="0 0 3 2" aria-hidden="true"><rect width="1" height="2" fill="${a}"/>` +
    `<rect x="1" width="1" height="2" fill="${b}"/><rect x="2" width="1" height="2" fill="${c}"/></svg>`;
  if (l === "it") return tre("#009246", "#f4f5f0", "#ce2b37");
  if (l === "fr") return tre("#002395", "#f4f5f0", "#ed2939");
  if (l === "es") return `<svg class="bandiera" viewBox="0 0 3 2" aria-hidden="true"><rect width="3" height="2" fill="#aa151b"/>` +
    `<rect y=".5" width="3" height="1" fill="#f1bf00"/></svg>`;
  return `<svg class="bandiera" viewBox="0 0 60 40" aria-hidden="true"><clipPath id="ukc${k}"><path d="M30,20h30v20zv20h-30zh-30v-20zv-20h30z"/></clipPath>` +
    `<rect width="60" height="40" fill="#012169"/><path d="M0,0L60,40M60,0L0,40" stroke="#fff" stroke-width="8"/>` +
    `<path d="M0,0L60,40M60,0L0,40" clip-path="url(#ukc${k})" stroke="#c8102e" stroke-width="5"/>` +
    `<path d="M30,0v40M0,20h60" stroke="#fff" stroke-width="12"/><path d="M30,0v40M0,20h60" stroke="#c8102e" stroke-width="7"/></svg>`;
}

/* ------------------------------------------------------------------------------------- selettore e cambio */
function linguaIniziale() {
  let l = null;
  try { l = localStorage.getItem("etcadVistaLingua"); } catch (e) { /* niente memoria: si usa il browser */ }
  if (LINGUE.some((x) => x.id === l)) return l;
  const nav = (navigator.languages || [navigator.language || "it"]).map((x) => String(x).slice(0, 2).toLowerCase());
  return nav.find((x) => LINGUE.some((y) => y.id === x)) || "it";
}

function impostaLingua(l, salva = true) {
  if (!LINGUE.some((x) => x.id === l)) l = "it";
  LINGUA = l;
  if (salva) { try { localStorage.setItem("etcadVistaLingua", l); } catch (e) { /* facoltativo */ } }
  document.documentElement.lang = l;
  if (_titoloIt === null) _titoloIt = document.title;
  document.title = traduci(_titoloIt);
  _traduciAlbero(document.body);
  const b = document.getElementById("btnLingua");
  if (b) {
    b.querySelector(".bandieraLingua").innerHTML = bandiera(l);
    b.querySelector(".codiceLingua").textContent = l.toUpperCase();
  }
  document.querySelectorAll("#menuLingua .voceLingua").forEach((v) => v.classList.toggle("scelta", v.dataset.lingua === l));
  window.dispatchEvent(new CustomEvent("lingua", { detail: l }));
}

function collegaSelettoreLingua() {
  const btn = document.getElementById("btnLingua"), menu = document.getElementById("menuLingua");
  if (!btn || !menu) return;
  menu.innerHTML = LINGUE.map((x) => `<button class="voceLingua notrad" role="menuitemradio" data-lingua="${x.id}">` +
    `${bandiera(x.id)}<span class="nomeLingua">${x.nome}</span><span class="codice">${x.id.toUpperCase()}</span></button>`).join("");
  const chiudi = () => { menu.hidden = true; btn.classList.remove("acceso"); btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    btn.classList.toggle("acceso", !menu.hidden);
    btn.setAttribute("aria-expanded", String(!menu.hidden));
  });
  menu.addEventListener("click", (e) => {
    const v = e.target.closest(".voceLingua");
    if (!v) return;
    impostaLingua(v.dataset.lingua);
    chiudi();
  });
  document.addEventListener("mousedown", (e) => { if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) chiudi(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) chiudi(); });
}

/* Parte subito (gli script stanno in fondo al body): traduce l'HTML e tutto quello che arriva dopo. */
LINGUA = linguaIniziale();
_osservatore.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTR_TRAD });
collegaSelettoreLingua();
impostaLingua(LINGUA, false);
