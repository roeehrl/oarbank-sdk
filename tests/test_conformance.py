import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oarbank_sdk.conformance import conform

TOY = Path(__file__).parents[1] / "examples" / "toy"


def copy(tmp_path) -> Path:
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    return d


def failed(rep) -> set[str]:
    return {c.name for c in rep.checks if c.status == "fail"}


def test_the_reference_module_conforms():
    rep = conform(TOY)
    assert rep.ok, rep.text()
    assert {c.suite for c in rep.checks} == {"manifest", "bundle", "settings", "protocol", "ui", "runner"}


def test_an_impure_verb_is_caught(tmp_path):
    d = copy(tmp_path)
    code = (d / "toy_module.py").read_text(encoding="utf-8").replace(
        'return mp.GoldenListResult(goldens=[mp.Golden(name="toy-golden"',
        'import uuid\n    return mp.GoldenListResult(goldens=[mp.Golden(name=f"toy-golden-{uuid.uuid4().hex}"')
    (d / "toy_module.py").write_text(code, encoding="utf-8", newline="\n")
    assert any("golden.list pure" in n for n in failed(conform(d, runner=False)))


def test_a_runner_giving_the_wrong_answer_is_caught(tmp_path):
    d = copy(tmp_path)
    (d / "toy_runner.py").write_text((d / "toy_runner.py").read_text().replace("total = n * (n - 1) // 2", "total = n * (n - 1) // 2 + 1"), encoding="utf-8", newline="\n")
    names = failed(conform(d))
    assert any("accepted" in n for n in names) and any("matches the golden" in n for n in names)


