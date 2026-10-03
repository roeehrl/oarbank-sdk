// Build the hub: the docs host's own files, served by a separate assets-only Worker
// on the docs.codonic.dev custom domain. Each product's Worker owns its route
// (docs.codonic.dev/oarbank/*) and runs ahead of the hub; everything else on the
// host reaches the hub.
//
//   deploy-hub/index.html    "Codonic docs": one card per product
//   deploy-hub/404.html      the host's 404 page (not_found_handling "404-page")
//   deploy-hub/robots.txt    allow everything; one Sitemap line per product
//   deploy-hub/llms.txt      a host-level index of the products' docs
//   deploy-hub/_redirects    /oarbank → /oarbank/ (the product root is a directory)
//   deploy-hub/_headers      CSP (no scripts at all), HSTS, nosniff, …
//   deploy-hub/style.css, favicon.svg, img/<product>.svg
//
// Every value comes from site.config.mjs. The pages run no script; JSON-LD is data.
import { copyFileSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { HUB, PUBLISHER, SECURITY_HEADERS, SITE } from '../site.config.mjs';
import { HUB_DEPLOY_DIR, HUB_SRC_DIR, SITE_DIR, rel, walk } from './lib/fs.mjs';

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const jsonForScript = (value) => JSON.stringify(value).replace(/</g, '\\u003c');
const year = new Date().getUTCFullYear();
const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-');

const WORDMARK = `<svg viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
        <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="9" stroke="currentColor" stroke-opacity="0.28" stroke-width="1.5"></rect>
        <circle cx="10" cy="10" r="2.6" fill="currentColor"></circle>
        <circle cx="16" cy="16" r="2.6" fill="currentColor" fill-opacity="0.62"></circle>
        <circle cx="22" cy="22" r="2.6" fill="currentColor" fill-opacity="0.3"></circle>
        <path d="M10 22 L22 10" stroke="currentColor" stroke-opacity="0.34" stroke-width="1.6" stroke-linecap="round"></path>
      </svg>`;

function page({ title, description, canonical, robots, body, jsonLd }) {
  return `<!doctype html>
<html lang="en" dir="ltr">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${esc(title)}</title>
    <meta name="description" content="${esc(description)}" />
${canonical ? `    <link rel="canonical" href="${esc(canonical)}" />\n` : ''}${robots ? `    <meta name="robots" content="${esc(robots)}" />\n` : ''}    <meta name="color-scheme" content="dark" />
    <meta name="theme-color" content="#08090b" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="${esc(HUB.title)}" />
    <meta property="og:title" content="${esc(title)}" />
    <meta property="og:description" content="${esc(description)}" />
${canonical ? `    <meta property="og:url" content="${esc(canonical)}" />\n` : ''}    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="stylesheet" href="/style.css" />
${jsonLd ? `    <script type="application/ld+json">${jsonForScript(jsonLd)}</script>\n` : ''}  </head>
  <body>
    <header>
      <div class="wrap">
        <a class="wordmark" href="${esc(PUBLISHER.url)}" aria-label="Codonic home">
      ${WORDMARK}
          <span>Codonic</span>
        </a>
        <nav aria-label="Site"><a href="${esc(PUBLISHER.url)}">codonic.dev</a></nav>
      </div>
    </header>
    <main>
      <div class="wrap">
${body}
      </div>
    </main>
    <footer>
      <div class="wrap">
        <a href="${esc(PUBLISHER.url)}">Codonic</a>
        <a href="${esc(PUBLISHER.url)}/privacy">Privacy</a>
        <a href="${esc(PUBLISHER.url)}/terms">Terms</a>
        <a href="${esc(PUBLISHER.url)}/support">Support</a>
        <span class="copy">© ${year} Codonic</span>
      </div>
    </footer>
  </body>
</html>
`;
}

const productCards = HUB.products
  .map(
    (p) => `          <li class="product">
            <img src="/img/${slug(p.name)}.svg" width="48" height="48" alt="" />
            <div>
              <h2><a href="${esc(p.path)}">${esc(p.name)}</a></h2>
              <p>${esc(p.tagline)}. ${esc(p.description)}</p>
              <p class="links">
                <a href="${esc(p.path)}">Documentation</a>
                <a href="${esc(p.productUrl)}">Product page</a>
              </p>
            </div>
          </li>`,
  )
  .join('\n');

const publisher = { '@type': 'Organization', name: PUBLISHER.name, url: PUBLISHER.url };
const index = page({
  title: HUB.title,
  description: HUB.description,
  canonical: `${SITE}/`,
  body: `        <h1>${esc(HUB.title)}</h1>
        <p class="lede">${esc(HUB.description)} For the products themselves, visit <a href="${esc(PUBLISHER.url)}">codonic.dev</a>.</p>
        <ul class="products">
${productCards}
        </ul>`,
  jsonLd: {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    name: 'Codonic Docs',
    url: `${SITE}/`,
    inLanguage: 'en',
    publisher,
  },
});

const notFound = page({
  title: `Page not found · ${HUB.title}`,
  description: 'This page does not exist on the Codonic docs.',
  robots: 'noindex',
  body: `        <h1>Page not found</h1>
        <p class="lede">There is nothing at this address. The documentation is listed on the <a href="/">Codonic docs</a> home page.</p>
        <ul class="products">
${productCards}
        </ul>`,
});

const robots = `User-agent: *
Allow: /

${HUB.products.map((p) => `Sitemap: ${p.sitemap}`).join('\n')}
`;

const llms = `# ${HUB.title}

> ${HUB.description}

## Products

${HUB.products
  .flatMap((p) => [
    `- [${p.name} documentation](${SITE}${p.path}): ${p.tagline}. ${p.description}`,
    `- [${p.name} documentation for agents](${p.llms}): an llms.txt index of the ${p.name} docs, with Markdown versions of every page`,
  ])
  .join('\n')}

## Optional

- [Codonic](${PUBLISHER.url}): the products and their pages
${HUB.products.map((p) => `- [${p.name} product page](${p.productUrl})`).join('\n')}
`;

// Bare /oarbank belongs to the hub: the product route is /oarbank/* only.
const redirects = `# Generated by site/scripts/build-hub.mjs.
${HUB.products.map((p) => `${p.path.replace(/\/$/, '')} ${p.path} 301`).join('\n')}
`;

const headers = `# Generated by site/scripts/build-hub.mjs. The hub serves every path the product Workers do not.
/*
  Content-Security-Policy: ${HUB.csp}
${SECURITY_HEADERS.map((h) => `  ${h}`).join('\n')}

/*.txt
  Content-Type: text/plain; charset=utf-8
`;

rmSync(HUB_DEPLOY_DIR, { recursive: true, force: true });
mkdirSync(join(HUB_DEPLOY_DIR, 'img'), { recursive: true });
writeFileSync(join(HUB_DEPLOY_DIR, 'index.html'), index);
writeFileSync(join(HUB_DEPLOY_DIR, '404.html'), notFound);
writeFileSync(join(HUB_DEPLOY_DIR, 'robots.txt'), robots);
writeFileSync(join(HUB_DEPLOY_DIR, 'llms.txt'), llms);
writeFileSync(join(HUB_DEPLOY_DIR, '_redirects'), redirects);
writeFileSync(join(HUB_DEPLOY_DIR, '_headers'), headers);
copyFileSync(join(HUB_SRC_DIR, 'style.css'), join(HUB_DEPLOY_DIR, 'style.css'));
copyFileSync(join(HUB_SRC_DIR, 'favicon.svg'), join(HUB_DEPLOY_DIR, 'favicon.svg'));
for (const p of HUB.products) {
  copyFileSync(join(SITE_DIR, 'src', 'assets', `${slug(p.name)}-logo.svg`), join(HUB_DEPLOY_DIR, 'img', `${slug(p.name)}.svg`));
}

const files = walk(HUB_DEPLOY_DIR).map((f) => rel(HUB_DEPLOY_DIR, f));
console.log(`hub: ${files.length} files in deploy-hub/: ${files.join(', ')}`);
