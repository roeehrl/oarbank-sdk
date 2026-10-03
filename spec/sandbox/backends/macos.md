# Sandbox backend: macOS (Seatbelt)

This page is the macOS enforcement of the [sandbox contract](../../sandbox.md). It is informative for module authors
and normative for agent implementers.

- **Profile generation.** `oarbank_sdk.sandbox` renders a deny-default SBPL profile per process. The text depends only
  on the policy's shape, never on paths; the golden shapes are in [macos-golden/](macos-golden/). Every path enters as
  a parameter (`sandbox_init_with_parameters`) after `realpath`, and the symlink hops on the way get metadata rules.
- **Launch.** A launcher applies the profile to itself and then execs the module. It execs only after `sandbox_init`
  succeeds; otherwise it exits 70. Exit 71 means the exec failed, exit 64 is a usage error.
- **Verification.** The parent verifies with `sandbox_check(pid)`.

## Mapping

| Contract element | Seatbelt |
|---|---|
| read and execute the bundle, runtime and tools | `file-read* file-map-executable process-exec (subpath RO_i)`, plus metadata on the path ancestors |
| work, data and temporary directories | `file-read* file-write* (subpath RW_i)`; with `exec_writable`, also `file-map-executable process-exec` |
| base system | `/System`, `/usr/lib`, `/usr/share`, `/bin`, `/usr/bin`, `/usr/libexec`, `/Library/Apple` (Rosetta), timezone, `/etc/hosts`, `/etc/resolv.conf`, `/etc/ssl`, `/dev/{null,zero,random,urandom,fd,dtracehelper}` |
| no local IPC except the broker | no `network*` rule for unix sockets except `(remote unix-socket (path-literal BROKER_SOCKET))`; `(deny mach-lookup (xpc-service-name-prefix ""))` |
| `net = egress-allowlist` | only `(remote ip "localhost:<proxy port>")`: the agent's proxy enforces the hosts |
| `net = egress-any` | `(remote ip "*:*")` and the mDNSResponder socket, then `(deny network-outbound (remote ip "localhost:*"))` |
| `devices.gpu = compute` | IOKit AGX user clients and `com.apple.MTLCompilerService` |
| children | the sandbox is inherited across fork and exec, and cannot be applied again |

## Enforcement report

| Capability | Status |
|---|---|
| filesystem, IPC, `net.none`, `net.egress-allowlist`, `net.egress-any`, no loopback, `exec_writable` deny, GPU, children | enforced |
| no link-local under `egress-any` | unavailable: Seatbelt's IP filter knows only `*` and `localhost` |
| hiding which files exist | not promised: metadata on path ancestors is readable |

Seatbelt is deprecated, but Apple has announced no removal date. The macOS 27 Endpoint Security client for descendant
processes is a planned second layer.
