/* Check the front end without a browser.
 *
 * A screenshot proves a page painted something. It does not prove the module
 * graph resolved, and the realistic way this front end breaks is a syntax error
 * or a bad import — which paints a blank page and logs to a console nobody is
 * reading. This loads app.js the way a browser would, against a DOM shim, and
 * fails loudly if boot throws.
 *
 * Run: node web/frontend-check.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const failures = [];
const ok = (name, condition, detail = '') =>
  condition ? console.log(`  PASS  ${name}${detail && '  ' + detail}`)
            : failures.push(`${name}${detail && '  ' + detail}`);

const html = readFileSync(join(here, 'app.html'), 'utf8');

/* --- every id app.js reaches for must exist in the markup --- */
const source = readFileSync(join(here, 'app.js'), 'utf8');
const wanted = new Set([...source.matchAll(/\$\('([\w-]+)'\)/g)].map(m => m[1]));
const present = new Set([...html.matchAll(/\bid="([\w-]+)"/g)].map(m => m[1]));
// Ids the code creates at runtime rather than finding in the markup. Listed one
// by one on purpose: a blanket exemption would swallow a genuine typo.
const RUNTIME_IDS = new Set(['apply-optimised']);
const built = [...source.matchAll(/id="?\$\{?(?:sid|id)/g)].length > 0;
const missing = [...wanted].filter(
  id => !present.has(id) && !RUNTIME_IDS.has(id) && !/^lv-/.test(id));
ok('every element id used by app.js exists in app.html', missing.length === 0,
   missing.length ? `missing: ${missing.join(', ')}` : `${wanted.size} ids checked`);
ok('dynamic control ids are generated, not hardcoded', built);

/* --- boot the module against a DOM shim --- */
/* Shimming rather than merely parsing, because parsing only proves the file is
 * syntactically valid. Booting proves the whole start-up path runs: state
 * restore, control construction, the first recompute, every chart draw. Those
 * are where a real front-end bug lives, and in a browser they fail into a blank
 * page and a console nobody is watching. */
installDomShim();
const rejections = [];
process.on('unhandledRejection', reason => rejections.push(reason));

let booted = true;
let bootError = null;
try {
  await import('./app.js');
  await new Promise(resolve => setTimeout(resolve, 250));  // let boot()'s awaits settle
} catch (error) {
  booted = false;
  bootError = error;
}
ok('app.js boots without throwing', booted, bootError ? String(bootError) : '');
ok('boot leaves no unhandled rejection', rejections.length === 0,
   rejections.map(String).join('; '));
ok('the first recompute produced a verdict',
   Boolean(globalThis.__ids['verdict-score']?.innerHTML),
   `score rendered: ${globalThis.__ids['verdict-score']?.innerHTML ?? '(nothing)'}`);
ok('the metric grid was filled',
   (globalThis.__ids.metrics?.innerHTML ?? '').includes('metric'));
ok('the protocol comparison table was filled',
   (globalThis.__ids.protocols?.tBodies[0].innerHTML ?? '').includes('<tr>'));
ok('both charts drew', globalThis.__canvasOps > 50,
   `${globalThis.__canvasOps} canvas operations`);

/* --- the first-run tour has to survive boot too --- */
const tour = await import('./tour.js');
let tourOk = true;
let tourError = '';
try {
  tour.startTour().close();
} catch (error) {
  tourOk = false;
  tourError = String(error);
}
ok('the guided tour opens and closes', tourOk, tourError);
/* A tour step that points at nothing dims the page and highlights empty space,
 * so check each selector's anchor really exists in the markup. Matched by the
 * distinctive part rather than the literal string, since attribute order in the
 * selector need not match attribute order in the HTML. */
ok('every tour step points at something that exists', (() => {
  const targets = [...readFileSync(join(here, 'tour.js'), 'utf8')
    .matchAll(/target:\s*'([^']+)'/g)].map(m => m[1]);
  const bad = targets.filter(selector => {
    const byId = selector.match(/^#([\w-]+)$/);
    if (byId) return !present.has(byId[1]);
    const byAttr = selector.match(/\[([\w-]+)="([^"]+)"\]/);
    if (byAttr) return !html.includes(`${byAttr[1]}="${byAttr[2]}"`);
    return !html.includes(selector);
  });
  tourError = bad.join(', ');
  return bad.length === 0;
})(), tourError);

/* --- everything app.js imports from engine.js must actually be exported --- */
const engine = await import('./engine.js');
const imported = source.match(/import\s*\{([\s\S]*?)\}\s*from\s*'\.\/engine\.js'/);
ok('app.js imports from engine.js', Boolean(imported));
if (imported) {
  const names = imported[1].split(',').map(s => s.trim()).filter(Boolean);
  const absent = names.filter(name => engine[name] === undefined);
  ok('every imported symbol is exported by engine.js', absent.length === 0,
     absent.length ? `missing: ${absent.join(', ')}` : `${names.length} symbols`);
}

/* --- the html must load the module, and load nothing the CSP would block --- */
ok('app.html loads app.js as a module',
   /<script type="module" src="\.\/app\.js">/.test(html));
const hosts = [...html.matchAll(/(?:src|href)="https?:\/\/([^/"]+)/g)].map(m => m[1]);
const external = hosts.filter(h => !h.endsWith('fonts.googleapis.com')
                                && !h.endsWith('fonts.gstatic.com'));
ok('app.html loads no unexpected external host', external.length === 0,
   external.join(', '));

/* --- tabs must line up with the panels they reveal --- */
const tabs = [...html.matchAll(/data-tab="(\w+)"/g)].map(m => m[1]);
const panels = [...html.matchAll(/id="panel-(\w+)"/g)].map(m => m[1]);
ok('every tab has a panel', tabs.every(t => panels.includes(t)),
   `tabs: ${tabs.join(', ')}`);
ok('every panel has a tab', panels.every(p => tabs.includes(p)));

/* --- the levers the UI offers must be levers the engine can run --- */
const uiKinds = [...source.matchAll(/kind:\s*'(\w+)'/g)].map(m => m[1]);
const unknown = [...new Set(uiKinds)].filter(k => !engine.LEVERS[k]);
ok('every intervention the UI offers is one the engine implements',
   unknown.length === 0, unknown.length ? `unknown: ${unknown.join(', ')}` : `${new Set(uiKinds).size} kinds`);

/* --- and their slider ranges must sit inside the engine's accepted ranges --- */
const rangeIssues = [];
for (const [kind, lever] of Object.entries(engine.LEVERS)) {
  const block = source.match(new RegExp(`kind:\\s*'${kind}'[\\s\\S]*?\\}\\]\\s*\\}`, 'm'));
  if (!block) continue;
  for (const [, key, lo, hi] of block[0].matchAll(
      /key:\s*'(\w+)',[^}]*?range:\s*\[([-\d.]+),\s*([-\d.]+)\]/g)) {
    const bounds = lever.fields[key];
    if (!bounds) { rangeIssues.push(`${kind}.${key} is not an engine field`); continue; }
    if (+lo < bounds[0] || +hi > bounds[1]) {
      rangeIssues.push(`${kind}.${key} slider ${lo}-${hi} exceeds engine ${bounds[0]}-${bounds[1]}`);
    }
  }
}
ok('no slider can produce a value the engine would reject',
   rangeIssues.length === 0, rangeIssues.join('; '));

