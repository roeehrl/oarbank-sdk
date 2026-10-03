"""The module sandbox (spec/sandbox.md): the OS-neutral policy, and its macOS backend (Seatbelt).

A module process may read its own bundle (and the interpreter it runs on), read and write its own data directory
and, for a job, its private work directory, and nothing else on the host: not the user's home, not other modules,
not the coordinator's or the agent's state. Network, host tool paths, the GPU and the container broker are
grants: declared in the manifest's `[sandbox]` section and approved by an operator for that module version.

`Policy` is the backend-neutral description of one process's grants. The macOS backend below renders it as a Seatbelt
profile (spec/sandbox/backends/macos.md; golden shapes in spec/sandbox/golden/). Every path enters the profile as a parameter
(`sandbox_init_with_parameters`), never as text, after `realpath`: the kernel matches resolved paths.

    text, params = render(Policy(module="dev.example.toy", ro=[bundle, interpreter_home], rw=[data, work]))
    argv = launch_argv(profile_path, params, ["/abs/python", "-I", "runner.py", ...])
"""
import ctypes
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_VERSION = 2
BACKEND = "seatbelt"
LAUNCHER = Path(__file__).with_name("_sandbox_launch.py")

_HEAD = """(version 1)
;; oarbank module sandbox, profile {version} ({kind})
(deny default (with message (param "MODULE_ID")))

;; process
(allow process-fork)
(allow process-exec (subpath "/bin") (subpath "/usr/bin") (subpath "/usr/libexec"))
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow ipc-posix-sem)
(allow ipc-posix-shm-read-data ipc-posix-shm-read-metadata (ipc-posix-name "apple.shm.notification_center"))
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.notification_center")
  (global-name "com.apple.logd")
  (global-name "com.apple.system.logger"))

;; read-only system
(allow file-read* (literal "/"))
(allow file-read-metadata (literal "/tmp") (literal "/var") (literal "/etc") (literal "/private/var/select/sh"))
(allow file-read* file-map-executable
  (subpath "/System") (subpath "/usr/lib") (subpath "/usr/share") (subpath "/Library/Apple")
  (subpath "/private/var/db/timezone") (subpath "/bin") (subpath "/usr/bin") (subpath "/usr/libexec")
  (literal "/private/etc/hosts") (literal "/private/etc/resolv.conf") (literal "/private/var/run/resolv.conf")
  (literal "/private/var/select/sh") (literal "/private/etc/services") (literal "/private/etc/protocols")
  (literal "/private/etc/localtime") (subpath "/private/etc/ssl")
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom")
  (literal "/dev/dtracehelper") (subpath "/dev/fd"))
(allow file-write-data (literal "/dev/null") (literal "/dev/zero") (subpath "/dev/fd"))
(allow file-ioctl (literal "/dev/dtracehelper"))
"""

_RO = """(allow file-read* file-map-executable process-exec (subpath (param "RO_{i}")))
(allow file-read-metadata (path-ancestors (param "RO_{i}")))
"""
_RW = """(allow file-read* file-write* (subpath (param "RW_{i}")))
(allow file-read-metadata (path-ancestors (param "RW_{i}")))
"""
_LINK = """(allow file-read-metadata (literal (param "LINK_{i}")))
"""
_EGRESS_ANY = """
;; grant: network egress-any (public addresses and DNS; never unix sockets, loopback or listening)
(allow system-socket)
(allow network-outbound (remote ip "*:*"))
(allow network-outbound (remote unix-socket (path-literal "/private/var/run/mDNSResponder")))
(allow mach-lookup (global-name "com.apple.dnssd.service"))
"""
_EGRESS_PROXY = """
;; grant: network egress-allowlist (only the agent's local proxy, which enforces the allowed hosts)
(allow system-socket)
(allow network-outbound (remote ip "localhost:{port}"))
"""
_BROKER = """
;; grant: the agent's container broker (this job's socket only)
(allow system-socket (socket-domain AF_UNIX))
(allow network-outbound (remote unix-socket (path-literal (param "BROKER_SOCKET"))))
"""
_HARDEN = """
;; hardening (last match wins: keep after every allow above)
(deny mach-lookup (xpc-service-name-prefix ""))
(deny system-fcntl (fcntl-command 80 110))
"""
_NO_LOOPBACK = """(deny network-outbound (remote ip "localhost:*"))
"""
_RW_EXEC = """(allow file-map-executable process-exec (subpath (param "RW_{i}")))
"""
NET_MODES = ("none", "egress-allowlist", "egress-any")
# what this backend enforces, per capability (spec/platforms.md, "What each OS enforces")
ENFORCEMENT = {"fs": "enforced", "ipc": "enforced", "net.none": "enforced", "net.egress-allowlist": "enforced",
               "net.egress-any": "enforced", "net.no-loopback": "enforced", "net.no-link-local": "unavailable",
               "exec_writable.deny": "enforced", "devices.gpu": "enforced", "children": "enforced"}
