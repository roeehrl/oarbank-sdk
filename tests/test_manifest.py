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


def test_tick_results_need_the_tick_and_core_2_3():
    def caps(*c, core=">=2.3,<3"):
        def f(d):
            d["requires"]["core"] = core
            d["coordinator"]["capabilities"] += list(c)
        return f
    bad(caps("campaign.tick.results"), "requires the campaign.tick capability")
    bad(caps("campaign.tick", "campaign.tick.results", core=">=2.2"), "campaign.tick.results need requires.core >= 2.3")
    d = copy.deepcopy(doc())
    caps("campaign.tick", "campaign.tick.results")(d)
    assert m.Manifest.model_validate(d).core_keys_used() == [("coordinator.capabilities campaign.tick.results", (2, 3))]


@pytest.mark.parametrize("where", ["campaign_effects", "move", "operation"])
def test_dataset_update_and_delete_effects_need_core_2_3(where):
    def declare(core):
        def f(d):
            d["requires"]["core"] = core
            if where == "campaign_effects":
                d["coordinator"]["capabilities"].append("campaign.tick")
                d["coordinator"]["campaign_effects"] = ["datasets.update"]
            elif where == "move":
                d["coordinator"]["move"]["effects"].append("datasets.delete")
            else:
                d["operations"][0]["effects"].append("datasets.update")
        return f
    bad(declare(">=2.2"), "datasets.update and datasets.delete effects need requires.core >= 2.3", name="toy")
    d = copy.deepcopy(doc("toy"))
    declare(">=2.3")(d)
    m.Manifest.model_validate(d)


SHA = "ab" * 32


def bootstrapped(d, **stage):
    """toy with a bootstrap `fetch` stage beside its default `run`, and one pinned tool dataset."""
    d["requires"]["core"] = ">=2.4,<3"
    d["coordinator"]["capabilities"].append("result.merge")
    d["stages"][0]["default"] = True
    d["stages"].append({"name": "fetch", "bootstrap": True, "determinism": "none", **stage})
    d["datasets"] = {"kinds": ["tool"], "pinned": [
        {"dataset_id": "tool:sum-1", "kind": "tool", "meta": {"version": "1"},
         "files": [{"path": "bin/sum.py", "sha256": SHA, "size": 12}]}]}
    return d


def test_a_bootstrap_stage_is_standalone_never_default_never_compared_and_reserves_no_pools():
    man = m.Manifest.model_validate(bootstrapped(copy.deepcopy(doc("toy"))))
    assert man.is_bootstrap("fetch") and not man.is_bootstrap("run") and not man.is_bootstrap(None)
    assert man.core_keys_used()[-2:] == [("stages[].bootstrap", (2, 4)), ("datasets.pinned", (2, 4))]
    bad(lambda d: bootstrapped(d, determinism="exact"), "a bootstrap stage has determinism none", name="toy")
    bad(lambda d: bootstrapped(d).update(results={**d["results"], "determinism": "exact"}) or d["stages"][1].pop("determinism"),
        "a bootstrap stage has determinism none", name="toy")
    bad(lambda d: bootstrapped(d, requires={"needs_pools": ["containers"]}).update(
        sandbox={"containers": [{"image": "docker.io/x/y@sha256:" + SHA}]}), "reserves and needs no pools", name="toy")

    def default(d):
        bootstrapped(d)
        d["stages"][0]["default"] = False
        d["stages"][1]["default"] = True
        d["stages"][0]["determinism"] = "exact"
    bad(default, "never the default stage", name="toy")

    def chained(d):
        d.update(requires={**d["requires"], "core": ">=2.4,<3"})
        d["stages"][1]["bootstrap"] = True                     # render, which score runs after
    bad(chained, "a bootstrap stage is standalone")


