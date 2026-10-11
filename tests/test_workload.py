"""The workload supervisor shim and event helpers (oarbank_sdk.workload, spec/workload-protocol.md), driven for real: a
Supervisor runs a synthetic daemon as the agent runs a workload (the leader of a new process group, started with SIGUSR1
ignored, its control document replaced and nudged)."""
import copy
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from oarbank_sdk import workload as W
from oarbank_sdk import workload_protocol as wp
from oarbank_sdk.conformance import _Nudge

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and signals")
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows process snapshots and suspension")

DAEMON = textwrap.dedent('''
    """A stand-in third-party daemon: a work unit every 0.1 s, a line per unit; SIGTERM ends it."""
    import argparse, os, signal, subprocess, sys, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int)
    ap.add_argument("--spawn", action="store_true")
    ap.add_argument("--exit-after", type=float)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (print("daemon: stopping", flush=True), sys.exit(0)))
    if a.spawn:                          # a helper of its own, which a pause must reach too
        kid = subprocess.Popen([sys.executable, "-c", "import time\\nwhile True: time.sleep(0.05)"])
        open("grandchild.pid", "w").write(str(kid.pid))
    open("daemon.pid", "w").write(str(os.getpid()))
    t0, i = time.monotonic(), 0
    while a.exit_after is None or time.monotonic() - t0 < a.exit_after:
        i += 1
        print(f"unit {i} threads={a.threads}", flush=True)
        time.sleep(0.1)
    sys.exit(3)
''')

WORKER = textwrap.dedent('''
    import json, os, sys
    from oarbank_sdk.workload import Supervisor
    conf = json.loads(os.environ["TEST_SUPERVISOR"])
    def on_line(line):
        if line.startswith("unit "):
            return {"type": "progress", "event": "unit", "n": 1}
    def health():
        return os.path.exists("daemon.pid"), "the daemon wrote its pid"
    sup = Supervisor([sys.executable, "-I", os.path.join(os.environ["TEST_DIR"], "daemon.py"), *conf.get("args", [])],
                     threads=conf.get("threads"), stop_timeout_s=conf.get("stop_timeout_s", 5), line_handler=on_line,
                     health=health, restart_on_exit=conf.get("restart_on_exit", False))
    sys.exit(sup.main())
''')


class Run:
    """One workload run as the agent starts it."""

    def __init__(self, tmp: Path, **conf):
        self.dir = tmp
        (tmp / "daemon.py").write_text(DAEMON, encoding="utf-8")
        (tmp / "worker.py").write_text(WORKER, encoding="utf-8")
        self.run = tmp / "run"
        self.run.mkdir()
        self.private = tmp / "private"
        self.private.mkdir()
        self.seq = 0
        self.doc = {"seq": 0}
        self.write()
        self.nudge = _Nudge()
        self.env = {**os.environ, "TEST_SUPERVISOR": json.dumps(conf), "TEST_DIR": str(tmp),
                    W.ENV_RUN: str(self.run), W.ENV_CONTROL_FILE: str(self.private / "control.json"),
                    W.ENV_EVENTS_FILE: str(self.run / "events.ndjson"), wp.ENV_WORKLOAD: "worker", **self.nudge.env()}
        argv = [sys.executable, "-I", str(tmp / "worker.py")]
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        self.p = subprocess.Popen(self.nudge.argv(argv + ["run"]), cwd=self.run, env=self.env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **kw, **self.nudge.popen_kwargs())

    def write(self):
        tmp = self.private / "control.json.tmp"
        tmp.write_text(json.dumps(self.doc), encoding="utf-8")
        os.replace(tmp, self.private / "control.json")

    def control(self, nudge: bool = True, **change):
        self.seq += 1
        self.doc = {**self.doc, **change, "seq": self.seq}
        self.write()
        if nudge:
            self.nudge.send(self.p)

    def status_file(self) -> dict:
        try:
            return json.loads((self.run / W.STATUS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status_op(self) -> dict:
        out = subprocess.run([sys.executable, "-I", str(self.dir / "worker.py"), "status"], cwd=self.run, env=self.env,
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return wp.Status.model_validate(json.loads(out.stdout.strip().splitlines()[-1])).model_dump()

    def pid_file(self, name: str, timeout: float = 10) -> int:
        return int(until(lambda: (self.run / name).read_text() if (self.run / name).exists() else None, timeout))

    def close(self):
        if self.p.poll() is None:
            if os.name != "nt":
                os.killpg(self.p.pid, signal.SIGKILL)
            else:
                self.p.kill()
            self.p.wait(10)
        self.nudge.close()


def until(fn, timeout: float = 10, every: float = 0.05):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout} s")


def state(pid: int) -> str:
    """The process's ps state ('T' when stopped), '' when it is gone."""
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return out


@pytest.fixture
def run(tmp_path):
    runs = []

    def start(**conf):
        d = tmp_path / f"r{len(runs)}"
        d.mkdir()
        r = Run(d, **conf)
        runs.append(r)
        return r
    yield start
    for r in runs:
        r.close()


@POSIX
def test_a_pause_stops_the_daemon_and_its_children_but_never_the_supervisor(run):
    r = run(args=["--spawn"])
    daemon, kid = r.pid_file("daemon.pid"), r.pid_file("grandchild.pid")
    r.control(pause=True)
    until(lambda: state(daemon).startswith("T") and state(kid).startswith("T"), 5)
    assert not state(r.p.pid).startswith("T")
    until(lambda: r.status_file().get("paused") is True, 5)
    assert r.status_op()["paused"] is True
    r.control(pause=False)
    until(lambda: not state(daemon).startswith("T") and not state(kid).startswith("T"), 5)
    until(lambda: r.status_file().get("paused") is False, 5)


@POSIX
def test_a_pause_is_seen_without_a_nudge_by_the_re_read(run):
    r = run()
    daemon = r.pid_file("daemon.pid")
    r.control(nudge=False, pause=True)                  # after an agent restart there may be no nudge
    until(lambda: state(daemon).startswith("T"), W.POLL_S + 3)


@POSIX
def test_a_thread_limit_restarts_the_daemon_with_its_flag(run):
    r = run(threads="--threads={n}")
    first = r.pid_file("daemon.pid")
    until(lambda: r.status_file().get("daemon_pid") == first, 5)
    r.control(threads=3)
    until(lambda: r.status_file().get("threads") == 3 and r.status_file().get("daemon_pid") != first, 10)
    st = r.status_file()
    assert st["restarts"] == 1 and st["daemon_running"]
    assert state(first) == ""                            # the old daemon is gone
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(st["daemon_pid"])], capture_output=True, text=True).stdout
    assert "--threads=3" in cmd
    assert r.status_op()["threads"] == 3


