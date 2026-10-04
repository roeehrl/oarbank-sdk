"""The agent's container broker, from inside a sandboxed runner (spec/sandbox.md, "Containers").

A runner cannot reach Docker itself (the sandbox denies all local IPC but this job's broker endpoint). It asks the
agent instead, which checks the request against the module's approved images and runs the container on the agent's
own container runtime (a VM that mounts only oarbank's job and module-data directories):

    from oarbank_sdk import broker
    r = broker.run("docker.io/org/tool:1.2@sha256:...", ["tool", "--in", "/w/frame.exr"],
                   mounts=[broker.Mount("inputs", "/w", ro=True), broker.Mount("out", "/out")], platform="linux/amd64")
    if r.exit_code != 0: ...

Mount sources are paths relative to the job's work directory, or `data:<path>` for the module's data directory.
The container gets no network unless the module was approved for egress and asks for it, and no GPU unless the job's
stage reserves the agent's `gpu` pool and asks for `gpus="all"` (CDI on Linux, GPU-PV through WSL containers on
Windows; macOS runtimes cannot pass one through).
Images are digest-pinned: one of the module's `containers`, or an image of one of its `container_sets` that the job
listed (jobs.enqueue `images`), whose cosign signature the agent verifies before pulling.
"""
import json
import os
import socket
import time
from dataclasses import dataclass, field

ENV = "OARBANK_BROKER"            # an endpoint URI: unix:/abs/path or npipe://./pipe/<name>


class BrokerError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass
class Mount:
    src: str                  # relative to the work dir, or "data:<relative path>"
    dst: str                  # absolute path inside the container
    ro: bool = False


@dataclass
class Result:
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    stdout_path: str = ""     # relative to the work dir: the full output the agent captured
    stderr_path: str = ""
    duration_s: float = 0.0
    extra: dict = field(default_factory=dict)


def available() -> bool:
    return bool(os.environ.get(ENV))


def _connect(uri: str, timeout: float):
    """A byte stream to the broker endpoint (spec/sandbox.md, "Containers")."""
    if uri.startswith("unix:"):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(uri[len("unix:"):])
        return s
    if uri.startswith("npipe:"):
        return _Pipe(_open_pipe("\\\\.\\pipe\\" + uri.rsplit("/", 1)[-1], timeout))
    raise BrokerError("bad_endpoint", f"unknown broker endpoint {uri!r}")


def _open_pipe(name: str, timeout: float):
    """Open a named pipe for reading and writing, waiting while every instance is busy (the agent makes the next instance
    as soon as one is taken, so a wait is short)."""
    import _winapi
    import msvcrt
    deadline = time.monotonic() + timeout
    while True:
        try:
            h = _winapi.CreateFile(name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0,
                                   _winapi.NULL)
            return open(msvcrt.open_osfhandle(h, 0), "r+b", buffering=0)
        except OSError as e:
            if e.winerror != _winapi.ERROR_PIPE_BUSY or time.monotonic() > deadline:
                raise
            try:
                _winapi.WaitNamedPipe(name, 1000)
            except OSError:
                pass


class _Pipe:
    """A Windows named pipe opened as a file, with the two socket methods _call uses."""

    def __init__(self, f):
        self.f = f

    def sendall(self, b):
        self.f.write(b)

    def recv(self, n):
        return self.f.read(n)

    def close(self):
        self.f.close()


def _call(req: dict, timeout: float) -> dict:
    uri = os.environ.get(ENV)
    if not uri:
        raise BrokerError("no_broker", "this job has no container broker (declare [sandbox].containers and a `containers` pool)")
    s = _connect(uri, timeout)
    try:
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    resp = json.loads(buf or b"{}")
    if not resp.get("ok"):
        raise BrokerError(resp.get("error") or "broker_error", resp.get("detail") or "no answer")
    return resp


def run(image: str, args: list[str], mounts: list[Mount] | None = None, platform: str = "linux/arm64",
        entrypoint: str | None = None, env: dict | None = None, workdir: str | None = None, network: bool = False,
        timeout_s: float = 3600, cpus: float | None = None, mem_gb: float | None = None, gpus: str = "none") -> Result:
    """Run one container to completion through the agent (`docker run --rm`, validated). Raises BrokerError when
    the agent refuses the request; a container that runs and fails returns its exit code. `gpus`: "none" or "all"."""
    req = {"op": "container.run", "image": image, "args": list(args), "platform": platform, "entrypoint": entrypoint,
           "mounts": [{"src": m.src, "dst": m.dst, "ro": m.ro} for m in (mounts or [])], "env": dict(env or {}),
           "workdir": workdir, "network": network, "timeout_s": timeout_s, "cpus": cpus, "mem_gb": mem_gb,
           "gpus": gpus}
    r = _call(req, timeout_s + 120)
    known = {"exit_code", "stdout_tail", "stderr_tail", "stdout_path", "stderr_path", "duration_s"}
    return Result(**{k: r[k] for k in known if k in r}, extra={k: v for k, v in r.items() if k not in known | {"ok"}})


def pull(image: str, platform: str = "linux/arm64", timeout_s: float = 1800) -> dict:
    """Make sure an approved image is present on the agent's runtime (the agent pulls it once)."""
    return _call({"op": "container.pull", "image": image, "platform": platform}, timeout_s + 60)


def status(timeout_s: float = 30) -> dict:
    """The runtime's state: {running, images[], gpus}; `gpus` is "all" where this node's runtime passes GPUs through to
    containers, else "none"."""
    return _call({"op": "status"}, timeout_s)
