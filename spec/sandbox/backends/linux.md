# Sandbox backend: Linux (Landlock and seccomp)

This page is the Linux enforcement of the [sandbox contract](../../sandbox.md). It is informative for module authors
and normative for agent implementers. Full parity needs Linux 6.12 or later (Landlock ABI 6); older kernels report
what they cannot enforce as `unavailable`, and work that needs it is not placed there.

- **Launch.** `oarbank-agent sandbox-exec POLICY.json -- argv` sets `no_new_privs`, applies a Landlock ruleset for
  the kernel's ABI and then a seccomp filter, and execs the module. If any step cannot be applied in full it exits
  70 (71: the exec failed; 64: a usage error). The policy is the SDK's `Policy` as JSON.
- **Verification.** The parent reads `/proc/<pid>/status`: `NoNewPrivs: 1` and `Seccomp: 2`, which only the launcher
  sets before the exec.
- **Process container.** The process group, and a cgroup v2 leaf where systemd delegated the agent's cgroup
  (`cgroup.kill`, `cgroup.freeze`; `cpu.max` and `memory.max` when the node's policy turns reservations into hard
  limits).

## Mapping

| Contract element | Linux |
|---|---|
| read and execute the bundle, runtime and tools | Landlock `path_beneath` with the read and execute rights |
| work, data and temporary directories | Landlock with every file right; execute only with `exec_writable` |
| base system | read and execute `/usr`, `/lib`, `/lib32`, `/lib64`, `/bin`, `/sbin`, `/etc`, `/opt`, `/proc`, `/sys/devices/system/cpu`; read and write `/dev/{null,zero,random,urandom,full}` |
| no local IPC except the broker | seccomp refuses `socket(AF_UNIX)` without a broker grant (`socketpair` stays: event loops need it) and every family other than unix, IPv4 and IPv6; Landlock ABI 6 scopes abstract unix sockets and signals to the sandbox |
| `net = none` | seccomp refuses IPv4 and IPv6 sockets |
| `net = egress-allowlist` | IPv4 and IPv6 streams only (seccomp); Landlock ABI 4 allows TCP connect to the agent's proxy port alone and no bind |
| `net = egress-any` | unavailable: Landlock filters ports, not addresses, so loopback and link-local cannot be refused |
| never listening | seccomp refuses `listen` |
| `devices.gpu = compute` | read and write `/dev/dri`, `/dev/nvidia*`, `/dev/kfd` |
| children | Landlock and seccomp are inherited across fork and exec; seccomp refuses `unshare`, `setns`, `mount`, namespace flags on `clone` (and `clone3`, which answers ENOSYS so libc falls back), `ptrace`, `bpf`, keyrings and kernel modules |

## Enforcement report

| Capability | Status |
|---|---|
| filesystem, `net.none`, `exec_writable` deny, GPU | enforced (Landlock ABI 3+) |
| `net.egress-allowlist`, no loopback, no link-local | enforced with Landlock ABI 4+ (Linux 6.7) |
| IPC | enforced with Landlock ABI 6+ (Linux 6.12) |
| `net.egress-any` | unavailable |
| hiding which files exist | not promised: Landlock does not restrict `stat` |
