"""The conformance kit: what a module must pass before a host installs it (spec/conformance.md).

    oarbank-sdk conform <module-dir> [--fixtures conformance.json] [--no-runner] [--json]

Suites:

- **manifest**: strict schema, UI contract references, no unknown fields, the lint warnings, and a resolved view per
  declared platform (runner exec, runtime, env and file subset) and per coordinator platform (coordinator exec);
- **bundle**: the module builds into a bundle (no core imports, no symlinks) that verifies;
- **protocol**: the coordinator starts and completes `initialize`; it advertises every capability the
  manifest declares and every required verb; verbs are pure (the same input twice gives the same output);
  `golden.list` works for each node class (by default one per declared platform, with `platform` set), its goldens'
  per-platform keys are declared platforms, every golden's stage compares (never determinism `none`), and `spec.build`
  turns every golden into the stage it names;
  `integrity.check` and the move verbs (when advertised) are pure, answer valid results and ask only for the
  effects `coordinator.move.effects` declares; each fixture operation (`ops`) is pure through `op.apply`, asks only for
  its declared effects, and names every `datasets.create` file well (digest and size always; origins https to public
  names);
- **runner**: for the first golden whose datasets are available, the real runner runs the golden's
  SpecEnvelope in a fresh workdir with a clean environment, **under the module sandbox** with the grants its
  manifest declares (spec/sandbox.md), and writes a valid ResultEnvelope, which
  `result.evaluate` accepts and the golden check passes; a second run with another locale gives the same
  digest (the golden's stage has determinism `exact`); for a `cancellable` runner, a control.json stop request sent
  (and nudged as the agent nudges) once the runner wrote its first `phase` is acknowledged within `STOP_REACTION_S`
  (failure.json with fault transient, timed to the runner's own acknowledgement, not to process teardown) and the runner
  exits within `stop_grace_s`;
  `doctor --json` is a valid DoctorOutput whose `attrs.platform`, when present, is OARBANK_PLATFORM. The runner runs
  with this host's runner variant (exec and env), the stage's variant (timeout, resources) and the golden's expected
  value resolved for this host's platform. A stage that reserves an endpoint service's pool runs with that service up
  and its connector, as on an agent. For a stage that keeps checkpoints, the kit replays an interrupted run: a
  checkpoint-then-stop request once the runner announced its first checkpoint must leave a valid checkpoint, and a run
  resumed from it in a fresh workdir must give the uninterrupted run's digest. Thumbnails a result declares must be
  small allowed images;
- **service**: each endpoint service starts as an agent starts it (with its endpoint channel), says hello, becomes
  ready, answers the fixtures' `service_specs` through connections the kit hands it, drops an attempt that ended, stops,
  and **never listens itself** (no process of it holds a listening socket).

Fixtures (optional `conformance.json` next to the manifest) feed the fake host:
`{"datasets": {id: {"kind", "attrs", "dir"?, "files"?}}, "settings": {...}, "node_classes": [{"platform"?, "pools": {...},
"capabilities": [...]}], "params": [...examples for params.check...], "store": {"<collection>/<key>": doc},
"files": {"<path>": "<text content>"}, "runner_specs": [{"name", "stage"?, "payload", "datasets"?, "mounts"?,
"expect": {"exit"?, "artifacts"?, "reason"?}}], "service_specs": [{"name", "service", "method"?, "path", "body"?,
"expect": {"status"?}}], "ops": [{"verb", "target"?, "params"?}], "folders": {"<id>": "<dir>"}}`. A read folder's `dir`
(relative to the module) is granted read-only; every write folder gets a fresh empty outbox. Each runner spec (a
non-golden task: an ingestion or provisioning job)
runs like a golden, sandboxed with the egress proxy, and is checked against `expect`; any connection the proxy refused
fails its run (goldens too).
A dataset with a `dir` is mounted (copied) into the runner's workdir under the golden spec's mount name.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field, ValidationError

from . import bundle as B
from . import platform as pf
from . import portable
from . import manifest as mf
from . import module_protocol as mp
from .envelopes import ResultEnvelope, SpecEnvelope
from .keys import job_key
from ._base import Name, StrictContract
from .runner_protocol import DoctorOutput, Failure, checkpoint_digest
from . import _endpoint_host as EH
from ._service_run import ServiceRun


@dataclass
class Check:
    suite: str
    name: str
    status: str                  # pass | fail | skip | warn (a warning never fails the report)
    detail: str = ""


@dataclass
class Report:
    module: str = ""
    checks: list = field(default_factory=list)

    def add(self, suite, name, ok, detail=""):
        self.checks.append(Check(suite, name, "pass" if ok is True else ("skip" if ok is None else "fail"), str(detail)[:600]))
        return ok

    def warn(self, suite, name, detail):
        self.checks.append(Check(suite, name, "warn", str(detail)[:600]))

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def as_dict(self) -> dict:
        return {"module": self.module, "ok": self.ok, "checks": [c.__dict__ for c in self.checks]}

    def text(self) -> str:
        mark = {"pass": "ok  ", "fail": "FAIL", "skip": "skip", "warn": "warn"}
        lines = [f"{mark[c.status]} {c.suite:<9} {c.name}" + (f": {c.detail}" if c.detail else "") for c in self.checks]
        n = {s: sum(c.status == s for c in self.checks) for s in ("pass", "fail", "skip", "warn")}
        return "\n".join(lines + [f"{self.module}: {n['pass']} passed, {n['fail']} failed, {n['skip']} skipped"
                                   + (f", {n['warn']} warning{'s' if n['warn'] > 1 else ''}" if n["warn"] else "")])


CANARIES = "_canaries"         # the fixtures key the kit keeps its secret canaries under (never read from a file)


def canaries(man, fx: dict) -> dict:
    """A value per declared secret: the fixtures' `secrets`, else a random canary. The kit delivers them as a host would
    and fails any run or verb that lets one out."""
    given = fx.get("secrets") or {}
    return {s.name: str(given.get(s.name) or f"conform-secret-{s.name}-{os.urandom(12).hex()}") for s in man.secrets}


def _leaked(texts, canaries: dict) -> list[str]:
    """The names of the secrets whose value appears in any of `texts` (str or bytes)."""
    out = set()
    for t in texts:
        b = t if isinstance(t, bytes) else str(t).encode()
        out |= {n for n, v in canaries.items() if v.encode() in b}
    return sorted(out)


class FakeHost:
    """The host callbacks, answered from fixtures (every callback the module is permitted)."""

    def __init__(self, fx: dict, platform: str | None = None):
        self.fx = fx
        self.platform = platform or portable.host_platform()

    def callbacks(self) -> dict:
        ds = self.fx.get("datasets") or {}

        def datasets_query(p):
            ids = p.get("ids") or []
            rows = [(i, d) for i, d in ds.items() if (i in ids if ids else (p.get("kind") in (None, d.get("kind"))))]
            rows = [(i, d) for i, d in rows if all((d.get("attrs") or {}).get(k) == v for k, v in (p.get("attrs") or {}).items())]
            return {"datasets": [{"id": i, "kind": d.get("kind", "data"), "attrs": d.get("attrs") or {},
                                  **({"files": d.get("files") or []} if p.get("with_files") else {})} for i, d in rows]}

        def store_get(p):
            return {"doc": (self.fx.get("store") or {}).get(f"{p.get('collection')}/{p.get('key')}")}

        def store_query(p):
            pre = f"{p.get('collection')}/"
            return {"docs": [{**d, "_key": k[len(pre):]} for k, d in (self.fx.get("store") or {}).items() if k.startswith(pre)]}

        import base64
        import hashlib
        files = {k: (v.encode() if isinstance(v, str) else bytes(v)) for k, v in (self.fx.get("files") or {}).items()}

        def entry(path):
            b = files[path]
            return {"path": path, "digest": hashlib.sha256(b).hexdigest(), "size": len(b), "updated_at": 0.0}

        def files_read(p):
            b = files.get(p.get("path"))
            if b is None:
                raise ValueError(f"no file {p.get('path')}")
            off, n = int(p.get("offset") or 0), int(p.get("length") or mp.FILE_READ_MAX)
            return {"content_b64": base64.b64encode(b[off:off + n]).decode(), "size": len(b),
                    "digest": hashlib.sha256(b).hexdigest(), "eof": off + n >= len(b)}

        o, a = portable.split_platform(self.platform)
        node = {"node_id": "n_conform", "hostname": "conform", "online": True, "module_state": "certified",
                "platform": self.platform, "os": o, "arch": a, "os_version": None}

        def nodes_query(p):
            return {"nodes": [node] if pf.matches(self.platform, p.get("platforms") or []) else []}

        canaries = self.fx.get(CANARIES) or {}

        def secrets_get(p):
            if p.get("name") not in canaries:
                raise ValueError(f"secret {p.get('name')!r} is not declared in [[secrets]]")
            return {"set": True, "value": canaries[p["name"]]}

        return {"host.files.list": lambda p: {"files": [entry(k) for k in sorted(files) if k.startswith(p.get("prefix") or "")]},
                "host.files.stat": lambda p: ({"exists": True, "file": entry(p["path"])} if p.get("path") in files
                                              else {"exists": False, "file": None}),
                "host.files.read": files_read,
                "host.datasets.query": datasets_query, "host.settings.get": lambda p: {"value": (self.fx.get("settings") or {}).get(p.get("key"))},
                "host.store.get": store_get, "host.store.query": store_query,
                "host.nodes.query": nodes_query,
                "host.jobs.query": lambda p: {"jobs": []}, "host.blobs.stat": lambda p: {"exists": False, "size": None},
                "host.secrets.get": secrets_get}


def _argv(exec_: list[str], root: Path) -> list[str]:
    return mf.resolve_exec(list(exec_), root, sys.executable)


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, default=str)


def check_manifest(root: Path, r: Report):
    from .cli import check_ui
    try:
        man = mf.load(root / B.MANIFEST_FILE)
    except (ValidationError, OSError, ValueError) as e:
        r.add("manifest", "schema", False, str(e).splitlines()[0])
        return None
    r.module = f"{man.module.id} {man.module.version}"
    r.add("manifest", "schema", True)
    ui = check_ui(str(root / B.MANIFEST_FILE), man)
    r.add("manifest", "ui references", not ui, "; ".join(ui[:3]))
    extra = mf.unknown_fields(man)
    r.add("manifest", "no unknown fields", not extra, ", ".join(extra[:5]))
    missing = [f"{part}.runtime.lock {rt.lock}" for part, rt in (("coordinator", man.coordinator.runtime), ("runner", man.runner.runtime))
               if rt.kind == "uv" and rt.lock and not (root / rt.lock).is_file()]
    if man.results.schema_ and not (root / man.results.schema_).is_file():
        missing.append(f"results.schema {man.results.schema_}")
    for cs in man.sandbox.container_sets:
        from . import images
        try:
            images.load_key(root, cs)
        except images.ImageError as e:
            missing.append(str(e))
    r.add("manifest", "referenced files exist", not missing, ", ".join(missing))
    issues = portability_issues(root, man)
    r.add("manifest", "portable on every declared platform", not issues, "; ".join(issues[:5]))
    for w in mf.lint(man):
        r.warn("manifest", "lint", w)
    platform_views(root, man, r)
    return man


def platform_views(root: Path, man, r: Report):
    """What each platform resolves to: per node platform the runner's exec, runtime, env and file subset, and per
    coordinator platform the coordinator's exec. Every bundle path an exec names must reach that platform."""
    try:
        paths = [f["path"] for f in B.list_files(root, man)]
    except (B.BundleError, OSError) as e:
        r.add("manifest", "platform views", None, f"the bundle file list is not available: {e}")
        return
    for w in B.platform_lint(man, paths):
        r.warn("manifest", "lint", w)

    def missing(argv, have):
        return [a for a in argv if a.startswith(mf.BUNDLE_TOKEN + "/") and a[len(mf.BUNDLE_TOKEN) + 1:] not in have]

    for plat in man.requires.platforms:
        run = man.runner.for_platform(plat)
        have = set(man.bundle.subset(paths, plat))
        gone = missing(run.exec, have)
        env = ",".join(sorted(run.env)) or "none"
        r.add("manifest", f"platform view {plat}", not gone,
              f"missing {gone}" if gone else f"runner {run.exec[0]} ({run.runtime.kind}), env {env}, {len(have)}/{len(paths)} files")
    host = portable.host_platform()
    cplats = man.requires.coordinator_platforms
    for plat in cplats or [host]:
        co = man.coordinator.for_platform(plat)
        gone = missing(co.exec, set(paths))
        label = f"coordinator view {plat}" + ("" if cplats else " (any platform; this host)")
        r.add("manifest", label, not gone, f"missing {gone}" if gone else
              f"{' '.join(co.exec[:3])} ({co.runtime.kind}), concurrency {co.concurrency}, env {','.join(sorted(co.env)) or 'none'}")
    if cplats and host not in cplats:
        r.warn("manifest", "coordinator platform", f"this host ({host}) is not a coordinator platform; the protocol suite runs "
               "the coordinator here anyway")


