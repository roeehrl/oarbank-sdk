# Manifest (schema 1)

Every module bundle has a `oarbank-module.toml` at its root. It holds every fact the core needs **before** running module code: identity, compatibility, entry points, stages and resources, services and probes, result fields, dataset kinds, goldens, the declarative UI and the module CLI. Behaviour stays in code (the module protocol verbs).

**Rule of thumb:** if the scheduler, the installer or the console needs a fact to decide something, declare it in the manifest. If deciding needs the module's judgement, it is a verb.

- Every key, with its type, default and stability label: [manifest-reference.md](manifest-reference.md), generated from the models.
- Schemas: `schemas/manifest-1.schema.json` (lenient, what the core reads) and `schemas/manifest-1.strict.schema.json` (for editors: unknown keys are errors).
- Validate a manifest with `oarbank-sdk check oarbank-module.toml`.
- Worked examples: [toy](../examples/toy/oarbank-module.toml) (minimal, the reference module), the bench module's manifest in its repository, oarbank-module-bench (typical), and [render](../tests/fixtures/manifests/render.toml), an invented module the SDK's tests use (a stage chain, a service and a probe, datasets, result fields, a CLI).

## Names and interpreters

- **The module's short name** ("module-short") is the last dot-separated part of `module.id`
  (`dev.example.primes` → `primes`). It names the module in operation ids (`mod.<short>.<verb>`, with `-`
  as `_`), schema references (`<short>/spec@N`), reason codes (`<short>/<code>`), the release layout
  (`modules/<short>/`) and `OARBANK_MODULE`.
## Exec

An exec (`coordinator.exec`, `runner.exec`, coordinator and runner variants, services, probes, `cli.exec`) is an argv
array, never a shell string.

- **Element 0** is either:
  - the token **`python`**: the Python of the module's own environment on every OS (`bin/python`, or
    `Scripts\python.exe` on Windows), never whatever `python` is on the PATH. It needs `runtime.kind` `python` (the
    host's managed CPython with the SDK, plus the bundle's requirements installed from wheels) or `uv` (a locked
    environment, wheels-only for every declared platform); or
  - **`{bundle}/<PortablePath>`**: a native executable in the bundle (`runtime.kind = "native"`). On Windows it names
    its `.exe`; the host never appends extensions. Shebang scripts are valid only in darwin and linux variants.
- `.bat`, `.cmd` and `.ps1` are never valid as element 0.
- **`{bundle}`** may appear in any element and is replaced by the bundle's absolute native path. Nothing else is
  rewritten: relative paths are **not** resolved for you.
- **cwd:** the bundle root for the coordinator, services and probes; the work directory for `run`; the data directory
  for `doctor`.
- `oarbank_sdk.manifest.resolve_exec` is the reference resolver.

## Platforms

- `requires.platforms` is required: the node platform tokens the module runs on ([platforms.md](platforms.md)).
- `requires.coordinator_platforms` lists the coordinator host platforms the coordinator side runs on (absent: any), and
  `[requires.unsupported]` gives reasons for what is not supported.
