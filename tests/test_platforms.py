"""The cross-platform baseline (spec/platforms.md): vectors every implementation reproduces, portable paths, platform
tokens, exec rules and variants, the h2 bundle digest, the control plane."""
import io
import json
import os
import shutil
import tarfile
import gzip
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import bundle as B
from oarbank_sdk import keys, manifest as mf, platform as pf, portable

ROOT = Path(__file__).parents[1]
VEC = ROOT / "spec" / "vectors"
TOY = ROOT / "examples" / "toy"


def test_canonical_json_vectors():
    v = json.loads((VEC / "canonical-json.json").read_text(encoding="utf-8"))
    for c in v["cases"]:
        assert keys.canonical_json(c["input"]) == c["canonical"]
    for bad in (float("nan"), float("inf"), 2 ** 53 + 1, {1: "x"}):
        with pytest.raises(keys.CanonicalError):
            keys.canonical_json(bad)


def test_job_key_vectors_and_integral_floats_agree():
    for c in json.loads((VEC / "job-key.json").read_text(encoding="utf-8"))["cases"]:
        assert keys.job_key(c["module_id"], c["compat"], c["key_inputs"], c["stage"]) == c["job_key"]
    a = keys.job_key("dev.x.y", "c", {"n": 3})
    assert a == keys.job_key("dev.x.y", "c", {"n": 3.0})       # 3 and 3.0 are the same number in every language


def test_portable_path_vectors():
    v = json.loads((VEC / "portable-path.json").read_text(encoding="utf-8"))
    assert all(portable.is_portable_path(p) for p in v["accept"])
    assert all(portable.is_portable_path(p, allow_dotfiles=True) for p in v["accept_with_dotfiles"])
    assert not any(portable.is_portable_path(p, allow_dotfiles=True) for p in v["reject"])
    for a, b in v["casefold_collisions"]:
        assert portable.casefold_collisions([a, b])


def test_platform_tokens_are_an_open_set():
    v = json.loads((VEC / "platform-token.json").read_text(encoding="utf-8"))
    assert all(portable.is_platform_token(p) for p in v["accept"])
    assert not any(portable.is_platform_token(p) for p in v["reject"])
    assert portable.oci_platform("linux-amd64") == "linux/amd64"
    assert portable.is_platform_token(portable.host_platform())


def test_variant_resolution_vectors():
    v = json.loads((VEC / "variant-resolution.json").read_text(encoding="utf-8"))
    for c in v["pick"]:
        table = {k: k for k in c["keys"]}
        assert pf.resolve(table, c["platform"]) == c["key"], c
    assert tuple(v["merged"]) == pf.MERGED
    for c in v["overlay"]:
        assert pf.apply_variants(c["base"], c["variants"], c["platform"]) == c["resolved"], c


def test_placement_class_vectors():
    v = json.loads((VEC / "placement-class.json").read_text(encoding="utf-8"))
    assert tuple(v["mixes"]) == pf.MIXES
    for c in v["class_key"]:
        assert pf.class_key(c["platform"], c["mix"]) == c["class"], c
    for c in v["applied"]:
        assert pf.normalize(c["mix"]) == c["applied"], c
    for c in v["stricter"]:
        assert pf.stricter(c["a"], c["b"]) == c["stricter"] == pf.stricter(c["b"], c["a"]), c
    for c in v["scope_mix"]:
        assert pf.scope_mix(c["scope"]) == c["mix"], c
    for c in v["feasible"]:
        assert pf.feasible_classes(c["platforms"], c["stage_platforms"], c["job_platforms"], c["mix"]) == c["classes"], c


_MODELS = {"runner": mf.Runner, "coordinator": mf.Coordinator, "stage": mf.Stage}


def test_section_models_reproduce_the_variant_resolution_vectors():
    """Coordinator, Runner and Stage.for_platform give exactly the vector's resolved view."""
    for c in json.loads((VEC / "variant-resolution.json").read_text(encoding="utf-8"))["overlay"]:
        model = _MODELS[c["section"]]
        got = model.model_validate({**c["base"], "variants": c["variants"]}).for_platform(c["platform"])
        want = model.model_validate({**c["resolved"], "variants": c["variants"]})
        assert got.model_dump() == want.model_dump(), c


