/* Prove the JavaScript engine agrees with the Python one.
 *
 * The browser lab runs on the port in engine.js. If that port drifts from the
 * Python engine, the lab keeps producing confident numbers — just different
 * ones from the ones that were calibrated and validated. This compares every
 * value in fixture.json, which `python web/fixture.py` generates.
 *
 * Run: node web/verify.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  PARAMS, challenge, elicitingDose, immunotherapy,
} from './engine.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, 'fixture.json'), 'utf8'));

const TOLERANCE = 0.005;   // 0.5% — the RK4 step sizes are chosen to hold this
let checked = 0;
const failures = [];

function agrees(name, got, want, tol = TOLERANCE) {
  checked++;
  const expected = want === 'Infinity' ? Infinity : want;
  if (!Number.isFinite(expected) || !Number.isFinite(got)) {
    if (expected !== got) failures.push(`${name}: got ${got}, expected ${expected}`);
    return;
  }
  const scale = Math.max(Math.abs(expected), 1e-12);
  const error = Math.abs(got - expected) / scale;
  if (error > tol) {
    failures.push(
      `${name}: got ${got.toPrecision(6)}, expected ${Number(expected).toPrecision(6)} `
      + `(${(error * 100).toFixed(2)}% off)`);
  }
}

/* --- parameters must be identical, not merely close --- */
for (const [name, value] of Object.entries(fixture.params)) {
  checked++;
  if (PARAMS[name] === undefined) {
    failures.push(`param ${name}: missing from the JavaScript port`);
  } else if (PARAMS[name] !== value) {
    failures.push(`param ${name}: JS has ${PARAMS[name]}, Python has ${value}`);
  }
}

/* --- challenges --- */
for (const row of fixture.challenges) {
  const patient = fixture.patients[row.patient];
  const got = challenge(patient, row.dose_mg);
  const where = `${row.patient} @ ${row.dose_mg}mg`;
  agrees(`${where} free_allergen`, got.free_allergen_m, row.free_allergen_m);
  agrees(`${where} crosslinks`, got.crosslinks_per_cell, row.crosslinks_per_cell);
  agrees(`${where} activation`, got.activation, row.activation);
  agrees(`${where} peak_histamine`, got.peak_histamine, row.peak_histamine);
  agrees(`${where} symptom_score`, got.symptom_score, row.symptom_score);
  checked++;
  if (got.reaction !== row.reaction) {
    failures.push(`${where} reaction: got ${got.reaction}, expected ${row.reaction}`);
  }
  // Time-to-peak is only a real quantity when histamine actually rises. Below a
  // patient's threshold the curve is flat to within floating-point noise — at
  // 0.05 mg the rise is around 1e-8% of baseline — and the argmax lands wherever
  // rounding puts it. Comparing it there tests the rounding, not the model.
  // Above the threshold it is read off a discrete grid in both engines, so
  // allow the coarser step size rather than a percentage.
  const rise = (row.peak_histamine - PARAMS.histamine_baseline)
    / PARAMS.histamine_baseline;
  if (rise > 0.01) {
    checked++;
    if (Math.abs(got.time_to_peak_s - row.time_to_peak_s) > 30) {
      failures.push(`${where} time_to_peak: got ${got.time_to_peak_s}s, `
        + `expected ${row.time_to_peak_s}s`);
    }
  }
}

/* --- eliciting doses --- */
for (const [name, want] of Object.entries(fixture.eliciting_doses)) {
  agrees(`eliciting dose ${name}`, elicitingDose(fixture.patients[name]), want, 0.01);
}

/* --- immunotherapy --- */
for (const row of fixture.immunotherapy) {
  const course = immunotherapy(fixture.patients.default, row.daily_dose_mg, row.days);
  const where = `OIT ${row.daily_dose_mg}mg/${row.days}d`;
  agrees(`${where} sIgE`, course.final.specific_ige_ku, row.specific_ige_ku);
  agrees(`${where} IgG4`, course.final.igg4_m, row.igg4_m);
  agrees(`${where} Treg`, course.final.treg, row.treg);
  agrees(`${where} threshold after`, elicitingDose(course.final),
    row.eliciting_dose_after_mg, 0.01);
}

/* --- determinism --- */
checked++;
if (challenge(fixture.patients.default, 137).symptom_score
    !== challenge(fixture.patients.default, 137).symptom_score) {
  failures.push('determinism: two identical runs disagreed');
}

const bar = '='.repeat(70);
console.log(bar);
console.log('JavaScript port vs Python engine');
console.log(bar);
if (failures.length) {
  for (const line of failures.slice(0, 25)) console.log(`  FAIL  ${line}`);
  if (failures.length > 25) console.log(`  ... and ${failures.length - 25} more`);
  console.log(bar);
  console.log(`  ${checked - failures.length}/${checked} agree — PORT HAS DRIFTED`);
  process.exit(1);
}
console.log(`  ${checked}/${checked} values agree within ${TOLERANCE * 100}%`);
console.log(bar);
