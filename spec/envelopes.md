# Envelopes (envelope 1)

Every spec and every result travels in an envelope. The envelope is the core's contract. The `payload` inside it belongs to the module, and is validated against the module's declared schemas. Models: `oarbank_sdk.envelopes`. Schemas: `spec-envelope-1`, `result-envelope-1`.

## Spec envelope

The coordinator stores one spec envelope per job stage. The agent writes it to `<W>/spec.json`.

| Field | Meaning |
|---|---|
| `envelope` | `1` |
| `schema` | `<module-short>/spec@N`: the payload version. |
| `module_id`, `module_version` | The module that built the spec. |
| `job_key` | sha256 of the RFC 8785 canonical JSON of `{module, compat, inputs: key_inputs}` ([platforms.md](platforms.md#canonical-json)): the content key used for caching and deduplication. |
| `stage` | The stage this spec runs. Absent for single-stage modules. |
| `protocol` | The runner protocol major chosen for the node. |
| `datasets`, `mounts` | The datasets to stage, and where each one goes in the workdir. |
| `inputs` | `name → {dataset, mount}`: artifacts from the upstream stage. |
| `resources` | What the job reserves: `cpu`, `mem_gb`, `pools`, `needs_pools` (the stage's variant for the node's platform applied). |
| `timeout_s` | The hard wall-clock limit for the attempt (likewise per platform). |
| `platform` | [beta] The token of the node the spec was written for (also `OARBANK_PLATFORM`). |
| `payload` | Module-owned. |

## Result envelope

The runner writes `<W>/result.json`. The core stores it exactly as written (after replacing artifact `local` paths with digests), and upgrades old payload versions only on read.

| Field | Meaning |
|---|---|
| `envelope` | `1` |
| `schema` | `<module-short>/result@N` |
| `module_version`, `protocol` | What produced the result. |
| `effective` | The modes and parameters the runner actually honoured. The module compares them with what the spec requested in `result.evaluate`, for example to reject a job that fell back to a slower code path. |
| `provenance` | `argv` per tool, `tool_versions` and `host`. |
| `artifacts` | `[{name, files: [{path, local | digest+size}]}]`. The downstream stage receives each one as `inputs.<name>`. |
| `payload` | Module-owned, up to `results.max_inline_kb` in size. Larger data goes in artifacts. |

## Rules

- Readers accept and **preserve** unknown fields, so an older core stores a newer runner's result without losing anything.
- A number that must compare exactly across machines, such as a score used in golden comparison, belongs in the payload as a decimal string. A float would not compare exactly.
- The digest for replica and golden comparison is computed by the module in `result.evaluate`, over the payload fields named in `results.digest.over`.
