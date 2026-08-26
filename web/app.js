/* Tikkun Lab — application front end.
 *
 * Compute is split on one principle: whatever has to feel instant runs here in
 * the browser, whatever cannot run here goes to the server.
 *
 *   here    engine.js drives every slider at about a millisecond a frame. It is
 *           not a second opinion — same equations, checked value-by-value
 *           against the Python engine by verify.mjs.
 *   server  the agent army (needs API keys and about a minute), the experiment
 *           log, and the parameter registry with its citations.
 */

import {
  PARAMS, PATIENTS, applyIntervention, challenge, crosslinks, doseResponse,
  elicitingDose, runProtocol, sensitizedReceptors,
} from './engine.js';
import { createCellView } from './cell3d.js';
import { startTour, tourWasSeen } from './tour.js';

const $ = id => document.getElementById(id);
const MG_PER_ML = 33;                       // cow's milk carries ~33 mg protein/mL
const STORE_KEY = 'tikkun-lab-state-v1';

/* ---------------- helpers ---------------- */

const toLog = (pos, lo, hi) =>
  Math.pow(10, Math.log10(lo) + (Math.log10(hi) - Math.log10(lo)) * pos / 100);
const fromLog = (v, lo, hi) =>
  100 * (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo));

const SIGE = [0.35, 300], TIGE = [30, 3000], BARRIER = [0.2, 5], DOSE = [0.01, 16000];

const num = v => (v === 'Infinity' ? Infinity : v === '-Infinity' ? -Infinity : v);

function fmtMg(mg) {
  mg = num(mg);
  if (!isFinite(mg)) return '∞';
  if (mg < 1) return mg.toFixed(3);
  if (mg < 100) return mg.toFixed(1);
  return Math.round(mg).toLocaleString('en-US');
}

function milkEquivalent(mg) {
  mg = num(mg);
  if (!isFinite(mg)) return 'כל כמות נסבלת';
  const ml = mg / MG_PER_ML;
  if (ml < 0.05) return 'פחות מטיפה';
  if (ml < 1) return Math.round(ml * 1000) + ' מיקרוליטר';
  if (ml < 5) return ml.toFixed(1) + ' מ״ל — פחות מכפית';
  if (ml < 240) return ml.toFixed(0) + ' מ״ל';
  return (ml / 240).toFixed(1) + ' כוסות';
}

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 2200);
}

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({ error: 'bad response' }));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

/* ---------------- state ---------------- */

const state = {
  patient: { ...PATIENTS.default },
  preset: 'default',
  dose: 25,
  levers: {
    oral_immunotherapy:   { on: false, daily_dose_mg: 300, days: 365 },
    anti_ige:             { on: false, free_ige_reduction: 0.9 },
    passive_igg4:         { on: false, titre_mg_l: 120 },
    barrier_repair:       { on: false, permeability_factor: 0.4 },
    mast_cell_stabiliser: { on: false, threshold_factor: 8 },
  },
};

function persist() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      patient: state.patient, preset: state.preset,
      dose: state.dose, levers: state.levers,
    }));
  } catch { /* private browsing — the app still works, it just forgets */ }
}

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!saved) return;
    Object.assign(state.patient, saved.patient || {});
    state.preset = saved.preset ?? state.preset;
    state.dose = saved.dose ?? state.dose;
    for (const [kind, values] of Object.entries(saved.levers || {})) {
      if (state.levers[kind]) Object.assign(state.levers[kind], values);
    }
  } catch { /* corrupt entry — start fresh rather than fail to boot */ }
}

/* ---------------- levers ---------------- */

const LEVER_UI = [
  { kind: 'oral_immunotherapy', name: 'אימונותרפיה פומית',
    mech: 'מנה יומית מעלה IgG4 חוסם ומשרה תאי Treg. Skripak 2008.',
    fields: [
      { key: 'daily_dose_mg', label: 'מינון יומי', range: [1, 3000], log: true, unit: 'מ״ג/יום' },
      { key: 'days', label: 'משך', range: [30, 1095], unit: 'ימים' }] },
  { kind: 'anti_ige', name: 'אנטי-IgE (אומאליזומאב)',
    mech: 'סופח IgE חופשי, כך שפחות קולטנים נושאים IgE לחלב. Wood 2016.',
    fields: [{ key: 'free_ige_reduction', label: 'הפחתת IgE חופשי', range: [0, 0.99], pct: true }] },
  { kind: 'passive_igg4', name: 'נוגדן חוסם פסיבי',
    mech: 'מכסה אפיטופים על האלרגן ישירות, בלי להמתין שהגוף ייצר. Orengo 2018.',
    fields: [{ key: 'titre_mg_l', label: 'טיטר', range: [0, 400], unit: 'מ״ג/ל׳' }] },
  { kind: 'barrier_repair', name: 'תיקון המחסום הרירי',
    mech: 'פחות אלרגן שלם חוצה את האפיתל באותו מינון נאכל.',
    fields: [{ key: 'permeability_factor', label: 'חדירות שנותרה', range: [0.05, 1], pct: true }] },
  { kind: 'mast_cell_stabiliser', name: 'מייצב תאי פיטום',
    mech: 'מעלה את מספר הקישורים הצולבים הדרוש לדגרנולציה. Zur 1987.',
    fields: [{ key: 'threshold_factor', label: 'העלאת הסף', range: [1, 50], log: true, unit: '×' }] },
];

const activeSteps = () => LEVER_UI
  .filter(l => state.levers[l.kind].on)
  .map(l => ({ kind: l.kind,
               params: Object.fromEntries(l.fields.map(f => [f.key, state.levers[l.kind][f.key]])) }));

const describeStep = step =>
  `${step.kind}(${Object.entries(step.params)
    .map(([k, v]) => `${k}=${Number(v).toPrecision(3).replace(/\.?0+$/, '')}`).join(', ')})`;

/* ---------------- charts ---------------- */

function palette() {
  const s = getComputedStyle(document.documentElement);
  const g = n => s.getPropertyValue(n).trim();
  return { accent: g('--accent'), stop: g('--stop'), ink3: g('--ink-3'), rule: g('--rule') };
}

function glPalette() {
  const s = getComputedStyle(document.documentElement);
  const g = n => s.getPropertyValue(n).trim();
  return {
    membrane: g('--gl-membrane'), rim: g('--gl-rim'), bare: g('--gl-bare'),
    sensitised: g('--gl-sens'), crosslinked: g('--gl-cross'),
    allergenDot: g('--gl-allergen'), granule: g('--gl-granule'),
  };
}

/* Both dimensions come from the element's laid-out size, so the canvas follows
 * whatever CSS gives it — a clamp against the viewport, a narrowing column, a
 * panel that was hidden when the page loaded. Reading a fixed height attribute
 * instead pins the chart to one size forever. */
