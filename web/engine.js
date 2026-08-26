/* Tikkun Lab engine — JavaScript port of engine/ for the browser lab.
 *
 * The browser cannot call Python, so this reimplements the same equations. A port
 * that drifts from the original is worse than no port: it would still produce
 * confident numbers, just different ones. `verify.mjs` checks every value here
 * against `fixture.json`, generated from the Python engine.
 *
 * Differences from the Python, both deliberate:
 *   - fixed-step RK4 instead of SciPy's LSODA, so the browser needs no solver
 *   - step sizes chosen to hold agreement inside 0.5% on every fixture value
 *
 * Everything else — the parameters, the equilibria, the symptom scale — is the
 * same arithmetic in the same order.
 */

const LN2 = Math.log(2);

export const PARAMS = {
  blg_mw: 18300,
  blg_fraction: 0.10,
  k_bind: 3.0e7,
  k_cross: 2.2e-4,
  receptors_per_cell: 2.4e5,
  k_occupancy_ku: 120.0,
  crosslink_threshold: 100.0,
  crosslink_hill: 2.0,
  mucosal_cmax: 6.89949e-8,
  mucosal_km: 5000.0,
  recruit_km: 300.0,
  absorption_tmax: 600.0,
  k_degranulate: 0.012,
  histamine_yield: 28.6331,
  histamine_halflife: 90.0,
  histamine_baseline: 0.5,
  symptom_ec50: 4.0,
  symptom_hill: 1.8,
  symptom_max: 10.0,
  reaction_threshold: 3.0,
  local_weight: 0.55,
  k_igg4: 1.0e7,
  igg4_mw: 146000,
  igg4_baseline: 2.0e-9,
  igg4_halflife: 21.0,
  igg4_kprod: 1.8898e-8,
  igg4_dose_km: 120.0,
  sige_halflife: 2.5,
  total_ige_ku: 420.0,
  treg_kind: 0.0640061,
  treg_halflife: 60.0,
  treg_dose_km: 80.0,
  treg_suppression: 0.75,
};

export const PATIENTS = {
  exquisite: { label: 'exquisite', specific_ige_ku: 100, total_ige_ku: 300, igg4_m: 2e-9, treg: 0, mucosal_barrier: 2.5 },
  default: { label: 'default', specific_ige_ku: 15, total_ige_ku: 400, igg4_m: 2e-9, treg: 0, mucosal_barrier: 1.0 },
  moderate: { label: 'moderate', specific_ige_ku: 5, total_ige_ku: 400, igg4_m: 2e-9, treg: 0, mucosal_barrier: 1.0 },
  outgrowing: { label: 'outgrowing', specific_ige_ku: 1.2, total_ige_ku: 350, igg4_m: 2e-9, treg: 0, mucosal_barrier: 1.0 },
};

export const GLASS_OF_MILK_MG = 8000;

/* ---- binding equilibria ---- */

export function hill(x, half, coef) {
  if (x <= 0) return 0;
  const xn = Math.pow(x, coef);
  return xn / (xn + Math.pow(half, coef));
}

/* Allergen still able to bridge two IgE after blocking IgG4 covers epitopes.
 * Quadratic in coverage: crosslinking needs two free epitopes on one molecule,
 * so covering 80% of epitopes removes 96% of bridging capacity. */
export function freeAllergen(totalM, igg4M, kIgg4) {
  if (totalM <= 0) return 0;
  if (kIgg4 <= 0 || igg4M <= 0) return totalM;
  const occupancy = (kIgg4 * igg4M) / (1 + kIgg4 * igg4M);
  return totalM * Math.pow(1 - occupancy, 2);
}

/* Dembo & Goldstein (1978) bivalent-ligand equilibrium, solved exactly.
 *   R_total = S + 2*K1*C*S + 2*Kx*K1*C*S^2 */
export function crosslinks(concM, receptors, kBind, kCross) {
  if (concM <= 0 || receptors <= 0 || kBind <= 0) return 0;
  const kc = kBind * concM;
  const a = 2 * kCross * kc;
  const b = 1 + 2 * kc;
  const free = a <= 0
    ? receptors / b
    : (-b + Math.sqrt(b * b + 4 * a * receptors)) / (2 * a);
  return kCross * kc * free * free;
}

export function sensitizedReceptors(specificKu, totalKu, perCell, kOccupancyKu) {
  if (totalKu <= 0 || specificKu <= 0) return 0;
  const occupancy = totalKu / (totalKu + kOccupancyKu);
  const specificFraction = Math.min(1, specificKu / totalKu);
  return perCell * occupancy * specificFraction;
}

/* ---- mucosal delivery ---- */

export function mucosalExposure(doseMg, p = PARAMS, barrier = 1) {
  if (doseMg <= 0) return 0;
  return barrier * p.mucosal_cmax * doseMg / (doseMg + p.mucosal_km);
}

