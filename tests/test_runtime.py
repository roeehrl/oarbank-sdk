"""The module runtime (rpc, server, client) and the toy reference module, end to end over real pipes."""
import json
import subprocess
import sys
import textwrap
import time
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path

import pytest

from oarbank_sdk import envelopes as e, module_protocol as mp
from oarbank_sdk.client import ModuleClient
from oarbank_sdk.rpc import ConnectionClosed, RpcError

TOY = Path(__file__).parents[1] / "examples" / "toy"


def spawn_toy(**kw):
    return ModuleClient.spawn([sys.executable, "-I", "toy_module.py"], cwd=TOY, **kw)


def spawn_src(tmp_path, src, **kw):
    f = tmp_path / "m.py"
    f.write_text(textwrap.dedent(src), encoding="utf-8", newline="\n")
    return ModuleClient.spawn([sys.executable, "-I", str(f)], cwd=tmp_path, **kw)


def test_toy_handshake_and_verbs():
    with spawn_toy() as m:
        info = m.initialize()
        assert (info.protocol_version, info.module.id) == (1, "dev.codonic.oarbank.toy")
        assert info.capabilities == ["integrity.check", "move.cancelled", "move.postflight", "move.preflight", "op.apply", "op.plan", "ui.view.compute"]
        assert m.call("params.check", {"params": {"n": 5}}) == {"ok": True, "normalized_params": {"n": 5}, "errors": []}
        bad = mp.ParamsCheckResult.model_validate(m.call("params.check", {"params": {"n": -1}}))
        assert not bad.ok and bad.errors[0].code == "toy/n_range"
        plan = mp.JobPlanResult.model_validate(m.call("job.plan", {"params": {"n": 5}}))
        built = mp.SpecBuildResult.model_validate(
            m.call("spec.build", {"jobs": [j.model_dump() for j in plan.jobs], "target_spec_version": 1}))
        assert built.specs[0].stages[0].payload == {"n": 5}


def test_toy_end_to_end_with_the_runner(tmp_path):
    """spec.build -> spec envelope -> runner -> result envelope -> result.evaluate, and the golden matches."""
    with spawn_toy() as m:
        m.initialize()
        golden = mp.GoldenListResult.model_validate(m.call("golden.list", {"node_class": {}})).goldens[0]
        stage = mp.SpecBuildResult.model_validate(m.call("spec.build", {
            "jobs": [{"key_inputs": golden.key_inputs, "stages": ["run"]}], "target_spec_version": 1})).specs[0].stages[0]
        spec = e.SpecEnvelope(schema="toy/spec@1", module_id="dev.codonic.oarbank.toy", module_version="0.1.0",
                              job_key="k" * 64, stage="run", payload=stage.payload)
        (tmp_path / "spec.json").write_text(spec.model_dump_json(by_alias=True), encoding="utf-8", newline="\n")
        rc = subprocess.run([sys.executable, "-I", str(TOY / "toy_runner.py"), "run", "--spec", str(tmp_path / "spec.json"),
                             "--workdir", str(tmp_path), "--out", str(tmp_path / "result.json")]).returncode
        assert rc == 0
        result = e.ResultEnvelope.model_validate_json((tmp_path / "result.json").read_text(encoding="utf-8"))
        v = mp.ResultEvaluateResult.model_validate(m.call("result.evaluate", {
            "spec": json.loads(spec.model_dump_json(by_alias=True)), "result": json.loads(result.model_dump_json(by_alias=True))}))
        assert v.verdict == "accept" and v.value == 499500.0
        assert v.digest == golden.expected["digest"] and v.digest_version == golden.expected["digest_version"]
        tampered = json.loads(result.model_dump_json(by_alias=True))
        tampered["payload"]["sum"] = "1"
        assert m.call("result.evaluate", {"spec": json.loads(spec.model_dump_json(by_alias=True)), "result": tampered})["verdict"] == "reject"


def test_runner_doctor_and_bad_spec(tmp_path):
    out = subprocess.run([sys.executable, "-I", str(TOY / "toy_runner.py"), "doctor", "--json"], capture_output=True, text=True, encoding="utf-8")
    assert json.loads(out.stdout)["health"] == "healthy"
    (tmp_path / "spec.json").write_text(json.dumps({"payload": {"n": -3}}), encoding="utf-8", newline="\n")
    rc = subprocess.run([sys.executable, "-I", str(TOY / "toy_runner.py"), "run", "--spec", str(tmp_path / "spec.json"),
                         "--workdir", str(tmp_path), "--out", str(tmp_path / "r.json")]).returncode
    assert rc == 2 and json.loads((tmp_path / "failure.json").read_text(encoding="utf-8"))["reason"] == "toy/bad_spec"