function setup(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(canvas.clientWidth));
  const h = Math.max(1, Math.round(canvas.clientHeight));
  const pixelW = Math.round(w * dpr);
  const pixelH = Math.round(h * dpr);
  if (canvas.width !== pixelW || canvas.height !== pixelH) {
    canvas.width = pixelW;
    canvas.height = pixelH;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  // Narrow charts get tighter gutters and fewer labels; crowded ticks are worse
  // than missing ones.
  const compact = w < 430;
  return {
    ctx, w, h, compact,
    pad: compact ? { t: 10, r: 8, b: 20, l: 27 } : { t: 12, r: 10, b: 24, l: 34 },
    font: compact ? '8px' : '9px',
  };
}

/** Pick as many labels as will fit without crowding. */
function tickCount(width, minSpacing = 88) {
  return Math.max(2, Math.min(6, Math.floor(width / minSpacing)));
}

function drawHistamine(withTx, without) {
  const canvas = $('chart-hist');
  if (!canvas.clientWidth || !canvas.clientHeight) return;
  const { ctx, w, h, pad, font, compact } = setup(canvas);
  const p = palette();
  const maxY = Math.max(2, ...withTx.map(d => d[1]), ...without.map(d => d[1])) * 1.12;
  const maxX = withTx[withTx.length - 1][0];
  const X = t => pad.l + (w - pad.l - pad.r) * t / maxX;
  const Y = v => h - pad.b - (h - pad.t - pad.b) * v / maxY;

  ctx.font = `${font} "IBM Plex Mono", monospace`;
  ctx.strokeStyle = p.rule; ctx.lineWidth = 1; ctx.fillStyle = p.ink3;
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  // One decimal while the axis spans only a few ng/mL, or the gridlines label
  // themselves 0, 1, 1, 2 and two of them look identical.
  const digits = maxY < 6 ? 1 : 0;
  const rows = h < 150 ? 2 : 3;
  for (let i = 0; i <= rows; i++) {
    const y = Y(maxY * i / rows);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText((maxY * i / rows).toFixed(digits), pad.l - 4, y);
  }
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const cols = tickCount(w - pad.l - pad.r, compact ? 62 : 84);
  for (let i = 0; i <= cols; i++) {
    ctx.fillText(Math.round(maxX * i / cols / 60) + 'ד', X(maxX * i / cols), h - pad.b + 4);
  }
  // The plasma histamine that corresponds to objective symptoms, so the reader
  // can see how far the curve has to climb before anything happens.
  if (2.4 < maxY) {
    ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = p.stop; ctx.globalAlpha = .55;
    ctx.beginPath(); ctx.moveTo(pad.l, Y(2.4)); ctx.lineTo(w - pad.r, Y(2.4)); ctx.stroke();
    ctx.restore();
  }
  const line = (data, color, fill) => {
    const trace = () => {
      ctx.beginPath();
      data.forEach(([t, v], i) => (i ? ctx.lineTo(X(t), Y(v)) : ctx.moveTo(X(t), Y(v))));
    };
    if (fill) {
      trace(); ctx.save(); ctx.lineTo(X(maxX), Y(0)); ctx.lineTo(X(0), Y(0)); ctx.closePath();
      ctx.globalAlpha = .13; ctx.fillStyle = color; ctx.fill(); ctx.restore();
    }
    trace(); ctx.strokeStyle = color; ctx.lineWidth = fill ? 2 : 1.3; ctx.stroke();
  };
  ctx.save(); ctx.globalAlpha = .5; line(without, p.ink3, false); ctx.restore();
  line(withTx, p.accent, true);
}

const DOSE_LO = 0.01, DOSE_HI = 16000;
/** Where the dose axis was last drawn, so the pointer can be mapped back to a dose. */
let doseAxis = null;

function drawDoseResponse(base, treated, edBase, edTreated) {
  const canvas = $('chart-dose');
  if (!canvas.clientWidth || !canvas.clientHeight) return;
  const { ctx, w, h, pad, font, compact } = setup(canvas);
  const p = palette();
  const lo = Math.log10(DOSE_LO), hi = Math.log10(DOSE_HI);
  const X = mg => pad.l + (w - pad.l - pad.r) * (Math.log10(mg) - lo) / (hi - lo);
  const Y = v => h - pad.b - (h - pad.t - pad.b) * v / 10;
  doseAxis = { X, Y, pad, w, h, lo, hi };

  ctx.font = `${font} "IBM Plex Mono", monospace`;
  ctx.strokeStyle = p.rule; ctx.lineWidth = 1; ctx.fillStyle = p.ink3;
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  const step = h < 210 ? 5 : 2.5;
  for (let v = 0; v <= 10; v += step) {
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText(v.toFixed(0), pad.l - 4, y);
  }
  // Decade ticks, thinned to whatever the width can hold without overlapping.
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const decades = [0.01, 0.1, 1, 10, 100, 1000, 16000];
  const every = Math.max(1, Math.ceil(decades.length / tickCount(w - pad.l - pad.r, compact ? 52 : 74)));
  decades.filter((_, i) => i % every === 0 || i === decades.length - 1).forEach(mg =>
    ctx.fillText(mg >= 1000 ? (mg / 1000) + 'g' : mg + 'mg', X(mg), h - pad.b + 5));

  ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = p.stop;
  ctx.beginPath(); ctx.moveTo(pad.l, Y(3)); ctx.lineTo(w - pad.r, Y(3)); ctx.stroke(); ctx.restore();

  const curve = (d, color, solid) => {
    ctx.beginPath();
    d.doses.forEach((mg, i) =>
      (i ? ctx.lineTo(X(mg), Y(d.scores[i])) : ctx.moveTo(X(mg), Y(d.scores[i]))));
    ctx.strokeStyle = color; ctx.lineWidth = solid ? 2.2 : 1.3;
    if (!solid) { ctx.save(); ctx.globalAlpha = .55; ctx.stroke(); ctx.restore(); }
    else ctx.stroke();
  };
  const marker = (mg, color) => {
    if (!isFinite(mg) || mg < 0.01 || mg > 16000) return;
    ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.globalAlpha = .85;
    ctx.beginPath(); ctx.moveTo(X(mg), Y(0)); ctx.lineTo(X(mg), Y(3)); ctx.stroke();
    ctx.fillStyle = color; ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(X(mg), Y(3), 3.5, 0, 7); ctx.fill(); ctx.restore();
  };
  curve(base, p.ink3, false); marker(edBase, p.ink3);
  if (treated) { curve(treated, p.accent, true); marker(edTreated, p.accent); }

  if (hoverDoseMg !== null) drawDoseCrosshair(ctx, X, Y, p, h, pad);
}

