# Changelog

What changed in each oarbank-sdk release, for module authors. The core's changelog is in
[roeehrl/oarbank](https://github.com/roeehrl/oarbank/blob/main/CHANGELOG.md). Contract rules:
[spec/versioning.md](spec/versioning.md).

## 1.6.0 (2026-10-11)

For Oarbank core 2.9.0. Everything is additive within manifest 1 and module protocol 1.

### New

- **Host tools by version** ([spec/sandbox.md](spec/sandbox.md#host-tools)). A `[sandbox].tools` entry names a fleet
  tool definition (`jdk`, `python`, or one an admin defines) and may add `version` (comma-separated clauses with `=`,
  `!=`, `>`, `>=`, `<`, `<=`, `~>`) and `arch` (`any`, `native`, `arm64`, `amd64`):

  ```toml
  [sandbox]
  tools = [{ id = "jdk", version = ">=17, <22", arch = "native", trust = "code-exec" }]
  ```

  `OARBANK_TOOLS_FILE` lists each granted installation with its `path`, `version` and `arch`
  (`oarbank_sdk.tools.path("jdk")`, `tools.version("jdk")`). `oarbank_sdk.toolversion` and
  [spec/vectors/tool-versions.json](spec/vectors/tool-versions.json) pin how versions compare. A tool id may appear
  once per manifest.
- **Module settings are declared keys** ([spec/manifest.md](spec/manifest.md#settings)). Each property of the
  `[settings].schema` file is one setting, validated on every write, with an `x-oarbank` annotation: `scope` (`fleet` or
  `node`), `required`, `unit`, `advanced` and `campaign`. Runners, doctor and services receive the node-scoped keys in
  `OARBANK_SETTINGS_FILE` (`oarbank_sdk.settings.read()`); the coordinator side gets every key. `oarbank-sdk check`,
  `bundle build` and `bundle verify` refuse a schema the core would not read, and the conformance kit gains a settings
  suite.
- **Campaign overrides.** A key marked `"x-oarbank": {"campaign": true}` may be overridden by a campaign
  (`campaigns.create` / `campaigns.update` `settings`); `campaign.tick` receives `campaign.settings`. Host capability
  `campaign_settings.v1`.
- **Stages that run before certification** ([spec/manifest.md](spec/manifest.md#stages-that-run-before-certification)).
  A bootstrap stage, or a stage with determinism `none` that needs no capability and no pool, runs wherever the
  module's runner starts (`Manifest.certification_exempt`). A failed doctor check named after a capability withdraws
  only that capability (`Manifest.capability_names`), and doctor check details are kept whole or by their end.

### Changed

- **The core-version gate for 1.6 features.** Older cores ignore these, so the SDK refuses them unless `requires.core`
  admits only 2.9 and later (cross-field rule 12): a tool `version`, a tool `arch` other than `any`, and in the settings
  schema a `node` key, a required key or a `campaign` key. `unit` and `advanced` need no newer core.
  `oarbank_sdk.settings.core_gate` applies the settings part in `check`, `bundle build` and `bundle verify`.
- The `toy` example declares a fleet setting and a node setting and needs core 2.9.
- **The conformance kit cleans up after itself.** A run of the kit (`oarbank-sdk conform`, `conform()`) kept every
  runner workdir, data directory, grants directory, checkpoint and sandbox profile it made in the temp directory,
  hundreds per test session. They now go in one directory, removed when the run ends, however it ends.

### Migrating from 1.5

- **Tools:** replace a registry id (`java17`) with a definition and a constraint (`{ id = "jdk", version = ">=17",
  trust = "code-exec" }`), read `tools.path("jdk")` (each entry is now an object), set `requires.core = ">=2.9,<3"` and
  publish a new module version.
- **Settings:** declare every key the module reads or writes, mark those its runners or services need
  `"x-oarbank": {"scope": "node"}`, move credentials to `[[secrets]]`, drop a `pipeline` property (the core's
  `[module] pipeline` replaces it), and set `requires.core = ">=2.9,<3"` if you use node, required or campaign keys.

## 1.5.0

Secrets, signed container image sets and the container GPU pool, service endpoints, GPU placement by API, folder
grants, portable checkpoints and the `artifact_ref` cell type (core 2.5).
