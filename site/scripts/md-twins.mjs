// Post-build: write a Markdown twin next to every page (spec/platforms.html →
// spec/platforms.md) for coding agents and other Markdown readers.
//
// The twin is made from the rendered page, not the source file, so MDX components
// come out as their content: OS tabs become a list with one item per OS, the
// schema component becomes tables, Expressive Code blocks become fenced code with
// their language. Each page advertises its twin with
// <link rel="alternate" type="text/markdown"> (src/routeData.ts), and _headers
// serves the twins with X-Robots-Tag: noindex so they never compete in search.
import { readFileSync, writeFileSync } from 'node:fs';
import { matches, select, selectAll } from 'hast-util-select';
import rehypeParse from 'rehype-parse';
import rehypeRemark from 'rehype-remark';
import remarkGfm from 'remark-gfm';
import remarkStringify from 'remark-stringify';
import { unified } from 'unified';
import { remove } from 'unist-util-remove';
import { DIST_DIR, rel, walk } from './lib/fs.mjs';

const text = (node) =>
  node.type === 'text' ? node.value : (node.children ?? []).map(text).join('');

/** Page chrome inside the content area that has no meaning in Markdown. */
const DROP = ['.sl-anchor-link', 'svg', 'button', '.sr-only', 'template', 'script', 'style', '.copy'];

function tidy() {
  return (tree) => {
    remove(tree, (n) => n.type === 'comment' || (n.type === 'element' && DROP.some((s) => matches(s, n))));

    // Expressive Code: one <div class="ec-line"> per line → a plain <pre><code class="language-x">.
    for (const fig of selectAll('.expressive-code', tree)) {
      const pre = select('pre', fig);
      if (!pre) continue;
      const lines = selectAll('.ec-line', pre).map(text);
      const lang = pre.properties.dataLanguage;
      fig.tagName = 'pre';
      fig.properties = {};
      fig.children = [
        {
          type: 'element',
          tagName: 'code',
          properties: lang && lang !== 'plaintext' ? { className: [`language-${lang}`] } : {},
          children: [{ type: 'text', value: lines.join('\n') }],
        },
      ];
    }

    // Tabs → a list: the tab label, then its panel.
    for (const tabs of selectAll('starlight-tabs', tree)) {
      const labels = selectAll('[role="tab"]', tabs).map((t) => text(t).trim());
      const panels = selectAll('[role="tabpanel"]', tabs);
      tabs.tagName = 'ul';
      tabs.properties = {};
      tabs.children = panels.map((panel, i) => ({
        type: 'element',
        tagName: 'li',
        properties: {},
        children: [
          { type: 'element', tagName: 'p', properties: {}, children: [{ type: 'element', tagName: 'strong', properties: {}, children: [{ type: 'text', value: labels[i] ?? '' }] }] },
          panel,
        ],
      }));
    }
  };
}

const toMarkdown = unified()
  .use(rehypeParse, { fragment: false })
  .use(() => (tree, file) => {
    const content = select('.sl-markdown-content', tree);
    file.data.title = text(select('h1#_top', tree) ?? { type: 'text', value: '' }).trim();
    const desc = select('meta[name="description"]', tree);
    file.data.description = desc?.properties.content ?? '';
    const canonical = select('link[rel="canonical"]', tree);
    file.data.canonical = canonical?.properties.href ?? '';
    return { type: 'root', children: content ? content.children : [] };
  })
  .use(tidy)
  .use(rehypeRemark)
  .use(remarkGfm)
  .use(remarkStringify, { bullet: '-', fences: true, rule: '-' });

let count = 0;
for (const file of walk(DIST_DIR).filter((f) => f.endsWith('.html'))) {
  const path = rel(DIST_DIR, file);
  if (path === '404.html') continue;
  const vfile = await toMarkdown.process(readFileSync(file, 'utf8'));
  const { title, description, canonical } = vfile.data;
  const body = String(vfile).trim();
  const head = [`# ${title}`, description && `> ${description}`, canonical && `HTML version: ${canonical}`]
    .filter(Boolean)
    .join('\n\n');
  writeFileSync(file.replace(/\.html$/, '.md'), `${head}\n\n${body}\n`);
  count++;
}
console.log(`md-twins: wrote ${count} Markdown twins`);
