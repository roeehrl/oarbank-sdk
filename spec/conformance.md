# The conformance kit

`oarbank-sdk conform <module-dir>` runs the checks a host relies on. Run it before publishing and in CI;
it exits non-zero on any failure.

| Suite | Checks |
|---|---|
| manifest | Strict schema, UI contract references, no unknown fields, and the portability lint for every declared platform (Windows execs are `.exe` or the python token, CRLF line endings). A resolved view per declared platform (the runner's exec, runtime, env and file subset under `[bundle.platform_files]`) and per coordinator platform (the coordinator's exec; this host's when `coordinator_platforms` is absent): every bundle path an exec names must reach that platform. The manifest and bundle lint (an unknown runner capability, an unknown `mix`, a mix coarser than `determinism_scope` while `results.value` is set, a `platform_files` glob that matches nothing) are reported as warnings |
| bundle | The module builds into a bundle that verifies (no host imports, no symlinks) |
| protocol | The coordinator starts as a host on this machine starts it (its variant for this platform, `OARBANK_PLATFORM`, its `env`) and completes `initialize`, which carries `host.platform` and every host capability. It advertises every capability the manifest declares and implements every required verb. `params.check`, `golden.list` and `spec.build` are pure (same input, same output). By default there is one node class per declared platform, with `platform` set. Every node class has goldens, each golden's stage compares (never determinism `none`), each golden's `platforms` and `expected_by_platform` keys are declared platforms or their OSes, `spec.build` turns each golden into exactly the stage it names, and each golden is comparable (`expected.digest`, also per platform, or `golden.compare`) |
| runner | `doctor --json` is a `DoctorOutput`, and its `attrs.platform`, when present, equals `OARBANK_PLATFORM`. The runner and `doctor` run with this host's runner variant, `env` included; goldens of this host's node class come first, and the expected value is resolved for this host's platform. For the first golden whose datasets are available, the real runner runs the golden's `SpecEnvelope` in a fresh workdir with a clean environment and writes a valid `ResultEnvelope`. `result.evaluate` accepts it and it matches the golden. A second run with another locale gives the same digest (the golden's stage has determinism `exact`). A stop request sent 0.3 s after start (`control.json` `stop` and the agent's nudge on this OS: SIGUSR1 to a runner started with it ignored on POSIX, the inherited `OARBANK_CONTROL_EVENT` on Windows; nothing else) ends the job within 0.5 s of the nudge with a non-zero exit (`cancellable`); a golden that finishes before 0.3 s cannot show it and the check is skipped, so make one golden run longer than that. The runner and `doctor` run under this host's sandbox backend with the module's grants, in the per-OS job environment |

## Not covered (yet)

The kit does not call `op.plan`/`op.apply`, check that enqueued `job_key`s equal the keys of `job.plan`'s
inputs, validate result payloads against `results.schema`, or run more than the first runnable golden. It runs the
runner and the coordinator only on this host's platform: run it on each platform you declare (a CI matrix).
Placement is the host's job; the kit only checks the declarations.
Test those in your own suite.

## Fixtures

An optional `conformance.json` next to the manifest feeds the kit's fake host:

```json
{"datasets": {"scene:demo": {"kind": "scene", "attrs": {"scene": "atrium"}, "dir": "fixtures/demo"}},
 "settings": {"tool_datasets": {"renderer": "tool:renderer"}},
 "tools": {"renderer": "/opt/renderer/4.2"},
 "node_classes": [{"platform": "darwin-arm64", "os_version": "26.1", "pools": {"containers": 1}, "capabilities": []},
                  {"pools": {"imagediff": 0}}],
 "params": [{"n": 3}, {"n": -1}],
 "store": {"study/c_demo": {"name": "demo"}}}
```

Without `node_classes` the kit asks for one class per declared platform (`{"platform": "<token>"}`); a node class
without `platform` gets only goldens that are not limited to some platforms.

A dataset with a `dir` is copied into the runner's workdir under the mount name the golden's spec gives
it. A golden needing a dataset the fixtures lack skips the runner suite for that golden, and the report
says so.

The runner suite gives the runner what an agent would:
- **`tools`** maps each `[sandbox].tools` id to a path on this machine. The kit resolves it (as the agent does),
  grants it in the sandbox and lists it in `OARBANK_TOOLS_FILE`.
- **`settings`** is written to a file passed as `OARBANK_SETTINGS_FILE` (doctor and runs), and also initializes
  the coordinator side.
- **`net.mode = "egress-allowlist"`** starts the reference allowlist proxy (`oarbank_sdk.egress_proxy`) with the
  manifest's `allow` list and sets `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`; the sandbox lets the runner reach only it.
- A golden whose stage reserves the `containers` pool is skipped: the kit has no container broker.