def portability_issues(root: Path, man) -> list[str]:
    """The portability lint (spec/platforms.md): per declared platform, the execs it would run must work there."""
    out = []
    for plat in man.requires.platforms:
        os_ = portable.split_platform(plat)[0]
        if os_ != "windows":
            continue
        execs = [("runner", man.runner.for_platform(plat).exec)]
        execs += [(f"service {s.name}", s.exec) for s in man.services if not s.platforms or plat in s.platforms]
        execs += [(f"probe {p.name}", p.exec) for p in man.probes if not p.platforms or plat in p.platforms]
        for what, argv in execs:
            head = argv[0]
            if head != "python" and not head.lower().endswith(".exe"):
                out.append(f"{plat}: {what} runs {head!r}; Windows needs a .exe (or the python token)")
    for p in sorted(Path(root).rglob("*")):
        if p.suffix in (".py", ".sh", ".toml", ".json") and p.is_file() and ".venv" not in p.parts:
            try:
                if b"\r\n" in p.read_bytes():
                    out.append(f"{p.relative_to(root).as_posix()}: CRLF line endings (use LF; add a .gitattributes)")
            except OSError:
                pass
    return out


def check_bundle(root: Path, r: Report):
    with tempfile.TemporaryDirectory() as td:
        try:
            out, info = B.build(root, Path(td) / "m.mfb")
            B.verify(out)
            r.add("bundle", "builds and verifies", True, f"{info.content_digest} ({len(info.files)} files)")
        except B.BundleError as e:
            r.add("bundle", "builds and verifies", False, e)


def spawn_coordinator(root: Path, man, fx: dict):
    """The coordinator side as a host on this machine starts it: its variant for this platform, OARBANK_PLATFORM and the
    variant's env set, and initialize carrying host.platform and the host capabilities."""
    from .client import ModuleClient
    host = portable.host_platform()
    c = man.coordinator.for_platform(host)
    return ModuleClient.spawn(_argv(c.exec, root), cwd=root, env={**c.env, pf.ENV_PLATFORM: host},
                              callbacks=FakeHost(fx, host).callbacks(), permissions=set(c.permissions),
                              host_platform=host, host_capabilities=list(mp.HOST_CAPABILITIES))


def default_node_classes(man) -> list[dict]:
    """One node class per declared platform (a host asks golden.list per class)."""
    return [{"platform": p, "pools": {}, "capabilities": []} for p in man.requires.platforms]


