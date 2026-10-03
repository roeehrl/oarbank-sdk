// Gates on the assembled deploy/ (the product) and deploy-hub/ (the hub). The
// build fails on any finding.
//
//   CSP gate      no inline executable <script>, inline event handler or
//                 javascript: URL in any page (the production CSP has no
//                 'unsafe-inline' for scripts).
//   Hygiene gate  nothing private or internal in anything published: see
//                 scripts/lib/hygiene-rules.mjs.
//   Search URLs   Pagefind results link to served URLs (no .html, which 307s).
//   Sitemap       every sitemap URL is a built page that may be indexed, under the
//                 production host; every indexable page is in the sitemap.
//   File limit    the Workers per-version file limit, per Worker.
//
// Usage: node scripts/check-dist.mjs [--require-private-terms]
import { existsSync, readFileSync } from 'node:fs';
import { extname, join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { BASE, HOME_URL, MAX_DEPLOY_FILES } from '../site.config.mjs';
import { DEPLOY_DIR, HUB_DEPLOY_DIR, SITE_DIR, rel, walk } from './lib/fs.mjs';
import { CONTENT_RULES, RAW_RULES, privateRule } from './lib/hygiene-rules.mjs';
import { SCRIPT_RE, classify, cspProblems, parseAttrs } from './lib/html-scripts.mjs';

for (const dir of [DEPLOY_DIR, HUB_DEPLOY_DIR]) {
  if (!existsSync(dir)) {
    console.error(`check-dist: no ${rel(SITE_DIR, dir)}/ directory; run npm run build first`);
    process.exit(1);
  }
}

const TEXT = new Set(['.html', '.md', '.txt', '.xml', '.json', '.js', '.mjs', '.css', '.svg', '']);
const CONTENT = new Set(['.html', '.md', '.txt', '.xml', '.json']);

const termsFile = join(SITE_DIR, '.hygiene-terms');
const termsText = process.env.DOCS_HYGIENE_TERMS ?? (existsSync(termsFile) ? readFileSync(termsFile, 'utf8') : '');
const priv = privateRule(termsText);
if (!priv) {
  const msg = 'hygiene: no private terms configured (site/.hygiene-terms or DOCS_HYGIENE_TERMS)';
  if (process.argv.includes('--require-private-terms')) {
    console.error(msg);
    process.exit(1);
  }
  console.warn(`${msg}; checking the generic rules only`);
}

/** Page content for the content rules: no executable scripts or styles; JSON-LD stays. */
function htmlContent(html) {
  return html
    .replace(/<style\b[\s\S]*?<\/style\s*>/gi, ' ')
    .replace(SCRIPT_RE, (whole, attrs) => (classify(parseAttrs(attrs)) === 'data' ? whole : ' '));
}

function scan(text, rules, where, out) {
  for (const rule of rules) {
    for (const m of text.matchAll(rule.re)) {
      const hit = m[0];
      if (rule.allow?.includes(hit) || rule.allowRe?.test(hit)) continue;
      const line = text.slice(0, m.index).split('\n').length;
      // CI logs may be public: never print a private term, even as context.
      const redact = (s) => (priv ? s.replace(priv.re, '(redacted)') : s);
      const context = redact(text.slice(Math.max(0, m.index - 40), m.index + hit.length + 40).replace(/\s+/g, ' '));
      out.push(`${where}:${line}: ${rule.name} "${rule === priv ? '(redacted)' : redact(hit)}" in …${context}…`);
    }
  }
}

const files = walk(DEPLOY_DIR);
const hubFiles = walk(HUB_DEPLOY_DIR);
const csp = [];
const hygiene = [];
let scanned = 0;
for (const file of [...files, ...hubFiles]) {
  const ext = extname(file).toLowerCase();
  if (!TEXT.has(ext)) continue;
  const where = rel(SITE_DIR, file);
  const text = readFileSync(file, 'utf8');
  scanned++;
  if (ext === '.html') for (const p of cspProblems(text)) csp.push(`${where}: ${p}`);
  scan(text, priv ? [...RAW_RULES, priv] : RAW_RULES, where, hygiene);
  if (CONTENT.has(ext)) scan(ext === '.html' ? htmlContent(text) : text, CONTENT_RULES, where, hygiene);
}

const search = files
  .filter((f) => f.endsWith('.pf_fragment'))
  .map((f) => [rel(DEPLOY_DIR, f), JSON.parse(gunzipSync(readFileSync(f)).toString('utf8').slice('pagefind_dcd'.length)).url])
  .filter(([, url]) => /\.html$/.test(url))
  .map(([f, url]) => `${f}: result URL ${url}`);

// Sitemap: URLs ↔ built pages. A page is indexable unless it has a robots noindex.
const productDir = join(DEPLOY_DIR, ...BASE.split('/').filter(Boolean));
const sitemapProblems = [];
const noindex = (html) => /<meta name="robots" content="[^"]*noindex/i.test(html);
const sitemapText = walk(productDir)
  .filter((f) => /\/sitemap-\d+\.xml$/.test(f))
  .map((f) => readFileSync(f, 'utf8'))
  .join('');
const listed = new Set([...sitemapText.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]));
for (const url of listed) {
  if (!url.startsWith(HOME_URL)) {
    sitemapProblems.push(`${url}: not under ${HOME_URL}`);
    continue;
  }
  const id = url.slice(HOME_URL.length);
  const file = join(productDir, `${id === '' ? 'index' : id}.html`);
  if (!existsSync(file)) sitemapProblems.push(`${url}: no page ${rel(SITE_DIR, file)}`);
  else if (noindex(readFileSync(file, 'utf8'))) sitemapProblems.push(`${url}: listed but noindex`);
}
for (const file of files.filter((f) => f.endsWith('.html'))) {
  const id = rel(productDir, file).replace(/\.html$/, '');
  if (id === '404' || noindex(readFileSync(file, 'utf8'))) continue;
  const url = id === 'index' ? HOME_URL : `${HOME_URL}${id}`;
  if (!listed.has(url)) sitemapProblems.push(`${url}: indexable page missing from the sitemap`);
}

const assets = files.filter((f) => !f.endsWith('_headers')).length;
const hubAssets = hubFiles.filter((f) => !/\/_(headers|redirects)$/.test(f)).length;
let failed = false;
const report = (name, problems, ok) => {
  if (problems.length) {
    failed = true;
    console.error(`${name}: ${problems.length} problem(s)`);
    for (const p of problems.slice(0, 200)) console.error(`  ${p}`);
  } else console.log(`${name}: ${ok}`);
};
report('CSP gate', csp, 'no inline executable scripts, event handlers or javascript: URLs');
report(
  'hygiene gate',
  hygiene,
  `clean (${scanned} text files; ${CONTENT_RULES.length + RAW_RULES.length} generic rules${priv ? ` + ${priv.count} private terms` : ''})`,
);
report('search URLs', search, 'no result URL ends in .html');
report('sitemap', sitemapProblems, `${listed.size} URLs, every one an indexable page, every indexable page listed`);
report(
  'file limit',
  [assets, hubAssets].some((n) => n > MAX_DEPLOY_FILES) ? [`${assets} or ${hubAssets} files > ${MAX_DEPLOY_FILES}`] : [],
  `product ${assets} files, hub ${hubAssets} files (limit ${MAX_DEPLOY_FILES} each)`,
);
process.exit(failed ? 1 : 0);
