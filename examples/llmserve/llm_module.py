"""llmserve: the coordinator side. `queue_prompts` enqueues `batch` jobs of `per_job` prompts each; `selftest` (the
default stage, the one goldens test) runs the deterministic stand-in model, so the coordinator checks a selftest result
by computing the same text. Every verb is a pure function of its input."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # -I leaves the bundle off sys.path

import llm_backends as B  # noqa: E402
from oarbank_sdk import effects as fx  # noqa: E402
from oarbank_sdk import module_protocol as mp  # noqa: E402
from oarbank_sdk.keys import job_key  # noqa: E402
from oarbank_sdk.server import Module  # noqa: E402

MODULE_ID, VERSION, COMPAT = "dev.codonic.oarbank.llmserve", "0.1.0", "llmserve1"
GOLDEN = {"prompts": ["the agent measures", "a duty is"], "max_tokens": 24}
module = Module(MODULE_ID, VERSION)


class _Free:
    """A duty governor and control that never hold (the coordinator computes the stand-in's text, it governs nothing)."""

    def unit(self):
        import contextlib
        return contextlib.nullcontext()

    def safe_point(self):
        pass


def stub_completions(prompts: list[str], max_tokens: int, work: int = 64) -> list[str]:
    m = B.Stub(work)
    free = _Free()
    return ["".join(m.generate(p, max_tokens, free, free)) for p in prompts]


def digest(completions: list[str]) -> str:
    return hashlib.sha256(json.dumps(completions, ensure_ascii=False).encode()).hexdigest()


def prompts_of(params: dict) -> list[str] | None:
    p = params.get("prompts")
    return p if isinstance(p, list) and p and all(isinstance(x, str) for x in p) else None


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    prompts = prompts_of(p.params)
    if prompts is None:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/prompts", message="one or more prompts",
                                                               code="llmserve/prompts")])
    return mp.ParamsCheckResult(ok=True, normalized_params={**p.params, "prompts": prompts})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    prompts, per = p.params["prompts"], int(p.params.get("per_job") or 16)
    rest = {k: p.params[k] for k in ("max_tokens", "backend", "model") if p.params.get(k) is not None}
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"prompts": prompts[i:i + per], **rest}, stages=["batch"])
                                  for i in range(0, len(prompts), per)])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[
        mp.StageSpec(stage=(j.stages or ["selftest"])[0], payload=dict(j.key_inputs))]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    pay = p.result.get("payload") or {}
    comps = pay.get("completions")
    spec = p.spec.get("payload") or {}
    if not isinstance(comps, list) or not all(isinstance(c, str) for c in comps) or len(comps) != len(spec.get("prompts") or []):
        return mp.ResultEvaluateResult(verdict="reject", reason="llmserve/bad_completions")
    if (p.stage or p.spec.get("stage") or "selftest") == "selftest":
        want = stub_completions(spec["prompts"], int(spec.get("max_tokens") or 32), int(spec.get("work") or 64))
        if comps != want:
            return mp.ResultEvaluateResult(verdict="reject", reason="llmserve/wrong_text")
    return mp.ResultEvaluateResult(verdict="accept", digest=digest(comps), digest_version=1,
                                   fields={"tokens": int(pay.get("tokens") or 0), "backend": str(pay.get("backend") or "")})


@module.verb("result.merge")
def result_merge(p: mp.ResultMergeParams, ctx) -> mp.ResultMergeResult:
    """Each job runs one standalone stage, so its result is that stage's."""
    return mp.ResultMergeResult(result=next(iter(p.stages.values())))


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    want = stub_completions(GOLDEN["prompts"], GOLDEN["max_tokens"])
    return mp.GoldenListResult(goldens=[mp.Golden(name="llmserve-selftest", key_inputs=GOLDEN, stages=["selftest"],
                                                  expected={"digest": digest(want), "digest_version": 1})])


@module.verb("op.plan")
def op_plan(p: mp.OpPlanParams, ctx) -> mp.OpPlanResult:
    n, per = len(p.params.get("prompts") or []), int(p.params.get("per_job") or 16)
    return mp.OpPlanResult(summary=f"queue {n} prompts in {-(-n // per)} batch jobs ({p.params.get('backend') or 'auto'})")


@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx) -> mp.OpApplyResult:
    if p.verb != "queue_prompts":
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=f"unknown verb {p.verb}",
                                                                    code="llmserve/unknown_verb")])
    prompts = prompts_of(p.params)
    if prompts is None:
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(path="/prompts", message="one or more prompts",
                                                                    code="llmserve/prompts")])
    plan = job_plan(mp.JobPlanParams(params=p.params), ctx)
    cid = "c_llm_" + hashlib.sha256(json.dumps(p.params, sort_keys=True).encode()).hexdigest()[:10]
    jobs = [fx.job(job_key(MODULE_ID, COMPAT, j.key_inputs, "batch"), dict(j.key_inputs), stage="batch") for j in plan.jobs]
    return mp.OpApplyResult(effects=[fx.campaign_create(cid, p.params.get("name") or "prompts"), fx.jobs_enqueue(cid, jobs)],
                            response="redirect", message=f"{len(jobs)} batch jobs queued", result={"campaign_id": cid})


if __name__ == "__main__":
    module.run()
