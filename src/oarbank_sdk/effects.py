"""Small builders for the effects and checks a module returns (spec/module-protocol.md, "Effects").

Verbs never write: they return effects, and the host applies them on its single writer. These helpers only
build the dicts, so a module's code reads like what it asks for:

    return mp.MovePostflightResult(effects=[fx.campaign_state("c_live", "running"),
                                            fx.files_write("state/config.json", json.dumps(cfg).encode())])

Campaigns and jobs, one campaign per platform (placement, spec/platforms.md "Placement"):

    effects = []
    for plat, n in ctx.host.fleet_platforms().items():
        cid = f"c_bench_{plat.replace('-', '_')}_{stamp}"
        effects += [fx.campaign_create(cid, f"bench {plat}", placement=fx.placement("same-platform", pin=plat)),
                    fx.jobs_enqueue(cid, [fx.job(job_key(ID, COMPAT, k), {"n": k["n"]}, labels=k) for k in keys])]
"""
import base64
import hashlib
import re
from typing import Any

from . import module_protocol as mp
from . import platform as pf
from . import portable
from .keys import canonical_json

FILE_WRITE_MAX = 1 << 20
CAMPAIGN_ID = re.compile(r"^[a-z][a-z0-9_]{3,40}$")
STAGE = re.compile(r"[a-z][a-z0-9_]{0,63}")
GROUP_MAX = 64
ENQUEUE_MAX = 5000


def effect(kind: str, **args: Any) -> mp.Effect:
    return mp.Effect(kind=kind, args=args)


def store_write(collection: str, key: str, doc: dict) -> mp.Effect:
    return effect("store.write", collection=collection, key=key, doc=doc)


def store_delete(collection: str, key: str) -> mp.Effect:
    return effect("store.delete", collection=collection, key=key)


def files_write(path: str, data: bytes) -> mp.Effect:
    """A small file (at most 1 MiB), stored by the host. Larger files arrive as job artifacts: bind them with files_put."""
    if len(data) > FILE_WRITE_MAX:
        raise ValueError(f"{path}: {len(data)} bytes > {FILE_WRITE_MAX}; upload it as a job artifact and use files_put")
    return effect("files.write", path=path, content_b64=base64.b64encode(data).decode())


def files_put(path: str, digest: str) -> mp.Effect:
    """Name a blob the coordinator already holds (a job artifact, a dataset file) as one of the module's files."""
    return effect("files.put", path=path, digest=digest)


def files_delete(path: str | None = None, prefix: str | None = None) -> mp.Effect:
    if (path is None) == (prefix is None):
        raise ValueError("files_delete takes exactly one of path or prefix")
    return effect("files.delete", **({"path": path} if path is not None else {"prefix": prefix}))


def campaign_state(campaign_id: str, state: str, message: str = "") -> mp.Effect:
    return effect("campaigns.update", campaign_id=campaign_id, state=state, **({"message": message} if message else {}))


