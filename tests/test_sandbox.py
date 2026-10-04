"""The module sandbox (spec/sandbox.md): the generator, its golden profiles, real confinement on macOS, the
[sandbox] manifest section and the broker client."""
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import broker
from oarbank_sdk import manifest as mf
from oarbank_sdk import sandbox as S

GOLDEN = Path(__file__).parents[1] / "spec" / "sandbox" / "backends" / "macos-golden"
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")


def test_golden_profiles_are_fresh():
    """The agent's Rust renderer (oarbank-core) is checked against the same files."""
    for c in json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8")):
        want = (GOLDEN / f"{c['name']}.sb").read_text(encoding="utf-8")
        got = S.render_text(c["kind"], c["ro"], c["rw"], c["links"], c["net"], c["broker"], c["gpu"], c.get("proxy_port"),
                            c.get("exec_rw", False))
        assert got == want, c["name"]


@pytest.mark.skipif(os.name == "nt", reason="Seatbelt profiles take POSIX paths")
def test_paths_are_parameters_resolved_with_their_links(tmp_path):
    real = tmp_path / "real"
    (real / "bin").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    text, params = S.render(S.Policy(module="dev.x.y", ro=[tmp_path / "link" / "bin"], rw=[tmp_path / "w"], exe=None))
    p = dict(params)
    assert p["RO_0"] == os.path.realpath(real / "bin") and "LINK_0" in p and p["LINK_0"].endswith("/link")
    assert str(tmp_path) not in text                     # paths never enter the text
    with pytest.raises(S.SandboxError):
        S.launch_argv("/p.sb", params, ["relative/python"])


def _probe(tmp_path, policy_kw, code, env=None):
    mod, work, data, outside = (tmp_path / n for n in ("mod", "work", "data", "outside"))
    for d in (mod, work / "tmp", data, outside):
        d.mkdir(parents=True, exist_ok=True)
    (outside / "secret").write_text("s", encoding="utf-8", newline="\n")
    (mod / "probe.py").write_text(code, encoding="utf-8", newline="\n")
    text, params = S.render(S.node_policy("dev.test.probe", mod, work, data, **policy_kw))
    prof = S.write_profile(text, tmp_path / "p.sb")
    p = subprocess.run(S.launch_argv(prof, params, [sys.executable, "-I", str(mod / "probe.py")]), cwd=work, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work / "tmp"), "REAL_HOME": os.path.expanduser("~"),
                            "T": str(tmp_path), **(env or {})}, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr[-500:]
    return json.loads(p.stdout)


PROBE = r'''
import json, os, socket, subprocess
H, T = os.environ["REAL_HOME"], os.environ["T"]
r = {}
def t(n, f):
    try: f(); r[n] = True
    except Exception: r[n] = False
def w(p): open(p, "w", encoding="utf-8", newline="\n").write("x"); os.remove(p)
t("list_ssh", lambda: os.listdir(H + "/.ssh"))
t("list_home", lambda: os.listdir(H))
t("write_desktop", lambda: w(H + "/Desktop/.oarbank_canary_%d" % os.getpid()))
t("read_outside", lambda: open(T + "/outside/secret", encoding="utf-8").read())
t("write_module", lambda: w(T + "/mod/x"))
t("write_tmp_shared", lambda: w("/tmp/.oarbank_canary_%d" % os.getpid()))
t("write_work", lambda: w(T + "/work/x"))
t("write_data", lambda: w(T + "/data/x"))
t("child_ls_ssh", lambda: subprocess.run(["/bin/ls", H + "/.ssh"], check=True, capture_output=True))
t("hardlink_out", lambda: os.link(T + "/outside/secret", T + "/data/hl"))
t("tcp", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3).close())
t("launchctl", lambda: subprocess.run(["/bin/launchctl", "submit", "-l", "oarbank.canary", "--", "/usr/bin/true"], check=True, capture_output=True))
print(json.dumps(r))
'''