def check_protocol(root: Path, man, fx: dict, r: Report):
    """Returns [(node_class, golden, built stage)] for the runner suite."""
    c = man.coordinator
    try:
        cli = spawn_coordinator(root, man, fx)
        info = cli.initialize(settings=fx.get("settings") or {})
    except Exception as e:                                  # noqa: BLE001 (a module that cannot start fails the suite)
        r.add("protocol", "initialize", False, f"{type(e).__name__}: {e}")
        return []
    runs, answers = [], []
    call = cli.call

    def recorded(method, params):
        out = call(method, params)
        answers.append(out)
        return out
    cli.call = recorded
    try:
        r.add("protocol", "initialize", True, f"protocol {info.protocol_version}")
        adv = set(info.capabilities)
        missing = sorted(set(c.capabilities) - adv)
        r.add("protocol", "declared capabilities advertised", not missing, ", ".join(missing))
        from .rpc import RpcError
        miss = []
        for v in [v for v, (_, _, req, _) in mp.VERBS.items() if req]:
            try:
                cli.call(v, {})                              # invalid params on purpose: only "not found" fails
            except RpcError as e:
                if e.code == mp.ERR_METHOD_NOT_FOUND:
                    miss.append(v)
        r.add("protocol", "required verbs", not miss, ", ".join(miss))
        for i, params in enumerate(fx.get("params") or []):
            a, b = cli.call("params.check", {"params": params}), cli.call("params.check", {"params": params})
            r.add("protocol", f"params.check #{i} pure", _canon(a) == _canon(b))
        stages = {s.name for s in man.stages}
        for nc in fx.get("node_classes") or default_node_classes(man):
            label = "node class " + json.dumps({k: v for k, v in nc.items() if v not in (None, [], {})}, sort_keys=True)
            g1 = cli.call("golden.list", {"node_class": nc})
            g2 = cli.call("golden.list", {"node_class": nc})
            if not r.add("protocol", f"golden.list pure ({label})", _canon(g1) == _canon(g2)):
                continue
            goldens = g1["goldens"]
            r.add("protocol", f"goldens exist ({label})", bool(goldens), "a module is certified only on golden evidence")
            for g in goldens:
                gm = mp.Golden.model_validate(g)
                keys = list(gm.platforms) + list(gm.expected_by_platform)
                bad = [k for k in keys if not pf.declared(k, man.requires.platforms)]
                if keys:
                    r.add("protocol", f"golden {g['name']}: platform keys declared ({label})", not bad,
                          f"not declared platforms or their OSes: {bad}" if bad else "")
                item = {"key_inputs": g["key_inputs"], "datasets": g.get("datasets") or [], "stages": g.get("stages") or []}
                b1 = cli.call("spec.build", {"jobs": [item], "target_spec_version": 1})
                b2 = cli.call("spec.build", {"jobs": [item], "target_spec_version": 1})
                ok = _canon(b1) == _canon(b2)
                built = b1["specs"][0]["stages"]
                want = (g.get("stages") or [None])[0]
                st = next((s for s in built if want is None or s.get("stage") == want), None)
                good = st is not None and (want is not None or len(built) == 1) and (st.get("stage") in (None, *stages))
                r.add("protocol", f"golden {g['name']}: spec.build pure and gives its stage ({label})", ok and good,
                      "" if good else f"asked {want!r}, got {[s.get('stage') for s in built]}")
                if not man.compares(want):
                    r.add("protocol", f"golden {g['name']}: its stage compares ({label})", False,
                          f"stage {want or man.default_stage()!r} has determinism none: its results are never golden-tested")
                exps = [gm.expected, *gm.expected_by_platform.values()]
                if not (all(e.get("digest") for e in exps) or "golden.compare" in adv):
                    r.add("protocol", f"golden {g['name']}: comparable", False, "expected.digest missing and no golden.compare")
                if good:
                    runs.append((nc, g, st, b1["specs"][0]))
        check_lifecycle_verbs(cli, man, adv, r)
        if man.secrets:
            leaked = _leaked([_canon(a) for a in answers], fx.get(CANARIES) or {})
            r.add("protocol", "no secret in any verb's answer (specs, goldens, plans)", not leaked,
                  f"the coordinator side passed on {leaked}: a value in a spec reaches every stage" if leaked else
                  ("host.secrets.get answered" if "secrets:read:self" in c.permissions else
                   "host.secrets.get refused without secrets:read:self"))
        check_operations(cli, man, fx, r)
    finally:
        cli.close()
    return runs


def check_lifecycle_verbs(cli, man, adv: set, r: Report):
    """integrity.check and the coordinator-move verbs: valid, pure, and within the declared effects."""
    now = 1_700_000_000.0
    calls = {
        "integrity.check": {"scope": "on_demand", "now": now},
        "move.preflight": {"move_id": "mv_conform", "to_url": "http://100.64.0.2:7443", "not_before": now, "phase": "draining", "now": now},
        "move.postflight": {"move_id": "mv_conform", "from_url": "http://100.64.0.1:7443", "epoch": 2, "now": now,
                            "skipped": [{"kind": "files" if r_.files is not None else "store", "selector": r_.files if r_.files is not None else r_.store,
                                         "class": r_.class_, "count": 1, "bytes": 1}
                                        for r_ in man.coordinator.move.rules if r_.class_ != "carry"]},
        "move.cancelled": {"move_id": "mv_conform", "reason": "conformance", "now": now},
    }
    allowed = set(man.coordinator.move.effects)
    for verb, params in calls.items():
        if verb not in adv:
            continue
        _, result_model, _, _ = mp.VERBS[verb]
        try:
            a, b = cli.call(verb, params), cli.call(verb, params)
            res = result_model.model_validate(a)
        except Exception as e:                              # noqa: BLE001
            r.add("protocol", f"{verb} answers", False, f"{type(e).__name__}: {e}"[:300])
            continue
        r.add("protocol", f"{verb} pure", _canon(a) == _canon(b))
        bad = sorted({e.kind for e in getattr(res, "effects", [])} - allowed)
        if hasattr(res, "effects"):
            r.add("protocol", f"{verb} effects declared", not bad, f"not in coordinator.move.effects: {bad}" if bad else "")
        if verb == "integrity.check":
            r.add("protocol", "integrity.check passes on the fixtures", res.ok,
                  "; ".join(f"{c.name}: {c.detail}" for c in res.checks if not c.ok and c.severity == "error")[:300])


def check_operations(cli, man, fx: dict, r: Report):
    """The fixture operations (`ops`): op.apply is pure, asks only for the operation's declared effects, and every
    datasets.create effect names its files well (oarbank_sdk.origins: digest and size always, origins https to public
    names), so an importer that registers datasets by URL is checked before a host relies on it."""
    from .origins import file_problem
    decl = {o.verb: o for o in man.operations}
    for i, op in enumerate(fx.get("ops") or []):
        verb = op.get("verb")
        label = f"operation {verb} #{i}"
        if verb not in decl:
            r.add("protocol", f"{label}: declared", False, f"{verb!r} is not in [[operations]]")
            continue
        params = {"verb": verb, "target": op.get("target"), "params": op.get("params") or {}, "actor": "conform"}
        try:
            a, b = cli.call("op.apply", params), cli.call("op.apply", params)
            res = mp.OpApplyResult.model_validate(a)
        except Exception as e:                              # noqa: BLE001
            r.add("protocol", f"{label}: op.apply answers", False, f"{type(e).__name__}: {e}"[:300])
            continue
        r.add("protocol", f"{label}: pure", _canon(a) == _canon(b))
        bad = sorted({e.kind for e in res.effects} - set(decl[verb].effects))
        r.add("protocol", f"{label}: effects declared", not bad, f"not in the operation's effects: {bad}" if bad else "")
        creates = [e for e in res.effects if e.kind == "datasets.create"]
        if creates:
            probs = [why for e in creates for f in (e.args.get("files") or []) if (why := file_problem(f))]
            r.add("protocol", f"{label}: datasets.create files", not probs, "; ".join(probs)[:300])


def _envelope(man, g, st, built) -> dict:
    return _spec_envelope(man, st.get("stage"), st["payload"], st.get("datasets") or g.get("datasets") or [],
                          st.get("mounts") or {}, built.get("spec_version") or 1,
                          job_key(man.module.id, man.module.compat, g["key_inputs"], st.get("stage")))


def _spec_envelope(man, stage_name, payload: dict, datasets: list, mounts: dict, spec_version: int, key: str) -> dict:
    """The SpecEnvelope an agent on this host writes: the stage's resources and timeout with its variant for this
    platform applied (no stage: the default one)."""
    host = portable.host_platform()
    stage = (man.stage(stage_name or man.default_stage() or "") or man.stages[0]).for_platform(host)
    return SpecEnvelope.model_validate({
        "envelope": 1, "schema": f"{man.module.id.rsplit('.', 1)[-1]}/spec@{spec_version}",
        "module_id": man.module.id, "module_version": man.module.version, "job_key": key, "stage": stage_name,
        "protocol": 1, "datasets": datasets, "mounts": mounts,
        "inputs": {}, "resources": {"cpu": stage.requires.resources.cpu, "mem_gb": stage.requires.resources.mem_gb},
        "timeout_s": stage.timeout_s, "platform": host, "payload": payload}).model_dump(mode="json", by_alias=True)


