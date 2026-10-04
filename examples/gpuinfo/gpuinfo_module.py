"""gpuinfo: the reference module for GPU placement (coordinator side). params {"numbers": [int]} -> one `squares` job;
its result is the sum of their squares. Every verb is a pure function of its input."""
import hashlib

from oarbank_sdk import module_protocol as mp
from oarbank_sdk.server import Module

MODULE_ID, VERSION = "dev.codonic.oarbank.gpuinfo", "0.1.0"
GOLDEN = [3, 4, 12]

module = Module(MODULE_ID, VERSION)


def digest(total: int) -> str:
    return hashlib.sha256(str(total).encode()).hexdigest()


def numbers_of(params: dict) -> list[int] | None:
    n = params.get("numbers")
    return n if isinstance(n, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in n) else None


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    numbers = numbers_of(p.params)
    if numbers is None:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/numbers", message="a list of integers",
                                                               code="gpuinfo/numbers")])
    return mp.ParamsCheckResult(ok=True, normalized_params={"numbers": numbers})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"numbers": p.params["numbers"]}, stages=["squares"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[mp.StageSpec(
        stage="squares", payload={"numbers": j.key_inputs["numbers"]})]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    total = (p.result.get("payload") or {}).get("total")
    if total != sum(n * n for n in p.spec["payload"]["numbers"]):
        return mp.ResultEvaluateResult(verdict="reject", reason="gpuinfo/wrong_total")
    return mp.ResultEvaluateResult(verdict="accept", digest=digest(total), digest_version=1, fields={"total": total})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    """One golden for every node class: the work is the same wherever it runs. (NodeClass.gpu_apis would let a module
    give a CUDA node and a Metal node different goldens.)"""
    return mp.GoldenListResult(goldens=[mp.Golden(name="gpuinfo-golden", key_inputs={"numbers": GOLDEN}, stages=["squares"],
                                                  expected={"digest": digest(sum(n * n for n in GOLDEN)), "digest_version": 1})])


if __name__ == "__main__":
    module.run()
