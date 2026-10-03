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
| `[runner]` | The runner-protocol executable: `exec`, `runtime`, `capabilities`, `stop_grace_s`, `gpu`, `bandwidth_class`, `env`, and per-platform `variants`. |
| `[[stages]]` | At least one. Each has `name`, optional `after`, and `requires` (node capabilities, reserved `pools`, `needs_pools` that must merely exist, and `resources` cpu/mem_gb), plus `timeout_s` and `retry`, per-platform `variants`, and a `placement` constraint with its `after` stage. |
| `[[services]]` | Node helpers the agent manages through the [service protocol](service-protocol.md): lifecycle, timeouts, restart policy, which pools and capabilities they `provide`, and the memory, yield and pause flags. |
| `[[probes]]` | Read-only capability checks (`fingerprint` only), each run every `period_s`. |
| `[settings]` | The JSON Schema for the module's settings. The core stores settings but never interprets them. |
| `[placement]` | Which unit of work stays on one platform class (`mix`, `unit`), how it binds (`bind`) and what happens when its class has no eligible node (`rebind`, `stranded_after_s`). |
| `[results]` | The payload schema and its version, `determinism` and its `determinism_scope`, the digest (`version`, `over`), the objective `value`, the inline size limit, and declared `fields` (typed; `indexed` promotes a field to a sortable column; `ui` sets column, format and unit). |
| `[datasets]` | The dataset `kinds` the module registers (namespaced by the core), their typed `attrs`, and the `platform_bound` kinds. |
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
5. A module with more than one stage implements `result.merge` (listed in `coordinator.capabilities`).
6. `goldens.compare = "verb"` requires the `golden.compare` capability.
7. With `runtime.kind = "uv"`, both `lock` and `python` are set.
8. A move rule names exactly one of `files` or `store`. A `rebuild` rule requires the `move.postflight` capability, and `coordinator.move.effects` requires at least one move verb.
9. Variant keys (runner, stages) and `bundle.platform_files` values are declared platforms or OSes of one. Coordinator variant keys are coordinator platforms or their OSes when `requires.coordinator_platforms` is set.
10. `requires.unsupported` never contradicts the allow-lists: a `runner` key is not a declared platform or the OS of one, and `coordinator` keys need `requires.coordinator_platforms` and are not in it.
11. `env` names (runner, coordinator, their variants) match `^[A-Z][A-Z0-9_]*$` and are never reserved: `OARBANK_*`, `PATH`, `HOME`, `USERPROFILE`, `SYSTEMROOT`, `TEMP`, `TMP`, `TMPDIR`, `LOCALAPPDATA`, `APPDATA` and the other variables the host sets ([platforms.md](platforms.md#per-platform-declarations)). Stage variant resources keep the stage's bounds.
12. Any per-platform or placement key (`requires.coordinator_platforms`, `requires.unsupported`, `requires.features`, `coordinator.env`, `coordinator.variants`, `runner.env` and runner variant `env`, `stages[].variants`, `stages[].placement`, `[placement]`, a `determinism_scope` other than `global` or `platform`, `bundle.platform_files`, `datasets.platform_bound`) needs a `requires.core` range whose lower bound is at least 2.2. Every entry of `requires.features` is one this SDK knows.
13. Every bundle path a node exec names (argv[0], or the script a `python` exec runs) reaches each platform that runs it under `bundle.platform_files`. `datasets.platform_bound` kinds are declared kinds.
14. **Lint** (warnings, not errors): an unknown `mix`; a stage `placement` on a stage without `after`; an unknown `determinism_scope`; a placement mix coarser than `determinism_scope` while `results.value` is set (values in one campaign would come from classes whose results are not comparable). `oarbank-sdk check` prints them; `oarbank_sdk.manifest.lint` returns them.

Several stage chains may coexist. For example, render declares a single `eval` stage and a `render → score` chain, and `job.plan` picks one per evaluation.

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
- **Secrets.** Settings are visible to the owner in the console.