class Grants:
    """What the agent would give the runner beyond its directories, from the module's [sandbox] and the fixtures:
    the tool paths (fixtures `tools`: {id: path}, resolved as the agent does), the settings file (fixtures `settings`)
    and, for egress-allowlist, a real allowlist proxy (egress_proxy). A bootstrap stage's jobs get the bootstrap grants
    instead (spec/sandbox.md, "Bootstrap jobs"): the same proxy, no tools, `{}` as settings, no module data directory.
    Folders: a read folder is the fixtures' `folders` directory (relative to the module), granted read-only; a write
    folder is a fresh empty outbox."""

    def __init__(self, man, fx: dict, root: Path | None = None):
        self.dir = Path(tempfile.mkdtemp(prefix="conform-grants-"))
        want = [t.id for t in man.sandbox.tools]
        given = fx.get("tools") or {}
        self.missing = [t for t in want if t not in given]
        self.tools = {t: [os.path.realpath(given[t])] for t in want if t in given}
        (self.dir / "tools.json").write_text(json.dumps(self.tools), encoding="utf-8")
        (self.dir / "settings.json").write_text(json.dumps(fx.get("settings") or {}), encoding="utf-8")
        self.folders, given_f = {}, fx.get("folders") or {}
        for fg in man.sandbox.folders:
            if fg.access == "write":
                d = self.dir / "outboxes" / fg.id
                d.mkdir(parents=True)
                self.folders[fg.id] = {"path": os.path.realpath(d), "access": "write"}
            elif fg.id in given_f:
                d = Path(given_f[fg.id])
                self.folders[fg.id] = {"path": os.path.realpath(d if d.is_absolute() else (root or Path.cwd()) / d), "access": "read"}
            else:
                self.missing.append(f"folder {fg.id}")
        (self.dir / "folders.json").write_text(json.dumps(self.folders), encoding="utf-8")
        (self.dir / "bootstrap").mkdir()
        for name in ("tools.json", "settings.json", "folders.json"):
            (self.dir / "bootstrap" / name).write_text("{}", encoding="utf-8")
        self.proxy = None
        if man.sandbox.net.mode == "egress-allowlist":
            from .egress_proxy import AllowlistProxy
            self.proxy = AllowlistProxy(man.sandbox.net.allow).start()

    def env(self, bootstrap: bool = False) -> dict:
        files = self.dir / "bootstrap" if bootstrap else self.dir
        e = {"OARBANK_TOOLS_FILE": str(files / "tools.json"), "OARBANK_SETTINGS_FILE": str(files / "settings.json"),
             "OARBANK_FOLDERS_FILE": str(files / "folders.json")}
        if self.proxy:
            url = f"http://127.0.0.1:{self.proxy.port}"
            e.update({"HTTPS_PROXY": url, "HTTP_PROXY": url, "ALL_PROXY": url, "https_proxy": url, "http_proxy": url,
                      "NO_PROXY": ""})
        return e

    def policy(self, man, root, ws, data, kind="runner", bootstrap: bool = False):
        from . import sandbox as S
        # the grants directory (tools.json, settings.json) is readable like the agent's per-job files, and so is the SDK
        # the host provides to every module process (an agent's node runtime has it installed)
        sdk = str(Path(__file__).resolve().parents[1])
        if bootstrap:
            return S.node_policy(man.module.id, root, ws, None, sandbox=man.sandbox.for_bootstrap(), kind=kind,
                                 tool_paths=[str(self.dir / "bootstrap"), sdk], proxy_port=self.proxy.port if self.proxy else None)
        return S.node_policy(man.module.id, root, ws, data, sandbox=man.sandbox, kind=kind,
                             tool_paths=[p for ps in self.tools.values() for p in ps] + [str(self.dir), sdk],
                             proxy_port=self.proxy.port if self.proxy else None, folders=self.folders)

    def close(self):
        if self.proxy:
            self.proxy.stop()


GRANTS: dict = {}
SERVICES: dict = {}          # the endpoint services the runner suite started, by name, until it ends


def _service_run(root: Path, man, svc, g: "Grants") -> ServiceRun:
    policy = (lambda data: g.policy(man, root, None, data, kind="service")) if SANDBOX["on"] else None
    return ServiceRun(root, man, svc, g.env(), policy)


def _connectors(root: Path, man, stage, attempt: int) -> tuple[list, str | None]:
    """The connectors a job of `stage` gets (its stage reserves the services' pools), each service started once for the
    runner suite: (connectors, None) or ([], what went wrong)."""
    out = []
    for svc in man.endpoint_services_of(stage):
        run = SERVICES.get(svc.name)
        if run is None:
            run = SERVICES[svc.name] = _service_run(root, man, svc, GRANTS["g"])
            why = run.start()
            if why:
                run.failed = why
        if getattr(run, "failed", None):
            return [], f"endpoint service {svc.name}: {run.failed}"
        out.append(EH.ConnectorHost(svc.name, run.host, attempt))
    return out, None


def _stop_services():
    for run in SERVICES.values():
        run.stop()
    SERVICES.clear()


def _merged(*kwargs: dict) -> dict:
    """Popen arguments passing every handle each part passes (one handle list on Windows)."""
    out, handles = {}, []
    for k in kwargs:
        si = k.get("startupinfo")
        if si is not None:
            handles += list((si.lpAttributeList or {}).get("handle_list") or [])
        out.update({n: v for n, v in k.items() if n not in ("startupinfo", "pass_fds")})
        out["pass_fds"] = list(out.get("pass_fds", [])) + list(k.get("pass_fds", []))
    if not out.get("pass_fds"):
        out.pop("pass_fds", None)
    if handles:
        out["startupinfo"] = subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles})
    return out


STOP_REACTION_S = 2.0      # a cancellable runner acknowledges a nudged stop this soon (at its next safe point)


class _Nudge:
    """The agent's half of the control channel (spec/runner-protocol.md, "Control"): after replacing control.json it
    nudges the runner. POSIX: SIGUSR1 to the runner process, which starts with SIGUSR1 ignored (so a nudge before its
    handler is lost harmlessly). Windows: an auto-reset event the runner inherits, alone with its standard handles,
    named by OARBANK_CONTROL_EVENT."""
    # starts argv with SIGUSR1 ignored: an ignored disposition survives exec (no preexec_fn, which threads make unsafe)
    _IGNORING = "import os, signal, sys; signal.signal(signal.SIGUSR1, signal.SIG_IGN); os.execvp(sys.argv[1], sys.argv[1:])"

    def __init__(self):
        self.event = None
        if os.name == "nt":
            import _overlapped
            self.event = _overlapped.CreateEvent(None, False, False, None)
            os.set_handle_inheritable(self.event, True)

    def env(self) -> dict:
        return {"OARBANK_CONTROL_EVENT": str(self.event)} if self.event is not None else {}

    def argv(self, argv: list[str]) -> list[str]:
        return argv if os.name == "nt" else [sys.executable, "-I", "-S", "-c", self._IGNORING, *argv]

    def popen_kwargs(self) -> dict:
        if os.name == "nt":
            return {"startupinfo": subprocess.STARTUPINFO(lpAttributeList={"handle_list": [self.event]})}
        return {}

    def send(self, p):
        if os.name == "nt":
            import _overlapped
            _overlapped.SetEvent(self.event)
        else:
            try:
                os.kill(p.pid, signal.SIGUSR1)
            except ProcessLookupError:
                pass

    def close(self):
        if self.event is not None:
            import _winapi
            _winapi.CloseHandle(self.event)
            self.event = None


