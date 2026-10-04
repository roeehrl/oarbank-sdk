"""Reference control-plane handling for Python runners (runner protocol 1, spec/runner-protocol.md "Control"). Stdlib
only, so a runner can vendor this file instead of importing the SDK.

    ctl = Control(workdir)             # on POSIX, on the main thread
    ctl.phase("work")                  # the job is underway (the conformance kit times a stop from here)
    try:
        for chunk in work:
            ctl.safe_point()           # raises Stopped on a stop request; holds while paused
            do(chunk, threads=ctl.threads or default_threads)
    except Stopped:
        return ctl.acknowledge_stop()  # failure.json {reason: "<module>/stopped", fault: "transient"}; exit 75

The agent replaces <W>/control.json and then nudges the runner: SIGUSR1 to the runner process on POSIX, the auto-reset
event it inherits as OARBANK_CONTROL_EVENT on Windows. Control reads the document when it is created, and again only
after a nudge; a pause blocks until the next nudge. Nothing is polled. Documents apply only when their `seq` is newer.

A safe point is any place where holding or stopping the job changes nothing already written; time held there is left
out of throughput a runner reports (`paused_s`). The agent bounds how long a job stays paused.

Portable checkpoints (runner capability `checkpoint`, a stage with `checkpoint`; spec/runner-protocol.md "Checkpoints"):

    ckpt = Checkpoints(workdir, events_path, spec)
    start, state = 0, None
    if ckpt.resume():                                   # <W>/checkpoint/ holds the job's latest checkpoint
        state = json.loads((ckpt.resume_dir() / "state.json").read_text())
    try:
        for step in range(start, n):
            ctl.safe_point()
            do(step)
            if step % 50 == 0:
                with ckpt.write({"step": step}) as d:   # a fresh directory; its files become the checkpoint
                    (d / "state.json").write_text(json.dumps(state))
    except Stopped:
        if ctl.checkpoint_requested:                    # checkpoint, then stop: the agent waits checkpoint_grace_s
            with ckpt.write({"step": step}) as d:
                (d / "state.json").write_text(json.dumps(state))
        return ctl.acknowledge_stop()

POSIX: Control installs the SIGUSR1 handler and the signal wakeup fd, so create it on the main thread, once per
process. safe_point and check may then be called from any thread.
Windows: a runner started without OARBANK_CONTROL_EVENT (by hand, not by the agent) is never nudged, so the document
read at creation is the one that applies.
"""
import contextlib
import json
import os
import time
from pathlib import Path

ENV_CONTROL_EVENT = "OARBANK_CONTROL_EVENT"
EXIT_STOPPED = 75                      # transient: the agent asked for the stop, so the job runs again later or elsewhere
REPLACE_RETRY_S = 2.0                  # Windows: a replace fails while the agent has the file open
CHECKPOINT_DIR = "checkpoint"          # <W>/checkpoint/: the checkpoint a resumed attempt starts from (read-only)
CHECKPOINT_DATA_MAX = 4096             # bytes of a checkpoint event's `data` (compact JSON)


def replace_text(path, text: str):
    """Write `path` atomically (a temporary file, then rename), retrying a replace the reader blocks (Windows)."""
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


class Stopped(Exception):
    """The agent asked the job to stop: clean up quickly and exit non-zero."""


if os.name == "nt":
    import _winapi

    class _Nudges:
        """The inherited auto-reset event: set once per nudge, reset by the wait that sees it."""

        def __init__(self):
            v = os.environ.get(ENV_CONTROL_EVENT)
            self._event = int(v) if v else None

        def take(self) -> bool:
            """Whether a nudge came since the last take or wait (without blocking)."""
            return self._event is not None and _winapi.WaitForSingleObject(self._event, 0) == _winapi.WAIT_OBJECT_0

        def wait(self):
            """Block until the next nudge."""
            if self._event is None:
                raise RuntimeError(f"paused, and nothing can resume the job: {ENV_CONTROL_EVENT} is not set")
            _winapi.WaitForSingleObject(self._event, _winapi.INFINITE)
else:
    import atexit
    import select
    import signal

    _pipe = None                                 # (read fd, write fd), made by the first Control

    def _on_signal(*_):
        try:
            os.write(_pipe[1], b"\0")
        except BlockingIOError:                  # the pipe is full: a nudge is pending anyway
            pass

    class _Nudges:
        """SIGUSR1 marks a nudge pending by writing a byte to a pipe, from whichever thread the OS delivers it to: the
        pipe is the signal wakeup fd (written by Python's C-level handler at once) and the Python handler writes it too
        (in case something else, such as asyncio's add_signal_handler, has the wakeup fd). One per process."""

        def __init__(self):
            global _pipe
            if _pipe is None:
                r, w = os.pipe()
                os.set_blocking(r, False)
                os.set_blocking(w, False)
                try:
                    signal.signal(signal.SIGUSR1, _on_signal)
                    prev = signal.set_wakeup_fd(w, warn_on_full_buffer=False)
                except ValueError:
                    os.close(r)
                    os.close(w)
                    raise RuntimeError("create Control on the main thread: it installs the SIGUSR1 handler the "
                                       "agent's nudges reach (spec/runner-protocol.md, Control)") from None
                if prev != -1:                   # someone else's: theirs stays, the handler alone writes the pipe
                    signal.set_wakeup_fd(prev)
                # at exit, back to ignoring nudges, as the agent started the runner: interpreter teardown would otherwise
                # reset the handler to the default, and a nudge arriving then would kill a runner that already finished
                atexit.register(signal.signal, signal.SIGUSR1, signal.SIG_IGN)
                _pipe = (r, w)
            self._r = _pipe[0]

        def take(self) -> bool:
            """Whether a nudge came since the last take or wait (without blocking)."""
            got = False
            try:
                while os.read(self._r, 512):
                    got = True
            except BlockingIOError:
                pass
            return got

        def wait(self):
            """Block until the next nudge."""
            while not self.take():
                select.select([self._r], [], [])


