# Workload protocol 1

Some work is a long-running process that is the work itself: a node of a peer-to-peer network, a pool worker paid per
unit of work, an inference provider that serves the public. Such a process never produces a result, must not be retried
on another node (its identity belongs to one machine), and loses its standing when it is paused for long. A
**workload** (`[[workloads]]` in the manifest, [manifest.md](manifest.md#workloads)) is how a module ships one: the
owner assigns it to nodes, and the agent runs it as one long-running process in the module sandbox, gives it its listed
secrets, controls it through the runner's control document, restarts it with backoff and treats it as fleet work in host
protection. Models: `oarbank_sdk.workload_protocol`. Schemas: `workload-*-1.schema.json`. Reference implementation:
`oarbank_sdk.workload` (stdlib only).

## Invocation

```
<workload.exec...> run       the workload itself: one long-running process
<workload.exec...> status    the health check, when `health` is declared
```

- **Exec:** the manifest's exec rules ([manifest.md](manifest.md#exec)): the `python` token and `{bundle}` are resolved
  as for runners. The agent appends the op. A workload limited by `platforms` runs only there.
- **cwd:** the run directory (`OARBANK_WORKLOAD_RUN`), for both ops.
- **Sandbox:** the module sandbox ([sandbox.md](sandbox.md)) with the module's approved grants, minus the container
  broker, plus the workload's private directory read-only.
- **One process, in the foreground.** `run` is the leader of the workload's process container (a POSIX process group,
  a Linux cgroup leaf, a Windows Job Object). It never daemonizes, never starts a new session or process group and never
  exits while its work goes on: **when the leader exits, the run has ended**, and the agent kills whatever is left in the
  container. A program that forks into the background is wrapped by a supervisor that keeps it in the foreground
  ([The supervisor shim](#the-supervisor-shim)).
- **Output:** stdout and stderr go to the workload's log on the node (appended, kept under 4 MiB). Never write a secret
  to it.
- **Exit:** after a stop request, any code (0 is usual). Otherwise any exit is a failure: the agent waits
  `min(restart.backoff_max_s, restart.backoff_initial_s × 2^(n−1))` (with `jitter = "full"`, a uniform delay between 0
  and that), starts it again, and withdraws it after `restart.max_failures` consecutive failures, until the owner
  restarts it, a new module version arrives or it is assigned again.

## Environment

The conventional per-OS variables ([platforms.md](platforms.md#environment-per-os)) with the home and temporary
directory inside the run directory, plus:

| Variable | Meaning |
|---|---|
| `OARBANK_WORKLOAD` | The workload's name. |
| `OARBANK_MODULE` | The module's short name. |
| `OARBANK_NODE_ID` | The node it runs on. |
| `OARBANK_PLATFORM` | The node's platform token. |
| `OARBANK_WORKLOAD_RUN` | The run directory `<module data>/workloads/<name>/run-<n>`: writable, fresh for each run (the previous one is deleted when the next starts), the cwd. |
| `OARBANK_MODULE_DATA` | The module's data directory, kept across runs (caches, a daemon's own database). |
| `OARBANK_CONTROL_FILE` | The control document, in the private directory, read-only ([Control](#control)). |
| `OARBANK_EVENTS_FILE` | `<run>/events.ndjson`: the workload appends its events ([Events](#events)). |
| `OARBANK_SECRETS_FILE` | Only for a workload that lists `secrets`: a UTF-8 JSON object `{"<name>": "<value>"}` with those secrets' values for this node, in the private directory, owner-only (POSIX 0600, a protected DACL on Windows) from its first byte, deleted when the run ends. `oarbank_sdk.workload.secrets()` reads it. |
| `OARBANK_SETTINGS_FILE` | The module's node-scoped settings for this node, as for runners ([manifest.md](manifest.md#settings)). |
| `OARBANK_TOOLS_FILE` | Its granted host tools, as for runners. |
| `OARBANK_LIMITS_FILE` | The owner's caps on the node. |
| `OARBANK_SEAT_ID`, `OARBANK_SEAT_FENCE` | Informational: the seat it runs in (`identity = "per_node"`) and the seat's fence. Nothing needs to act on them. |
| `OARBANK_CONTROL_EVENT` | Windows: the handle, in decimal, of the inherited auto-reset event the agent sets after every change to the control document. |
| `OARBANK_ENDPOINT_CHANNEL` | Only for a workload that serves `listeners`: its endpoint channel, as for an endpoint service ([service-protocol.md](service-protocol.md#endpoints)); every inbound connection arrives on it (`oarbank_sdk.service_endpoint.Acceptor`). |
| `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` | For `net.mode = "egress-allowlist"`: the agent's local proxy, the only route out. |

Settings and secrets are files, never environment variables: sizes differ per OS, and a child would inherit them.

## The private directory

`<agent home>/run/workloads/<module>/<name>/`: the control document, the secrets file, the settings, tools and limits
files and the sandbox profile. It is **read-only to the workload**, and the workload's sandbox profile is the only one
that may read it: the module's other processes (its stages, services and probes) never see this workload's secrets.

## Control

The workload's control document is the runner's ([runner-protocol.md](runner-protocol.md#control)), schema
`runner-control-1`: `{seq, stop, pause, threads?, gpu_duty?, reason?}`. The agent writes `{"seq": 0}` before the start
and replaces the document atomically with a higher `seq` on every change; apply only a newer one.

- **The nudge.** After every change the agent sends SIGUSR1 to the leader (POSIX; never to the rest of the container) or
  sets the control event (Windows). The leader starts with SIGUSR1 ignored, so a nudge before it is ready is lost
  harmlessly: install the handler first, then read the document.
- **The re-read.** Unlike a runner, a workload **also re-reads the document at least every 2 s**. A workload outlives
  agent restarts (the next agent adopts it), and an adopted workload's control event is gone on Windows, so a nudge can
  be missing.
- **`stop: true`:** exit within the manifest's `stop_timeout_s`. After that the agent kills the container: SIGKILL to
  the group and each tracked member on macOS, `cgroup.kill` on Linux, `TerminateJobObject` on Windows.
- **`pause: true`** (`control = ["cooperative_pause"]`): stop working and hold until a newer document clears it. A
  workload that declares `freeze_ok` may instead be frozen whole by the agent (SIGSTOP to the group and its tracked
  members, `cgroup.freeze`, every member of the Job Object suspended). Host protection pauses a workload for at most its
  `pause_tolerance_s`, then stops it and holds it while the cause lasts. While the agent holds a workload frozen it does
  not run its `status` op.
- **`threads`** (`cooperative_throttle`): the maximum number of compute threads from now on (absent: no limit).
- **`gpu_duty`** (`cooperative_throttle`): the maximum GPU duty fraction. A workload that honours it says so in its
  status (`gpu_duty_supported`).

`oarbank_sdk.workload.ControlWatch` reads the document on a nudge and every time its `wait(timeout)` returns.

## Events

The workload appends events to `OARBANK_EVENTS_FILE` (`<run>/events.ndjson`): one UTF-8 JSON object per line, LF
terminated (schema `workload-event-1`). The agent reads complete lines only and tails the file for as long as the run
lasts.

| `type` | Fields | What the agent does |
|---|---|---|
| `progress` | `t`, `event` (a lower-case name), `n` (default 1), any other attributes | Sums `n` per `event` and reports the counts in the node's heartbeat |
| `error` | `t`, `message` | Keeps the latest as the run's last error (shown with the workload's state and in its withdrawal alert) |
| `report` | `t`, `data` (an object of at most 4 KiB as compact JSON) | With `reports = true`: queues it (at most 200 waiting), ships at most 60 a minute per workload to the coordinator in heartbeats, numbered by a `seq` rising per workload on the node; the coordinator calls the module's `workload.report` verb with them ([module-protocol.md](module-protocol.md#workload-reports)) |
| `log` | `t`, `level` (`debug`, `info`, `warn`, `error`), `message` | Adds the line to the workload's log |

Helpers: `oarbank_sdk.workload.progress(event, n=1, **attrs)`, `report(data)` (refuses more than 4 KiB), `error(message)`
and `log(message, level)`. A report is how a workload's observations reach the module's own records without any job:
the verb's answer is store and file effects ([Keep your own records](#keep-your-own-records)).

## Health

With `health = {period_s, timeout_s, failures}` declared, the agent runs `<exec> status` (sandboxed, the same
environment) every `period_s` with `timeout_s`. It prints one JSON object (schema `workload-status-1`) and exits 0:

```json
{"healthy": true, "detail": "daemon 4242 running", "paused": false, "threads": 4, "gpu_duty_supported": false}
```

`healthy` and `detail` are required; `paused`, `threads` and `gpu_duty_supported` say what the workload applies now.
`failures` consecutive unhealthy answers (a timeout, a non-zero exit, output that is not a status, or `healthy: false`)
end the run as a failure: the agent kills the container and applies the restart policy. Without `health`, a running
leader is healthy.

## Containment

The container is what the agent pauses, lowers, throttles and stops, and what it kills when the leader exits. What each
OS gives:

| | macOS | Linux | Windows |
|---|---|---|---|
| The container | the leader's process group, plus every process of the agent's account that runs with this workload's sandbox profile (a process cannot drop the profile it inherited) | a delegated cgroup v2 leaf; without one, workloads are not offered | a named Job Object, breakaway refused |
| A process that calls `setsid` and forks twice | found by its sandbox profile and treated as a member | stays in the cgroup | stays in the job |
| Freeze | SIGSTOP to the group and each tracked member | `cgroup.freeze` | every member suspended |
| Throttle | the `threads` hint only | `threads` and `cpu.max` | `threads` and the job's CPU rate cap |
| Stop | SIGKILL to the group and each tracked member | `cgroup.kill` | `TerminateJobObject` |
| `net.mode = "egress-any"` | enforced | not enforceable: such workloads are placed on macOS and Windows nodes only | enforced |

Even where the agent can track a process that left the group, a workload must not do it: the conformance kit fails a
workload any of whose processes leaves the leader's process group.

## What a workload never does

- Daemonize, start a new session or process group, or leave its process container. Wrap a program that does in the
  supervisor shim, or run it with its own "stay in the foreground" option.
- **Listen.** The sandbox refuses `bind` and `listen` (so a workload can never raise a firewall prompt); a workload that
  serves the public declares `listeners`, and every inbound connection is handed to it over `OARBANK_ENDPOINT_CHANNEL`.
- Read the user's keychain or credential stores (the sandbox denies them), or anything outside its grants.
- Write a secret to its log, events, run directory or module data, or pass one on a command line.
- Start persistent jobs (launchd, systemd, cron, scheduled tasks, services) or rely on running after its leader exits.

## The supervisor shim

Most workloads wrap a third-party daemon that knows nothing of this protocol. `oarbank_sdk.workload.Supervisor` is the
reference shim, stdlib only (a module may vendor the file):

```python
# worker.py; the manifest's exec is ["python", "-I", "{bundle}/worker.py"]
import sys
from oarbank_sdk.workload import Supervisor

def on_line(line):                     # each line the daemon prints; also passed through to the log
    if line.startswith("unit "):
        return {"type": "progress", "event": "unit", "n": 1}

sup = Supervisor([sys.executable, "-I", "unitd.py", "--foreground"], threads="--threads={n}", line_handler=on_line)
sys.exit(sup.main())                   # `run` or `status`
```

- `run`: starts the daemon as its child **in its own process group**, watches the control document (the nudge and a
  1 s re-read), and translates it: `pause` (`"signal"`: SIGSTOP and SIGCONT to the daemon and all of its descendants,
  found through `/proc` on Linux, `ps` on macOS and a Toolhelp snapshot on Windows, where it suspends with
  `NtSuspendProcess`; never the supervisor itself; or `"none"`, or a callable), `threads` (a callable, or a flag template:
  the daemon is restarted with the flag when the limit changes) and `gpu_duty` (a callable). On a stop it sends
  `stop_signal` (SIGTERM; on Windows it terminates the daemon's tree), kills what is left after its own
  `stop_timeout_s` (keep it below the manifest's) and exits 0. When the daemon exits on its own, the supervisor exits
  non-zero, so the agent's restart policy applies (or restarts it itself with `restart_on_exit`). SIGTERM to the
  supervisor is a stop too.
- It rewrites `<run>/.supervisor.json` every 2 s: the daemon's pid, whether it runs, paused, threads, gpu_duty, the time
  of its last line, restarts.
- `status`: healthy while that file is less than three intervals old, the daemon runs and the optional `health`
  callable (`() -> (bool, detail)`) agrees.
- Any other op exits 64.

## Keep your own records

A workload that earns or spends keeps the module's own record of it, from its reports: `report({"ref": ..., "amount":
...})` on the node, and on the coordinator `workload.report` answers `store.write` effects, one document per report
keyed by the source's own reference. Amounts are integers in the smallest unit; a correction is a new record that
reverses the old one, never an edit. Ask owners only for public identifiers (an address to watch), never for a key that
controls funds.

## The conformance kit

`oarbank-sdk conform` runs the **workload** suite for each workload declared on the host's platform: it plays the agent
(a run directory, the private directory, the environment above, `run` as the leader of a new process group or, on
Windows, of a Job Object the kit creates) and checks that the workload keeps running, answers `status` healthy, pauses
and resumes, **stays in its process container**, never listens, writes valid events and ends on a stop within
`stop_timeout_s` ([conformance.md](conformance.md)).
