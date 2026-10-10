// Dates on the site are calendar dates in UTC, whatever machine builds it.
process.env.TZ = 'UTC';

import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightLinksValidator from 'starlight-links-validator';
import starlightLlmsTxt from 'starlight-llms-txt';
import { BASE, HOME_URL, PRODUCT, RELEASE, REPO, SECTIONS, SITE } from './site.config.mjs';
import { gitDateUtc, pageSource } from './scripts/lib/git-dates.mjs';

export default defineConfig({
  site: SITE,
  base: BASE,
  // Pages are files (get-started/quickstart.html) served without the extension, so
  // no page needs a trailing-slash redirect. Only the product root is a directory;
  // the hub 301s /oarbank to /oarbank/. 'preserve' writes the same files as 'file'
  // for Starlight's routes, but makes Starlight link to /x instead of /x.html,
  // which the Worker's auto-trailing-slash handling would 307 to /x.
  trailingSlash: 'never',
  build: { format: 'preserve' },
  integrations: [
    // Added here (Starlight then skips its own) so the root entry is /oarbank/.
    sitemap({
      serialize(item) {
        if (item.url === SITE + BASE) item.url = HOME_URL;
        // lastmod: the page source's last commit, as a UTC date.
        const src = pageSource(item.url.slice(HOME_URL.length));
        if (src) item.lastmod = gitDateUtc(src);
        return item;
      },
    }),
    starlight({
      title: PRODUCT.docsTitle,
      description: PRODUCT.description,
      titleDelimiter: '·',
      logo: { src: './src/assets/oarbank-logo.svg', alt: 'Oarbank' },
      favicon: '/favicon.svg',
      customCss: ['./src/styles/codonic.css'],
      lastUpdated: true,
      // Hand-written pages edit under site/; pages synced from docs/ and spec/ set
      // their own editUrl to the repository file (scripts/sync-content.mjs).
      ...(REPO.public ? { editLink: { baseUrl: `${REPO.url}/edit/${REPO.branch}/site/` } } : {}),
      credits: false,
      pagefind: true,
      expressiveCode: { themes: ['starlight-dark'] },
      routeMiddleware: './src/routeData.ts',
      components: {
        // Dark only: no theme script, no theme picker.
        ThemeProvider: './src/components/overrides/ThemeProvider.astro',
        ThemeSelect: './src/components/overrides/ThemeSelect.astro',
        SiteTitle: './src/components/overrides/SiteTitle.astro',
        SocialIcons: './src/components/overrides/SocialIcons.astro',
        Footer: './src/components/overrides/Footer.astro',
      },
      sidebar: SECTIONS.map(({ dir, label, page, pages = [], groups }) =>
        page
          ? { slug: dir, label }
          : {
              label,
              items: groups
                ? [
                    ...pages.map((slug) => ({ slug })),
                    ...groups.map((g) => ({ label: g.label, items: [{ autogenerate: { directory: `${dir}/${g.dir}` } }] })),
                  ]
                : [{ autogenerate: { directory: dir } }],
            },
      ),
      plugins: [
        starlightLinksValidator({ errorOnRelativeLinks: true, errorOnInvalidHashes: true }),
        starlightLlmsTxt({
          projectName: 'Oarbank',
          description: PRODUCT.description,
          details: [
            `Current release: Oarbank ${RELEASE.core}, module SDK ${RELEASE.sdk}. These docs describe that release only; for an older installation, read the docs in the repositories at its tag (https://github.com/roeehrl/oarbank/tree/v<version>/docs).`,
            'Load only what the task needs: one of the sets below, or single pages. Every page has a Markdown version at its URL plus `.md`; they are listed under Pages. A Markdown URL that returns a status other than 200 is not a page.',
          ].join('\n\n'),
          // The default collapses all prose whitespace, which turns tables and
          // step lists into one line per page. Keep the line structure.
          minify: { whitespace: false },
          // The abridged set is the guides without the normative specification,
          // which has its own set and is most of the text.
          exclude: ['spec/**', 'reference/**'],
          customSets: [
            { label: 'Operate', description: 'Install, run and maintain an Oarbank fleet.', paths: ['operate/**', 'get-started/**', 'concepts/**'] },
            { label: 'Build modules', description: 'Write, test and ship modules with the open SDK.', paths: ['build/**'] },
            { label: 'Specification', description: 'The normative public contracts.', paths: ['spec/**', 'reference/**'] },
          ],
          optionalLinks: [{ label: 'Oarbank product page', url: PRODUCT.productUrl }],
          // Heading anchor links ("Section titled …") are page chrome, not content.
          customSelectors: { all: ['.sl-anchor-link'] },
        }),
      ],
    }),
  ],
});
