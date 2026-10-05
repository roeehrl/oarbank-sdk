# Sandbox backend: Windows (AppContainer and Job Objects)

This page is the Windows enforcement of the [sandbox contract](../../sandbox.md). It is informative for module
authors and normative for agent implementers. It needs Windows 10 1809 or later.

- **Launch.** Windows has no exec. `oarbank-agent sandbox-exec POLICY.json -- argv` is a shim, already in the attempt's
  Job Object, that starts the module in an AppContainer and waits for it, passing its exit code on. It exits 70 when
  the container cannot be set up and 71 when the module cannot start.
- **Identity.** One AppContainer profile per module and role: `Oarbank.<module id>` for a node's processes (runners,
  doctors, services, probes, dependency installs: policy kinds other than those below), `Oarbank.coordinator.<module
  id>` for the coordinator's (kinds `coordinator` and `coordinator-install`) and `Oarbank.cli.<module id>` for a module CLI (kind `cli`). A container's named objects live in one directory
  per session that the account starting it first owns, so two accounts never share one (the agent's and the
  coordinator's services both run in session 0). Its SID is granted read and execute on the policy's read-only roots
  and full access on its read-write roots.
- **Verification.** The parent checks that the shim's children run with an AppContainer token.
- **Process container.** The Job Object, killed with the agent for attempts; a job memory limit and a CPU rate cap
  when the node's policy turns reservations into hard limits.

## Mapping

| Contract element | Windows |
|---|---|
| read and execute the bundle, runtime and tools | an inheritable allow entry for the container's SID; system directories are readable by every AppContainer already |
| work, data and temporary directories | an inheritable full-access entry for the container's SID; the AppContainer start points `LOCALAPPDATA`, `TEMP` and `TMP` at `<LOCALAPPDATA>\Packages\<container>\AC` under the home's `LOCALAPPDATA` (a job's work directory), which the launcher creates first ([platforms.md](../../platforms.md#environment-per-os)) |
| a runner's input folders | an inheritable `FILE_GENERIC_READ` entry for a capability SID only runner tokens of the module carry (`DeriveCapabilitySidsFromName("oarbank.runner.<module>")`); doctor, services and probes run without it |
| a runner's outboxes | an inheritable entry for the same capability SID: `FILE_ADD_FILE`, `FILE_ADD_SUBDIRECTORY` (write data and append data on the files created), `FILE_WRITE_EA`, `FILE_WRITE_ATTRIBUTES`, `SYNCHRONIZE`; no `FILE_LIST_DIRECTORY`/`FILE_READ_DATA`, `DELETE`, `FILE_DELETE_CHILD`, `READ_CONTROL` or `WRITE_DAC`. An AppContainer token holds no symbolic-link privilege. The agent removes entries no accepted folder statement or release grants any more, and at start |
| no local IPC except the broker | AppContainer object isolation: named pipes, sections and other objects outside the container are denied |
| the job's broker | a named pipe (`OARBANK_BROKER=npipe://./pipe/<name>`, a random name created exclusively) whose DACL allows only the agent's account and the module's AppContainer, with a low mandatory label so the runner's token may write; remote clients are refused |
| `net = none` | no network capability |
| `net = egress-allowlist` | the elevated helper (a LocalSystem service) exempts the container from loopback isolation, and Windows Filtering Platform filters block its loopback connections except to the agent's proxy port, for as long as the job's launcher runs (however the job ends) |
| `net = egress-any` | the `internetClient` capability; loopback isolation keeps it off loopback |
| children | children of an AppContainer process stay in it, and in the Job Object |

## Enforcement report

| Capability | Status |
|---|---|
| filesystem, IPC, `net.none`, `net.egress-any`, no loopback, GPU | enforced |
| `net.egress-allowlist` | enforced where the elevated helper runs; unavailable otherwise |
| no link-local under `egress-any` | unavailable |
| `exec_writable` deny | unavailable: any readable binary is executable without application control |
| `folders.read`, `folders.write` | enforced (a read folder's binaries are executable, as above) |
| containers | the agent's WSL containers session: enforced once it is ready (WSL 2.9.3 or later and the Virtual Machine Platform; the node's facts `containers` say what is missing otherwise) |

Some Windows builds (seen on Windows Server 2025) refuse an AppContainer the null device: opening `NUL`
(`os.devnull`, `subprocess.DEVNULL`) fails with access denied. The agent gives every module process standard handles
it opened itself, so a module that starts a child passes those on (Python's `subprocess` does when `stdin`, `stdout` or
`stderr` is left as it is) or a file in its own directories, never a new `NUL`. uv starts an environment's
interpreter that way to learn what it is, so a host installing a module's dependencies records the interpreter in
the install's uv cache first, outside the sandbox ([bundles.md](../../bundles.md), "Install on a host").
