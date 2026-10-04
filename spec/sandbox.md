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
| approved folders (`[sandbox].folders`) | none | read (input folders); create and write only (outboxes) | none |
| everything else: homes, other modules, the agent's and coordinator's state, credential stores, devices other than null, zero, random and urandom | denied | denied | denied |

**Processes**
- A module process may spawn children, and children inherit the confinement.
- It may signal only processes in its own confinement.
- It never creates persistent or detached jobs (launchd, systemd, cron, scheduled tasks, services) and never leaves
  its process container.
- It cannot rely on sandboxing itself again: tools such as Bazel or SwiftPM must skip their own sandbox.

**Local IPC.** The only channels are the job's broker endpoint, when granted, and the endpoint handles the agent hands
out itself ([service-protocol.md](service-protocol.md#endpoints)): a job's connectors to its module's endpoint
services and an endpoint service's channel, inherited and already connected, so no rule has to allow a name. There are no
other sockets, pipes, D-Bus, mach services or Docker sockets. Name resolution and logging go through backend-listed
system facilities.

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
folders = [{ id = "inputs", access = "read" }, { id = "outbox", access = "write" }]

[[sandbox.container_sets]]          # images approved by signature (core 2.5): see "Image sets"
name = "tasks"
registry = "ghcr.io"
repository = "example/swe-tasks/"
platform = "linux/amd64"
key = "keys/tasks.pub"
```

| Grant | What the process gets |
|---|---|
| `net.mode = "none"` (default) | No network at all. |
| `net.mode = "egress-allowlist"` | Only the `allow` hosts, `host[:port]` with port 443 by default and `*.` for subdomains, never IP addresses. Traffic goes through **the agent's local proxy**, which enforces the list and refuses a name that resolves to a non-public address (loopback, link-local, private, multicast); `oarbank_sdk.egress_proxy` is the reference. The agent sets `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`; the backend blocks every other route. |
| `net.mode = "egress-any"` | Outbound connections to any public address, plus DNS. A separately approved **full-trust** grant. |
| `tools` | Read and execute the host paths that the operator's **tool registry** maps the id to, per OS (e.g. `renderer4` → the renderer's install directory), resolved to canonical paths; the process finds them in `OARBANK_TOOLS_FILE`. `trust = "code-exec"` flags tools that run arbitrary code (a JVM, an interpreter, a shell) in the approval UI. On Windows, any readable binary is executable. |
| `devices.gpu = "compute"` | GPU compute through the platform's APIs, with no display server. Flagged at approval: it widens the kernel surface. |
| `containers` | The job's broker endpoint, for these digest-pinned images only. A stage that runs containers reserves the agent-provided `containers` pool. |
| `container_sets` | The job's broker endpoint, for digest-pinned images under a registry and repository prefix that carry a cosign signature by a pinned key (or that a signed index lists), when the job lists them ([Image sets](#image-sets)). |
| `exec_writable = true` | Runners may execute files they wrote into the data or work directory, such as downloaded tools. Windows cannot enforce `false` without application control, so nodes report it as `unavailable` there. |
| `folders` | Runners only. Each id is a folder the operator maps to a path **per node** in the folder registry ([Folders](#folders)); the runner finds the granted ones in `OARBANK_FOLDERS_FILE`. `access = "read"`: read files and listings, never write or execute. `access = "write"`: an outbox: create files and directories and write them, never read, list, rename or delete anything there. |

**Network rules, whatever the mode:**
- never loopback or link-local;
- never listening (an endpoint service is handed its connections);
- never another unix socket or pipe.

**Approval**
- A version whose `[sandbox]` requests anything cannot be enabled, canaried, pinned or promoted until an operator
  approves its exact requests. The approval is recorded by digest and audited.
- A new version needs its own approval. A version that requests nothing needs none.

**Placement.** Every node reports, per capability, whether its backend enforces it: `enforced`, `cooperative` or
`unavailable` (and `endpoints`: whether its agent hands out service endpoints; a module with an endpoint service needs
it). A module runs only where all of its grants, and the always-on rules above, are enforced. A node that
cannot enforce them advertises `SANDBOX_BACKEND_MISSING` or `CAPABILITY_NOT_ENFORCED` for that module.

## Folders

A runner that works on the user's own files on a node, or leaves results there, asks for folders by id. Nothing about
where they are is in the module: the operator maps each id to a path per node, and the node checks it.

- **Approval.** Folders are part of the requests an operator approves per version. The approval names each folder and
  its access; an outbox's approval warns that files the module creates may replace files of the same name there (give
  it an empty, dedicated directory).
- **Mapping.** The operator's folder registry maps an id to an absolute path on each node, with an access that must
  equal the module's. In signing mode a node accepts its mapping only in a statement signed by the owner's release
  key, so a compromised coordinator cannot point an approved folder at another directory.
- **On the node.** The agent grants the folder's canonical path (symlinks resolved) and refuses a mapping that is a
  filesystem root, a home directory itself, inside the agent's or the coordinator's data, a system directory, or that
  overlaps another grant or another folder. It reports each folder `ok` or why not; a job of a module that needs a
  folder the node does not provide waits with `FOLDER_UNAVAILABLE`.
- **Links.** Every backend checks a link's target, so a symlink inside an input folder that points elsewhere leads
  nowhere, and a runner cannot create a symlink or a hard link in an outbox.
- **Execution.** Nothing in a folder may be executed, except on Windows, where any readable file can be (as for tools).
- **Enforcement.** Nodes report `folders.read` and `folders.write` in `sandbox.enforcement`; a module that requests
  folders runs only where both of its kinds are enforced.

## Bootstrap jobs

A job of a bootstrap stage ([manifest.md](manifest.md#pinned-datasets), `stages[].bootstrap`) may run on a node
before the module's goldens pass there, so it gets less than any other job of its module:

| Area | A bootstrap job |
|---|---|
| its bundle and runtime | read, execute |
| work directory (and the private temporary directory inside it) | read, write |
| data directory | none: `OARBANK_MODULE_DATA` is not set, and nothing it fetched stays on the node |
| network | the module's `egress-allowlist`, through the agent's proxy; none when the module's mode is `none` (a module with a bootstrap stage never requests `egress-any`) |
| host tools, folders, GPU, the container broker, executing written files | none (`OARBANK_TOOLS_FILE` and `OARBANK_FOLDERS_FILE` list none) |
| module settings | an empty object in `OARBANK_SETTINGS_FILE` |
| secrets | none (a bootstrap stage lists none; no `OARBANK_SECRETS_FILE`) |

`SandboxSection.for_bootstrap()` returns these grants. The agent takes the bootstrap flag from the module's entry in
the signed release, never from the grant. A node whose agent applies them reports `grants.bootstrap` as `enforced`;
bootstrap jobs run only there (`CAPABILITY_NOT_ENFORCED` elsewhere).

## Containers: the agent's broker

A runner never talks to Docker or Podman itself. The agent owns a container runtime that mounts only its own work and
module-data directories:

| Platform | Runtime |
|---|---|
| macOS | Colima, or Apple `container` |
| Linux | rootless Podman, or Docker Engine |
| Windows | an agent-owned WSL containers (WSLc) session: a VM of its own, WSL 2.9.3 or later |

Each job whose module may run containers gets an endpoint, `OARBANK_BROKER` (`unix:/path` or `npipe://./pipe/<name>`),
the only IPC its confinement allows. Requests are one JSON object per line, one request per connection. Use
`oarbank_sdk.broker`.

| Request | Fields | Answer |
|---|---|---|
| `container.run` | `image` (an approved reference), `platform`, `args[]`, `entrypoint?`, `mounts[] {src, dst, ro}`, `env{}`, `workdir?`, `network` (only with an egress grant), `timeout_s`, `cpus?`, `mem_gb?`, `gpus?` (`"none"` or `"all"`; core 2.5) | `{ok, exit_code, stdout_tail, stderr_tail, stdout_path, stderr_path, duration_s}`; the full output is written into the work directory |
| `container.pull` | `image`, `platform` | `{ok}` once the image is present |
| `status` | | `{ok, running, images[], gpus}`; `gpus` is `"all"` where the node passes GPUs through to containers, else `"none"` |
| any refusal | | `{ok: false, error, detail}`: `image_not_approved`, `bad_mount`, `network_not_granted`, `gpu_not_granted`, `gpu_unavailable`, `registry_unavailable` (retryable), `runtime_unavailable`, `platform_unavailable` |

**Mounts**
- Mount sources are PortablePaths inside the work directory, or `data:<path>` inside the data directory.
- Destinations are POSIX absolute paths, for `linux/*` images.

**Platforms.** A runtime runs the platforms it reports (`containers.platforms` on Windows); others are
`platform_unavailable`. Linux adds a foreign platform where binfmt has a handler for it, macOS runs `linux/amd64` under
Rosetta, Windows runs its own architecture only (WSLc has no emulation).

**The agent's container run**
- Always: `run --rm`, the platform, the CPU and memory limits within the job's reservation, the validated mounts and
  the job's ownership labels.
- Never: privileged mode, host networking, or another socket.

**Ownership and filesystem behaviour**
- Files a container writes belong to the job's identity: rootless mode, or a user namespace, on Linux.
- Exec bits and case sensitivity follow the host filesystem.
- Containers die with their job.

## Image sets

Suites with one image per task (agent attempts, test suites, benchmark tasks) cannot list every digest. A
`[[sandbox.container_sets]]` entry approves images by **who signed them** instead:

| Key | Meaning |
|---|---|
| `name` | The set's name, unique; shown at approval and in the audit. |
| `registry` | The registry, `host[:port]`, lowercase (`docker.io` for Docker Hub). |
| `repository` | A repository path. Ending with `/` it is a prefix: every repository below it; otherwise exactly that repository. |
| `platform` | The images' OCI platform. |
| `key` | Bundle path of the cosign public key: one ECDSA P-256 key, PEM `PUBLIC KEY` (`cosign generate-key-pair` writes `cosign.pub`). |
| `index` | Optional: a tagged reference of a signed image index; only the digests it lists are members. |

**Membership.** A reference `<registry>/<repository>[:tag]@sha256:<hex>` (normalized as Docker does: `docker.io`,
`library/`) belongs to a set when its registry and platform equal the set's and its repository is under the prefix, and:
- **without `index`:** its digest carries a cosign signature by the key, in either format cosign writes: a Sigstore bundle
  attached as an OCI referrer (cosign 3, and 2.4+ with `--new-bundle-format`: a DSSE envelope over an in-toto statement
  with predicate `https://sigstore.dev/cosign/sign/v1` naming the digest), or the simple-signing manifest at tag
  `sha256-<hex>.sig` (cosign 2). The key alone verifies it: no transparency log is consulted;
- **with `index`:** the set's index lists the digest. The index is an OCI artifact at the `index` reference (artifact
  type `application/vnd.oarbank.image-set.v1+json`, one layer of that type holding `{"type": "oarbank.image-set/v1",
  "registry", "repository", "seq", "images": ["sha256:…"]}`) whose own digest carries a signature by the key. A node
  refuses an index whose `seq` is lower than one it already accepted for the set. `oarbank_sdk.images.index_document()`
  writes the layer; `oras push` and `cosign sign --key` publish it.

**Digest pinning stays mandatory.** `container.run`, `container.pull` and `jobs.enqueue` name images by digest. A job
runs a set's images only if it lists them (`jobs.enqueue` items' `images`, core 2.5); the coordinator refuses an image
outside every approved list and set with 422 `image_not_approved`, and the broker refuses a set image the job did not
list. The agent verifies membership **before pulling** (the runtime then pulls by digest), audits each digest's first
run, and refuses anything else with `image_not_approved` and the reason in `detail`.

