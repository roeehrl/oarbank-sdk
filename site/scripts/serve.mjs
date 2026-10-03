// Local preview of the production docs host: serves deploy/ and deploy-hub/ the way
// the two assets-only Workers do on docs.codonic.dev.
//
//   /oarbank/*        the product Worker (deploy/): its route runs ahead of the hub
//   everything else   the hub Worker (deploy-hub/)
//
// Each Worker: html_handling "auto-trailing-slash", not_found_handling "404-page",
// its own _headers (with the production CSP) and _redirects. Good enough to click
// through the built site; the Cloudflare behaviour itself was verified on a test host.
//
// Usage: node scripts/serve.mjs [port]   (default 4321)
import { existsSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize } from 'node:path';
import { BASE } from '../site.config.mjs';
import { DEPLOY_DIR, HUB_DEPLOY_DIR } from './lib/fs.mjs';

const PORT = Number(process.argv[2] ?? process.env.PORT ?? 4321);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.wasm': 'application/wasm',
  '.woff2': 'font/woff2',
};

/** `_headers`: [{ re, headers: [[name, value]] }] in file order. */
function parseHeaders(file) {
  if (!existsSync(file)) return [];
  const rules = [];
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim() || line.trim().startsWith('#')) continue;
    if (!/^\s/.test(line)) {
      const re = new RegExp(`^${line.trim().replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`);
      rules.push({ re, headers: [] });
    } else rules.at(-1)?.headers.push(line.trim().split(/:\s*(.*)/s).slice(0, 2));
  }
  return rules;
}

/** `_redirects`: [{ from, to, status }]. */
function parseRedirects(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter((l) => l.trim() && !l.trim().startsWith('#'))
    .map((l) => {
      const [from, to, status = '302'] = l.trim().split(/\s+/);
      return { from, to, status: Number(status) };
    });
}

function worker(dir) {
  return { dir, headers: parseHeaders(join(dir, '_headers')), redirects: parseRedirects(join(dir, '_redirects')) };
}
const product = worker(DEPLOY_DIR);
const hub = worker(HUB_DEPLOY_DIR);

const isFile = (p) => existsSync(p) && statSync(p).isFile();

/** auto-trailing-slash: → { file } to serve, or { redirect } (307), or null (miss). */
function resolve(dir, path) {
  const fsPath = (p) => join(dir, normalize(decodeURIComponent(p)));
  if (path.endsWith('/index.html')) return { redirect: path.slice(0, -'index.html'.length) };
  if (path.endsWith('.html')) {
    const bare = path.slice(0, -'.html'.length);
    return isFile(fsPath(path)) ? { redirect: bare } : null;
  }
  if (path.endsWith('/')) {
    if (isFile(fsPath(`${path}index.html`))) return { file: fsPath(`${path}index.html`) };
    if (path !== '/' && isFile(fsPath(`${path.slice(0, -1)}.html`))) return { redirect: path.slice(0, -1) };
    return null;
  }
  if (isFile(fsPath(path))) return { file: fsPath(path) };
  if (isFile(fsPath(`${path}.html`))) return { file: fsPath(`${path}.html`) };
  if (isFile(fsPath(`${path}/index.html`))) return { redirect: `${path}/` };
  return null;
}

/** not_found_handling "404-page": the nearest 404.html walking up from the request path. */
function notFoundPage(dir, path) {
  const parts = path.split('/').filter(Boolean);
  for (let i = parts.length; i >= 0; i--) {
    const f = join(dir, ...parts.slice(0, i), '404.html');
    if (isFile(f)) return f;
  }
  return null;
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;
  const w = path.startsWith(`${BASE}/`) ? product : hub;
  const send = (status, headers, body) => {
    const extra = w.headers.filter((r) => r.re.test(path)).flatMap((r) => r.headers);
    res.writeHead(status, [...extra, ...Object.entries(headers)].reduce((h, [k, v]) => ({ ...h, [k]: v }), {}));
    res.end(body);
  };
  const redirect = w.redirects.find((r) => r.from === path);
  if (redirect) return send(redirect.status, { Location: redirect.to + url.search }, '');
  const hit = resolve(w.dir, path);
  if (hit?.redirect) return send(307, { Location: hit.redirect + url.search }, '');
  if (hit?.file) {
    const type = TYPES[extname(hit.file)] ?? 'application/octet-stream';
    return send(200, { 'Content-Type': type }, req.method === 'HEAD' ? '' : readFileSync(hit.file));
  }
  const nf = notFoundPage(w.dir, path);
  send(404, { 'Content-Type': 'text/html; charset=utf-8' }, nf ? readFileSync(nf) : 'Not found');
  console.log(`404 ${path}`);
});

server.listen(PORT, '127.0.0.1', () => console.log(`docs preview: http://localhost:${PORT}/ (product ${BASE}/, hub /)`));
