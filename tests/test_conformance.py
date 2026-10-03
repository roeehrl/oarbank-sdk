import shutil
from pathlib import Path

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
    assert {c.suite for c in rep.checks} == {"manifest", "bundle", "protocol", "runner"}


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
    """The toy runner, taking 1.5 s to answer: at safe points (listens) or deaf to control.json."""
    d = copy(tmp_path)
    work = ("    from oarbank_sdk.control import Control, Stopped\n"
            "    ctl = Control(workdir)\n"
            "    t = __import__('time').monotonic()\n"
            "    try:\n"
            "        while __import__('time').monotonic() - t < 1.5:\n"
            "            ctl.safe_point()\n"
            "            __import__('time').sleep(0.01)\n"
            "    except Stopped:\n"
            "        return 1\n") if listens else "    __import__('time').sleep(1.5)\n"
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8")
    (d / "oarbank-module.toml").write_text(m.replace('capabilities = ["cancellable", "deterministic_output", "freeze_ok"]',
                                                     'capabilities = ["cancellable", "deterministic_output", "freeze_ok"]\nstop_grace_s = 3'), encoding="utf-8", newline="\n")
    r = (d / "toy_runner.py").read_text(encoding="utf-8")
    (d / "toy_runner.py").write_text(r.replace("    total = n * (n - 1) // 2\n", work + "    total = n * (n - 1) // 2\n"), encoding="utf-8", newline="\n")
    return d


def _stop_check(rep):
    return next(c for c in rep.checks if "a nudged stop ends the job" in c.name)


def test_a_cancellable_runner_reacts_to_the_nudged_stop(tmp_path):
    c = _stop_check(conform(_slow_toy(tmp_path, listens=True)))
    assert c.status == "pass", c.detail
    print(c.detail)


def test_a_runner_deaf_to_control_fails_the_stop_check(tmp_path):
    c = _stop_check(conform(_slow_toy(tmp_path, listens=False)))
    assert c.status == "fail", c.detail
