"""Reference helpers for workloads (workload protocol 1, spec/workload-protocol.md). Standard library only, so a module
can vendor this file instead of importing the SDK.

Most workloads wrap a third-party daemon that knows nothing about Oarbank. `Supervisor` is the shim the manifest's
`exec` runs: it starts the daemon as its child, in its own process group (so the agent's process container keeps it),
turns the control document into what the daemon understands, writes a status file the `status` op reads, and ends the
daemon on a stop:

    # worker.py; manifest: exec = ["python", "-I", "{bundle}/worker.py"]
    import sys
    from oarbank_sdk.workload import Supervisor, progress

    def on_line(line):                               # each line the daemon prints (also passed through to the log)
        if line.startswith("unit "):
            return {"type": "progress", "event": "unit", "n": 1}

    sup = Supervisor([sys.executable, "-I", "unitd.py"], threads="--threads={n}", line_handler=on_line)
    sys.exit(sup.main())                             # `run` or `status`, as the agent appends them

- pause: "signal" (SIGSTOP and SIGCONT to the daemon and all of its descendants, never to the supervisor; on Windows
  NtSuspendProcess and NtResumeProcess), "none", or a callable(bool) that pauses and resumes the daemon its own way;
- threads: None, a callable(int | None), or a flag template such as "--threads={n}": the daemon is restarted with the
  flag whenever the limit changes (no flag while there is none);
- gpu_duty: None or a callable(float | None); with one, the status says `gpu_duty_supported`;
- stop: `stop_signal` (SIGTERM; on Windows the daemon's tree is terminated) to the daemon, then a kill of it and its
  descendants after `stop_timeout_s` (keep it below the manifest's `stop_timeout_s`); the supervisor then exits 0;
- the daemon exiting on its own makes the supervisor exit non-zero (the agent's restart policy applies), unless
  `restart_on_exit`;
- `<run>/.supervisor.json` is rewritten every STATUS_INTERVAL_S: the daemon's pid, paused, threads, gpu_duty, the last
  time it printed a line, restarts. `status` is healthy while that file is fresh, the daemon runs and the optional
  `health` callable (() -> (bool, str)) agrees.

Without the shim, a workload uses `ControlWatch` (the control document: a nudge, or a re-read every `poll_s`) and the
event helpers directly:

    ctl = ControlWatch()                             # on POSIX on the main thread: it installs the SIGUSR1 handler
    while not ctl.stop:
        if not ctl.pause:
            do_some_work(threads=ctl.threads)
            progress("unit")
        ctl.wait(1.0)

Events go to OARBANK_EVENTS_FILE as JSON lines: `progress(event, n=1)`, `report(data)` (at most 4 KiB of JSON; with
`reports = true` the coordinator hands it to the module's `workload.report` verb), `error(message)`, `log(message)`.
`secrets()` reads OARBANK_SECRETS_FILE (only a workload that lists secrets has one): never log a value.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ENV_WORKLOAD, ENV_RUN, ENV_MODULE_DATA = "OARBANK_WORKLOAD", "OARBANK_WORKLOAD_RUN", "OARBANK_MODULE_DATA"
ENV_CONTROL_FILE, ENV_EVENTS_FILE, ENV_SECRETS_FILE = "OARBANK_CONTROL_FILE", "OARBANK_EVENTS_FILE", "OARBANK_SECRETS_FILE"
ENV_CONTROL_EVENT = "OARBANK_CONTROL_EVENT"
STATUS_FILE = ".supervisor.json"               # <run>/.supervisor.json
STATUS_INTERVAL_S = 2.0                        # how often the supervisor rewrites it
STALE_AFTER = 3                                # status is unhealthy once the file is this many intervals old
POLL_S = 1.0                                   # the control document is re-read at least this often (the protocol: 2 s)
REPORT_DATA_MAX = 4096                         # bytes of a report's data as compact JSON
MESSAGE_MAX = 4096
DEFAULT_STOP_TIMEOUT_S = 20.0
EXIT_USAGE = 64
REPLACE_RETRY_S = 2.0
WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------- events and secrets

_events_lock = threading.Lock()


def emit(event: dict) -> bool:
    """Append one event (a JSON object with `type`) to OARBANK_EVENTS_FILE, stamped with `t` unless it has one. False
    when there is no events file (the workload was started by hand)."""
    path = os.environ.get(ENV_EVENTS_FILE)
    if not path:
        return False
    line = json.dumps({"t": time.time(), **event}, separators=(",", ":"), ensure_ascii=False) + "\n"
    with _events_lock, open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
    return True


def progress(event: str, n: int = 1, **attrs) -> bool:
    """`n` more of `event` (a lower-case name: `unit`, `block.verified`); the agent sums them per workload."""
    if not isinstance(event, str) or not event:
        raise ValueError("a progress event is named")
    return emit({"type": "progress", "event": event, "n": int(n), **attrs})


def report(data: dict) -> bool:
    """An observation for the module's own records (at most 4 KiB as compact JSON). With `reports = true` the agent ships
    it to the coordinator, which calls the module's `workload.report` verb with it."""
    if not isinstance(data, dict):
        raise TypeError("report data is a JSON object")
    size = len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size > REPORT_DATA_MAX:
        raise ValueError(f"report data is {size} bytes as JSON; at most {REPORT_DATA_MAX}")
    return emit({"type": "report", "data": data})


