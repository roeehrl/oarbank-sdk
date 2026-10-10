# Runner protocol 1

The runner is the node-side half of a module. The agent execs it once per job attempt. It speaks no network protocol:
its whole interface is argv, a fixed environment, files in a workdir (including the control document) and an exit code.
So a runner can be written in any language, for any platform ([platforms.md](platforms.md)). Models:
`oarbank_sdk.runner_protocol`. Schemas: `runner-*-1.schema.json`.

## Invocation

```
<runner.exec...> run --spec <W>/spec.json --workdir <W> --out <W>/result.json [--events <W>/events.ndjson]
<runner.exec...> doctor --json
```

- `runner.exec` (or the variant for the node's platform) is resolved as [manifest.md](manifest.md#exec) says: the
  `python` token becomes the module environment's interpreter, `{bundle}` the bundle's absolute path.
- **Arguments:** the agent passes an argv array, never through a shell. On Windows it builds the command line with the
  `CommandLineToArgvW` quoting rules.
- **Process container:** the runner and every process it starts live in one process container ([platforms.md](platforms.md#process-containers))
  that the agent can stop, freeze and kill as a whole. A runner never detaches.
- `--events` is passed only to a runner that declares `progress_events` or `checkpoint`.
- **cwd:** the work directory for `run`; the module's data directory for `doctor`.

## Environment

The agent passes exactly these variables, plus the conventional ones derived per OS ([platforms.md](platforms.md#environment-per-os)),
then the manifest's `[runner].env` with the platform variant's `env` merged over it (never a reserved name:
[platforms.md](platforms.md#per-platform-declarations)). Nothing is inherited. The runner's home and every per-user
location (`HOME` and the XDG directories; `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`) point into its work directory, so
they are discarded with it: [platforms.md](platforms.md#environment-per-os), "What the home is".

| Variable | Stability | Meaning |
|---|---|---|
| `OARBANK_WORKDIR` | stable | The job's work directory `<W>`. |
| `OARBANK_TMP` | stable | `<W>/tmp`, private to the job (also `TMPDIR`, or `TEMP`/`TMP` on Windows, which the AppContainer start moves into the work directory's container folder: [platforms.md](platforms.md#environment-per-os)). |
| `OARBANK_MODULE_DATA` | stable | The module's data directory on this node, kept across jobs (caches, locks). Besides the work directory, the only place a runner may write. Executing files from it needs the `exec_writable` grant ([sandbox.md](sandbox.md)). |
| `OARBANK_PLATFORM` | stable | The node's platform token, e.g. `linux-amd64`. |
| `OARBANK_MODULE` | stable | The module's short name (the last part of `module.id`). |
| `OARBANK_ATTEMPT_ID` | stable | Unique per attempt. Use it to label anything created outside the workdir. |
| `OARBANK_PROTOCOL` | stable | The runner protocol major the agent chose. |
| `OARBANK_POOL_<NAME>_TOKENS` | stable | For each pool the node offers: its token count (upper-case name). |
| `OARBANK_DISABLED_SERVICES` | beta | Comma-separated names of this module's services the owner disabled on the node. |
| `OARBANK_SETTINGS_FILE` | stable | A UTF-8 JSON file with the module's settings for this node. It is a file because environment size limits differ per OS. |
| `OARBANK_SECRETS_FILE` | beta | Set only when the job's stage lists secrets (`stages[].secrets`, core 2.5): a UTF-8 JSON file `{"<name>": "<value>"}` with those secrets' values for this node, owner-only, inside the work directory and deleted with it. `oarbank_sdk.secrets.get(name)` reads it. Never log a value or write it into the result or artifacts. |
| `OARBANK_BROKER` | beta | The job's container broker endpoint, `unix:/path` or `npipe://./pipe/<name>` ([sandbox.md](sandbox.md#containers-the-agents-broker)). Set only for a module approved for containers. |
| `OARBANK_SERVICE_<NAME>` | beta | The job's connector to its module's endpoint service `<name>` (upper-cased), `fd:<n>` or `handle:<n>`, for each endpoint service providing a pool its stage reserves ([service-protocol.md](service-protocol.md#endpoints)). Use `oarbank_sdk.service_endpoint`. |
| `OARBANK_TOOLS_FILE` | stable | A UTF-8 JSON file `{"<tool id>": [{"path": "<canonical path>", "version": "17.0.12", "arch": "aarch64"}]}` for the module's approved `[sandbox].tools` resolved on this node: the one detected installation per tool that satisfies the request ([sandbox.md](sandbox.md#host-tools)), exactly the paths the sandbox grants (canonical; conventional symlinks such as `/opt/homebrew/opt/...` are not readable inside the sandbox). `oarbank_sdk.tools.path(id)` and `tools.version(id)` read it. |
| `OARBANK_FOLDERS_FILE` | beta | A UTF-8 JSON file `{"<folder id>": {"path": "<canonical path>", "access": "read" \| "write"}}` for the runner's granted `[sandbox].folders` on this node (an empty object when there are none): exactly the paths the sandbox grants. `oarbank_sdk.folders.path(id, access)` reads it. |
| `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` | stable | Set for `egress-allowlist`: the agent's local proxy, the only network route ([sandbox.md](sandbox.md#sandbox-grants)). |
| `OARBANK_CONTROL_EVENT` | stable | Windows: the handle, in decimal, of the auto-reset event the runner inherits and the agent sets after every change to `control.json` ([Control](#control)). |
| `PYTHONUTF8=1` | stable | For Python runtimes, on every OS. |

A module gets host tools through `[sandbox].tools` and finds them in `OARBANK_TOOLS_FILE`; there are no tool-specific
variables such as `JAVA_HOME`.

A job of a bootstrap stage gets less ([sandbox.md](sandbox.md#bootstrap-jobs)): no `OARBANK_MODULE_DATA`, no
`OARBANK_BROKER`, an `OARBANK_TOOLS_FILE` that lists no tools, an `OARBANK_FOLDERS_FILE` that lists no folders and an
`OARBANK_SETTINGS_FILE` holding `{}`, and no `OARBANK_SECRETS_FILE`.

All of this runs under the module sandbox ([sandbox.md](sandbox.md)).

## Workdir

The agent creates `<W>` fresh for each attempt, and deletes it after the result is recorded. Every path the runner
names is a PortablePath, `/`-separated ([platforms.md](platforms.md#portable-paths)).

| Path | Written by | Contents |
|---|---|---|
| `spec.json` | agent | The spec envelope ([envelopes.md](envelopes.md)). |
| `<mount>/...` | agent | Each dataset in `spec.datasets`, under `spec.mounts[id]`, as **read-only regular files** (never symlinks). How they are placed (clone, hardlink or copy) is the agent's business. Modifying one is a fault, and the agent may verify. |
| `inputs/<name>/...` | agent | Artifacts of the upstream stage (`spec.inputs`). |
| `checkpoint/...` | agent | [beta] When `spec.resume` is set: the job's latest checkpoint, each file under its name, as read-only regular files ([Checkpoints](#checkpoints)). |
| `control.json` | agent | The control document, always present ([Control](#control)). |
| `.grants/` | agent | The files the environment names (tools, settings, and for a stage that lists secrets `secrets.json`, mode 0600). Not output. |
| `result.json` | runner | The result envelope, written atomically (a temporary file, then rename) before exiting 0. |
| `failure.json` | runner | `{reason, detail, fault?, retryable?}`, written atomically before a non-zero exit. `reason` is one of the agent's end reasons (`bad_input`, `mode_mismatch`, `oom`, `doctor`, `no_metrics`), which the coordinator maps to its reason codes, or the module's own `<module-short>/<code>`, which ends the attempt as `exit_nonzero` with the code in its detail. |
| `events.ndjson` | runner | One UTF-8 event per line (LF; CR tolerated): log, progress, metric, checkpoint or phase. The agent reads complete lines only. |
| `phase` | runner | Optional: one line naming the current phase, replaced atomically. |

Writers replace atomically. A replace that fails because the other side has the file open (Windows) is retried for up
to 2 s. `oarbank_sdk` helpers do this. Output artifacts are listed in `result.json` as
`artifacts[].files[] = {path, local}`, with `local` relative to the workdir. The agent uploads each file by content
digest and replaces `local` with `digest` and `size`. Artifacts carry no file modes. A file may name a `thumbnail:
{local}`: a small preview image the runner made of it (PNG, JPEG, WebP or AVIF, at most 1 MiB), uploaded the same way,
which media components show (spec/ui-contract.md, "Media").

### Bootstrap results

A bootstrap stage's runner writes a result whose `payload` is `{}` and whose artifacts are the datasets it fetched, one
artifact per dataset, each holding exactly the files of one `[[datasets.pinned]]` entry (the same paths; the agent's
digests and sizes must be the pinned ones). The host checks that instead of calling `result.evaluate`, registers each
dataset, and stores nothing else of the result (`effective` and `provenance` are dropped). A result that is not exactly
pinned datasets fails with `pin_mismatch`, a job fault that never counts against the node.

## Exit codes

| Code | Meaning | Attribution |
|---|---|---|
| 0 | `result.json` was written. | The module's `result.evaluate` decides. |
| 2 | The spec can never succeed. | Job fault: not retried elsewhere. |
| 3 | A node dependency is missing. | Host fault: the node is re-doctored and loses the capability. |
| 75 | Transient failure (EX_TEMPFAIL). | Retried; not a job fault. |
| other, or crashed | Failure. | The core's breaker rules. |

**Codes 2, 3 and 75 count only when `failure.json` was written.** On Windows a C/C++ `abort()` exits 3, and argparse
exits 2 everywhere. Without the file, any non-zero exit is "other". `failure.fault` (`job`, `host`, `transient`)
overrides the code, and so does `retryable`. The agent records separately whether it terminated the job itself or the
job crashed (a POSIX signal, or a Windows exception code).

## Control

`<W>/control.json` is a `Control` document: `{seq, stop, pause, threads?, gpu_duty?, reason?, checkpoint?}`.
- The agent writes it for **every** job, atomically (a temporary file, then rename): `{"seq": 0}` before the runner
  starts, then a newer `seq` on every change.
- **After every change the agent nudges the runner:**
  - POSIX: SIGUSR1 to the runner process, never to its children. The runner starts with SIGUSR1 ignored, so a nudge
    that comes before it is ready is lost harmlessly: a runner installs its SIGUSR1 handler first, then reads the
    document. (A shell cannot trap a signal ignored on entry: a shell runner execs a program that handles it.)
  - Windows: it sets the auto-reset event whose handle the runner inherits, named by `OARBANK_CONTROL_EVENT`. The
    sandbox passes the runner that handle and its standard handles, nothing else. A nudge stays set until the runner
    waits on the event.
- **The runner re-reads the document when nudged** and acts on it at its next safe point. It has no reason to re-read it
  otherwise: nothing changes without a nudge.

`oarbank_sdk.control.Control` is a stdlib-only reference implementation (vendorable). It reads the document when
created and again only after a nudge, and holds a pause by blocking on the next nudge; on POSIX create it on the main
thread (it installs the signal handler):

```python
ctl = Control()                    # OARBANK_WORKDIR
ctl.phase("work")                  # <W>/phase: the job is underway
try:
    for chunk in work:
        ctl.safe_point()           # raises Stopped on a stop request; holds while paused
        do(chunk, threads=ctl.threads or default)
except Stopped:
    sys.exit(ctl.acknowledge_stop())   # failure.json {reason: "<module-short>/stopped", fault: "transient"}; 75
```

- **Safe point:** any place where holding or stopping the job changes nothing already written: between work chunks,
  between tool invocations, before a checkpoint.
- **Stop** (`stop: true`): finish within `runner.stop_grace_s`, exiting non-zero, or write the result if it is already
  done.
  - On POSIX the agent also sends SIGTERM to the process container.
  - After the grace period it kills the container: SIGKILL, or `cgroup.kill` on Linux, or `TerminateJobObject` on
    Windows.
  - A runner that declares `cancellable` honours stop promptly. At its next safe point it **acknowledges** the stop:
    it writes `failure.json` with `fault = "transient"` (`Control.acknowledge_stop`; reason `<module-short>/stopped`),
    or the result if it was already finishing, and then exits (75 after `failure.json`) within `stop_grace_s`. The
    acknowledgement is the runner's own observable reaction; the process exit that follows includes interpreter and
    tool teardown, which load on the machine stretches, so only `stop_grace_s` bounds it. Keep safe points at most about
    a second of work apart.
  - The conformance kit checks both: it sends the stop once the runner has written its first `phase` (so start-up is
    never timed), expects the acknowledgement within 2 s of the nudge (a bound that holds on a loaded machine, and far
    below any reasonable `stop_grace_s`), and expects the exit within `stop_grace_s`.
- **Pause** (`pause: true`, for `cooperative_pause` runners): hold at the next safe point until a newer document clears
  it.
  - Runners that declare `freeze_ok` may instead be frozen at any instruction, with the platform's mechanism: SIGSTOP,
    the cgroup freezer, or suspending every process of the Job Object on Windows.
  - A pause lasts at most 10 minutes (an owner may set less on a node). After that the agent releases the attempt (not a
    failure), and the job runs again elsewhere or later: a runner that declares `checkpoint` is asked to checkpoint
    first and resumes from it on any node ([Checkpoints](#checkpoints)); one that declares `resumable` continues from
    its own data-directory checkpoint on the same node only.
- **Throttle** (`cooperative_throttle`):
  - `threads` is the maximum number of active compute threads from the next safe point on.
  - `gpu_duty` is the maximum GPU duty fraction.
  - Time spent held is left out of any speed the result reports.

- **Checkpoint, then stop** (`stop: true` with `checkpoint: true`, runners that declare `checkpoint`): write a
  checkpoint at the next safe point, then acknowledge the stop as above, within `runner.checkpoint_grace_s` instead of
  `stop_grace_s`. The agent sends no SIGTERM with this request ([Checkpoints](#checkpoints)).

Host protection prefers cooperative runners: it throttles before it pauses, and pauses before it evicts; a
checkpointing runner is asked to checkpoint before it is evicted.

## Checkpoints

A runner that declares the capability `checkpoint`, on a stage with `checkpoint = {max_mb, min_interval_s}`
([manifest.md](manifest.md#portable-checkpoints)), keeps **portable checkpoints**: the job's next attempt, on any
node, resumes from the latest one.

- **Announce.** The runner writes a checkpoint's files into the workdir, then appends a `checkpoint` event:
  `{"t", "kind": "checkpoint", "files": [{"path": "<workdir path>", "name": "<checkpoint path>"}], "data": {...}}`.
  `path` names a regular file in the workdir (never a symlink), `name` where it appears in the checkpoint (default:
  `path`); both are PortablePaths and names are unique. `data` is at most 4 KiB of JSON, given back on resume.
- **Hand over.** Once the event is written, its files belong to the agent: it moves them out of the workdir, uploads
  them by content digest and records them. Write each checkpoint to new paths and never touch a named file again.
  Only the latest checkpoint is kept.
- **Limits.** The agent skips (and logs) a checkpoint over the stage's `max_mb`, or one that comes sooner than
  `min_interval_s` after the last one it uploaded, unless it answers a checkpoint-then-stop request.
- **When the job must leave the node** (a pause past its limit, an eviction, an owner's hard cap, a drain, its deadline),
  the agent sends checkpoint-then-stop ([Control](#control)), takes the last checkpoint the runner wrote before it
  exited, and releases the attempt only after the upload.
- **Resume.** The next attempt finds the checkpoint under `<W>/checkpoint/<name>` (read-only regular files), and its
  spec envelope carries `resume: {from_attempt, digest, data}`; `digest` is the sha256 of the canonical JSON list of
  the files' `{name, digest, size}` sorted by name (`oarbank_sdk.runner_protocol.checkpoint_digest`). Without
  `resume`, start from the beginning.
- **Not for goldens or bootstrap jobs.** A golden runs whole (certification never resumes one) and a bootstrap job keeps
  nothing: the agent ignores their checkpoint events and stops them like any other job.
- **Determinism.** A resumed attempt gives the same result as an uninterrupted one: `results.determinism` and the
  stage's apply to it like any other result, and the conformance kit replays an interrupted run to check.

`oarbank_sdk.control.Checkpoints` (stdlib only, vendorable) writes each checkpoint into a fresh directory and appends
its event, and reads `resume`; `Control.checkpoint_requested` says a stop asks for a checkpoint first:

```python
ckpt = Checkpoints(workdir, events_path, spec)
step = int(ckpt.resume_data().get("step", 0)) if ckpt.resume() else 0
try:
    while step < n:
        ctl.safe_point()
        work(step); step += 1
        if step % 50 == 0:
            with ckpt.write({"step": step}) as d:
                save_state(d / "state.bin")
except Stopped:
    if ctl.checkpoint_requested:
        with ckpt.write({"step": step}) as d:
            save_state(d / "state.bin")
    sys.exit(ctl.acknowledge_stop())
```

### GPU use

`runner.gpu.use` (`none`, `shared` or `exclusive`) declares whether jobs use a GPU. The core leaves GPU jobs pending
(`GPU_BLOCKED`) while the node may admit none: the owner set `gpu_jobs = "never"`, or a protected process uses that GPU.

`apis_any` [beta] names the GPU APIs the runner can use; its jobs run only on nodes providing one of them. The set is
open (`^[a-z][a-z0-9]*$`); a core detects six:

| API | Detected on | Counts when |
|---|---|---|
| `metal` | macOS | `MTLCopyAllDevices` returns a device |
| `cuda` | Linux, Windows | `cuInit` and `cuDeviceGetCount` > 0 (`libcuda.so.1`, `nvcuda.dll`) |
| `rocm` | Linux, Windows | the HIP runtime's `hipGetDeviceCount` > 0 (`libamdhip64`, `amdhip64_7.dll` or `amdhip64_6.dll`) |
| `vulkan` | every OS | the loader lists a physical device that is not a CPU device and has a compute queue (MoltenVK on macOS) |
| `opencl` | every OS | a platform lists a device of type GPU or accelerator (not a CPU device, not Windows' Basic Render Driver) |
| `directml` | Windows | `DirectML.dll` loads and a hardware DXGI adapter supports Direct3D 12 at feature level 11_0 |

A node's agent reports the APIs it provides on the host and inside its containers in its doctor report (the agent
command `oarbank-agent gpu-apis` prints them; `oarbank-sdk gpu-apis` runs the same probes on a developer's machine, and
`oarbank_sdk.gpu.detect()` returns them). What a job of a stage needs on a node of a platform
(`Manifest.gpu_needs(stage, platform)`) is a list of "any of" groups:

- the runner's `gpu` for that platform (a `[runner.variants.<key>].gpu` replaces it whole), when `use` is not `none`
  and `apis_any` is not empty: with `in_container = false` it is checked against the host's APIs, for every stage;
  with `in_container = true` against the containers' APIs, only for stages reserving the agent's `gpu` pool
  ([sandbox.md](sandbox.md#gpu-passthrough));
- each service on that platform with `gpu.use` not `none` and `apis_any` set that provides a pool the stage reserves
  (`requires.pools`), against the host's APIs.

A node runs the job only when every group shares an API with its list (explain: `GPU_API_MISSING`). A module whose
runner needs an API the node lacks on the host is not certified there, and its doctor should say `undetected`;
`NodeClass.gpu_apis` lets `golden.list` give nodes of different APIs different goldens. `min_vram_gb` is carried and
selects nothing yet.

## doctor

`doctor --json` prints a `DoctorOutput` to stdout and exits 0, even when unhealthy. It reports:
- `runner_protocol.supported`: the protocol majors the runner speaks;
- `health`, the verdict on the module as a whole: `healthy`, `unhealthy` (should work here but something is broken;
  this alerts) or `undetected` (this node cannot do the module's work; no alert). Only a `healthy` module is certified
  on the node (its goldens run), so only then do the stages that need certification run there;
- `capabilities`, `attrs` (include `platform`) and `checks`, each `{name, ok, detail}`.

A doctor that prints a `DoctorOutput` shows that the module's runner starts on the node, so the agent offers the module
whatever its health, and the stages that need no certification (bootstrap stages, and stages that compare nothing and
need no capability or pool: [manifest.md](manifest.md#stages-that-run-before-certification)) run there. Checks decide
which: **a check named after a capability** (a probe's name, or a capability one of the module's services provides)
proves that capability for the module, and when it fails the node lacks it for the module's stages, whatever the probe
or service reports; a stage requiring it waits, any other stage keeps running. Name a check after the capability it
proves (`java17` for the JDK a `java17` probe also finds), and give checks that no stage depends on other names.
`runner_protocol.disproved_capabilities(checks)` is the reference. A doctor that crashes, hangs or prints no
`DoctorOutput` gets the module offered nowhere on that node.

`detail` says why, in full: the agent and the coordinator keep it as it is and show it whole (`oarbank node show`, the
console's node page). If a runner shortens a long one, it keeps the end, where the cause of a failed import or command
usually is.

The agent runs doctor at install, after upgrades, when capabilities change, and on a slow timer. It must finish within
60 s.

## Requirements the conformance kit checks

- **Idempotent retries:** the same spec in a fresh workdir, or after a kill mid-run, gives an equal result.
- **Deterministic results:** with `determinism = "exact"`, runs in fresh workdirs with different locales, CPU counts,
  path lengths and spaces in paths give the same digest. They are compared only within one platform when
  `results.determinism_scope = "platform"`.
- **Writes:** the runner writes nothing outside the work and data directories.
- **Environment:** the runner does not depend on inherited environment, the current directory or the network, unless
  the manifest declares it.
- **`doctor` is honest:** it reports `undetected` or `unhealthy` whenever `run` would fail with exit code 3.
