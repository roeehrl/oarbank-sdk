# Build your module's GUI

A module's GUI is data: JSON pages and panels the console draws with its own components, rows from host queries and
views your module computes, buttons that name operations, and, for what data cannot express, a sandboxed frame that
talks to the console over a bridge. No module HTML or script ever runs in the console's origin. This guide builds one
from the toy and reel examples; the rules are in [spec/ui-contract.md](../spec/ui-contract.md).

## 1. Declare pages and panels

```toml
[requires]
ui_contract = ">=1.2,<2"

[ui]
icon = "cube"
pages = [{ id = "overview", title = "Reel", slot = "module.overview", file = "ui/pages/overview.json" },
         { id = "data", title = "Data", slot = "module.page", file = "ui/pages/data.json" }]
panels = [{ id = "job_checkpoint", title = "Checkpoints", slot = "job.detail.panel", file = "ui/panels/job.json", when = "job.module == self" }]
```

- `module.overview` is the first tab of your module's page; `module.page` adds up to six more. The core's own tabs
  (Health, Secrets, Settings, Operations audit) always follow.
- Panels go on job, node and campaign pages (`job.detail.panel`, `node.detail.panel`, `campaign.panel`); `when` decides
  where they show.

## 2. Write a page

A page is a list of components. A data component names a `source`: a **host query** (the core's rows, always only
your module's) or a **view** your module computes.

```json
{"ui_contract": "1.2", "title": "Reel data", "body": [
 {"type": "table", "source": {"query": "results", "fields": ["job_id", "value", "at"], "order_by": "at"},
  "columns": [{"key": "job_id", "label": "job", "type": "job_ref"}, {"key": "value", "type": "number", "format": ",d"},
              {"key": "at", "label": "when", "type": "relative_time"}]},
 {"type": "stat", "label": "best", "source": {"query": "results", "agg": {"value": "max"}}, "field": "value"},
 {"type": "table", "source": {"view": "renders"}, "columns": [{"key": "job", "type": "job_ref"}, {"key": "frames", "type": "integer"}]}
]}
```

- Host queries take `params` (each query filters on a fixed set, such as `job_id` or `node_id`), `fields`, `group_by`
  with `agg` (`count`, `mean`, `median`, `min`, `max`, `p95`, `sum`), `order_by` and `limit` (at most 200).
- A view is declared in `[ui.views.<id>]` with its `shape`, `inputs` and `columns`, and computed by your
  `ui.view.compute` verb when its inputs change. Only declared columns reach the console. The console never waits for
  your module: a view that has not been computed shows a placeholder, and a module that is down shows its last values.
- Values are raw; the console formats them by the column's `type` and `format`. Styling is a `tone`, never a colour.

## 3. Show what the core knows about your module

UI contract 1.2 adds host queries for what cores 2.2 to 2.5 do for a module, all filtered to your module and never
holding a secret value:

| Query | Rows |
|---|---|
| `secrets` | each declared secret: set or not, its fingerprint, when and by whom it changed, the stages that receive it |
| `checkpoints` | the latest checkpoint of each of your open jobs |
| `services` | each of your services on each node: its state, health and why it stopped |
| `pins` | each pinned dataset of your bootstrap stages: registered, missing, or held by something else |
| `images` | the container set images your jobs ran, with their first run |
| `platforms` | your per-platform support matrix beside the fleet's nodes |

and new fields on older ones: resume fields on `attempts`; a node's platform, GPU APIs, container runtime and its
fixes, your services, your folders and its sandbox enforcement on `nodes`; placement on `campaigns`; owner, size,
files, origin hosts and `pinned` on `datasets`.

A component that uses any of them sets `"requires": "1.2"` and a `fallback` (`placeholder` or `drop`), so a 1.1 console
draws the fallback; `oarbank-sdk check` tells you which components need it.

```json
{"type": "table", "requires": "1.2", "fallback": "placeholder", "source": {"query": "checkpoints"},
 "columns": [{"key": "job_id", "type": "job_ref"}, {"key": "node_id", "type": "node_ref"}, {"key": "size", "type": "bytes"}]}
```

## 4. Panels and their subject

A panel is shown for a job, a node or a campaign. `$job`, `$node` and `$campaign` in a source's `params` are that
subject's id, and `when` reads its fields (`job.module`, `job.state`, `node.online`) and the viewer's role
(`user.role`):

```json
{"type": "kv", "requires": "1.2", "source": {"query": "checkpoints", "params": {"job_id": "$job"}},
 "items": [{"label": "latest checkpoint", "field": "seq", "type": "integer"}, {"label": "size", "field": "size", "type": "bytes"}]}
```

## 5. Buttons, forms and links

- `action`, row actions and a form's `submit` name an operation: `self.<verb>` for one of your `[[operations]]`, or a
  core operation id. The console draws the button with the registry's title and tier, asks for confirmation (T1) or
  shows a plan (T2 and T3), and draws it disabled for a viewer whose role is below the operation's `min_role`.
- `link` targets are typed: `job`, `node`, `dataset`, `campaign`, `page`, an allowlisted `url`, and in 1.2 `tab`
  (`secrets` or `health`: your core tabs), `download` (beside `dataset` or `campaign`: its download) and `upload`.
  Table cells of type `dataset_ref` and `campaign_ref` link the same way, to the download with `"download": true`.
- An upload link sends the operator to the console's folder upload with your module and a kind filled in; with `then`,
  your importer operation is offered on the new dataset once it is registered:

```json
{"type": "link", "requires": "1.2", "fallback": "drop", "text": "Upload a folder, then use it as an asset",
 "to": {"upload": {"kind": "upload", "then": "self.adopt_upload"}}}
```

## 6. A frame, for what data cannot express

```toml
[ui]
iframes = [{ id = "explorer", entry = "ui/frames/explorer.html", title = "Toy explorer", bridge = ["read.view", "read.query", "request.operation", "resize", "navigate"] }]
```

```json
{"type": "iframe", "view": "explorer", "title": "Toy explorer", "height": 320}
```

The console serves the entry's directory from a separate origin, inside `sandbox="allow-scripts allow-forms"` with no
network (`connect-src 'none'`). Your script waits for the console's first message, which carries a port and the frame's
context, and makes every call through the port. A call to a method the frame did not declare in `bridge` is refused, by
the console's script and again by the console's server.

```js
let port = null, seq = 0, pending = {}, context = {};
function call(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++seq; pending[id] = {resolve, reject};
    port.postMessage({id, method, params});
  });
}
window.addEventListener("message", (ev) => {
  if (port || !ev.ports || !ev.ports[0] || !ev.data || ev.data.type !== "oarbank.bridge") return;
  port = ev.ports[0];
  context = ev.data.context || {};                      // {job} on a job panel, {node}, {campaign}; route variables
  port.onmessage = (m) => { const p = pending[m.data.id]; if (!p) return; delete pending[m.data.id];
                            m.data.error ? p.reject(m.data.error) : p.resolve(m.data.result); };
  start();
});
async function start() {
  const sums = (await call("read.view", {view: "sums"})).rows;
  const mine = (await call("read.query", {query: "results", params: {job_id: "$job"}})).rows;   // $job: the context's job
  // call("navigate", {to: {job: 812}})                   a typed reference; the console picks the URL
  // call("request.operation", {op: "self.set_favorite", params: {n: 7}})   the console confirms; params stay JSON
  // call("read.media", {ref: {job: 812, artifact: "frames", path: "f.png"}, kind: "image"})  -> {src}: use it in <img>
  call("resize", {height: document.body.scrollHeight + 16});
}
```

| Method | Capability | Answer |
|---|---|---|
| `read.view` | `read.view` | the stored view |
| `read.query` | `read.query` | the host query's rows, as on a page |
| `read.media` | `read.media` | `{src, thumb, job}`: capability URLs for one of your artifacts (media is shown with `<img>`, `<video>` or `<audio>`; the frame cannot fetch) |
| `request.operation` | `request.operation` | the console confirms outside the frame and runs it as a page button would; a core job, campaign or dataset operation must name one of yours |
| `resize` | `resize` | the frame's height, up to twice the declared one |
| `navigate` | `navigate` | the console goes to a typed reference (never a URL) |

## 7. Preview and check

```sh
oarbank-sdk check oarbank-module.toml          # page files, cross-references, which components need requires
oarbank-sdk preview oarbank-module.toml        # http://127.0.0.1:8700/: the console's renderer, CSP and bridge
oarbank-sdk preview oarbank-module.toml --check   # render everything, check the views, run axe (no server)
oarbank-sdk conform .                          # the ui suite: views against their declarations, every page and panel
```

The preview reads fixtures from `fixtures/ui/`: `queries/<query>.json` (rows for each host query), `inputs.json` (what
your views are computed from), `settings.json`, `media/` (artifact files) and `context.json` (the subject panels are
shown for and the viewer's role: `{"job": {"id": 1, "module": "reel"}, "user": {"role": "operator"}}`). It shapes rows
with the console's own code, validates your views as the console does, and refuses a post without the CSRF token, as
the console does. `--check` and the conformance kit run axe-core in Node with jsdom when `OARBANK_SDK_NODE_MODULES`
names a `node_modules` holding `axe-core` and `jsdom`, and say so when they skip it.
