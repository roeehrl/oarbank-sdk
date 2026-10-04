# The conformance kit

`oarbank-sdk conform <module-dir>` runs the checks a host relies on. Run it before publishing and in CI;
it exits non-zero on any failure.

| Suite | Checks |
|---|---|
| manifest | Strict schema, UI contract references, no unknown fields, and the portability lint for every declared platform (Windows execs are `.exe` or the python token, CRLF line endings). A resolved view per declared platform (the runner's exec, runtime, env and file subset under `[bundle.platform_files]`) and per coordinator platform (the coordinator's exec; this host's when `coordinator_platforms` is absent): every bundle path an exec names must reach that platform. The manifest and bundle lint (an unknown runner capability, an unknown `mix`, a mix coarser than `determinism_scope` while `results.value` is set, a `platform_files` glob that matches nothing) are reported as warnings |
| bundle | The module builds into a bundle that verifies (no host imports, no symlinks) |
| protocol | The coordinator starts as a host on this machine starts it (its variant for this platform, `OARBANK_PLATFORM`, its `env`) and completes `initialize`, which carries `host.platform` and every host capability. It advertises every capability the manifest declares and implements every required verb. `params.check`, `golden.list` and `spec.build` are pure (same input, same output). By default there is one node class per declared platform, with `platform` set. Every node class has goldens, each golden's stage compares (never determinism `none`), each golden's `platforms` and `expected_by_platform` keys are declared platforms or their OSes, `spec.build` turns each golden into exactly the stage it names, and each golden is comparable (`expected.digest`, also per platform, or `golden.compare`) |
| images | For each `[[sandbox.container_sets]]` entry: its key is one ECDSA P-256 public key; every member the fixtures name (`images`) verifies with the reference policy (`oarbank_sdk.images`, which the agent shares), against the fixture's OCI image layout or the set's registry; and both refusals hold: an image outside the set's prefix and an unsigned image inside it (a digest not in the index, for a set with one) are refused with `image_not_approved` |
| secrets (in the protocol and runner suites) | Each declared secret gets a random canary (or the fixtures' `secrets` value). Runner runs of a stage that lists secrets get `OARBANK_SECRETS_FILE` (0600, in the work directory) with exactly that stage's names; every other run gets none. The fake host answers `host.secrets.get` only with `secrets:read:self` (`-32002` otherwise). Any canary in a run's output, result, `failure.json`, events or files, or in any coordinator verb's answer (a built spec, a golden), fails the check: other stages and the coordinator side never see a value unless the manifest says so |
| runner | `doctor --json` is a `DoctorOutput`, and its `attrs.platform`, when present, equals `OARBANK_PLATFORM`. The runner and `doctor` run with this host's runner variant, `env` included; goldens of this host's node class come first, and the expected value is resolved for this host's platform. For the first golden whose datasets are available, the real runner runs the golden's `SpecEnvelope` in a fresh workdir with a clean environment and writes a valid `ResultEnvelope`. `result.evaluate` accepts it and it matches the golden. A second run with another locale gives the same digest (the golden's stage has determinism `exact`). For a `cancellable` runner, the golden runs once more and gets a stop request (`control.json` `stop` and the agent's nudge on this OS: SIGUSR1 to a runner started with it ignored on POSIX, the inherited `OARBANK_CONTROL_EVENT` on Windows; nothing else) as soon as the runner has written its first `<W>/phase`, so its start-up is never timed. The runner must acknowledge it within 2 s of the nudge, timed to its own acknowledgement (`failure.json` with `fault = "transient"`, or the result if it was finishing), not to the process exit, and must exit within its `stop_grace_s` (75 after `failure.json`). A runner that writes no `phase`, or a golden that finishes before the stop is sent, cannot show it and the check is skipped, so name a phase when work starts and make one golden run long enough. Every runner spec of the fixtures (`runner_specs`, below) runs the same way and is checked against its expected outcome. Any connection the egress proxy refused fails that run, goldens included, even when the runner coped with the refusal. The runner and `doctor` run under this host's sandbox backend with the module's grants, in the per-OS job environment |

## Not covered (yet)

The kit does not call `op.plan`/`op.apply`, check that enqueued `job_key`s equal the keys of `job.plan`'s
inputs, validate result payloads against `results.schema`, or run more than the first runnable golden (list other tasks as
`runner_specs`). It runs the
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
 "store": {"study/c_demo": {"name": "demo"}},
 "runner_specs": [{"name": "fetch-tools", "stage": "fetch", "payload": {"task": "fetch", "dataset_id": "tool:renderer-4"},
                   "expect": {"exit": 0, "artifacts": ["files"]}},
                  {"name": "sync-bad-cursor", "stage": "sync", "payload": {"task": "sync", "cursor": -1},
                   "expect": {"exit": 2, "reason": "render/bad_cursor"}}]}
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
- **`secrets`** (optional `{name: value}`) replaces the random canaries; the kit delivers each to the stages that list
  it, as an agent does.
- **`images`** (`{"<set name>": {"layout": "fixtures/images", "members": ["ghcr.io/org/tasks/t1@sha256:…"]}}`) names
  members to verify and, optionally, an OCI image layout to read them from (`oarbank_sdk.imagetest` writes one; without
  it the set's registry is asked).
- **`net.mode = "egress-allowlist"`** starts the reference allowlist proxy (`oarbank_sdk.egress_proxy`) with the
  manifest's `allow` list and sets `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`; the sandbox lets the runner reach only it.
- A golden whose stage reserves the `containers` pool is skipped: the kit has no container broker.

**Runner specs** are the runner's other tasks (an ingestion `sync`, a `fetch` that provisions tools): work no golden
exercises, whose grants (egress above all, including redirects to allowed hosts such as a release page's asset host)
would otherwise first meet the sandbox on a real agent. Each entry has a `name`, an optional `stage` (default: the
default stage; never a stage that runs `after` another, which would need inputs), the `payload`, optional `datasets` and
`mounts` (fixture datasets, mounted as for goldens) and `expect`: `exit` (default 0), `artifacts` (the exact artifact
names, for exit 0) and `reason` (the `failure.json` reason, for other exits). The kit builds its envelope as an agent
on this host would (the stage's resources and timeout), runs it sandboxed with the module's grants and the egress proxy,
and reports the exit code, the result's artifact names (each must be a `Name`, which a real agent also requires) or the
`failure.json` (required for a non-zero exit). A spec whose datasets the fixtures lack, or whose stage reserves the
`containers` pool, is skipped with the reason.

A runner spec for a **bootstrap stage** runs with the bootstrap grants instead ([sandbox.md](sandbox.md#bootstrap-jobs)):
the allowlist proxy, no tools, `{}` as settings, no module data directory. For exit 0 the kit also checks what the host
will: the payload is empty, and every artifact's files, hashed from the workdir, are exactly one pinned dataset's
("artifacts match the pinned datasets", naming the first file that differs). So a fetch is proven against the real
origins, through the real allowlist, before the module ships.
