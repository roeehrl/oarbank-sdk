# Sandbox backend: Windows (AppContainer and Job Objects)

This page is the Windows enforcement of the [sandbox contract](../../sandbox.md). It is informative for module
authors and normative for agent implementers. It needs Windows 10 1809 or later.

- **Launch.** Windows has no exec. `oarbank-agent sandbox-exec POLICY.json -- argv` is a shim, already in the attempt's
  Job Object, that starts the module in an AppContainer and waits for it, passing its exit code on. It exits 70 when
  the container cannot be set up and 71 when the module cannot start.
- **Identity.** One AppContainer profile per module, `Oarbank.<module id>`. Its SID is granted read and execute on the
  policy's read-only roots and full access on its read-write roots.
- **Verification.** The parent checks that the shim's children run with an AppContainer token.
- **Process container.** The Job Object, killed with the agent for attempts; a job memory limit and a CPU rate cap
  when the node's policy turns reservations into hard limits.

## Mapping

| Contract element | Windows |
|---|---|
| read and execute the bundle, runtime and tools | an inheritable allow entry for the container's SID; system directories are readable by every AppContainer already |
| work, data and temporary directories | an inheritable full-access entry for the container's SID |
| no local IPC except the broker | AppContainer object isolation: named pipes, sections and other objects outside the container are denied |
| `net = none` | no network capability |
| `net = egress-allowlist` | the elevated helper (a LocalSystem service) exempts the container from loopback isolation for the job, and Windows Filtering Platform filters block every loopback port but the agent's proxy |
| `net = egress-any` | the `internetClient` capability; loopback isolation keeps it off loopback |
| children | children of an AppContainer process stay in it, and in the Job Object |

## Enforcement report

| Capability | Status |
|---|---|
| filesystem, IPC, `net.none`, `net.egress-any`, no loopback, GPU | enforced |
| `net.egress-allowlist` | enforced where the elevated helper runs; unavailable otherwise |
| no link-local under `egress-any` | unavailable |
| `exec_writable` deny | unavailable: any readable binary is executable without application control |

Some Windows builds (seen on Windows Server 2025) refuse an AppContainer the null device: opening `NUL`
(`os.devnull`, `subprocess.DEVNULL`) fails with access denied. The agent gives every module process standard handles
it opened itself, so a module that starts a child passes those on (Python's `subprocess` does when `stdin`, `stdout` or
`stderr` is left as it is) or a file in its own directories, never a new `NUL`.
| containers | unavailable until the agent-owned WSL2 distribution ships |
