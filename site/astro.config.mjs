// Dates on the site are calendar dates in UTC, whatever machine builds it.
process.env.TZ = 'UTC';

import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightLinksValidator from 'starlight-links-validator';
import starlightLlmsTxt from 'starlight-llms-txt';
import { BASE, HOME_URL, PRODUCT, SECTIONS, SITE } from './site.config.mjs';
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