def test_bootstrap_stages_and_pins_need_each_other_and_core_2_4():
    bad(lambda d: bootstrapped(d)["datasets"].update(pinned=[]), r"need \[\[datasets.pinned\]\]", name="toy")
    bad(lambda d: bootstrapped(d)["stages"][1].update(bootstrap=False), r"\[\[datasets.pinned\]\] needs a bootstrap stage",
        name="toy")
    bad(lambda d: bootstrapped(d)["requires"].update(core=">=2.3,<3"),
        r"stages\[\]\.bootstrap, datasets.pinned need requires.core >= 2.4", name="toy")
    bad(lambda d: bootstrapped(d).update(sandbox={"net": {"mode": "egress-any"}}), "cannot request sandbox.net.mode = 'egress-any'",
        name="toy")
    d = bootstrapped(copy.deepcopy(doc("toy")))
    d["sandbox"] = {"net": {"mode": "egress-allowlist", "allow": ["example.org"]}}
    m.Manifest.model_validate(d)


def test_pinned_datasets_are_well_formed():
    def pin(**kw):
        def f(d):
            bootstrapped(d)
            d["datasets"]["pinned"][0].update(kw)
        return f
    bad(pin(kind="scene"), "kind 'scene' is not in datasets.kinds", name="toy")
    bad(pin(dataset_id="bad id"), "dataset_id", name="toy")
    bad(pin(files=[]), "files", name="toy")
    bad(pin(files=[{"path": "/abs", "sha256": SHA, "size": 1}]), "relative", name="toy")
    bad(pin(files=[{"path": "a", "sha256": "AB" * 32, "size": 1}]), "sha256", name="toy")
    bad(pin(files=[{"path": "a", "sha256": SHA, "size": -1}]), "size", name="toy")
    bad(pin(files=[{"path": "a", "sha256": SHA, "size": 1}, {"path": "a", "sha256": SHA, "size": 1}]), "paths must be unique",
        name="toy")
    bad(pin(platform="linux-amd64"), "only for platform-bound kinds", name="toy")
    bad(lambda d: pin()(d) or d["datasets"].update(platform_bound=["tool"]), "is platform-bound, so the pin sets `platform`",
        name="toy")
    bad(lambda d: pin(platform="plan9-arm64")(d) or d["datasets"].update(platform_bound=["tool"]), "not in requires.platforms",
        name="toy")
    bad(lambda d: pin()(d) or d["datasets"]["pinned"].append(dict(d["datasets"]["pinned"][0])), "more than once", name="toy")
    bad(lambda d: pin()(d) or d["datasets"]["pinned"].append({**d["datasets"]["pinned"][0], "dataset_id": "tool:sum-2"}),
        "holds the same files as 'tool:sum-1'", name="toy")


def test_a_bootstrap_result_is_exactly_pinned_datasets():
    man = m.Manifest.model_validate(bootstrapped(copy.deepcopy(doc("toy"))))
    good = [{"name": "tool", "files": [{"path": "bin/sum.py", "digest": SHA, "size": 12}]}]
    assert man.bootstrap_problem({}, good) is None and man.pin_of(good[0]["files"]).dataset_id == "tool:sum-1"
    assert man.datasets.pin("tool:sum-1").dataset_files() == [{"path": "bin/sum.py", "digest": SHA, "size": 12}]
    assert "no payload" in man.bootstrap_problem({"n": 1}, good)
    assert "at least one artifact" in man.bootstrap_problem({}, [])
    changed = [{"name": "tool", "files": [{"path": "bin/sum.py", "digest": "cd" * 32, "size": 12}]}]
    assert man.bootstrap_problem({}, changed) == (f"artifact 'tool': bin/sum.py is sha256 {'cd' * 32} (12 bytes); pinned dataset "
                                                  f"tool:sum-1 pins {SHA} (12 bytes)")
    extra = [{"name": "tool", "files": good[0]["files"] + [{"path": "README", "digest": SHA, "size": 1}]}]
    assert "README is not a file of pinned dataset tool:sum-1" in man.bootstrap_problem({}, extra)
    assert "no pinned dataset's" in man.bootstrap_problem({}, good + [{"name": "other", "files": [{"path": "x", "digest": SHA,
                                                                                                    "size": 1}]}])
    assert man.pin_of(changed[0]["files"]) is None