def test_a_core_import_and_a_missing_capability_are_caught(tmp_path):
    d = copy(tmp_path)
    (d / "toy_module.py").write_text("import oarbank.coordinator  # noqa\n" + (d / "toy_module.py").read_text(), encoding="utf-8", newline="\n")
    assert "builds and verifies" in failed(conform(d, runner=False))
    d2 = copy(tmp_path / "b")
    m = (d2 / "oarbank-module.toml").read_text(encoding="utf-8").replace('capabilities = ["ui.view.compute", "op.plan", "op.apply",',
                                                          'capabilities = ["ui.view.compute", "op.plan", "op.apply", "campaign.tick",')
    (d2 / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    assert "declared capabilities advertised" in failed(conform(d2, runner=False))


def per_platform_toy(tmp_path, doctor_platform="os.environ['OARBANK_PLATFORM']") -> Path:
    """The toy with per-platform declarations: a runner env the runner needs, a coordinator env the module needs, and a
    golden whose default expectation is wrong everywhere but this host's OS."""
    from oarbank_sdk import portable
    host_os = portable.split_platform(portable.host_platform())[0]
    d = copy(tmp_path)
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.2,<3"')
    m = m.replace('[runner]\n', '[runner]\nenv = { TOY_SCALE = "1" }\n')
    m = m.replace('[coordinator.move]', f'[coordinator.variants.{host_os}]\nenv = {{ TOY_COORD = "on" }}\n\n[coordinator.move]')
    (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    r = (d / "toy_runner.py").read_text(encoding="utf-8")
    r = r.replace('    total = n * (n - 1) // 2\n', '    total = n * (n - 1) // 2 * int(os.environ.get("TOY_SCALE", "0"))\n')
    r = r.replace('"health": "healthy", "capabilities": [],', f'"health": "healthy", "capabilities": [], "attrs": {{"platform": {doctor_platform}}},')
    (d / "toy_runner.py").write_text(r, encoding="utf-8", newline="\n")
    code = (d / "toy_module.py").read_text(encoding="utf-8").replace(
        'expected={"digest": digest(expected_sum(GOLDEN_N)), "digest_version": 1})])',
        'expected={"digest": "0" * 64, "digest_version": 1},\n'
        f'        expected_by_platform={{"{host_os}": {{"digest": digest(expected_sum(GOLDEN_N)), "digest_version": 1}}}})])')
    code = code.replace('def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:\n',
                        'def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:\n'
                        '    import os\n    assert os.environ.get("TOY_COORD") == "on" and ctx.host_platform == os.environ["OARBANK_PLATFORM"]\n'
                        '    assert ctx.host_has(mp.HOST_GOLDENS_BY_PLATFORM)\n')
    (d / "toy_module.py").write_text(code, encoding="utf-8", newline="\n")
    return d


def test_the_kit_runs_a_module_as_its_platform_would(tmp_path):
    """Runner env and coordinator variant env applied, host.platform and capabilities sent, the golden's expected value
    resolved for this host, doctor attrs.platform checked, a view per declared platform."""
    rep = conform(per_platform_toy(tmp_path))
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert {"platform view windows-amd64", "doctor attrs.platform is OARBANK_PLATFORM"} <= names
    assert any(n.startswith("golden toy-golden: platform keys declared") for n in names)
    assert any("matches the golden" in n for n in names)


def test_a_wrong_doctor_platform_and_undeclared_golden_keys_are_caught(tmp_path):
    d = per_platform_toy(tmp_path, doctor_platform='"plan9-mips"')
    code = (d / "toy_module.py").read_text(encoding="utf-8").replace('expected_by_platform={"', 'platforms=["plan9"], expected_by_platform={"')
    (d / "toy_module.py").write_text(code, encoding="utf-8", newline="\n")
    names = failed(conform(d))
    assert "doctor attrs.platform is OARBANK_PLATFORM" in names
    assert any(n.startswith("golden toy-golden: platform keys declared") for n in names)


def test_lint_warnings_do_not_fail_the_report(tmp_path):
    d = copy(tmp_path)
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.2,<3"')
    (d / "oarbank-module.toml").write_text(m + '\n[placement]\nmix = "same-rack"\n', encoding="utf-8", newline="\n")
    rep = conform(d, runner=False)
    assert rep.ok, rep.text()
    assert any(c.status == "warn" and "same-rack" in c.detail for c in rep.checks)
    assert rep.text().endswith(", 1 warning")


def _slow_toy(tmp_path, listens: bool) -> Path:
    """The toy runner, taking 1.5 s to answer: at safe points, acknowledging a stop (listens), or deaf to control.json
    for longer than its stop_grace_s. Both name their phase once work starts."""
    d = copy(tmp_path)
    work = ("    from oarbank_sdk.control import Control, Stopped\n"
            "    ctl = Control(workdir)\n"
            "    ctl.phase('summing')\n"
            "    t = __import__('time').monotonic()\n"
            "    try:\n"
            "        while __import__('time').monotonic() - t < 1.5:\n"
            "            ctl.safe_point()\n"
            "            __import__('time').sleep(0.01)\n"
            "    except Stopped:\n"
            "        return ctl.acknowledge_stop()\n") if listens else (
            "    (workdir / 'phase').write_text('summing', encoding='utf-8')\n"
            "    __import__('time').sleep(5)\n")
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8")
    (d / "oarbank-module.toml").write_text(m.replace('capabilities = ["cancellable", "deterministic_output", "freeze_ok"]',
                                                     'capabilities = ["cancellable", "deterministic_output", "freeze_ok"]\nstop_grace_s = 3'), encoding="utf-8", newline="\n")
    r = (d / "toy_runner.py").read_text(encoding="utf-8")
    (d / "toy_runner.py").write_text(r.replace("    total = n * (n - 1) // 2\n", work + "    total = n * (n - 1) // 2\n"), encoding="utf-8", newline="\n")
    return d


def _stop_checks(rep):
    return [c for c in rep.checks if "a nudged stop is acknowledged" in c.name or "exits within stop_grace_s" in c.name]


def test_a_cancellable_runner_acknowledges_the_nudged_stop(tmp_path):
    """Timed to the runner's acknowledgement (failure.json, fault transient), not to process teardown, and sent only once
    the runner named its first phase, so a slow start on a loaded machine is never counted."""
    checks = _stop_checks(conform(_slow_toy(tmp_path, listens=True)))
    assert [c.status for c in checks] == ["pass", "pass"], [(c.name, c.detail) for c in checks]


def test_a_runner_deaf_to_control_fails_the_stop_checks(tmp_path):
    checks = _stop_checks(conform(_slow_toy(tmp_path, listens=False)))
    assert [c.status for c in checks] == ["fail", "fail"], [(c.name, c.detail) for c in checks]


def test_a_runner_that_names_no_phase_is_not_timed(tmp_path):
    d = _slow_toy(tmp_path, listens=True)
    r = (d / "toy_runner.py").read_text(encoding="utf-8").replace("    ctl.phase('summing')\n", "")
    (d / "toy_runner.py").write_text(r, encoding="utf-8", newline="\n")
    checks = _stop_checks(conform(d))
    assert [c.status for c in checks] == ["skip"] and "phase" in checks[0].detail


def test_a_golden_for_a_stage_that_does_not_compare_is_caught(tmp_path):
    """A stage with determinism none (an ingestion stage) is never golden-tested: its results depend on when it ran."""
    d = copy(tmp_path)
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.3,<3"')
    m = m.replace('capabilities = ["ui.view.compute",', 'capabilities = ["result.merge", "ui.view.compute",')
    m = m.replace("[results]", '[[stages]]\nname = "fetch"\ndeterminism = "none"\n\n[results]').replace('name = "run"\n', 'name = "run"\ndefault = true\n')
    (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    code = (d / "toy_module.py").read_text(encoding="utf-8").replace('key_inputs={"n": GOLDEN_N}, stages=["run"]', 'key_inputs={"n": GOLDEN_N}, stages=["fetch"]')
    (d / "toy_module.py").write_text(code, encoding="utf-8", newline="\n")
    assert any(n.startswith("golden toy-golden: its stage compares") for n in failed(conform(d, runner=False)))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_killing_a_runner_group_that_already_exited_is_not_an_error():
    """The leader exited but is not reaped yet (macOS answers EPERM for such a group), then reaped (ESRCH)."""
    from oarbank_sdk.conformance import _kill_tree
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    import time
    t = time.monotonic()
    while not subprocess.run(["ps", "-o", "stat=", "-p", str(p.pid)], capture_output=True, text=True).stdout.startswith("Z"):
        assert time.monotonic() - t < 30, "the child never exited"
        time.sleep(0.01)                                          # until it is a zombie: exited, not reaped
    _kill_tree(p)
    p.wait()
    _kill_tree(p)


MODELSERVER = Path(__file__).parents[1] / "examples" / "modelserver"


def test_the_endpoint_reference_module_conforms_with_its_service_up():
    """modelserver's golden reaches its warm service through a connector the kit hands it, as on an agent, and the service
    suite drives the service through its endpoint channel."""
    rep = conform(MODELSERVER)
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert {"service model: starts, says hello on its endpoint channel and becomes ready",
            "service model: service spec generate", "service model: service spec unknown-path",
            "service model: the service never listens itself", "service model: stops"} <= names, rep.text()
    assert "golden modelserver-golden (generate): matches the golden" in names


def test_a_service_that_listens_is_caught(tmp_path):
    d = tmp_path / "modelserver"
    shutil.copytree(MODELSERVER, d, ignore=shutil.ignore_patterns("__pycache__"))
    code = (d / "model_service.py").read_text(encoding="utf-8").replace(
        'def main(op: str) -> int:\n',
        'LISTENER = []\n\n\ndef main(op: str) -> int:\n'
        '    if op in ("start", "serve"):                     # a model server that also opens a port of its own\n'
        '        import socket\n'
        '        s = socket.socket()\n'
        '        s.bind(("127.0.0.1", 0))\n'
        '        s.listen()\n'
        '        LISTENER.append(s)\n')
    (d / "model_service.py").write_text(code, encoding="utf-8", newline="\n")
    rep = conform(d)
    assert "service model: the service never listens itself" in failed(rep), rep.text()


REEL = Path(__file__).parents[1] / "examples" / "reel"


def copy_reel(tmp_path) -> Path:
    d = tmp_path / "reel"
    shutil.copytree(REEL, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    return d


def test_reel_conforms_with_its_checkpoint_replay_thumbnails_and_origin_operation():
    rep = conform(REEL)
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert any("resumes from a checkpoint with the same digest" in n for n in names)
    assert any("a resumed result's files are all there" in n for n in names)
    assert any("thumbnails are small allowed images" in n for n in names)
    assert any("import_asset #1: datasets.create files" in n for n in names)
    assert any("adopt_upload #0: datasets.create files" in n for n in names)        # an importer of an uploaded dataset


def test_a_runner_that_resumes_wrongly_fails_the_replay(tmp_path):
    d = copy_reel(tmp_path)
    code = (d / "reel_runner.py").read_text(encoding="utf-8").replace(
        'digests, start = list(state["digests"]), int(state["next"])',
        'digests, start = list(state["digests"])[:-1], int(state["next"])')     # loses a frame on resume
    (d / "reel_runner.py").write_text(code, encoding="utf-8", newline="\n")
    assert any("resumes from a checkpoint with the same digest" in n for n in failed(conform(d)))


def test_a_checkpoint_naming_a_file_outside_the_workdir_fails(tmp_path):
    d = copy_reel(tmp_path)
    code = (d / "reel_runner.py").read_text(encoding="utf-8").replace(
        "        if ctl.checkpoint_requested and i > start:\n            checkpoint(i)",
        "        if ctl.checkpoint_requested:\n            open(events, 'a').write('{\"t\": 1, \"kind\": \"checkpoint\", "
        "\"files\": [{\"path\": \"missing/state.json\"}]}\\n')")
    assert "missing/state.json" in code
    (d / "reel_runner.py").write_text(code, encoding="utf-8", newline="\n")
    assert any("checkpoint, then stop" in n for n in failed(conform(d)))


def test_an_import_with_a_plain_http_origin_fails(tmp_path):
    d = copy_reel(tmp_path)
    fx = json.loads((d / "conformance.json").read_text())
    code = (d / "reel_module.py").read_text(encoding="utf-8").replace(   # skip the SDK builder's own check
        "module = Module(MODULE_ID, VERSION)",
        "fx.datasets_create = lambda d, k, files, meta=None: mp.Effect(kind='datasets.create', args={'dataset_id': d, "
        "'kind': k, 'files': files, 'meta': meta or {}})\nmodule = Module(MODULE_ID, VERSION)")
    (d / "reel_module.py").write_text(code, encoding="utf-8", newline="\n")
    next(o for o in fx["ops"] if o["verb"] == "import_asset")["params"]["url"] = "http://assets.example.org/clip.webm"
    rep = conform(d, fx, runner=False)
    assert any("datasets.create files" in n for n in failed(rep)), rep.text()


LLMSERVE = Path(__file__).parents[1] / "examples" / "llmserve"


def test_the_gpu_reference_module_conforms_and_holds_its_duty():
    """llmserve's batch stage holds the gpu_duty the kit writes (its stand-in backend's units run on the CPU, so the kit
    measures its CPU time on any machine), and its selftest golden certifies it."""
    rep = conform(LLMSERVE)
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert {"duty spec stand-in-at-0.3 (gpu_duty 0.3): reports the duty it holds",
            "duty spec stand-in-at-0.3 (gpu_duty 0.3): its reports stay within the duty",
            "duty spec stand-in-at-0.3 (gpu_duty 0.3): its measured time stays within the duty",
            "golden llmserve-selftest (selftest): matches the golden"} <= names, rep.text()


def test_a_runner_that_does_not_hold_its_duty_is_caught(tmp_path):
    d = tmp_path / "llmserve"
    shutil.copytree(LLMSERVE, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    r = d / "llm_runner.py"
    # it reports the duty asked, but never idles for it
    r.write_text(r.read_text().replace('    completions, tokens = [], 0\n',
                                       '    duty.after = lambda b: duty._report()\n    completions, tokens = [], 0\n'),
                 encoding="utf-8", newline="\n")
    rep = conform(d)
    bad = failed(rep)
    assert "duty spec stand-in-at-0.3 (gpu_duty 0.3): its reports stay within the duty" in bad, rep.text()
    assert "duty spec stand-in-at-0.3 (gpu_duty 0.3): its measured time stays within the duty" in bad, rep.text()
    # and one that writes no reports at all cannot show it holds anything
    r.write_text(r.read_text().replace("Duty(ctl, events=events,", "Duty(ctl, events=None,"), encoding="utf-8", newline="\n")
    assert "duty spec stand-in-at-0.3 (gpu_duty 0.3): reports the duty it holds" in failed(conform(d))


PUBLICECHO = Path(__file__).parents[1] / "examples" / "publicecho"


def copy_publicecho(tmp_path) -> Path:
    d = tmp_path / "publicecho"
    shutil.copytree(PUBLICECHO, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    return d


def test_the_listener_reference_module_conforms_with_its_listener_served():
    """publicecho's service starts as an agent starts it with its listener, gets the kit's announcement, and answers the
    fixtures' listener_specs through listener connections the kit hands it."""
    rep = conform(PUBLICECHO)
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert {"service echo: starts with its listeners, says hello on its endpoint channel and becomes ready",
            "service echo: listener echo: takes a connection the agent hands it",
            "service echo: listener spec one-line", "service echo: listener spec two-lines",
            "service echo: the service never listens itself", "service echo: stops"} <= names, rep.text()
    assert not [c for c in rep.checks if c.suite == "service"]   # a service with listeners runs in the listener suite


def test_a_listener_service_that_listens_itself_is_caught(tmp_path):
    d = copy_publicecho(tmp_path)
    code = (d / "echo_service.py").read_text(encoding="utf-8").replace(
        'def main(op: str) -> int:\n',
        'LISTENER = []\n\n\ndef main(op: str) -> int:\n'
        '    if op in ("start", "serve"):                     # an echo server that also opens a port of its own\n'
        '        s = socket.socket()\n'
        '        s.bind(("127.0.0.1", 0))\n'
        '        s.listen()\n'
        '        LISTENER.append(s)\n')
    (d / "echo_service.py").write_text(code, encoding="utf-8", newline="\n")
    rep = conform(d)
    assert "service echo: the service never listens itself" in failed(rep), rep.text()


def test_a_listener_spec_the_service_does_not_answer_fails(tmp_path):
    d = copy_publicecho(tmp_path)
    fx = json.loads((d / "conformance.json").read_text(encoding="utf-8"))
    fx["listener_specs"][0]["expect"]["contains"] = "echo hello 198.51.100.1:1\n"
    fx["listener_specs"].append({"name": "stray", "listener": "nope", "send": "x\n"})
    rep = conform(d, fixtures=fx)
    assert {"service echo: listener spec one-line", "listener spec stray: fixture"} <= failed(rep), rep.text()


@pytest.mark.skipif(sys.platform == "win32", reason="listen_fd listeners exist on macOS and Linux only")
def test_a_listen_fd_listener_conforms_on_its_inherited_socket(tmp_path):
    d = copy_publicecho(tmp_path)
    toml = d / "oarbank-module.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('name = "echo"\nport_hint', 'name = "echo"\nhandover = "listen_fd"\n'
                                                              'port_hint'), encoding="utf-8", newline="\n")
    rep = conform(d)
    assert rep.ok, rep.text()
    names = {c.name for c in rep.checks if c.status == "pass"}
    assert {"service echo: listener spec one-line", "service echo: the service never listens itself"} <= names, rep.text()
    assert any("listen_fd" in c.detail for c in rep.checks if c.status == "warn")   # Windows nodes cannot serve it
