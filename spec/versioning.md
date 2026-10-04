# Versioning and deprecation policy

**Before 1.0.** Every contract published before oarbank-sdk 1.0.0 was a draft. Protocol 1.0 redefined "1" in place to add the cross-platform baseline ([platforms.md](platforms.md)). From 1.0.0 on, the rules below apply in full: new platforms, OS keys, GPU APIs, network modes and container platforms are additive, because every such set is open.

oarbank has five public contracts. Each one carries its own small integer version, and each is negotiated at a defined point.

| Contract | Version form | Negotiated where | Compatibility rule | Support window |
|---|---|---|---|---|
| Manifest | `manifest = N` | At install | Within a major, new fields are only ever added | N and N−1 |
| Module protocol | Integer major, additive minors | The `initialize` handshake | Unknown fields are ignored; a missing capability means the feature is unsupported | N and N−1, through core-side adapters |
| Runner protocol | Integer major | `doctor --json` | The agent picks the highest major both sides share; a job is claimed only by nodes that support it | N and N−1 |
| Service protocol | Integer major | `fingerprint` | Same rule as the runner protocol | N and N−1 |
| Envelopes | `envelope = N` | Read side | Readers accept and preserve unknown fields | N and N−1 |

Module-owned payloads are versioned separately as `<module>/spec@N` and `<module>/result@N`:

- **Results** are stored exactly as the runner wrote them. They must be backward-transitive: a module can read every result version it ever produced, either directly or through `result.upgrade`. A result version stays supported for as long as any stored row uses it.
- **Specs** are gated by negotiation. The core passes `target_spec_version` to `spec.build`: the highest spec version every eligible runner understands. So a runner must understand spec N+1 before any coordinator emits it.
- **`digest_version`** is a per-module integer. Replica and golden comparisons happen only between equal versions, and old versions are kept permanently.

The **`oarbank-sdk` package** follows SemVer. Its major equals the highest module protocol major it speaks. The latest two majors receive fixes.

## Additive changes within manifest 1

A new manifest key that older cores would silently ignore (their readers are lenient) is gated by the core version
instead of a manifest major: the SDK refuses such a key unless the lower bound of `requires.core` admits only cores that
understand it, and older cores already refuse a module whose `requires.core` excludes them. The per-platform and
placement keys of SDK 1.1 need `requires.core >= 2.2`, the stage, tick-result and dataset keys of SDK 1.3 need
`requires.core >= 2.3`, and the bootstrap stages and pinned datasets of SDK 1.4 need `requires.core >= 2.4`
([manifest.md](manifest.md#cross-field-rules), rule 12). For
later features, `requires.features` is a must-understand list: a reader refuses a manifest that lists a feature it
does not know. Module protocol additions are optional fields plus host capability names
([module-protocol.md](module-protocol.md#host-capabilities)); a module checks a capability before relying on it at run
time.

## Stability labels

Every field, verb, capability, environment variable, exit code and manifest key is labelled `[stable]`, `[beta]` or `[experimental]` in its schema description.

| Label | Promise |
|---|---|
| stable | Never removed or retyped within a major. Deprecation needs at least two core minor releases or six months (whichever is longer), then a new major. |
| beta | May change in a minor release, with one release of deprecation warnings first. |
| experimental | May change or disappear at any time. Using one requires listing it in `requires.experimental`, and any such opt-in blocks the verified badge. |

## Deprecation

1. The item is marked `[deprecated]` in its schema description, with a `removed_in` version. A dated announcement goes out at least six months before removal of a stable item.
2. While deprecated, every use emits:
   - a warning into the module's event journal;
   - the metric `deprecated_api_used{module,item,removed_in}`;
   - a lint from `oarbank-sdk check`.
3. Removal needs a new major plus a core-side adapter for the old major. Old behaviour lives in adapters, so module code never branches on version.

## Field rules

These apply to every contract:

- Never reuse a field name for a different meaning, and never change a field's type.
- Never add a required field within a major.
- Removed names go on a `reserved` list in the spec and are never reused. Removed module ids are tombstoned.
- Enums that may grow document that readers must treat an unknown value as `unknown`, not as an error.
- Readers are lenient (they keep unknown fields); authoring checks are strict (`oarbank-sdk check` and the `*.strict.schema.json` schemas flag unknown fields as probable typos).

## Module versions and `compat`

- `module.version` is the SemVer of the module package.
- `module.compat` is part of every `job_key`. Bump it **only** when results for the same inputs change meaning, for example a new tool version or scoring change. A patch or minor release that keeps `compat` keeps the result cache.
- Certification is keyed by the bundle's content digest: every new build is re-certified by golden jobs on every node before it gets work, whatever its `compat`.