def test_bootstrap_jobs_get_the_network_and_nothing_else():
    sb = m.SandboxSection.model_validate({"net": {"mode": "egress-allowlist", "allow": ["example.org"]},
                                          "tools": [{"id": "java17", "trust": "code-exec"}], "devices": {"gpu": "compute"},
                                          "containers": [{"image": "docker.io/x/y@sha256:" + SHA}], "exec_writable": True})
    b = sb.for_bootstrap()
    assert (b.net, b.tools, b.devices.gpu, b.containers, b.exec_writable) == (sb.net, [], "none", [], False)


def served(d, **svc):
    """toy whose `run` stage reserves the `model` pool of an on-demand endpoint service."""
    d["requires"].update(core=">=2.5,<3", service_protocol=[1])
    d["stages"][0]["requires"]["pools"] = {"model": 1}
    d["services"] = [{"name": "model", "exec": ["python", "-I", "{bundle}/model.py"], "endpoint": True,
                      "provides": {"pools": ["model"]}, **svc}]
    return d


def test_endpoint_services_are_reached_through_a_reserved_pool_and_need_core_2_5():
    man = m.Manifest.model_validate(served(copy.deepcopy(doc("toy"))))
    assert [s.name for s in man.endpoint_services_of(None)] == ["model"] == [s.name for s in man.endpoint_services_of("run")]
    assert man.services[0].env_name() == "OARBANK_SERVICE_MODEL"
    assert man.core_keys_used()[-1:] == [("services[].endpoint", (2, 5))]
    assert man.gpu_pools() == set()
    bad(lambda d: served(d)["requires"].update(core=">=2.4,<3"), r"services\[\]\.endpoint need requires.core >= 2.5", name="toy")
    bad(lambda d: served(d, provides={"capabilities": ["llm"]}).update(stages=[{**d["stages"][0], "requires": {}}]),
        "an endpoint service provides at least one pool", name="toy")
    bad(lambda d: served(d, lifecycle="manual"), "an endpoint service is on_demand or always", name="toy")
    # needs_pools gives no endpoint: only a reservation does
    d = served(copy.deepcopy(doc("toy")))
    d["stages"][0]["requires"] = {"needs_pools": ["model"]}
    assert m.Manifest.model_validate(d).endpoint_services_of(None) == []
    # a service that is not an endpoint is reached by nobody
    assert m.Manifest.model_validate(served(copy.deepcopy(doc("toy")), endpoint=False)).endpoint_services_of(None) == []


def test_service_gpu_use_needs_the_gpu_grant_and_core_2_5():
    gpu = {"use": "shared", "apis_any": ["metal", "cuda"]}
    d = served(copy.deepcopy(doc("toy")), gpu=gpu)
    d["sandbox"] = {"devices": {"gpu": "compute"}}
    man = m.Manifest.model_validate(d)
    assert man.services[0].gpu.use == "shared" and man.gpu_pools() == {"model"}
    assert ("services[].gpu", (2, 5)) in man.core_keys_used()
    bad(lambda d: served(d, gpu=gpu), r"gpu.use = 'shared' needs sandbox.devices.gpu = 'compute'", name="toy")
    bad(lambda d: served(d, gpu={"use": "always"}).update(sandbox={"devices": {"gpu": "compute"}}), "use", name="toy")

    def old_core(d):
        served(d, endpoint=False, gpu=gpu).update(sandbox={"devices": {"gpu": "compute"}})
        d["requires"]["core"] = ">=2.4,<3"
    bad(old_core, r"services\[\]\.gpu need requires.core >= 2.5", name="toy")