/* ---------------- reading a point off the dose curve ---------------- */

let hoverDoseMg = null;
/* The last drawn chart inputs. Hovering and resizing both need to repaint
 * without re-running the engine over 46 doses, which is ~20 ms and would make
 * the pointer feel heavy. */
let lastCharts = null;

function drawCurves() {
  if (!lastCharts) return;
  drawHistamine(lastCharts.histTx, lastCharts.histBase);
  drawDoseResponse(lastCharts.base, lastCharts.treated,
                   lastCharts.edBase, lastCharts.edTx);
}

function drawDoseCrosshair(ctx, X, Y, p, h, pad) {
  const x = X(hoverDoseMg);
  if (x < pad.l || x > ctx.canvas.clientWidth - pad.r) return;
  ctx.save();
  ctx.strokeStyle = p.accent; ctx.globalAlpha = .45; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
  ctx.restore();
}

function bindDoseHover() {
  const canvas = $('chart-dose');
  const readout = $('dose-readout');
  if (!canvas || !readout) return;

  const onMove = event => {
    if (!doseAxis) return;
    const box = canvas.getBoundingClientRect();
    const x = event.clientX - box.left;
    const { pad, w, lo, hi } = doseAxis;
    if (x < pad.l || x > w - pad.r) return onLeave();

    const fraction = (x - pad.l) / (w - pad.l - pad.r);
    hoverDoseMg = Math.pow(10, lo + (hi - lo) * fraction);

    // Recomputed from the engine at the exact hovered dose, not interpolated off
    // the drawn polyline — the curve is sampled at 46 points and reading between
    // them would quietly invent values.
    const steps = activeSteps();
    let patient = state.patient, params = PARAMS;
    for (const s of steps) [patient, params] = applyIntervention(s, patient, params);
    const result = challenge(patient, hoverDoseMg, params);

    readout.innerHTML =
      `<b>${fmtMg(hoverDoseMg)} מ״ג</b> · ${milkEquivalent(hoverDoseMg)}<br>` +
      `ציון ${result.symptom_score.toFixed(1)}/10 · ` +
      `${result.reaction ? 'תגובה' : 'נסבל'}`;
    readout.classList.add('show');
    drawCurves();
  };

  const onLeave = () => {
    if (hoverDoseMg === null) return;
    hoverDoseMg = null;
    readout.classList.remove('show');
    drawCurves();
  };

  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerdown', onMove);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('pointercancel', onLeave);
}

/* ---------------- bench ---------------- */

const PRESET_HE = { exquisite: 'רגיש קיצוני', default: 'טיפוסי',
                    moderate: 'בינוני', outgrowing: 'מחלים' };

function buildChips() {
  const chips = $('chips');
  Object.keys(PRESET_HE).forEach(key => {
    const b = document.createElement('button');
    b.className = 'chip'; b.type = 'button'; b.textContent = PRESET_HE[key];
    b.setAttribute('aria-pressed', key === state.preset);
    b.onclick = () => {
      state.patient = { ...PATIENTS[key] };
      state.preset = key;
      syncPatient();
      [...chips.children].forEach(c => c.setAttribute('aria-pressed', c === b));
      recompute();
    };
    chips.appendChild(b);
  });
}

function buildLevers() {
  const host = $('levers');
  LEVER_UI.forEach(lever => {
    const wrap = document.createElement('div');
    wrap.className = 'lever';
    wrap.dataset.on = String(state.levers[lever.kind].on);
    const id = 'lv-' + lever.kind;
    wrap.innerHTML =
      `<div class="lever-head"><input type="checkbox" id="${id}">` +
      `<label for="${id}">${lever.name}</label></div>` +
      `<p class="mech">${lever.mech}</p><div class="controls"></div>`;
    const controls = wrap.querySelector('.controls');

    lever.fields.forEach(f => {
      const sid = `${id}-${f.key}`;
      const field = document.createElement('div');
      field.className = 'field';
      field.innerHTML =
        `<div class="field-top"><label for="${sid}">${f.label}</label>` +
        `<span class="val" id="${sid}-v"></span></div>` +
        `<input type="range" id="${sid}" min="0" max="100">`;
      controls.appendChild(field);

      const input = field.querySelector('input');
      // Reached through the field, not document.getElementById: this whole
      // subtree is still detached until the loop appends it, so a lookup by id
      // returns null and the first write throws.
      const readout = field.querySelector('.val');
      const [lo, hi] = f.range;
      input.disabled = !state.levers[lever.kind].on;
      const write = () => {
        const v = f.log ? toLog(+input.value, lo, hi) : lo + (hi - lo) * (+input.value) / 100;
        state.levers[lever.kind][f.key] = v;
        readout.textContent = f.pct
          ? Math.round(v * 100) + '%'
          : (v >= 100 ? Math.round(v) : v.toFixed(v < 10 ? 1 : 0)) + ' ' + (f.unit || '');
      };
      const cur = state.levers[lever.kind][f.key];
      input.value = f.log ? fromLog(cur, lo, hi) : 100 * (cur - lo) / (hi - lo);
      write();
      input.oninput = () => { write(); schedule(); };
      f._input = input;
    });

    const box = wrap.querySelector('input[type=checkbox]');
    box.checked = state.levers[lever.kind].on;
    box.onchange = e => {
      state.levers[lever.kind].on = e.target.checked;
      wrap.dataset.on = String(e.target.checked);
      lever.fields.forEach(f => { f._input.disabled = !e.target.checked; });
      recompute();
    };
    host.appendChild(wrap);
  });
}

function syncPatient() {
  $('sige').value = fromLog(state.patient.specific_ige_ku, ...SIGE);
  $('tige').value = fromLog(state.patient.total_ige_ku, ...TIGE);
  $('barrier').value = fromLog(state.patient.mucosal_barrier, ...BARRIER);
  labelPatient();
}

function labelPatient() {
  $('sige-v').textContent = state.patient.specific_ige_ku.toFixed(1) + ' kU/L';
  $('tige-v').textContent = Math.round(state.patient.total_ige_ku) + ' kU/L';
  $('barrier-v').textContent = state.patient.mucosal_barrier.toFixed(2) + '×';
  const s = state.patient.specific_ige_ku;
  $('sige-note').textContent = s >= 15
    ? 'מעל 15 — ערך ההכרעה שמנבא אלרגיה פעילה ב-95% מהמקרים.'
    : s >= 2 ? 'טווח ביניים. חלק מהילדים כאן כבר סובלים כמויות קטנות.'
             : 'מתחת ל-2 — רוב הילדים כאן כבר החלימו מהאלרגיה.';
  $('agent-patient').textContent =
    `ירוץ על: milk-sIgE ${state.patient.specific_ige_ku.toFixed(1)}, ` +
    `total IgE ${Math.round(state.patient.total_ige_ku)}, ` +
    `barrier ${state.patient.mucosal_barrier.toFixed(2)}×`;
}