def error(message: str) -> bool:
    """Something went wrong: the agent keeps the latest as the run's last error."""
    return emit({"type": "error", "message": str(message)[:MESSAGE_MAX]})


def log(message: str, level: str = "info") -> bool:
    return emit({"type": "log", "level": level, "message": str(message)[:MESSAGE_MAX]})


def secrets() -> dict:
    """The workload's secrets ({name: value}) from OARBANK_SECRETS_FILE; {} when it has none."""
    path = os.environ.get(ENV_SECRETS_FILE)
    if not path:
        return {}
    try:
        return dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}


def replace_text(path, text: str):
    """Write `path` atomically (a temporary file, then rename), retrying a replace a reader blocks (Windows)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    t = time.monotonic()
    while True:
        try:
            return os.replace(tmp, path)
        except PermissionError:
            if time.monotonic() - t > REPLACE_RETRY_S:
                raise
            time.sleep(0.01)


# ---------------------------------------------------------------------------- the control document

if WINDOWS:
    import _overlapped
    import _winapi

    class _Nudges:
        """The inherited auto-reset event (absent after an agent restart: the re-read covers that) and our own event,
        which `wake` sets from any thread."""

        def __init__(self):
            v = os.environ.get(ENV_CONTROL_EVENT)
            self._agent = int(v) if v else None
            self._own = _overlapped.CreateEvent(None, False, False, None)

        def take(self) -> bool:
            return self._agent is not None and _winapi.WaitForSingleObject(self._agent, 0) == _winapi.WAIT_OBJECT_0

        def wait(self, timeout: float) -> bool:
            handles = [h for h in (self._agent, self._own) if h is not None]
            r = _winapi.WaitForMultipleObjects(handles, False, max(0, int(timeout * 1000)))
            return r != _winapi.WAIT_TIMEOUT

        def wake(self):
            _overlapped.SetEvent(self._own)
else:
    import select

    class _Nudges:
        """SIGUSR1 (the agent's nudge, sent to the leader only) and `wake` both write a byte to a pipe that `wait`
        selects on. The handler must be installed on the main thread; elsewhere only the re-read applies."""

        def __init__(self):
            self._r, self._w = os.pipe()
            os.set_blocking(self._r, False)
            os.set_blocking(self._w, False)
            try:
                signal.signal(signal.SIGUSR1, lambda *_: self.wake())
                self.installed = True
            except ValueError:                   # not the main thread
                self.installed = False

        def take(self) -> bool:
            got = False
            try:
                while os.read(self._r, 512):
                    got = True
            except BlockingIOError:
                pass
            return got

        def wait(self, timeout: float) -> bool:
            ready, _, _ = select.select([self._r], [], [], max(0.0, timeout))
            return bool(ready) and self.take()

        def wake(self):
            try:
                os.write(self._w, b"\0")
            except BlockingIOError:              # full: a wake is pending anyway
                pass


class ControlWatch:
    """The workload's control document (OARBANK_CONTROL_FILE): `{seq, stop, pause, threads?, gpu_duty?, reason?}`,
    replaced atomically with a rising seq. `wait(timeout)` returns early on the agent's nudge (SIGUSR1 on POSIX, the
    control event on Windows) or a `wake()`, then re-reads the document; call it at least every 2 s, since a nudge can be
    lost (Windows after an agent restart). Create it on the main thread on POSIX: it installs the SIGUSR1 handler first
    and only then reads the document, so no change is missed."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get(ENV_CONTROL_FILE) or "control.json")
        self.seq = -1
        self.stop = self.pause = False
        self.threads: int | None = None
        self.gpu_duty: float | None = None
        self.reason: str | None = None
        self._nudges = _Nudges()
        self._nudges.take()
        self.refresh()

    def refresh(self) -> bool:
        """Read the document and apply it when its seq is newer; returns whether it did."""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            seq = int(doc.get("seq", -1))
        except (OSError, ValueError, TypeError, AttributeError):
            return False
        if seq <= self.seq:
            return False
        self.seq = seq
        self.stop, self.pause = bool(doc.get("stop")), bool(doc.get("pause"))
        self.threads, self.gpu_duty, self.reason = doc.get("threads"), doc.get("gpu_duty"), doc.get("reason")
        return True

    def wait(self, timeout: float = POLL_S) -> bool:
        """Sleep until a nudge, a wake or `timeout`, then re-read the document; returns whether it changed."""
        self._nudges.wait(timeout)
        return self.refresh()

    def wake(self):
        """End the current `wait` early (from a signal handler or another thread)."""
        self._nudges.wake()


