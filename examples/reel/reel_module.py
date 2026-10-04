"""reel: the reference module for files and media. params {"frames", "seed", "step_ms"?, "every"?, "asset"?} -> a render
of `frames` deterministic frames (PNG artifacts with thumbnails), a sample clip and a text log; with `asset` (an asset
dataset, registered by URL or adopted from an upload) the render mounts it and lists its files' digests.

The coordinator side. Every verb is a pure function of its input. result.evaluate recomputes the expected digest from
the frames' pixels (reel_frames), so a result is checked, not trusted.
"""
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reel_frames as F  # noqa: E402
from oarbank_sdk import effects as fx  # noqa: E402
from oarbank_sdk import module_protocol as mp  # noqa: E402
from oarbank_sdk.goldens import load as load_goldens  # noqa: E402
from oarbank_sdk.keys import job_key  # noqa: E402
from oarbank_sdk.server import Module  # noqa: E402

MODULE_ID, VERSION, COMPAT = "dev.codonic.oarbank.reel", "0.1.0", "reel1"
ROOT = Path(__file__).parent
MAX_FRAMES, MAX_STEP_MS, GALLERY = 240, 5000, 48
ASSET = re.compile(r"^asset:[A-Za-z0-9_.+-]{1,120}$")

module = Module(MODULE_ID, VERSION)


def _problems(p: dict) -> list[mp.Issue]:
    out = []
    frames, seed, step, every = p.get("frames"), p.get("seed", 0), p.get("step_ms", 0), p.get("every", 4)
    if not isinstance(frames, int) or isinstance(frames, bool) or not 1 <= frames <= MAX_FRAMES:
        out.append(mp.Issue(path="/frames", message=f"frames: an integer in 1..{MAX_FRAMES}", code="reel/frames"))
    if not isinstance(seed, int) or not 0 <= seed < 2**31:
        out.append(mp.Issue(path="/seed", message="seed: an integer in 0..2^31-1", code="reel/seed"))
    if not isinstance(step, int) or not 0 <= step <= MAX_STEP_MS:
        out.append(mp.Issue(path="/step_ms", message=f"step_ms: an integer in 0..{MAX_STEP_MS}", code="reel/step"))
    if not isinstance(every, int) or not 1 <= every <= MAX_FRAMES:
        out.append(mp.Issue(path="/every", message="every: frames between checkpoints, at least 1", code="reel/every"))
    if "asset" in p and not (isinstance(p["asset"], str) and ASSET.fullmatch(p["asset"])):
        out.append(mp.Issue(path="/asset", message="asset: an asset dataset id (asset:<name>)", code="reel/asset"))
    return out


def _norm(p: dict) -> dict:
    n = {"frames": p["frames"], "seed": p.get("seed", 0), "step_ms": p.get("step_ms", 0), "every": p.get("every", 4)}
    return {**n, "asset": p["asset"]} if p.get("asset") else n


def _mounts(k: dict) -> dict:
    """A render's dataset and mount: the asset, mounted as asset/."""
    return {"datasets": [k["asset"]], "mounts": {k["asset"]: "asset"}} if k.get("asset") else {}


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
    errs = _problems(p.params)
    return mp.ParamsCheckResult(ok=not errs, errors=errs, normalized_params=None if errs else _norm(p.params))


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx) -> mp.JobPlanResult:
    k = _norm(p.params)
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs=k, stages=["render"], label=f"{k['frames']} frames, seed {k['seed']}",
                                              datasets=_mounts(k).get("datasets", []))])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1,
                                                  stages=[mp.StageSpec(stage="render", payload=_norm(j.key_inputs),
                                                                       **_mounts(j.key_inputs))])
                                     for j in p.jobs])


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx) -> mp.ResultEvaluateResult:
    spec = p.spec["payload"]
    pay = p.result.get("payload") or {}
    if pay.get("frames") != spec["frames"] or not isinstance(pay.get("digest"), str):
        return mp.ResultEvaluateResult(verdict="reject", reason="reel/malformed")
    names = {a.get("name"): len(a.get("files") or []) for a in p.result.get("artifacts") or []}
    if names.get("frames") != spec["frames"]:
        return mp.ResultEvaluateResult(verdict="reject", reason="reel/missing_frames")
    want = F.expected(spec.get("seed", 0), spec["frames"])
    if pay["digest"] != want:
        return mp.ResultEvaluateResult(verdict="reject", reason="reel/wrong_frames", digest=pay["digest"], digest_version=1)
    return mp.ResultEvaluateResult(verdict="accept", value=float(spec["frames"]), digest=want, digest_version=1,
                                   summary={"frames": spec["frames"]}, fields={"frames": spec["frames"], "digest": want})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
    return mp.GoldenListResult(goldens=load_goldens(ROOT, "goldens/*.json", p.node_class))