@darwin
def test_a_sandboxed_process_reaches_only_its_own_directories(tmp_path):
    r = _probe(tmp_path, {}, PROBE)
    assert r["write_work"] and r["write_data"]
    escaped = [k for k in ("list_ssh", "list_home", "write_desktop", "read_outside", "write_module", "write_tmp_shared",
                           "child_ls_ssh", "hardlink_out", "tcp", "launchctl") if r[k]]
    assert not escaped, escaped


@pytest.mark.skipif(os.name == "nt", reason="Seatbelt profiles take POSIX paths")
def test_interpreter_roots_keep_the_names_an_interpreter_uses_through_links(tmp_path):
    """A Homebrew CPython names its prefix, library and executable by its opt/ link whichever way it was started (and
    a venv on it names its base the same way): the roots keep those spellings, so the profile gives their hops metadata,
    while everything they grant to read is the resolved keg."""
    keg = tmp_path / "Cellar" / "py" / "1"
    (keg / "lib" / "python3").mkdir(parents=True)
    (keg / "bin").mkdir()
    (keg / "bin" / "python3").write_text("", encoding="utf-8")
    (tmp_path / "opt").mkdir()
    (tmp_path / "opt" / "py").symlink_to("../Cellar/py/1")
    opt = tmp_path / "opt" / "py"
    lay = {"base_prefix": str(opt), "prefix": str(opt), "executable": str(keg / "bin" / "python3"),
           "paths": [str(opt / "lib" / "python3")], "names": [str(opt / "bin" / "python3")]}
    roots = S.interpreter_roots(layout=lay)
    real = os.path.realpath(keg)
    assert roots[0] == real and str(opt) in roots and str(opt / "bin" / "python3") in roots
    _, params = S.render(S.Policy(module="m", ro=roots))
    assert str(opt) in {v for k, v in params if k.startswith("LINK_")}
    assert all(v == real or v.startswith(real + "/") for k, v in params if k.startswith("RO_"))


@darwin
def test_an_interpreter_behind_symlink_hops_starts_and_reads_no_more(tmp_path):
    """Homebrew's layout: bin/python3 and opt/python@X are symlinks into the Cellar. Its CPython realpath()s its own
    location at startup, strictly (an lstat refused anywhere on the way ends it), so every link on argv[0]'s way and
    the directories above it need metadata; their directories stay unlistable and a file beside a link unreadable."""
    brew, mod, work = tmp_path / "brew", tmp_path / "mod", tmp_path / "work"
    for d in (brew / "Cellar" / "python@9" / "9.0" / "bin", brew / "opt", brew / "bin", mod, work):
        d.mkdir(parents=True)
    (brew / "Cellar" / "python@9" / "9.0" / "bin" / "python3").symlink_to(os.path.realpath(sys.executable))
    (brew / "opt" / "python@9").symlink_to("../Cellar/python@9/9.0")
    (brew / "bin" / "python3").symlink_to("../opt/python@9/bin/python3")
    (brew / "bin" / "secret").write_text("s", encoding="utf-8", newline="\n")
    (mod / "probe.py").write_text(
        "import json, os, sys\nB = os.environ['B']\nr = {'exe': os.path.realpath(sys.executable, strict=True)}\n"
        "for n, f in (('list_bin', lambda: os.listdir(B + '/bin')), ('read_beside', lambda: open(B + '/bin/secret').read())):\n"
        "    try: f(); r[n] = True\n    except OSError: r[n] = False\nprint(json.dumps(r))\n", encoding="utf-8", newline="\n")
    py = str(brew / "bin" / "python3")
    text, params = S.render(S.Policy(module="dev.test.probe", ro=[mod, *S.interpreter_roots()], rw=[work], exe=py))
    prof = S.write_profile(text, tmp_path / "p.sb")
    p = subprocess.run(S.launch_argv(prof, params, [py, "-I", str(mod / "probe.py")]), cwd=work, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(work), "B": str(brew)}, capture_output=True, text=True,
                       encoding="utf-8")
    assert p.returncode == 0, p.stderr[-500:]
    assert json.loads(p.stdout) == {"exe": os.path.realpath(sys.executable), "list_bin": False, "read_beside": False}


