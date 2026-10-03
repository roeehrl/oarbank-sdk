"""Per-platform declarations for coordinator-side code (spec/module-protocol.md): optional protocol fields, the host
capability names, Host.fleet_platforms and ctx.host_has, the effect builders and the goldens helper."""
import json

import pytest
from pydantic import ValidationError

from oarbank_sdk import effects as fx, goldens, module_protocol as mp
from test_runtime import MODULE_HEADER, spawn_src


def test_old_messages_still_validate_and_new_fields_are_optional():
    assert mp.HostInfo(version="2.1.0").platform is None
    assert mp.PlanItem(key_inputs={"n": 1}).model_dump(exclude_defaults=True) == {"key_inputs": {"n": 1}}
    g = mp.Golden(name="g", key_inputs={}, expected={"digest": "a", "digest_version": 1})
    assert (g.platforms, g.expected_by_platform) == ([], {})
    assert mp.CampaignJob(job_id=1, job_key="k", state="done").platform is None
    assert mp.NodesQueryParams().platforms == []
    with pytest.raises(ValidationError):
        mp.PlanItem(key_inputs={}, group="x" * 65)
    with pytest.raises(ValidationError):
        mp.PlanItem(key_inputs={}, platforms=["Linux"])
    assert set(mp.HOST_CAPABILITIES) == {"placement.v1", "nodes.platform", "goldens.by_platform", "coordinator.variants"}


def test_golden_resolution_per_platform():
    g = mp.Golden(name="g", key_inputs={}, expected={"digest": "default", "digest_version": 1}, platforms=["linux", "darwin-arm64"],
                  expected_by_platform={"linux": {"digest": "lin", "digest_version": 1},
                                        "linux-arm64": {"digest": "lin-arm", "digest_version": 1}})
    assert [g.runs_on(p) for p in ("linux-amd64", "darwin-arm64", "darwin-amd64", "windows-amd64", None)] == [True, True, False, False, False]
    assert g.for_platform("linux-arm64").expected["digest"] == "lin-arm"
    assert g.for_platform("linux-amd64").expected["digest"] == "lin"
    assert g.for_platform("darwin-arm64").expected["digest"] == "default"
    assert g.for_platform("linux-amd64").expected_by_platform == {}
    assert g.for_platform(None).expected["digest"] == "default"


def test_goldens_load_filters_and_resolves(tmp_path):
    d = tmp_path / "goldens"
    d.mkdir()
    exp = {"digest": "d", "digest_version": 1}
    (d / "a.json").write_text(json.dumps({"name": "any", "key_inputs": {"n": 1}, "expected": exp}), encoding="utf-8", newline="\n")
    (d / "b.json").write_text(json.dumps([
        {"name": "win", "key_inputs": {"n": 2}, "expected": exp, "platforms": ["windows"]},
        {"name": "by", "key_inputs": {"n": 3}, "expected": exp, "expected_by_platform": {"windows-amd64": {"digest": "w", "digest_version": 1}}},
    ]), encoding="utf-8", newline="\n")
    names = lambda gs: [(g.name, g.expected["digest"]) for g in gs]
    assert names(goldens.load(tmp_path, "goldens/*.json", {"platform": "windows-amd64"})) == [("any", "d"), ("win", "d"), ("by", "w")]
    assert names(goldens.load(tmp_path, "goldens/*.json", mp.NodeClass(platform="linux-amd64"))) == [("any", "d"), ("by", "d")]
    assert names(goldens.load(tmp_path, "goldens/*.json")) == [("any", "d"), ("by", "d")]


