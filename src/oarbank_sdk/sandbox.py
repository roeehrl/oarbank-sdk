"""The module sandbox (spec/sandbox.md): the OS-neutral policy, and its macOS backend (Seatbelt).

A module process may read its own bundle (and the interpreter it runs on), read and write its own data directory
and, for a job, its private work directory, and nothing else on the host: not the user's home, not other modules,
not the coordinator's or the agent's state. Network, host tool paths, the GPU and the container broker are
grants: declared in the manifest's `[sandbox]` section and approved by an operator for that module version. A runner may
also get folders (`[sandbox].folders`): read-only input folders, and write-only outboxes it can create files in but never
read, list, rename or delete.

`Policy` is the backend-neutral description of one process's grants. The macOS backend below renders it as a Seatbelt
profile (spec/sandbox/backends/macos.md; golden shapes in spec/sandbox/golden/). Every path enters the profile as a parameter
(`sandbox_init_with_parameters`), never as text, after `realpath`: the kernel matches resolved paths. Each symlink met
on the way gets metadata (lstat, readlink) on itself and the directories above it, which `realpath` walks.

    text, params = render(Policy(module="dev.example.toy", ro=[bundle, interpreter_home], rw=[data, work]))
    argv = launch_argv(profile_path, params, ["/abs/python", "-I", "runner.py", ...])
"""
import ctypes
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_VERSION = 4
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
_RD = """(allow file-read* (subpath (param "RD_{i}")))
(allow file-read-metadata (path-ancestors (param "RD_{i}")))
"""
# an outbox: create regular files and directories (never links) and write them; no reading, listing, unlink or rename
_WO = """(allow file-read-metadata (subpath (param "WO_{i}")) (path-ancestors (param "WO_{i}")))
(allow file-write-create (require-all (subpath (param "WO_{i}")) (vnode-type REGULAR-FILE DIRECTORY)))
(allow file-write-data (subpath (param "WO_{i}")))
"""
_LINK = """(allow file-read-metadata (literal (param "LINK_{i}")) (path-ancestors (param "LINK_{i}")))
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
(allow iokit-open-service (iokit-registry-entry-class "IOAccelerator"))
(allow iokit-open-user-client (iokit-user-client-class "IOGPUDeviceUserClient" "AppleParavirtDeviceUserClient" "IOAccelerationUserClient" "IOSurfaceRootUserClient"))
(allow iokit-get-properties)
(allow mach-lookup (global-name "com.apple.MTLCompilerService") (xpc-service-name "com.apple.MTLCompilerService"))
"""


class SandboxError(RuntimeError):
    pass


@dataclass
class Policy:
    """What one module process may touch. `ro`: read, map and exec (bundle, interpreter, approved host paths);
    `rw`: read and write (data dir, job work dir, tmp); `rd`: read only, never execute (a runner's read folders); `wo`:
    create and write, never read, list, unlink or rename (a runner's outboxes). The kind only labels the profile."""
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
    rd: list = field(default_factory=list)
    wo: list = field(default_factory=list)


def _real(p) -> str:
    r = os.path.realpath(str(p))
    if not r.startswith("/") or "\n" in r or "\0" in r:
        raise SandboxError(f"bad sandbox path {p!r}")
    return r


def links_of(p, _depth: int = 0) -> list[str]:
    """Every symlink met while resolving a path, hop by hop (each needs a metadata rule on itself and its ancestors)."""
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
                proxy_port: int | None = None, exec_rw: bool = False, n_rd: int = 0, n_wo: int = 0) -> str:
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
    parts += [_RD.format(i=i) for i in range(n_rd)]
    parts += [_WO.format(i=i) for i in range(n_wo)]
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
    rd = list(dict.fromkeys(_real(p) for p in policy.rd))
    wo = list(dict.fromkeys(_real(p) for p in policy.wo))
    links = list(dict.fromkeys(link for p in [*policy.ro, *policy.rw, *policy.rd, *policy.wo, *([policy.exe] if policy.exe else [])]
                               for link in links_of(p)))
    params = [("MODULE_ID", policy.module)]
    params += [(f"RO_{i}", p) for i, p in enumerate(ro)]
    params += [(f"RW_{i}", p) for i, p in enumerate(rw)]
    params += [(f"RD_{i}", p) for i, p in enumerate(rd)]
    params += [(f"WO_{i}", p) for i, p in enumerate(wo)]
    params += [(f"LINK_{i}", p) for i, p in enumerate(links)]
    if policy.broker_socket:
        b = Path(policy.broker_socket)
        params.append(("BROKER_SOCKET", _real(b.parent) + "/" + b.name))
    text = render_text(policy.kind, len(ro), len(rw), len(links), policy.net, bool(policy.broker_socket), policy.gpu,
                       policy.proxy_port, policy.exec_rw, len(rd), len(wo))
    return text, params