@darwin
def test_egress_is_a_grant(tmp_path):
    r = _probe(tmp_path, {"sandbox": mf.SandboxSection(net={"mode": "egress-any"})}, PROBE)
    assert r["tcp"] and not r["list_ssh"] and not r["read_outside"]


@darwin
def test_the_parent_can_tell_a_process_is_sandboxed(tmp_path):
    text, params = S.render(S.node_policy("dev.test.probe", tmp_path, tmp_path, None))
    prof = S.write_profile(text, tmp_path / "p.sb")
    p = subprocess.Popen(S.launch_argv(prof, params, ["/bin/sleep", "5"]), cwd=tmp_path)
    try:
        import time
        for _ in range(50):
            if S.is_sandboxed(p.pid):
                break
            time.sleep(0.05)
        assert S.is_sandboxed(p.pid) and not S.is_sandboxed(os.getpid())
    finally:
        p.kill()


def test_sandbox_section_is_validated():
    s = mf.SandboxSection(net={"mode": "egress-allowlist", "allow": ["api.example.org", "*.files.example.org:8443"]},
                          tools=[{"id": "renderer4", "trust": "code-exec"}], devices={"gpu": "compute"},
                          containers=[{"image": "docker.io/o/t:1@sha256:" + "a" * 64, "platform": "linux/amd64"}])
    assert s.requests() and not mf.SandboxSection().requests() and mf.SandboxSection(exec_writable=True).requests()
    for bad in ({"net": {"mode": "egress-allowlist"}},                       # no allow list
                {"net": {"mode": "egress-any", "allow": ["x.org"]}},         # allow only with the allowlist mode
                {"net": {"mode": "egress-allowlist", "allow": ["10.0.0.1"]}},  # never IP addresses
                {"containers": [{"image": "docker.io/o/t:latest"}]}):        # digest-pinned only
        with pytest.raises(ValidationError):
            mf.SandboxSection(**bad)


def test_the_containers_pool_is_the_agents():
    import tomllib
    doc = tomllib.loads((Path(__file__).parents[1] / "examples" / "toy" / "oarbank-module.toml").read_text(encoding="utf-8"))
    doc["stages"][0]["requires"]["pools"] = {"containers": 1}
    with pytest.raises(ValidationError, match="containers"):
        mf.Manifest.model_validate(doc)                  # no container grant, so nothing provides the pool
    doc["sandbox"] = {"containers": [{"image": "docker.io/o/t:1@sha256:" + "b" * 64}]}
    assert mf.Manifest.model_validate(doc).sandbox.containers


def _broker_endpoint(answer: dict, seen: dict):
    """A one-shot fake broker on this OS's endpoint kind (spec/sandbox.md, "Containers"): a unix socket on POSIX, a
    named pipe on Windows. Returns (uri, cleanup)."""
    if os.name == "nt":
        from multiprocessing.connection import Listener
        name = f"oarbank-test-broker-{os.getpid()}"
        srv = Listener(rf"\\.\pipe\{name}", family="AF_PIPE")

        def serve():
            with srv.accept() as c:
                seen["req"] = json.loads(c.recv_bytes())
                c.send_bytes(json.dumps(answer).encode() + b"\n")
        threading.Thread(target=serve, daemon=True).start()
        return f"npipe://./pipe/{name}", srv.close
    path = f"/tmp/mfb-{os.getpid()}.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        seen["req"] = json.loads(c.makefile().readline())
        c.sendall(json.dumps(answer).encode() + b"\n")
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    return "unix:" + path, lambda: (srv.close(), os.unlink(path))


