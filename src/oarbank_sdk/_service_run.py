"""The conformance kit's agent for module services (spec/service-protocol.md): one run of a service started the way an
agent starts it (its environment, cwd the bundle root, sandboxed where the kit sandboxes, an endpoint service with its
channel), its ops, the connections a job would get, and the listening sockets its processes hold (an endpoint service
never listens: the kit fails one that does)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import portable
from . import _endpoint_host as H
from .service_protocol import DEFAULT_TIMEOUT_S


def _argv(exec_: list[str], root: Path) -> list[str]:
    return [sys.executable if a == "python" else a.replace("{bundle}", str(root)) for a in exec_]


def service_env(man, svc, data: Path, grants_env: dict, limits: Path) -> dict:
    """The protocol's environment (spec/service-protocol.md, "Invocation") for this host's OS."""
    return {"OARBANK_MODULE_DATA": str(data), "OARBANK_MODULE": man.module.id.rsplit(".", 1)[-1], "OARBANK_SERVICE": svc.name,
            "OARBANK_NODE_ID": "conform", "OARBANK_PLATFORM": portable.host_platform(), "OARBANK_PROTOCOL": "1",
            "OARBANK_LIMITS_FILE": str(limits), **grants_env, **portable.os_env(data, data / "tmp")}


class ServiceRun:
    """One service run under the kit. `policy(data)`: the sandbox policy to render (None: run unconfined)."""

    def __init__(self, root: Path, man, svc, grants_env: dict, policy=None):
        self.root, self.man, self.svc, self.policy = root, man, svc, policy
        self.data = Path(tempfile.mkdtemp(prefix="conform-svc-"))
        (self.data / "tmp").mkdir()
        limits = self.data.parent / f"{self.data.name}.limits.json"
        limits.write_text("{}", encoding="utf-8")
        self.env = service_env(man, svc, self.data, grants_env, limits)
        self.host: H.ServiceHost | None = None
        self.leader: int | None = None
        self.started = False
        self.output = ""

    def _launch(self, op: str) -> list[str]:
        argv = _argv(self.svc.exec, self.root) + [op]
        if self.policy is not None:
            from . import sandbox as S
            text, params = S.render(self.policy(self.data))
            argv = S.launch_argv(S.write_profile(text, self.data.parent / f"{self.data.name}.sb"), params, argv)
        return argv

    def op(self, op: str, timeout: float | None = None) -> tuple[int | str, dict | None, str]:
        """(exit code or "timeout", its JSON document, its stderr tail)."""
        t = timeout or DEFAULT_TIMEOUT_S.get(op, 60)
        try:
            p = subprocess.run(self._launch(op), cwd=self.root, env=self.env, capture_output=True, text=True, timeout=t,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return "timeout", None, ""
        doc = None
        for line in reversed(p.stdout.strip().splitlines()):
            try:
                doc = json.loads(line)
                break
            except ValueError:
                continue
        return p.returncode, doc, p.stderr[-600:]

    def start(self) -> str | None:
        """Start the service (an endpoint service with its channel) and wait for hello and `ready`: None, or what went
        wrong."""
        env, kwargs = dict(self.env), {}
        if self.svc.endpoint:
            self.host = H.ServiceHost()
            env.update(self.host.env)
            kwargs = self.host.popen_kwargs
        if os.name != "nt":
            kwargs = {**kwargs, "start_new_session": True}       # its process group is its container here
        limit = self.svc.start_timeout_s
        # files, not pipes: what `start` leaves running may keep its output open for as long as it runs
        err_path = self.data.parent / f"{self.data.name}.start.err"
        with open(err_path, "wb") as err, open(os.devnull, "wb") as null:
            try:
                p = subprocess.Popen(self._launch("start"), cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=null,
                                     stderr=err, **kwargs)
            finally:
                if self.host:
                    self.host.spawned()
        self.leader = p.pid
        self.started = True
        try:
            p.wait(limit)
        except subprocess.TimeoutExpired:
            return f"start did not finish within start_timeout_s ({limit:g} s)"
        self.output = err_path.read_text(encoding="utf-8", errors="replace")[-600:]
        if p.returncode != 0:
            return f"start exited {p.returncode}: {self.output}"
        deadline = time.monotonic() + limit
        if self.host:
            hello = {}
            t = threading.Thread(target=lambda: hello.update(pid=self._hello()), daemon=True)
            t.start()
            t.join(limit)
            if not hello.get("pid"):
                return f"no hello on the endpoint channel within start_timeout_s ({limit:g} s){self._log()}"
        while time.monotonic() < deadline:
            code, doc, err = self.op("ready")
            if code == 0 and doc and doc.get("ready") is True:
                return None
            time.sleep(0.2)
        return f"`ready` did not answer true within start_timeout_s ({limit:g} s){self._log()}"

    def _hello(self):
        try:
            return self.host.wait_hello()
        except Exception:                                # noqa: BLE001 (any failure is "no hello")
            return None

    def _log(self) -> str:
        return f": {self.output}" if self.output else ""

    def processes(self) -> list[int]:
        """The service's processes: its process group (POSIX), or the hello's process and its descendants (Windows)."""
        if os.name != "nt":
            if self.leader is None:
                return []
            ps = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True).stdout
            return [int(f[0]) for f in (line.split() for line in ps.splitlines())
                    if len(f) == 3 and int(f[1]) == self.leader and not f[2].startswith("Z")]
        root = self.host.pid if self.host and self.host.pid else self.leader
        if root is None:
            return []
        q = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                           capture_output=True, text=True).stdout
        parent = {}
        for line in q.splitlines():
            f = line.split()
            if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
                parent[int(f[0])] = int(f[1])
        out, todo = {root}, [root]
        while todo:
            cur = todo.pop()
            for pid, pp in parent.items():
                if pp == cur and pid not in out:
                    out.add(pid)
                    todo.append(pid)
        return sorted(out)

    def listening(self) -> list[str]:
        """The listening sockets the service's processes hold, described."""
        return listening(self.processes())

    def connect(self, attempt: int):
        return H.connect_here(self.host, attempt)

    def stop(self) -> str | None:
        """`stop`, then the channel closed and whatever is left of the service ended. None, or what went wrong."""
        if not self.started:
            return None
        code, doc, err = self.op("stop", self.svc.stop_timeout_s)
        if self.host:
            self.host.close()
        left = self.processes()
        for pid in left:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)
            except OSError:
                pass
        self.started = False
        return None if code == 0 else f"stop exited {code}: {err}"


