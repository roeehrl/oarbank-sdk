// Find <script> elements in built HTML and classify them for the CSP
// (script-src 'self'): which ones are inline code the browser would execute.
//
// Astro and Starlight emit plain, well-formed HTML; script bodies never contain a
// literal "</script", so a regex over the elements is exact here.

export const SCRIPT_RE = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;
const ATTR_RE = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;

/** JavaScript MIME types (WHATWG HTML): a script with one of these, or none, is classic JS. */
const JS_TYPES = new Set([
  '', 'text/javascript', 'application/javascript', 'application/ecmascript', 'application/x-ecmascript',
  'application/x-javascript', 'text/ecmascript', 'text/javascript1.0', 'text/javascript1.1', 'text/javascript1.2',
  'text/javascript1.3', 'text/javascript1.4', 'text/javascript1.5', 'text/jscript', 'text/livescript',
  'text/x-ecmascript', 'text/x-javascript',
]);
/** Inline data blocks the browser never executes, so CSP does not apply. */
export const DATA_TYPES = new Set(['application/ld+json', 'application/json']);

export function parseAttrs(raw) {
  const attrs = [];
  for (const m of raw.matchAll(ATTR_RE)) attrs.push({ name: m[1].toLowerCase(), value: m[2] ?? m[3] ?? m[4] ?? null });
  return attrs;
}

/**
 * Classify a script element.
 *   external  – has src (allowed by script-src 'self' when same-origin)
 *   classic   – inline classic script (executes)
 *   module    – inline module script (executes)
 *   data      – inline data block (ld+json, json): allowed
 *   blocked   – inline import map, speculation rules or unknown type: CSP-relevant
 *               and cannot be moved to a file, so the gate fails on it
 */
export function classify(attrs) {
  if (attrs.some((a) => a.name === 'src')) return 'external';
  const type = (attrs.find((a) => a.name === 'type')?.value ?? '').trim().toLowerCase();
  if (JS_TYPES.has(type)) return 'classic';
  if (type === 'module') return 'module';
  if (DATA_TYPES.has(type)) return 'data';
  return 'blocked';
}

/** CSP problems in one HTML document: inline executable scripts, event-handler attributes, javascript: URLs. */
export function cspProblems(html) {
  const problems = [];
  for (const m of html.matchAll(SCRIPT_RE)) {
    const kind = classify(parseAttrs(m[1]));
    if (kind === 'classic' || kind === 'module' || kind === 'blocked') {
      problems.push(`inline <script${m[1]}> (${kind}): ${m[2].trim().slice(0, 80).replace(/\s+/g, ' ')}…`);
    }
  }
  // Strip script bodies so their text is not mistaken for markup.
  const markup = html.replace(SCRIPT_RE, '<script$1></script>');
  for (const m of markup.matchAll(/<[a-z][a-z0-9-]*\b[^>]*?\s(on[a-z]+)\s*=/gi)) problems.push(`inline event handler ${m[1]}=`);
  for (const m of markup.matchAll(/\s(?:href|src|action|formaction)\s*=\s*["']?\s*javascript:/gi)) problems.push(`javascript: URL (${m[0].trim()})`);
  return problems;
}
