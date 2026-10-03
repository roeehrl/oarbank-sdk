# Public surface

This page states exactly what a module may depend on. Anything not listed here is internal to the core, and may change without notice.

## Public (versioned, documented, Apache-2.0)

- The contracts in this repository:
  - the manifest schema;
  - module, runner and service protocols;
  - envelopes;
  - the JSON Schemas in `schemas/`;
  - the Python models in `oarbank_sdk`.
- The runner environment variables listed in [runner-protocol.md](runner-protocol.md), and the service environment variables in [service-protocol.md](service-protocol.md).
- The workdir layout the agent creates (`spec.json`, `result.json`, `events.ndjson`, `control.json`, dataset mounts, `inputs/`).
- The exit codes in the runner protocol.
- Reason codes: the core's reason-code registry (published with each core release as `reason-codes.json`), plus the module's own namespaced codes (`<module-short>/<code>`).
- The declarative UI vocabulary: field formats, `digest_line` templates, `study_columns` and icons.
- `oarbank <module> ...` passthrough, with a scoped admin-API token given to the module CLI.

## Not public

- oarbankd's database schema, internal Python modules, HTTP routes other than the documented admin API, and log formats.
- The agent's internals, its local state files and its heartbeat format.
- Anything reachable only by reading core source.

A module that imports a core Python module, reads core tables or parses core logs is out of contract, and fails the conformance kit.

## Guarantees the core gives every module

- The module never sees another module's settings, results or datasets.
- The coordinator side is stateless: every verb call carries everything it needs. The core owns leases, attempts, retries, certification, replication and storage.
- The runner gets exactly the documented environment, plus the `env` its manifest declares, and nothing inherited.
- Host protection is owner-set: a module cannot declare exemptions from it, or request priority over processes the owner protects. A runner can only declare its own behaviour (`gpu`, `freeze_ok`, `cooperative_throttle`).
