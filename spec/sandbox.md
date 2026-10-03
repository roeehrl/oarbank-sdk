# The module sandbox

Every module process runs confined: the coordinator side, the runner, services, probes and `doctor`, on every
platform. This page is the **contract**: what a module process may and may not reach. Each platform enforces it with
its own backend:

| Platform | Backend | Details |
|---|---|---|
| macOS | Seatbelt | [backends/macos.md](sandbox/backends/macos.md) |
| Linux | Landlock + seccomp, plus a cgroup v2 leaf | [backends/linux.md](sandbox/backends/linux.md) |
| Windows | AppContainer + Job Object | [backends/windows.md](sandbox/backends/windows.md) |

A module never sees the backend. It declares what it needs in `[sandbox]`, and an operator approves it.

## What every module process may touch

| Area | Coordinator | Runner | Service, probe |
|---|---|---|---|
| its bundle, its runtime, the platform's base system | read, execute | read, execute | read, execute |
| work directory | none | read, write | none |
| data directory (kept across jobs) | read, write | read, write | read, write |
| private temporary directory | read, write | read, write (inside work) | read, write (inside data) |
| approved host tools (`[sandbox].tools`) | none | read, execute | read, execute |
| everything else: homes, other modules, the agent's and coordinator's state, credential stores, devices other than null, zero, random and urandom | denied | denied | denied |

**Processes**
- A module process may spawn children, and children inherit the confinement.
- It may signal only processes in its own confinement.
- It never creates persistent or detached jobs (launchd, systemd, cron, scheduled tasks, services) and never leaves
  its process container.
- It cannot rely on sandboxing itself again: tools such as Bazel or SwiftPM must skip their own sandbox.

**Local IPC.** The only channel is the job's broker endpoint, when granted. There are no other sockets, pipes, D-Bus,
mach services or Docker sockets. Name resolution and logging go through backend-listed system facilities.

**Grants are whole directories or single files**, resolved to canonical absolute paths at launch. There are no globs
and no "deny inside allow" exceptions.

**Hiding which files exist is not promised** (Landlock cannot restrict `stat`). Keep secrets out of readable paths and
out of the environment.

**Identity.** A module must not depend on its user, uid or SID, or on privileges.

**Resources.** The agent may enforce `mem_gb` and CPU as hard limits where the OS does (Linux cgroups, Windows Job
Objects). Exceeding the memory limit is a job fault (`oom`).

**Verification.** The parent verifies confinement after every spawn and kills an unconfined process. No unconfined
module process ever runs.

## `[sandbox]`: grants

```toml
[sandbox]
contract = 1
net = { mode = "egress-allowlist", allow = ["api.example.org", "*.files.example.org:8443"] }
tools = [{ id = "renderer4", trust = "code-exec" }]
devices = { gpu = "compute" }
containers = [{ image = "docker.io/org/tool:1.2@sha256:<64 hex>", platform = "linux/amd64" }]
exec_writable = false
```

| Grant | What the process gets |
|---|---|
| `net.mode = "none"` (default) | No network at all. |
| `net.mode = "egress-allowlist"` | Only the `allow` hosts, `host[:port]` with port 443 by default and `*.` for subdomains, never IP addresses. Traffic goes through **the agent's local proxy**, which enforces the list and refuses a name that resolves to a non-public address (loopback, link-local, private, multicast); `oarbank_sdk.egress_proxy` is the reference. The agent sets `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`; the backend blocks every other route. |
| `net.mode = "egress-any"` | Outbound connections to any public address, plus DNS. A separately approved **full-trust** grant. |
| `tools` | Read and execute the host paths that the operator's **tool registry** maps the id to, per OS (e.g. `renderer4` → the renderer's install directory), resolved to canonical paths; the process finds them in `OARBANK_TOOLS_FILE`. `trust = "code-exec"` flags tools that run arbitrary code (a JVM, an interpreter, a shell) in the approval UI. On Windows, any readable binary is executable. |
| `devices.gpu = "compute"` | GPU compute through the platform's APIs, with no display server. Flagged at approval: it widens the kernel surface. |
| `containers` | The job's broker endpoint, for these digest-pinned images only. A stage that runs containers reserves the agent-provided `containers` pool. |
| `exec_writable = true` | Runners may execute files they wrote into the data or work directory, such as downloaded tools. Windows cannot enforce `false` without application control, so nodes report it as `unavailable` there. |

**Network rules, whatever the mode:**
- never loopback or link-local;
- never listening;
- never another unix socket or pipe.

**Approval**
- A version whose `[sandbox]` requests anything cannot be enabled, canaried, pinned or promoted until an operator
  approves its exact requests. The approval is recorded by digest and audited.
- A new version needs its own approval. A version that requests nothing needs none.

**Placement.** Every node reports, per capability, whether its backend enforces it: `enforced`, `cooperative` or
`unavailable`. A module runs only where all of its grants, and the always-on rules above, are enforced. A node that
cannot enforce them advertises `SANDBOX_BACKEND_MISSING` or `CAPABILITY_NOT_ENFORCED` for that module.

## Containers: the agent's broker

A runner never talks to Docker or Podman itself. The agent owns a container runtime that mounts only its own work and
module-data directories:

| Platform | Runtime |
|---|---|
| macOS | Colima, or Apple `container` |
| Linux | rootless Podman, or Docker Engine |
| Windows | an agent-owned WSL2 distribution running Podman |

Each job whose module may run containers gets an endpoint, `OARBANK_BROKER` (`unix:/path` or `npipe://./pipe/<name>`),
the only IPC its confinement allows. Requests are one JSON object per line, one request per connection. Use
`oarbank_sdk.broker`.

| Request | Fields | Answer |
|---|---|---|
| `container.run` | `image` (an approved reference), `platform`, `args[]`, `entrypoint?`, `mounts[] {src, dst, ro}`, `env{}`, `workdir?`, `network` (only with an egress grant), `timeout_s`, `cpus?`, `mem_gb?` | `{ok, exit_code, stdout_tail, stderr_tail, stdout_path, stderr_path, duration_s}`; the full output is written into the work directory |
| `container.pull` | `image`, `platform` | `{ok}` once the image is present |
| `status` | | `{ok, running, images[]}` |
| any refusal | | `{ok: false, error, detail}`: `image_not_approved`, `bad_mount`, `network_not_granted`, `runtime_unavailable`, `platform_unavailable` |

**Mounts**
- Mount sources are PortablePaths inside the work directory, or `data:<path>` inside the data directory.
- Destinations are POSIX absolute paths, for `linux/*` images.

**The agent's container run**
- Always: `run --rm`, the platform, the CPU and memory limits within the job's reservation, the validated mounts and
  the job's ownership labels.
- Never: privileged mode, host networking, or another socket.

**Ownership and filesystem behaviour**
- Files a container writes belong to the job's identity: rootless mode, or a user namespace, on Linux.
- Exec bits and case sensitivity follow the host filesystem.
- Containers die with their job.

## Testing your module

`oarbank-sdk conform` runs the runner suite under this host's backend with the grants the manifest declares, plus a
portability lint for every declared platform. A module that writes outside its directories, reads homes, or expects a
tool it didn't request fails there first.
