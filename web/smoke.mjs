/* Smoke-test the built page.
 *
 * build.mjs already refuses to run if the engine has drifted. This checks the
 * assembled artefact itself: that both placeholders were filled, that the whole
 * inline script parses, and that the engine still computes correctly *after*
 * the `export` stripping — a transform that operates on source text and could
 * silently mangle it.
 *
 * Run: node web/smoke.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(here, 'lab.html'), 'utf8');
const fixture = JSON.parse(readFileSync(join(here, 'fixture.json'), 'utf8'));

const failures = [];
const ok = (name, condition, detail = '') =>
  condition ? console.log(`  PASS  ${name}${detail && '  ' + detail}`)
            : failures.push(`${name}${detail && '  ' + detail}`);

/* --- placeholders --- */
ok('no unreplaced placeholders', !/__[A-Z]+__(?!\s*=)/.test(page.replace(/globalThis\.__\w+__/g, '')));
ok('no leftover export statements', !/^export\s/m.test(page));

/* --- self-contained: the artifact host blocks every host but Google Fonts --- */
const externals = [...page.matchAll(/(?:src|href)="(https?:\/\/[^"]+)"/g)]
  .map(m => new URL(m[1]).host)
  .filter(h => !h.endsWith('fonts.googleapis.com') && !h.endsWith('fonts.gstatic.com'));
ok('no blocked external resources', externals.length === 0, externals.join(', '));

/* --- the whole inline script must parse --- */
const scripts = [...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
ok('page has exactly one inline script', scripts.length === 1);
let script;
try {
  // Wrapped in an async function because the page ships the bundle as a module,
  // where top-level await is legal; vm.Script alone parses it as a classic
  // script and would reject valid code.
  script = new vm.Script(`(async()=>{${scripts[0]}\n})()`, { filename: 'lab.html' });
  ok('inline script parses', true);
} catch (error) {
  failures.push(`inline script parse error: ${error.message}`);
}

/* --- the engine still computes after the module syntax is stripped --- */
if (script) {
  // Everything up to where app.js begins is engine + cell view; running only
  // that part keeps the check free of the DOM the app needs.
  const engineOnly = scripts[0].split('/* Tikkun Lab — application front end.')[0];
  const sandbox = {
    console, Math, Number, Object, JSON, Array, Infinity, globalThis: {},
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }) },
  };
  vm.createContext(sandbox);
  try {
    new vm.Script(engineOnly + `
      __out = {
        challenge: challenge(PATIENTS.default, 25),
        ed: elicitingDose(PATIENTS.default),
        oit: immunotherapy(PATIENTS.default, 300, 365).final,
        protocol: runProtocol(PATIENTS.default,
          [{kind:'anti_ige', params:{free_ige_reduction:0.95}}]),
      };`).runInContext(sandbox);

    const out = sandbox.__out;
    const want = fixture.challenges.find(c => c.patient === 'default' && c.dose_mg === 25);
    const near = (got, expected, tol = 0.005) =>
      Math.abs(got - expected) / Math.max(Math.abs(expected), 1e-12) <= tol;

    ok('inlined engine: challenge symptom score',
       near(out.challenge.symptom_score, want.symptom_score),
       `${out.challenge.symptom_score.toFixed(4)} vs ${want.symptom_score.toFixed(4)}`);
    ok('inlined engine: eliciting dose',
       near(out.ed, fixture.eliciting_doses.default, 0.01),
       `${out.ed.toFixed(3)} mg`);
    ok('inlined engine: immunotherapy IgG4',
       near(out.oit.igg4_m, fixture.immunotherapy.find(r =>
         r.daily_dose_mg === 300 && r.days === 365).igg4_m),
       `${(out.oit.igg4_m * 1e9).toFixed(1)} nM`);
    ok('inlined engine: protocols run', out.protocol.fold_shift > 1,
       `anti-IgE gives ${out.protocol.fold_shift.toFixed(1)}x`);
  } catch (error) {
    failures.push(`inlined engine threw: ${error.message}`);
  }
}

/* --- standalone specifics --- */
const provenance = page.match(/globalThis\.__PROVENANCE__ = (\[[\s\S]*?\]);/);
ok('citation table is baked in', Boolean(provenance),
   provenance ? `${JSON.parse(provenance[1]).length} parameters` : '');
ok('marked as standalone', page.includes('globalThis.__STANDALONE__ = true'));
ok('no leftover module imports', !/^\s*import\s+\{/m.test(scripts[0] ?? ''));
ok('carries no document skeleton the host supplies',
   !/<!doctype/i.test(page) && !/<html/i.test(page) && !/<body/i.test(page));

const bar = '='.repeat(70);
console.log(bar);
if (failures.length) {
  for (const line of failures) console.log(`  FAIL  ${line}`);
  console.log(bar);
  process.exit(1);
}
console.log('  built page is self-contained, parses, and computes correctly');
console.log(bar);
