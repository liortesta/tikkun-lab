/* Assemble the standalone browser lab from the application itself.
 *
 * One design, not two. An earlier version kept a separate template for the
 * shareable page, and the two drifted the moment either was restyled. This
 * inlines the very files the app serves — app.html, app.js, cell3d.js,
 * engine.js — so the standalone page is the app minus the parts that need a
 * server, and it cannot fall behind.
 *
 * The published page has to be a single self-contained file: the artifact host
 * blocks every external request bar Google Fonts, and ES module imports across
 * files would each be a request.
 *
 * Run: node web/build.mjs
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);

/* Refuse to build an engine that has not been proven to match the Python one.
 * A page that silently ships a drifted port is the exact failure this whole
 * project exists to avoid. */
for (const check of ['verify.mjs', 'frontend-check.mjs']) {
  try {
    execFileSync(process.execPath, [join(here, check)], { stdio: 'pipe' });
  } catch (error) {
    console.error(`${check} failed — refusing to build.\n`);
    console.error(error.stdout?.toString() ?? error.message);
    process.exit(1);
  }
}

/** Strip module syntax so several files can share one inline script scope. */
function flatten(name) {
  return readFileSync(join(here, name), 'utf8')
    .replace(/^\s*import[\s\S]*?from\s*'[^']+';\s*$/gm, '')  // resolved by concatenation
    .replace(/^export\s+/gm, '');
}

/* Provenance, read straight out of the Python registry so the page cannot claim
 * a citation the engine does not have. */
const provenance = execFileSync('python', ['-c', `
import json, sys
sys.path.insert(0, r"${root.replace(/\\/g, '\\\\')}")
from engine import MILK
json.dump([{"name": k, "value": p.value, "unit": p.unit,
            "provenance": p.provenance.value, "source": p.source, "note": p.note}
           for k, p in MILK.items()], sys.stdout)
`], { encoding: 'utf8', cwd: root });

// Order matters: engine and cell view define what app.js calls.
const bundle = [
  'globalThis.__STANDALONE__ = true;',
  `globalThis.__PROVENANCE__ = ${provenance};`,
  flatten('engine.js'),
  flatten('cell3d.js'),
  flatten('app.js'),
].join('\n');

const page = readFileSync(join(here, 'app.html'), 'utf8')
  // The artifact host wraps the file in its own document skeleton.
  .replace(/^<!doctype html>\s*<html[^>]*>\s*<head>\s*/i, '')
  .replace(/\s*<\/head>\s*<body>\s*/i, '\n')
  .replace(/\s*<\/body>\s*<\/html>\s*$/i, '\n')
  .replace(/<meta[^>]*>\s*/gi, '')
  .replace(/<link rel="icon"[^>]*>\s*/i, '')
  .replace('<script type="module" src="./app.js"></script>',
           `<script type="module">\n${bundle}\n</script>`)
  // Direction comes from the html element, which the host owns, so restate it.
  .replace(':root{', ':root{direction:rtl;');

// Two names for one build: `lab.html` is the working output, `milk-lab.html` is
// the path the published artifact is bound to. Writing both keeps a republish
// from silently shipping a stale page.
const out = join(here, 'lab.html');
writeFileSync(out, page, 'utf8');
writeFileSync(join(here, 'milk-lab.html'), page, 'utf8');

const kb = (Buffer.byteLength(page, 'utf8') / 1024).toFixed(1);
console.log(`built ${out}`);
console.log(`  ${kb} KB — app, cell view and engine inlined, `
  + `${JSON.parse(provenance).length} parameters`);