export function engagedMastCells(doseMg, p = PARAMS) {
  if (doseMg <= 0) return 0;
  return doseMg / (doseMg + p.recruit_km);
}

/* ---- fixed-step RK4 ---- */

function rk4(deriv, y0, t0, t1, steps) {
  const h = (t1 - t0) / steps;
  let y = y0.slice();
  let t = t0;
  const trace = [[t, y.slice()]];
  for (let i = 0; i < steps; i++) {
    const k1 = deriv(t, y);
    const k2 = deriv(t + h / 2, y.map((v, j) => v + (h / 2) * k1[j]));
    const k3 = deriv(t + h / 2, y.map((v, j) => v + (h / 2) * k2[j]));
    const k4 = deriv(t + h, y.map((v, j) => v + h * k3[j]));
    y = y.map((v, j) => v + (h / 6) * (k1[j] + 2 * k2[j] + 2 * k3[j] + k4[j]));
    t += h;
    trace.push([t, y.slice()]);
  }
  return trace;
}

/* ---- one oral food challenge ---- */

export function challenge(patient, doseMg, p = PARAMS, options = {}) {
  const durationS = options.durationS ?? 7200;
  const steps = options.steps ?? 900;   // 8 s per step; verified against SciPy

  const plateau = mucosalExposure(doseMg, p, patient.mucosal_barrier ?? 1);
  const freeM = freeAllergen(plateau, patient.igg4_m, p.k_igg4);
  const receptors = sensitizedReceptors(
    patient.specific_ige_ku, patient.total_ige_ku,
    p.receptors_per_cell, p.k_occupancy_ku);
  const x = crosslinks(freeM, receptors, p.k_bind, p.k_cross);
  const activation = hill(x, p.crosslink_threshold, p.crosslink_hill);
  const engaged = engagedMastCells(doseMg, p);

  const kDeg = p.k_degranulate;
  const kAbsorb = 1 / p.absorption_tmax;
  const kClear = LN2 / p.histamine_halflife;
  const baseline = p.histamine_baseline;

  const deriv = (t, y) => {
    const [degranulated, histamine] = y;
    // Two sequential gut transit steps give an Erlang-2 arrival profile whose
    // slope starts at zero, so nothing reaches the mast cells in the first
    // moments. A single exponential fires within seconds of swallowing.
    const xt = kAbsorb * t;
    const arrived = plateau * (1 - (1 + xt) * Math.exp(-xt));
    const bridgeable = freeAllergen(arrived, patient.igg4_m, p.k_igg4);
    const active = hill(
      crosslinks(bridgeable, receptors, p.k_bind, p.k_cross),
      p.crosslink_threshold, p.crosslink_hill);
    const flux = kDeg * active * Math.max(0, engaged - degranulated);
    return [flux, p.histamine_yield * flux - kClear * (histamine - baseline)];
  };

  const trace = rk4(deriv, [0, baseline], 0, durationS, steps);

  let peak = baseline;
  let peakT = 0;
  for (const [t, y] of trace) {
    if (y[1] > peak) { peak = y[1]; peakT = t; }
  }

  const local = p.symptom_max * activation * p.local_weight;
  const systemic = p.symptom_max * hill(peak, p.symptom_ec50, p.symptom_hill);
  // Probabilistic OR: either channel alone can produce symptoms, and the two
  // together saturate rather than summing past the top of the scale.
  const combined = p.symptom_max
    * (1 - (1 - local / p.symptom_max) * (1 - systemic / p.symptom_max));

  return {
    dose_mg: doseMg,
    free_allergen_m: freeM,
    crosslinks_per_cell: x,
    activation,
    engaged_fraction: engaged,
    peak_histamine: peak,
    time_to_peak_s: peakT,
    local_score: local,
    systemic_score: systemic,
    symptom_score: combined,
    reaction: combined >= p.reaction_threshold,
    trace: options.keepTrace ? trace.map(([t, y]) => [t, y[1]]) : null,
  };
}

/* ---- eliciting dose ---- */

export function elicitingDose(patient, p = PARAMS, loMg = 1e-5, hiMg = 1e5, tol = 1e-3) {
  if (challenge(patient, loMg, p).reaction) return loMg;
  if (!challenge(patient, hiMg, p).reaction) return Infinity;
  let logLo = Math.log10(loMg);
  let logHi = Math.log10(hiMg);
  while (logHi - logLo > tol) {
    const mid = (logLo + logHi) / 2;
    if (challenge(patient, Math.pow(10, mid), p).reaction) logHi = mid;
    else logLo = mid;
  }
  return Math.pow(10, logHi);
}

