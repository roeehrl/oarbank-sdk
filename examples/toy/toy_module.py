"""toy: the reference module. params {"n": int} -> result {"sum": sum(range(n))}.

The coordinator side, in full. Every verb is a pure function of its input.
"""
import hashlib

from oarbank_sdk import effects as fx
from oarbank_sdk import module_protocol as mp
from oarbank_sdk.keys import job_key
from oarbank_sdk.server import Module

MODULE_ID, VERSION, COMPAT = "dev.codonic.oarbank.toy", "0.1.0", "toy1"
MAX_N = 10**9
GOLDEN_N = 1000

module = Module(MODULE_ID, VERSION)


def expected_sum(n: int) -> int:
    return n * (n - 1) // 2


def digest(total: int) -> str:
    return hashlib.sha256(str(total).encode()).hexdigest()


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    n = p.params.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/n", message="n must be an integer", code="toy/n_type")])
    if not 0 <= n <= MAX_N:
        return mp.ParamsCheckResult(ok=False, errors=[mp.Issue(path="/n", message=f"n must be in 0..{MAX_N}", code="toy/n_range")])
    return mp.ParamsCheckResult(ok=True, normalized_params={"n": n})


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"n": p.params["n"]}, stages=["run"], label=f"n={p.params['n']}")])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1,
                                                  stages=[mp.StageSpec(stage="run", payload={"n": j.key_inputs["n"]})])
                                     for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    n = p.spec["payload"]["n"]
    raw = p.result.get("payload", {}).get("sum")
    try:
        total = int(raw)
    except (TypeError, ValueError):
        return mp.ResultEvaluateResult(verdict="reject", reason="toy/no_sum")
    if total != expected_sum(n):
        return mp.ResultEvaluateResult(verdict="reject", reason="toy/wrong_sum", digest=digest(total), digest_version=1)
    return mp.ResultEvaluateResult(verdict="accept", value=float(total), digest=digest(total), digest_version=1,
                                   summary={"sum": total}, fields={"sum": total})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    return mp.GoldenListResult(goldens=[mp.Golden(name="toy-golden", key_inputs={"n": GOLDEN_N}, stages=["run"],
                                                  expected={"digest": digest(expected_sum(GOLDEN_N)), "digest_version": 1})])


# ---------------------------------------------------------------- UI contract 1 (spec/ui-contract.md)

@module.verb("ui.view.compute")
def view_compute(p: mp.ViewComputeParams, ctx) -> mp.ViewComputeResult:
    if p.view_id != "sums":
        raise ValueError(f"unknown view {p.view_id}")
    rows = []
    for r in p.inputs.get("results", []):
        n = (r.get("key_inputs") or {}).get("n")
        total = r.get("value")
        if n is None or total is None:
            continue
        rows.append({"n": int(n), "sum": int(total), "ok": int(total) == expected_sum(int(n))})
    rows.sort(key=lambda x: -x["n"])
    return mp.ViewComputeResult(rows=rows[:1000], data_version=p.data_version)


@module.verb("op.plan")
def op_plan(p: mp.OpPlanParams, ctx) -> mp.OpPlanResult:
    if p.verb == "queue_sums":
        return mp.OpPlanResult(summary=f"queue {len(p.params.get('ns') or [])} sums")
    n = int(p.params["n"])
    return mp.OpPlanResult(summary=f"favorite n becomes {n}",
                           effects=[mp.Effect(kind="module_settings.update", args={"favorite_n": n})],
                           diff=[{"path": "/favorite_n", "before": ctx.settings.get("favorite_n"), "after": n}])