function labelDose() {
  $('dose-v').textContent = fmtMg(state.dose) + ' מ״ג';
  $('dose-note').textContent = milkEquivalent(state.dose) +
    (state.dose >= 7000 ? ' — כוס חלב מלאה' : '');
}

function clearChips() {
  state.preset = null;
  [...$('chips').children].forEach(c => c.setAttribute('aria-pressed', 'false'));
}

const PROTOCOL_PANEL = [
  ['אימונותרפיה 30 מ״ג/יום', [{ kind: 'oral_immunotherapy', params: { daily_dose_mg: 30, days: 365 } }]],
  ['אימונותרפיה 300 מ״ג/יום', [{ kind: 'oral_immunotherapy', params: { daily_dose_mg: 300, days: 365 } }]],
  ['אימונותרפיה 1000 מ״ג/יום', [{ kind: 'oral_immunotherapy', params: { daily_dose_mg: 1000, days: 365 } }]],
  ['אנטי-IgE ואז אימונותרפיה', [{ kind: 'anti_ige', params: { free_ige_reduction: 0.95 } },
                                 { kind: 'oral_immunotherapy', params: { daily_dose_mg: 300, days: 365 } }]],
  ['נוגדן חוסם פסיבי', [{ kind: 'passive_igg4', params: { titre_mg_l: 120 } }]],
];

let lastResult = null;

function recompute() {
  const steps = activeSteps();
  let treated = state.patient, params = PARAMS;
  for (const s of steps) [treated, params] = applyIntervention(s, treated, params);
  const hasTx = steps.length > 0;

  const rTx = challenge(treated, state.dose, params, { keepTrace: true });
  const rBase = hasTx ? challenge(state.patient, state.dose, PARAMS, { keepTrace: true }) : rTx;

  const v = $('verdict');
  v.className = 'verdict ' + (rTx.symptom_score >= 6 ? 'v-danger'
                            : rTx.reaction ? 'v-caution' : 'v-safe');
  $('verdict-word').textContent = rTx.symptom_score >= 6 ? 'תגובה חמורה'
                                : rTx.reaction ? 'תגובה' : 'נסבל';
  $('verdict-why').textContent = rTx.reaction
    ? (rTx.systemic_score > rTx.local_score
        ? 'סימפטומים מערכתיים — היסטמין בפלזמה' : 'סימפטומים מקומיים — עיכול')
    : 'מתחת לסף הסימפטומים האובייקטיביים';
  $('verdict-score').innerHTML = rTx.symptom_score.toFixed(1) + '<small>/10</small>';

  $('metrics').innerHTML = [
    ['קישורים צולבים', Math.round(rTx.crosslinks_per_cell).toLocaleString('en-US'), 'לתא',
     'אלרגן אחד מגשר בין שני נוגדנים. מעל 100 לתא — התא מתפוצץ.'],
    ['תאים מדגרנלים', (rTx.activation * 100).toFixed(1), '%',
     'איזה חלק מהתאים שנחשפו אכן שחררו את התכולה שלהם.'],
    ['שיא היסטמין', rTx.peak_histamine.toFixed(2), 'ng/mL',
     'מעל 2.4 מתחילים סימפטומים. מעל 10 זו תגובה מסכנת חיים.'],
    ['זמן לשיא', rTx.peak_histamine > 0.51 ? (rTx.time_to_peak_s / 60).toFixed(1) : '—', 'דקות',
     'כמה זמן אחרי הבליעה. הקיבה והמעי מעכבים את זה.'],
    ['אלרגן חופשי', (rTx.free_allergen_m * 1e12).toFixed(1), 'pM',
     'הריכוז שבאמת מגיע לתא, אחרי המחסום ואחרי חסימה על ידי נוגדנים.'],
    ['מאגר מגויס', (rTx.engaged_fraction * 100).toFixed(1), '%',
     'מנה גדולה פוגשת יותר שטח מעי, לא ריכוז גבוה יותר.'],
  ].map(([k, val, u, why]) =>
    `<div class="metric"><span class="k">${k}</span>` +
    `<span class="v">${val} <span class="u">${u}</span></span>` +
    `<span class="why">${why}</span></div>`).join('');

  drawHistamine(rTx.trace, rBase.trace);
  updateCell(treated, params, rTx);

  const edBase = elicitingDose(state.patient, PARAMS);
  const edTx = hasTx ? elicitingDose(treated, params) : edBase;
  const fold = edTx / edBase;

  $('shift').innerHTML =
    `<div class="side"><span class="k">סף היום</span>` +
    `<span class="v">${fmtMg(edBase)}</span>` +
    `<span class="sub">${milkEquivalent(edBase)}</span></div>` +
    `<div class="arrow">${hasTx ? '←' : '·'}<small>${
      hasTx ? (isFinite(fold) ? fold.toFixed(0) + '× גבוה יותר' : 'ללא תגובה') : 'ללא טיפול'
    }</small></div>` +
    `<div class="side"><span class="k">סף אחרי טיפול</span>` +
    `<span class="v" style="color:${hasTx ? 'var(--hema)' : 'var(--ink-3)'}">${fmtMg(edTx)}</span>` +
    `<span class="sub">${milkEquivalent(edTx)}</span></div>`;

  lastCharts = {
    histTx: rTx.trace, histBase: rBase.trace,
    base: doseResponse(state.patient, PARAMS, DOSE_LO, DOSE_HI, 46),
    treated: hasTx ? doseResponse(treated, params, DOSE_LO, DOSE_HI, 46) : null,
    edBase, edTx,
  };
  drawDoseResponse(lastCharts.base, lastCharts.treated, edBase, edTx);

  const rows = PROTOCOL_PANEL
    .map(([name, s]) => runProtocol(state.patient, s, name))
    .sort((a, b) => b.fold_shift - a.fold_shift);
  $('protocols').tBodies[0].innerHTML = rows.map(o =>
    `<tr><td>${o.label}</td><td class="n">${fmtMg(o.eliciting_dose_after_mg)} מ״ג</td>` +
    `<td class="n">${isFinite(o.fold_shift) ? o.fold_shift.toFixed(0) + '×' : '∞'}</td>` +
    `<td class="n">${o.igg4_mg_l.toFixed(1)}</td>` +
    `<td>${o.protects_against_a_glass
      ? '<span class="badge b-safe">כן</span>' : '<span class="badge b-danger">לא</span>'}</td></tr>`
  ).join('');

  renderLimits(edBase, rTx);
  lastResult = { challenge: rTx, eliciting_dose_before_mg: edBase,
                 eliciting_dose_after_mg: edTx, fold_shift: fold, dose_mg: state.dose };
  persist();
}

