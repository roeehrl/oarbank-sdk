"""The reference Control reacts to the agent's nudges and never polls: SIGUSR1 on POSIX, the inherited control event on
Windows (spec/runner-protocol.md, "Control")."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from oarbank_sdk import control
from oarbank_sdk import runner_protocol as rp
from oarbank_sdk.conformance import _Nudge
from oarbank_sdk.control import Control, Stopped

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX nudges (SIGUSR1)")
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="the Windows control event")
REACTION_S = 0.1


def write(ws, **doc):
    rp.Control.model_validate(doc)                       # the agent's document shape
    tmp = ws / "control.json.tmp"
    tmp.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    tmp.replace(ws / "control.json")


@pytest.fixture
def nudge(monkeypatch):
    """Nudge this process the way the agent nudges a runner; returns when (perf_counter) it was nudged."""
    if os.name == "nt":
        import _overlapped
        event = _overlapped.CreateEvent(None, False, False, None)
        monkeypatch.setenv(control.ENV_CONTROL_EVENT, str(event))

        def send():
            t = time.perf_counter()
            _overlapped.SetEvent(event)
            return t
        yield send
        import _winapi
        _winapi.CloseHandle(event)
    else:
        import signal

        def send():
            t = time.perf_counter()
            os.kill(os.getpid(), signal.SIGUSR1)
            return t
        yield send


def later(delay, fn):
    """Run fn on another thread after `delay`; .result holds what it returned."""
    box = {}

    def run():
        time.sleep(delay)
        box["t"] = fn()
    th = threading.Thread(target=run)
    th.start()
    return th, box


def test_the_constant_matches_the_protocol():
    assert control.ENV_CONTROL_EVENT == rp.ENV_CONTROL_EVENT


def test_newer_documents_apply_and_older_are_ignored(tmp_path, nudge):
    t = Control(tmp_path)
    write(tmp_path, seq=3, threads=2)
    assert t.refresh() and t.threads == 2 and not t.pause
    write(tmp_path, seq=2, pause=True)
    assert not t.refresh() and not t.pause


def test_the_document_is_read_when_created_and_again_only_after_a_nudge(tmp_path, nudge):
    write(tmp_path, seq=1, threads=2)
    ctl = Control(tmp_path)
    assert ctl.threads == 2
    write(tmp_path, seq=2, threads=3)
    ctl.check()
    assert ctl.threads == 2, "no nudge, no read"
    nudge()
    ctl.check()
    assert ctl.threads == 3 and ctl.seq == 2


def test_a_pause_holds_without_spinning_until_a_nudge_brings_the_release(tmp_path, nudge):
    write(tmp_path, seq=1, pause=True)
    ctl = Control(tmp_path)
    th, box = later(0.5, lambda: (write(tmp_path, seq=2, pause=False), nudge())[1])
    cpu = time.process_time()
    ctl.safe_point()
    back = time.perf_counter()
    cpu = time.process_time() - cpu
    th.join()
    assert back - box["t"] < REACTION_S, f"resumed {back - box['t']:.3f} s after the nudge"
    assert cpu < 0.05, f"{cpu:.3f} s of CPU while paused"
    assert ctl.paused_s >= 0.4 and ctl.seq == 2 and not ctl.pause


def test_a_stop_while_paused_ends_the_hold_within_100ms(tmp_path, nudge):
    write(tmp_path, seq=1, pause=True)
    ctl = Control(tmp_path)
    th, box = later(0.3, lambda: (write(tmp_path, seq=2, stop=True), nudge())[1])
    with pytest.raises(Stopped):
        ctl.safe_point()
    back = time.perf_counter()
    th.join()
    assert back - box["t"] < REACTION_S


def test_a_stop_while_running_raises_at_the_next_safe_point(tmp_path, nudge):
    ctl = Control(tmp_path)
    ctl.safe_point()
    write(tmp_path, seq=1, stop=True)
    nudge()
    with pytest.raises(Stopped):
        ctl.safe_point()


@POSIX
def test_a_signal_delivered_to_another_thread_wakes_the_main_threads_hold(tmp_path, nudge):
    import signal
    write(tmp_path, seq=1, pause=True)
    ctl = Control(tmp_path)

    def to_this_thread():
        write(tmp_path, seq=2, pause=False)
        t = time.perf_counter()
        signal.pthread_kill(threading.get_ident(), signal.SIGUSR1)   # the C handler runs here, not on the main thread
        return t
    th, box = later(0.3, to_this_thread)
    ctl.safe_point()
    back = time.perf_counter()
    th.join()
    assert back - box["t"] < REACTION_S


@POSIX
def test_a_worker_thread_holds_at_its_safe_point_and_resumes_on_a_nudge(tmp_path, nudge):
    write(tmp_path, seq=1, pause=True)
    ctl = Control(tmp_path)                              # on the main thread; the work runs on another
    done = {}
    worker = threading.Thread(target=lambda: (ctl.safe_point(), done.setdefault("t", time.perf_counter())))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    write(tmp_path, seq=2, pause=False)
    t = nudge()
    worker.join(5)
    assert done["t"] - t < REACTION_S


@POSIX
def test_control_off_the_main_thread_is_an_error_not_polling():
    code = ("import threading\nfrom oarbank_sdk.control import Control\nerr = []\n"
            "def make():\n    try:\n        Control('.')\n    except RuntimeError as e:\n        err.append(str(e))\n"
            "t = threading.Thread(target=make); t.start(); t.join(); print(err[0])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, encoding="utf-8").stdout
    assert "main thread" in out


@WINDOWS
def test_a_nudge_before_the_check_waits_in_the_event(tmp_path, nudge):
    ctl = Control(tmp_path)
    write(tmp_path, seq=1, threads=4)
    nudge()                                              # set; the next check sees it without blocking
    ctl.check()
    assert ctl.threads == 4
    write(tmp_path, seq=2, threads=5)
    ctl.check()
    assert ctl.threads == 4, "one nudge, one read"


@WINDOWS
def test_without_the_event_only_the_first_document_applies(tmp_path, monkeypatch):
    monkeypatch.delenv(control.ENV_CONTROL_EVENT, raising=False)
    write(tmp_path, seq=1, pause=True)
    ctl = Control(tmp_path)
    with pytest.raises(RuntimeError, match=control.ENV_CONTROL_EVENT):
        ctl.safe_point()


RUNNER = """
import sys, time
sys.path.insert(0, sys.argv[2])
from oarbank_sdk.control import Control, Stopped
ctl = Control(sys.argv[1])
print("ready", flush=True)
try:
    while True:
        ctl.check()
        if ctl.pause:
            print("paused", flush=True)
            ctl.safe_point()
            print("resumed", flush=True)
        t = time.perf_counter()
        while time.perf_counter() - t < 0.005:           # a work chunk between safe points
            pass
