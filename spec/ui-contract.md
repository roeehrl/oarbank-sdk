# UI contract 1

A module defines its GUI as **data**. The console draws all of it with its own templates, and no module HTML, CSS or JavaScript ever runs in the console origin. Rich UIs that data cannot express use a **sandboxed iframe** served from a separate origin.

- Models: `oarbank_sdk.ui`.
- Schemas: `schemas/ui-page-1.schema.json`, and the `[ui]` and `[[operations]]` parts of `manifest-1`.
- Reference module: [examples/toy](../examples/toy).

## Versioning

- `requires.ui_contract = ">=1.0,<2"` in the manifest. The installer refuses a module outside the host's range.
- The host announces the contract version it renders in `initialize` (`host.capabilities` contains `ui_contract:1.<minor>`).
- Within a major, changes are additive only. A component may declare `requires = "1.<minor>"` plus `fallback` (`placeholder` or `drop`), so a newer page still renders on an older host.
- An unknown component, or an invalid prop found at render time, renders as a placeholder carrying a developer note. It never breaks the page.

## Where modules contribute (placements)

| Slot | Renders | Limit | Host rules |
|---|---|---|---|
| `module.overview` | First tab of `/modules/<id>` | 1 page | Core tabs always follow it: Health, Settings, Versions, Operations, Audit |
| `module.page` | Further tabs, at `/m/<id>/<page>` | 6 pages | Ids match `^[a-z0-9][a-z0-9_.-]{0,63}$` |
| `job.detail.panel` | Job page, beside the core sections | 2 panels | `when` filters them, e.g. `job.module == self` |
| `node.detail.panel` | Node page | 1 panel | Shown where the module is certified |
| `campaign.panel` | Campaign page | 1 panel | |

The **Modules** navigation entry, the home page and every core page belong to the core. Modules reach the home page only as inbox alerts, with namespaced reason codes.

## Pages

`ui/pages/<id>.json` and `ui/panels/<id>.json` are static documents covered by the bundle digest:

```json
{"ui_contract": "1.0", "title": "Toy", "vars": [], "body": [ <component>, ... ]}
```

The body holds components, at most 40 per container:

| Group | Components |
|---|---|
| Layout | `section` (title, span 1, 2 or full), `tabs` (up to 8), `columns` (2–3; they stack on phones) |
| Display | `text`, `markdown` (paragraphs, emphasis, code, lists, typed internal links), `kv`, `stat`, `status`, `progress`, `callout`, `empty` |
| Data | `table` (typed columns, paging, sorting, up to 4 row actions), `chart` (`line`, `bar`, `scatter`, `histogram`, `parallel_coords`; a text `summary` is required), `logs`, `json` |
| Input | `form` (restricted JSON Schema plus hints; `submit` names an operation), `filter_bar` (page variables that are pushed to the URL) |
| Action | `action` (an operation), `link` (a typed reference: `job`, `node`, `dataset`, `campaign`, `page`, or an allowlisted `https` URL) |
| Frame | `iframe` (a view from `[[ui.iframes]]`) |

**Values** are raw and typed. The host formats them:

- Cell types: `text`, `number`, `integer`, `percent`, `bytes`, `duration`, `relative_time`, `timestamp`, `bool`, `digest`, `code`, `status`, `job_ref`, `node_ref`, `dataset_ref`, `campaign_ref`, `link`.
- Formats: `.Nf`, `.Ne`, `.N%`, `d`, `,d`, `s`, `d/d`, with an optional leading `+`.
- Columns may declare `direction` (`min` or `max`). That is what makes "best" and good/bad colouring generic.
- A wrongly typed value renders as "—".

**Styling** is semantic only: `tone` ∈ `ok`, `warn`, `error`, `info`, `neutral`, `running`. There is no colour, class or style.

**Visibility** uses `when`, a small comparison grammar the host evaluates in Python over a fixed context: `row.*`, `job.*`, `node.*`, `user.role`, `setting.*`. There is no client-side expression language.

## Data

Every data component names a `source`, which is one of two kinds. The console never waits on the module for either.