function renderLimits(edBase, r) {
  const notes = [];
  if (!isFinite(edBase))
    notes.push('המטופל הזה לא מגיב לשום מינון שנבדק — עד 100 גרם חלבון חלב. זו תשובה נכונה עבור מי שהחלים, לא פער במודל.');
  if (state.patient.specific_ige_ku > state.patient.total_ige_ku)
    notes.push('IgE ספציפי גדול מ-IgE כללי — המנוע חוסם את היחס ב-1, כי זה בלתי אפשרי פיזית.');
  if (r.free_allergen_m > 1.7e-8)
    notes.push('הריכוז חצה את שיא עקומת הקישור (17 nM). מכאן ומעלה עודף אלרגן דווקא מפחית קישור צולב — אפקט הפרוזון.');
  $('limits').innerHTML = notes.length
    ? '<b>שים לב.</b> ' + notes.join(' ')
    : '<b>איך לקרוא את זה.</b> הסף המעורר הוא המינון שבו הציון חוצה 3 — הנקודה שבה אתגר מזון קליני נחשב חיובי. כל שאר המספרים נגזרים ממנו, לא להפך.';
}

/* ---------------- the cell view ---------------- */

let cellView = null;

/* Bind the 3D view to what the engine just computed, and say in words what the
 * picture is showing. The narrative is generated from the same numbers — it is
 * a reading of the result, never an addition to it. */
function updateCell(patient, params, result) {
  // Receptor occupancy is the quantity the picture is really about, so compute
  // it the same way the engine does rather than eyeballing a proportion.
  const sensitised = sensitizedReceptors(
    patient.specific_ige_ku, patient.total_ige_ku,
    params.receptors_per_cell, params.k_occupancy_ku);
  const sensitisedFraction = sensitised / params.receptors_per_cell;
  // Two receptors per crosslink.
  const crosslinkedFraction = sensitised > 0
    ? Math.min(1, (result.crosslinks_per_cell * 2) / sensitised) : 0;

  if (cellView) {
    cellView.update({
      sensitised: sensitisedFraction,
      crosslinked: crosslinkedFraction,
      activation: result.activation,
      // Scale the allergen cloud against the crosslinking optimum, so the field
      // thickens across exactly the range that matters biologically.
      allergen: Math.min(1, Math.sqrt(result.free_allergen_m / 1.7e-8)),
      reaction: result.reaction,
    });
  }

  const big = $('cell-big');
  if (big) {
    big.textContent = Math.round(result.crosslinks_per_cell).toLocaleString('en-US');
    big.style.color = result.crosslinks_per_cell >= params.crosslink_threshold
      ? 'var(--gl-cross)' : 'var(--ink-2)';
  }

  const narrative = $('cell-narrative');
  if (!narrative) return;
  const pct = v => (v * 100).toFixed(v < 0.01 ? 2 : v < 0.1 ? 1 : 0) + '%';
  let story;
  if (sensitisedFraction < 1e-4) {
    story = 'כמעט אף קולטן לא נושא נוגדן שמזהה חלב. אין למה להיקשר, ולכן אין תגובה בשום מינון.';
  } else if (result.crosslinks_per_cell < 1) {
    story = `<b>${pct(sensitisedFraction)}</b> מהקולטנים נושאים IgE לחלב, אבל כמעט שום אלרגן לא מגיע — `
      + 'אף קולטן לא מגושר, והתא שקט.';
  } else if (!result.reaction) {
    story = `<b>${pct(sensitisedFraction)}</b> מהקולטנים חמושים ב-IgE לחלב, `
      + `ואלרגן גישר <b>${Math.round(result.crosslinks_per_cell)}</b> מהם. `
      + `זה מתחת לסף של ${params.crosslink_threshold} שדרוש כדי להפעיל את התא — `
      + 'משהו קורה, אבל לא מספיק כדי להרגיש.';
  } else if (result.systemic_score > result.local_score) {
    story = `אלרגן גישר <b>${Math.round(result.crosslinks_per_cell)}</b> זוגות קולטנים — `
      + `הרבה מעל הסף. <b>${pct(result.activation)}</b> מהתאים משחררים את תכולתם, `
      + `ההיסטמין בפלזמה מגיע ל-<b>${result.peak_histamine.toFixed(1)} ng/mL</b>, `
      + 'וזו כבר תגובה מערכתית — לא רק מקומית.';
  } else {
    story = `אלרגן גישר <b>${Math.round(result.crosslinks_per_cell)}</b> זוגות קולטנים, `
      + `<b>${pct(result.activation)}</b> מהתאים מדגרנלים. `
      + 'הכמות שנספגה קטנה מדי לתגובה מערכתית, אבל מספיקה לסימפטומים מקומיים במעי.';
  }
  narrative.innerHTML = story;
}

let pending = 0;
function schedule() {
  if (pending) return;
  pending = requestAnimationFrame(() => { pending = 0; recompute(); });
}

/* ---------------- agent army ---------------- */

const STAGE_HE = {
  design: 'החוקר הראשי מתכנן פרוטוקולים',
  simulate: 'המנוע מריץ את הפרוטוקולים',
  review: 'המומחים קוראים את הפלט',
  judge: 'המבקר מדרג',
  optimise: 'המנוע מכייל את המינונים של המוביל',
};

let stream = null;

function stepEl(key, label, detail, stateName) {
  let el = document.querySelector(`.step[data-key="${key}"]`);
  if (!el) {
    el = document.createElement('div');
    el.className = 'step'; el.dataset.key = key;
    el.innerHTML = '<span class="dot"></span><div><div class="label"></div><div class="detail"></div></div>';
    $('timeline').appendChild(el);
  }
  el.dataset.state = stateName;
  el.querySelector('.label').textContent = label;
  if (detail !== null) el.querySelector('.detail').textContent = detail;
  return el;
}