# ---------------------------------------------------------------------------- process trees

def _children() -> dict[int, list[int]]:
    """parent pid -> its children's pids, for every process this account can see."""
    out: dict[int, list[int]] = {}
    for pid, ppid in _pairs():
        out.setdefault(ppid, []).append(pid)
    return out


def _pairs() -> list[tuple[int, int]]:
    if WINDOWS:
        return _toolhelp_pairs()
    if sys.platform.startswith("linux"):
        pairs = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                stat = Path(f"/proc/{d}/stat").read_text()
            except OSError:
                continue
            fields = stat[stat.rfind(")") + 2:].split()      # after "pid (comm) ": state ppid ...
            if len(fields) > 1 and fields[0] != "Z":
                pairs.append((int(d), int(fields[1])))
        return pairs
    if sys.platform == "darwin":
        found = _kinfo_pairs()
        if found is not None:
            return found
    ps = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat="], capture_output=True, text=True).stdout
    return [(int(f[0]), int(f[1])) for f in (line.split() for line in ps.splitlines())
            if len(f) == 3 and f[0].isdigit() and f[1].isdigit() and not f[2].startswith("Z")]


_KINFO_SIZE, _P_STAT, _P_PID, _E_PPID = 648, 36, 40, 560       # struct kinfo_proc on 64-bit macOS
_SZOMB = 5


def _kinfo_pairs() -> list[tuple[int, int]] | None:
    """macOS: (pid, ppid) of every live process from sysctl kern.proc.all, which the module sandbox allows (`ps` is
    setuid there, and the sandbox refuses to exec it). None when the answer does not look like kinfo_proc records."""
    import ctypes
    import struct
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    mib = (ctypes.c_int * 3)(1, 14, 0)                            # CTL_KERN, KERN_PROC, KERN_PROC_ALL
    for _ in range(3):
        size = ctypes.c_size_t(0)
        if lib.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        size.value += 64 * _KINFO_SIZE                            # room for processes started meanwhile
        buf = ctypes.create_string_buffer(size.value)
        if lib.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0:
            break
        if ctypes.get_errno() != 12:                              # ENOMEM: it grew; ask again
            return None
    else:
        return None
    raw = buf.raw[:size.value]
    if len(raw) % _KINFO_SIZE:
        return None
    out = []
    for off in range(0, len(raw), _KINFO_SIZE):
        if raw[off + _P_STAT] == _SZOMB:
            continue
        pid, = struct.unpack_from("i", raw, off + _P_PID)
        ppid, = struct.unpack_from("i", raw, off + _E_PPID)
        out.append((pid, ppid))
    return out


def descendants(pid: int) -> list[int]:
    """Every live descendant of `pid` (children first), never the calling process."""
    kids, out, todo, me = _children(), [], [pid], os.getpid()
    while todo:
        for c in kids.get(todo.pop(0), []):
            if c not in out and c != pid and c != me:
                out.append(c)
                todo.append(c)
    return out


