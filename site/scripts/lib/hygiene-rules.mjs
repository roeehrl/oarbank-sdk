// Rules for the public-hygiene gate (scripts/check-dist.mjs).
//
// The published site must not leak where or on what it was made: no locations,
// local time zones or offsets, personal or machine names, private network
// addresses, local file paths or internal design references. These rules are
// generic on purpose. Owner-specific terms (names, machine names) are never
// written in this public repository: the gate reads them from site/.hygiene-terms
// (gitignored) or the DOCS_HYGIENE_TERMS environment variable (a CI secret).

/** Country names (ISO 3166 short names, common forms). Matched case-sensitively as whole words. */
export const COUNTRIES = `Afghanistan Albania Algeria Andorra Angola Antigua Argentina Armenia Australia Austria Azerbaijan
Bahamas Bahrain Bangladesh Barbados Belarus Belgium Belize Benin Bhutan Bolivia Bosnia Botswana Brazil Brunei Bulgaria
Burkina Burundi Cambodia Cameroon Canada Chile China Colombia Comoros Congo Croatia Cuba Cyprus Czechia Denmark
Djibouti Dominica Ecuador Egypt Eritrea Estonia Eswatini Ethiopia Fiji Finland France Gabon Gambia Germany Ghana
Greece Grenada Guatemala Guinea Guyana Haiti Honduras Hungary Iceland India Indonesia Iran Iraq Ireland Israel Italy
Jamaica Japan Kazakhstan Kenya Kiribati Kosovo Kuwait Kyrgyzstan Laos Latvia Lebanon Lesotho Liberia Libya
Liechtenstein Lithuania Luxembourg Madagascar Malawi Malaysia Maldives Malta Mauritania Mauritius Mexico Micronesia
Moldova Mongolia Montenegro Morocco Mozambique Myanmar Namibia Nauru Nepal Netherlands Nicaragua Nigeria Norway Oman
Pakistan Palau Palestine Panama Paraguay Peru Philippines Poland Portugal Qatar Romania Russia Rwanda Samoa Senegal
Serbia Seychelles Singapore Slovakia Slovenia Somalia Spain Sudan Suriname Sweden Switzerland Syria Taiwan
Tajikistan Tanzania Thailand Togo Tonga Trinidad Tunisia Turkmenistan Tuvalu Uganda Ukraine Uruguay Uzbekistan
Vanuatu Vatican Venezuela Vietnam Yemen Zambia Zimbabwe`
  .split(/\s+/)
  .concat([
    'Cape Verde', 'Costa Rica', 'Dominican Republic', 'East Timor', 'El Salvador', 'Equatorial Guinea', 'Ivory Coast',
    'Marshall Islands', 'New Zealand', 'North Korea', 'North Macedonia', 'Papua New Guinea', 'Saint Lucia',
    'San Marino', 'Saudi Arabia', 'Sierra Leone', 'Solomon Islands', 'South Africa', 'South Korea', 'South Sudan',
    'Sri Lanka', 'United Arab Emirates', 'United Kingdom', 'United States',
  ]);

const words = (list) => new RegExp(`(?<![A-Za-z])(?:${list.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s+')).join('|')})(?![A-Za-z])`, 'g');

/**
 * Applied to page content: visible HTML text and attributes (scripts and styles
 * removed, JSON-LD kept), Markdown twins, llms*.txt, sitemaps and our own JSON.
 */
export const CONTENT_RULES = [
  // The site is English: letters of right-to-left and Cyrillic scripts never belong in it.
  { name: 'non-Latin script', re: /[\u0400-\u04FF\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]+/g },
  { name: 'country name', re: words(COUNTRIES) },
  { name: 'IANA time zone', re: /\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)\/[A-Z][A-Za-z_+-]+/g },
  { name: 'non-UTC offset after a time', re: /\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s?[+-](?!00:?00\b)(?:0\d|1[0-4]):?[0-5]\d\b/g },
  { name: 'git-style UTC offset', re: /(?<=\s|^)[+-](?:0[1-9]|1[0-4])[03]0(?!\d)/gm },
  { name: '.local host name', re: /(?<![\w~/.-])[a-z0-9][a-z0-9-]*\.local\b/gi },
  { name: 'internal plan reference', re: /\bPLAN\b|(?<![\w-])D\d{1,3}(?![\w-])/g, allow: ['D2'] },
  { name: 'internal path', re: /\bdocs\/design\/|\bsrc\/oarbank\/|\bvendor\/oarbank-sdk\b/g },
  {
    name: 'e-mail address',
    re: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g,
    allow: ['dev@codonic.dev', 'support@codonic.dev', 'git@github.com'],
    allowRe: /@example\.(?:com|org|net)$/,
  },
];

/** Applied to every text file, including JavaScript and CSS bundles. */
export const RAW_RULES = [
  { name: 'local file path', re: /\/Users\/[A-Za-z]|\/home\/[a-z_][a-z0-9_-]*\/|[A-Za-z]:\\\\?Users\\\\?|\/private\/(?:tmp|var)\/|\/var\/folders\/|file:\/\/\//g },
  { name: 'tailnet host name', re: /\b[a-z0-9-]+\.ts\.net\b/gi },
  { name: 'CGNAT (tailnet) address', re: /\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b/g },
];

/** Owner-specific terms from site/.hygiene-terms or DOCS_HYGIENE_TERMS: one per line, # comments. */
export function privateRule(text) {
  const terms = text
    .split(/[\n,]/)
    .map((t) => t.replace(/#.*/, '').trim())
    .filter(Boolean);
  if (!terms.length) return null;
  const re = new RegExp(`(?<![A-Za-z])(?:${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?![A-Za-z])`, 'gi');
  return { name: 'private term', re, count: terms.length };
}