function runAgents() {
  if (stream) stream.close();
  $('timeline').innerHTML = '';
  $('agent-error').innerHTML = '';
  $('agent-reviews').innerHTML = '';
  $('agent-verdict').innerHTML = '';
  $('agent-optimised').innerHTML = '';
  $('agent-results').hidden = true;
  $('run-agents').disabled = true;
  $('stop-agents').disabled = false;

  const query = new URLSearchParams({
    goal: $('goal').value.trim(),
    count: $('count').value,
    offline: $('offline').checked ? '1' : '0',
    pi: $('pi-model').value,
    review: $('review-model').value,
    judge: $('judge-model').value,
    patient: JSON.stringify(state.patient),
  });

  stream = new EventSource('/api/lab/stream?' + query);
  const on = (name, fn) => stream.addEventListener(name, e => fn(JSON.parse(e.data)));

  on('design', d => stepEl('design', STAGE_HE.design,
    d.offline ? 'offline — פאנל מובנה' : d.model, 'run'));

  on('designed', d => {
    stepEl('design', STAGE_HE.design,
      d.protocols.map(p => p.label).join(' · '), 'done');
    d.notes.forEach((note, i) => stepEl('note' + i, 'הערה', note, 'fail'));
  });

  on('simulate', d => stepEl('simulate', STAGE_HE.simulate, `${d.count} פרוטוקולים`, 'run'));

  on('simulated', d => {
    stepEl('simulate', STAGE_HE.simulate, `${d.outcomes.length} הורצו במנוע`, 'done');
    const rows = d.outcomes.slice().sort((a, b) => num(b.fold_shift) - num(a.fold_shift));
    $('agent-protocols').tBodies[0].innerHTML = rows.map(o =>
      `<tr><td>${o.label}</td><td class="n" style="font-size:.72rem">${o.steps.join('<br>')}</td>` +
      `<td class="n">${fmtMg(o.eliciting_dose_after_mg)} מ״ג</td>` +
      `<td class="n">${isFinite(num(o.fold_shift)) ? num(o.fold_shift).toFixed(0) + '×' : '∞'}</td>` +
      `<td>${o.protects_against_a_glass
        ? '<span class="badge b-safe">כן</span>' : '<span class="badge b-danger">לא</span>'}</td></tr>`
    ).join('');
    $('agent-results').hidden = false;
  });

  on('review', d => stepEl('review', STAGE_HE.review, d.model, 'run'));

  on('reviewed', d => {
    const ROLE_HE = { immunologist: 'אימונולוג', pharmacologist: 'פרמקולוג',
                      toxicologist: 'טוקסיקולוג' };
    const guard = d.untraceable.length
      ? `<span class="badge b-danger">${d.untraceable.length} מספרים לא ניתנים לייחוס: ${d.untraceable.join(', ')}</span>`
      : '<span class="badge b-safe">כל המספרים מיוחסים לפלט המנוע</span>';
    const box = document.createElement('div');
    box.className = 'review';
    box.innerHTML =
      `<div class="who"><strong>${ROLE_HE[d.role] || d.role}</strong>` +
      `<span class="via">${d.model}</span></div>` +
      `<div class="text"></div><div class="guard">${guard}</div>`;
    box.querySelector('.text').textContent = d.text;
    $('agent-reviews').appendChild(box);
    stepEl('review', STAGE_HE.review,
      `${$('agent-reviews').children.length}/3 חוות דעת`, 'run');
  });

  on('judge', d => {
    stepEl('review', STAGE_HE.review, `${$('agent-reviews').children.length}/3 חוות דעת`, 'done');
    stepEl('judge', STAGE_HE.judge, d.model, 'run');
  });

  on('judged', d => {
    stepEl('judge', STAGE_HE.judge, null, 'done');
    if (!d.text) return;
    const guard = d.untraceable.length
      ? `<span class="badge b-danger">${d.untraceable.length} מספרים לא ניתנים לייחוס</span>`
      : '<span class="badge b-safe">כל המספרים מיוחסים לפלט המנוע</span>';
    const box = document.createElement('div');
    box.className = 'card';
    box.innerHTML = '<h2>פסק המבקר</h2><div class="verdict-box"><div class="text"></div></div>' +
                    `<div class="guard" style="margin-top:.6rem">${guard}</div>`;
    box.querySelector('.text').textContent = d.text;
    $('agent-verdict').appendChild(box);
  });

  on('optimise', d => stepEl('optimise', STAGE_HE.optimise,
    `${d.protocol} — ${d.steps.join(', ')}`, 'run'));

  on('optimised', d => {
    if (d.error) {
      stepEl('optimise', STAGE_HE.optimise, d.error, 'fail');
      return;
    }
    stepEl('optimise', STAGE_HE.optimise,
      `נבדקו ${d.evaluations} הגדרות · ${d.rejected_unsafe} נפסלו כלא בטוחות`, 'done');
    renderOptimisation(d);
  });

  on('done', d => {
    stepEl('done', 'הסתיים', `${d.usage}${d.best ? ' · מוביל: ' + d.best : ''}`, 'done');
    finishAgents();
  });

  on('error', d => {
    $('agent-error').innerHTML = `<div class="err">${d.message}</div>`;
    finishAgents();
  });

  stream.onerror = () => {
    // EventSource fires this both on a network failure and on a clean server
    // close, so only surface it when nothing arrived at all.
    if (!$('timeline').children.length) {
      $('agent-error').innerHTML = '<div class="err">החיבור לשרת נכשל. השרת עדיין רץ?</div>';
    }
    finishAgents();
  };
}

/** Load a protocol the agents produced straight onto the bench, so a result can
 *  be taken apart by hand instead of only read. */
function applyStepsToBench(steps) {
  for (const lever of LEVER_UI) state.levers[lever.kind].on = false;
  for (const step of steps) {
    if (!state.levers[step.kind]) continue;
    Object.assign(state.levers[step.kind], step.params, { on: true });
  }
  $('levers').innerHTML = '';
  buildLevers();
  recompute();
  showTab('bench');
  toast('הפרוטוקול נטען לשולחן העבודה');
}

function renderOptimisation(d) {
  const host = $('agent-optimised');
  const fold = v => (isFinite(num(v)) ? num(v).toFixed(0) + '×' : '∞');
  const safe = d.safe_start
    ? '<span class="badge b-safe">בטוח להתחיל</span>'
    : '<span class="badge b-danger">מגיב כבר במנה הראשונה</span>';

  host.innerHTML =
    '<div class="card"><h2>כיול המנוע</h2>' +
    '<p class="hint">הסוכנים בחרו אילו מנגנונים לשלב. את המינונים המנוע מחפש בעצמו — ' +
    'בכפוף לאילוץ שהמומחים העלו בכל ריצה: מנת הפתיחה חייבת להיות מתחת לסף של המטופל.</p>' +
    `<div class="shift">
       <div class="side"><span class="k">כפי שהוצע</span>
         <span class="v">${fmtMg(d.before.eliciting_dose_after_mg)}</span>
         <span class="sub">${fold(d.before.fold_shift)} · ${d.steps_before ?? ''}</span></div>
       <div class="arrow">←<small>${d.evaluations} הגדרות נבדקו</small></div>
       <div class="side"><span class="k">אחרי כיול</span>
         <span class="v" style="color:var(--accent)">${fmtMg(d.after.eliciting_dose_after_mg)}</span>
         <span class="sub">${fold(d.after.fold_shift)}</span></div>
     </div>` +
    `<p style="font-size:.85rem;margin-bottom:.7rem">${safe} ` +
    `<span style="color:var(--ink-2)">${d.steps.map(describeStep).join(' · ')}</span></p>` +
    (d.notes?.length
      ? `<div class="note" style="margin-bottom:.8rem">${d.notes.map(n => '· ' + n).join('<br>')}</div>`
      : '') +
    '<button class="btn" id="apply-optimised">טען לשולחן העבודה</button></div>';

  $('apply-optimised').onclick = () => applyStepsToBench(d.steps);
}

