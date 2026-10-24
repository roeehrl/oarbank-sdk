# Changelog

What changed in each oarbank-sdk release, for module authors. The core's changelog is in
[roeehrl/oarbank](https://github.com/roeehrl/oarbank/blob/main/CHANGELOG.md). Contract rules:
[spec/versioning.md](spec/versioning.md).

## Unreleased

### Changed

- **On Linux, the GPU grant also lets the sandbox read the GPU's sysfs**
  ([spec/sandbox/backends/linux.md](spec/sandbox/backends/linux.md)). With `devices.gpu = compute`, a runner or doctor
  can read `/sys/class/drm` and its GPU devices' sysfs directories, which Vulkan and OpenCL read to identify the GPU.
  Without them a sandboxed doctor found no Vulkan device and no OpenCL platform where the host found both. The rest of
  sysfs stays closed. Applies from the next core release.

## 1.8.0 (2026-10-15)

For Oarbank core 2.11.0. Everything is additive within manifest 1 and module protocol 1. The cleanup stage needs
`requires.core >= 2.11`; the failure fields and the `fault`/`retryable` rule apply from core 2.10.4.

### Changed

- **A job that can never succeed is not retried** ([spec/runner-protocol.md](spec/runner-protocol.md#exit-codes)).
  The spec now says how `failure.json`'s `fault` and `retryable` combine. `fault: "job"` (or exit 2), or
  `retryable: false`, ends the job `failed` at once: it is never retried on other nodes and never quarantined.
  `fault: "job"` with `retryable: true` spends one of the stage's attempts. `retryable: true` with no fault is
  transient. From core 2.10.4 the coordinator acts on this. Before, a job fault spent the stage's retry and was then
  quarantined. A golden job that ends this way fails the module's certification on that node at once.

### New

- **A cleanup stage** ([spec/manifest.md](spec/manifest.md#cleanup-stage),
  [spec/runner-protocol.md](spec/runner-protocol.md#cleanup-runs), [spec/sandbox.md](spec/sandbox.md#cleanup-runs)).
  From core 2.11 a node deletes what a module left on it once the module leaves the node: its data directory, the
  datasets it cached and the container images it pulled. A module that also holds state outside the node declares
  `[[stages]] cleanup = true`; the agent runs that stage once, best effort and within its `timeout_s` (at most 600 s),
  before it deletes the module's data there. Its spec names the stage and carries `payload.reason` (`disabled`,
  `removed`, `retired` or `purge`); it gets the module's data directory, network and node settings, and no tools,
  folders, GPU, containers or secrets (`SandboxSection.for_cleanup()`). A failure is reported to the owner and the
  deletion goes ahead. No job runs the stage, so it adds no default, `result.merge` or comparison requirement to a
  module (rule 25, `Manifest.cleanup_stage()`, `Manifest.job_stages()`). It needs `requires.core >= 2.11`.
  `oarbank-sdk conform` runs a cleanup stage's runner specs as the agent runs them.
- **Why a job failed** ([spec/module-protocol.md](spec/module-protocol.md#why-a-job-failed)). From core 2.10.4, failed
  and quarantined jobs in `campaign.tick` (`CampaignJob.failure`) and `host.jobs.query` carry `failure`:
  `{reason, code, detail, fault, retryable, final, ...}`, the runner's reason code and detail included.

## 1.7.0 (2026-10-12)

For Oarbank core 2.10.4. Everything is additive within manifest 1 and module protocol 1; workload protocol 1 is new.

### New

- **Inbound listeners** ([spec/sandbox.md](spec/sandbox.md#inbound-listeners),
  [spec/service-protocol.md](spec/service-protocol.md#listeners), [docs/inbound-listeners.md](docs/inbound-listeners.md),
  [docs/make-a-node-reachable.md](docs/make-a-node-reachable.md)). A module may ask for TCP listeners the node's agent
  opens on nodes the owner assigns: `[sandbox].net.inbound` entries with a `name`, `port_hint`, `port_policy`
  (`stable` or `flexible`), `tls`, `handover` (`connections`, or `listen_fd` on macOS and Linux), `proxy_protocol`
  and the connection limits that are the module's ceilings (`max_conns`, `max_conns_per_ip`, `new_conns_per_ip_per_s`,
  `idle_timeout_s`, `max_bytes_per_s`). An endpoint service lists the listeners it serves in `services[].listeners`
  (rule 22). The module never listens: each accepted connection arrives over the service's endpoint channel
  (`{"op": "connection", "listener", "peer"}`), the agent announces each listener's external address
  (`{"op": "listener", ...}`, seeded from `OARBANK_LISTENERS_FILE`), and the service may narrow who connects
  (`listener_allow`). `oarbank_sdk.service_endpoint` serves listener connections (`Acceptor`, `serve_http`, PROXY v2
  with `read_proxy_v2`), and `listen_socket()` / `exec_with_listen_fds()` hand `listen_fd` sockets to a program that
  must accept itself. `inbound` is a sandbox request approved by digest. The conformance kit's **listener suite** plays
  the agent: it announces an address, hands a connection, runs `listener_specs` and fails a service that listens
  itself. **`examples/publicecho`** is the reference module.
- **Workloads** ([spec/manifest.md](spec/manifest.md#workloads), [spec/workload-protocol.md](spec/workload-protocol.md),
  [docs/workloads.md](docs/workloads.md)). `[[workloads]]` declares a long-running process that is the work itself (a
  node of a peer-to-peer network, a public inference server), assigned to nodes by the owner: its executable,
  `identity` (`none`, or `per_node` for one seat per identity under a coordinator lease), the secrets it receives,
  `restart` backoff and `max_failures`, `health`, `pause_tolerance_s`, `resources` and the listeners it serves
  (rule 24). Workload protocol 1 specifies its control document (`pause`, `threads`, `gpu_duty`, `stop`), events,
  health and containment; `requires.workload_protocol` names it, and the `workload.report` verb gives the coordinator
  side the workload's reports. `oarbank_sdk.workload.Supervisor` runs a third-party daemon as a workload (it keeps the
  daemon in the foreground, applies cooperative pause and thread limits, answers `status`) with the event helpers
  `progress`, `report` and `error`. The conformance kit's **workload suite** runs each workload as an agent would and
  fails one that leaves its process group, listens or ignores a stop. **`examples/workload`** is the reference module.
- **Holding a GPU duty** ([spec/runner-protocol.md](spec/runner-protocol.md#holding-a-gpu-duty),
  [docs/gpu-duty.md](docs/gpu-duty.md)). `oarbank_sdk.duty.Duty` (also `oarbank_sdk.gpu.Duty`, stdlib only) holds the
  `gpu_duty` of a runner's control document: wrap each unit of GPU work in `duty.unit()`; it idles `b·(1−d)/d` after a
  unit of `b` seconds, applies a newer document while it idles, holds at duty 0, sizes units with `scale()`, and writes
  `gpu_duty` metric events. Core 2.10 sets the duty under `gpu_jobs = "certified"` from an AIMD budget on the
  protected program's progress and pauses, then stops, a runner its measured GPU time shows over the duty plus 0.1.
- **`Duty.set(d)`** feeds a governor made without a control document, such as a workload's supervisor `gpu_duty`
  callback; a workload holds its duty the same way a runner does.
- **`Control.poll()`** applies a nudged document without blocking or raising; **`Control.wait()`** blocks until the next
  nudge.
- **Conformance: duty specs** ([spec/conformance.md](spec/conformance.md)). A `cooperative_throttle` runner runs each
  `duty_specs` task, is given `gpu_duty` once it names a phase, and must hold it: in its own reports and in what the kit
  measures (GPU time on macOS, CPU time per thread for a CPU stand-in). A GPU runner without duty specs is a warning.
- **`examples/llmserve`**: the reference module for GPU work beside a protected program. A `batch` stage on MLX or
  llama.cpp that holds its duty, and a deterministic `selftest` stage whose golden certifies it and whose CPU stand-in
  lets the duty spec run in CI without a GPU or a model.
- **Network-heavy stages** ([spec/manifest.md](spec/manifest.md#network-heavy-stages)).
  `stages[].requires.resources.network = "heavy"` (and `workloads[].resources.network`) says a stage's jobs, or a
  workload, move a lot of data over the node's internet line themselves. Where the owner groups nodes into sites (nodes
  sharing one line, core 2.10), such work runs only within the site's budget and pauses on every node of the site while a protected process there is in an upload phase. Older cores
  ignore it.

### Changed

- **The core-version gate for 1.7 features** (cross-field rule 12). Older cores ignore them, so the SDK refuses them
  unless `requires.core` admits only 2.10 and later: `[sandbox].net.inbound`, `services[].listeners` and
  `[[workloads]]`. `stages[].requires.resources.network` and `cooperative_throttle` duty specs need no gate (older cores
  ignore them harmlessly).
- An endpoint service now needs a pool or a listener (rule 18).

### Migrating from 1.6

Nothing a 1.6 module uses changes: rebuild and republish only to use a 1.7 feature.

- **Accepting connections:** move code that bound a port into an endpoint service that lists the listener and takes
  connections from its endpoint channel (or, for a program that must accept itself, `handover = "listen_fd"` and
  `exec_with_listen_fds()`); add the `[sandbox].net.inbound` entry, set `requires.core = ">=2.10,<3"`, run the listener
  suite, and publish a new version (the owner approves the new grant and assigns the listener per node).
- **A daemon that was a long job or an `always` service:** declare it as a workload, wrap a third-party daemon with
  `oarbank_sdk.workload.Supervisor`, give it `identity = "per_node"` if one key may run on one node at a time, and set
  `requires.core = ">=2.10,<3"`.
- **GPU work beside a protected program:** declare `cooperative_throttle`, wrap each unit in `Duty.unit()`, add duty
  specs, and check with `oarbank-sdk conform`.
- **Work that moves a lot of data over the node's line:** mark the stage or workload `resources.network = "heavy"`.

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