export function doseResponse(patient, p = PARAMS, loMg = 1e-2, hiMg = 1e4, points = 60) {
  const doses = [];
  const scores = [];
  const a = Math.log10(loMg);
  const b = Math.log10(hiMg);
  for (let i = 0; i < points; i++) {
    const d = Math.pow(10, a + (b - a) * i / (points - 1));
    doses.push(d);
    scores.push(challenge(patient, d, p).symptom_score);
  }
  return { doses, scores };
}

/* ---- a course of oral immunotherapy ---- */

export function immunotherapy(patient, dailyDoseMg, days = 365, p = PARAMS, options = {}) {
  const steps = options.steps ?? 1460;   // 6 h per step

  const igg4Decay = LN2 / p.igg4_halflife;
  const tregDecay = LN2 / p.treg_halflife;
  const igeDecay = LN2 / p.sige_halflife;
  // Resting production, or an untreated patient decays toward zero antibody
  // instead of holding at their own baseline.
  const igeProd = igeDecay * patient.specific_ige_ku;
  const igg4ProdBaseline = igg4Decay * p.igg4_baseline;

  const signalIgg4 = hill(dailyDoseMg, p.igg4_dose_km, 1);
  const signalTreg = hill(dailyDoseMg, p.treg_dose_km, 1);

  const deriv = (_t, y) => {
    const [sige, igg4, treg] = y;
    return [
      igeProd * (1 - p.treg_suppression * treg) - igeDecay * sige,
      igg4ProdBaseline + p.igg4_kprod * signalIgg4 - igg4Decay * igg4,
      p.treg_kind * signalTreg * (1 - treg) - tregDecay * treg,
    ];
  };

  const trace = rk4(deriv,
    [patient.specific_ige_ku, patient.igg4_m, patient.treg ?? 0], 0, days, steps);
  const [, last] = trace[trace.length - 1];

  return {
    days: trace.map(([t]) => t),
    specific_ige_ku: trace.map(([, y]) => y[0]),
    igg4_m: trace.map(([, y]) => y[1]),
    treg: trace.map(([, y]) => y[2]),
    final: {
      ...patient,
      label: `${patient.label}+OIT`,
      specific_ige_ku: last[0],
      igg4_m: last[1],
      treg: last[2],
    },
  };
}

/* ---- interventions: the same closed vocabulary the agents use ---- */

export const LEVERS = {
  oral_immunotherapy: { fields: { daily_dose_mg: [0.1, 5000], days: [7, 1095] } },
  anti_ige: { fields: { free_ige_reduction: [0, 0.99] } },
  passive_igg4: { fields: { titre_mg_l: [0, 500] } },
  barrier_repair: { fields: { permeability_factor: [0.05, 1] } },
  mast_cell_stabiliser: { fields: { threshold_factor: [1, 50] } },
};

export function applyIntervention(step, patient, p) {
  const v = step.params;
  switch (step.kind) {
    case 'oral_immunotherapy':
      return [immunotherapy(patient, v.daily_dose_mg, v.days, p).final, p];
    case 'anti_ige': {
      const keep = 1 - v.free_ige_reduction;
      return [{ ...patient,
        specific_ige_ku: patient.specific_ige_ku * keep,
        total_ige_ku: patient.total_ige_ku * keep }, p];
    }
    case 'passive_igg4':
      return [{ ...patient,
        igg4_m: patient.igg4_m + v.titre_mg_l * 1e-3 / p.igg4_mw }, p];
    case 'barrier_repair':
      return [{ ...patient,
        mucosal_barrier: (patient.mucosal_barrier ?? 1) * v.permeability_factor }, p];
    case 'mast_cell_stabiliser':
      return [patient,
        { ...p, crosslink_threshold: p.crosslink_threshold * v.threshold_factor }];
    default:
      throw new Error(`unknown intervention ${step.kind}`);
  }
}

export function runProtocol(patient, steps, label = 'protocol', p = PARAMS) {
  const before = elicitingDose(patient, p);
  const glassBefore = challenge(patient, GLASS_OF_MILK_MG, p).symptom_score;

  let current = patient;
  let currentP = p;
  for (const step of steps) [current, currentP] = applyIntervention(step, current, currentP);

  const after = elicitingDose(current, currentP);
  const glassAfter = challenge(current, GLASS_OF_MILK_MG, currentP);

  return {
    label, steps,
    eliciting_dose_before_mg: before,
    eliciting_dose_after_mg: after,
    fold_shift: before > 0 ? after / before : Infinity,
    glass_score_before: glassBefore,
    glass_score_after: glassAfter.symptom_score,
    specific_ige_ku: current.specific_ige_ku,
    igg4_mg_l: current.igg4_m * currentP.igg4_mw * 1e3,
    treg: current.treg ?? 0,
    mucosal_barrier: current.mucosal_barrier ?? 1,
    protects_against_a_glass: !glassAfter.reaction,
  };
}