function finishAgents() {
  if (stream) { stream.close(); stream = null; }
  $('run-agents').disabled = false;
  $('stop-agents').disabled = true;
}

/* ---------------- experiment log ---------------- */

async function refreshLog() {
  const entries = await api('/api/log');
  $('log-count').textContent = entries.length ? `${entries.length} ריצות` : '';
  const body = $('log-table').tBodies[0];
  if (!entries.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">עוד לא שמרת ריצות. ' +
      'הרץ משהו בשולחן העבודה ולחץ «שמור ליומן».</td></tr>';
    return;
  }
  body.innerHTML = entries.slice().reverse().map((e, i) => {
    const r = e.result || {};
    const when = e.saved_at ? new Date(e.saved_at).toLocaleString('he-IL') : '—';
    const fold = num(r.fold_shift);
    return `<tr><td class="n" style="font-size:.72rem">${when}</td>` +
      `<td>${(e.note || '').slice(0, 60) || '—'}</td>` +
      `<td class="n" style="font-size:.72rem">sIgE ${(e.patient?.specific_ige_ku ?? 0).toFixed(1)}<br>` +
      `barrier ${(e.patient?.mucosal_barrier ?? 1).toFixed(2)}×</td>` +
      `<td style="font-size:.72rem">${(e.steps || []).map(describeStep).join('<br>') || 'ללא'}</td>` +
      `<td class="n">${fmtMg(r.eliciting_dose_after_mg)} מ״ג</td>` +
      `<td class="n">${isFinite(fold) ? fold.toFixed(0) + '×' : '∞'}</td>` +
      `<td><button class="btn quiet" data-load="${i}" style="font-size:.72rem;padding:.25rem .55rem">טען</button></td></tr>`;
  }).join('');

  const reversed = entries.slice().reverse();
  body.querySelectorAll('button[data-load]').forEach(btn => {
    btn.onclick = () => {
      const entry = reversed[+btn.dataset.load];
      if (entry?.patient) Object.assign(state.patient, entry.patient);
      for (const lever of LEVER_UI) state.levers[lever.kind].on = false;
      for (const step of entry?.steps || []) {
        if (!state.levers[step.kind]) continue;
        Object.assign(state.levers[step.kind], step.params, { on: true });
      }
      $('levers').innerHTML = '';
      buildLevers();
      clearChips(); syncPatient(); recompute();
      showTab('bench');
      toast('הריצה נטענה לשולחן העבודה');
    };
  });
}

async function saveExperiment() {
  if (!lastResult) return;
  const note = ($('save-hint').dataset.note || '').trim();
  await api('/api/log/save', {
    saved_at: new Date().toISOString(),
    note: note || `${PRESET_HE[state.preset] || 'מותאם'} · ${fmtMg(state.dose)} מ״ג`,
    patient: state.patient,
    steps: activeSteps(),
    result: {
      eliciting_dose_before_mg: lastResult.eliciting_dose_before_mg,
      eliciting_dose_after_mg: isFinite(lastResult.eliciting_dose_after_mg)
        ? lastResult.eliciting_dose_after_mg : 'Infinity',
      fold_shift: isFinite(lastResult.fold_shift) ? lastResult.fold_shift : 'Infinity',
      dose_mg: lastResult.dose_mg,
      symptom_score: lastResult.challenge.symptom_score,
      peak_histamine: lastResult.challenge.peak_histamine,
      reaction: lastResult.challenge.reaction,
    },
  });
  toast('נשמר ליומן');
  refreshLog();
}

/* ---------------- parameters ---------------- */

let PROVENANCE = [];
const BADGE = { measured: 'b-hema', calibrated: 'b-caution',
                assumed: 'b-eosin', derived: 'b-safe' };

function renderParams() {
  const q = $('param-filter').value.trim().toLowerCase();
  const rows = PROVENANCE
    .filter(p => !q || `${p.name} ${p.provenance} ${p.source} ${p.note}`.toLowerCase().includes(q))
    .sort((a, b) => a.provenance.localeCompare(b.provenance) || a.name.localeCompare(b.name));
  const body = $('params-table').tBodies[0];
  body.innerHTML = rows.length ? rows.map(p =>
    `<tr><td class="n">${p.name}</td>` +
    `<td class="n">${Math.abs(p.value) < 0.001 || Math.abs(p.value) >= 1e5
      ? p.value.toExponential(3) : p.value}</td>` +
    `<td class="n">${p.unit}</td>` +
    `<td><span class="badge ${BADGE[p.provenance] || 'b-hema'}">${p.provenance}</span></td>` +
    `<td class="src">${p.source}</td>` +
    `<td class="src" style="max-width:26rem">${p.note || ''}</td></tr>`).join('')
    : '<tr><td colspan="6" class="empty">אין התאמות</td></tr>';
}

/* ---------------- tabs ---------------- */

const TABS = ['bench', 'agents', 'log', 'params'];

function showTab(name) {
  document.querySelectorAll('#nav button').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  // Only touch panels that are still in the document: the standalone build
  // removes the two that need a server.
  TABS.forEach(t => {
    const panel = $('panel-' + t);
    if (panel) panel.hidden = t !== name;
  });
  if (name === 'bench') schedule();
  if (name === 'log') refreshLog();
  location.hash = name;
}

/* ---------------- boot ---------------- */

const MODEL_CHOICES = [
  ['claude-fable-5', 'Claude Fable 5 — החזק ביותר'],
  ['claude-opus-4-8', 'Claude Opus 4.8'],
  ['claude-sonnet-5', 'Claude Sonnet 5'],
  ['gpt-5-6-sol', 'GPT-5.6 Sol'],
  ['gpt-5-6-luna', 'GPT-5.6 Luna — זול ומהיר'],
  ['grok-4-5', 'Grok 4.5'],
  ['grok-4-3', 'Grok 4.3 — הזול ביותר'],
  ['gemini-3.1-pro', 'Gemini 3.1 Pro'],
  ['gemini-3-5-flash', 'Gemini 3.5 Flash'],
];