@dataclass
class Outcome:
    """One runner run: its exit code ("timeout" when the kit killed it at the stage's limit), result.json and
    failure.json as written (when the exit code allows them), its stderr tail, its workdir, and the hosts the egress proxy
    refused it. A stop run also says how the stop went: `stop` is no_phase, finished_first, stopped or ignored; `ack_s`
    is the time from the nudge to the runner's acknowledgement (failure.json, or result.json if it was finishing) and
    `exit_s` to its exit."""
    code: int | str
    result: dict | None
    failure: dict | None
    stderr: str
    ws: Path
    refused: list = field(default_factory=list)
    leaks: list = field(default_factory=list)
    events: list = field(default_factory=list)
    stop: str | None = None
    ack_s: float | None = None
    exit_s: float | None = None


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run(root: Path, man, env_doc: dict, fx: dict, locale: str = "C", stop: bool = False, bootstrap: bool = False,
         checkpoint: bool = False, resume: tuple | None = None) -> Outcome:
    """One runner run. `stop`: a stop request once the runner wrote its first phase (`checkpoint`: checkpoint, then stop,
    within checkpoint_grace_s). `resume`: (a directory holding a checkpoint by name, the envelope's `resume`): the files
    go read-only under <W>/checkpoint/ and the envelope names it."""
    ws = Path(tempfile.mkdtemp(prefix="conform-"))
    if resume is not None:
        src, doc = resume
        shutil.copytree(src, ws / "checkpoint")
        for f in (ws / "checkpoint").rglob("*"):
            if f.is_file():
                f.chmod(0o444)
        env_doc = {**env_doc, "resume": doc}
    for did in env_doc["datasets"]:
        src = (fx.get("datasets") or {}).get(did, {}).get("dir")
        mount = env_doc["mounts"].get(did, did.replace(":", "_"))
        if src:
            shutil.copytree(root / src, ws / mount)              # relative to the module (an absolute dir stays as is)
    (ws / "spec.json").write_text(json.dumps(env_doc), encoding="utf-8", newline="\n")
    (ws / "tmp").mkdir()
    data = Path(tempfile.mkdtemp(prefix="conform-data-"))
    g = GRANTS["g"]
    nudge = _Nudge()
    run = man.runner.for_platform(portable.host_platform())
    env = {**job_env(man, ws, data, locale), **run.env, **g.env(bootstrap), **nudge.env()}
    if bootstrap:
        env.pop("OARBANK_MODULE_DATA")                   # a bootstrap job keeps nothing on the node
    every = fx.get(CANARIES) or {}
    mine = {n: every[n] for n in man.secrets_of(env_doc.get("stage")) if n in every}
    if mine:                                             # only the declaring stage's runner, as the agent delivers them
        (ws / ".grants").mkdir(mode=0o700)
        fd = os.open(ws / ".grants" / "secrets.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mine, f)
        env["OARBANK_SECRETS_FILE"] = str(ws / ".grants" / "secrets.json")
    (ws / "control.json").write_text(json.dumps({"seq": 0}), encoding="utf-8")
    argv = _argv(run.exec, root) + ["run", "--spec", str(ws / "spec.json"), "--workdir", str(ws), "--out", str(ws / "result.json")]
    conns, why = _connectors(root, man, env_doc.get("stage"), 1)
    if why:
        nudge.close()
        return Outcome("service", None, None, why, ws)
    for c in conns:
        env.update(c.env)
    if {"progress_events", "checkpoint"} & set(run.capabilities):
        argv += ["--events", str(ws / "events.ndjson")]
    if SANDBOX["on"]:
        from . import sandbox as S
        text, params = S.render(g.policy(man, root, ws, data, bootstrap=bootstrap))
        argv = S.launch_argv(S.write_profile(text, data.parent / f"{data.name}.sb"), params, argv)
    seen = len(g.proxy.refused) if g.proxy else 0
    out, exited = {}, threading.Event()
    try:
        p = _spawn(nudge.argv(argv), ws, env, _merged(nudge.popen_kwargs(), *(c.popen_kwargs for c in conns)))
        for c in conns:
            c.spawned()

        def reap():                                      # the one owner of the wait: drains the pipes, records the exit
            out["out"], out["err"] = p.communicate()
            exited.set()
        reader = threading.Thread(target=reap, daemon=True)
        reader.start()
        limit = float(env_doc.get("timeout_s") or 1800)
        o = Outcome("timeout", None, None, "", ws)
        if stop:
            _stop(p, ws, nudge, exited, run.checkpoint_grace_s if checkpoint else run.stop_grace_s, limit, o, checkpoint)
        elif not exited.wait(limit):
            _kill_tree(p)
            o.stop = "timeout"
        reader.join()
    finally:
        nudge.close()
        for c in conns:
            c.close()                                    # the attempt ended: its connector closes, the service is told
    o.refused = list(g.proxy.refused[seen:]) if g.proxy else []
    if o.stop != "timeout":
        o.code = p.returncode
    o.stderr = (out.get("err") or b"").decode(errors="replace")[-400:] if p.returncode else ""
    o.result = _read_json(ws / "result.json") if p.returncode == 0 else None
    o.failure = _read_json(ws / "failure.json") if p.returncode not in (0, None) else None
    o.events = _events(ws / "events.ndjson")
    if every:
        written = [f.read_bytes() for f in ws.rglob("*") if f.is_file() and ".grants" not in f.relative_to(ws).parts
                   and f.name != "spec.json"]
        o.leaks = _leaked([out.get("out") or b"", out.get("err") or b"", *written], every)
    return o


def _secret_leaks(r: Report, label: str, man, o: Outcome):
    """No secret value in anything a run leaves: its output, result, failure.json, events or any file it wrote."""
    if man.secrets:
        r.add("runner", f"{label}: no secret in its output or files", not o.leaks,
              f"{o.leaks} appear in what the runner wrote (results, artifacts, logs)" if o.leaks else "")


def _events(path: Path) -> list[dict]:
    """The complete, valid JSON lines of a runner's events file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.split("\n")[:-1]:                   # the last piece has no newline yet
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return [e for e in out if isinstance(e, dict)]


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _acknowledged(ws: Path) -> bool:
    """The runner's own reaction to a stop: failure.json with fault transient, or the result if it was finishing."""
    f = _read_json(ws / "failure.json")
    return (f is not None and f.get("fault") == "transient") or (ws / "result.json").exists()


def _stop(p, ws: Path, nudge: "_Nudge", exited: threading.Event, grace: float, limit: float, o: Outcome,
          checkpoint: bool = False):
    """The agent's stop, timed on the runner's acknowledgement rather than on the OS tearing the process down: wait for
    the runner's first `phase` write (its sign that work is underway, so start-up is never timed), send the stop, then
    watch for the acknowledgement and the exit until stop_grace_s has passed (the agent kills the container then).
    `exited` is set by the thread that owns the process's wait; nothing here polls the process itself."""
    t0 = time.monotonic()
    # a checkpoint-then-stop waits for the runner's first own checkpoint, so the one it writes on request holds progress
    ready = (lambda: b'"checkpoint"' in _read_bytes(ws / "events.ndjson")) if checkpoint else (lambda: (ws / "phase").exists())
    while not exited.is_set() and not ready() and time.monotonic() - t0 < limit:
        exited.wait(0.005)
    if not ready():
        o.stop = "no_phase"
    elif exited.is_set() or _acknowledged(ws):
        o.stop = "finished_first"
    else:
        t = _request_stop(p, ws, nudge, checkpoint)      # control.json stop and the nudge, nothing else
        while not exited.is_set() and time.monotonic() - t < grace:
            if o.ack_s is None and _acknowledged(ws):
                o.ack_s = time.monotonic() - t
            exited.wait(0.005)
        if not exited.is_set():
            o.stop = "ignored"
        else:
            o.stop, o.exit_s = "stopped", time.monotonic() - t
            if o.ack_s is None and _acknowledged(ws):
                o.ack_s = o.exit_s                       # written just before it exited, between two looks
    if not exited.is_set():
        _kill_tree(p)


def job_env(man, ws: Path, data: Path, locale: str = "C.UTF-8") -> dict:
    """The runner environment the agent passes (spec/runner-protocol.md and spec/platforms.md), for this host's OS."""
    env = {"OARBANK_WORKDIR": str(ws), "OARBANK_TMP": str(ws / "tmp"), "OARBANK_MODULE_DATA": str(data),
           "OARBANK_MODULE": man.module.id.rsplit(".", 1)[-1], "OARBANK_ATTEMPT_ID": "1", "OARBANK_PROTOCOL": "1",
           "OARBANK_PLATFORM": portable.host_platform(), "PYTHONUTF8": "1"}
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        env.update({"SystemRoot": root, "windir": root, "ComSpec": rf"{root}\System32\cmd.exe", "PATHEXT": ".COM;.EXE",
                    "PATH": rf"{root}\System32;{root};{root}\System32\Wbem", "USERPROFILE": str(ws), "TEMP": str(ws / "tmp"),
                    "TMP": str(ws / "tmp"), "APPDATA": str(ws / "AppData" / "Roaming"), "LOCALAPPDATA": str(ws / "AppData" / "Local"),
                    "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
                    "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1")})
    else:
        env.update({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(ws), "TMPDIR": str(ws / "tmp"),
                    "LANG": locale, "LC_ALL": locale})
    return env