**Approval** shows the prefix and the key's SHA-256 fingerprint, never a list of digests: new images in an approved set
need no new module version. A new key, prefix or index needs one.

`oarbank_sdk.images` is the reference policy (stdlib only, ECDSA included); the agent's Rust implementation agrees with
it on `spec/vectors/image-signatures.json`. `oarbank_sdk.imagetest` builds signed test images in an OCI image layout,
without cosign or a registry.

## GPU passthrough

`container.run` `gpus = "all"` gives the container every GPU of the node; a count comes in a later minor (an integer is
`bad_request` today). A stage that runs GPU containers reserves the agent's **`gpu` pool** besides `containers`
(`pools = {containers = 1, gpu = 1}`), and the runner declares `gpu.in_container = true` with `gpu.use` `shared` or
`exclusive`, so the job holds a container token and the GPU for its lifetime, and the host's GPU admission (the owner's
`gpu_jobs` policy, protected processes using the GPU) applies to it. The broker refuses `gpus = "all"` with
`gpu_not_granted` for a job that did not reserve the `gpu` pool.

| Platform | Mechanism | The node |
|---|---|---|
| Linux | CDI: a CDI spec (`/etc/cdi`, `/var/run/cdi`) with an `all` device, such as `nvidia-ctk cdi generate` writes; the run gets `--device <kind>=all` (Podman 4.1+, Docker 25+ with CDI) | offers the `gpu` pool; facts `containers.gpu = "cdi:<kind>"` |
| Windows | GPU-PV in the agent's WSLc session: its guest writes a CDI spec (`microsoft.com/wslc=gpu`: `/dev/dxg` and the host driver's libraries under `/usr/lib/wsl`, the same edits `nvidia-ctk cdi generate --mode=wsl` makes, for every vendor); the run gets `--gpus all` | offers the `gpu` pool when the session's VM has a GPU; facts `containers.gpu = "cdi:microsoft.com/wslc"` |
| macOS | none: Colima and Apple `container` VMs have no Metal passthrough | `containers.gpu = "undetected"`, no `gpu` pool |

**GPU APIs in containers.** The node's facts `containers.gpu_apis` list the GPU APIs a `gpus = "all"` container can use
there, in `runner.gpu.apis_any` tokens: `cuda`, `rocm` and `levelzero` from a Linux CDI kind (`nvidia.com/gpu`,
`amd.com/gpu`, `intel.com/gpu`); on Windows `directml` (D3D12 and DXCore come with WSL) and `cuda` where the host's
NVIDIA driver provides its WSL library. Runtimes that come in the image (ROCm or Level Zero on WSL, Mesa's Vulkan) are
not listed. Empty wherever `containers.gpu` is `undetected`. On Windows, GPU containers need a glibc image (the guest's
hook runs the image's `ldconfig`).

## Testing your module

`oarbank-sdk conform` runs the runner suite under this host's backend with the grants the manifest declares (a bootstrap
stage's runner specs with the bootstrap grants), plus a portability lint for every declared platform. A module that writes outside its directories, reads homes, or expects a
tool it didn't request fails there first.