async function boot() {
  restore();
  buildChips();
  buildLevers();
  syncPatient();
  $('dose').value = fromLog(state.dose, ...DOSE);
  labelDose();

  $('sige').oninput = e => { state.patient.specific_ige_ku = toLog(+e.target.value, ...SIGE); clearChips(); labelPatient(); schedule(); };
  $('tige').oninput = e => { state.patient.total_ige_ku = toLog(+e.target.value, ...TIGE); clearChips(); labelPatient(); schedule(); };
  $('barrier').oninput = e => { state.patient.mucosal_barrier = toLog(+e.target.value, ...BARRIER); clearChips(); labelPatient(); schedule(); };
  $('dose').oninput = e => { state.dose = toLog(+e.target.value, ...DOSE); labelDose(); schedule(); };

  MODEL_CHOICES.forEach(([value, label]) => {
    for (const [id, preferred] of [['pi-model', 'claude-fable-5'],
                                   ['review-model', 'gpt-5-6-luna'],
                                   ['judge-model', 'claude-opus-4-8']]) {
      const option = document.createElement('option');
      option.value = value; option.textContent = label;
      option.selected = value === preferred;
      $(id).appendChild(option);
    }
  });

  $('count').oninput = e => { $('count-v').textContent = e.target.value; };
  $('run-agents').onclick = runAgents;
  $('stop-agents').onclick = () => { finishAgents(); toast('הריצה נעצרה'); };
  $('save-exp').onclick = saveExperiment;
  $('refresh-log').onclick = refreshLog;
  $('clear-log').onclick = async () => {
    await api('/api/log/clear', {});
    toast('היומן נמחק'); refreshLog();
  };
  $('param-filter').oninput = renderParams;

  document.querySelectorAll('#nav button').forEach(b => {
    b.onclick = () => showTab(b.dataset.tab);
  });

  const cellCanvas = $('cell-canvas');
  if (cellCanvas) {
    cellView = createCellView(cellCanvas, { palette: glPalette() });
    if (!cellView) {
      // No WebGL. The bench is still fully usable — say so plainly instead of
      // leaving a dead rectangle on the page.
      cellCanvas.replaceWith(Object.assign(document.createElement('div'), {
        className: 'cell-fallback',
        textContent: 'הדפדפן הזה לא תומך ב-WebGL, אז תצוגת התא התלת־ממדית כבויה. '
          + 'כל שאר המעבדה עובדת רגיל.',
      }));
    }
  }

  bindDoseHover();

  // A window resize is not the only thing that changes a chart's width: showing
  // a tab, the column layout collapsing, a panel appearing. Observe the elements
  // themselves and repaint from cache — no engine work, so it stays smooth.
  if (typeof ResizeObserver === 'function') {
    let frame = 0;
    const observer = new ResizeObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; drawCurves(); });
    });
    for (const id of ['chart-hist', 'chart-dose']) {
      const canvas = $(id);
      if (canvas) observer.observe(canvas);
    }
  } else {
    window.addEventListener('resize', schedule);
  }

  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const onTheme = () => { cellView?.setPalette(glPalette()); schedule(); };
  (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(onTheme);

  recompute();

  // The standalone build has no server behind it, so the two tabs that need one
  // are removed rather than left to fail when clicked.
  const standalone = Boolean(globalThis.__STANDALONE__);
  if (standalone) {
    for (const tab of ['agents', 'log']) {
      document.querySelector(`#nav button[data-tab="${tab}"]`)?.remove();
      $('panel-' + tab)?.remove();
    }
    $('status').innerHTML =
      `<span class="pill on">מנוע · ${Object.keys(PARAMS).length} פרמטרים</span>`
      + '<span class="pill">גרסה עצמאית — בלי צבא הסוכנים</span>';
  }

  // Status and the citation table come from the server — the browser engine
  // carries the numbers but not their provenance.
  try {
    if (standalone) throw new Error('standalone');
    const health = await api('/api/health');
    // Report the provider the offered models actually use. An OpenRouter-only
    // setup would otherwise show "connected" while every call in the panel fails.
    const ready = health.default_models_reachable;
    const fleetLabel = ready ? 'צבא הסוכנים מחובר'
      : health.providers.openrouter ? 'יש רק מפתח OpenRouter — המודלים כאן דורשים KIE'
      : 'אין מפתח API — סוכנים offline';
    $('status').innerHTML =
      `<span class="pill on">מנוע · ${health.parameters} פרמטרים</span>` +
      `<span class="pill ${ready ? 'on' : 'off'}">${fleetLabel}</span>` +
      `<span class="pill">חוזק מקורות ${Math.round(health.trust * 100)}%</span>`;
    if (!ready) $('offline').checked = true;
  } catch {
    if (!standalone) $('status').innerHTML = '<span class="pill off">אין חיבור לשרת</span>';
  }

  // The standalone build bakes the citation table in, because it has no server
  // to ask. Either way the bench works — only the parameters tab needs this.
  if (Array.isArray(globalThis.__PROVENANCE__)) {
    PROVENANCE = globalThis.__PROVENANCE__;
    renderParams();
  } else {
    try {
      PROVENANCE = await api('/api/params');
      renderParams();
    } catch { /* the bench works without the citation table */ }
  }

  const initial = location.hash.slice(1);
  showTab(TABS.includes(initial) && $('panel-' + initial) ? initial : 'bench');

  // Every highlighted element except the nav buttons lives on the bench, and a
  // hidden panel has no position to point at — so make sure it is showing.
  const runTour = () => startTour({
    onStep: step => {
      if (step.target && !step.target.startsWith('#nav')) showTab('bench');
    },
  });
  $('help').onclick = runTour;
  if (!tourWasSeen()) setTimeout(runTour, 700);
}

/* A front end that fails during start-up paints a page that looks merely empty,
 * and says why only in a console nobody has open. Say it on the page. */
function reportBootFailure(error) {
  const message = (error && (error.stack || error.message)) || String(error);
  const banner = document.createElement('div');
  banner.className = 'err';
  banner.style.margin = '1rem';
  banner.innerHTML = '<strong>המעבדה לא הצליחה לעלות.</strong><br>'
    + '<span style="font-family:var(--f-mono);font-size:.78rem;white-space:pre-wrap"></span>';
  banner.querySelector('span').textContent = message;
  document.body.prepend(banner);
  const status = document.getElementById('status');
  if (status) status.innerHTML = '<span class="pill off">שגיאת טעינה</span>';
  console.error('Tikkun Lab failed to boot:', error);
}

window.addEventListener('error', e => reportBootFailure(e.error || e.message));
window.addEventListener('unhandledrejection', e => reportBootFailure(e.reason));

try {
  await boot();
} catch (error) {
  reportBootFailure(error);
}