def test_current_platform_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("OARBANK_PLATFORM", "windows-arm64")
    assert (pf.current(), pf.os_(), pf.arch()) == ("windows-arm64", "windows", "arm64")
    monkeypatch.setenv("OARBANK_PLATFORM", "not a token")
    assert pf.current() == portable.host_platform()
    monkeypatch.delenv("OARBANK_PLATFORM")
    assert pf.current() == portable.host_platform()
    assert pf.os_("linux-amd64") == "linux" and pf.arch("linux-amd64") == "amd64"


def _doc():
    return tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))


def test_platforms_are_required():
    doc = _doc()
    doc["requires"].pop("platforms")
    with pytest.raises(ValidationError, match="platforms"):
        mf.Manifest.model_validate(doc)
    doc = _doc()
    doc["requires"]["platforms"] = ["darwin-arm64", "plan9-mips"]       # unknown but well formed: fine
    doc["requires"]["os"] = {"darwin": ">=15.0", "windows": ">=10.0.19045"}
    assert mf.Manifest.model_validate(doc).requires.platforms[1] == "plan9-mips"


@pytest.mark.parametrize("argv,ok", [
    (["python", "-I", "{bundle}/m.py"], True),
    (["{bundle}/bin/windows-amd64/runner.exe"], True),
    (["m.py"], False),                                   # neither the python token nor a bundle path
    (["/usr/bin/python3", "x"], False),
    (["{bundle}/../escape"], False),
    (["{bundle}/run.bat"], False),
    (["{bundle}/C:/x.exe"], False),
])
def test_exec_rules(argv, ok):
    if ok:
        mf.check_exec(argv)
    else:
        with pytest.raises(ValueError):
            mf.check_exec(argv)


def test_runtime_kind_matches_the_exec_head():
    doc = _doc()
    doc["runner"]["runtime"] = {"kind": "native"}
    with pytest.raises(ValidationError, match="token needs runtime kind"):
        mf.Manifest.model_validate(doc)


def test_runner_variants_resolve_most_specific_first():
    doc = _doc()
    doc["runner"]["variants"] = {
        "windows": {"exec": ["{bundle}/bin/windows/runner.exe"], "runtime": {"kind": "native"}},
        "windows-arm64": {"exec": ["{bundle}/bin/windows-arm64/runner.exe"], "runtime": {"kind": "native"}, "stop_grace_s": 5},
    }
    m = mf.Manifest.model_validate(doc)
    assert m.runner.for_platform("darwin-arm64").exec == ["python", "-I", "{bundle}/toy_runner.py"]
    assert m.runner.for_platform("windows-amd64").exec == ["{bundle}/bin/windows/runner.exe"]
    w = m.runner.for_platform("windows-arm64")
    assert w.exec == ["{bundle}/bin/windows-arm64/runner.exe"] and w.stop_grace_s == 5
    doc["runner"]["variants"] = {"haiku": {"stop_grace_s": 5}}
    with pytest.raises(ValidationError, match="neither a declared platform"):
        mf.Manifest.model_validate(doc)


def test_resolve_exec_substitutes_only_the_placeholders(tmp_path):
    assert mf.resolve_exec(["python", "-I", "{bundle}/m.py", "--x={bundle}"], tmp_path, "/py") == \
        ["/py", "-I", f"{tmp_path}/m.py", f"--x={tmp_path}"]


def test_reserved_capability_prefixes_belong_to_the_agent():
    doc = _doc()
    doc["probes"] = [{"name": "gpu.cuda", "exec": ["python", "-I", "{bundle}/p.py"]}]
    doc["requires"]["service_protocol"] = [1]
    with pytest.raises(ValidationError, match="provided by the agent"):
        mf.Manifest.model_validate(doc)