def test_broker_client_round_trip(tmp_path, monkeypatch):
    seen = {}
    uri, cleanup = _broker_endpoint({"ok": True, "exit_code": 3, "stdout_tail": "hi", "duration_s": 1.5}, seen)
    monkeypatch.setenv(broker.ENV, uri)
    try:
        r = broker.run("docker.io/o/t:1@sha256:" + "c" * 64, ["tool", "-x"], mounts=[broker.Mount("in", "/in", ro=True)])
    finally:
        cleanup()
    assert r.exit_code == 3 and r.stdout_tail == "hi"
    assert seen["req"]["op"] == "container.run" and seen["req"]["mounts"] == [{"src": "in", "dst": "/in", "ro": True}]
    monkeypatch.delenv(broker.ENV)
    with pytest.raises(broker.BrokerError, match="no_broker"):
        broker.status()


def _layout(prefix, exe, paths, base=None):
    return {"base_prefix": str(base or prefix), "prefix": str(prefix), "executable": str(exe), "paths": [str(p) for p in paths]}


def test_interpreter_roots_never_grant_a_shared_prefix(tmp_path, monkeypatch):
    """A Homebrew-shaped Python (bin/python3 under the shared /opt/homebrew, resolving into its own framework prefix), a
    venv on top of it, and a system-shaped one (prefix /usr): only the interpreters' own prefixes, library directories
    and executables are granted."""
    brew, usr = tmp_path / "opt" / "homebrew", tmp_path / "usr"
    monkeypatch.setattr(S, "SHARED_PREFIXES", (str(brew), str(usr)))
    fw = brew / "Cellar" / "python@3.12" / "3.12.9" / "Frameworks" / "Python.framework" / "Versions" / "3.12"
    lib = fw / "lib" / "python3.12"
    for d in (fw / "bin", lib / "site-packages", usr / "bin", usr / "lib" / "python3.12" / "lib-dynload",
              usr / "lib" / "python3" / "dist-packages", tmp_path / "venv" / "lib" / "python3.12" / "site-packages"):
        d.mkdir(parents=True)
    real = lambda p: os.path.realpath(p)
    roots = S.interpreter_roots(layout=_layout(fw, fw / "bin" / "python3.12", [lib, lib, lib / "site-packages", lib / "site-packages"]))
    assert roots == [real(fw)]
    venv = tmp_path / "venv"
    roots = S.interpreter_roots(layout=_layout(venv, fw / "bin" / "python3.12", [lib, lib, venv / "lib" / "python3.12" / "site-packages"] * 1 +
                                               [venv / "lib" / "python3.12" / "site-packages"], base=fw))
    assert roots == [real(fw), real(venv)]
    system = _layout(usr, usr / "bin" / "python3.12", [usr / "lib" / "python3.12", usr / "lib" / "python3.12",
                                                        usr / "lib" / "python3" / "dist-packages", usr / "lib" / "python3" / "dist-packages"])
    roots = S.interpreter_roots(layout=system)
    assert roots == [real(usr / "lib" / "python3.12"), real(usr / "lib" / "python3" / "dist-packages"), real(usr / "bin" / "python3.12")]
    assert not {real(usr), real(usr / "bin"), real(brew)} & set(roots)


def test_another_interpreter_reports_its_own_layout(tmp_path):
    """A venv other than this process's shares its base's executable (realpath), but its prefix is its own: it is asked."""
    import subprocess
    import venv as V
    V.create(tmp_path / "v", with_pip=False, symlinks=os.name != "nt")
    py = tmp_path / "v" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lay = S.interpreter_layout(str(py))
    assert os.path.realpath(lay["prefix"]) == os.path.realpath(tmp_path / "v") and lay["base_prefix"] == sys.base_prefix
    roots = S.interpreter_roots(str(py))
    assert os.path.realpath(tmp_path / "v") in roots and os.path.realpath(tmp_path) not in roots
    assert subprocess.run([str(py), "-c", "pass"]).returncode == 0