const bar = '='.repeat(70);
console.log(bar);
if (failures.length) {
  for (const line of failures) console.log(`  FAIL  ${line}`);
  console.log(bar);
  process.exit(1);
}
console.log('  front end is wired correctly and boots clean');
console.log(bar);


/* ------------------------------------------------------------------ */
/* A DOM small enough to read, large enough to boot the app.           */
/* ------------------------------------------------------------------ */

function installDomShim() {
  globalThis.__canvasOps = 0;
  const ids = globalThis.__ids = Object.create(null);

  const canvasContext = new Proxy({}, {
    get(_target, name) {
      if (name === 'setTransform' || name === 'clearRect') {
        return () => { globalThis.__canvasOps++; };
      }
      // Every 2D call is a no-op that counts itself, so the check can tell a
      // chart that drew from one that silently skipped.
      return () => { globalThis.__canvasOps++; };
    },
    set() { globalThis.__canvasOps++; return true; },
  });

  function element(id = '') {
    const node = {
      id, value: '50', textContent: '', innerHTML: '', className: '',
      checked: false, disabled: false, hidden: false,
      dataset: {}, style: {}, children: [],
      clientWidth: 600, clientHeight: 180, width: 600, height: 180,
      tBodies: [{ innerHTML: '' }],
      appendChild(child) { this.children.push(child); return child; },
      setAttribute(name, value) { this[name] = value; },
      getAttribute(name) { return this[name]; },
      addEventListener() {},
      removeEventListener() {},
      classList: { add() {}, remove() {}, toggle() {} },
      querySelector(selector) { return element(selector); },
      querySelectorAll() { return []; },
      remove() {},
      prepend() {},
      scrollIntoView() {},
      getBoundingClientRect() {
        return { top: 100, left: 100, right: 400, bottom: 200, width: 300, height: 100 };
      },
      // No WebGL here, so this also exercises the cell view's fallback path —
      // the page must stay fully usable on a machine without it.
      getContext(kind) { return kind === '2d' ? canvasContext : null; },
      replaceWith() {},
      focus() {},
      setPointerCapture() {},
    };
    return node;
  }

  globalThis.document = {
    // Strict on purpose: only ids that really appear in app.html resolve, and
    // everything else returns null exactly as a browser would. An earlier
    // permissive version invented an element for any id asked for, which hid a
    // real bug — a lookup by id against a subtree that had not been appended
    // yet, which threw on the live page and passed here.
    getElementById(id) {
      if (!present.has(id)) return null;
      return ids[id] ??= element(id);
    },
    createElement() { return element(); },
    querySelectorAll() { return []; },
    // Null, like a browser with no match. Returning a stub for any selector
    // made `document.querySelector('.tour-root')` look like an existing tour on
    // a fresh page, which is the opposite of the truth.
    querySelector() { return null; },
    addEventListener() {},
    removeEventListener() {},
    documentElement: element('root'),
    body: element('body'),
  };

  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#5A34A0' });
  globalThis.localStorage = {
    _store: {},
    getItem(key) { return this._store[key] ?? null; },
    setItem(key, value) { this._store[key] = String(value); },
    removeItem(key) { delete this._store[key]; },
  };
  globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
  globalThis.cancelAnimationFrame = id => clearTimeout(id);
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  globalThis.EventSource = class { close() {} addEventListener() {} };
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  globalThis.location = { hash: '', href: 'http://127.0.0.1:8756/' };
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.window = globalThis;
  globalThis.devicePixelRatio = 1;

  // The server is not running during this check, so every fetch fails. app.js
  // has to survive that: the bench is meant to work without the server, and if
  // a failed /api/health takes the whole page down, this catches it.
  globalThis.fetch = async () => { throw new Error('offline (expected in this check)'); };
}
