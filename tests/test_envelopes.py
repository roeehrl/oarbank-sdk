import json

import pytest
from pydantic import ValidationError

from oarbank_sdk import envelopes as e, module_protocol as mp, runner_protocol as rp, service_protocol as sp

SPEC = {
    "envelope": 1, "schema": "render/spec@1", "module_id": "dev.example.render", "module_version": "2.0.0",
    "job_key": "ab" * 32, "stage": "score", "protocol": 1,
    "datasets": ["reference:atrium", "art:" + "cd" * 32], "mounts": {"reference:atrium": "reference"},
    "inputs": {"frame": {"dataset": "art:" + "cd" * 32, "mount": "inputs/frame"}},
    "resources": {"cpu": 0.5, "mem_gb": 1.0, "pools": {"imagediff": 1}}, "timeout_s": 600,
    "payload": {"frames": "1-24", "render_options": {"denoiser": "none"}},
}
RESULT = {
    "envelope": 1, "schema": "render/result@1", "module_version": "2.0.0", "protocol": 1,
    "effective": {"runtime": "native-arm64", "sampler": "sobol-owen"},
    "provenance": {"argv": {"renderer": ["renderer", "--scene", "x", "--frames", "1-24"]}, "tool_versions": {"renderer": "4.2.0"}},
    "artifacts": [{"name": "frame", "files": [{"path": "frame.exr", "local": "out/frame.exr"}]}],
    "payload": {"score": 0.947512, "tiles": 412},
}


@pytest.mark.parametrize("model,data", [(e.SpecEnvelope, SPEC), (e.ResultEnvelope, RESULT)])
def test_round_trip(model, data):
    obj = model.model_validate(data)
    again = json.loads(obj.model_dump_json(by_alias=True, exclude_none=True))
    assert model.model_validate(again) == obj
    assert again["schema"] == data["schema"]


def test_unknown_top_level_fields_survive_a_round_trip():
    """Forward compatibility: an older core must store a newer runner's result without losing fields."""
    r = e.ResultEnvelope.model_validate({**RESULT, "future": {"x": 1}})
    assert json.loads(r.model_dump_json(by_alias=True))["future"] == {"x": 1}


@pytest.mark.parametrize("ref", ["render/spec", "render/spec@0", "Render/spec@1", "render/other@1"])
def test_schema_ref_format(ref):
    with pytest.raises(ValidationError):
        e.SpecEnvelope.model_validate({**SPEC, "schema": ref})


def test_artifact_digest_is_sha256():
    bad = {**RESULT, "artifacts": [{"name": "frame", "files": [{"path": "a", "digest": "xyz"}]}]}
    with pytest.raises(ValidationError):
        e.ResultEnvelope.model_validate(bad)


def test_runner_doctor_and_control():
    d = rp.DoctorOutput.model_validate({"runner_protocol": {"supported": [1]}, "health": "undetected"})
    assert d.checks == []
    with pytest.raises(ValidationError):
        rp.Control.model_validate({"seq": 1, "gpu_duty": 1.5})
    assert rp.EXIT_RETRYABLE == 75


def test_service_fingerprint():
    f = sp.Fingerprint.model_validate({"service_protocol": {"supported": [1]}, "health": "healthy", "pools": {"imagediff": 2},
                                       "reserve": {"mem_gb": 8.0}, "running": True})
    assert f.pools["imagediff"] == 2


def test_module_protocol_handshake_and_verdicts():
    init = mp.InitializeParams.model_validate({"protocol_versions": [1], "host": {"version": "2.0.0"}})
    assert init.host.name == "oarbank"
    res = mp.InitializeResult.model_validate({"protocol_version": 1, "capabilities": ["result.merge"],
                                              "module": {"id": "dev.codonic.oarbank.toy", "version": "0.1.0"}})
    assert res.module.id == "dev.codonic.oarbank.toy"
    ok = mp.ResultEvaluateResult.model_validate({"verdict": "accept", "value": 1.0, "digest": "d", "digest_version": 1})
    assert ok.reason == "ok"
    with pytest.raises(ValidationError):
        mp.ResultEvaluateResult.model_validate({"verdict": "maybe"})


def test_required_verbs_are_the_minimal_module():
    required = sorted(m for m, (_, _, req, _) in mp.VERBS.items() if req)
    assert required == ["golden.list", "job.plan", "params.check", "result.evaluate", "spec.build"]
    for m, (_, _, req, cap) in mp.VERBS.items():
        assert req == (cap is None)


def test_failure_reasons_are_the_agents_or_the_modules_own():
    from typing import get_args
    from pydantic import ValidationError
    from oarbank_sdk import runner_protocol as rp
    assert get_args(rp.FailureReason) == rp.FAILURE_REASONS
    for ok in [*rp.FAILURE_REASONS, "toy/bad_spec", "primes/stopped"]:
        rp.Failure.model_validate({"reason": ok})
    for bad in ["INVALID_SPEC", "exit_nonzero", "stopped", "Toy/x", "a/b/c"]:
        with pytest.raises(ValidationError):
            rp.Failure.model_validate({"reason": bad})
