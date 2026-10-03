# Module protocol 1

The coordinator side of a module is a separate process. The core (oarbankd) spawns it and talks to it over **newline-delimited JSON-RPC 2.0 on stdin/stdout**: one JSON object per line, UTF-8, no embedded newlines. Anything the module writes to stderr goes to the module's log. No module code ever runs inside oarbankd. Models: `oarbank_sdk.module_protocol`. Schemas: `module-*-1.schema.json`.

## Lifecycle

1. **Spawn.** The core starts the process with `coordinator.exec` from the installed bundle, in the module's own venv (for `runtime.kind = "uv"`), with a clean environment and the module's install directory as cwd. It applies the coordinator variant for its own platform (`coordinator.for_platform`), and the environment carries `OARBANK_PLATFORM` and the module's `coordinator.env`.
2. **Handshake.** Host → module: `initialize {protocol_versions, host, module_digest, settings}`, where `host` is `{name, version, capabilities, platform}` (see "Host capabilities"). The module answers `{protocol_version, module, capabilities}`: it picks one major from `protocol_versions`, or fails with `-32001` and `data.supported`. The host then sends the `initialized` notification.
3. **Verbs.** Requests flow, at most `coordinator.concurrency` in flight at once, each bounded by `coordinator.timeouts_s`.
4. **Shutdown.** The host sends the `shutdown` notification and closes stdin. After 5 s it terminates the process container (POSIX: SIGTERM, then SIGKILL; Windows: Job termination). Never rely on a notice.

The host restarts crashed modules with backoff. Repeated crashes put the module in the `module_fault` state. Its jobs stop being scheduled, jobs already running finish, and their results wait for evaluation. A module fault never counts against a node or a job.

## The module is stateless