def _spawn(argv, cwd, env, kwargs: dict):
    """Start the runner in its own process container (a process group here; the agent uses cgroups or Job Objects)."""
    if os.name == "nt":
        return subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **kwargs)
    return subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                            **kwargs)


def _request_stop(p, ws: Path, nudge: _Nudge, checkpoint: bool = False) -> float:
    """Replace control.json with a stop request (with `checkpoint`: checkpoint, then stop) and nudge the runner; returns
    when (monotonic) it was nudged."""
    doc = json.loads((ws / "control.json").read_text(encoding="utf-8"))
    tmp = ws / "control.json.tmp"
    req = {**doc, "seq": int(doc.get("seq", 0)) + 1, "stop": True, **({"checkpoint": True} if checkpoint else {})}
    tmp.write_text(json.dumps(req), encoding="utf-8")
    os.replace(tmp, ws / "control.json")
    t = time.monotonic()
    nudge.send(p)
    return t


def _kill_tree(p):
    """Kill the runner's process group (its container here). A group whose processes all exited meanwhile is fine:
    POSIX says ESRCH, macOS EPERM while the leader is an unreaped zombie, so EPERM passes only when no live member is
    left."""
    if os.name != "posix":
        p.kill()                                         # Popen.kill tolerates a process that already exited
        return
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if _live_members(p.pid):
            raise


def _live_members(pgid: int) -> list[int]:
    """The processes of a group that have not exited (zombies excluded)."""
    ps = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True, check=True).stdout
    return [int(f[0]) for f in (line.split() for line in ps.splitlines())
            if len(f) == 3 and int(f[1]) == pgid and not f[2].startswith("Z")]


def _egress(r: Report, label: str, o: Outcome):
    """A connection the allowlist proxy refused fails the run, even when the runner coped with the refusal."""
    if o.refused:
        r.add("runner", f"{label}: egress within the allowlist", False, "; ".join(sorted(set(o.refused)))[:600])


def _check_stop(r: Report, label: str, o: Outcome):
    """`cancellable`: the runner acknowledges a nudged stop at its next safe point (within STOP_REACTION_S, measured to
    its acknowledgement), and exits within stop_grace_s (75 after failure.json; 0 if it was finishing its result)."""
    name = f"{label}: a nudged stop is acknowledged within {STOP_REACTION_S:g} s"
    if o.stop == "no_phase":
        r.add("runner", name, None, "the runner wrote no <W>/phase before finishing: the kit times a stop from the "
              "runner's first phase write (Control.phase), so start-up is never counted")
        return
    if o.stop == "finished_first":
        r.add("runner", name, None, "the golden finished before the kit could send the stop; make one golden run longer")
        return
    r.add("runner", name, o.ack_s is not None and o.ack_s <= STOP_REACTION_S,
          f"acknowledged {o.ack_s:.3f} s after the nudge" if o.ack_s is not None else
          "no failure.json with fault transient (Control.acknowledge_stop) and no result")
    want = 0 if o.result is not None else 75
    r.add("runner", f"{label}: exits within stop_grace_s after a stop", o.stop == "stopped" and o.code == want,
          f"exit {o.code}, {o.exit_s:.3f} s after the nudge" if o.stop == "stopped" else
          "still running at stop_grace_s; the agent kills the container then")


def _take_checkpoint(o: Outcome, man, stage_name) -> tuple:
    """The last checkpoint a run's runner announced, checked as the agent checks it (regular files in the workdir, unique
    names, within the stage's max_mb, `data` within 4 KiB) and copied by name into a fresh directory: (directory, the
    envelope's `resume`, problem)."""
    from .runner_protocol import CHECKPOINT_DATA_MAX, Event
    cp = man.checkpoint_of(stage_name)
    events = [e for e in o.events if e.get("kind") == "checkpoint"]
    if not events:
        return None, None, "no checkpoint event in the events file"
    try:
        ev = Event.model_validate(events[-1])
    except ValidationError as e:
        return None, None, f"not a checkpoint event: {str(e).splitlines()[0]}"
    if not ev.files:
        return None, None, "a checkpoint event lists no files"
    if len(json.dumps(ev.data, separators=(",", ":")).encode()) > CHECKPOINT_DATA_MAX:
        return None, None, f"its data is over {CHECKPOINT_DATA_MAX} bytes"
    names = [f.checkpoint_name() for f in ev.files]
    if len(set(names)) != len(names):
        return None, None, "checkpoint names are not unique"
    out = Path(tempfile.mkdtemp(prefix="conform-ckpt-"))
    entries, total = [], 0
    for f in ev.files:
        src = o.ws / f.path
        if src.is_symlink() or not src.is_file() or o.ws.resolve() not in src.resolve().parents:
            return None, None, f"{f.path} is not a regular file in the workdir"
        dst = out / f.checkpoint_name()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        size = dst.stat().st_size
        total += size
        entries.append({"name": f.checkpoint_name(), "digest": hashlib.sha256(dst.read_bytes()).hexdigest(), "size": size})
    if cp and total > cp.max_mb * (1 << 20):
        return None, None, f"{total} bytes is over the stage's checkpoint.max_mb ({cp.max_mb})"
    return out, {"from_attempt": 1, "digest": checkpoint_digest(entries), "data": ev.data}, ""


def _check_resume(root: Path, man, fx: dict, env_doc: dict, stage_name, digest, cli, r: Report, label: str):
    """A stage that keeps checkpoints: a checkpoint-then-stop leaves a valid checkpoint and exits 75, and a run resumed from
    it in a fresh workdir gives the uninterrupted run's digest (`results.determinism` still holds for resumed attempts)."""
    o = _run(root, man, env_doc, fx, stop=True, checkpoint=True)
    if o.stop in ("no_phase", "finished_first"):
        r.add("runner", f"{label}: checkpoint, then stop", None, "the run finished before the kit could ask for a checkpoint; "
              "make the golden run longer")
        return
    ckpt, resume, why = _take_checkpoint(o, man, stage_name)
    stopped = o.stop == "stopped" and o.code == 75 and (o.failure or {}).get("fault") == "transient"
    if not r.add("runner", f"{label}: checkpoint, then stop", stopped and ckpt is not None,
                 why or ("" if stopped else f"exit {o.code} ({o.stop}); a checkpoint-then-stop ends like an acknowledged "
                                            "stop: failure.json fault transient, exit 75, within checkpoint_grace_s")):
        return
    o2 = _run(root, man, env_doc, fx, resume=(ckpt, resume))
    v2 = cli.call("result.evaluate", {"spec": {**env_doc, "resume": resume}, "result": o2.result, "stage": stage_name}) \
        if o2.result else {}
    same = bool(o2.result) and v2.get("digest") == digest
    r.add("runner", f"{label}: resumes from a checkpoint with the same digest", same,
          "" if same else o2.stderr or f"resumed digest {v2.get('digest')}, uninterrupted {digest}")
    if o2.result:
        missing = _missing_locals(o2.ws, ResultEnvelope.model_validate(o2.result))
        r.add("runner", f"{label}: a resumed result's files are all there", not missing,
              f"missing after resuming (keep them in the checkpoint): {missing[:5]}" if missing else "")


def _missing_locals(ws: Path, res: ResultEnvelope) -> list[str]:
    """The `local` files (thumbnails included) a result names that its workdir lacks: the agent would fail the upload."""
    out = []
    for a in res.artifacts:
        for f in a.files:
            for local in (f.local, f.thumbnail.local if f.thumbnail else None):
                if local and not (ws / local).is_file():
                    out.append(local)
    return out


def _thumbnails(ws: Path, res: ResultEnvelope) -> list[str] | None:
    """Problems with the thumbnails a result declares (None: it declares none): each a small allowed image."""
    from . import media
    probs, seen = [], False
    for a in res.artifacts:
        for f in a.files:
            if f.thumbnail is None or not f.thumbnail.local:
                continue
            seen = True
            t = ws / f.thumbnail.local
            if not t.is_file():
                probs.append(f"{f.thumbnail.local}: missing")
                continue
            why = media.problem(media.THUMBNAIL, t.read_bytes()[:media.HEAD_BYTES], t.stat().st_size)
            if why:
                probs.append(f"{f.thumbnail.local}: {why}")
    return probs if seen else None