def placement(mix: str, unit: str | None = None, pin: str | None = None, bind: str | None = None) -> dict:
    """A campaign's placement (campaigns.create `placement`, host capability placement.v1). It may be stricter than the
    manifest's [placement], never looser (the host refuses with 422 placement_looser_than_manifest). `pin` binds the
    campaign to one class now: a token for same-platform (`linux-amd64`), an OS for same-os, an arch for same-arch."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", mix or ""):            # an open set: unknown mixes apply as same-platform
        raise ValueError(f"placement mix {mix!r}: one of {', '.join(pf.MIXES)}")
    if pin is not None:
        m = pf.normalize(mix)
        ok = portable.is_platform_token(pin) if m == pf.STRICTEST else bool(re.fullmatch(r"[a-z][a-z0-9_]*", pin)) and m != "any"
        if not ok:
            raise ValueError(f"pin {pin!r} is not a class of mix {mix!r} (a token for same-platform, an OS for same-os, "
                             "an arch for same-arch; nothing for any)")
    return {"mix": mix, **{k: v for k, v in (("unit", unit), ("bind", bind), ("pin", pin)) if v is not None}}


def campaign_create(campaign_id: str, name: str, priority: int = 0, weight: float = 1, labels: dict | None = None,
                    placement: dict | None = None) -> mp.Effect:
    """A running campaign owned by the module. `placement` (from `placement()`) needs the placement.v1 host capability."""
    if not CAMPAIGN_ID.fullmatch(campaign_id):
        raise ValueError(f"campaign id {campaign_id!r}: a lowercase letter, then 3-40 of [a-z0-9_]")
    args: dict[str, Any] = {"campaign_id": campaign_id, "name": name, "priority": priority, "weight": weight}
    if labels is not None:
        args["labels"] = labels
    if placement is not None:
        args["placement"] = placement
    return effect("campaigns.create", **args)


def job(job_key: str, spec: dict, *, stage: str | None = None, group: str | None = None, platforms: list[str] | None = None,
        target_node: str | None = None, labels: dict | None = None, dataset_id: str | None = None,
        datasets: list[str] | None = None, mounts: dict | None = None, resources: dict | None = None,
        timeout_s: float | None = None, priority: int | None = None, subpriority: int | None = None,
        spec_version: int | None = None, name: str | None = None) -> dict:
    """One jobs.enqueue item; unset fields are left out (the host's defaults apply). `stage` runs exactly that standalone
    stage, never the chain (host capability jobs.stage; key it with keys.job_key(..., stage)). `group` (at most 64
    characters) and `platforms` (tokens or OSes) need the placement.v1 host capability."""
    if stage is not None and not STAGE.fullmatch(stage):
        raise ValueError(f"stage {stage!r}: a stage name ([a-z][a-z0-9_]*)")
    if group is not None and not (isinstance(group, str) and 0 < len(group) <= GROUP_MAX):
        raise ValueError(f"group {group!r}: 1-{GROUP_MAX} characters")
    bad = [p for p in platforms or [] if not pf.is_key(p)]
    if bad:
        raise ValueError(f"platforms {bad}: platform tokens or OS names")
    item: dict[str, Any] = {"job_key": job_key, "spec": spec}
    opt = {"stage": stage, "group": group, "platforms": list(platforms) if platforms else None, "target_node": target_node, "labels": labels,
           "dataset_id": dataset_id, "datasets": datasets, "mounts": mounts, "resources": resources, "timeout_s": timeout_s,
           "priority": priority, "subpriority": subpriority, "spec_version": spec_version, "name": name}
    item.update({k: v for k, v in opt.items() if v is not None})
    return item


def jobs_enqueue(campaign_id: str, jobs: list[dict]) -> mp.Effect:
    """Adds jobs (plain dicts or `job()` items, at most 5000) to one of the module's campaigns."""
    if len(jobs) > ENQUEUE_MAX:
        raise ValueError(f"{len(jobs)} jobs > {ENQUEUE_MAX} in one effect; split them")
    return effect("jobs.enqueue", campaign_id=campaign_id, jobs=list(jobs))


def datasets_create(dataset_id: str, kind: str, files: list[dict], meta: dict | None = None,
                    platform: str | None = None) -> mp.Effect:
    """Registers a dataset whose blobs the coordinator holds. `platform` (a token) binds it to one platform; kinds in
    [datasets].platform_bound must give it (host capability placement.v1)."""
    if platform is not None and not portable.is_platform_token(platform):
        raise ValueError(f"dataset platform {platform!r}: a platform token")
    args: dict[str, Any] = {"dataset_id": dataset_id, "kind": kind, "meta": meta or {}, "files": files}
    if platform is not None:
        args["platform"] = platform
    return mp.Effect(kind="datasets.create", args=args)            # `kind` is also an argument name here


def check(name: str, ok: bool, detail: str = "", severity: str = "error") -> mp.CheckItem:
    return mp.CheckItem(name=name, ok=bool(ok), detail=detail[:1000], severity=severity)


def integrity(checks: list[mp.CheckItem], fingerprint: str | None = None) -> mp.IntegrityCheckResult:
    """An integrity result: ok unless an `error` check failed."""
    return mp.IntegrityCheckResult(ok=all(c.ok or c.severity != "error" for c in checks), checks=checks, fingerprint=fingerprint)


def fingerprint(*parts: Any) -> str:
    """sha256 over the canonical JSON of the parts: give it the state you own (sorted file digests, store documents)."""
    return hashlib.sha256(canonical_json(list(parts)).encode()).hexdigest()
