# Run a long-running workload

Some work is not a job. A node of a peer-to-peer network, a pool worker paid per unit of work, an inference provider
that serves the public: each is one process that runs for days, never produces a result, must not be retried on another
machine, and loses its standing when it is paused for long. Ship it as a **workload**: the owner assigns it to nodes,
and each node's agent runs it in your module's sandbox, controls it through the same document runners get, restarts it
with backoff and lets host protection throttle, pause or stop it like any fleet work.
[`examples/workload`](../examples/workload) is the complete reference: a stand-in third-party daemon, the supervisor shim
around it, and the records its reports become. The contract is [workload-protocol.md](../spec/workload-protocol.md).

## 1. Declare the workload

```toml
[requires]
core = ">=2.10,<3"
workload_protocol = [1]

[coordinator]
capabilities = ["workload.report"]
workload_effects = ["store.write"]   # what workload.report may change: the module's own records only

[[workloads]]
name = "worker"
exec = ["python", "-I", "{bundle}/worker.py"]       # the agent appends `run` or `status`
seat = "node"                                        # the owner assigns it to nodes, one seat each
identity = "none"                                    # or "per_node": the seat is named by the node's own secrets
resources = { cpu_cores = 1, mem_gb = 0.2 }          # counted against the node while it runs
control = ["cooperative_throttle", "freeze_ok"]      # a thread limit, and the agent may freeze it
pause_tolerance_s = 60                               # longer than this, protection stops it instead
stop_timeout_s = 15
health = { period_s = 15, timeout_s = 5, failures = 3 }
reports = true                                       # its report events reach workload.report
```

- **`control`** is what host protection may do besides stopping it. `cooperative_throttle`: it honours the control
  document's `threads` (and `gpu_duty`). `cooperative_pause`: it stops working on `pause` and holds. `freeze_ok`: the
  agent may freeze its whole process container. Declare neither pause item and it is never paused: it is throttled where
  it can be, lowered, or stopped.
- **`pause_tolerance_s`** is the longest pause the work survives (peers that drop a silent member, a pool that reassigns
  its share). It defaults to 20 for a pausable workload and 0 otherwise.
- **`secrets`** a workload lists reach only that workload, in an owner-only file deleted when its run ends. With
  `identity = "per_node"` the owner sets each one for each node, and at most one node runs an identity at a time.
