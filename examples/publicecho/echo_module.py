"""publicecho: the reference module for inbound listeners (coordinator side). params {"lines": [str]} -> one `echo` job;
its result is the answers the echo service gives those lines before any address is announced. Every verb is a pure
function of its input."""
import hashlib
import json

from oarbank_sdk import module_protocol as mp
from oarbank_sdk.server import Module

MODULE_ID, VERSION = "dev.codonic.oarbank.publicecho", "0.1.0"
GOLDEN = ["hello", "world"]

module = Module(MODULE_ID, VERSION)


def answer(line: str, external: str | None = None) -> str:
    """What the echo service answers a line (echo_service.py)."""
    return f"echo {line} {external or '-'}"


def digest(answers: list[str]) -> str:
    return hashlib.sha256(json.dumps(answers).encode()).hexdigest()


def lines_of(params: dict) -> list[str] | None:
    p = params.get("lines")
    ok = isinstance(p, list) and p and all(isinstance(x, str) and "\n" not in x and len(x) <= 1000 for x in p)
    return p if ok else None


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    lines = lines_of(p.params)
    if lines is None:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/lines", message="one or more single lines",
                                                               code="publicecho/lines")])
    return mp.ParamsCheckResult(ok=True, normalized_params={"lines": lines})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"lines": p.params["lines"]}, stages=["echo"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[mp.StageSpec(
        stage="echo", payload={"lines": j.key_inputs["lines"]})]) for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    got = (p.result.get("payload") or {}).get("answers")
    want = [answer(x) for x in p.spec["payload"]["lines"]]
    if got != want:
        return mp.ResultEvaluateResult(verdict="reject", reason="publicecho/wrong_answers")
    return mp.ResultEvaluateResult(verdict="accept", digest=digest(got), digest_version=1, fields={"lines": len(got)})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    return mp.GoldenListResult(goldens=[mp.Golden(name="publicecho-golden", key_inputs={"lines": GOLDEN}, stages=["echo"],
                                                  expected={"digest": digest([answer(x) for x in GOLDEN]),
                                                            "digest_version": 1})])


if __name__ == "__main__":
    module.run()
