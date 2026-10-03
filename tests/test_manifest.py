import copy
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import manifest as m
from oarbank_sdk.cli import main

EX = Path(__file__).parent / "fixtures" / "manifests"


def path(name):
    return Path(__file__).parents[1] / "examples" / "toy" / "oarbank-module.toml" if name == "toy" else EX / f"{name}.toml"


def doc(name="render"):
    return tomllib.loads(path(name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["toy", "bench", "render", "per-platform"])
def test_examples_pass_strict_check(name, capsys):
    assert main(["check", str(path(name))]) == 0
    assert m.unknown_fields(m.load(path(name))) == []


def test_unknown_fields_are_tolerated_by_the_core_and_rejected_by_check(tmp_path):
    text = path("toy").read_text(encoding="utf-8").replace('compat = "toy1"', 'compat = "toy1"\ncompta = "x"')
    text = text.replace("[runner]", "[runner]\nfuture_knob = true")
    p = tmp_path / "m.toml"
    p.write_text(text, encoding="utf-8", newline="\n")
    man = m.load(p)                                   # lenient: the core keeps going
    assert man.module.model_extra == {"compta": "x"}
    assert sorted(m.unknown_fields(man)) == ["module.compta", "runner.future_knob"]
    assert main(["check", str(p)]) == 1


def bad(mutate, match, name="render"):
    d = copy.deepcopy(doc(name))
    mutate(d)
    with pytest.raises(ValidationError, match=match):
        m.Manifest.model_validate(d)


def test_pool_without_a_provider():
    bad(lambda d: d.update(services=[]), "no declared service provides")


def test_capability_without_a_probe():
    bad(lambda d: d.update(probes=[]), "no probe or service provides")


def test_services_need_a_service_protocol():
    bad(lambda d: d["requires"].update(service_protocol=[]), "service_protocol")


def test_stage_cycle():
    def f(d):
        d["stages"][1]["after"] = "score"
    bad(f, "cycle")


def test_after_must_name_a_stage():
    bad(lambda d: d["stages"][2].update(after="nope"), "is not a stage")


def test_duplicate_stage_names():
    bad(lambda d: d["stages"][1].update(name="eval"), "unique")


def test_multi_stage_needs_merge():
    bad(lambda d: d["coordinator"].update(capabilities=[]), "result.merge")


def test_value_field_must_be_declared():
    bad(lambda d: d["results"]["value"].update(field="nope"), "not a declared result field")


def test_uv_runtime_needs_lock_and_python():
    bad(lambda d: d["runner"].update(runtime={"kind": "uv"}), "requires `lock` and `python`")


@pytest.mark.parametrize("line", ["<b>{score}</b>x{", "{score:.6q}", "{Score}", "{score!r}"])
def test_digest_line_is_restricted(line):
    bad(lambda d: d["ui"].update(digest_line=line), "digest_line")


def test_digest_line_accepts_escaped_braces():
    d = doc()
    d["ui"]["digest_line"] = "{{raw}} {score:.2f}"
    m.Manifest.model_validate(d)


@pytest.mark.parametrize("fmt", ["%s", ".100f", "x", "{}"])
def test_format_whitelist(fmt):
    bad(lambda d: d["results"]["fields"][0]["ui"].update(format=fmt), "whitelist")


@pytest.mark.parametrize("mid", ["render", "Dev.x", "dev..x", "dev.x_"])
def test_module_id_is_reverse_dns(mid):
    bad(lambda d: d["module"].update(id=mid), "module.id|pattern")


def test_argv_is_a_list_never_a_shell_string():
    bad(lambda d: d["runner"].update(exec="python runner/main.py"), "list")


def test_unsupported_manifest_major():
    bad(lambda d: d.update(manifest=2), "manifest")


def test_stage_determinism_is_for_standalone_stages_and_needs_core_2_3():
    def on(name, value="none", core=">=2.3,<3"):
        def f(d):
            d["requires"]["core"] = core
            next(s for s in d["stages"] if s["name"] == name)["determinism"] = value
        return f
    bad(on("score"), "determinism applies to standalone stages")          # a chain's tail
    bad(on("render"), "determinism applies to standalone stages")         # the stage a tail is `after`
    bad(on("eval", core=">=2.2,<3"), r"stages\[\]\.determinism need requires.core >= 2.3")
    d = copy.deepcopy(doc())
    on("eval")(d)
    man = m.Manifest.model_validate(d)
    assert (man.determinism_of("eval"), man.determinism_of(None), man.determinism_of("score")) == ("none", "none", "exact")
    assert not man.compares("eval") and man.compares("render") and man.compares("score")
    assert man.core_keys_used() == [("stages[].determinism", (2, 3))]


def test_a_module_needs_a_stage_that_compares():
    def none_everywhere(d):
        d["results"]["determinism"] = "none"
    bad(none_everywhere, "no stage compares", name="toy")
    d = copy.deepcopy(doc("toy"))
    d["results"]["determinism"] = "none"
    d["stages"][0]["default"] = True
    d["stages"].append({"name": "sum", "determinism": "exact"})
    d["requires"]["core"] = ">=2.3,<3"
    d["coordinator"]["capabilities"].append("result.merge")
    man = m.Manifest.model_validate(d)
    assert man.standalone_stages() == ["run", "sum"] and not man.compares("run") and man.compares("sum")


def test_the_default_stage_is_explicit_when_several_stages_are_standalone():
    def two(d, **run):
        d["requires"]["core"] = ">=2.3,<3"
        d["coordinator"]["capabilities"].append("result.merge")
        d["stages"][0].update(run)
        d["stages"].append({"name": "sync", "determinism": "none"})
    bad(lambda d: two(d), "mark the one a job runs when it names no stage", name="toy")
    bad(lambda d: two(d, default=True) or d["stages"][1].update(default=True), "at most one stage", name="toy")
    bad(lambda d: d["requires"].update(core=">=2.3,<3") or d["stages"][2].update(default=True), "only a standalone stage")
    bad(lambda d: d["stages"][0].update(default=True), r"stages\[\]\.default need requires.core >= 2.3")
    d = copy.deepcopy(doc("toy"))
    two(d, default=True)
    man = m.Manifest.model_validate(d)
    assert man.default_stage() == "run" and man.determinism_of(None) == "exact" and not man.compares("sync")
    assert m.load(path("render")).default_stage() == "eval"                 # the only standalone stage, unmarked
