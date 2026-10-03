// Starlight route middleware: per-page <head> additions.
//   - canonical and og:url are the served URL (no .html); the product root's
//     are /oarbank/ (the hub 301s the slashless form there);
//   - <link rel="alternate" type="text/markdown"> to the page's Markdown twin;
//   - JSON-LD: TechArticle (publisher Codonic) and BreadcrumbList;
//   - draft pages get a banner and noindex; the 404 page gets noindex and no canonical.
import { defineRouteMiddleware } from '@astrojs/starlight/route-data';
import { getCollection } from 'astro:content';
import { BASE, HOME_URL, PRODUCT, PUBLISHER, SECTIONS, SITE } from '../site.config.mjs';

type HeadEntry = { tag: string; attrs?: Record<string, string | boolean | undefined>; content?: string };

/** JSON for a <script> element: `<` escaped so content can never close the element. */
const jsonForScript = (value: unknown) => JSON.stringify(value).replace(/</g, '\\u003c');

let docs: Promise<Map<string, { data: { title: string } }>> | undefined;
const docsById = () => (docs ??= getCollection('docs').then((all) => new Map(all.map((e) => [e.id, e]))));

export const onRequest = defineRouteMiddleware(async (context) => {
  const route = context.locals.starlightRoute;
  const { entry } = route;
  const head = route.head as HeadEntry[];
  const id = entry.id;
  if (id === '404') {
    // Served with status 404 at whatever URL missed: no canonical, never indexed.
    for (let i = head.length - 1; i >= 0; i--) {
      const h = head[i];
      if ((h.tag === 'link' && h.attrs?.rel === 'canonical') || (h.tag === 'meta' && h.attrs?.property === 'og:url')) head.splice(i, 1);
    }
    head.push({ tag: 'meta', attrs: { name: 'robots', content: 'noindex' } });
    return;
  }

  const isHome = id === 'index' || id === '';
  const pageUrl = isHome ? HOME_URL : `${SITE}${BASE}/${id}`;

  // Starlight derives these from the output file name (….html); the served URL has
  // no extension, and the root is /oarbank/.
  for (const h of head) {
    if (isHome && h.tag === 'title') h.content = PRODUCT.docsTitle;
    if (h.tag === 'link' && h.attrs?.rel === 'canonical') h.attrs.href = pageUrl;
    if (h.tag === 'meta' && h.attrs?.property === 'og:url') h.attrs.content = pageUrl;
  }

  head.push({
    tag: 'link',
    attrs: { rel: 'alternate', type: 'text/markdown', href: `${BASE}/${isHome ? 'index' : id}.md` },
  });

  if (entry.data.status === 'draft') {
    head.push({ tag: 'meta', attrs: { name: 'robots', content: 'noindex, follow' } });
    entry.data.banner ??= {
      content: 'Draft: this page is a placeholder. Its content is still being written.',
    };
  }

  // Breadcrumbs: docs home, the section's index page when it has one, the page.
  const crumbs: { name: string; url: string }[] = [{ name: PRODUCT.docsTitle, url: HOME_URL }];
  if (!isHome) {
    const parts = id.split('/');
    for (let i = 1; i < parts.length; i++) {
      const parentId = parts.slice(0, i).join('/');
      const parent = (await docsById()).get(parentId);
      const section = SECTIONS.find((s) => s.dir === parentId);
      if (parent) crumbs.push({ name: parent.data.title ?? section?.label ?? parentId, url: `${SITE}${BASE}/${parentId}` });
    }
    crumbs.push({ name: entry.data.title, url: pageUrl });
  }

  const publisher = { '@type': 'Organization', name: PUBLISHER.name, url: PUBLISHER.url };
  const graph: Record<string, unknown>[] = [
    {
      '@type': 'TechArticle',
      '@id': `${pageUrl}#article`,
      headline: entry.data.title,
      description: entry.data.description,
      url: pageUrl,
      inLanguage: 'en',
      ...(route.lastUpdated ? { dateModified: route.lastUpdated.toISOString().slice(0, 10) } : {}),
      publisher,
      isPartOf: { '@type': 'WebSite', name: 'Codonic Docs', url: `${SITE}/` },
    },
  ];
  if (crumbs.length > 1) {
    graph.push({
      '@type': 'BreadcrumbList',
      itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.url })),
    });
  }
  head.push({
    tag: 'script',
    attrs: { type: 'application/ld+json' },
    content: jsonForScript({ '@context': 'https://schema.org', '@graph': graph }),
  });
});