def test_bundle_digest_covers_modes_from_the_manifest(tmp_path):
    src = tmp_path / "toy"
    shutil.copytree(TOY, src)
    (src / "tool.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8", newline="\n")
    os.chmod(src / "tool.sh", 0o755)                         # the build host's mode is ignored ...
    _, a = B.build(src, tmp_path / "a.mfb")
    assert dict((f["path"], f["mode"]) for f in a.files)["tool.sh"] == "644"
    m = (src / "oarbank-module.toml").read_text(encoding="utf-8")
    (src / "oarbank-module.toml").write_text(m + '\n[bundle]\nexecutables = ["*.sh"]\n', encoding="utf-8", newline="\n")
    _, b = B.build(src, tmp_path / "b.mfb")                  # ... the manifest decides, and the digest changes
    assert dict((f["path"], f["mode"]) for f in b.files)["tool.sh"] == "755"
    assert a.content_digest != b.content_digest


@pytest.mark.parametrize("evil", ["..\\..\\evil.txt", "C:/Windows/x", "CON", "a/nul.txt", "dir./f", "Readme.md"])
def test_unsafe_or_colliding_member_names_are_refused(tmp_path, evil):
    out, _ = B.build(TOY, tmp_path / "ok.mfb")
    with tarfile.open(out, "r:gz") as t:
        members = [(m, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
    meta = json.loads(next(d for m, d in members if m.name == "bundle.json"))
    data = b"x"
    import hashlib
    meta["files"].append({"path": evil, "sha256": hashlib.sha256(data).hexdigest(), "mode": "644"})
    if evil == "Readme.md":                                    # collides with README.md, not a bad name by itself
        meta["files"].append({"path": "README.md", "sha256": hashlib.sha256(data).hexdigest(), "mode": "644"})
    meta["content_digest"] = B.content_digest(meta["files"])
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as t:
        def add(n, d):
            ti = tarfile.TarInfo(n)
            ti.size = len(d)
            t.addfile(ti, io.BytesIO(d))
        for m, d in members:
            if m.name != "bundle.json":
                add(m.name, d)
        add(evil, data)
        if evil == "Readme.md":
            add("README.md", data)
        add("bundle.json", json.dumps(meta).encode())
    bad = tmp_path / "bad.mfb"
    bad.write_bytes(gzip.compress(raw.getvalue()))
    with pytest.raises(B.BundleError):
        B.verify(bad, tmp_path / "unpacked")
    assert not (tmp_path / "unpacked").exists() and not (tmp_path.parent / "evil.txt").exists()


# ---------------------------------------------------------------------------- per-platform declarations

PP = ROOT / "tests" / "fixtures" / "manifests" / "per-platform.toml"


def _pp():
    return tomllib.loads(PP.read_text(encoding="utf-8"))


def _refused(doc, match):
    with pytest.raises(ValidationError, match=match):
        mf.Manifest.model_validate(doc)


def test_the_per_platform_fixture_resolves_per_platform():
    m = mf.load(PP)
    assert m.coordinator.for_platform("darwin-arm64").exec == ["python", "-I", "{bundle}/coordinator.py"]
    lin = m.coordinator.for_platform("linux-arm64")
    assert lin.exec[-1] == "--fork-server" and lin.concurrency == 2
    assert lin.timeouts_s == {"default": 10.0, "job.plan": 60.0} and lin.env == {"PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1"}
    w = m.runner.for_platform("windows-amd64")
    assert w.exec == ["{bundle}/native/windows-amd64/scorer.exe"] and w.runtime.kind == "native"
    assert w.env == {"MKL_CBWR": "COMPATIBLE", "OMP_NUM_THREADS": "1", "KMP_AFFINITY": "disabled"}
    render = m.stages[0].for_platform("windows-amd64")
    assert (render.timeout_s, render.requires.resources.cpu, render.requires.resources.mem_gb, render.retry.max_attempts) == (5400, 4, 10, 4)
    assert m.stages[0].for_platform("linux-amd64") is m.stages[0]
    assert m.placement.effective_bind() == "capacity" and m.mix() == "same-os"
    assert pf.feasible(m) == ["darwin", "linux"]                         # `score` runs on darwin-arm64 and linux-amd64 only
    assert pf.feasible(m, ["render"], mix="same-platform") == sorted(m.requires.platforms)
    assert pf.feasible(m, ["render"], platforms=["windows"]) == ["windows"]
    assert m.bundle.subset(["native/windows-amd64/scorer.exe", "native/linux-arm64/lib.so", "node/main.py"], "linux-arm64") == \
        ["native/linux-arm64/lib.so", "node/main.py"]
    assert mf.lint(m) == []


def test_old_modules_need_nothing_new():
    """Absent keys keep today's behaviour: any coordinator platform, mix `any`, no env, no file subsets."""
    m = mf.load(TOY / "oarbank-module.toml")
    assert m.requires.coordinator_platforms is None and m.placement is None and m.mix() == "any"
    assert m.core_keys_used() == [] and mf.lint(m) == []
    assert m.coordinator.for_platform("windows-amd64") is m.coordinator
    assert m.bundle.receives("toy_runner.py", "windows-arm64")
    assert pf.feasible(m) == ["*"]


@pytest.mark.parametrize("core", [">=2.1,<3", "<3", ">2.1", "==2.1.9"])
def test_every_new_key_needs_core_2_2(core):
    doc = _pp()
    doc["requires"]["core"] = core
    _refused(doc, "need requires.core >= 2.2")
    for k in (">=2.2", ">=2.2.0,<3", "~=2.2", "==2.3.1", ">=3"):
        doc["requires"]["core"] = k
        mf.Manifest.model_validate(doc)


@pytest.mark.parametrize("mutate", [
    lambda d: d["requires"].update(coordinator_platforms=["linux-amd64"]),
    lambda d: d["requires"].update(features=["placement"]),
    lambda d: d["requires"].update(unsupported={"runner": {"plan9": "no"}}),
    lambda d: d["coordinator"].update(env={"X": "1"}),
    lambda d: d["coordinator"].update(variants={"linux": {"concurrency": 2}}),
    lambda d: d["runner"].update(env={"X": "1"}),
    lambda d: d["runner"].update(variants={"linux": {"env": {"X": "1"}}}),
    lambda d: d["stages"][0].update(variants={"linux": {"timeout_s": 5}}),
    lambda d: d["stages"][0].update(placement={"mix": "same-os"}),
    lambda d: d.update(placement={"mix": "same-os"}),
    lambda d: d["results"].update(determinism_scope="os"),
    lambda d: d["results"].update(determinism_scope="arch"),
    lambda d: d.setdefault("bundle", {}).update(platform_files={"x/**": ["linux"]}),
    lambda d: d.update(datasets={"kinds": ["index"], "platform_bound": ["index"]}),
], ids=lambda f: "")
def test_each_new_key_alone_trips_the_core_floor(mutate):
    doc = _doc()                                     # toy: core >= 2.1
    mutate(doc)
    _refused(doc, "need requires.core >= 2.2")
    doc["requires"]["core"] = ">=2.2,<3"
    mf.Manifest.model_validate(doc)


def test_every_per_user_location_lies_in_the_home(tmp_path):
    # spec/platforms.md, "What the home is": a job's home is its work directory, so nothing kept there outlives it
    home = tmp_path / "w"
    env = portable.os_env(home, home / "tmp")
    names = (("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP") if os.name == "nt" else
             ("HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"))
    for n in names:
        assert Path(env[n]).is_relative_to(home), (n, env.get(n))
    assert all(k.upper() in mf.RESERVED_ENV for k in env), "a module's env may not move any of them"


@pytest.mark.parametrize("name", ["OARBANK_PLATFORM", "oarbank_x", "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP",
                                  "TMPDIR", "LOCALAPPDATA", "APPDATA", "XDG_CACHE_HOME", "xdg_config_home", "LANG",
                                  "HTTPS_PROXY", "PYTHONUTF8"])
def test_reserved_env_names_are_refused(name):
    for where in ("runner", "coordinator"):
        doc = _pp()
        doc[where]["env"] = {name: "x"}
        _refused(doc, "reserved|pattern")
    doc = _pp()
    doc["runner"]["variants"]["windows-amd64"]["env"] = {name: "x"}
    _refused(doc, "reserved|pattern")


@pytest.mark.parametrize("name", ["SystemRoot", "lower", "1ABC", "A-B", ""])
def test_env_names_are_upper_snake_case(name):
    doc = _pp()
    doc["runner"]["env"] = {name: "x"}
    _refused(doc, "pattern|reserved")


def test_unsupported_never_contradicts_the_allow_lists():
    doc = _pp()
    doc["requires"]["unsupported"]["runner"] = {"linux-amd64": "no"}
    _refused(doc, "contradicts requires.platforms")
    doc = _pp()
    doc["requires"]["unsupported"]["runner"] = {"windows": "no"}         # windows-amd64 is allowed
    _refused(doc, "contradicts requires.platforms")
    doc = _pp()
    doc["requires"]["unsupported"]["coordinator"] = {"darwin": "no"}
    _refused(doc, "contradicts requires.coordinator_platforms")
    doc = _pp()
    doc["requires"].pop("coordinator_platforms")
    _refused(doc, "absent means every platform")
    doc = _pp()
    doc["requires"]["unsupported"]["runner"] = {"Windows": "no"}
    _refused(doc, "pattern")


def test_variant_keys_are_declared_platforms_or_their_oses():
    doc = _pp()
    doc["coordinator"]["variants"]["windows"] = {"concurrency": 1}       # not a coordinator platform
    _refused(doc, "neither a coordinator platform")
    doc = _pp()
    doc["requires"].pop("coordinator_platforms")
    doc["requires"]["unsupported"].pop("coordinator")
    doc["coordinator"]["variants"]["windows"] = {"concurrency": 1}       # absent: any platform
    mf.Manifest.model_validate(doc)
    doc = _pp()
    doc["stages"][0]["variants"]["freebsd"] = {"timeout_s": 5}
    _refused(doc, "stage 'render': variant 'freebsd'")
    doc = _pp()
    doc["coordinator"]["variants"]["linux"]["runtime"] = {"kind": "native"}
    _refused(doc, "coordinator.variants.linux")


def test_stage_variant_resources_stay_within_bounds():
    for res in ({"mem_gb": 0}, {"cpu": 1000}, {"mem_gb": 2048}):
        doc = _pp()
        doc["stages"][0]["variants"]["windows"]["requires"]["resources"] = res
        _refused(doc, "greater than|less than")


def test_features_are_must_understand():
    doc = _pp()
    doc["requires"]["features"] = ["placement", "teleport"]
    _refused(doc, "not understood by this SDK")


def test_platform_bound_kinds_are_declared_kinds():
    doc = _pp()
    doc["datasets"]["platform_bound"] = ["texture"]
    _refused(doc, "not in datasets.kinds")


def test_platform_files_cover_each_platforms_execs():
    doc = _pp()
    doc["bundle"]["platform_files"]["native/windows-amd64/**"] = ["linux"]   # the windows scorer would go to Linux only
    _refused(doc, "windows-amd64: runner runs '{bundle}/native/windows-amd64/scorer.exe'")
    doc = _pp()
    doc["bundle"]["platform_files"]["node/**"] = ["darwin"]                  # the python runner kept from Linux nodes
    _refused(doc, "linux-amd64: runner runs '{bundle}/node/main.py'")
    doc = _pp()
    doc["bundle"]["platform_files"]["native/**"] = ["plan9"]
    _refused(doc, "neither declared platforms")
    doc = _pp()
    doc["bundle"]["platform_files"]["/abs/**"] = ["linux"]
    _refused(doc, "pattern")


def test_lint_warns_but_does_not_refuse(capsys, tmp_path):
    from oarbank_sdk.cli import main
    doc = _pp()
    doc["placement"]["mix"] = "same-rack"
    doc["stages"][0]["placement"] = {"mix": "same-os"}                     # `render` has no `after`
    doc["results"]["determinism_scope"] = "platform"
    m = mf.Manifest.model_validate(doc)
    w = mf.lint(m)
    assert any("'same-rack' is not known" in x for x in w) and any("has no `after`" in x for x in w)
    assert m.mix() == "same-platform"                                        # unknown: the strictest
    doc = _pp()
    doc["placement"]["mix"] = "any"
    m = mf.Manifest.model_validate(doc)
    assert any("coarser than results.determinism_scope 'os'" in x for x in mf.lint(m))
    doc["results"].pop("value")
    assert mf.lint(mf.Manifest.model_validate(doc)) == []                   # no value compared: nothing to warn about
    assert main(["check", str(PP)]) == 0
    text = PP.read_text(encoding="utf-8").replace('mix = "same-os"', 'mix = "same-rack"')
    p = ROOT / "tests" / "fixtures" / "manifests"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.toml"
        f.write_text(text, encoding="utf-8", newline="\n")
        assert main(["check", str(f)]) == 0
    assert "warning: placement.mix 'same-rack'" in capsys.readouterr().out


def test_bundle_platform_files_subset_and_lint(tmp_path):
    src = tmp_path / "toy"
    shutil.copytree(TOY, src)
    (src / "native" / "linux-amd64").mkdir(parents=True)
    (src / "native" / "linux-amd64" / "helper").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
    m = (src / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.2,<3"')
    (src / "oarbank-module.toml").write_text(m + '\n[bundle.platform_files]\n"native/linux-*/**" = ["linux"]\n"native/windows-*/**" = ["windows"]\n', encoding="utf-8", newline="\n")
    _, info = B.build(src, tmp_path / "a.mfb")
    files = [f["path"] for f in info.files]
    assert "native/linux-amd64/helper" in files                         # the bundle and its digest stay whole
    assert "native/linux-amd64/helper" not in B.subset(files, info.manifest, "darwin-arm64")
    assert "native/linux-amd64/helper" in [f["path"] for f in B.subset(info.files, info.manifest, "linux-arm64")]
    assert B.platform_lint(info.manifest, files) == ["bundle.platform_files 'native/windows-*/**' matches no file"]
    (src / "oarbank-module.toml").write_text(m + '\n[bundle.platform_files]\n"*.toml" = ["linux"]\n', encoding="utf-8", newline="\n")
    with pytest.raises(B.BundleError, match="goes to every node"):
        B.build(src, tmp_path / "b.mfb")


def test_lint_warns_about_runner_capabilities_the_agent_does_not_know():
    doc = _doc()
    doc["runner"]["capabilities"] = ["cancellable", "cancel_signal"]
    doc["runner"]["variants"] = {"windows": {"capabilities": ["pausable"]}}
    w = mf.lint(mf.Manifest.model_validate(doc))
    assert "runner.capabilities 'cancel_signal' is not known to this SDK; the agent ignores it" in w
    assert "runner.variants.windows.capabilities 'pausable' is not known to this SDK; the agent ignores it" in w
    assert not any("'cancellable'" in x for x in w)


def test_windows_platforms_need_container_images_of_their_architecture():
    """A Windows node's container runtime runs its own architecture only (spec/sandbox.md, "Containers"): a module that
    runs containers on windows-arm64 without a linux/arm64 image or set gets a warning (not a refusal: its containers
    may run only in stages that never go there)."""
    doc = tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))
    doc["requires"]["platforms"] = ["linux-amd64", "windows-amd64", "windows-arm64"]
    doc["sandbox"] = {"containers": [{"image": "docker.io/o/t:1@sha256:" + "b" * 64, "platform": "linux/amd64"}]}
    warned = [w for w in mf.lint(mf.Manifest.model_validate(doc)) if "container" in w]
    assert warned == ["requires.platforms lists windows-arm64, but no container image or set is for linux/arm64: Windows nodes "
                      "run containers of their own architecture only, so its containers cannot run there"]
    doc["sandbox"]["containers"].append({"image": "docker.io/o/t:1@sha256:" + "c" * 64, "platform": "linux/arm64"})
    assert not [w for w in mf.lint(mf.Manifest.model_validate(doc)) if "container" in w]
    del doc["sandbox"]
    assert not [w for w in mf.lint(mf.Manifest.model_validate(doc)) if "container" in w]