@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx) -> mp.OpApplyResult:
    if p.verb == "queue_sums":
        ns = [int(n) for n in p.params.get("ns") or []]
        if not ns or any(not 0 <= n <= MAX_N for n in ns):
            return mp.OpApplyResult(response="errors", errors=[mp.Issue(path="/ns", message=f"1+ integers in 0..{MAX_N}", code="toy/n_range")])
        cid = p.params.get("campaign_id") or "c_toy_" + hashlib.sha256(repr(ns).encode()).hexdigest()[:10]
        jobs = [{"job_key": job_key(MODULE_ID, COMPAT, {"n": n}), "spec": {"n": n}, "labels": {"n": n},
                 "resources": {"cpu": 1, "mem_gb": 0.1}} for n in ns]
        return mp.OpApplyResult(effects=[mp.Effect(kind="campaigns.create", args={"campaign_id": cid, "name": p.params.get("name") or "sums"}),
                                         mp.Effect(kind="jobs.enqueue", args={"campaign_id": cid, "jobs": jobs})],
                                response="redirect", message=f"{len(jobs)} sums queued", result={"campaign_id": cid, "jobs": len(jobs)})
    if p.verb == "save_note":
        name, text = str(p.params.get("name") or ""), str(p.params.get("text") or "")
        if not name.isidentifier():
            return mp.OpApplyResult(response="errors", errors=[mp.Issue(path="/name", message="a plain name", code="toy/note_name")])
        area = "cache" if p.params.get("cache") else "notes"
        return mp.OpApplyResult(effects=[fx.files_write(f"{area}/{name}.txt", text.encode())], response="refresh",
                                message=f"saved {area}/{name}.txt")
    if p.verb != "set_favorite":
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=f"unknown verb {p.verb}", code="toy/unknown_verb")])
    n = int(p.params["n"])
    if not 0 <= n <= MAX_N:
        return mp.OpApplyResult(response="errors", errors=[mp.Issue(path="/n", message=f"n must be in 0..{MAX_N}", code="toy/n_range")])
    return mp.OpApplyResult(effects=[mp.Effect(kind="module_settings.update", args={"favorite_n": n})],
                            response="refresh", message=f"favorite n is now {n:,}")


# ---------------------------------------------------------------- integrity and coordinator moves
# Notes live in the module's files (`notes/` is carried, `cache/` is rebuilt after a move); the `toy_moves` store
# collection records what each move did, and a `toy_block/now` document blocks a move (tests use it).

def _owned_files(ctx) -> list[mp.FileEntry]:
    return [f for f in ctx.host.files_list() if not f.path.startswith("cache/")]


@module.verb("integrity.check")
def integrity_check(p: mp.IntegrityCheckParams, ctx) -> mp.IntegrityCheckResult:
    files = _owned_files(ctx)
    checks = [fx.check("notes_are_text", all(f.size < 1 << 20 for f in files), f"{len(files)} files")]
    if p.deep:
        bad = [f.path for f in files if hashlib.sha256(ctx.host.files_read(f.path)).hexdigest() != f.digest]
        checks.append(fx.check("notes_match_digests", not bad, ", ".join(bad[:5])))
    return fx.integrity(checks, fingerprint=fx.fingerprint(sorted((f.path, f.digest) for f in files)))


@module.verb("move.preflight")
def move_preflight(p: mp.MovePreflightParams, ctx) -> mp.MovePreflightResult:
    block = ctx.host.store_get("toy_block", "now")
    return mp.MovePreflightResult(blockers=[mp.MoveBlocker(code="toy/blocked", message=str(block.get("reason") or "blocked"))]
                                  if block else [])


@module.verb("move.postflight")
def move_postflight(p: mp.MovePostflightParams, ctx) -> mp.MovePostflightResult:
    rebuilt = [s.selector for s in p.skipped if s.class_ == "rebuild"]
    effects = [fx.store_write("toy_moves", p.move_id, {"postflight": True, "from": p.from_url, "epoch": p.epoch, "rebuilt": rebuilt})]
    if "cache/" in rebuilt:
        effects.append(fx.files_write("cache/rebuilt.txt", f"rebuilt after {p.move_id}".encode()))
    return mp.MovePostflightResult(effects=effects, checks=[fx.check("notes_present", True)])


@module.verb("move.cancelled")
def move_cancelled(p: mp.MoveCancelledParams, ctx) -> mp.MoveCancelledResult:
    return mp.MoveCancelledResult(effects=[fx.store_write("toy_moves", p.move_id, {"cancelled": p.reason})])


if __name__ == "__main__":
    module.run()
