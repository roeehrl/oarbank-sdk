"""modelserver: the reference module for service endpoints (coordinator side). params {"prompts": [str]} -> one
`generate` job; its result is the model's completions. Every verb is a pure function of its input."""
import hashlib
import json

from oarbank_sdk import module_protocol as mp
from oarbank_sdk.keys import job_key
from oarbank_sdk.server import Module

MODULE_ID, VERSION, COMPAT = "dev.codonic.oarbank.modelserver", "0.1.0", "modelserver1"
GOLDEN = ["the quick brown fox"]

module = Module(MODULE_ID, VERSION)


def complete(prompt: str) -> str:
    """What the stand-in model answers (model_service.py)."""
    return f"{prompt} -> {hashlib.sha256(prompt.encode()).hexdigest()[:16]}"


def digest(texts: list[str]) -> str:
    return hashlib.sha256(json.dumps(texts).encode()).hexdigest()


def prompts_of(params: dict) -> list[str] | None:
    p = params.get("prompts")
    return p if isinstance(p, list) and p and all(isinstance(x, str) for x in p) else None


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    prompts = prompts_of(p.params)
    if prompts is None:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/prompts", message="one or more prompts",
                                                               code="modelserver/prompts")])
    return mp.ParamsCheckResult(ok=True, normalized_params={"prompts": prompts})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"prompts": p.params["prompts"]}, stages=["generate"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[mp.StageSpec(
        stage="generate", payload={"prompts": j.key_inputs["prompts"]})]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    texts = (p.result.get("payload") or {}).get("texts")
    want = [complete(x) for x in p.spec["payload"]["prompts"]]
    if texts != want:
        return mp.ResultEvaluateResult(verdict="reject", reason="modelserver/wrong_texts")
    return mp.ResultEvaluateResult(verdict="accept", digest=digest(texts), digest_version=1, fields={"prompts": len(texts)})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    return mp.GoldenListResult(goldens=[mp.Golden(name="modelserver-golden", key_inputs={"prompts": GOLDEN},
                                                  stages=["generate"],
                                                  expected={"digest": digest([complete(x) for x in GOLDEN]),
                                                            "digest_version": 1})])


@module.verb("op.plan")
def op_plan(p: mp.OpPlanParams, ctx) -> mp.OpPlanResult:
    return mp.OpPlanResult(summary=f"queue {len(p.params.get('jobs') or [])} generate jobs")


@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx) -> mp.OpApplyResult:
    if p.verb != "queue_prompts":
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=f"unknown verb {p.verb}",
                                                                    code="modelserver/unknown_verb")])
    groups = p.params.get("jobs") or []
    if not groups or any(prompts_of({"prompts": g}) is None for g in groups):
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(path="/jobs", message="lists of prompts",
                                                                    code="modelserver/prompts")])
    hold = float(p.params.get("hold_s") or 0)
    cid = "c_model_" + hashlib.sha256(json.dumps(groups).encode()).hexdigest()[:10]
    jobs = [{"job_key": job_key(MODULE_ID, COMPAT, {"prompts": g}), "spec": {"prompts": g, "hold_s": hold}} for g in groups]
    return mp.OpApplyResult(effects=[mp.Effect(kind="campaigns.create", args={"campaign_id": cid,
                                                                              "name": p.params.get("name") or "prompts"}),
                                     mp.Effect(kind="jobs.enqueue", args={"campaign_id": cid, "jobs": jobs})],
                            response="redirect", message=f"{len(jobs)} jobs queued", result={"campaign_id": cid})


if __name__ == "__main__":
    module.run()