@POSIX
def test_a_stop_ends_the_daemon_and_its_children_and_the_supervisor_exits_0(run):
    r = run(args=["--spawn"], stop_timeout_s=5)
    daemon, kid = r.pid_file("daemon.pid"), r.pid_file("grandchild.pid")
    t = time.monotonic()
    r.control(stop=True)
    assert r.p.wait(10) == 0
    assert time.monotonic() - t < 5
    until(lambda: state(daemon) == "" and state(kid) == "", 5)
    assert r.status_op()["healthy"] is False             # the daemon is gone


@POSIX
def test_a_stop_while_paused_still_ends_the_daemon(run):
    r = run(stop_timeout_s=5)
    daemon = r.pid_file("daemon.pid")
    r.control(pause=True)
    until(lambda: state(daemon).startswith("T"), 5)
    r.control(stop=True)
    assert r.p.wait(10) == 0
    until(lambda: state(daemon) == "", 5)


@POSIX
def test_sigterm_to_the_group_is_a_stop(run):
    r = run()
    r.pid_file("daemon.pid")
    os.killpg(r.p.pid, signal.SIGTERM)
    assert r.p.wait(10) == 0


@POSIX
def test_a_daemon_that_exits_on_its_own_ends_the_run_non_zero(run):
    r = run(args=["--exit-after", "0.5"])
    assert r.p.wait(15) != 0
    events = [json.loads(x) for x in (r.run / "events.ndjson").read_text().splitlines()]
    assert any(e["type"] == "error" and "exited with 3" in e["message"] for e in events)


@POSIX
def test_restart_on_exit_starts_the_daemon_again(run):
    r = run(args=["--exit-after", "0.3"], restart_on_exit=True)
    until(lambda: r.status_file().get("restarts", 0) >= 1, 15)
    assert r.p.poll() is None
    r.control(stop=True)
    assert r.p.wait(10) == 0


@POSIX
def test_the_status_op_is_healthy_while_the_daemon_runs_and_the_lines_become_events(run):
    r = run()
    r.pid_file("daemon.pid")
    until(lambda: r.status_file().get("daemon_running"), 5)
    st = r.status_op()
    assert st["healthy"] is True and st["paused"] is False and st["gpu_duty_supported"] is False
    assert st["detail"] == "the daemon wrote its pid"
    lines = until(lambda: (r.run / "events.ndjson").read_text().splitlines() if (r.run / "events.ndjson").exists() else None)
    for line in lines:
        wp.Event.model_validate(json.loads(line))
    assert json.loads(lines[0])["event"] == "unit"