if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _PROCESS_TERMINATE, _PROCESS_SUSPEND_RESUME, _SYNCHRONIZE, _QUERY_LIMITED = 0x0001, 0x0800, 0x00100000, 0x1000

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def _toolhelp_pairs() -> list[tuple[int, int]]:
        snap = _k32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
        if snap in (None, wintypes.HANDLE(-1).value):
            return []
        out = []
        try:
            e = _PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                out.append((int(e.th32ProcessID), int(e.th32ParentProcessID)))
                ok = _k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            _k32.CloseHandle(snap)
        return out

    def _with_process(pid: int, access: int, fn) -> bool:
        h = _k32.OpenProcess(access, False, pid)
        if not h:
            return False
        try:
            fn(h)
            return True
        finally:
            _k32.CloseHandle(h)

    def _suspend(pid: int) -> bool:
        return _with_process(pid, _PROCESS_SUSPEND_RESUME, lambda h: _ntdll.NtSuspendProcess(h))   # undocumented, stable

    def _resume(pid: int) -> bool:
        return _with_process(pid, _PROCESS_SUSPEND_RESUME, lambda h: _ntdll.NtResumeProcess(h))

    def _terminate(pid: int) -> bool:
        return _with_process(pid, _PROCESS_TERMINATE, lambda h: _k32.TerminateProcess(h, 1))

    def alive(pid: int) -> bool:
        h = _k32.OpenProcess(_SYNCHRONIZE | _QUERY_LIMITED, False, pid)
        if not h:
            return False
        try:
            return _k32.WaitForSingleObject(h, 0) == 0x102                # WAIT_TIMEOUT: still running
        finally:
            _k32.CloseHandle(h)
else:
    def _suspend(pid: int) -> bool:
        return _signal(pid, signal.SIGSTOP)

    def _resume(pid: int) -> bool:
        return _signal(pid, signal.SIGCONT)

    def _terminate(pid: int) -> bool:
        return _signal(pid, signal.SIGKILL)

    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _signal(pid: int, sig) -> bool:
    """Signal one process of our own process group (never the caller, never a pid that was reused elsewhere)."""
    if pid == os.getpid():
        return False
    try:
        if os.getpgid(pid) != os.getpgrp():
            return False
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def freeze(pid: int, frozen: list[int] | None = None) -> list[int]:
    """Suspend `pid` and all of its descendants (the parent first, then whatever it started, again until no new one
    appears); `frozen` are those already suspended. Returns every suspended pid."""
    frozen = list(frozen or [])
    for _ in range(4):
        new = [p for p in [pid, *descendants(pid)] if p not in frozen]
        if not new:
            break
        for p in new:
            if _suspend(p):
                frozen.append(p)
    return frozen


def thaw(pids: list[int]):
    """Resume what `freeze` suspended, each once."""
    for p in reversed(pids):
        _resume(p)


# ---------------------------------------------------------------------------- the supervisor