class Control:
    def __init__(self, workdir=None):
        workdir = workdir or os.environ.get("OARBANK_WORKDIR", ".")
        self.path = Path(workdir) / "control.json"
        self.seq = -1
        self.stop = self.pause = self.checkpoint = False
        self.threads: int | None = None
        self.gpu_duty: float | None = None
        self.paused_s = 0.0
        self._nudges = _Nudges()                 # first, so that any change after this read leaves a nudge pending
        self._nudges.take()                      # and what came before it is in the read
        self.refresh()

    def refresh(self) -> bool:
        """Read the document and apply it if it is newer; returns whether anything changed."""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        seq = int(doc.get("seq", -1))
        if seq <= self.seq:
            return False
        self.seq = seq
        self.stop, self.pause = bool(doc.get("stop")), bool(doc.get("pause"))
        self.checkpoint = bool(doc.get("checkpoint"))
        self.threads, self.gpu_duty = doc.get("threads"), doc.get("gpu_duty")
        return True

    @property
    def checkpoint_requested(self) -> bool:
        """The stop asks for a checkpoint first (`stop` with `checkpoint`): write one, then acknowledge the stop."""
        return self.stop and self.checkpoint

    def check(self):
        """Apply a nudged change (never blocks); raise Stopped on a stop request."""
        if self._nudges.take():
            self.refresh()
        if self.stop:
            raise Stopped()

    def phase(self, name: str):
        """Name the job's current phase in <W>/phase (one line, replaced atomically)."""
        replace_text(self.path.with_name("phase"), name + "\n")

    def acknowledge_stop(self, reason: str | None = None) -> int:
        """Acknowledge a stop request: write <W>/failure.json (`fault = "transient"`; the reason defaults to
        `<module-short>/stopped`) and return the exit code to end with (75). Call it where Stopped is caught."""
        module = os.environ.get("OARBANK_MODULE") or "module"
        replace_text(self.path.with_name("failure.json"), json.dumps(
            {"reason": reason or f"{module}/stopped", "detail": "stopped at a safe point on the agent's request",
             "fault": "transient"}))
        return EXIT_STOPPED

    def safe_point(self):
        """Raise Stopped on a stop request; hold while a pause is in force, until a nudge brings a change."""
        self.check()
        while self.pause:
            t = time.perf_counter()
            self._nudges.wait()
            self.paused_s += time.perf_counter() - t
            self.refresh()
            if self.stop:
                raise Stopped()


class Checkpoints:
    """A runner's portable checkpoints: write each one into a fresh directory and announce it with a `checkpoint` event
    (the agent then takes the files away and uploads them), and find the checkpoint a resumed attempt starts from.

    `events` is the `--events` path the agent passes; `spec` the parsed spec.json (for `resume`)."""

    def __init__(self, workdir=None, events=None, spec: dict | None = None):
        self.workdir = Path(workdir or os.environ.get("OARBANK_WORKDIR", "."))
        self.events = Path(events) if events else None
        self.spec = spec or {}
        self.seq = 0

    def resume(self) -> dict | None:
        """The spec envelope's `resume` ({from_attempt, digest, data}) when this attempt resumes from a checkpoint."""
        r = self.spec.get("resume")
        return r if isinstance(r, dict) and (self.workdir / CHECKPOINT_DIR).is_dir() else None

    def resume_dir(self) -> Path:
        """<W>/checkpoint/: the checkpoint's files under their names (read-only)."""
        return self.workdir / CHECKPOINT_DIR

    def resume_data(self) -> dict:
        r = self.resume()
        return dict(r.get("data") or {}) if r else {}

    @contextlib.contextmanager
    def write(self, data: dict | None = None):
        """A fresh directory for one checkpoint; when the block ends, every regular file in it becomes the checkpoint
        (named by its path inside the directory) and its event is written. The files then belong to the agent: write the
        next checkpoint with another `write()`."""
        raw = json.dumps(data or {}, separators=(",", ":"), sort_keys=True)
        if len(raw.encode()) > CHECKPOINT_DATA_MAX:
            raise ValueError(f"checkpoint data is {len(raw.encode())} bytes; at most {CHECKPOINT_DATA_MAX}")
        if self.events is None:
            raise RuntimeError("no events file: the agent passes --events to a runner that declares `checkpoint`")
        self.seq += 1
        d = self.workdir / "ckpt" / f"{self.seq:06d}"
        d.mkdir(parents=True)
        yield d
        files = []
        for f in sorted(d.rglob("*")):
            if f.is_file() and not f.is_symlink():
                files.append({"path": f.relative_to(self.workdir).as_posix(), "name": f.relative_to(d).as_posix()})
        if not files:
            raise ValueError("a checkpoint holds at least one file")
        event = {"t": time.time(), "kind": "checkpoint", "files": files, "data": data or {}}
        with open(self.events, "a", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(event, separators=(",", ":")) + "\n")
            out.flush()
            os.fsync(out.fileno())