_GPU = """
;; grant: GPU (Metal)
(allow iokit-open-service (iokit-registry-entry-class "IOAccelerator" "AGXAccelerator"))
(allow iokit-open-user-client (iokit-user-client-class "AGXDeviceUserClient" "IOAccelerationUserClient" "IOSurfaceRootUserClient"))
(allow iokit-get-properties)
(allow mach-lookup (global-name "com.apple.MTLCompilerService") (xpc-service-name "com.apple.MTLCompilerService"))
"""


class SandboxError(RuntimeError):
    pass


@dataclass
class Policy:
    """What one module process may touch. `ro`: read, map and exec (bundle, interpreter, approved host paths);
    `rw`: read and write (data dir, job work dir, tmp). The kind only labels the profile."""
    module: str
    ro: list = field(default_factory=list)
    rw: list = field(default_factory=list)
    net: str = "none"                            # none | egress-allowlist (through proxy_port) | egress-any
    proxy_port: int | None = None
    broker_socket: str | None = None
    gpu: bool = False
    exec_rw: bool = False                        # exec_writable: the rw roots may hold executables
    kind: str = "runner"
    exe: str | None = None                       # argv[0]: its symlink hops need metadata rules too


def _real(p) -> str:
    r = os.path.realpath(str(p))
    if not r.startswith("/") or "\n" in r or "\0" in r:
        raise SandboxError(f"bad sandbox path {p!r}")
    return r


def links_of(p, _depth: int = 0) -> list[str]:
    """Every symlink met while resolving a path, hop by hop (each needs a metadata rule on the link itself)."""
    if _depth > 32:
        raise SandboxError(f"symlink loop resolving {p}")
    out, cur = [], Path("/")
    parts = Path(os.path.abspath(str(p))).parts[1:]
    for i, part in enumerate(parts):
        nxt = cur / part
        if nxt.is_symlink():
            out.append(str(nxt))
            target = Path(os.readlink(nxt))
            target = target if target.is_absolute() else cur / target
            rest = Path(*parts[i + 1:]) if i + 1 < len(parts) else None
            return out + links_of(target / rest if rest else target, _depth + 1)
        cur = nxt
    return out


def render_text(kind: str, n_ro: int, n_rw: int, n_links: int, net: str, broker: bool, gpu: bool,
                proxy_port: int | None = None, exec_rw: bool = False) -> str:
    """The profile text for a policy shape (spec/sandbox/golden pins it)."""
    if net not in NET_MODES:
        raise SandboxError(f"network mode {net!r} is not enforceable by the {BACKEND} backend")
    if net == "egress-allowlist" and not proxy_port:
        raise SandboxError("egress-allowlist needs the agent's proxy port")
    parts = [_HEAD.format(version=PROFILE_VERSION, kind=kind), "\n;; module grants\n"]
    parts += [_RO.format(i=i) for i in range(n_ro)]
    for i in range(n_rw):
        parts.append(_RW.format(i=i))
        if exec_rw:
            parts.append(_RW_EXEC.format(i=i))
    parts += [_LINK.format(i=i) for i in range(n_links)]
    if net == "egress-any":
        parts.append(_EGRESS_ANY)
    elif net == "egress-allowlist":
        parts.append(_EGRESS_PROXY.format(port=int(proxy_port)))
    if broker:
        parts.append(_BROKER)
    parts.append(_HARDEN)
    if net == "egress-any":
        parts.append(_NO_LOOPBACK)               # after the egress allow: loopback is never reachable
    if gpu:
        parts.append(_GPU)                       # after the hardening deny: its XPC allow must win
    return "".join(parts)