def test_a_stale_status_file_is_unhealthy(tmp_path, monkeypatch):
    monkeypatch.setenv(W.ENV_RUN, str(tmp_path))
    sup = W.Supervisor(["true"])
    assert sup.status()["healthy"] is False              # nothing written yet
    (tmp_path / W.STATUS_FILE).write_text(json.dumps({"t": time.time() - 60, "interval_s": 2, "daemon_pid": os.getpid(),
                                                      "daemon_running": True, "paused": False}))
    st = sup.status()
    assert st["healthy"] is False and "old" in st["detail"]
    (tmp_path / W.STATUS_FILE).write_text(json.dumps({"t": time.time(), "interval_s": 2, "daemon_pid": os.getpid(),
                                                      "daemon_running": True, "paused": False}))
    assert sup.status()["healthy"] is True
    wp.Status.model_validate(sup.status())


def test_an_unknown_op_is_a_usage_error(capsys):
    assert W.Supervisor(["true"]).main(["worker.py", "fly"]) == W.EXIT_USAGE
    assert W.Supervisor(["true"]).main(["worker.py"]) == W.EXIT_USAGE


def test_the_supervisor_refuses_a_bad_configuration():
    for bad in ({"pause": "freeze"}, {"threads": "--threads"}, {"gpu_duty": 0.5}):
        with pytest.raises(ValueError):
            W.Supervisor(["true"], **bad)
    with pytest.raises(ValueError):
        W.Supervisor([])


def test_the_event_helpers_write_valid_protocol_lines(tmp_path, monkeypatch):
    events = tmp_path / "events.ndjson"
    monkeypatch.setenv(W.ENV_EVENTS_FILE, str(events))
    assert W.progress("unit", 2, block=7)
    assert W.report({"ref": "u-1", "amount": 1200})
    assert W.error("the peer went away")
    assert W.log("connected", "info")
    lines = events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    got = [wp.Event.model_validate(json.loads(x)).root for x in lines]
    assert [e.type for e in got] == ["progress", "report", "error", "log"]
    assert got[0].n == 2 and json.loads(lines[0])["block"] == 7 and got[1].data == {"ref": "u-1", "amount": 1200}


def test_a_report_over_4_kib_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    events = tmp_path / "events.ndjson"
    monkeypatch.setenv(W.ENV_EVENTS_FILE, str(events))
    with pytest.raises(ValueError):
        W.report({"blob": "x" * W.REPORT_DATA_MAX})
    assert not events.exists()
    with pytest.raises(TypeError):
        W.report(["not", "an", "object"])
    W.report({"blob": "x" * (W.REPORT_DATA_MAX - 20)})   # just inside: {"blob":"..."} is 11 bytes more
    with pytest.raises(Exception):
        wp.Event.model_validate({"t": 1, "type": "report", "data": {"blob": "x" * W.REPORT_DATA_MAX}})


def test_without_an_events_file_the_helpers_do_nothing(monkeypatch):
    monkeypatch.delenv(W.ENV_EVENTS_FILE, raising=False)
    assert W.progress("unit") is False


def test_secrets_come_from_the_secrets_file(tmp_path, monkeypatch):
    monkeypatch.delenv(W.ENV_SECRETS_FILE, raising=False)
    assert W.secrets() == {}
    f = tmp_path / "secrets.json"
    f.write_text(json.dumps({"signing_key": "k"}))
    monkeypatch.setenv(W.ENV_SECRETS_FILE, str(f))
    assert W.secrets() == {"signing_key": "k"}


def test_the_constants_match_the_protocol_models():
    assert (W.ENV_RUN, W.ENV_CONTROL_FILE, W.ENV_EVENTS_FILE, W.ENV_SECRETS_FILE, W.ENV_CONTROL_EVENT) == (
        wp.ENV_RUN, wp.ENV_CONTROL_FILE, wp.ENV_EVENTS_FILE, wp.ENV_SECRETS_FILE, wp.ENV_CONTROL_EVENT)
    assert W.REPORT_DATA_MAX == wp.REPORT_DATA_MAX and W.EXIT_USAGE == wp.EXIT_USAGE
    assert W.POLL_S <= wp.CONTROL_REREAD_S


