// Page dates from git, always as UTC calendar dates (never a local time).
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { PAGES } from '../sources.mjs';
import { REPO_DIR, SITE_DIR } from './fs.mjs';

/** Date of the last commit touching `repoPath` (repo-relative); today for an uncommitted file. */
export function gitDateUtc(repoPath) {
  const r = spawnSync('git', ['log', '-1', '--format=%ct', '--', repoPath], { cwd: REPO_DIR, encoding: 'utf8' });
  const ts = Number.parseInt((r.stdout || '').trim(), 10);
  const d = Number.isFinite(ts) ? new Date(ts * 1000) : new Date();
  return d.toISOString().slice(0, 10);
}

/** Repo-relative source file of a page, by content id ('' or 'index' for the home page), or null. */
export function pageSource(id) {
  const page = PAGES.find((p) => p.dest.replace(/\.mdx?$/, '') === id);
  if (page) return page.src;
  const base = id === '' ? 'index' : id;
  for (const candidate of [`${base}.mdx`, `${base}.md`, `${base}/index.mdx`, `${base}/index.md`]) {
    const rel = `site/src/content/docs/${candidate}`;
    if (existsSync(join(SITE_DIR, 'src', 'content', 'docs', candidate))) return rel;
  }
  return null;
}
