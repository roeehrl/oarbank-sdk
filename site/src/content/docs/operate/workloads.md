---
title: Workloads
description: "Run a module's long-running workload on Oarbank nodes: assign it, give each node its own secrets, read its state and see how host protection throttles it."
type: how-to
platforms: [macos, linux, windows]
core: '2.11.1'
sidebar:
  order: 12
---

Most module work is jobs: short units the coordinator hands out, retries and compares. A **workload** is different: one
process that runs for days and is the work itself, for example a node of a peer-to-peer network or a server that answers
the public. It never produces a result and is never moved to another node on its own. You decide where it runs. This
page covers Oarbank 2.11.1. Module authors: [Run a long-running workload](/oarbank/build/workloads).

## What a workload is

A module declares its workloads in its manifest. Each one says:

| | What it means for you |
|---|---|
| **Seat** | `node`: you assign it to nodes, one copy each. `fleet`: you ask for a number of copies and the coordinator places them once |
| **Identity** | `none`: any number of nodes may run it. `per_node`: its identity comes from secrets you set for each node, and one identity never runs on two nodes at once |
| **Secrets** | The module secrets it receives. Only that workload sees them, in a file deleted when its run ends |
| **Resources** | The cores and memory it reserves on the node while it runs |
| **Pause tolerance** | The longest pause it survives. Host protection stops it rather than pause it longer |
| **Listeners** | The [inbound listeners](/oarbank/operate/make-a-node-reachable) it serves, if any |

The agent runs it in the module's sandbox, with the grants you approved for the module, inside a process container of its
own: the process group plus every process running with its sandbox profile on macOS, a cgroup on Linux, a Job Object on
Windows. It restarts a workload that exits, with growing pauses, and gives up after the module's limit of failures in a
row.

## Assign a workload

Install, approve and enable the module as usual ([Running modules](/oarbank/operate/modules)). Then, on the module's
**Workloads** tab, choose a node and **Assign** (or, for a `fleet` seat, **Place seats** with a count). From a terminal:

```sh
oarbank workload list --module example                      # what is assigned, and its state on each node
oarbank workload assign example/worker --node build-07 --dry-run
oarbank workload assign example/worker --node build-07
oarbank workload assign example/pool --count 3              # a seat = "fleet" workload: three copies, placed once
```

Assigning is a reviewed change. The preview names the node, the secrets the workload will receive there (by
fingerprint, never the values), the listeners it opens and the protection rules that act on it. A `fleet` workload's
copies go to the eligible nodes with the most free memory; the coordinator never moves them afterwards.

A workload only starts where it can run: the module is enabled there, the node's platform is one the module and the
workload support, the sandbox is enforced, and on Linux the node has a delegated cgroup to contain it. A workload that
needs unrestricted internet access (`egress-any`) is placed on macOS and Windows nodes only, because Linux cannot enforce
that mode.

## Per-node identity and seats

A workload with `identity = "per_node"` holds a key or account of its own on each node. Set each of its secrets **for
each node**:

```sh
oarbank secret set example signing_key --node build-07      # the value is read from stdin or a hidden prompt
```

A value set for a group or for the whole module is refused for such a workload (`WORKLOAD_IDENTITY_SHARED`): two nodes
would then hold one identity. A secret with no value for the node keeps the workload from starting
(`WORKLOAD_SECRETS_NOT_SET`).

The workload's **seat** is named by fingerprints of those secrets, so two nodes given the same key compete for one seat
and only one of them runs it; the other shows `WORKLOAD_SEAT_ELSEWHERE`. The node holding the seat has a 30-minute
**lease**, renewed with every heartbeat:

- The agent keeps the lease on a clock that counts sleep. A node that slept past its lease stops the workload when it
  wakes (`WORKLOAD_SEAT_LEASE_EXPIRED`), then asks again.
- A seat moves to another node as soon as its holder reports the workload stopped, or 60 seconds after the lease ran out.
- If the coordinator is unreachable, workloads keep running until their leases end. A workload with no identity keeps
  running.
- An agent restart does not stop a workload: the next agent adopts it while its lease runs. A workload that serves
  listeners restarts instead, because its connections went with the old agent.

## Read its state

| Where | What it shows |
|---|---|
| The module's **Workloads** tab | Each workload with its seat, identity, secrets, pause tolerance and resources, and per node: state, why, seat and fence, lease left, restarts, last error, the log tail, **Restart** and **Unassign** |
| The node page's **Workloads** card | What runs on that node, held or paused and why, and the lease left |
| `oarbank workload list [--node N] [--module M] [--json]` | The same rows in a terminal |
| `oarbank workload logs <module>/<workload> --node N [--follow]` | The workload's log tail |

The states are `running`, `starting`, `stopping`, `held` (host protection keeps it down for now), `withdrawn` (it
failed too often) and stopped with a reason. The reasons are the `WORKLOAD_…` codes in
[Reason codes](/oarbank/reference/reason-codes#workload).

## When a workload keeps failing

A workload that exits is started again after a growing, randomised pause. After the module's `max_failures` failures in
a row it is **withdrawn**: it stops, gives its seat up, and the `workload_withdrawn` alert opens with its last error and
log tail. Fix the cause (a secret, a setting, the module), then:

```sh
oarbank workload restart example/worker --node build-07
```

**Restart** also lifts a withdrawal. The alert clears once the workload runs again. **Unassign**
(`oarbank workload unassign example/worker --node build-07`) stops it on that node for good.

## Host protection and workloads

A workload is fleet work: host protection acts on it as on jobs, with its own ladder, and never pauses it longer than it
tolerates.

1. **Not started** while the node admits no work of its kind (a pause rule, GPU work not allowed for a GPU workload, the
   memory guard).
2. **Throttled** while the fleet's CPU budget binds: fewer threads through its control document, and a hard CPU cap on its
   container on Linux and Windows.
3. **Lowered** to background scheduling, as jobs are.
4. **Paused**, when it can be paused and only up to its pause tolerance. One that cannot be paused is lowered instead.
5. **Stopped and held** while the cause lasts: a pause longer than its tolerance (`WORKLOAD_PAUSE_TOLERANCE`), a rule's
   `evict`, the memory guard (`MEMORY_HARD_FLOOR_WORKLOAD`), or GPU work not allowed for a GPU workload that cannot
   pause. A hold is not a failure: it starts again when the cause clears.

Each action is in the node's decision timeline (`WORKLOAD_PAUSE`, `WORKLOAD_THROTTLE`, `WORKLOAD_HOLD`, and so on). The
pause probe never pauses a workload. A workload whose module marks it network-heavy also pauses, then holds, while its
[site](/oarbank/operate/sites) is in an upload phase, and counts against the site's budget. Under
`gpu_jobs = "certified"`, a GPU workload that holds its duty shares the certified GPU duty with cooperative jobs
([Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy#the-duty-governor)).

## Turn workloads off on a node

The node setting `workloads` (on by default) lets assigned workloads run. Off, the node runs none and stops the running
ones (`WORKLOAD_POLICY_OFF`). A machine's managed policy may force it off
([Settings](/oarbank/operate/settings#managed-on-this-machine)). Pausing or draining a node also stops its workloads
(`WORKLOAD_NODE_NOT_ACTIVE`), and removing Oarbank from a node stops them
([Updating and removing](/oarbank/operate/update-and-remove#remove-a-node)).

## Not in this release

- Container workloads: a workload runs as a process on the node, never in a container.
- A program the person runs themselves (for example one that needs the login keychain) cannot be a workload. It can be
  a [delegated process](/oarbank/operate/delegated-processes): yours, but paused and lowered by host protection.