except Stopped:
    print("stopped", flush=True)
    sys.exit(3)
"""


def test_a_runner_process_reacts_to_the_agents_nudges(tmp_path):
    """A separate runner process, started and nudged the way the agent does it on this OS."""
    write(tmp_path, seq=0)
    n = _Nudge()
    src = os.path.dirname(os.path.dirname(control.__file__))
    p = subprocess.Popen(n.argv([sys.executable, "-I", "-c", RUNNER, str(tmp_path), src]), stdout=subprocess.PIPE, text=True,
                         env={**os.environ, **n.env()}, **n.popen_kwargs(), encoding="utf-8")
    try:
        assert p.stdout.readline().strip() == "ready"
        took = {}
        for seq, doc, expect in [(1, {"pause": True}, "paused"), (2, {"pause": False}, "resumed"),
                                 (3, {"pause": True}, "paused"), (4, {"stop": True}, "stopped")]:
            time.sleep(0.2)
            write(tmp_path, seq=seq, **doc)
            t = time.perf_counter()
            n.send(p)
            assert p.stdout.readline().strip() == expect
            took[f"{seq}:{expect}"] = time.perf_counter() - t
        assert p.wait(5) == 3
        print("reaction latency:", {k: f"{v * 1000:.1f} ms" for k, v in took.items()})
        assert max(took.values()) < 0.2, took
    finally:
        p.kill()
        n.close()