The host owns leases, attempts, retries, certification, replication, disputes and every row in the database. Each call carries everything it needs. Verbs must be **pure functions of their input**: same input, same output, with no filesystem or network access outside the declared host callbacks. The conformance kit checks this. That lets the host evaluate completions *outside* its write lock, cache verb results, and restart a module at any time. A module that needs durable state (a search's trials, say) keeps it in its **store**: it writes documents through `store.write` effects and reads them back with `host.store.*`, so a restarted module picks up exactly where it was.

## Verbs (host → module)

| Method | Required | Purpose |
|---|---|---|
| `params.check` | yes | Validate and normalise a parameter set before any job exists. It returns `ok`, `normalized_params` and `errors[] {path, message, code}`. Search strategies call it for every sampled config. |
| `job.plan` | yes | Turn `(params, datasets, study)` into the evaluations to run. Each is a `PlanItem`: `key_inputs` (canonical; the host computes `job_key = H(module_id, compat, key_inputs)`), `datasets`, `stages` (the manifest stages to run, for example `["call","score"]` or `["eval"]`), per-stage resource overrides within the manifest's bounds, and [beta] a `group` (at most 64 characters) and `platforms` (tokens or OSes). |
| `spec.build` | yes | Turn plan items into spec payloads, one per stage, at `target_spec_version` or lower. |
| `result.evaluate` | yes | Judge one completed attempt from `(spec envelope, result envelope, stage)`. It returns `verdict`, `reason`, `value`, `digest`, `digest_version`, `summary` and `fields`. |
| `golden.list` | yes | Golden jobs for a node class: `platform`, `os_version`, `cpu`, `gpus`, `capabilities` (list) and `pools` (name → 1 if the node has it, 0 if not), never node identity. Each golden has `key_inputs`, `datasets`, `stages` and `expected` (at least `digest` and `digest_version`, or keys your `golden.compare` checks), and [beta] `platforms` (only nodes of these tokens or OSes get it) and `expected_by_platform` (the expected value per token or OS; the host resolves the token, then the OS, then `expected`). A verb may read its own bundle's files (they are immutable), for example golden fixtures in `goldens/*.json` stored as `Golden` JSON; `[goldens].fixtures` only tells tools where they are; `oarbank_sdk.goldens.load(root, glob, node_class)` reads them, filtered and resolved for the node class. It must never read host state except through callbacks. |
| `result.merge` | multi-stage | Build the canonical result of an evaluation from its accepted stage results. |
| `result.upgrade` | optional | Upgrade a stored result payload from version N to version M. |
| `golden.compare` | optional | Custom golden comparison, used when `goldens.compare = "verb"`. |
| `study.metrics` | optional | Values for the manifest's `ui.study_columns`, per trial. |
| `params.distance` | optional | Distance between two parameter sets, used by search strategies and deduplication. |
| `ui.view.compute` | optional | Compute a declared module view from the inputs the host resolved (UI contract 1). |
| `op.plan` / `op.apply` | optional | Preview and apply one of the module's own `[[operations]]`; `op.apply` returns effects (below). |
| `integrity.check` | optional | Verify the module's own state through host callbacks; see "Integrity checks" below. |
| `move.preflight` / `move.postflight` / `move.cancelled` | optional | The module's part in a coordinator move; see "Coordinator moves" below. |
| `campaign.tick` | optional | Advance one of the module's running campaigns. The host calls it every few seconds per running campaign, with the campaign (including [beta] its `placement {mix, unit, class, state}`) and all its jobs and canonical results (each with the `platform` that produced it); it returns effects limited to `coordinator.campaign_effects`. A campaign the module leaves running with no open jobs stays running until the module marks it `done`. |

An optional verb is available only if the module lists it in `initialize.capabilities`. A call to an unadvertised verb fails with `-32003`.

### Verdicts

| Verdict | Meaning | What the host does |
|---|---|---|
| `accept` | The result is valid. | Records it. It then goes through replica and quorum rules like any other result. |
| `reject` | The result is wrong for this spec: wrong mode, malformed, or a missing artifact. | Counts it against the node, as a bad result, and re-runs the job elsewhere. |
| `retry` | The failure was transient and says nothing about the node. | Re-runs the job without counting it as a fault. |
| `fail_permanent` | The job can never succeed. | Quarantines the job. The node is not blamed. |

`reason` is a core reason code or a module code of the form `<module-short>/<code>`.

## Host callbacks (module → host)

A module can make requests back to the host while it is handling a verb, if the manifest lists the matching `coordinator.permissions` entry. Any other call fails with `-32002`.

| Method | Permission | Returns |
|---|---|---|
| `host.datasets.query {kind?, ids?, attrs, limit}` | `datasets:read` | Datasets matching the query (by kind and equality on attributes, or by id), each `{id, kind, attrs}`. |
| `host.blobs.stat {digest}` | `blobs:stat` | Whether the coordinator holds a blob, and its size. |
| `host.settings.get {key}` | `settings:read:self` | A value from this module's own settings. |
| `host.store.get {collection, key}` | `store:read:self` | One document from this module's store, or null. |
| `host.store.query {collection, where, limit}` | `store:read:self` | This module's documents in a collection; `where` is equality on top-level fields. Each document carries its `_key`. |
| `host.nodes.query {certified_for_self, platforms}` | `nodes:read` | Ready nodes (`node_id`, `hostname`, `online`, `module_state`, and with `nodes.platform` also `platform`, `os`, `arch`, `os_version`), by default only those certified for this module; `platforms` (tokens or OSes) filters them. `Host.fleet_platforms()` counts them per platform. |
| `host.files.list {prefix, limit}` | `files:read:self` | This module's files under a path prefix, sorted: `path`, `digest`, `size`, `updated_at`. |
| `host.files.stat {path}` | `files:read:self` | One file's entry, or `exists: false`. |
| `host.files.read {path, offset, length}` | `files:read:self` | Up to 1 MiB of a file as `content_b64`, with the file's `size`, `digest` and `eof`. `Host.files_read` reads a whole file. |
| `host.jobs.query {campaign_id, states?, limit}` | `jobs:read:self` | This module's jobs in one of its campaigns: `job_id`, `job_key`, `state`, `kind`, `stage`, `dataset_id`, `labels`, `group`, `target_node`, and for done jobs the canonical result's `value`, `digest`, `fields`, `node_id` and `platform`. |

## Effects

`op.apply` and `campaign.tick` never write. They return effects, which the host checks (declared kind, ownership) and applies on its single writer inside the operation's transaction, together with its audit row. A module can only touch its own campaigns, jobs, store documents and settings.

| Kind | Arguments | Effect |
|---|---|---|
| `campaigns.create` | `campaign_id` (a lowercase letter, then 3–40 of `[a-z0-9_]`; `c_…` by convention), `name`, `priority`, `weight`, `labels`, [beta] `placement {mix, unit, bind, pin}` | A running campaign owned by the module. An id that already exists is an error and the whole operation fails: derive ids so a repeat is intended, or check your store first. `placement` may be stricter than the manifest's, never looser (422 `placement_looser_than_manifest`); `pin` binds the campaign to one class now ([platforms.md](platforms.md#placement)). |
| `campaigns.update` | `campaign_id`, and any of `state` (`running`, `paused`, `done`), `name`, `priority`, `weight`, `labels` (merged), `message` | Changes the campaign. `done` is how a module finishes one. |
| `campaigns.cancel` | `campaign_id` | Cancels it and its open jobs. |
| `jobs.enqueue` | `campaign_id`, `jobs[]` of `{job_key, spec, spec_version (default 1), labels (your grouping: shown in views and returned by `host.jobs.query`; part of the no-op check), dataset_id, datasets, mounts, resources (what the job reserves; default: the stage's manifest resources; it must fit a node), timeout_s, priority, subpriority, target_node, name, group, platforms}` (at most 5000; [beta] `group`: at most 64 characters, the unit of a `group` placement; `platforms`: tokens or OSes the job may run on) | Adds jobs. `spec` is the stage payload; see "Jobs on the wire" below. The same `(job_key, labels)` twice in a campaign is a no-op. An untargeted job whose key already has a canonical result of this module is done at once (result cache). A `done` campaign runs again. |
| `jobs.cancel` | `job_ids[]` | Cancels the module's open jobs. |
| `store.write` / `store.delete` | `collection`, `key`, `doc` (an object, at most 256 KiB) | Upserts or removes a module document. |
| `datasets.create` | `dataset_id`, `kind`, `meta`, `files[]` of `{path, digest, size}`, [beta] `platform` (a token; required for kinds in `[datasets].platform_bound`) | Registers a dataset whose blobs the coordinator already holds. A dataset with a `platform` is used only by jobs on that platform. |
| `files.write` | `path`, `content_b64` (at most 1 MiB decoded) | Stores a small file in the module's files. |
| `files.put` | `path`, `digest` | Names a blob the coordinator already holds (a job artifact, a dataset file) as one of the module's files. |
| `files.delete` | `path`, or `prefix` | Removes files (the blobs stay; other rows may name them). |
| `module_settings.update` | settings to merge | Changes the module's own settings. |

`oarbank_sdk.effects` builds them: `fx.campaign_create(cid, name, priority=0, weight=1, labels=None, placement=None)`,
`fx.placement(mix, unit=None, pin=None, bind=None)`, `fx.jobs_enqueue(cid, jobs)` with
`fx.job(job_key, spec, *, group=None, platforms=None, target_node=None, ...)` items, and
`fx.datasets_create(dataset_id, kind, files, meta=None, platform=None)`. Unset fields are left out.

## Host capabilities

`initialize`'s `host.capabilities` names the host features a module may rely on. A module that uses one of these only
at run time checks it with `ctx.host_has(name)` (constants in `oarbank_sdk.module_protocol`); a manifest that depends on
them needs `requires.core >= 2.2` instead.

| Capability | The host |
|---|---|
| `placement.v1` | Honours `campaigns.create` `placement`, `jobs.enqueue` `group` and `platforms`, `datasets.create` `platform`, and reports `placement` and job `platform` in `campaign.tick` and `host.jobs.query`. |
| `nodes.platform` | Returns `platform`, `os`, `arch` and `os_version` from `host.nodes.query` and accepts its `platforms` filter. |
| `goldens.by_platform` | Passes the node's `platform` and `os_version` in `golden.list`'s node class, drops goldens whose `platforms` exclude the node, and resolves `expected_by_platform`. |
| `coordinator.variants` | Starts the coordinator side with its variant for the host's platform, sets `OARBANK_PLATFORM` and `coordinator.env`, and sends `host.platform`. |

`host.platform` [beta] is the coordinator host's platform token (`ctx.host_platform`). Verbs keep job keys
platform-independent: never put it in `key_inputs`.

## Module files

A module that needs durable files on the coordinator keeps them in its **files**: a private namespace of
relative paths (plain segments, no `.` or `..`, at most 512 characters), each naming a content-addressed blob.
Module code never touches the coordinator's disk. Verbs read through `host.files.*` and write by returning
`files.*` effects, which the host applies atomically with the operation's audit row. Small files arrive inline
(`files.write`, at most 1 MiB). Larger ones are produced by a job: its runner writes them as artifacts, and the
module binds them with `files.put`. Files, store documents, datasets and settings are everything a module
keeps, and all of it moves with the coordinator. `oarbank_sdk.effects` builds these effects.

## Integrity checks

`integrity.check {scope, deep, move_id, now}` asks the module to verify its own state, reading only through host
callbacks. It returns `ok`, a list of named `checks` (`error` checks decide `ok`; `warn` and `info` are shown),
and an optional `fingerprint`: a digest of the state the module considers its own (`oarbank_sdk.effects.fingerprint`).
The host adds its own checks of the module's files (every blob present, sizes or, with `deep`, digests), records
every outcome and shows the last one on the module's page.

| Scope | When |
|---|---|
| `routine` | Daily. A failure opens an `integrity_failed:<module>` alert; a later pass resolves it. |
| `on_demand` | An operator ran `oarbank module check` or pressed "Check integrity". |
| `move_source` | The old coordinator, frozen for a move. A failure aborts the move unless the operator forced it. |
| `move_target` | The target, on its verified copy before it reports ready. The fingerprint must equal the source's, or the move aborts. On the target, files that your move rules rebuild or drop are listed but cannot be read. |

The fingerprint is compared across machines: derive it from your state only (sorted paths and digests, store
documents), never from time, paths on disk or anything your move rules leave behind.

## Coordinator moves

The coordinator can move to another host (oarbank `coordinator prepare`, `move`, `finalize`). Everything a module
keeps through the SDK moves with it and is verified by digest. A module takes part in three ways.

**Rules** (manifest `[coordinator.move]`). Each rule names a files prefix or a store collection and a class. The first
matching rule wins; anything unmatched is carried.

| Class | What happens |
|---|---|
| `carry` (default) | Moved and verified. |
| `rebuild` | Not transferred; deleted on the target and listed in `move.postflight`'s `skipped`, so the module recreates it (requires `move.postflight`). |
| `drop` | Scratch: deleted on the target. |

`coordinator.move.effects` lists the effects the move verbs may return.

**Verbs.**

| Verb | Called | Returns |
|---|---|---|
| `move.preflight {move_id, to_url, not_before, phase, now}` | `phase = planned` when the move is reviewed or requested (effects ignored); `phase = draining` every few seconds once the time lock passed and dispatch stopped. | `blockers[] {code, message}`: while any remains, the cutover waits (up to 10 minutes, then the move aborts, unless the operator forced it). `checks[]`, and `effects` (applied while draining, e.g. pause a campaign). |
| `move.postflight {move_id, from_url, epoch, skipped[], now}` | Once, on the new coordinator after it took over. | `effects` (resume work, rebuild what was skipped) and `checks[]`; a failed `error` check raises an alert. |
| `move.cancelled {move_id, reason, now}` | On the old coordinator when the move was cancelled or aborted before the commit decision. | `effects` (resume what preflight paused). |

The order on a move: preflight (planned) at request time → preflight (draining) until no blocker → freeze →
`integrity.check {move_source}` → the target copies and verifies → `integrity.check {move_target}` on the copy →
fingerprints compared → commit → `move.postflight` on the new coordinator. Any failure before the commit
decision thaws the old coordinator and calls `move.cancelled`.

## Jobs on the wire

- **Keys.** `job_key = H(module_id, compat, key_inputs)`, computed with `oarbank_sdk.keys.job_key` by a module that enqueues through effects. The result cache and replica checks are scoped to the module.
- **What a job carries.** A `jobs.enqueue` item's `spec` is the module-owned payload. The envelope fields (`datasets`, `mounts`, `resources`, `timeout_s`) belong on the item. If they appear inside `spec`, the host lifts them out. `compat` and `expected` inside `spec` are host-side and never reach the runner.
- **What the runner receives.** The agent always writes a `SpecEnvelope` ([envelopes.md](envelopes.md)) at grant: `schema = <name>/spec@<spec_version>`, the module's id and version, the job key, the stage, the datasets, mounts and stage inputs, the resources the stage reserves, and the payload.
- **Stage chains.** A module whose manifest has a stage `B` with `after = "A"` can run a job as the chain A → B. The host creates the A job with key `<key>:A`, feeds A's artifacts to B as `inputs`, and asks `result.merge` for B's canonical result. Its single-stage form is the stage that is neither `after` another nor depended on.
- **Replicas and comparisons.** The host re-runs a sample of finished jobs on another node and compares the digests (else
  the values to 6 decimals); disagreement opens a dispute settled by a third node, and a node that disagrees with itself
  is convicted. A job of a stage that does not **compare** (its effective determinism, `stages[].determinism` or else
  `results.determinism`, is `none`) is exempt: it is never replicated, a late second result is recorded and never
  compared, it neither takes nor serves a result-cache hit, and no golden runs it. It is otherwise an ordinary job:
  fenced by generation, run only on nodes certified for the module, with its stage's retry and placement.
- **Goldens.** The host builds each golden's spec by calling `spec.build` with a `PlanItem` of the golden's `key_inputs`, `datasets` and `stages`. It runs the stage the golden names; with no stage named, the one stage the spec has. It then accepts the result when `golden.compare` says so, or, for a module without that capability, when the evaluated digest equals `expected.digest`. A golden for a stage that does not compare is a module error: nothing is queued.

### Operations: plan and apply

`op.plan` is called for every operation of tier T2 or above, and for any operation with `preview = true`;
its summary, diff and effects are what the operator reviews. `op.apply` does the change. To refuse invalid
input in either verb, raise invalid params (`-32602`, `RpcError(mp.ERR_INVALID_PARAMS, message)`); the host
shows the message. `op.apply` can also answer `response = "errors"` with per-field issues.

### Digests

Compute a digest over a canonical encoding: `oarbank_sdk.keys.canonical_json` is RFC 8785 (JCS), the one the host uses for
job keys, with three refusals (NaN and infinities, integers beyond ±2^53, non-string keys) and shared vectors in
[vectors/](vectors/) so every language computes the same bytes. Send numbers that must compare exactly beyond 2^53 as
decimal strings.

## Notifications

| Method | Direction | Params |
|---|---|---|
| `initialized` | host → module | none |
| `$/cancel` | host → module | `{id}`: stop working on that request. The module answers it with `-32004`. |
| `log` | module → host | `{level, msg, data}`: goes into the module's event journal. |
| `shutdown` | host → module | none |

## Errors

| Code | Meaning |
|---|---|
| -32700, -32600, -32601, -32602, -32603 | Standard JSON-RPC errors: parse error, invalid request, method not found, invalid params, internal error. |
| -32001 | Unsupported protocol version; `data: {supported, requested}`. |
| -32002 | Permission denied for a host callback. |
| -32003 | The verb's capability was not advertised. |
| -32004 | Request cancelled. |

An error response to `result.evaluate` is **not** a verdict. The host keeps the completion pending and retries it after the module recovers. So a module bug can delay results, but cannot corrupt them.

## Example exchange

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_versions":[1],"host":{"name":"oarbank","version":"2.0.0"},"settings":{}}}
{"jsonrpc":"2.0","id":1,"result":{"protocol_version":1,"module":{"id":"dev.codonic.oarbank.toy","version":"0.1.0"},"capabilities":[]}}
{"jsonrpc":"2.0","method":"initialized","params":{}}
{"jsonrpc":"2.0","id":2,"method":"params.check","params":{"params":{"n":-1}}}
{"jsonrpc":"2.0","id":2,"result":{"ok":false,"errors":[{"path":"/n","message":"n must be >= 0","code":"toy/negative_n"}]}}
```

## Python runtime

`oarbank_sdk.server.Module` implements this protocol for Python authors:

- handshake and capability advertisement;
- typed params and results;
- host callbacks through `ctx.host` (and `ctx.host.fleet_platforms()`);
- the host's capabilities and platform through `ctx.host_has(name)` and `ctx.host_platform`;
- cancellation through `ctx.cancelled`;
- `ctx.log`;
- stdout protection: `print` goes to stderr.

`oarbank_sdk.client.ModuleClient` is the matching unsupervised host, for tests and tools. The reference module is [examples/toy](../examples/toy).

Modules in other languages implement the wire format above directly. The JSON Schemas in `schemas/module-*-1.schema.json` describe every message.

### Driving a module from tests

`oarbank_sdk.client.ModuleClient` runs your coordinator as the host does and serves host callbacks from a
dict:

```python
from oarbank_sdk.client import ModuleClient
c = ModuleClient.spawn([sys.executable, "-I", "primes_module.py"], cwd=module_dir,
                       callbacks={"host.settings.get": lambda p: {"value": None}}, permissions={"settings:read:self"})
info = c.initialize()                          # an InitializeResult (pydantic model)
r = c.call("params.check", {"params": {"n": 10}})   # plain dicts in and out
c.close()
```