def test_effect_builders_for_placement():
    assert fx.placement("same-os") == {"mix": "same-os"}
    assert fx.placement("same-platform", pin="linux-amd64", unit="campaign") == {"mix": "same-platform", "unit": "campaign", "pin": "linux-amd64"}
    assert fx.placement("same-os", pin="linux", bind="explicit") == {"mix": "same-os", "bind": "explicit", "pin": "linux"}
    for mix, pin in (("same-platform", "linux"), ("same-os", "linux-amd64"), ("any", "linux"), ("Same", None)):
        with pytest.raises(ValueError):
            fx.placement(mix, pin=pin)
    e = fx.campaign_create("c_bench_linux_amd64", "bench linux", placement=fx.placement("same-platform", pin="linux-amd64"))
    assert e.kind == "campaigns.create" and e.args == {"campaign_id": "c_bench_linux_amd64", "name": "bench linux", "priority": 0,
                                                       "weight": 1, "placement": {"mix": "same-platform", "pin": "linux-amd64"}}
    assert "placement" not in fx.campaign_create("c_old", "x").args
    with pytest.raises(ValueError):
        fx.campaign_create("Bad-Id", "x")
    j = fx.job("k" * 64, {"n": 1}, group="atrium", platforms=["linux", "darwin-arm64"], labels={"n": 1})
    assert j == {"job_key": "k" * 64, "spec": {"n": 1}, "group": "atrium", "platforms": ["linux", "darwin-arm64"], "labels": {"n": 1}}
    assert fx.job("k", {}) == {"job_key": "k", "spec": {}}
    for bad in ({"group": ""}, {"group": "g" * 65}, {"platforms": ["Linux"]}):
        with pytest.raises(ValueError):
            fx.job("k", {}, **bad)
    q = fx.jobs_enqueue("c_bench", [j, {"job_key": "k2", "spec": {}}])
    assert q.kind == "jobs.enqueue" and len(q.args["jobs"]) == 2
    with pytest.raises(ValueError):
        fx.jobs_enqueue("c_bench", [j] * 5001)
    ds = fx.datasets_create("idx:1", "index", [{"path": "a", "digest": "d" * 64, "size": 1}], platform="linux-amd64")
    assert ds.args["platform"] == "linux-amd64" and "platform" not in fx.datasets_create("idx:2", "index", []).args
    with pytest.raises(ValueError):
        fx.datasets_create("idx:3", "index", [], platform="linux")


def test_host_platform_capabilities_and_fleet_platforms(tmp_path):
    src = MODULE_HEADER + """
    from oarbank_sdk import module_protocol as mp
    @m.verb("params.check")
    def check(p, ctx):
        return {"ok": True, "normalized_params": {"platform": ctx.host_platform, "placement": ctx.host_has(mp.HOST_PLACEMENT),
                                                  "fleet": ctx.host.fleet_platforms(),
                                                  "linux": [n["node_id"] for n in ctx.host.nodes_query(platforms=["linux"])]}}
    m.run()
    """
    nodes = [{"node_id": "a", "platform": "linux-amd64"}, {"node_id": "b", "platform": "linux-amd64"},
             {"node_id": "c", "platform": "darwin-arm64"}, {"node_id": "d"}]
    seen = []

    def query(p):
        seen.append(p)
        keep = p.get("platforms") or []
        return {"nodes": [n for n in nodes if not keep or (n.get("platform") or "").split("-")[0] in keep]}

    with spawn_src(tmp_path, src, callbacks={"host.nodes.query": query}, permissions={"nodes:read"},
                   host_platform="linux-arm64", host_capabilities=[mp.HOST_PLACEMENT]) as c:
        c.initialize()
        r = c.call("params.check", {"params": {}})["normalized_params"]
        assert r == {"platform": "linux-arm64", "placement": True, "fleet": {"darwin-arm64": 1, "linux-amd64": 2}, "linux": ["a", "b"]}
        assert seen[0] == {"certified_for_self": True, "platforms": []}
    with spawn_src(tmp_path, src, callbacks={"host.nodes.query": query}, permissions={"nodes:read"},
                   env={"OARBANK_PLATFORM": "windows-amd64"}) as c:                # an older host: no platform, no capabilities
        c.initialize()
        r = c.call("params.check", {"params": {}})["normalized_params"]
        assert r["platform"] == "windows-amd64" and r["placement"] is False