1. **Host query** (`{"query": <name>, ...}`):
   - `<name>` comes from a closed catalogue: `results`, `jobs`, `attempts`, `campaigns`, `datasets`, `module_settings`, `module_events`, `nodes`, `node_metrics`, `store`.
   - Every query is filtered to the module's own rows.
   - Options: `fields`, `params` (literals, or `$route.*`, `$var.*`, `$node`, `$job`, `$campaign`, `$self`), `group_by` with `agg` (`count`, `mean`, `median`, `min`, `max`, `p95`, `sum`), `order_by`, `limit` of at most 200.
   - Each query runs under a 250 ms budget.
2. **Module view** (`{"view": <id>}`):
   - Declared in `[ui.views.<id>]` with a `shape` (`rows`, `kv`, `series`, `stat`), `columns`, `params` and `inputs` (for example `results`, `datasets:<kind>`, `store:<collection>`).
   - oarbankd calls `ui.view.compute` when the inputs' data version changes (debounced), or every `refresh_s` (at least 300).
   - oarbankd validates the result against the declaration and stores it. The console reads it like any other row.
   - A module that is down shows its last values with a "computed at" watermark.

## Actions

Buttons, row actions and form submits name an **operation**. That is either a core operation id, or `self.<verb>` for one of the module's own `[[operations]]`, which is registered at install as `mod.<module>.<verb>`.

- **The host draws the friction.** The button label is the registry title, and module `hint` text is only a secondary line. The tier comes from the registry. T1 confirms in the browser. T2 and T3 open the host's plan page, built from `op.plan`, and T3 asks for a typed confirmation string the host generates. The audit record comes from the host.
- **Effective tier** is the higher of the declared tier and the floor of its effects: `jobs.cancel` T1, `campaigns.cancel` T2, `datasets.delete` T2, `store.delete` T1, `external` T1. An effective tier of T2 or T3 needs `preview = true`, and so an `op.plan` implementation.
- **Execution.** `op.apply` returns *effects*: core changes the host applies on its single writer, under the same checks as any operation. A module update that adds an operation or effect, or lowers a tier, is shown as a diff at install and needs the owner's approval.
- **Responses** map to htmx without module markup: `toast`, `refresh`, `redirect`, `job` or `errors` (keyed by JSON Pointer).

## Forms

Form schemas are restricted JSON Schema:

- `type`, `enum`, `const`, bounds, `pattern`, and `format` ∈ `date-time`, `duration`, `uri`, `hostname`;
- `default`, `description`, `oneOf`, local refs only;
- `x-secret` marks values the host never echoes or stores in plans.

**Hints** set order, grouping, help and a widget from the host registry. An unknown widget falls back to the default widget for the type.

An `object` or `array` property is edited as JSON text (field name `pj.<name>`); the host parses it and validates the whole parameter set against the schema before forwarding it.

The host validates input before forwarding it. The module returns semantic errors keyed by JSON Pointer.

## The iframe placement

`[[ui.iframes]]` declares `id`, `entry` (an HTML file in the bundle), `title` and `bridge` capabilities.

**Serving and sandboxing:**
- The host serves the frame from the **module origin**: a separate listener, never the console origin. It carries a CSP whose `sandbox` directive allows scripts and forms and nothing else.
- The host embeds it with `sandbox="allow-scripts allow-forms"`, never `allow-same-origin`. The frame therefore has an opaque origin, no cookies, and no access to the console DOM.

**The bridge:**
- After load, the host transfers a `MessagePort` in a message `{type: "oarbank.bridge"}`. Every call goes through that port as `{id, method, params}` and is answered with `{id, result | error}`.
- Methods, each available only if declared in `bridge`:
  - `read.query` and `read.view`: the same catalogue and self-filter as pages;
  - `request.operation`: the host shows its own confirmation outside the frame, for every tier;
  - `resize`, bounded by the declared height limits;
  - `navigate`, to typed internal references only.
- A frame can never apply an operation itself, never read another module's data, and never reach oarbankd.

## Tooling

- `oarbank-sdk check` validates the manifest, every page file, and the cross-references to views, operations, iframes, form schemas and external URLs.
- `oarbank-sdk preview` renders the pages with the published console renderer, under the same CSP, against fixture data.
