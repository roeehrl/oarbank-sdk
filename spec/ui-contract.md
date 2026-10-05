# UI contract 1

A module defines its GUI as **data**. The console draws all of it with its own templates, and no module HTML, CSS or JavaScript ever runs in the console origin. Rich UIs that data cannot express use a **sandboxed iframe** served from a separate origin.

- Models: `oarbank_sdk.ui`.
- Schemas: `schemas/ui-page-1.schema.json`, and the `[ui]` and `[[operations]]` parts of `manifest-1`.
- Reference modules: [examples/toy](../examples/toy) (a frame using the bridge), [examples/reel](../examples/reel) (media
  components, datasets, checkpoints, downloads and an upload link), [examples/modelserver](../examples/modelserver)
  (services), [examples/gpuinfo](../examples/gpuinfo) (nodes' GPU APIs, the platform matrix) and
  [examples/taskbench](../examples/taskbench) (secrets, container images).
- How-to: [Build your module's GUI](../docs/module-gui.md).

## Versioning

- `requires.ui_contract = ">=1.0,<2"` in the manifest. The installer refuses a module outside the host's range.
- The host announces the contract version it renders in `initialize` (`host.capabilities` contains `ui_contract:1.<minor>`). The current minor is 1.2. Minor 1.1 added the media components; 1.2 adds host queries and fields for what cores 2.2 to 2.5 do for a module, links to downloads, the module's core tabs and uploads, and the frame context and `read.media` on the bridge.
- Within a major, changes are additive only. A component may declare `requires = "1.<minor>"` plus `fallback` (`placeholder` or `drop`), so a newer page still renders on an older host. `oarbank-sdk check` refuses a component without it when it uses something newer than 1.0: a newer component or cell type, a newer host query, a field a newer minor added to an older host query (by name: in `fields`, a column, a `kv` item, or a component's `field`), or a newer link.
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
| Action | `action` (an operation), `link` (a typed reference: `job`, `node`, `dataset`, `campaign`, `page`, an allowlisted `https` URL, and in 1.2 `tab`, `upload` and `download`; see Links) |
| Media (1.1) | `media` (one image, video, audio or text artifact), `gallery` (a grid of images or videos), `compare` (two images side by side or with a slider) |
| Frame | `iframe` (a view from `[[ui.iframes]]`) |

**Values** are raw and typed. The host formats them:

- Cell types: `text`, `number`, `integer`, `percent`, `bytes`, `duration`, `relative_time`, `timestamp`, `bool`, `digest`, `code`, `status`, `job_ref`, `node_ref`, `dataset_ref`, `campaign_ref`, `artifact_ref` (1.1: an artifact reference, see Media), `link`.
- Formats: `.Nf`, `.Ne`, `.N%`, `d`, `,d`, `s`, `d/d`, with an optional leading `+`.
- Columns may declare `direction` (`min` or `max`). That is what makes "best" and good/bad colouring generic.
- A wrongly typed value renders as "—".

**Styling** is semantic only: `tone` ∈ `ok`, `warn`, `error`, `info`, `neutral`, `running`. There is no colour, class or style.

**Visibility** uses `when`, a small comparison grammar the host evaluates in Python over a fixed context: `row.*`, `job.*`, `node.*`, `campaign.*`, `user.role` (the signed-in viewer's role: `viewer`, `operator` or `admin`), `setting.*`. There is no client-side expression language.

**Links** are typed references the host turns into its own URLs; a reference it cannot resolve renders as text:

| Target | Goes to |
|---|---|
| `job`, `node`, `dataset`, `campaign` | the console's page for it; cells of type `job_ref`, `node_ref`, `dataset_ref`, `campaign_ref` link the same way |
| `download: true` (1.2), beside `dataset` or `campaign` | its download: the dataset's files, or the campaign's artifacts, as a zip. A `dataset_ref` or `campaign_ref` column with `"download": true` links each cell to its download |
| `page` | one of the module's pages |
| `tab` (1.2) | the module's own core tab: `secrets` (only for a module that declares secrets) or `health` |
| `upload` (1.2) | `{kind, then?}`: the console's folder upload with this module and `kind` (one of its `[datasets].kinds`) filled in. With `then = "self.<verb>"`, an operation whose `target = "dataset"`, the host offers that operation on the new dataset once it is registered, with its own title, tier and confirmation, and then returns to the page |
| `url` | an `https` URL in the manifest's `ui.external_urls`, in a new tab |

## Data

Every data component names a `source`, which is one of two kinds. The console never waits on the module for either.

1. **Host query** (`{"query": <name>, ...}`):
   - `<name>` comes from a closed catalogue (below).
   - Every query is filtered to the module's own rows: datasets are the module's own and the operator's (unowned) of the
     module's kinds, never another module's; events are those about the module or one of its jobs or campaigns. No
     query returns a secret's value.
   - Options: `fields`, `params` (literals, or `$route.*`, `$var.*`, `$node`, `$job`, `$campaign`, `$self`; `$job`,
     `$node` and `$campaign` are the id of the job, node or campaign a panel or frame is shown for), `group_by` with
     `agg` (`count`, `mean`, `median`, `min`, `max`, `p95`, `sum`), `order_by`, `limit` of at most 200. A query filters
     on its own params only (`ui.QUERY_PARAMS`); the others are ignored. The console and the preview shape rows with the
     same code (`oarbank_sdk.render.shape`).
   - Each query runs under a 250 ms budget.
2. **Module view** (`{"view": <id>}`):
   - Declared in `[ui.views.<id>]` with a `shape` (`rows`, `kv`, `series`, `stat`), `columns`, `params` and `inputs` (for example `results`, `datasets:<kind>`, `store:<collection>`). `datasets:<kind>` is owner-scoped as the `datasets` query is.
   - oarbankd calls `ui.view.compute` when the inputs' data version changes (debounced), or every `refresh_s` (at least 300).
   - oarbankd validates the result against the declaration (`ui.validate_view`: shape, row limit, and for rows only the declared columns) and stores it. The console reads it like any other row.
   - A module that is down shows its last values with a "computed at" watermark.

| Query | One row per | Fields (1.2 additions in *italics*) | Params |
|---|---|---|---|
| `results` | accepted result | `result_id`, `job_id`, `node_id`, `value`, `at`, `digest`, the result's scalar fields | `job_id`, `node_id` |
| `jobs` | job | `job_id`, `state`, `kind`, `dataset_id`, `priority`, `created_at`, `done_at`, `exec_failures`, *`stage`*, *`campaign_id`* | `job_id`, `state`, `kind`, `dataset_id`, `campaign` |
| `attempts` | attempt | `attempt_id`, `job_id`, `node_id`, `state`, `phase`, `cpu_s`, `granted_at`, `ended_at`, `end_reason`, *`rss_gb`*, *`module_version`*, *`resumed_from_attempt`*, *`resumed_from_node`*, *`resume_digest`* | `attempt_id`, `job_id`, `node_id`, `state` |
| `campaigns` | campaign | `campaign_id`, `name`, `state`, `priority`, `weight`, `labels`, `created_at`, `finished_at`, *`placement_mix`*, *`placement_unit`*, *`placement_pin`*, *`bound_class`*, *`binding_state`*, *`binding_source`*, *`stranded_since`* | `campaign`, `state` |
| `datasets` | dataset | `dataset_id`, `kind`, `created_at`, its meta's scalar keys, *`owner`* (`module`, `operator`), *`module`*, *`platform`*, *`files`*, *`size`*, *`origins`* (hosts), *`pinned`* | `dataset_id`, `kind` |
| `module_settings` | (one) | the module's settings | |
| `module_events` | event | `event_id`, `ts`, `kind`, `reason`, `node_id`, `job_id`, `campaign_id` | `kind`, `job_id`, `campaign` |
| `nodes` | node | `node_id`, `hostname`, `lifecycle`, `desired_state`, `online`, `module_state`, `last_heartbeat_at`, *`platform`*, *`os`*, *`arch`*, *`os_version`*, *`gpu_apis_host`*, *`gpu_apis_containers`*, *`container_gpu`*, *`container_runtime`* (`wslc` on Windows), *`container_state`* (`ready`, `starting`, `missing`, `failed`, `absent`), *`container_platforms`*, *`container_detail`*, *`container_missing`* (`[{what, detail, fix}]`), *`container_fixes`*, *`services`* (this module's, as `services` rows), *`service_health`*, *`folders`* (this module's: `{id: {access, status}}`), *`folders_ok`*, *`enforcement`* (`{capability: state}` for what this module's sandbox needs), *`sandbox_gaps`* | `node_id` |
| `node_metrics` | sample | `ts` and the node's telemetry | `node_id` (required) |
| `store` | document | the document and `_key` | `collection` (required), `campaign` |
| *`secrets`* | declared secret | `name`, `description`, `set`, `fingerprint` (keyed, as the Secrets tab shows it), `changed_at`, `changed_by`, `node_values`, `stages`, `coordinator`, `unreadable` | `name` |
| *`checkpoints`* | open job with a checkpoint | `job_id`, `attempt_id`, `node_id`, `generation`, `seq`, `files`, `size`, `at`, `digest` | `job_id`, `node_id` |
| *`services`* | node and service | `node_id`, `hostname`, `service`, `health`, `state` (`ready`, `starting`, `stopped`), `stopped_reason` (`held: <reason>`, `disabled`, `withdrawn`, `gpu api missing`, `idle`), `error`, `gpu_api_missing`, `lifecycle`, `users`, `endpoint`, `reported_at` | `node_id`, `service` |
| *`pins`* | pinned dataset | `dataset_id`, `kind`, `platform`, `files`, `size`, `state` (`registered`, `missing`, `conflict`), `conflict`, `alert` | `dataset_id` |
| *`images`* | container image first run | `digest`, `image`, `set_name`, `key_sha256`, `first_run_at`, `node_id`, `attempt_id`, `registry`, `repository`, `platform` | `set_name` |
| *`platforms`* | platform | `platform`, `runner` (`supported`, `unsupported`, `undeclared`), `reason`, `coordinator` (`supported`, `unsupported`, `any`), `coordinator_reason`, `nodes`, `online`, `certified`, `doctor_failed` | `platform` |

A list value in a `text` cell is shown joined with commas. A field a node does not report is null.

## Actions

Buttons, row actions and form submits name an **operation**. That is either a core operation id, or `self.<verb>` for one of the module's own `[[operations]]`, which is registered at install as `mod.<module>.<verb>`.

- **The host draws the friction.** The button label is the registry title, and module `hint` text is only a secondary line. The tier comes from the registry. T1 confirms in the browser. T2 and T3 open the host's plan page, built from `op.plan`, and T3 asks for a typed confirmation string the host generates. The audit record comes from the host.
- **The viewer's role.** A button whose operation needs a higher role than the viewer's (`min_role`) is drawn disabled, naming the role; oarbankd refuses it anyway.
- **Effective tier** is the higher of the declared tier and the floor of its effects: `jobs.cancel` T1, `campaigns.cancel` T2, `datasets.delete` T2, `store.delete` T1, `external` T1. An effective tier of T2 or T3 needs `preview = true`, and so an `op.plan` implementation.
- **Execution.** `op.apply` returns *effects*: core changes the host applies on its single writer, under the same checks as any operation. A module update that adds an operation or effect, or lowers a tier, is shown as a diff at install and needs the owner's approval.
- **Responses** map to htmx without module markup: `toast`, `refresh`, `redirect`, `job` or `errors` (keyed by JSON Pointer).

## Forms

Form schemas are restricted JSON Schema:

- `type`, `enum`, `const`, bounds, `pattern`, and `format` ∈ `date-time`, `duration`, `uri`, `hostname`;
- `default`, `description`, `oneOf`, local refs only.

A form never takes a credential: a value handed to an operation reaches module code and the plan. Declare it in
`[[secrets]]` instead ([manifest.md](manifest.md#secrets)); the owner sets it through the core, and `oarbank-sdk check`
refuses `x-secret` in a form schema.

**Hints** set order, grouping, help and a widget from the host registry. An unknown widget falls back to the default widget for the type.

An `object` or `array` property is edited as JSON text (field name `pj.<name>`); the host parses it and validates the whole parameter set against the schema before forwarding it.

The host validates input before forwarding it. The module returns semantic errors keyed by JSON Pointer.

## Media

Modules whose results are media show them without an iframe: generated images and grids, rendered frames, encoded
video, transcripts. Each component sets `requires = "1.1"` and a `fallback` (the page check refuses one without), so a
1.0 host draws the fallback.

```json
{"type": "gallery", "requires": "1.1", "fallback": "placeholder", "kind": "image", "source": {"view": "frames"},
 "field": "frame", "caption_field": "seed", "columns": 4}
{"type": "media", "requires": "1.1", "fallback": "drop", "kind": "video", "source": {"view": "renders"}, "field": "clip"}
{"type": "compare", "requires": "1.1", "fallback": "placeholder", "source": {"view": "pair"}, "left": "a", "right": "b",
 "mode": "slider", "labels": ["seed 1", "seed 2"]}
```

- `media` shows one artifact (`kind`: `image`, `video`, `audio` or `text`) from the first row of its source; `gallery`
  one image or video per row, each by its thumbnail when it has one and linked to its job; `compare` two images from
  the first row.
- **Artifact references.** The named field holds `{"job": <id>, "artifact": <name>, "path": <path>}` (a file of a job's
  canonical result) or `{"digest": <sha256>, "thumbnail"?: <sha256>}` (a blob the module can see: its files, its
  datasets and its jobs' artifacts, and the operator's datasets). A view declares such a column with type `artifact_ref`
  (core 2.5), since only declared columns reach the console; a table shows it as its artifact and path.
- **Ownership.** The host checks every reference on every render: a job of this module with that artifact file, or a
  digest the module can see. Anything else renders as a placeholder, and no URL is made.
- **Bytes come only from the module origin**, never the console's: the host serves each checked reference at a
  short-lived capability URL on the origin it serves frames from, which carries no session. It sniffs the file's first
  bytes and serves only an allowed type for the component's kind, with exactly that `Content-Type`,
  `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`:

  | Kind | Allowed | Size cap |
  |---|---|---|
  | `image` | PNG, JPEG, WebP, AVIF | 64 MiB |
  | thumbnails | PNG, JPEG, WebP, AVIF | 1 MiB |
  | `video` | MP4, WebM | 16 GiB |
  | `audio` | MP3, M4A, Ogg, WAV | 1 GiB |
  | `text` | UTF-8 text: plain text, VTT, SRT and Markdown, all shown as plain text | 4 MiB |

  SVG, HTML and anything else are refused, whatever the file is called. Video and audio honour `Range` requests.
  `oarbank_sdk.media` is the allowlist (`sniff`, `problem`), shared by the console and `oarbank-sdk preview`.
- **Thumbnails come from the module.** A runner names a small preview image for any result file
  (`artifacts[].files[].thumbnail`, [envelopes.md](envelopes.md#result-envelope)); the host never transcodes module
  media.
- Text is shown in a sandboxed frame with no scripts; images, video and audio in the console's own elements.

## The iframe placement

`[[ui.iframes]]` declares `id`, `entry` (an HTML file in the bundle), `title` and `bridge` capabilities.

**Serving and sandboxing:**
- The host serves the frame from the **module origin**: a separate listener, never the console origin. It carries a CSP whose `sandbox` directive allows scripts and forms and nothing else.
- The host embeds it with `sandbox="allow-scripts allow-forms"`, never `allow-same-origin`. The frame therefore has an opaque origin, no cookies, and no access to the console DOM.

**The bridge:**
- After load, the host transfers a `MessagePort` in a message `{type: "oarbank.bridge", module, view, context}`. Every call goes through that port as `{id, method, params}` and is answered with `{id, result | error}`.
- `context` (1.2) is the subject the frame is shown for: `{job}` on a job panel, `{node}` on a node panel, `{campaign}` on a campaign panel, plus the page's `route` and `var` values. The host sends it with every read and interpolates `$job`, `$node`, `$campaign` server side; it is never an authority (every read stays owner-scoped).
- Methods, each available only if declared in `bridge`. The console's script refuses an undeclared method before it makes any request, and the console's server checks the frame's declaration again on every request:

  | Method | Answer |
  |---|---|
  | `read.query` | the same catalogue, scoping and shaping as pages |
  | `read.view` | the stored view (a campaign-scoped view reads the context's campaign) |
  | `read.media` (1.2) | `{src, thumb, job}` for an artifact reference of this module: capability URLs on the module origin, checked as the media components' are. The frame shows them with `<img>`, `<video>` or `<audio>` (its CSP allows `img-src` and `media-src` on the module origin; it cannot fetch) |
  | `request.operation` | `{op, target, params}`: what a page action may name, `self.<verb>` or a core operation. The viewer's role must meet its `min_role`, and a core job, campaign or dataset operation must name one of the module's own. The host shows its own confirmation outside the frame, with the registry title, tier and target, for every tier, then posts it as a page button does, with the session's CSRF token (which the frame never sees) and `params` as JSON; T2 and T3 continue on the plan page |
  | `resize` | bounded by the declared height limits |
  | `navigate` | `{to}`: a typed reference (as `link`, never a URL); the host maps it to one of its pages and the top page goes there |
- A frame can never apply an operation itself, never read another module's data, and never reach oarbankd.

## Tooling

- `oarbank-sdk check` validates the manifest, every page file, and the cross-references to views, operations, iframes, form schemas and external URLs.
- `oarbank-sdk preview` renders the pages with the published console renderer, under the same CSP, against fixture data:
  `fixtures/ui/queries/<query>.json`, shaped with the console's code; views computed from `fixtures/ui/inputs.json` and
  validated as oarbankd validates them; panels with the subject in `fixtures/ui/context.json`; media references resolved
  to files in `fixtures/ui/media/` (`{job, artifact, path}` is `<artifact>/<path>` there), served through the same
  allowlist. It enforces CSRF and the bridge's capabilities as the console does.
- `oarbank-sdk preview --check` renders every page and panel without a server and runs axe-core (WCAG 2.0/2.1 A and AA,
  best practice) in Node with jsdom; serious or critical violations fail it. It needs `OARBANK_SDK_NODE_MODULES` (a
  `node_modules` with `axe-core` and `jsdom`) and says when it skipped.
- The conformance kit's `ui` suite computes every view from the fixtures and validates it, renders every page and panel,
  and runs the axe check where it can.
