// Prebuild: copy the repository's published Markdown (docs/, spec/) into the
// content collection, and its raw artefacts (schemas, test vectors) into public/.
//
// For each page it:
//   - turns the H1 into front-matter `title` (or uses the override in sources.mjs);
//   - adds `description`, `type`, `source` and a UTC `lastUpdated` date from git;
//   - rewrites repo-relative links: a published page becomes its site route, a raw
//     artefact its published URL, and anything else a GitHub link (or plain text
//     when REPO.public is false).
//
// Generated files are gitignored; the repo file is the only source.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, posix } from 'node:path';
import { BASE, REPO } from '../site.config.mjs';
import { REPO_DIR, SITE_DIR } from './lib/fs.mjs';
import { gitDateUtc } from './lib/git-dates.mjs';
import { PAGES, RAW_DIRS } from './sources.mjs';

const CONTENT_DIR = join(SITE_DIR, 'src', 'content', 'docs');
const PUBLIC_DIR = join(SITE_DIR, 'public');

/** Repo path → site route (without base), for every published page. */
const routes = new Map(PAGES.map((p) => [p.src, p.dest.replace(/\.mdx?$/, '')]));

function rewriteLink(target, srcPath) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#') || target.startsWith('/')) return { href: target };
  const [pathPart, hash = ''] = target.split('#');
  const repoPath = posix.normalize(posix.join(posix.dirname(srcPath), pathPart)).replace(/\/$/, '');
  const anchor = hash ? `#${hash}` : '';
  if (routes.has(repoPath)) return { href: `${BASE}/${routes.get(repoPath)}${anchor}` };
  for (const raw of RAW_DIRS) {
    if (repoPath.startsWith(`${raw.src}/`) && raw.match.test(repoPath)) {
      return { href: `${BASE}/${raw.dest}/${repoPath.slice(raw.src.length + 1)}` };
    }
  }
  if (REPO.public) return { href: `${REPO.url}/blob/${REPO.branch}/${repoPath}${anchor}` };
  return { href: null };
}

/** Rewrite Markdown links outside fenced code blocks. */
function rewriteLinks(body, srcPath, unresolved) {
  const parts = body.split(/(^```[\s\S]*?^```)/m);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part; // fenced code
      return part.replace(/(?<!!)\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, text, target) => {
        const { href } = rewriteLink(target, srcPath);
        if (href === null) {
          unresolved.push(`${srcPath}: ${target}`);
          return text;
        }
        return `[${text}](${href})`;
      });
    })
    .join('');
}

const yaml = (v) => JSON.stringify(v); // JSON strings and arrays are valid YAML

function syncPages() {
  for (const p of PAGES) rmSync(join(CONTENT_DIR, p.dest), { force: true });
  const unresolved = [];
  for (const p of PAGES) {
    const text = readFileSync(join(REPO_DIR, p.src), 'utf8').replace(/\r\n/g, '\n');
    const h1 = text.match(/^# (.+)\n/m);
    if (!h1) throw new Error(`${p.src}: no H1 to use as the title`);
    const body = rewriteLinks(text.replace(h1[0], '').replace(/^\n+/, ''), p.src, unresolved);
    const fm = [
      '---',
      `title: ${yaml(p.title ?? h1[1].trim())}`,
      `description: ${yaml(p.description)}`,
      `type: ${yaml(p.type)}`,
      ...(p.platforms ? [`platforms: ${yaml(p.platforms)}`] : []),
      `source: ${yaml(p.src)}`,
      `lastUpdated: ${gitDateUtc(p.src)}`,
      ...(p.order ? ['sidebar:', `  order: ${p.order}`] : []),
      ...(REPO.public ? [`editUrl: ${yaml(`${REPO.url}/edit/${REPO.branch}/${p.src}`)}`] : []),
      '---',
      '',
    ].join('\n');
    const out = join(CONTENT_DIR, p.dest);
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, fm + body);
  }
  console.log(`sync: ${PAGES.length} pages`);
  if (unresolved.length) {
    console.log(`sync: ${unresolved.length} links to unpublished repo files left as plain text (REPO.public is false):`);
    for (const u of unresolved) console.log(`  ${u}`);
  }
}

function syncRaw() {
  for (const raw of RAW_DIRS) {
    const out = join(PUBLIC_DIR, raw.dest);
    rmSync(out, { recursive: true, force: true });
    mkdirSync(out, { recursive: true });
    const srcDir = join(REPO_DIR, raw.src);
    if (!existsSync(srcDir)) throw new Error(`missing ${raw.src}`);
    const files = readdirSync(srcDir).filter((f) => raw.match.test(f));
    for (const f of files) copyFileSync(join(srcDir, f), join(out, f));
    console.log(`sync: ${files.length} files ${raw.src}/ -> public/${raw.dest}/`);
  }
}

syncPages();
syncRaw();