def listening(pids: list[int]) -> list[str]:
    """Listening sockets (TCP listeners, bound UDP sockets, unix sockets accepting connections) held by `pids`."""
    if not pids:
        return []
    if sys.platform.startswith("linux"):
        return _linux(pids)
    if sys.platform == "darwin":
        return _lsof(pids)
    if os.name == "nt":
        return _netstat(pids)
    return []


def _linux(pids: list[int]) -> list[str]:
    inodes = {}
    for pid in pids:
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                m = re.match(r"socket:\[(\d+)\]", os.readlink(f"/proc/{pid}/fd/{fd}"))
                if m:
                    inodes[m.group(1)] = pid
        except OSError:
            continue
    out = []
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        try:
            rows = Path(f"/proc/net/{proto}").read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) > 9 and f[9] in inodes:
                if (proto.startswith("tcp") and f[3] == "0A") or (proto.startswith("udp") and set(f[2].split(":")[1]) == {"0"}):
                    out.append(f"{proto} {f[1]} (process {inodes[f[9]]})")
    try:
        for row in Path("/proc/net/unix").read_text().splitlines()[1:]:
            f = row.split()
            if len(f) > 6 and f[6] in inodes and int(f[3], 16) & 0x10000:
                out.append(f"unix {f[7] if len(f) > 7 else '(unnamed)'} (process {inodes[f[6]]})")
    except OSError:
        pass
    return out


def _lsof(pids: list[int]) -> list[str]:
    out = []
    for flags in (["-iTCP", "-sTCP:LISTEN"], ["-iUDP"]):
        r = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), *flags, "-Fpn"],
                           capture_output=True, text=True)
        pid = None
        for line in r.stdout.splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("n"):
                out.append(f"{'tcp' if flags[0] == '-iTCP' else 'udp'} {line[1:]} (process {pid})")
    return out


def _netstat(pids: list[int]) -> list[str]:
    r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
    want = {str(p) for p in pids}
    out = []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 4 and f[-1] in want and (f[0] == "UDP" or (f[0] == "TCP" and f[-2] == "LISTENING")):
            out.append(f"{f[0].lower()} {f[1]} (process {f[-1]})")
    return out
