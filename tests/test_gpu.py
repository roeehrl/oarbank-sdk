"""GPU APIs: detection, the manifest's rules and needs, NodeClass.gpu_apis and the conformance kit's gpu suite
(spec/runner-protocol.md, "GPU use")."""
import copy
import json
import shutil
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import gpu, manifest as m, module_protocol as mp, portable
from oarbank_sdk.cli import main
from oarbank_sdk.conformance import conform

EXAMPLES = Path(__file__).parents[1] / "examples"
GPUINFO = EXAMPLES / "gpuinfo"


def gpuinfo() -> dict:
    return tomllib.loads((GPUINFO / "oarbank-module.toml").read_text(encoding="utf-8"))


def model(d: dict) -> m.Manifest:
    return m.Manifest.model_validate(d)


def test_detect_names_every_known_api_with_its_evidence():
    found = gpu.detect()
    assert set(found) == {"host", "containers", "evidence"}
    assert set(found["evidence"]) == set(gpu.KNOWN_APIS) == set(gpu.PROBES)
    assert found["host"] == sorted(found["host"]) and set(found["host"]) <= set(gpu.KNOWN_APIS)
    assert found["containers"] == []                       # only an agent knows its container runtime
    assert all(isinstance(v, str) and v for v in found["evidence"].values())
    if portable.host_platform() == "darwin-arm64":         # every Apple Silicon Mac has a Metal GPU
        assert "metal" in found["host"] and found["evidence"]["metal"].startswith("Apple")
    else:
        assert "metal" not in found["host"]


