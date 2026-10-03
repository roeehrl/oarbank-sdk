import { readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/** site/ */
export const SITE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
/** The SDK repository root. */
export const REPO_DIR = join(SITE_DIR, '..');
/** Astro's build output (flat: the base path is not part of it). */
export const DIST_DIR = join(SITE_DIR, 'dist');
/** The deployable asset directory: deploy/oarbank/... plus deploy/_headers. */
export const DEPLOY_DIR = join(SITE_DIR, 'deploy');
/** The hub's deployable asset directory (scripts/build-hub.mjs). */
export const HUB_DEPLOY_DIR = join(SITE_DIR, 'deploy-hub');
/** The hub's static sources. */
export const HUB_SRC_DIR = join(SITE_DIR, 'hub');

/** Every file under `dir`, recursively. */
export function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else if (entry.isFile()) out.push(p);
  }
  return out.sort();
}

/** `file` relative to `root`, always with `/` separators (Windows-safe). */
export function rel(root, file) {
  return relative(root, file).split(sep).join('/');
}
