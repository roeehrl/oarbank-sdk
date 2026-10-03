# Service protocol 1

Some modules need something long-lived on the node: a VM, a daemon, a container runtime. Others need a fact checked, such as a JDK being present. The agent manages both generically. A **service** is an executable in the module's bundle that implements seven operations. A **probe** implements only `fingerprint`. The agent knows nothing about Colima, Docker or Java; the module's own executables do. Models: `oarbank_sdk.service_protocol`. Schemas: `service-*-1.schema.json`.

## Invocation

```
<service.exec...> <op> [args]
ops: fingerprint | start | stop | status | ready | list_owned | destroy <id> [--force]
```

- **Environment:** the per-OS conventional variables ([platforms.md](platforms.md#environment-per-os)) with the home and temporary directory inside the module's data directory, plus `OARBANK_MODULE_DATA`, `OARBANK_MODULE`, `OARBANK_SERVICE`, `OARBANK_NODE_ID`, `OARBANK_PLATFORM`, `OARBANK_SETTINGS_FILE` (the module's settings for this node), `OARBANK_TOOLS_FILE` (its granted host tools, as for runners) and `OARBANK_LIMITS_FILE` (the owner's caps). Settings and limits are UTF-8 JSON files, because environment sizes differ per OS.
- **Exec:** the manifest's exec rules ([manifest.md](manifest.md#exec)), cwd the bundle root. A service limited by `platforms` runs only there.
- **Sandbox:** services and probes run under the module sandbox ([sandbox.md](sandbox.md)) with the module's approved grants, minus the container broker. A service cannot start VMs or containers itself: a module that needs containers declares them in `[sandbox].containers` and its jobs use the agent's broker, whose `containers` pool replaces a module's own container service.
- **Output:** one JSON document on stdout. Exit 0 means the operation succeeded.
- **Idempotency:** every op must be idempotent. `start` on a running service succeeds; `stop` on a stopped one succeeds.

| Op | Default timeout | Returns | Meaning |
|---|---|---|---|
| `fingerprint` | 5 s | `Fingerprint` | Health, attrs, the pool tokens it can provide now, the host resources it reserves while running, and whether it is running. It must be cheap and read-only. |
| `start` | 120 s | `OpResult` | Bring the service up. The manifest's `start_timeout_s` overrides the timeout. |
| `stop` | 120 s | `OpResult` | Stop gracefully. Set `escalated` if the forceful path was needed. |
| `status` | 5 s | `Status` | Running and ready flags. |
| `ready` | 5 s | `Status` | Readiness gate: the agent admits no job that depends on the service until `ready` returns true. |
| `list_owned` | 5 s | `OwnedList` | Objects (containers, VMs, temporary volumes) the service created for oarbank attempts. |
| `destroy <id>` | 60 s | `OpResult` | Remove one owned object. With `--force`, remove it even if it looks busy. |

## Lifecycle

`services[].lifecycle` in the manifest takes one of three values.

- **`on_demand`** (default): the agent reference-counts admitted jobs that need the service's pools or capabilities.
  - It starts the service when the first such job is admitted, and waits for `ready`.
  - It stops the service after `idle_timeout_s` with no users.
- **`always`**: kept running while the module is enabled on the node.
- **`manual`**: the agent never starts or stops it. It only fingerprints it.

**Restarts:** failures back off exponentially (`restart.backoff_initial_s` up to `backoff_max_s`). After `max_failures`, the service is marked unhealthy and its capabilities are withdrawn.

**Adoption:** after an agent restart, `fingerprint.running` tells the agent to adopt a running service rather than start a second one.

## Pools and reservations

- `fingerprint.pools` reports how many tokens of each declared pool the service can provide. The module computes this from its own settings and the host. Stages reserve tokens through `requires.pools` or require them through `requires.needs_pools`.
- With `reserves_host_memory = true`, `fingerprint.reserve.mem_gb` is charged against the node's memory budget while the service runs. Host protection accounts for it.
- `yieldable = true` lets the agent stop an idle service under memory pressure.
- `freeze_ok = true` lets the agent freeze the service's process container (SIGSTOP, or the cgroup freezer). Freezing frees no memory.

## Ownership and reaping

Everything a service or runner creates outside the workdir must carry these three labels: `oarbank.attempt_id`, `oarbank.node` and `oarbank.service`.

- The agent calls `list_owned` periodically, and `destroy`s objects whose attempt is no longer live.
- Objects without the labels are never touched, so oarbank never reaps something the owner created by hand.

## Probes

A probe is `probes[].name` (the capability it establishes) plus an `exec` that implements `fingerprint` only. The agent runs it every `period_s`, and again after doctor-triggering events. A capability is available while its probe reports `healthy`.