def test_the_cli_prints_the_detection_with_the_platform(capsys):
    assert main(["gpu-apis"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["platform"] == portable.host_platform() and set(out["host"]) <= set(gpu.KNOWN_APIS)


def test_fits_unmet_and_describe():
    assert gpu.fits([], []) and gpu.fits(["cuda", "vulkan"], ["metal", "vulkan"]) and not gpu.fits(["cuda"], ["metal"])
    needs = [{"apis": ["cuda"], "where": "host", "source": "runner"},
             {"apis": ["vulkan"], "where": "containers", "source": "runner"}]
    assert gpu.unmet(needs, {"host": ["cuda"], "containers": ["vulkan"]}) == []
    assert gpu.unmet(needs, {"host": ["vulkan"], "containers": ["cuda"]}) == needs
    assert gpu.unmet(needs, {}) == needs
    assert gpu.describe(needs[0]) == "cuda on the host"
    assert gpu.describe({"apis": ["metal", "vulkan"], "where": "containers"}) == "one of metal, vulkan in containers"


def test_a_runners_needs_follow_its_platform_variant():
    man = model(gpuinfo())
    host = [{"apis": ["cuda", "opencl", "rocm", "vulkan"], "where": "host", "source": "runner"}]
    assert man.gpu_needs(None, "linux-amd64") == host == man.gpu_needs("squares", "linux-arm64")
    assert man.gpu_needs(None, "darwin-arm64") == [{"apis": ["metal"], "where": "host", "source": "runner"}]
    assert man.gpu_needs(None, "windows-arm64")[0]["apis"] == ["cuda", "directml", "opencl", "rocm", "vulkan"]
    d = gpuinfo()
    d["runner"]["variants"]["darwin"]["gpu"] = {"use": "none"}            # no GPU there: no need there
    assert model(d).gpu_needs(None, "darwin-amd64") == []
    d["runner"]["gpu"] = {"use": "shared"}                                 # a GPU, any API
    assert model(d).gpu_needs(None, "linux-amd64") == []


def test_container_gpu_needs_bind_only_stages_with_the_gpu_pool():
    d = gpuinfo()
    d["runner"]["gpu"] = {"use": "exclusive", "in_container": True, "apis_any": ["vulkan"]}
    d["runner"]["variants"] = {}
    d["sandbox"]["containers"] = [{"image": "docker.io/library/alpine@sha256:" + "a" * 64, "platform": "linux/arm64"}]
    d["stages"] = [{"name": "squares", "default": True, "requires": {"pools": {"containers": 1, "gpu": 1}}},
                   {"name": "plain", "requires": {"pools": {"containers": 1}}}]
    d["coordinator"]["capabilities"] = ["result.merge"]
    man = model(d)
    assert man.gpu_needs("squares", "darwin-arm64") == [{"apis": ["vulkan"], "where": "containers", "source": "runner"}]
    assert man.gpu_needs("plain", "darwin-arm64") == []


def test_a_gpu_service_binds_the_stages_that_reserve_its_pools_on_its_platforms():
    d = gpuinfo()
    d["runner"]["gpu"], d["runner"]["variants"] = {"use": "none"}, {}
    d["requires"]["service_protocol"] = [1]
    d["services"] = [{"name": "model", "exec": ["python", "{bundle}/s.py"], "provides": {"pools": ["model"]},
                      "platforms": ["linux-amd64", "darwin-arm64"], "gpu": {"use": "shared", "apis_any": ["cuda", "metal"]}}]
    d["stages"] = [{"name": "squares", "default": True, "requires": {"pools": {"model": 1}}},
                   {"name": "other", "requires": {"needs_pools": ["model"]}}]
    d["coordinator"]["capabilities"] = ["result.merge"]
    man = model(d)
    svc = [{"apis": ["cuda", "metal"], "where": "host", "source": "service model"}]
    assert man.gpu_needs("squares", "linux-amd64") == svc == man.gpu_needs(None, "darwin-arm64")
    assert man.gpu_needs("squares", "windows-amd64") == []                 # the service does not run there
    assert man.gpu_needs("other", "linux-amd64") == []                     # needs_pools reaches no service


def bad(d: dict, match: str):
    with pytest.raises(ValidationError, match=match):
        model(d)


def test_apis_any_needs_gpu_use_and_core_2_5():
    d = gpuinfo()
    d["runner"]["gpu"]["use"] = "none"
    bad(d, r"runner.gpu.apis_any needs use = 'shared' or 'exclusive'")
    d = gpuinfo()
    d["runner"]["variants"]["darwin"]["gpu"]["use"] = "none"
    bad(d, r"runner.variants.darwin.gpu.apis_any needs use")
    d = gpuinfo()
    d["requires"]["core"] = ">=2.4,<3"
    bad(d, r"runner.gpu.apis_any need requires.core >= 2.5")
    d = gpuinfo()
    d["runner"]["gpu"] = {"use": "shared"}
    d["requires"]["core"] = ">=2.4,<3"
    d["runner"]["variants"]["windows"]["gpu"] = {"use": "shared"}
    bad(d, r"runner.gpu.apis_any need requires.core >= 2.5")                 # a variant naming APIs needs it too
    d["runner"]["variants"]["darwin"]["gpu"] = {"use": "shared"}
    assert ("runner.gpu.apis_any", (2, 5)) not in model(d).core_keys_used()
    d = gpuinfo()
    d["requires"]["service_protocol"] = [1]
    d["services"] = [{"name": "s", "exec": ["python", "{bundle}/s.py"], "gpu": {"use": "none", "apis_any": ["cuda"]}}]
    bad(d, r"services.s.gpu.apis_any needs use")


def test_an_api_no_core_detects_is_a_lint_warning():
    assert m.lint(model(gpuinfo())) == []
    d = gpuinfo()
    d["runner"]["variants"]["darwin"]["gpu"]["apis_any"] = ["metal", "webgpu"]
    warn = m.lint(model(d))
    assert len(warn) == 1 and "runner.variants.darwin.gpu.apis_any ['webgpu']: no core detects it yet" in warn[0]


def test_node_class_carries_the_gpu_apis():
    nc = mp.NodeClass.model_validate({"platform": "darwin-arm64", "gpu_apis": {"host": ["metal"], "containers": ["vulkan"]}})
    assert nc.gpu_apis.host == ["metal"] and nc.gpu_apis.containers == ["vulkan"]
    assert mp.NodeClass().gpu_apis.host == []                             # an older host sends none


def fake_host(monkeypatch, host: list[str]):
    monkeypatch.setattr(gpu, "detect", lambda: {"host": host, "containers": [], "evidence": {}})


def test_the_kit_runs_goldens_where_the_host_provides_the_runners_api(monkeypatch):
    api = "metal" if portable.host_platform().startswith("darwin") else "cuda"
    fake_host(monkeypatch, [api])
    rep = conform(GPUINFO, sandbox=False)
    assert rep.ok, rep.text()
    gpu_checks = [c for c in rep.checks if c.suite == "gpu"]
    assert [c.status for c in gpu_checks] == ["pass"] and api in gpu_checks[0].detail
    assert any(c.name == "golden gpuinfo-golden (squares): matches the golden" and c.status == "pass" for c in rep.checks)
    host_class = [c for c in rep.checks if c.name.startswith("goldens exist") and portable.host_platform() in c.name]
    assert host_class and f'"host": ["{api}"]' in host_class[0].name    # this host's node class carries its APIs


def test_the_kit_skips_work_this_host_could_never_get(monkeypatch):
    fake_host(monkeypatch, ["opencl"] if portable.host_platform().startswith("darwin") else ["metal"])
    rep = conform(GPUINFO, sandbox=False)
    assert rep.ok, rep.text()                                             # skipped, never failed
    g = [c for c in rep.checks if c.suite == "gpu"]
    assert [c.status for c in g] == ["skip"] and "so a node like it gets none of these jobs" in g[0].detail
    golden = [c for c in rep.checks if c.name == "golden gpuinfo-golden (squares)"]
    assert golden and golden[0].status == "skip" and golden[0].detail.startswith("not run here: its runner needs")
    assert any(c.name == "doctor --json" for c in rep.checks)            # the runner's doctor still runs


def test_the_kit_skips_a_gpu_service_whose_api_the_host_lacks(monkeypatch, tmp_path):
    d = tmp_path / "modelserver"
    shutil.copytree(EXAMPLES / "modelserver", d, ignore=shutil.ignore_patterns("__pycache__"))
    toml = (d / "oarbank-module.toml").read_text(encoding="utf-8")
    toml = toml.replace("yieldable = true", 'yieldable = true\ngpu = { use = "shared", apis_any = ["cuda"] }')
    (d / "oarbank-module.toml").write_text(toml + '\n[sandbox]\ndevices = { gpu = "compute" }\n', encoding="utf-8")
    fake_host(monkeypatch, ["metal"])
    rep = conform(d, sandbox=False)
    assert rep.ok, rep.text()
    svc = [c for c in rep.checks if c.name == "service model"]
    assert svc and svc[0].status == "skip" and "needs one of cuda on the host" in svc[0].detail
    golden = [c for c in rep.checks if c.name == "golden modelserver-golden (generate)"]
    assert golden and golden[0].detail.startswith("not run here: its service model needs cuda on the host")
