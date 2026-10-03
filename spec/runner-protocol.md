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
- `--events` is passed only to a runner that declares `progress_events`.
- **cwd:** the work directory for `run`; the module's data directory for `doctor`.

## Environment

The agent passes exactly these variables, plus the conventional ones derived per OS ([platforms.md](platforms.md#environment-per-os)),
then the manifest's `[runner].env` with the platform variant's `env` merged over it (never a reserved name:
[platforms.md](platforms.md#per-platform-declarations)). Nothing is inherited.

| Variable | Stability | Meaning |
|---|---|---|
| `OARBANK_WORKDIR` | stable | The job's work directory `<W>`. |
| `OARBANK_TMP` | stable | `<W>/tmp`, private to the job (also `TMPDIR`, or `TEMP`/`TMP` on Windows). |
| `OARBANK_MODULE_DATA` | stable | The module's data directory on this node, kept across jobs (caches, locks). Besides the work directory, the only place a runner may write. Executing files from it needs the `exec_writable` grant ([sandbox.md](sandbox.md)). |
| `OARBANK_PLATFORM` | stable | The node's platform token, e.g. `linux-amd64`. |
| `OARBANK_MODULE` | stable | The module's short name (the last part of `module.id`). |
| `OARBANK_ATTEMPT_ID` | stable | Unique per attempt. Use it to label anything created outside the workdir. |
| `OARBANK_PROTOCOL` | stable | The runner protocol major the agent chose. |
| `OARBANK_POOL_<NAME>_TOKENS` | stable | For each pool the node offers: its token count (upper-case name). |
| `OARBANK_DISABLED_SERVICES` | beta | Comma-separated names of this module's services the owner disabled on the node. |
| `OARBANK_SETTINGS_FILE` | stable | A UTF-8 JSON file with the module's settings for this node. It is a file because environment size limits differ per OS. |
| `OARBANK_BROKER` | beta | The job's container broker endpoint, `unix:/path` or `npipe://./pipe/<name>` ([sandbox.md](sandbox.md#containers-the-agents-broker)). Set only for a module approved for containers. |
| `OARBANK_TOOLS_FILE` | stable | A UTF-8 JSON file `{"<tool id>": ["<canonical path>", ...]}` for the module's approved `[sandbox].tools` on this node: exactly the paths the sandbox grants (resolved; conventional symlinks such as `/opt/homebrew/opt/...` are not readable inside the sandbox). `oarbank_sdk.tools.path(id)` reads it. |
| `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` | stable | Set for `egress-allowlist`: the agent's local proxy, the only network route ([sandbox.md](sandbox.md#sandbox-grants)). |
| `OARBANK_CONTROL_EVENT` | stable | Windows: the handle, in decimal, of the auto-reset event the runner inherits and the agent sets after every change to `control.json` ([Control](#control)). |
| `PYTHONUTF8=1` | stable | For Python runtimes, on every OS. |

A module gets host tools through `[sandbox].tools` and finds them in `OARBANK_TOOLS_FILE`; there are no tool-specific
variables such as `JAVA_HOME`.

All of this runs under the module sandbox ([sandbox.md](sandbox.md)).

## Workdir

The agent creates `<W>` fresh for each attempt, and deletes it after the result is recorded. Every path the runner
names is a PortablePath, `/`-separated ([platforms.md](platforms.md#portable-paths)).

| Path | Written by | Contents |
|---|---|---|
| `spec.json` | agent | The spec envelope ([envelopes.md](envelopes.md)). |
| `<mount>/...` | agent | Each dataset in `spec.datasets`, under `spec.mounts[id]`, as **read-only regular files** (never symlinks). How they are placed (clone, hardlink or copy) is the agent's business. Modifying one is a fault, and the agent may verify. |
| `inputs/<name>/...` | agent | Artifacts of the upstream stage (`spec.inputs`). |
| `control.json` | agent | The control document, always present ([Control](#control)). |
| `result.json` | runner | The result envelope, written atomically (a temporary file, then rename) before exiting 0. |
| `failure.json` | runner | `{reason, detail, fault?, retryable?}`, written atomically before a non-zero exit. `reason` is one of the agent's end reasons (`bad_input`, `mode_mismatch`, `oom`, `doctor`, `no_metrics`), which the coordinator maps to its reason codes, or the module's own `<module-short>/<code>`, which ends the attempt as `exit_nonzero` with the code in its detail. |
| `events.ndjson` | runner | One UTF-8 event per line (LF; CR tolerated): log, progress, metric, checkpoint or phase. |
| `phase` | runner | Optional: one line naming the current phase, replaced atomically. |

Writers replace atomically. A replace that fails because the other side has the file open (Windows) is retried for up
to 2 s. `oarbank_sdk` helpers do this. Output artifacts are listed in `result.json` as
`artifacts[].files[] = {path, local}`, with `local` relative to the workdir. The agent uploads each file by content
digest and replaces `local` with `digest` and `size`. Artifacts carry no file modes.

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

`<W>/control.json` is a `Control` document: `{seq, stop, pause, threads?, gpu_duty?, reason?}`.
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
  - A pause lasts at most 10 minutes. After that the agent releases the attempt (not a failure), and the job runs again
    elsewhere or later. Runners that declare `resumable` continue from their own checkpoint.
- **Throttle** (`cooperative_throttle`):
  - `threads` is the maximum number of active compute threads from the next safe point on.
  - `gpu_duty` is the maximum GPU duty fraction.
  - Time spent held is left out of any speed the result reports.

Host protection prefers cooperative runners: it throttles before it pauses, and pauses before it evicts.

### GPU use

`runner.gpu.use` (`none`, `shared` or `exclusive`) declares whether jobs use a GPU. `apis_any` and `min_vram_gb` select
devices. The core leaves GPU jobs pending (`GPU_BLOCKED`) while the node may admit none: the owner set
`gpu_jobs = "never"`, or a protected process uses that GPU.

## doctor

`doctor --json` prints a `DoctorOutput` to stdout and exits 0, even when unhealthy. It reports:
- `runner_protocol.supported`: the protocol majors the runner speaks;
- `health`: `healthy`, `unhealthy` (should work here but something is broken; this alerts) or `undetected` (this node
  cannot run it; no alert, never offered);
- `capabilities`, `attrs` (include `platform`) and per-check details.

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