def render(policy: Policy) -> tuple[str, list[tuple[str, str]]]:
    """The profile text and its parameters, every path realpath'd. The text depends only on the counts and flags."""
    ro = list(dict.fromkeys(_real(p) for p in policy.ro))
    rw = list(dict.fromkeys(_real(p) for p in policy.rw))
    links = list(dict.fromkeys(link for p in list(policy.ro) + list(policy.rw) + ([policy.exe] if policy.exe else [])
                               for link in links_of(p)))
    params = [("MODULE_ID", policy.module)]
    params += [(f"RO_{i}", p) for i, p in enumerate(ro)]
    params += [(f"RW_{i}", p) for i, p in enumerate(rw)]
    params += [(f"LINK_{i}", p) for i, p in enumerate(links)]
    if policy.broker_socket:
        b = Path(policy.broker_socket)
        params.append(("BROKER_SOCKET", _real(b.parent) + "/" + b.name))
    text = render_text(policy.kind, len(ro), len(rw), len(links), policy.net, bool(policy.broker_socket), policy.gpu,
                       policy.proxy_port, policy.exec_rw)
    return text, params


def interpreter_roots(python: str | None = None) -> list[str]:
    """What a Python process needs to read to start and import: the interpreter's home, the venv, and every import
    root (editable installs included). For `python` other than this one, its venv and resolved home only."""
    import sysconfig
    if python is None or os.path.realpath(python) == os.path.realpath(sys.executable):
        roots = [sys.base_prefix, sys.prefix, sysconfig.get_paths()["stdlib"]] + [p for p in sys.path if p and os.path.isdir(p)]
    else:
        venv = Path(python).parent.parent
        real = Path(os.path.realpath(python))
        roots = [str(venv), str(real.parent.parent)]
    out = []
    for r in roots:
        rr = os.path.realpath(r)
        if not any(rr == o or rr.startswith(o + "/") for o in out):
            out = [o for o in out if not o.startswith(rr + "/")] + [rr]
    return out


def node_policy(module: str, bundle, work, data, python: str | None = None, sandbox=None, broker_socket: str | None = None,
                kind: str = "runner", tool_paths: list | None = None, proxy_port: int | None = None) -> Policy:
    """A node-side process (runner, doctor, service, probe): its bundle and interpreter read-only, the job's work dir
    and the module's data dir read-write, plus the approved grants of `sandbox` (a manifest SandboxSection).
    `tool_paths`: the host's paths for the approved tool ids (the operator's tool registry resolves them per OS)."""
    ro = [str(bundle), *interpreter_roots(python), *(tool_paths or [])]
    rw = [str(p) for p in (work, data) if p]
    net = sandbox.net.mode if sandbox else "none"
    return Policy(module=module, ro=ro, rw=rw, net=net, proxy_port=proxy_port if net == "egress-allowlist" else None,
                  broker_socket=broker_socket if sandbox and sandbox.containers else None,
                  gpu=bool(sandbox and sandbox.devices.gpu != "none"), kind=kind, exe=python or sys.executable,
                  exec_rw=bool(sandbox and sandbox.exec_writable))


def write_profile(text: str, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(tmp, 0o444)
    tmp.replace(path)
    return path


def launch_argv(profile_path, params: list[tuple[str, str]], argv: list[str], python: str | None = None) -> list[str]:
    """argv that applies the profile to a launcher process, then execs `argv` (absolute argv[0]) inside it."""
    if not argv or not argv[0].startswith("/"):
        raise SandboxError(f"sandboxed argv needs an absolute executable, got {argv[:1]}")
    return [python or sys.executable, "-I", str(LAUNCHER), str(profile_path), *[f"{k}={v}" for k, v in params], "--", *argv]


def supported() -> bool:
    return sys.platform == "darwin"


def is_sandboxed(pid: int) -> bool:
    """True when the process runs under a sandbox (`sandbox_check(pid, NULL, 0)`)."""
    if not supported():
        return False
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    f = lib.sandbox_check
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    return f(int(pid), None, 0) == 1