# ---------------------------------------------------------------- UI (contract 1.1: media, gallery, compare)

def _ref(job_id: int, artifact: str, path: str) -> dict:
    return {"job": job_id, "artifact": artifact, "path": path}


@module.verb("ui.view.compute")
def view_compute(p: mp.ViewComputeParams, ctx) -> mp.ViewComputeResult:
    renders = sorted((r for r in p.inputs.get("results", []) if (r.get("fields") or {}).get("frames")),
                     key=lambda r: -int(r["job_id"]))
    latest = renders[0] if renders else None
    if p.view_id == "frames":
        rows = [] if not latest else [{"frame": _ref(latest["job_id"], "frames", F.frame_name(i)), "index": i}
                                      for i in range(min(GALLERY, int(latest["fields"]["frames"])))]
    elif p.view_id == "renders":
        rows = [{"job": r["job_id"], "frames": r["fields"]["frames"], "clip": _ref(r["job_id"], "clip", "clip.webm"),
                 "log": _ref(r["job_id"], "log", "render.txt")} for r in renders[:20]]
    elif p.view_id == "pair":
        n = int(latest["fields"]["frames"]) if latest else 0
        rows = [] if not latest else [{"first": _ref(latest["job_id"], "frames", F.frame_name(0)),
                                       "last": _ref(latest["job_id"], "frames", F.frame_name(n - 1))}]
    else:
        raise ValueError(f"unknown view {p.view_id}")
    return mp.ViewComputeResult(rows=rows, data_version=p.data_version)


# ---------------------------------------------------------------- operations

@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx) -> mp.OpApplyResult:
    if p.verb == "queue_render":
        renders = p.params.get("renders") or []
        errs = [e for r in renders for e in _problems(r)] if renders else [mp.Issue(path="/renders", message="at least one render",
                                                                                 code="reel/renders")]
        if errs:
            return mp.OpApplyResult(response="errors", errors=errs)
        cid = p.params.get("campaign_id") or "c_reel_" + hashlib.sha256(repr(renders).encode()).hexdigest()[:10]
        jobs = [fx.job(job_key(MODULE_ID, COMPAT, _norm(r)), _norm(r), labels={"frames": r["frames"], "seed": r.get("seed", 0)},
                       **_mounts(_norm(r))) for r in renders]
        return mp.OpApplyResult(effects=[fx.campaign_create(cid, p.params.get("name") or "renders"), fx.jobs_enqueue(cid, jobs)],
                                response="redirect", message=f"{len(jobs)} renders queued", result={"campaign_id": cid})
    if p.verb == "import_asset":
        a = p.params
        try:
            effect = fx.datasets_create(a.get("dataset_id") or "", "asset", [{"path": a.get("name") or "asset.bin",
                                        "digest": a.get("sha256"), "size": a.get("size"), "origins": [a.get("url")]}],
                                        meta={"name": a.get("name") or "asset.bin"})
        except ValueError as e:
            return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=str(e), code="reel/bad_asset")])
        return mp.OpApplyResult(effects=[effect], response="refresh", message=f"asset {a.get('dataset_id')} registered from its origin")
    if p.verb == "adopt_upload":
        # an importer: the files of an uploaded (or origin) dataset become one of reel's asset datasets, naming the same
        # blobs; reel reads only their names, digests and sizes, never their bytes
        found = ctx.host.datasets_query(ids=[p.target or ""], with_files=True).datasets
        if not found or not found[0].files:
            return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=f"no dataset {p.target} with files",
                                                                         code="reel/no_upload")])
        src = found[0]
        did = p.params.get("dataset_id") or "asset:" + src.id.split(":", 1)[-1][:120]
        files = [{k: f[k] for k in ("path", "digest", "size", "origins") if f.get(k) is not None} for f in src.files]
        return mp.OpApplyResult(effects=[fx.datasets_create(did, "asset", files, meta={"from": src.id})], response="refresh",
                                message=f"{len(files)} files of {src.id} are now asset {did}", result={"dataset_id": did})
    return mp.OpApplyResult(response="errors", errors=[mp.Issue(message=f"unknown verb {p.verb}", code="reel/unknown_verb")])


if __name__ == "__main__":
    module.run()