class Supervisor:
    """Run a third-party daemon as a workload: see the module docstring."""

    def __init__(self, argv: list[str], *, env: dict | None = None, cwd=None, pause="signal", threads=None,
                 gpu_duty=None, stop_signal=None, stop_timeout_s: float | None = None, line_handler=None, health=None,
                 restart_on_exit: bool = False, restart_delay_s: float = 1.0):
        if not argv:
            raise ValueError("the daemon's argv is empty")
        if not (pause in ("signal", "none") or callable(pause)):
            raise ValueError("pause is 'signal', 'none' or a callable(bool)")
        if not (threads is None or callable(threads) or (isinstance(threads, str) and "{n}" in threads)):
            raise ValueError("threads is None, a callable(int | None) or a flag template with {n}, e.g. '--threads={n}'")
        if not (gpu_duty is None or callable(gpu_duty)):
            raise ValueError("gpu_duty is None or a callable(float | None)")
        self.argv, self.env, self.cwd = list(argv), env, cwd
        self.pause_mode, self.threads_mode, self.gpu_cb = pause, threads, gpu_duty
        self.stop_signal = stop_signal if stop_signal is not None else (None if WINDOWS else signal.SIGTERM)
        self.stop_timeout_s = float(stop_timeout_s or DEFAULT_STOP_TIMEOUT_S)
        self.line_handler, self.health = line_handler, health
        self.restart_on_exit, self.restart_delay_s = restart_on_exit, float(restart_delay_s)
        self.proc: subprocess.Popen | None = None
        self.paused = False
        self.frozen: list[int] = []
        self.threads: int | None = None
        self.gpu_duty: float | None = None
        self.restarts = 0
        self.last_output_t: float | None = None
        self.started_t = 0.0
        self.stopping = False
        self.ctl: ControlWatch | None = None
        self._term = False

    # ------------------------------------------------------------ paths and entry point

    @property
    def run_dir(self) -> Path:
        return Path(os.environ.get(ENV_RUN) or os.getcwd())

    @property
    def status_path(self) -> Path:
        return self.run_dir / STATUS_FILE

    def main(self, argv: list[str] | None = None) -> int:
        """Dispatch the op the agent appended: `run` (the run loop), `status` (print the Status JSON); anything else is a
        usage error (64). Returns the exit code: `sys.exit(sup.main())`."""
        argv = sys.argv if argv is None else argv
        op = argv[1] if len(argv) > 1 else None
        if op == "run":
            return self.run()
        if op == "status":
            print(json.dumps(self.status()), flush=True)
            return 0
        sys.stderr.write(f"usage: {Path(argv[0]).name if argv else 'workload'} run|status\n")
        return EXIT_USAGE

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        """The `status` op's answer (workload-status-1), from the status file the run loop writes and `health`."""
        try:
            doc = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            return {"healthy": False, "detail": "the supervisor has not written its status yet"}
        out = {"paused": bool(doc.get("paused")), "threads": doc.get("threads"),
               "gpu_duty_supported": bool(doc.get("gpu_duty_supported"))}
        age = time.time() - float(doc.get("t") or 0)
        limit = STALE_AFTER * float(doc.get("interval_s") or STATUS_INTERVAL_S)
        pid = doc.get("daemon_pid")
        if age > limit:
            return {**out, "healthy": False, "detail": f"the supervisor's status is {age:.0f} s old (more than {limit:g} s)"}
        if not doc.get("daemon_running") or not isinstance(pid, int) or not alive(pid):
            return {**out, "healthy": False, "detail": "the daemon is not running"}
        healthy, detail = True, f"daemon {pid} running" + (", paused" if out["paused"] else "")
        if self.health is not None:
            try:
                ok, why = self.health()
            except Exception as e:                       # noqa: BLE001 (a broken check is an unhealthy answer)
                ok, why = False, f"the health check failed: {type(e).__name__}: {e}"
            healthy, detail = bool(ok), (str(why) or detail)
        return {**out, "healthy": healthy, "detail": detail[:MESSAGE_MAX]}

    def _write_status(self):
        p = self.proc
        doc = {"t": time.time(), "interval_s": STATUS_INTERVAL_S, "supervisor_pid": os.getpid(),
               "daemon_pid": p.pid if p else None, "daemon_running": bool(p and p.poll() is None),
               "paused": self.paused, "threads": self.threads, "gpu_duty": self.gpu_duty,
               "gpu_duty_supported": self.gpu_cb is not None, "last_output_t": self.last_output_t,
               "restarts": self.restarts, "seq": self.ctl.seq if self.ctl else None, "stopping": self.stopping}
        try:
            replace_text(self.status_path, json.dumps(doc))
        except OSError:
            pass

    # ------------------------------------------------------------ the run loop

    def run(self) -> int:
        """Supervise the daemon until a stop (exit 0) or until it exits on its own (non-zero, or restarted with
        `restart_on_exit`)."""
        self.ctl = ctl = ControlWatch()                  # the SIGUSR1 handler first, then the read
        if not WINDOWS:
            try:
                signal.signal(signal.SIGTERM, self._on_term)
            except ValueError:
                pass
        if ctl.stop:
            self._write_status()
            return 0
        self.threads, self.gpu_duty = ctl.threads, ctl.gpu_duty
        if callable(self.threads_mode) and self.threads is not None:
            self.threads_mode(self.threads)
        if self.gpu_cb is not None and self.gpu_duty is not None:
            self.gpu_cb(self.gpu_duty)
        self._start()
        if ctl.pause:
            self._set_paused(True)
        last_status, failures = 0.0, 0
        while True:
            if self._term:
                ctl.stop = True
            if ctl.stop:
                return self._shutdown()
            self._apply()
            code = self.proc.poll()
            if code is not None:
                if not self.restart_on_exit:
                    error(f"the daemon exited with {code}")
                    self._write_status()
                    return code if code > 0 else (128 - code if code < 0 else 1)
                failures = failures + 1 if time.monotonic() - self.started_t < 60 else 1
                log(f"the daemon exited with {code}; restarting", "warn")
                self._write_status()
                deadline = time.monotonic() + min(60.0, self.restart_delay_s * 2 ** min(failures - 1, 6))
                while time.monotonic() < deadline and not (ctl.stop or self._term):
                    ctl.wait(min(POLL_S, max(0.0, deadline - time.monotonic())))
                if ctl.stop or self._term:
                    continue                             # the stop wins: nothing to start
                self.restarts += 1
                self._start()
                continue
            if self.paused and self.pause_mode == "signal":
                self.frozen = freeze(self.proc.pid, self.frozen)     # a child started just before the pause
            now = time.monotonic()
            if now - last_status >= STATUS_INTERVAL_S:
                self._write_status()
                last_status = now
            ctl.wait(POLL_S)

    def _on_term(self, *_):
        self._term = True
        if self.ctl:
            self.ctl.wake()

    def _apply(self):
        """Bring the daemon in line with the control document: pause, threads, gpu_duty."""
        ctl = self.ctl
        if ctl.pause != self.paused:
            self._set_paused(ctl.pause)
        if ctl.threads != self.threads:
            if isinstance(self.threads_mode, str):
                if not self.paused:                      # restarting a paused daemon would resume it: wait
                    self.threads = ctl.threads
                    self._end_daemon()
                    self.restarts += 1
                    self._start()
            else:
                self.threads = ctl.threads
                if callable(self.threads_mode):
                    self.threads_mode(self.threads)
        if ctl.gpu_duty != self.gpu_duty:
            self.gpu_duty = ctl.gpu_duty
            if self.gpu_cb is not None:
                self.gpu_cb(self.gpu_duty)

    def _set_paused(self, on: bool):
        if self.pause_mode == "none":
            return
        if self.pause_mode == "signal":
            if on:
                self.frozen = freeze(self.proc.pid, self.frozen)
            else:
                thaw(self.frozen)
                self.frozen = []
        else:
            self.pause_mode(on)
        self.paused = on
        self._write_status()

    def _daemon_argv(self) -> list[str]:
        if isinstance(self.threads_mode, str) and self.threads is not None:
            return self.argv + [self.threads_mode.format(n=int(self.threads))]
        return list(self.argv)

    def _start(self):
        """The daemon, as our child in our own process group (never a new session or group: the agent's container must
        keep it), its output read line by line."""
        self.proc = subprocess.Popen(self._daemon_argv(), env=self.env, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.started_t = time.monotonic()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._watch, args=(self.proc,), daemon=True).start()
        self._write_status()

    def _watch(self, proc):
        proc.wait()
        if self.ctl:
            self.ctl.wake()                              # notice the exit at once

    def _read(self, proc):
        out = getattr(sys.stdout, "buffer", None)
        for raw in iter(proc.stdout.readline, b""):
            self.last_output_t = time.time()
            try:
                if out is not None:
                    out.write(raw)
                    out.flush()
            except (OSError, ValueError):
                pass
            if self.line_handler is None:
                continue
            try:
                ev = self.line_handler(raw.decode("utf-8", "replace").rstrip("\r\n"))
                if ev:
                    emit(ev)
            except Exception as e:                       # noqa: BLE001 (the module's handler must not end the reader)
                error(f"line handler: {type(e).__name__}: {e}")

    def _end_daemon(self):
        """`stop_signal` to the daemon (Windows: terminate its tree), then, after stop_timeout_s, kill it and whatever it
        started."""
        p = self.proc
        if p is None:
            return
        if self.frozen:
            thaw(self.frozen)                            # a stopped process acts on no signal but SIGKILL
            self.frozen = []
            self.paused = False
        tree = [p.pid, *descendants(p.pid)]
        if p.poll() is None:
            if WINDOWS or self.stop_signal is None:
                for pid in reversed(tree):
                    _terminate(pid)
            else:
                try:
                    p.send_signal(self.stop_signal)
                except OSError:
                    pass
            try:
                p.wait(self.stop_timeout_s)
            except subprocess.TimeoutExpired:
                pass
        for pid in dict.fromkeys([*tree, *descendants(p.pid)]):
            if pid != p.pid or p.poll() is None:
                _terminate(pid)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            pass

    def _shutdown(self) -> int:
        self.stopping = True
        self._write_status()
        self._end_daemon()
        self._write_status()
        return 0
