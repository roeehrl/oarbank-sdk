"""taskbench: the coordinator side. run_tasks enqueues one attempt job per task image, each listing its image (a job
runs a set's images only if it lists them); score jobs check a task's log, and the golden scores a fixed one."""
import hashlib

from oarbank_sdk import effects as fx
from oarbank_sdk import module_protocol as mp
from oarbank_sdk.keys import job_key
from oarbank_sdk.server import Module

MODULE_ID, VERSION, COMPAT = "dev.codonic.oarbank.taskbench", "0.1.0", "taskbench1"
GOLDEN_LOG = "test_a PASSED\ntest_b PASSED\ntest_c FAILED\n"
module = Module(MODULE_ID, VERSION)


def passed(log: str) -> int:
    return sum(1 for line in log.splitlines() if line.endswith(" PASSED"))


def digest(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx):
    return mp.ParamsCheckResult(ok=isinstance(p.params.get("log"), str), normalized_params=p.params)


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx):
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"log": p.params["log"]}, stages=["score"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx):
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[
        mp.StageSpec(stage="score", payload={"log": j.key_inputs["log"]})]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx):
    n = (p.result.get("payload") or {}).get("passed")
    if not isinstance(n, int):
        return mp.ResultEvaluateResult(verdict="reject", reason="taskbench/no_count")
    return mp.ResultEvaluateResult(verdict="accept", digest=digest(n), digest_version=1, fields={"passed": n})


@module.verb("result.merge")
def result_merge(p: mp.ResultMergeParams, ctx):
    return mp.ResultMergeResult(result=next(iter(p.stages.values())))


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx):
    return mp.GoldenListResult(goldens=[mp.Golden(name="taskbench-golden", key_inputs={"log": GOLDEN_LOG}, stages=["score"],
                                                  expected={"digest": digest(passed(GOLDEN_LOG)), "digest_version": 1})])


@module.verb("op.plan")
def op_plan(p: mp.OpPlanParams, ctx):
    return mp.OpPlanResult(summary=f"run {len(p.params.get('images') or [])} task images")


@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx):
    images = list(p.params.get("images") or [])
    cid = "c_" + hashlib.sha256("\n".join(images).encode()).hexdigest()[:12]
    jobs = [fx.job(job_key(MODULE_ID, COMPAT, {"image": img}, "attempt"), {"image": img}, stage="attempt", images=[img])
            for img in images]
    return mp.OpApplyResult(effects=[fx.campaign_create(cid, f"{len(images)} tasks"), fx.jobs_enqueue(cid, jobs)],
                            result={"campaign_id": cid})


if __name__ == "__main__":
    module.run()
