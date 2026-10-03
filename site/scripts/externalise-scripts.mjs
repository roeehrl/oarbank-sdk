// Post-build: move every inline executable <script> in dist/**/*.html into a
// content-hashed file under dist/_inline/, and put <script src> in its place, so
// the site runs under script-src 'self' with no hashes and no 'unsafe-inline'.
//
// Behaviour is preserved:
//   - A classic <script src> without async/defer runs synchronously, in document
//     order, exactly where the inline script ran (document.currentScript still works).
//   - A module script keeps type="module" and stays deferred, in document order.
//     Module scripts execute once per URL, so a second identical inline module on
//     the same page gets a distinct query string (the file is the same).
//   - Every other attribute is kept. async/defer are dropped from classic scripts:
//     an inline classic script ignores them, a script with src would not.
// Data blocks (application/ld+json, application/json) stay inline: they never run.
//
// Then the gate: the build fails if any inline executable script, inline event
// handler or javascript: URL is left in any page.
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { BASE } from '../site.config.mjs';
import { DIST_DIR, rel, walk } from './lib/fs.mjs';
import { SCRIPT_RE, classify, cspProblems, parseAttrs } from './lib/html-scripts.mjs';

const OUT_DIR = join(DIST_DIR, '_inline');
mkdirSync(OUT_DIR, { recursive: true });

const written = new Map(); // hash → bytes
let moved = 0;
let pages = 0;
const failures = [];

for (const file of walk(DIST_DIR).filter((f) => f.endsWith('.html'))) {
  const html = readFileSync(file, 'utf8');
  const seenModules = new Map(); // hash → count on this page
  let changed = false;
  const out = html.replace(SCRIPT_RE, (whole, rawAttrs, body) => {
    const attrs = parseAttrs(rawAttrs);
    const kind = classify(attrs);
    if (kind !== 'classic' && kind !== 'module') return whole;
    changed = true;
    if (body.trim() === '') return ''; // an empty inline script does nothing
    // A relative import resolves against the page in an inline module but against
    // the file once moved, so refuse instead of silently changing what it loads.
    if (kind === 'module' && /(?:\bfrom|\bimport)\s*\(?\s*[`'"]\.{1,2}\//.test(body)) {
      failures.push(`${rel(DIST_DIR, file)}: inline module with a relative import cannot be moved to a file`);
      return whole;
    }
    const code = body.endsWith('\n') ? body : `${body}\n`;
    const hash = createHash('sha256').update(code).digest('hex').slice(0, 20);
    if (!written.has(hash)) {
      writeFileSync(join(OUT_DIR, `${hash}.js`), code);
      written.set(hash, Buffer.byteLength(code));
    }
    let src = `${BASE}/_inline/${hash}.js`;
    if (kind === 'module') {
      const n = (seenModules.get(hash) ?? 0) + 1;
      seenModules.set(hash, n);
      if (n > 1) src += `?n=${n}`;
    }
    moved++;
    const keep = attrs.filter((a) => kind === 'module' || (a.name !== 'async' && a.name !== 'defer'));
    const attrText = keep.map((a) => (a.value === null ? ` ${a.name}` : ` ${a.name}="${a.value}"`)).join('');
    return `<script src="${src}"${attrText}></script>`;
  });
  if (changed) {
    writeFileSync(file, out);
    pages++;
  }
  for (const p of cspProblems(out)) failures.push(`${rel(DIST_DIR, file)}: ${p}`);
}

console.log(
  `externalise: moved ${moved} inline scripts on ${pages} pages into ${written.size} files under ${BASE}/_inline/ ` +
    `(${[...written.values()].reduce((a, b) => a + b, 0)} bytes)`,
);
if (failures.length) {
  console.error(`CSP gate: ${failures.length} problem(s) left in the HTML:`);
  for (const f of failures) console.error(`  ${f}`);
  process.exit(1);
}
console.log('CSP gate: no inline executable scripts, event handlers or javascript: URLs left');