# Prefixes many programs share: never granted whole, even when an interpreter's prefix is one of them (a system Python's
# is /usr); its own library directories and executable are granted instead.
SHARED_PREFIXES = ("/", "/usr", "/usr/local", "/opt/homebrew", "/opt/local", "/home/linuxbrew/.linuxbrew")
_LAYOUT = ("import json, sys, sysconfig, os; p = sysconfig.get_paths(); print(json.dumps({'base_prefix': sys.base_prefix, "
           "'prefix': sys.prefix, 'executable': os.path.realpath(sys.executable), 'paths': [p[k] for k in "
           "('stdlib', 'platstdlib', 'purelib', 'platlib')], 'names': [sys.executable, sys._base_executable]}))")


def interpreter_layout(python: str | None = None) -> dict:
    """Where an interpreter lives, as it reports itself: {base_prefix, prefix, executable (resolved), paths (stdlib,
    platstdlib, purelib, platlib), names (its executable and its base's, as it calls them)}, plus `extra`: this
    process's other import roots (editable installs included)."""
    import json
    import subprocess
    import sysconfig
    if python is None or os.path.abspath(python) == os.path.abspath(sys.executable):     # a venv shares its base's realpath
        paths = sysconfig.get_paths()
        return {"base_prefix": sys.base_prefix, "prefix": sys.prefix, "executable": os.path.realpath(sys.executable),
                "paths": [paths[k] for k in ("stdlib", "platstdlib", "purelib", "platlib")],
                "names": [sys.executable, sys._base_executable],
                "extra": [p for p in sys.path if p and os.path.isdir(p)]}
    out = subprocess.run([python, "-I", "-c", _LAYOUT], capture_output=True, text=True, timeout=60, check=True)
    return {**json.loads(out.stdout), "extra": []}


def interpreter_roots(python: str | None = None, layout: dict | None = None) -> list[str]:
    """What a Python process needs to read to start and import: its own prefix (a venv's, and the base interpreter's)
    unless that is a shared one such as /usr or /opt/homebrew, its library directories, its resolved executable, and
    (for this process) every import root. Never a shared parent of the interpreter.

    Also each of those paths, and the interpreter's own names for its executable, as the interpreter spells them when
    that goes through a symlink and lands inside a root above: the profile resolves them to that root, and their hops
    get the metadata rules realpath needs. A Homebrew CPython calls itself and its prefix by its opt/python@3.x path
    whichever way it was started, so those links must resolve inside the sandbox."""
    lay = layout or interpreter_layout(python)
    shared = {os.path.realpath(x) for x in SHARED_PREFIXES}
    roots = [x for x in (lay["base_prefix"], lay["prefix"]) if os.path.realpath(x) not in shared]
    roots += [*lay["paths"], lay["executable"], *lay.get("extra", [])]
    out = []
    for r in roots:
        rr = os.path.realpath(r)
        if rr in shared:
            continue
        if not any(rr == o or rr.startswith(o.rstrip(os.sep) + os.sep) for o in out):
            out = [o for o in out if not o.startswith(rr.rstrip(os.sep) + os.sep)] + [rr]
    inside = lambda p: any(p == o or p.startswith(o.rstrip(os.sep) + os.sep) for o in out)
    linked = [n for n in (*roots, *lay.get("names", [])) if n and os.path.realpath(n) != os.path.abspath(n)
              and inside(os.path.realpath(n))]
    return out + [n for n in dict.fromkeys(linked) if n not in out]


def node_policy(module: str, bundle, work, data, python: str | None = None, sandbox=None, broker_socket: str | None = None,
                kind: str = "runner", tool_paths: list | None = None, proxy_port: int | None = None,
                folders: dict | None = None) -> Policy:
    """A node-side process (runner, doctor, service, probe): its bundle and interpreter read-only, the job's work dir
    and the module's data dir read-write, plus the approved grants of `sandbox` (a manifest SandboxSection).
    `tool_paths`: the host's paths for the approved tool ids (the operator's tool registry resolves them per OS).
    `folders`: {id: {path, access}} for a runner's granted folders (only runners get them)."""
    granted = folders if kind == "runner" else {}
    ro = [str(bundle), *interpreter_roots(python), *(tool_paths or [])]
    rw = [str(p) for p in (work, data) if p]
    net = sandbox.net.mode if sandbox else "none"
    return Policy(module=module, ro=ro, rw=rw, net=net, proxy_port=proxy_port if net == "egress-allowlist" else None,
                  broker_socket=broker_socket if sandbox and sandbox.containers else None,
                  gpu=bool(sandbox and sandbox.devices.gpu != "none"), kind=kind, exe=python or sys.executable,
                  exec_rw=bool(sandbox and sandbox.exec_writable),
                  rd=[f["path"] for f in (granted or {}).values() if f["access"] == "read"],
                  wo=[f["path"] for f in (granted or {}).values() if f["access"] == "write"])


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
