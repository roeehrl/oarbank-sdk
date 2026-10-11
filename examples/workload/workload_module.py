"""workload: the coordinator side. The `tally` stage's verbs (params {"amounts": [int]} -> {"total": int}) and
`workload.report`, which keeps one record per report the `worker` workload sends.

Records: collection `units`, one document per report, keyed by where it came from (node, workload) and the report's
seq, so a delivery the host repeats writes the same document again. Amounts stay integers in the smallest unit; a
correction would be a new record that reverses the old one, never an edit.
"""
import hashlib

from oarbank_sdk import effects as fx
from oarbank_sdk import module_protocol as mp
from oarbank_sdk.server import Module

MODULE_ID, VERSION = "dev.codonic.oarbank.workload", "0.1.0"
GOLDEN = [1200, 800, 450]

module = Module(MODULE_ID, VERSION)


def digest(total: int) -> str:
    return hashlib.sha256(str(total).encode()).hexdigest()


def amounts_problem(amounts) -> str | None:
    if not isinstance(amounts, list) or not amounts:
        return "amounts is a non-empty list"
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in amounts):
        return "every amount is a non-negative integer (the smallest unit)"
    return None


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    why = amounts_problem(p.params.get("amounts"))
    if why:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/amounts", message=why, code="workload/amounts")])
    return mp.ParamsCheckResult(ok=True, normalized_params={"amounts": list(p.params["amounts"])})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"amounts": p.params["amounts"]}, stages=["tally"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[
        mp.StageSpec(stage="tally", payload={"amounts": j.key_inputs["amounts"]})]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    want = sum(p.spec["payload"]["amounts"])
    try:
        total = int(p.result.get("payload", {}).get("total"))
    except (TypeError, ValueError):
        return mp.ResultEvaluateResult(verdict="reject", reason="workload/no_total")
    if total != want:
        return mp.ResultEvaluateResult(verdict="reject", reason="workload/wrong_total", digest=digest(total), digest_version=1)
    return mp.ResultEvaluateResult(verdict="accept", value=float(total), digest=digest(total), digest_version=1,
                                   summary={"total": total}, fields={"total": total})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    return mp.GoldenListResult(goldens=[mp.Golden(name="workload-golden", key_inputs={"amounts": GOLDEN}, stages=["tally"],
                                                  expected={"digest": digest(sum(GOLDEN)), "digest_version": 1})])


@module.verb("workload.report")
def workload_report(p: mp.WorkloadReportParams, ctx) -> mp.WorkloadReportResult:
    """One store document per report: keyed by node, workload and seq (a repeated delivery rewrites the same document),
    holding the daemon's own reference for the unit and its integer amount."""
    effects, skipped = [], 0
    for e in p.events:
        ref, amount = e.data.get("unit"), e.data.get("amount")
        if not isinstance(ref, str) or not isinstance(amount, int) or isinstance(amount, bool):
            skipped += 1                             # not a unit report: nothing to keep
            continue
        effects.append(fx.store_write("units", f"{p.node}.{p.workload}.{e.seq}", {
            "node": p.node, "workload": p.workload, "seq": e.seq, "unit": ref, "amount": amount, "at": e.t}))
    return mp.WorkloadReportResult(effects=effects, message=f"{len(effects)} unit(s) recorded" +
                                   (f", {skipped} report(s) skipped" if skipped else ""))


if __name__ == "__main__":
    module.run()