- `oarbank-sdk check` enforces the rules ([manifest.md](../spec/manifest.md#workloads), rule 24).

## 2. Wrap the daemon in the supervisor shim

The agent runs `<exec> run` as one long-running process, the leader of the workload's process container. It must stay
in the foreground, never start a new session or process group and never listen; when it exits, the run has ended. Most
third-party daemons know none of this, so run them under `oarbank_sdk.workload.Supervisor` (stdlib only: vendor the
file if you like):

```python
# worker.py
import os
import re
import sys

from oarbank_sdk.workload import Supervisor, progress

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT = re.compile(r"^unit (\S+) amount=(\d+)$")


def on_line(line):                       # every line the daemon prints; it also reaches the workload's log
    m = UNIT.match(line)
    if not m:
        return None
    progress("unit")
    return {"type": "report", "data": {"unit": m.group(1), "amount": int(m.group(2))}}


supervisor = Supervisor([sys.executable, "-I", os.path.join(HERE, "unitd.py")],
                        threads="--threads={n}",      # restart the daemon with this flag when the limit changes
                        pause="signal",               # SIGSTOP/SIGCONT the daemon and its children (Windows: suspend)
                        line_handler=on_line,
                        stop_timeout_s=10)            # below the manifest's stop_timeout_s

if __name__ == "__main__":
    sys.exit(supervisor.main())
```

The supervisor starts the daemon as its child in its own process group, watches the control document (the agent's nudge
and a re-read every second), ends the daemon on a stop (SIGTERM, then a kill) and exits; when the daemon exits on its own
the supervisor exits non-zero, so the agent's restart policy applies. `pause` and `threads` also take callables, for a
daemon you control through its own API; `gpu_duty` takes one too.

A daemon that forks into the background usually has an option to stay in the foreground: use it. If it has none, the
conformance kit will tell you (step 5).

## 3. Answer `status`

With `health` declared, the agent runs `<exec> status` every `period_s`. The supervisor answers from the status file
its run loop rewrites every 2 s, so it is healthy while the daemon runs; add your own check of the daemon's state:

```python
def health():
    st = json.loads(open("unitd-state.json").read())    # the run directory is the cwd of both ops
    age = time.time() - st["t"]
    return age < 60, f"{st['units']} unit(s) done"

supervisor = Supervisor([...], health=health)
```

`health.failures` unhealthy answers in a row end the run as a failure and the agent starts it again.

## 4. Report into your own records

Events go to `OARBANK_EVENTS_FILE`: `progress(event, n)` counts (summed into the node's heartbeat), `error(message)`
(the run's last error), `log(message)`, and `report(data)`, an observation of at most 4 KiB. With `reports = true` the
agent ships reports to the coordinator, at most 60 a minute per workload, and your coordinator's `workload.report` verb
turns them into effects on the module's own records:

```python
@module.verb("workload.report")
def workload_report(p: mp.WorkloadReportParams, ctx) -> mp.WorkloadReportResult:
    effects = [fx.store_write("units", f"{p.node}.{p.workload}.{e.seq}",
                              {"node": p.node, "unit": e.data["unit"], "amount": e.data["amount"], "at": e.t})
               for e in p.events if isinstance(e.data.get("amount"), int)]
    return mp.WorkloadReportResult(effects=effects)
```

### Keep and show your own records

Whatever a workload earns, spends or counts is the module's business, not the core's: keep it in your store, from the
reports, and show it on your pages.

- **Integers in the smallest unit.** Never floats: an amount is a whole number of the source's smallest unit, and the
  page formats it.
- **Key each record by the source's own reference**: the daemon's name for the unit, the network's identifier for the
  event, or the report's `seq` with its node and workload. A delivery may repeat; the same key writes the same document
  again, so nothing is counted twice.
- **Correct by reversing.** Never edit or delete a record that was right when written: add one that reverses it (the
  same amount, negated, naming the record it reverses), then the right one.
- **Ask owners only for public identifiers**, such as an account or address to watch, never for a key or credential that
  controls funds. If the work needs a signing key, it is a secret the owner sets per node, and it never leaves that
  node's workload.

A page reads the records with the `store` host query
([`ui/pages/overview.json`](../examples/workload/ui/pages/overview.json)):

```json
{"type": "stat", "label": "total amount", "field": "amount", "format": ",d",
 "source": {"query": "store", "params": {"collection": "units"}, "agg": {"amount": "sum"}}}
```

## 5. Test it

`oarbank-sdk conform` runs the **workload** suite after the runner suite (`--only workload` runs it alone). It plays
the agent: a run directory, the control document, the protocol's environment and a token unique to the run, `run` as
the leader of a new process group, sandboxed as on a node. It checks that the workload keeps running, answers `status`
healthy, survives a freeze or shows a cooperative pause, **stays in its process container** (every process carrying the
token must be in the leader's group, so a daemon that calls `setsid` and forks twice fails), never listens, writes valid
events and stops within `stop_timeout_s`. Give the protocol suite a few report `data` objects to deliver to
`workload.report`:

```json
{"workload_reports": [{"unit": "3f2a91c0-1", "amount": 137}]}
```

## On each operating system

The same code runs everywhere; the container differs. On Linux it is a cgroup (a daemon cannot leave it), on Windows a
Job Object (breakaway refused), and on macOS the leader's process group plus every process running with the workload's
sandbox profile. The agent freezes a container with SIGSTOP on macOS, the cgroup freezer on Linux and by suspending every
process on Windows. Linux cannot enforce
`net.mode = "egress-any"`, so such a workload is placed on macOS and Windows nodes only.

## What the owner does

Nothing runs until the owner assigns the workload: per node (`seat = "node"`) or as a number of copies the coordinator
places once (`seat = "fleet"`), from the module's **Workloads** tab or `oarbank workload assign`. With
`identity = "per_node"` the owner sets each secret for each node. The tab shows each node's state, seat, lease, restarts,
last error and log tail; a workload that fails `max_failures` times in a row is withdrawn until the owner restarts it.
See [Workloads](https://docs.codonic.dev/oarbank/operate/workloads) in the operator guide.
