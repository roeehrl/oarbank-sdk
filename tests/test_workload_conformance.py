"""The conformance kit's workload suite (spec/conformance.md, spec/workload-protocol.md): it plays the agent for each
declared workload and fails one that leaves its process group, listens, or ignores a stop."""
import os
import shutil
from pathlib import Path

import pytest

from oarbank_sdk import conformance as C
from oarbank_sdk.cli import main

FIX = Path(__file__).parent / "fixtures" / "workloads"
POSIX = pytest.mark.skipif(os.name == "nt", reason="the fixtures' escaping daemon forks (POSIX)")


def copy(tmp_path, name="good") -> Path:
    d = tmp_path / name
    shutil.copytree(FIX / name, d, ignore=shutil.ignore_patterns("__pycache__"))
    return d


def names(rep, status) -> set[str]:
    return {c.name for c in rep.checks if c.status == status}


def strays(rep_token_free=True) -> list:
    """Processes some earlier run left carrying a conformance token (none may survive the suite)."""
    import subprocess
    import sys
    if sys.platform == "darwin":
        out = subprocess.run(["ps", "-A", "-E", "-ww", "-o", "pid=,command="], capture_output=True, text=True).stdout
        return [line[:80] for line in out.splitlines() if f"{C.TOKEN_ENV}=" in line and "ps -A" not in line]
    if sys.platform.startswith("linux"):
        found = []
        for d in os.listdir("/proc"):
            try:
                if d.isdigit() and f"{C.TOKEN_ENV}=".encode() in Path(f"/proc/{d}/environ").read_bytes():
                    found.append(d)
            except OSError:
                pass
        return found
    return []


@POSIX
@pytest.mark.parametrize("sandbox", [True, False], ids=["sandboxed", "unconfined"])
def test_a_well_behaved_workload_passes_every_check(sandbox):
    rep = C.conform_workloads(FIX / "good", sandbox=sandbox)
    assert rep.ok, rep.text()
    passed = names(rep, "pass")
    for check in ("keeps running after it starts", "`status` answers healthy within health.timeout_s",
                  "shows a cooperative pause within 3 s", "resumes within 3 s", "stays in its process container",
                  "never listens", "its events are workload-event-1 lines", "a stop ends it within stop_timeout_s"):
        assert f"workload worker: {check}" in passed, rep.text()
    assert not strays()


@POSIX
def test_a_daemon_that_calls_setsid_and_forks_twice_fails_and_is_cleaned_up():
    rep = C.conform_workloads(FIX / "escapes")
    assert not rep.ok
    assert names(rep, "fail") == {"workload worker: stays in its process container"}, rep.text()
    assert "left the leader's process group" in next(c.detail for c in rep.checks if c.status == "fail")
    assert not strays()                                  # the kit killed the escaped process too


@POSIX
def test_a_workload_that_listens_fails(tmp_path):
    d = copy(tmp_path)
    code = (d / "daemon.py").read_text(encoding="utf-8").replace(
        "i = 0\n",
        "import socket\ns = socket.socket()\ns.bind(('127.0.0.1', 0))\ns.listen()\ni = 0\n", 1)
    (d / "daemon.py").write_text(code, encoding="utf-8", newline="\n")
    for sandbox in (True, False):                         # refused by the sandbox, or caught holding the socket
        rep = C.conform_workloads(d, sandbox=sandbox)
        assert "workload worker: never listens" in names(rep, "fail"), rep.text()
    assert not strays()


@POSIX
def test_a_workload_deaf_to_its_control_document_fails_the_pause_and_the_stop(tmp_path):
    d = copy(tmp_path)
    (d / "worker.py").write_text(
        "import json, sys, time\n"
        "if sys.argv[1] == 'status':\n"
        "    print(json.dumps({'healthy': True, 'detail': 'fine'}))\n"
        "    sys.exit(0)\n"
        "while True:\n"
        "    time.sleep(0.2)\n", encoding="utf-8", newline="\n")
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace("stop_timeout_s = 10", "stop_timeout_s = 2")
    (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    rep = C.conform_workloads(d)
    failed = names(rep, "fail")
    assert {"workload worker: shows a cooperative pause within 3 s",
            "workload worker: a stop ends it within stop_timeout_s"} <= failed, rep.text()
    assert not strays()


@POSIX
def test_a_workload_that_exits_at_once_fails_and_the_rest_is_not_run(tmp_path):
    d = copy(tmp_path)
    (d / "worker.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8", newline="\n")
    rep = C.conform_workloads(d)
    assert names(rep, "fail") == {"workload worker: keeps running after it starts"}, rep.text()


@POSIX
def test_the_cli_runs_only_the_workload_suite(capsys):
    assert main(["conform", str(FIX / "good"), "--only", "workload"]) == 0
    out = capsys.readouterr().out
    assert "workload worker: stays in its process container" in out and "protocol" not in out
    assert main(["conform", str(FIX / "escapes"), "--only", "workload"]) == 1


def test_a_module_without_workloads_skips_the_suite():
    rep = C.conform_workloads(Path(__file__).parents[1] / "examples" / "toy")
    assert rep.ok and names(rep, "skip") == {"workloads"}