def check_runner(root: Path, man, fx: dict, runs: list, r: Report):
    g = GRANTS["g"] = Grants(man, fx, root)
    GRANTS["resumed"] = set()
    try:
        _check_runner(root, man, fx, runs, r, g)
        check_services(root, man, fx, r, g)
    finally:
        _stop_services()
        g.close()


def _needs_broker(man, stage_name) -> bool:
    st = next((s for s in man.stages if s.name == stage_name), None)
    return bool(st and ("containers" in st.requires.pools or "containers" in st.requires.needs_pools))


def _check_runner(root: Path, man, fx: dict, runs: list, r: Report, g: "Grants"):
    if g.missing:
        r.add("runner", "host tools and folders", None, f"fixtures `tools` and `folders` do not map {g.missing}: the runner "
              "may fail without them")
    data = Path(tempfile.mkdtemp(prefix="conform-data-"))
    (data / "tmp").mkdir()
    host = portable.host_platform()
    argv = _argv(man.runner.for_platform(host).exec, root) + ["doctor", "--json"]
    if SANDBOX["on"]:
        from . import sandbox as S
        text, params = S.render(g.policy(man, root, None, data, kind="doctor"))
        argv = S.launch_argv(S.write_profile(text, data.parent / f"{data.name}.sb"), params, argv)
    denv = {**job_env(man, data, data), **man.runner.for_platform(host).env, **g.env()}
    denv.pop("OARBANK_WORKDIR", None)
    out = subprocess.run(argv, cwd=data, capture_output=True, text=True, timeout=120, env=denv, encoding="utf-8")
    try:
        d = DoctorOutput.model_validate(json.loads(out.stdout.strip().splitlines()[-1]))
        r.add("runner", "doctor --json", out.returncode == 0, f"health {d.health}")
        if "platform" in d.attrs:
            r.add("runner", "doctor attrs.platform is OARBANK_PLATFORM", d.attrs["platform"] == host,
                  f"{d.attrs['platform']!r} != {host!r}" if d.attrs["platform"] != host else "")
    except (ValueError, IndexError, ValidationError) as e:
        r.add("runner", "doctor --json", False, f"not a DoctorOutput: {e}")
    _check_goldens(root, man, fx, runs, r)
    _check_runner_specs(root, man, fx, r)


def _check_goldens(root: Path, man, fx: dict, runs: list, r: Report):
    host = portable.host_platform()
    if not runs:
        r.add("runner", "golden runs", None, "no goldens to run")
        return
    cli = spawn_coordinator(root, man, fx)
    cli.initialize(settings=fx.get("settings") or {})
    adv = set(cli.info.capabilities)
    ran = False
    # this host's node class first: its goldens are the ones a node of this platform gets
    rank = lambda run: 0 if run[0].get("platform") == host else (1 if not run[0].get("platform") else 2)
    try:
        for nc, g, st, built in sorted(runs, key=rank):
            gm = mp.Golden.model_validate(g)
            if not gm.runs_on(host) and nc.get("platform") != host:
                continue
            expected = gm.for_platform(host).expected
            env_doc = _envelope(man, g, st, built)
            missing = [d for d in env_doc["datasets"] if not (fx.get("datasets") or {}).get(d, {}).get("dir")]
            label = f"golden {g['name']} ({st.get('stage') or 'single stage'})"
            if missing:
                r.add("runner", label, None, f"datasets not in fixtures: {missing[:3]}")
                continue
            if _needs_broker(man, st.get("stage")):
                r.add("runner", label, None, "its stage reserves the agent's `containers` pool; the kit has no container "
                      "broker, so it runs only on an agent")
                continue
            ran = True
            o = _run(root, man, env_doc, fx)
            _egress(r, label, o)
            _secret_leaks(r, label, man, o)
            res = o.result
            if not r.add("runner", f"{label}: result envelope", res is not None, o.stderr or f"exit {o.code}"):
                continue
            try:
                ResultEnvelope.model_validate(res)
            except ValidationError as e:
                r.add("runner", f"{label}: result envelope valid", False, str(e).splitlines()[0])
                continue
            v = cli.call("result.evaluate", {"spec": env_doc, "result": res, "stage": st.get("stage")})
            r.add("runner", f"{label}: accepted", v["verdict"] == "accept", v.get("reason"))
            if "golden.compare" in adv:
                ok = cli.call("golden.compare", {"expected": expected, "result": res})["ok"]
            else:
                ok = bool(v.get("digest")) and v.get("digest") == expected.get("digest")
            r.add("runner", f"{label}: matches the golden", ok)
            thumbs = _thumbnails(o.ws, ResultEnvelope.model_validate(res))
            if thumbs is not None:
                r.add("runner", f"{label}: thumbnails are small allowed images", not thumbs, "; ".join(thumbs)[:300])
            if man.determinism_of(st.get("stage")) == "exact":
                o2 = _run(root, man, env_doc, fx, locale="en_US.UTF-8")
                v2 = cli.call("result.evaluate", {"spec": env_doc, "result": o2.result, "stage": st.get("stage")}) if o2.result else {}
                r.add("runner", f"{label}: deterministic across locales/workdirs",
                      bool(o2.result) and v2.get("digest") == v.get("digest"), o2.stderr)
            if "cancellable" in man.runner.capabilities:
                _check_stop(r, label, _run(root, man, env_doc, fx, stop=True))
            if man.checkpoint_of(st.get("stage")):
                _check_resume(root, man, fx, env_doc, st.get("stage"), v.get("digest"), cli, r, label)
                checked = GRANTS.setdefault("resumed", set())
                checked.add(st.get("stage") or man.default_stage())
            break                                           # one golden per module is enough for the runner suite
    finally:
        cli.close()
    if not ran:
        r.add("runner", "golden runs", None, "every golden needs datasets the fixtures do not provide")


class RunnerSpecExpect(StrictContract):
    exit: int = 0
    artifacts: list[str] | None = None
    reason: str | None = None


class RunnerSpec(StrictContract):
    """A conformance fixture's extra runner spec (`runner_specs`): a non-golden task the runner must handle, run like a
    golden and checked against its expected outcome."""
    name: str = Field(min_length=1, max_length=80)
    stage: Name | None = None
    payload: dict
    datasets: list[str] = Field(default_factory=list)
    mounts: dict[str, str] = Field(default_factory=dict)
    expect: RunnerSpecExpect = Field(default_factory=RunnerSpecExpect)


def _uploaded(ws: Path, res: ResultEnvelope) -> list[dict]:
    """The result's artifacts as the host sees them after the agent's upload: each file's sha256 and size, hashed from
    the workdir (`local`); a file the runner listed but did not write has neither."""
    out = []
    for a in res.artifacts:
        files = []
        for f in a.files:
            src = ws / (f.local or "")
            if f.local and src.is_file():
                files.append({"path": f.path, "digest": hashlib.sha256(src.read_bytes()).hexdigest(), "size": src.stat().st_size})
            else:
                files.append({"path": f.path, "digest": f.digest, "size": f.size})
        out.append({"name": a.name, "files": files})
    return out


