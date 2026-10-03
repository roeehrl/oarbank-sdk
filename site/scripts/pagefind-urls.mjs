// Post-build: make search results link to the served URLs.
//
// Pagefind records each page's URL from its output file (spec/platforms.html).
// The Worker serves pages without the extension and 307-redirects the .html form,
// so every search click would cost a redirect. Each fragment is gzip-compressed
// "pagefind_dcd" + JSON; this rewrites its `url` in place (the file name is only
// an identifier, nothing verifies it against the content).
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gunzipSync, gzipSync } from 'node:zlib';
import { DIST_DIR, walk } from './lib/fs.mjs';

const PREFIX = 'pagefind_dcd';
const dir = join(DIST_DIR, 'pagefind', 'fragment');
let changed = 0;
let total = 0;
for (const file of walk(dir)) {
  total++;
  const text = gunzipSync(readFileSync(file)).toString('utf8');
  if (!text.startsWith(PREFIX)) throw new Error(`${file}: not a Pagefind fragment`);
  const fragment = JSON.parse(text.slice(PREFIX.length));
  const url = fragment.url.replace(/\.html$/, '');
  if (url === fragment.url) continue;
  fragment.url = url;
  writeFileSync(file, gzipSync(Buffer.from(PREFIX + JSON.stringify(fragment), 'utf8')));
  changed++;
}
console.log(`pagefind-urls: ${changed} of ${total} result URLs rewritten without .html`);