def test_error_codes():
    with spawn_toy() as m:
        with pytest.raises(RpcError) as ei:
            m.call("params.check", {"params": {}})             # before initialize
        assert ei.value.code == mp.ERR_INVALID_REQUEST
        with pytest.raises(RpcError) as ei:
            m.initialize(protocol_versions=[7])
        assert ei.value.code == mp.ERR_UNSUPPORTED_PROTOCOL and ei.value.data["supported"] == [1]
        m.initialize()
        for method, params, code in [("nope.verb", {}, mp.ERR_METHOD_NOT_FOUND),
                                     ("result.merge", {"stages": {}}, mp.ERR_CAPABILITY_MISSING),
                                     ("golden.compare", {"expected": {}, "result": {}}, mp.ERR_CAPABILITY_MISSING),
                                     ("params.check", {"wrong": 1}, mp.ERR_INVALID_PARAMS)]:
            with pytest.raises(RpcError) as ei:
                m.call(method, params)
            assert ei.value.code == code, method


MODULE_HEADER = """
    import time
    from oarbank_sdk import module_protocol as mp
    from oarbank_sdk.server import Module
    m = Module("dev.test.m", "0.0.1", concurrency=4)
    @m.verb("job.plan")
    def plan(p, ctx):
        return {"jobs": []}
    @m.verb("spec.build")
    def build(p, ctx):
        return {"specs": []}
    @m.verb("result.evaluate")
    def ev(p, ctx):
        return {"verdict": "accept"}
    @m.verb("golden.list")
    def gl(p, ctx):
        return {"goldens": []}
"""


def test_prints_never_corrupt_the_protocol_and_logs_arrive(tmp_path):
    src = MODULE_HEADER + """
    @m.verb("params.check")
    def check(p, ctx):
        print("debugging output that must not break JSON-RPC")
        ctx.log("checked", n=p.params.get("n"))
        return {"ok": True}
    m.run()
    """
    with spawn_src(tmp_path, src) as c:
        c.initialize()
        assert c.call("params.check", {"params": {"n": 1}})["ok"] is True
        time.sleep(0.2)
        assert any(l["msg"] == "checked" for l in c.logs)
        assert any("debugging output" in l for l in c.stderr)


def test_host_callbacks_and_permissions(tmp_path):
    src = MODULE_HEADER + """
    @m.verb("params.check")
    def check(p, ctx):
        ds = ctx.host.datasets_query("scene", {"scene": "atrium"})
        return {"ok": bool(ds.datasets), "normalized_params": {"n": len(ds.datasets)}}
    m.run()
    """
    seen = []

    def query(params):
        seen.append(params)
        return {"datasets": [{"id": "scene:1", "kind": "scene"}]}

    with spawn_src(tmp_path, src, callbacks={"host.datasets.query": query}, permissions={"datasets:read"}) as c:
        c.initialize()
        assert c.call("params.check", {"params": {}})["normalized_params"] == {"n": 1}
        assert seen[0]["kind"] == "scene" and seen[0]["attrs"] == {"scene": "atrium"}
    with spawn_src(tmp_path, src, callbacks={"host.datasets.query": query}, permissions=set()) as c:
        c.initialize()
        with pytest.raises(RpcError) as ei:
            c.call("params.check", {"params": {}})
        assert ei.value.code == mp.ERR_PERMISSION_DENIED        # an unhandled denial propagates to the host as-is


def test_timeout_cancels_and_concurrent_requests_proceed(tmp_path):
    src = MODULE_HEADER + """
    @m.verb("params.check")
    def check(p, ctx):
        if p.params.get("slow"):
            while not ctx.cancelled:
                time.sleep(0.01)
            return {"ok": False}
        return {"ok": True}
    m.run()
    """
    with spawn_src(tmp_path, src) as c:
        c.initialize()
        slow = c.peer.request_async("params.check", {"params": {"slow": True}})
        assert c.call("params.check", {"params": {}}, timeout=5)["ok"] is True        # not blocked by the slow one
        with pytest.raises((TimeoutError, FutTimeout)):
            c.call("params.check", {"params": {"slow": True}}, timeout=0.3)
        c.peer.cancel(slow.rpc_id)
        with pytest.raises((TimeoutError, FutTimeout)):
            slow.result(1)
        assert c.call("params.check", {"params": {}}, timeout=5)["ok"] is True        # module still healthy


def test_a_module_crash_fails_pending_requests_fast(tmp_path):
    src = MODULE_HEADER + """
    import os
    @m.verb("params.check")
    def check(p, ctx):
        os._exit(3)
    m.run()
    """
    with spawn_src(tmp_path, src) as c:
        c.initialize()
        t = time.monotonic()
        with pytest.raises(ConnectionClosed):
            c.call("params.check", {"params": {}}, timeout=10)
        assert time.monotonic() - t < 5
        assert c.proc.wait(5) == 3


def test_missing_required_verbs_fail_fast(tmp_path):
    src = """
    from oarbank_sdk.server import Module
    Module("dev.test.m", "0.0.1").run()
    """
    f = tmp_path / "m.py"
    f.write_text(textwrap.dedent(src), encoding="utf-8", newline="\n")
    p = subprocess.run([sys.executable, "-I", str(f)], capture_output=True, text=True, timeout=20, input="", encoding="utf-8")
    assert p.returncode != 0 and "does not implement required verbs" in p.stderr