def _check_runner_specs(root: Path, man, fx: dict, r: Report):
    """Every `runner_specs` entry runs as a golden does (sandboxed with the module's grants, the egress proxy for
    egress-allowlist) and is checked against `expect`: the exit code; for exit 0 a valid ResultEnvelope (artifact names
    are Names) with the expected artifact names; otherwise a valid failure.json with the expected reason. A bootstrap
    stage's spec runs with the bootstrap grants, and its result must be exactly pinned datasets, as the host requires."""
    for i, raw in enumerate(fx.get("runner_specs") or []):
        try:
            spec = RunnerSpec.model_validate(raw)
        except ValidationError as e:
            r.add("runner", f"runner spec #{i}: fixture", False, str(e).replace("\n", " ")[:300])
            continue
        label = f"runner spec {spec.name}"
        stage = man.stage(spec.stage) if spec.stage else man.stage(man.default_stage() or "")
        if spec.stage and stage is None:
            r.add("runner", f"{label}: fixture", False, f"stage {spec.stage!r} is not a declared stage")
            continue
        if stage is not None and stage.after:
            r.add("runner", f"{label}: fixture", False, f"stage {stage.name!r} runs after {stage.after!r}: runner specs run "
                  "stages without inputs")
            continue
        missing = [d for d in spec.datasets if not (fx.get("datasets") or {}).get(d, {}).get("dir")]
        if missing:
            r.add("runner", label, None, f"datasets not in fixtures: {missing[:3]}")
            continue
        if stage is not None and _needs_broker(man, stage.name):
            r.add("runner", label, None, "its stage reserves the agent's `containers` pool; the kit has no container broker")
            continue
        sent = None if stage is None or stage.name == man.default_stage() else stage.name     # as the host sends it
        env_doc = _spec_envelope(man, sent, spec.payload, spec.datasets, spec.mounts, 1,
                                 job_key(man.module.id, man.module.compat, spec.payload, sent))
        boot = stage is not None and stage.bootstrap
        o = _run(root, man, env_doc, fx, bootstrap=boot)
        _egress(r, label, o)
        _secret_leaks(r, label, man, o)
        r.add("runner", f"{label}: exit {spec.expect.exit}", o.code == spec.expect.exit,
              f"exit {o.code}" + (f": {o.stderr}" if o.stderr else ""))
        if o.code == 0:
            try:
                res = ResultEnvelope.model_validate(o.result)
            except ValidationError as e:
                r.add("runner", f"{label}: result envelope valid", False, str(e).replace("\n", " ")[:300])
                continue
            names = sorted(a.name for a in res.artifacts)
            if spec.expect.artifacts is not None:
                r.add("runner", f"{label}: artifacts", names == sorted(spec.expect.artifacts),
                      f"wrote {names}, expected {sorted(spec.expect.artifacts)}")
            if boot:
                problem = man.bootstrap_problem(res.payload, _uploaded(o.ws, res))
                r.add("runner", f"{label}: artifacts match the pinned datasets", problem is None, problem or "")
            thumbs = _thumbnails(o.ws, res)
            if thumbs is not None:
                r.add("runner", f"{label}: thumbnails are small allowed images", not thumbs, "; ".join(thumbs)[:300])
            if stage is not None and stage.checkpoint and stage.name not in GRANTS.setdefault("resumed", set()):
                GRANTS["resumed"].add(stage.name)
                cli = spawn_coordinator(root, man, fx)
                try:
                    cli.initialize(settings=fx.get("settings") or {})
                    v = cli.call("result.evaluate", {"spec": env_doc, "result": o.result, "stage": sent})
                    _check_resume(root, man, fx, env_doc, sent, v.get("digest"), cli, r, label)
                finally:
                    cli.close()
        elif isinstance(o.code, int):
            try:
                f = Failure.model_validate(o.failure)
            except ValidationError:
                r.add("runner", f"{label}: failure.json", False, "missing or not a Failure: exit codes 2, 3 and 75 need one")
                continue
            if spec.expect.reason is not None:
                r.add("runner", f"{label}: failure reason", f.reason == spec.expect.reason,
                      f"{f.reason!r}" + (f" ({f.detail[:200]})" if f.detail else ""))


class ServiceSpecExpect(StrictContract):
    status: int = 200


class ServiceSpec(StrictContract):
    """A conformance fixture's request to an endpoint service (`service_specs`), sent through a connection the kit
    hands the service as an agent would."""
    name: str = Field(min_length=1, max_length=80)
    service: Name
    method: str = "POST"
    path: str = Field(min_length=1)
    body: dict | list | None = None
    expect: ServiceSpecExpect = Field(default_factory=ServiceSpecExpect)


def _request(conn, method: str, path: str, body) -> tuple[int, bytes]:
    import http.client

    class _Here(http.client.HTTPConnection):
        def connect(self):
            self.sock = conn
    c = _Here("localhost")
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers={"Content-Type": "application/json", "Connection": "close"} if data else
              {"Connection": "close"})
    resp = c.getresponse()
    out = (resp.status, resp.read())
    c.close()
    return out


def check_services(root: Path, man, fx: dict, r: Report, g: "Grants"):
    """The service suite: each endpoint service on this host's platform, run as an agent runs it (spec/service-protocol.md,
    "Endpoints")."""
    host = portable.host_platform()
    specs = []
    for i, raw in enumerate(fx.get("service_specs") or []):
        try:
            specs.append(ServiceSpec.model_validate(raw))
        except ValidationError as e:
            r.add("service", f"service spec #{i}: fixture", False, str(e).replace("\n", " ")[:300])
    for svc in man.services:
        if not svc.endpoint or not pf.matches(host, svc.platforms):
            continue
        label = f"service {svc.name}"
        run = _service_run(root, man, svc, g)
        try:
            why = run.start()
            r.add("service", f"{label}: starts, says hello on its endpoint channel and becomes ready", why is None, why or "")
            if why:
                refused = any(w in why.lower() for w in ("permission", "not permitted")) and any(
                    w in why for w in ("bind", "listen"))
                if refused:
                    r.add("service", f"{label}: the service never listens itself", False,
                          f"the sandbox refused it a listening socket: {why}")
                continue
            listens = run.listening()
            for spec in [s for s in specs if s.service == svc.name]:
                try:
                    status, body = _request(run.connect(attempt=7), spec.method, spec.path, spec.body)
                    r.add("service", f"{label}: service spec {spec.name}", status == spec.expect.status,
                          f"status {status}: {body[:200]!r}")
                except OSError as e:
                    r.add("service", f"{label}: service spec {spec.name}", False, repr(e))
            run.host.ended(7)
            listens += run.listening()
            r.add("service", f"{label}: the service never listens itself", not listens,
                  "; ".join(sorted(set(listens)))[:600] or "no listening socket after ready and after its requests")
        finally:
            stopped = run.stop()
        r.add("service", f"{label}: stops", stopped is None, stopped or "")


def check_images(root: Path, man, fx: dict, r: Report):
    """Container sets (spec/sandbox.md, "Image sets"), with the reference policy the agent shares: each member the
    fixtures name verifies (fixtures `images`: {set: {"layout"?: OCI image layout dir, "members": [refs]}}; without a
    layout the set's registry is asked), and the two refusals hold: an image outside the set's prefix and an unsigned
    image inside it are both `image_not_approved`."""
    from . import images
    for cs in man.sandbox.container_sets:
        label = f"container set {cs.name}"
        try:
            pol = images.SetPolicy(cs, images.load_key(root, cs))
        except images.ImageError as e:
            r.add("images", f"{label}: key", False, str(e))
            continue
        conf = (fx.get("images") or {}).get(cs.name) or {}
        src = images.source_for(root / conf["layout"] if conf.get("layout") else None)
        highest = None
        for ref in conf.get("members") or []:
            v = pol.verify(ref, cs.platform, src)
            r.add("images", f"{label}: {ref} verifies", v.ok, v.reason)
            highest = v.seq if v.seq is not None and (highest is None or v.seq > highest) else highest
        if not conf.get("members"):
            r.add("images", f"{label}: members verify", None, "fixtures `images` name no members of this set")
        stray = "sha256:" + hashlib.sha256(os.urandom(32)).hexdigest()
        base = cs.repository.rstrip("/")
        outside = f"{cs.registry}/{base}-outside/conformance@{stray}"
        inside = f"{cs.registry}/{cs.repository}conformance-unsigned@{stray}" if cs.repository.endswith("/") else \
            f"{cs.registry}/{cs.repository}@{stray}"
        for what, ref in (("an image outside the set", outside), ("an unsigned image inside the set", inside)):
            v = pol.verify(ref, cs.platform, src, highest)
            r.add("images", f"{label}: {what} is refused (image_not_approved)", v.code == "image_not_approved",
                  v.reason if not v.ok else f"{ref} was approved")


SANDBOX = {"on": sys.platform == "darwin"}


def conform(root, fixtures: dict | None = None, runner: bool = True, sandbox: bool | None = None) -> Report:
    """`sandbox`: run the runner under the module sandbox (default: on macOS)."""
    root = Path(root).resolve()
    if sandbox is not None:
        SANDBOX["on"] = sandbox and sys.platform == "darwin"
    fx = fixtures if fixtures is not None else (json.loads((root / "conformance.json").read_text(encoding="utf-8"))
                                                if (root / "conformance.json").exists() else {})
    r = Report()
    man = check_manifest(root, r)
    if man is None:
        return r
    check_bundle(root, r)
    fx = {**fx, CANARIES: canaries(man, fx)}
    if man.sandbox.container_sets:
        check_images(root, man, fx, r)
    runs = check_protocol(root, man, fx, r)
    if runner:
        check_runner(root, man, fx, runs, r)
    return r
