// Settings shared by astro.config.mjs, the route middleware and the build scripts.
// Nothing else hardcodes these values.

/** Origin of the docs host. Each product lives under its own base path. */
export const SITE = 'https://docs.codonic.dev';
/** This product's prefix on the docs host. No trailing slash (Astro `base`). */
export const BASE = '/oarbank';
/** The product root as readers and crawlers should see it (the hub 301s `/oarbank` here). */
export const HOME_URL = `${SITE}${BASE}/`;

export const PRODUCT = {
  name: 'Oarbank',
  docsTitle: 'Oarbank docs',
  description:
    'Documentation for Oarbank: run jobs on a fleet of macOS, Linux and Windows computers, and build modules for it with the open SDK.',
  productUrl: 'https://codonic.dev/apps/oarbank',
};

/**
 * The current release the docs describe. llms.txt states it for agents, and
 * check-dist fails the build when the home page names a different release.
 */
export const RELEASE = { core: '2.8.0', sdk: '1.5.0' };

export const PUBLISHER = { name: 'Codonic', url: 'https://codonic.dev' };

export const REPO = {
  url: 'https://github.com/roeehrl/oarbank-sdk',
  branch: 'main',
  /**
   * The SDK repository is public: pages get edit links and a source link, and
   * repo files that are not published as pages link to GitHub. Set to false to
   * show no GitHub links at all (repo files are then named in plain text).
   */
  public: true,
};

/**
 * The production Content-Security-Policy. Every page must work under it; the
 * script externaliser and the CSP gate in scripts/ keep it that way.
 * 'wasm-unsafe-eval' is for Pagefind's WebAssembly search index.
 */
export const CSP = [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
].join('; ');

/** Headers every response on the docs host carries, from the product Worker and the hub alike. */
export const SECURITY_HEADERS = [
  'Strict-Transport-Security: max-age=31536000; includeSubDomains; preload',
  'X-Content-Type-Options: nosniff',
  'X-Frame-Options: DENY',
  'Referrer-Policy: strict-origin-when-cross-origin',
  'Permissions-Policy: geolocation=(), camera=(), microphone=(), payment=(), usb=()',
];

/**
 * The hub: the docs host's own pages (/, its 404, robots.txt, llms.txt), served by
 * a separate assets-only Worker on the docs.codonic.dev custom domain. Built by
 * scripts/build-hub.mjs into deploy-hub/. It runs no script at all.
 */
export const HUB = {
  title: 'Codonic docs',
  description: 'Documentation for Codonic products.',
  csp: [
    "default-src 'self'",
    "script-src 'none'",
    "style-src 'self'",
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; '),
  /** One entry per product on the docs host. */
  products: [
    {
      name: 'Oarbank',
      path: `${BASE}/`,
      tagline: 'Spread batch jobs across the computers you already own',
      description: 'Run a fleet of macOS, Linux and Windows computers, and build modules with the open-source SDK.',
      productUrl: 'https://codonic.dev/apps/oarbank',
      sitemap: `${SITE}${BASE}/sitemap-index.xml`,
      llms: `${SITE}${BASE}/llms.txt`,
    },
  ],
};

/**
 * Sidebar sections: directory → label. Used by the sidebar and the breadcrumbs.
 *   page    the section is one page (<dir>/index), shown as a top-level link
 *   pages   pages listed before the groups, by content id
 *   groups  subfolders shown as labelled groups
 */
export const SECTIONS = [
  { dir: 'get-started', label: 'Get started', page: true },
  { dir: 'concepts', label: 'Concepts' },
  { dir: 'operate', label: 'Operate' },
  { dir: 'build', label: 'Build modules' },
  { dir: 'reference', label: 'Reference', pages: ['reference/cli', 'reference/reason-codes', 'reference/manifest'], groups: [{ dir: 'schemas', label: 'JSON Schemas' }] },
  { dir: 'spec', label: 'Specification' },
];

/** Workers Free plan limit on files per deployed version. */
export const MAX_DEPLOY_FILES = 20000;