- `requires.os` gives per-OS version ranges (`darwin`, `windows`, and `linux = {kernel, glibc}`).
- `[runner.variants."<platform>"]` (or `[runner.variants.<os>]`) overrides `exec`, `runtime`, `capabilities`,
  `stop_grace_s`, `gpu` and `env` for that platform. The most specific key wins. `[coordinator.variants.<key>]` does
  the same for the coordinator host (`exec`, `runtime`, `timeouts_s`, `concurrency`, `env`), and
  `[stages.variants.<key>]` for a stage (`timeout_s`, `requires.resources`, `retry`). `env`, `timeouts_s` and
  resources merge key by key; every other field replaces
  ([platforms.md](platforms.md#per-platform-declarations)).
- `platforms` on services, probes and `stages[].requires` restricts them to some declared platforms.
- One bundle carries every platform's files: one digest, one approval, one `compat`. `[bundle.platform_files]` says
  which files only some platforms' nodes receive.
- `[placement]` keeps each campaign, group, dataset or pipeline on one platform class
  ([platforms.md](platforms.md#placement)).
- These per-platform and placement keys need `requires.core >= 2.2`.

## Sections

| Section | What it declares |
|---|---|
| `manifest` | The schema major, `1`. |
| `[module]` | `id` (reverse DNS; never reused), `version` (SemVer), `compat` (part of every job key), publisher, SPDX license, codeowners, stability. |
| `[requires]` | Supported core, agent and OS ranges, node and coordinator platforms, `unsupported` reasons, the protocol majors spoken, and must-understand `features`. `experimental` lists opt-ins. |
| `[coordinator]` | The module-protocol process: `exec`, `runtime`, optional-verb `capabilities`, `concurrency`, per-verb `timeouts_s`, host-callback `permissions`, the effects `campaign.tick` may request, `env`, and per-platform `variants`. |
| `[coordinator.move]` | The module's part in a coordinator move: `rules` (a files prefix or a store collection, with class `carry`, `rebuild` or `drop`) and the `effects` the move verbs may request ([module-protocol.md](module-protocol.md#coordinator-moves)). |
| `[runner]` | The runner-protocol executable: `exec`, `runtime`, `capabilities`, `stop_grace_s`, `checkpoint_grace_s`, `gpu`, `bandwidth_class`, `env`, and per-platform `variants`. |
| `[[stages]]` | At least one. Each has `name`, optional `after`, and `requires` (node capabilities, reserved `pools`, `needs_pools` that must merely exist, and `resources` cpu/mem_gb), plus `timeout_s` and `retry`, per-platform `variants`, a `placement` constraint with its `after` stage, and, for a standalone stage, `default` (the stage a job runs when it names none) and its own `determinism` (`none` for work whose results depend on when it ran, such as ingesting a moving feed: the host never replicates, compares, caches or golden-tests it, and with no capability or pool required it runs before certification: [Stages that run before certification](#stages-that-run-before-certification)). A stage's `checkpoint` (`max_mb`, `min_interval_s`) keeps portable checkpoints ([Portable checkpoints](#portable-checkpoints)). |
| `[[services]]` | Node helpers the agent manages through the [service protocol](service-protocol.md): lifecycle, timeouts, restart policy, which pools and capabilities they `provide`, the memory, yield and pause flags, GPU use (`gpu`), and `endpoint` for a service jobs reach through the agent (a warm model server). |
| `[[probes]]` | Read-only capability checks (`fingerprint` only), each run every `period_s`. |
| `[settings]` | `schema`: a JSON Schema file whose properties are the module's settings, each with its type, default, constraints, title, description and an `x-oarbank` annotation (scope `fleet` or `node`, `required`, `unit`, `advanced`, `campaign`). The core validates every value against it ([Settings](#settings)). Settings are visible to the owner: never put a credential in them. |
| `[[secrets]]` | Write-only credentials the owner sets through the core: `name`, `description` ([Secrets](#secrets)). |
| `[placement]` | Which unit of work stays on one platform class (`mix`, `unit`), how it binds (`bind`) and what happens when its class has no eligible node (`rebind`, `stranded_after_s`). |
| `[results]` | The payload schema and its version, `determinism` and its `determinism_scope`, the digest (`version`, `over`), the objective `value`, the inline size limit, and declared `fields` (typed; `indexed` promotes a field to a sortable column; `ui` sets column, format and unit). |
| `[datasets]` | The dataset `kinds` the module registers (short names, scoped by the owning module), their typed `attrs`, and the `platform_bound` kinds. |
| `[bundle]` | `executables` globs (mode 755) and `platform_files` (glob → the platforms or OSes whose nodes receive the files). |
| `[goldens]` | The fixtures glob and the comparison mode (`digest`, or `verb` to call `golden.compare`). |
| `[ui]` | Declarative contributions only: a `digest_line` template, `study_columns`, `icon`. |
| `[cli]` | The module CLI. `oarbank cli <module> [args...]` runs it on the coordinator, sandboxed (its bundle read-only, a scratch directory writable, only the admin API reachable), with `OARBANKD_URL` and `OARBANK_TOKEN`: a one-hour token limited to the module's own operations and reads. |

## Cross-field rules

The models enforce these, beyond the per-field types:

1. Stage names are unique. `after` must name another stage, and stage dependencies form no cycle.
2. Every pool a stage requires (`pools` or `needs_pools`) is provided by a declared service. Every required capability is provided by a service or a probe.
3. If services or probes are declared, `requires.service_protocol` lists at least one major.
4. `results.value.field` is a declared result field.
5. A module with more than one stage implements `result.merge` (listed in `coordinator.capabilities`). The `campaign.tick.results` capability requires `campaign.tick`.
6. `goldens.compare = "verb"` requires the `golden.compare` capability.
7. With `runtime.kind = "uv"`, both `lock` and `python` are set.
8. A move rule names exactly one of `files` or `store`. A `rebuild` rule requires the `move.postflight` capability, and `coordinator.move.effects` requires at least one move verb.
9. Variant keys (runner, stages) and `bundle.platform_files` values are declared platforms or OSes of one. Coordinator variant keys are coordinator platforms or their OSes when `requires.coordinator_platforms` is set.
10. `requires.unsupported` never contradicts the allow-lists: a `runner` key is not a declared platform or the OS of one, and `coordinator` keys need `requires.coordinator_platforms` and are not in it.
11. `env` names (runner, coordinator, their variants) match `^[A-Z][A-Z0-9_]*$` and are never reserved: `OARBANK_*`, `PATH`, `HOME`, `USERPROFILE`, `SYSTEMROOT`, `TEMP`, `TMP`, `TMPDIR`, `LOCALAPPDATA`, `APPDATA`, the XDG base directories and the other variables the host sets ([platforms.md](platforms.md#per-platform-declarations)). Stage variant resources keep the stage's bounds.
12. A key older cores would ignore needs a `requires.core` range whose lower bound is at least the core that understands it ([versioning.md](versioning.md#additive-changes-within-manifest-1)):
    - **2.2:** the per-platform and placement keys (`requires.coordinator_platforms`, `requires.unsupported`, `requires.features`, `coordinator.env`, `coordinator.variants`, `runner.env` and runner variant `env`, `stages[].variants`, `stages[].placement`, `[placement]`, a `determinism_scope` other than `global` or `platform`, `bundle.platform_files`, `datasets.platform_bound`);
    - **2.3:** `stages[].determinism`, `stages[].default`, the coordinator capability `campaign.tick.results`, and the effects `datasets.update` and `datasets.delete` in any effects list (`coordinator.campaign_effects`, `operations[].effects`, `coordinator.move.effects`).
    - **2.4:** `stages[].bootstrap` and `datasets.pinned`.
    - **2.5:** `[[secrets]]`, `stages[].secrets`, the permission `secrets:read:self`, `sandbox.container_sets`, a
      stage reserving the agent's `gpu` pool, `runner.gpu.apis_any` (or a runner variant's), `services[].endpoint`,
      `services[].gpu`, `sandbox.folders`,
      `stages[].checkpoint`, `runner.checkpoint_grace_s`, the runner capability `checkpoint` and the view cell type
      `artifact_ref`.

    Every entry of `requires.features` is one this SDK knows.
13. Every bundle path a node exec names (argv[0], or the script a `python` exec runs) reaches each platform that runs it under `bundle.platform_files`. `datasets.platform_bound` kinds are declared kinds.
14. `stages[].default` is set on at most one stage, a standalone one; when several stages are standalone, exactly one sets it. `stages[].determinism` is set only on standalone stages (neither `after` another nor depended on): a chain is one evaluation and compares as `results.determinism`. At least one stage compares (its effective determinism is `exact` or `within_tolerance`): goldens run only on such stages, and every module is certified on golden evidence.
15. `stages[].bootstrap` is set only on a standalone stage that is not the default stage, whose effective determinism is `none` and which reserves and needs no pools. A module with a bootstrap stage pins at least one dataset (`[[datasets.pinned]]`), pins need a bootstrap stage, and its `sandbox.net.mode` is `none` or `egress-allowlist`. Pinned dataset ids are unique; each pin's `kind` is a declared kind, its `platform` is set exactly when the kind is platform-bound and is a declared platform, its file paths are unique, and no two pins hold the same files ([Pinned datasets](#pinned-datasets)).
16. Secrets: names are unique; a stage lists only declared secrets, each once, and a bootstrap stage lists none; every
    declared secret reaches something (a stage lists it, or the coordinator has `secrets:read:self`).
17. Container sets: names are unique; each `key` is a bundle file holding one ECDSA P-256 public key (checked by
    `oarbank-sdk check` and bundle verification). A stage that reserves the `gpu` pool also reserves `containers`, and
    the runner declares `gpu.in_container = true` with `gpu.use` `shared` or `exclusive`
    ([sandbox.md](sandbox.md#gpu-passthrough)).
18. An endpoint service (`endpoint = true`) provides at least one pool (a job reaches it through a pool its stage
    reserves) and its `lifecycle` is `on_demand` or `always` (the agent never starts a `manual` service, so it could never
    hand it its channel). A service whose `gpu.use` is not `none` needs `[sandbox].devices.gpu = "compute"`.
19. `sandbox.folders` ids are unique, each with `access` `read` or `write` ([sandbox.md](sandbox.md#folders)).
20. A stage with `checkpoint` needs the runner capability `checkpoint`, and the capability needs at least one such
    stage; a bootstrap stage never sets `checkpoint`.
21. `gpu.apis_any` (the runner's, a runner variant's, a service's) is set only with `gpu.use` `shared` or `exclusive`: it
    places GPU work ([runner-protocol.md](runner-protocol.md#gpu-use)).
22. **Lint** (warnings, not errors): an unknown `mix`; a stage `placement` on a stage without `after`; an unknown `determinism_scope`; a placement mix coarser than `determinism_scope` while `results.value` is set (values in one campaign would come from classes whose results are not comparable); stages receiving secrets while the network mode is `egress-any`; a `gpu.apis_any` entry no core detects yet (outside `cuda`, `directml`, `metal`, `opencl`, `rocm`, `vulkan`: such work is placed nowhere). `oarbank-sdk check` prints them; `oarbank_sdk.manifest.lint` returns them.

A module may offer both forms of an evaluation. For example, render declares a single `eval` stage and a `render → score` chain; the operator's pipeline setting picks the form for jobs that name no stage. A module may also declare standalone utility stages (an ingestion `sync`, a `fetch` that provisions tools) and enqueue jobs that name them; it then marks its evaluation stage `default = true`.

## Stages that run before certification

A node runs a module's work only once the module is **certified** there: its doctor is healthy and its goldens passed
(golden jobs on the stages that compare). Certification is what lets a node's results count: it shows that the node
computes the module's comparable results exactly as the goldens expect. Two kinds of stage need no certification,
because the goldens say nothing about their results and no other node's result is ever checked against theirs:

- a **bootstrap stage** ([Pinned datasets](#pinned-datasets)): its output counts only when it is exactly the pinned
  datasets, which the host checks file by file;
- a stage that **compares nothing and needs nothing a node must prove**: its effective determinism is `none` (never
  replicated, compared, cached or golden-tested, rule 14) and it requires no capability (`requires.capabilities`) and
  no pool (`requires.pools`, `requires.needs_pools`). An ingestion `sync` that pulls a moving feed is the usual case.

`Manifest.certification_exempt(stage)` says which stages these are. Their jobs run on any node where the module's
runner starts (the agent ran its doctor and got a `DoctorOutput`), whatever the doctor's `health`: a module that is
`unhealthy` or `undetected` on a node is not certified there, but its exempt stages still run. A failed doctor check
gates only the stages that need what it proves: a check named after a capability (a probe's name, or a capability a
service provides) that fails takes that capability away from the module on that node, so a stage that requires it
waits there (`STAGE_CAPABILITY_MISSING`), and a stage that does not keeps running. Everything else is checked as for
any job (platforms, sandbox, secrets, placement, retries). A stage with `determinism = "exact"` or
`"within_tolerance"`, or one that needs a capability or a pool, always waits for certification: its result is
replicated, compared and served from the result cache, and only certified nodes' results may enter that.

Results of exempt stages are still judged by the module (`result.evaluate`) and fenced by job generation; they never
count toward certification. A node whose jobs keep failing trips its breaker as for any job, which revokes the module
there until its doctor runs again.

## Pinned datasets

A module that provisions its own tools or reference data (downloads from public origins) does it with a **bootstrap
stage**: on a fresh fleet its goldens mount those datasets, so the jobs that fetch them must run before any node is
certified.

```toml
[[stages]]
name = "fetch"
bootstrap = true            # runs where the module's runner starts, before the goldens pass
determinism = "none"        # never compared, cached or golden-tested
timeout_s = 3600
requires = { resources = { cpu = 1, mem_gb = 1.0 } }

[[datasets.pinned]]
dataset_id = "tool:gatk-4.5.0.0"
kind = "tool"
meta = { version = "4.5.0.0" }        # optional: the registered dataset's meta
files = [{ path = "gatk-package-4.5.0.0-local.jar", sha256 = "<64 hex>", size = 1234567 }]
```

- A bootstrap job runs with the bootstrap grants ([sandbox.md](sandbox.md#bootstrap-jobs)): the module's egress
  allowlist, its work directory and nothing else.
- Its result has an empty `payload` and one artifact per dataset it fetched, each holding exactly one pin's files
  ([runner-protocol.md](runner-protocol.md#bootstrap-results)). The host registers each such dataset itself (the
  pin's id, kind, meta, platform and files, owned by the module) and calls no module verb on the result. Anything
  else is refused with `pin_mismatch`, counted against the job and never against the node, and nothing is registered.
- `datasets.create` of a pinned id succeeds only with the pinned contents, so the module's own code may register a
  pinned dataset too.
- The pins are part of the manifest, so the bundle digest an operator installs and approves covers them.
- A job runs a bootstrap stage only when it names it (`jobs.enqueue` `stage`); its results never count toward
  certification.

## Settings

A module's settings are the choices an owner makes for it: a VM size, a pipeline threshold, a gate's numbers. The
module declares each one in the JSON Schema file `[settings].schema` names, one property per setting:

```toml
[settings]
schema = "schemas/settings.schema.json"
```

```json
{
 "type": "object",
 "properties": {
  "vm_mem_gb": {"type": "number", "minimum": 2, "maximum": 64, "default": 8,
                "title": "VM memory", "description": "Memory the module's VM gets on a node.",
                "x-oarbank": {"scope": "node", "unit": "GB"}},
  "pool_tao_per_round": {"type": "number", "minimum": 0,
                         "title": "Miner pool per round", "description": "For the gate's expected value.",
                         "x-oarbank": {"scope": "fleet", "unit": "TAO", "required": true}}
 }
}
```

- **The property is the contract.** Its `type` (`boolean`, `integer`, `number`, `string`, `array` or `object`,
  optionally with `"null"`) and every JSON Schema constraint on it (`minimum`, `maximum`, `enum`, `pattern`,
  `maxLength`, `items`, `format`, ...) are what the core checks each value against, on every write. `default` must be
  valid itself. `title` is the label the owner reads (at most 80 characters; the key's name otherwise) and
  `description` its one line of help (at most 500). A name is lower-case letters, digits and `_`, starting with a
  letter, at most 64 characters; a schema declares at most 128 settings.
- **`x-oarbank`** says how the core treats it:

  | Field | Default | Meaning |
  |---|---|---|
  | `scope` | `"fleet"` | `fleet`: one value for the whole fleet, which the module's coordinator side reads (`host.settings.get`, `initialize`). `node`: a value the owner sets for the fleet, a node group or one node (the most specific wins); each node's runners, doctor and services receive it in `OARBANK_SETTINGS_FILE` |
  | `required` | `false` (or the schema's own `required` list) | The owner must set it: until a value exists the module's work waits (`SETTINGS_NOT_SET`) and its readiness checklist shows the step "Required settings". A required setting has no `default` |
  | `unit` | none | A short word shown beside the value (`GB`, `s`, `TAO`) |
  | `advanced` | `false` | Shown under the module's Advanced disclosure |
  | `campaign` | `false` | A campaign of the module may override it while the campaign runs (`campaigns.create` or `campaigns.update` `settings`, or the owner on the campaign's page), bounded by the owner's locks. The campaign's value reaches that campaign's jobs only: a `node` key in their runners' `OARBANK_SETTINGS_FILE`, every key in `campaign.tick` (`campaign.settings`). Core 2.9 and later (an older core refuses the annotation at install) |

- **What a process receives.** Runners, `doctor` and services get the node-scoped settings, resolved for their node:
  every `node` key with the owner's value, else its default (a key with neither is absent), and nothing else, in
  `OARBANK_SETTINGS_FILE` (`oarbank_sdk.settings.read()`). Bootstrap jobs get `{}`. The coordinator side gets every
  key at fleet scope the same way. A change to one module's setting never touches another module's.
- **Writes are validated.** The owner sets values in the module's Settings tab or with
  `oarbank settings set <key> <value> --module <module> [--node N | --group G]`. A module's own operations change
  its settings with the `module_settings.update` effect ([module-protocol.md](module-protocol.md#effects)): each key
  must be declared and each value valid, or the whole effect is refused; `null` resets a key to inherit.
- **Upgrades.** The core reads the schema of the module's current version (before it is enabled, the newest
  installed one). A key a new version drops keeps its values, unused, until the owner resets them or a later version
  declares it again; a value a new version's schema no longer accepts is dropped, and the module event names it.
- **The core's keys per module** are not yours to declare: `enabled` (the module on the fleet or on one node),
  `services.disabled`, `pipeline` and `replica_rate` exist for every module and are set as `[module] <key>`.
  `oarbank-sdk check` warns about a property with one of those names.
- **Credentials are never settings.** A property with `writeOnly`, `x-secret` or `x-oarbank.secret` is refused, and a
  name that looks like a credential gets a warning: declare it in [`[[secrets]]`](#secrets).

`oarbank-sdk check`, `bundle build` and `bundle verify` refuse a schema the core would not read, and the conformance kit
checks the fixtures' settings against it ([conformance.md](conformance.md)).

**Migrating a module written for SDK 1.5 or earlier.** The core used to store a module's settings as one opaque
object. Now: (1) declare every key the module reads or writes in its settings schema, (2) mark those its runners or
services need on a node `"x-oarbank": {"scope": "node"}` (only those reach `OARBANK_SETTINGS_FILE`), (3) move any
credential to `[[secrets]]`, (4) drop a `pipeline` property (the core's `[module] pipeline` replaces it). An
undeclared key in `module_settings.update` is refused, and values an owner set before are kept only for declared keys
that validate.

## Secrets

A credential (an API key for a model provider, a token for a private package registry, a licence key) is a secret,
never a setting:

```toml
[[secrets]]
name = "llm_api_key"
description = "API key for the model provider the agent harness calls"

[[stages]]
name = "attempt"
secrets = ["llm_api_key"]            # only this stage's runner receives it
```

- **The owner sets it** through the core (`oarbank secret set <module> <name> [--node N]`, or the module's Settings
  page), for the module or for one node; a node's own value wins. The core stores it encrypted, and no page, API read,
  plan, audit row, export or error message ever shows it: only whether it is set, a fingerprint and when it changed.
- **Delivery.** Only the runners of stages that list it get it, in `OARBANK_SECRETS_FILE`: an owner-only file inside the
  job's work directory, deleted with it ([runner-protocol.md](runner-protocol.md#environment);
  `oarbank_sdk.secrets.get`). Other stages, services, probes, doctor and bootstrap jobs never do. A job waits
  (`SECRETS_NOT_SET`) on a node with no value for a secret its stage lists.
- **The coordinator side** reads the module's value with `host.secrets.get` only when `coordinator.permissions` lists
  `secrets:read:self` ([module-protocol.md](module-protocol.md#host-callbacks-module--host)). Few modules need it; a
  value a verb puts in a spec reaches every stage, which the conformance kit refuses.
- **Redaction** of exact values in captured logs is a safety net, not a guarantee: never log a value, write it into
  results or artifacts, or pass it on a command line.
- **Pair it with an egress allowlist** naming only the service the key is for, so a leaked key can reach only that host.
- Coordinator moves carry secrets re-encrypted for the new coordinator; the move preview lists them by name.

## Portable checkpoints

A long job on a machine people also use may be paused or moved by host protection. A runner that keeps **portable
checkpoints** loses only the work since its last one:

```toml
[runner]
capabilities = ["cooperative_pause", "checkpoint"]
checkpoint_grace_s = 120          # from a checkpoint-then-stop request to the kill

[[stages]]
name = "train"
checkpoint = { max_mb = 4096, min_interval_s = 600 }
```

- The runner announces each checkpoint with a `checkpoint` event naming its files; the agent takes them away and
  uploads them (at most `max_mb`, at most one per `min_interval_s`), and the coordinator keeps the job's latest.
- When protection would evict the job, the agent asks for a checkpoint first (`stop` with `checkpoint` in the control
  document) and waits `checkpoint_grace_s`.
- The job's next attempt, on any node, starts with the checkpoint under `<W>/checkpoint/` and `resume` in its spec
  envelope ([runner-protocol.md](runner-protocol.md#checkpoints)). A resumed attempt must give the same result as an
  uninterrupted one; the conformance kit replays an interrupted run to check it.

## Declarative UI: formats and templates

Module UI is data. Core templates render it and escape it, and no module HTML or JS is ever executed.

- **Field formats** are one of these:

  | Format | Renders as |
  |---|---|
  | `.Nf` | fixed-point, N decimals |
  | `.Ne` | exponent notation, N decimals |
  | `.N%` | percentage, N decimals |
  | `d` | integer |
  | `,d` | integer with thousands separators |
  | `s` | string |
  | `.Ns` | string truncated to N characters |

  N is one or two digits.
- **`digest_line`** is plain text with `{field}` or `{field:FORMAT}` placeholders (field names in lower snake case), and `{{`/`}}` for literal braces. Nothing else is allowed.
- **`icon`** comes from a fixed set: `cpu`, `gpu`, `dna`, `chart`, `flask`, `cube`, `bolt`.

## What a manifest cannot declare

- **Host protection:** exemptions, priority over the owner's protected processes, or node selection by identity. Protection is owner-set only (see the public-surface statement).
- **Secret values.** A manifest declares secrets by name; only the owner sets their values.