def test_the_workload_module_is_stdlib_only():
    """Modules vendor it: it imports nothing outside the standard library."""
    import ast
    tree = ast.parse(Path(W.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_descendants_finds_a_grandchild_and_never_the_caller(tmp_path):
    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"
    p = subprocess.Popen([sys.executable, "-c", code])
    try:
        found = until(lambda: W.descendants(p.pid), 10)
        assert len(found) == 1 and os.getpid() not in found and p.pid not in found
        assert os.getpid() not in W.descendants(os.getppid())
    finally:
        for pid in W.descendants(p.pid):
            W._terminate(pid) if os.name == "nt" else os.kill(pid, signal.SIGKILL)
        p.kill()
        p.wait()


@WINDOWS
def test_windows_snapshot_lists_this_process_with_its_parent():
    pairs = dict(W._toolhelp_pairs())
    assert pairs.get(os.getpid()) == os.getppid()


@WINDOWS
def test_windows_freeze_and_thaw_a_child():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        frozen = W.freeze(p.pid)
        assert p.pid in frozen
        W.thaw(frozen)
        assert W.alive(p.pid)
    finally:
        p.kill()
        p.wait()


# ---------------------------------------------------------------------------- the manifest's rule 24

def workload_doc(**w) -> dict:
    import tomllib
    d = tomllib.loads((Path(__file__).parents[1] / "examples" / "toy" / "oarbank-module.toml").read_text(encoding="utf-8"))
    d["requires"]["core"] = ">=2.10,<3"
    d["requires"]["workload_protocol"] = [1]
    d["workloads"] = [{"name": "worker", "exec": ["python", "-I", "{bundle}/worker.py"], **w}]
    return d


def refused(d: dict, needle: str):
    from pydantic import ValidationError
    from oarbank_sdk import manifest as mf
    with pytest.raises(ValidationError) as e:
        mf.Manifest.model_validate(d)
    assert needle in str(e.value), str(e.value)


def test_a_workload_needs_its_protocol_and_core_2_10():
    from oarbank_sdk import manifest as mf
    m = mf.Manifest.model_validate(workload_doc(control=["freeze_ok"]))
    assert m.workload("worker").effective_pause_tolerance_s() == 20
    assert mf.Manifest.model_validate(workload_doc()).workload("worker").effective_pause_tolerance_s() == 0
    d = workload_doc()
    d["requires"]["workload_protocol"] = []
    refused(d, "requires.workload_protocol")
    d = workload_doc()
    d["requires"]["core"] = ">=2.5,<3"
    refused(d, "2.10")


def test_a_secret_only_a_workload_lists_reaches_something():
    from oarbank_sdk import manifest as mf
    d = workload_doc(secrets=["signing_key"], identity="per_node")
    d["secrets"] = [{"name": "signing_key", "description": "the node's key"}]
    assert mf.Manifest.model_validate(d).workload("worker").secrets == ["signing_key"]
    refused(workload_doc(identity="per_node"), "lists at least one secret")
    refused(workload_doc(secrets=["nope"]), "not declared in [[secrets]]")


def test_workload_rules_refuse_what_cannot_work():
    refused(workload_doc(pause_tolerance_s=30), "needs control cooperative_pause or freeze_ok")
    refused(workload_doc(control=["freeze_ok", "freeze_ok"]), "listed twice")
    refused(workload_doc(gpu={"use": "shared"}), "sandbox.devices.gpu")
    refused(workload_doc(restart={"backoff_initial_s": 60, "backoff_max_s": 10}), "backoff_max_s")
    refused(workload_doc(reports=True), "workload.report")
    d = workload_doc()
    d["workloads"].append(dict(d["workloads"][0]))
    refused(d, "unique")


def test_a_workload_serves_an_inbound_listener_as_its_only_target():
    from oarbank_sdk import manifest as mf
    d = workload_doc(listeners=["peer"])
    d["sandbox"] = {"net": {"inbound": [{"name": "peer", "port_hint": 9000}]}}
    man = mf.Manifest.model_validate(d)
    assert [(w, n) for w, n, _ in man.listener_targets()] == [("workload 'worker'", ["peer"])]
    assert man.listener_service("peer").name == "worker"
    assert ("workloads", (2, 10)) in man.core_keys_used() and ("sandbox.net.inbound", (2, 10)) in man.core_keys_used()
    # rule 22: exactly one target; rule 24: a workload is handed connections, never a listening socket
    two = copy.deepcopy(d)
    two["services"] = [{"name": "node", "exec": ["python", "-I", "{bundle}/node.py"], "endpoint": True,
                        "lifecycle": "always", "listeners": ["peer"]}]
    two["requires"]["service_protocol"] = [1]
    refused(two, "a listener needs exactly one service or workload")
    fd = copy.deepcopy(d)
    fd["sandbox"]["net"]["inbound"][0]["handover"] = "listen_fd"
    refused(fd, "never a listening socket")
    refused(workload_doc(listeners=["peer"]), "not [sandbox].net.inbound entries")
