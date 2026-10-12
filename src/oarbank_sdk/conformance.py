"""The conformance kit: what a module must pass before a host installs it (spec/conformance.md).

    oarbank-sdk conform <module-dir> [--fixtures conformance.json] [--no-runner] [--no-workloads] [--only workload] [--json]

Suites:

- **manifest**: strict schema, UI contract references, no unknown fields, the lint warnings, and a resolved view per
  declared platform (runner exec, runtime, env and file subset) and per coordinator platform (coordinator exec);
- **bundle**: the module builds into a bundle (no core imports, no symlinks) that verifies;
- **settings**: the `[settings].schema` file parses as the core reads it, and the fixtures' settings validate against it
  (runners get the node-scoped keys, the coordinator side every key, each with its default when unset);
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
- **gpu**: the runner's GPU APIs for this host's platform against what this host provides (`oarbank-sdk gpu-apis`, the
  probes an agent runs): a golden run, runner spec or endpoint service whose GPU APIs this host lacks is skipped with
  that reason, as a node like this host would get none of that work, never failed; this host's node class carries its
  `gpu_apis`, so golden lists that differ per API are exercised;
- **service**: each endpoint service starts as an agent starts it (with its endpoint channel), says hello, becomes
  ready, answers the fixtures' `service_specs` through connections the kit hands it, drops an attempt that ended, stops,
  and **never listens itself** (no process of it holds a listening socket);
- **listener**: each endpoint service that lists inbound listeners starts the same way, with OARBANK_LISTENERS_FILE
  (and, for a `listen_fd` listener, the listening socket the kit made), gets an announcement for each listener after its
  hello, accepts a connection the kit hands it on each listener (with the PROXY v2 header when the listener has one),
  answers the fixtures' `listener_specs` (and its `service_specs`), sends only well-formed `listener_allow` messages,
  stops, and never listens itself;
- **workload**: each workload runs as an agent runs it (a run directory, the private directory with its control
  document, the protocol's environment and a unique token, `run` as the leader of a new process group or Job Object):
  it keeps running, answers `status` healthy, survives a freeze (`freeze_ok`), shows and clears a cooperative pause,
  **stays in its process container** (every process carrying the token is in the leader's group), never listens,
  writes valid events, leaks no secret and ends on a stop within `stop_timeout_s`;
- **ui**: every declared view is computed from `fixtures/ui/inputs.json` and validated against its declaration as the
  host validates it (shape, row limit, declared columns, artifact references); every page and panel renders with the
  fixtures and `fixtures/ui/context.json` (a placeholder is a warning); axe finds no serious or critical violation
  (skipped, with the reason, without Node, axe-core and jsdom: OARBANK_SDK_NODE_MODULES).

Fixtures (optional `conformance.json` next to the manifest) feed the fake host:
`{"datasets": {id: {"kind", "attrs", "dir"?, "files"?}}, "settings": {...}, "node_classes": [{"platform"?, "pools": {...},
"capabilities": [...], "gpu_apis"?: {"host": [...], "containers": [...]}}], "params": [...examples for params.check...], "store": {"<collection>/<key>": doc},
"files": {"<path>": "<text content>"}, "runner_specs": [{"name", "stage"?, "payload", "datasets"?, "mounts"?,
"expect": {"exit"?, "artifacts"?, "reason"?}}], "service_specs": [{"name", "service", "method"?, "path", "body"?,
"expect": {"status"?}}], "listener_specs": [{"name", "listener", "send"? | "method"?, "path"?, "body"?,
"expect": {"status"?, "contains"?}}], "ops": [{"verb", "target"?, "params"?}], "folders": {"<id>": "<dir>"}}`. A read folder's `dir`
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
from . import gpu
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


NODE_SETTINGS = "_node_settings"


def check_settings(root: Path, man, fx: dict, r: Report) -> dict:
    """The settings suite: the declared settings parse (spec/manifest.md, "Settings") and the fixtures' `settings` are
    values the core would accept. Returns what the fake host hands on, as the core does: every key (the fixture's value,
    else its default) to the coordinator side, the node-scoped ones to runners and services."""
    from . import settings as S
    try:
        sch = S.load(root, man)
    except S.SettingsSchemaError as e:
        r.add("settings", "schema", False, "; ".join(e.errors[:3]))
        return {}
    if not man.settings.schema_:
        return {}
    r.add("settings", "schema", True, f"{len(sch.keys)} settings")
    for w in sch.warnings:
        r.warn("settings", "lint", w)
    given = fx.get("settings") or {}
    bad = S.check_values(sch.keys, given)
    r.add("settings", "fixtures validate", not bad, "; ".join(bad[:3]))
    missing = [k.name for k in sch.keys if k.required and k.name not in given]
    r.add("settings", "required settings have fixture values", not missing,
          f"set {', '.join(missing)} in the fixtures' settings (the core holds the module's work until the owner sets them)"
          if missing else "")
    return {"settings": S.effective(sch.keys, given), NODE_SETTINGS: S.effective(sch.keys, given, "node")}


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


SCRATCH: dict = {}           # the running conform()'s scratch directory: every directory and file the kit makes goes in it


def _scratch(prefix: str) -> Path:
    """A fresh directory for one part of a conform() run, inside the run's scratch directory, which conform() removes
    when it ends however it ends (the processes the kit starts never own a directory: a killed runner leaves nothing)."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=SCRATCH.get("dir")))


def check_bundle(root: Path, r: Report):
    with tempfile.TemporaryDirectory(prefix="conform-bundle-", dir=SCRATCH.get("dir")) as td:
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
    """One node class per declared platform (a host asks golden.list per class); this host's carries its GPU APIs."""
    host = portable.host_platform()
    return [{"platform": p, "pools": {}, "capabilities": [],
             **({"gpu_apis": {"host": host_gpu_apis(), "containers": []}} if p == host else {})} for p in man.requires.platforms]


HOST_GPU: dict = {}


def host_gpu_apis() -> list[str]:
    """This host's GPU APIs (oarbank_sdk.gpu.detect), detected once per conformance run."""
    if "host" not in HOST_GPU:
        HOST_GPU.update(gpu.detect())
    return HOST_GPU["host"]


def gpu_shortfall(man, stage: str | None) -> str | None:
    """Why a node like this host gets none of a stage's jobs, by the GPU APIs it provides (the core places by the same
    rule, docs: spec/runner-protocol.md "GPU use"), or None. The kit has no container runtime, so it reports no
    container APIs; a stage needing them runs only on an agent, as every container stage does."""
    miss = gpu.unmet([n for n in man.gpu_needs(stage, portable.host_platform()) if n["where"] == "host"],
                     {"host": host_gpu_apis()})
    if not miss:
        return None
    return (f"its {miss[0]['source']} needs {gpu.describe(miss[0])}; this host provides "
            f"{', '.join(host_gpu_apis()) or 'no GPU API'} (`oarbank-sdk gpu-apis`), so a node like it gets none of these jobs")


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
        check_workload_report(cli, man, fx, adv, r)
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


def check_workload_report(cli, man, fx: dict, adv: set, r: Report):
    """`workload.report` (spec/module-protocol.md, "Workload reports"): fed the fixtures' `workload_reports` (report
    `data` objects) as one delivery from a reporting workload, it answers a valid result, is pure (a repeated delivery
    asks for the same effects) and asks only for the effects coordinator.workload_effects declares."""
    if mf.WORKLOAD_REPORT not in adv:
        return
    w = next((w for w in man.workloads if w.reports), None)
    datas = fx.get("workload_reports") or []
    if w is None or not datas:
        r.add("protocol", "workload.report", None, "the fixtures name no `workload_reports` (report data objects) to deliver")
        return
    params = {"workload": w.name, "node": "n_conform", "seat_id": "s_conform" if w.identity == "per_node" else None,
              "fence": 1 if w.identity == "per_node" else None,
              "events": [{"seq": i + 1, "t": 1_700_000_000.0 + i, "data": d} for i, d in enumerate(datas)]}
    try:
        a, b = cli.call("workload.report", params), cli.call("workload.report", params)
        res = mp.WorkloadReportResult.model_validate(a)
    except Exception as e:                              # noqa: BLE001
        r.add("protocol", "workload.report answers", False, f"{type(e).__name__}: {e}"[:300])
        return
    r.add("protocol", "workload.report pure (a repeated delivery asks for the same effects)", _canon(a) == _canon(b))
    bad = sorted({e.kind for e in res.effects} - set(man.coordinator.workload_effects))
    r.add("protocol", "workload.report effects declared", not bad,
          f"not in coordinator.workload_effects: {bad}" if bad else f"{len(res.effects)} effect(s) for {len(datas)} report(s)")


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
    the tool installations (fixtures `tools`: {id: path} or {id: {path, version, arch}}, resolved as the agent does and
    checked against the request's version), the settings file (fixtures `settings`)
    and, for egress-allowlist, a real allowlist proxy (egress_proxy). A bootstrap stage's jobs get the bootstrap grants
    instead (spec/sandbox.md, "Bootstrap jobs"): the same proxy, no tools, `{}` as settings, no module data directory.
    Folders: a read folder is the fixtures' `folders` directory (relative to the module), granted read-only; a write
    folder is a fresh empty outbox."""

    def __init__(self, man, fx: dict, root: Path | None = None):
        self.dir = _scratch("conform-grants-")
        from . import toolversion
        given = fx.get("tools") or {}
        self.missing = [t.id for t in man.sandbox.tools if t.id not in given]
        self.tools, self.unmet = {}, []
        for t in man.sandbox.tools:
            if t.id not in given:
                continue
            g = given[t.id] if isinstance(given[t.id], dict) else {"path": given[t.id]}
            inst = {"path": os.path.realpath(g["path"]), "version": g.get("version"), "arch": g.get("arch")}
            if t.version and inst["version"] and not toolversion.satisfies(inst["version"], t.version):
                self.unmet.append(f"{t.id} {inst['version']} (needs {t.version})")
            self.tools[t.id] = [inst]
        (self.dir / "tools.json").write_text(json.dumps(self.tools), encoding="utf-8")
        # the node-scoped settings, as the agent writes them (fixtures `settings` over the declared defaults)
        (self.dir / "settings.json").write_text(json.dumps(fx.get(NODE_SETTINGS, fx.get("settings")) or {}), encoding="utf-8")
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
                             tool_paths=[i["path"] for ps in self.tools.values() for i in ps] + [str(self.dir), sdk],
                             proxy_port=self.proxy.port if self.proxy else None, folders=self.folders)

    def close(self):
        if self.proxy:
            self.proxy.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


GRANTS: dict = {}
SERVICES: dict = {}          # the endpoint services the runner suite started, by name, until it ends


def _service_run(root: Path, man, svc, g: "Grants") -> ServiceRun:
    policy = (lambda data: g.policy(man, root, None, data, kind="service")) if SANDBOX["on"] else None
    return ServiceRun(root, man, svc, g.env(), policy, scratch=SCRATCH.get("dir"))


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
    duty: dict | None = None


def _why(o: Outcome) -> str:
    """Why a run failed, for the report: its exit code, its failure.json's reason, fault and detail when it wrote one,
    and its stderr tail."""
    f = o.failure or {}
    parts = [f"exit {o.code}"]
    if f.get("reason"):
        parts.append(f"failure.json {f['reason']}" + (f" ({f['fault']})" if f.get("fault") else "") +
                     (f": {f['detail']}" if f.get("detail") else ""))
    if o.stderr:
        parts.append(o.stderr)
    return "; ".join(parts)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run(root: Path, man, env_doc: dict, fx: dict, locale: str = "C", stop: bool = False, bootstrap: bool = False,
         checkpoint: bool = False, resume: tuple | None = None, duty: "DutySpec | None" = None) -> Outcome:
    """One runner run. `stop`: a stop request once the runner wrote its first phase (`checkpoint`: checkpoint, then stop,
    within checkpoint_grace_s). `resume`: (a directory holding a checkpoint by name, the envelope's `resume`): the files
    go read-only under <W>/checkpoint/ and the envelope names it."""
    ws = _scratch("conform-")
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
    data = _scratch("conform-data-")
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
        if duty is not None:
            _hold_duty(p, ws, nudge, exited, duty, limit, o, "cancellable" in run.capabilities, run.stop_grace_s)
        elif stop:
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


DUTY_SLACK = 0.1          # the agent holds a unit over its gpu_duty plus this for three samples (D50)
DUTY_SETTLE_S = 1.0       # after the nudge, the runner applies the duty at its next unit


def _tree_cpu_s(pgid: int) -> float | None:
    """CPU seconds the processes of a group have used so far (None where the kit cannot read them)."""
    try:
        if sys.platform.startswith("linux"):
            tick, total = os.sysconf("SC_CLK_TCK"), 0.0
            for d in Path("/proc").iterdir():
                if d.name.isdigit():
                    try:
                        f = (d / "stat").read_text().rsplit(")", 1)[1].split()
                    except (OSError, IndexError):
                        continue
                    if int(f[2]) == pgid:              # fields after the name: state ppid pgrp ...; utime stime at 11, 12
                        total += (int(f[11]) + int(f[12])) / tick
            return total
        if sys.platform == "darwin":
            ps = subprocess.run(["ps", "-A", "-o", "pgid=,time="], capture_output=True, text=True, check=True).stdout
            total = 0.0
            for line in ps.splitlines():
                g, _, t = line.strip().partition(" ")
                if g.isdigit() and int(g) == pgid:
                    parts = [float(x) for x in t.strip().replace("-", ":").split(":")]
                    total += sum(v * m for v, m in zip(reversed(parts), (1, 60, 3600, 86400)))
            return total
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    return None


def _gpu_ns_by_pid() -> dict[int, float] | None:
    """macOS: each process's accumulated GPU time (ns) from the AGX driver's user clients, as the agent reads it
    (undocumented; None where it cannot be read)."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["ioreg", "-r", "-c", "AGXDeviceUserClient", "-d", "1", "-l"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    import re
    got: dict[int, float] = {}
    for entry in out.split("+-o ")[1:]:
        m = re.search(r'"IOUserClientCreator" = "pid (\d+),', entry)
        if m:
            got[int(m.group(1))] = got.get(int(m.group(1)), 0.0) + sum(
                float(x) for x in re.findall(r'"accumulatedGPUTime"=(\d+)', entry))
    return got


def _group_pids(pgid: int) -> list[int]:
    try:
        return _live_members(pgid) if os.name == "posix" else []
    except (OSError, subprocess.CalledProcessError):
        return []


def _hold_duty(p, ws: Path, nudge: "_Nudge", exited: threading.Event, spec: "DutySpec", limit: float, o: Outcome,
               cancellable: bool, grace: float):
    """The agent's gpu_duty, measured: once the runner wrote its first `phase`, write `gpu_duty` into control.json and
    nudge; after DUTY_SETTLE_S, measure the runner's process group for `window_s` (its GPU time where this host shows it,
    its CPU time, its own `gpu_duty` reports); then stop it (a cancellable runner) or let it finish."""
    t0 = time.monotonic()
    while not exited.is_set() and not (ws / "phase").exists() and time.monotonic() - t0 < limit:
        exited.wait(0.005)
    if exited.is_set() or not (ws / "phase").exists():
        o.duty = {"state": "no_phase" if not (ws / "phase").exists() else "finished_first"}
        if not exited.is_set():
            exited.wait(max(0.0, limit - (time.monotonic() - t0)))
        return
    doc = json.loads((ws / "control.json").read_text(encoding="utf-8"))
    tmp = ws / "control.json.tmp"
    tmp.write_text(json.dumps({**doc, "seq": int(doc.get("seq", 0)) + 1, "gpu_duty": spec.duty}), encoding="utf-8")
    os.replace(tmp, ws / "control.json")
    nudge.send(p)
    exited.wait(DUTY_SETTLE_S)
    pids = _group_pids(p.pid)
    cpu0, gpu0, a = _tree_cpu_s(p.pid), _gpu_ns_by_pid(), time.monotonic()
    exited.wait(spec.window_s)
    pids = sorted(set(pids) | set(_group_pids(p.pid)))
    cpu1, gpu1, b = _tree_cpu_s(p.pid), _gpu_ns_by_pid(), time.monotonic()
    span = b - a
    gpu_s = None
    if gpu0 is not None and gpu1 is not None:
        gpu_s = sum(max(0.0, gpu1.get(x, 0.0) - gpu0.get(x, 0.0)) for x in pids) / 1e9
    o.duty = {"state": "finished_first" if exited.is_set() else "measured", "span_s": span,
              "cpu": (cpu1 - cpu0) / span if cpu0 is not None and cpu1 is not None and span > 0 else None,
              "gpu": gpu_s / span if gpu_s is not None and span > 0 else None, "since": time.time() - span}
    if not exited.is_set():
        if cancellable:
            t = _request_stop(p, ws, nudge)
            exited.wait(grace)
            o.stop = "stopped" if exited.is_set() else "ignored"
            o.exit_s = time.monotonic() - t if exited.is_set() else None
        else:
            exited.wait(max(0.0, limit - (time.monotonic() - t0)))
    if not exited.is_set():
        _kill_tree(p)


def job_env(man, ws: Path, data: Path, locale: str = "C.UTF-8") -> dict:
    """The runner environment the agent passes (spec/runner-protocol.md and spec/platforms.md), for this host's OS."""
    return {"OARBANK_WORKDIR": str(ws), "OARBANK_TMP": str(ws / "tmp"), "OARBANK_MODULE_DATA": str(data),
            "OARBANK_MODULE": man.module.id.rsplit(".", 1)[-1], "OARBANK_ATTEMPT_ID": "1", "OARBANK_PROTOCOL": "1",
            "OARBANK_PLATFORM": portable.host_platform(), **portable.os_env(ws, ws / "tmp", locale)}


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
    out = _scratch("conform-ckpt-")
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
        check_listeners(root, man, fx, r, g)
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
    if g.unmet:
        r.add("runner", "host tool versions", False, f"fixtures `tools` give versions the module does not accept: "
              f"{', '.join(g.unmet)} (a node grants only an installation that satisfies [sandbox].tools[].version)")
    data = _scratch("conform-data-")
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
    need = man.runner.for_platform(host).gpu
    if need.use != "none" and need.apis_any and not need.in_container:
        why = gpu_shortfall(man, None)
        r.add("gpu", "this host provides a GPU API the runner names", None if why else True,
              why or f"needs one of {', '.join(need.apis_any)}; this host provides {', '.join(host_gpu_apis())}")
    _check_goldens(root, man, fx, runs, r)
    _check_runner_specs(root, man, fx, r)
    _check_duty_specs(root, man, fx, r)


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
            short = gpu_shortfall(man, st.get("stage"))
            if short:
                r.add("runner", label, None, f"not run here: {short}")
                continue
            ran = True
            o = _run(root, man, env_doc, fx)
            _egress(r, label, o)
            _secret_leaks(r, label, man, o)
            res = o.result
            if not r.add("runner", f"{label}: result envelope", res is not None, _why(o)):
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
        short = gpu_shortfall(man, stage.name if stage else None)
        if short:
            r.add("runner", label, None, f"not run here: {short}")
            continue
        sent = None if stage is None or stage.name == man.default_stage() else stage.name     # as the host sends it
        env_doc = _spec_envelope(man, sent, spec.payload, spec.datasets, spec.mounts, 1,
                                 job_key(man.module.id, man.module.compat, spec.payload, sent))
        boot = stage is not None and stage.bootstrap
        o = _run(root, man, env_doc, fx, bootstrap=boot)
        _egress(r, label, o)
        _secret_leaks(r, label, man, o)
        r.add("runner", f"{label}: exit {spec.expect.exit}", o.code == spec.expect.exit, _why(o))
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


class DutySpec(StrictContract):
    """A conformance fixture's GPU duty check (`duty_specs`): a task the runner runs while the kit asks it, as the agent
    does beside a protected GPU program, to hold `duty`."""
    name: str = Field(min_length=1, max_length=80)
    stage: Name | None = None
    payload: dict
    datasets: list[str] = Field(default_factory=list)
    mounts: dict[str, str] = Field(default_factory=dict)
    duty: float = Field(0.3, gt=0, lt=1, description="The gpu_duty the kit writes once the runner has named a phase.")
    window_s: float = Field(4.0, ge=1, le=60, description="How long it measures, after a settling second.")


def _check_duty_specs(root: Path, man, fx: dict, r: Report):
    """`cooperative_throttle` runners: every `duty_specs` entry runs as a golden does, and once the runner has written its
    first `phase` the kit writes `gpu_duty` into control.json and nudges it, as the agent does beside a protected GPU
    program. Then it measures: the runner reports the duty it held (`gpu_duty` metric events, oarbank_sdk.duty) at most
    the duty plus 0.1, and so does what the kit measures itself: the runner's GPU time where this host shows it (macOS),
    else its CPU time per thread when its reports say its units run on the CPU (a CPU stand-in backend). A GPU runner
    with no duty spec is a warning: the agent measures its GPU time and stops a runner that does not hold its duty."""
    run = man.runner.for_platform(portable.host_platform())
    specs = fx.get("duty_specs") or []
    if "cooperative_throttle" not in run.capabilities:
        if specs:
            r.add("runner", "gpu_duty", False, "duty_specs need a runner that declares cooperative_throttle")
        return
    if not specs:
        if run.gpu.use != "none":
            r.warn("runner", "gpu_duty", "a cooperative_throttle GPU runner without duty_specs in conformance.json: the kit "
                   "cannot show that it holds gpu_duty (the agent measures its GPU time beside protected GPU work and "
                   "stops a runner that does not)")
        return
    for i, raw in enumerate(specs):
        try:
            spec = DutySpec.model_validate(raw)
        except ValidationError as e:
            r.add("runner", f"duty spec #{i}: fixture", False, str(e).replace("\n", " ")[:300])
            continue
        label = f"duty spec {spec.name} (gpu_duty {spec.duty:g})"
        stage = man.stage(spec.stage) if spec.stage else man.stage(man.default_stage() or "")
        if spec.stage and stage is None:
            r.add("runner", f"{label}: fixture", False, f"stage {spec.stage!r} is not a declared stage")
            continue
        short = gpu_shortfall(man, stage.name if stage else None)
        if short:
            r.add("runner", label, None, f"not run here: {short}")
            continue
        sent = None if stage is None or stage.name == man.default_stage() else stage.name
        env_doc = _spec_envelope(man, sent, spec.payload, spec.datasets, spec.mounts, 1,
                                 job_key(man.module.id, man.module.compat, spec.payload, sent))
        o = _run(root, man, env_doc, fx, duty=spec)
        _egress(r, label, o)
        _check_duty(r, label, o, spec)


def _check_duty(r: Report, label: str, o: Outcome, spec: DutySpec):
    d = o.duty or {}
    if d.get("state") == "no_phase":
        r.add("runner", f"{label}: holds its duty", None, "the runner wrote no <W>/phase: the kit writes gpu_duty once work "
              "is underway (Control.phase)")
        return
    if d.get("state") == "finished_first":
        r.add("runner", f"{label}: holds its duty", None, "the task finished before the kit could measure it; make the duty "
              f"spec's task run longer than {DUTY_SETTLE_S + spec.window_s:g} s")
        return
    cap = spec.duty + DUTY_SLACK
    reports = [e for e in o.events if e.get("kind") == "metric" and e.get("name") == "gpu_duty"
               and (e.get("data") or {}).get("asked") == spec.duty and isinstance(e.get("value"), (int, float))]
    r.add("runner", f"{label}: reports the duty it holds", bool(reports),
          "" if reports else "no `gpu_duty` metric events at the asked duty (oarbank_sdk.duty.Duty with the events file)")
    if reports:
        worst = max(e["value"] for e in reports[1:] or reports)
        r.add("runner", f"{label}: its reports stay within the duty", worst <= cap, f"reported up to {worst:.3f}, at most {cap:.2f}")
    devices = {(e.get("data") or {}).get("device") for e in reports}
    threads = max([int((e.get("data") or {}).get("threads") or 1) for e in reports] or [1])
    if d.get("gpu"):
        r.add("runner", f"{label}: its measured GPU time stays within the duty", d["gpu"] <= cap,
              f"busy {d['gpu']:.3f} of {d['span_s']:.1f} s (AGX GPU time), at most {cap:.2f}")
    elif devices == {"cpu"} and d.get("cpu") is not None:
        per = d["cpu"] / max(1, threads)
        r.add("runner", f"{label}: its measured time stays within the duty", per <= cap,
              f"CPU {per:.3f} per thread over {d['span_s']:.1f} s (its units run on the CPU), at most {cap:.2f}")
    else:
        r.add("runner", f"{label}: its measured GPU time stays within the duty", None,
              "this host shows no GPU time for the runner (no GPU, or not macOS): only its own reports are checked")


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
        if not svc.endpoint or svc.listeners or not pf.matches(host, svc.platforms):
            continue                                        # a service with listeners runs in the listener suite
        label = f"service {svc.name}"
        if svc.gpu.use != "none" and not gpu.fits(svc.gpu.apis_any, host_gpu_apis()):
            r.add("service", label, None, f"not run here: it needs one of {', '.join(svc.gpu.apis_any)} on the host; this host "
                  f"provides {', '.join(host_gpu_apis()) or 'no GPU API'}, so an agent here would not offer it")
            continue
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


# ---------------------------------------------------------------------------- workloads

WORKLOAD_SETTLE_S = 3.0        # a workload is still running this long after its start
WORKLOAD_REACTION_S = 3.0      # it shows a cooperative pause (and its end) this soon after the nudge
TOKEN_ENV = "OARBANK_CONFORMANCE_TOKEN"      # a value unique to one run: every process carrying it belongs to the run


def _until(fn, timeout: float, every: float = 0.1):
    """fn's first truthy value within `timeout`, else None (fn is tried at least once)."""
    t = time.monotonic()
    while True:
        v = fn()
        if v or time.monotonic() - t >= timeout:
            return v or None
        time.sleep(every)


def _ps(extra: list[str], fields: str) -> list[list[str]]:
    out = subprocess.run(["ps", "-A", *extra, "-o", fields], capture_output=True, text=True, errors="replace").stdout
    return [line.split(None, 3) for line in out.splitlines() if line.strip()]


def _group_members(pgid: int) -> list[tuple[int, str]]:
    """The live processes of a POSIX process group: (pid, ps state)."""
    return [(int(f[0]), f[2]) for f in _ps([], "pid=,pgid=,stat=")
            if len(f) >= 3 and f[1].isdigit() and int(f[1]) == pgid and not f[2].startswith("Z")]


def _tokened(token: str) -> list[tuple[int, int]] | None:
    """Every live process whose environment carries the run's token: (pid, pgid). Linux reads /proc/<pid>/environ, macOS
    `ps -E` (both work without privileges for the account's own processes); None where the kit cannot look (Windows,
    where the Job Object contains the run instead)."""
    needle = f"{TOKEN_ENV}={token}"
    if sys.platform.startswith("linux"):
        out = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                env = Path(f"/proc/{d}/environ").read_bytes().split(b"\0")
                if needle.encode() not in env:
                    continue
                stat = Path(f"/proc/{d}/stat").read_text()
            except OSError:
                continue
            f = stat[stat.rfind(")") + 2:].split()                # state ppid pgrp ...
            if f and f[0] != "Z":
                out.append((int(d), int(f[2])))
        return out
    if sys.platform == "darwin":
        return [(int(f[0]), int(f[1])) for f in _ps(["-E", "-ww"], "pid=,pgid=,stat=,command=")
                if len(f) == 4 and f[0].isdigit() and not f[2].startswith("Z") and needle in f[3].split()]
    return None


class _WinJob:
    """A Job Object for one workload run on Windows (the agent's container there): breakaway refused, killed on close."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.k32 = ctypes, ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.job = self.k32.CreateJobObjectW(None, None)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IoInfo", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = Extended()
        info.Basic.LimitFlags = 0x2000                                 # KILL_ON_JOB_CLOSE; no BREAKAWAY_OK
        self.k32.SetInformationJobObject(wintypes.HANDLE(self.job), 9, ctypes.byref(info), ctypes.sizeof(info))

    def assign(self, process_handle: int):
        from ctypes import wintypes
        if not self.k32.AssignProcessToJobObject(wintypes.HANDLE(self.job), wintypes.HANDLE(int(process_handle))):
            raise OSError(self.ct.get_last_error(), "AssignProcessToJobObject failed")

    def pids(self) -> list[int]:
        ct = self.ct
        from ctypes import wintypes

        class Ids(ct.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("Listed", wintypes.DWORD), ("Ids", ct.c_size_t * 1024)]
        ids = Ids()
        if not self.k32.QueryInformationJobObject(wintypes.HANDLE(self.job), 3, ct.byref(ids), ct.sizeof(ids), None):
            return []
        return [int(ids.Ids[i]) for i in range(ids.Listed)]

    def terminate(self):
        from ctypes import wintypes
        self.k32.TerminateJobObject(wintypes.HANDLE(self.job), 1)

    def close(self):
        from ctypes import wintypes
        if self.job:
            self.k32.CloseHandle(wintypes.HANDLE(self.job))
            self.job = None


class _WorkloadRun:
    """One workload run as an agent starts it (spec/workload-protocol.md): a run directory inside a fresh module data
    directory, the private directory (control.json `{"seq": 0}`, settings, tools, limits and, for a workload that lists
    secrets, secrets.json 0600), the protocol's environment plus a unique token, and `<exec> run` as the leader of a new
    process group (a Job Object the kit creates on Windows), started with SIGUSR1 ignored and nudged after every change."""

    def __init__(self, root: Path, man, fx: dict, w, g: "Grants"):
        self.root, self.man, self.w, self.g = root, man, w, g
        self.base = _scratch("conform-wl-")
        self.data = self.base / "data"
        self.run = self.data / "workloads" / w.name / "run-1"
        (self.run / "tmp").mkdir(parents=True)
        self.private = self.base / "private"
        self.private.mkdir(mode=0o700)
        self.seq, self.doc = 0, {"seq": 0}
        self._write_control()
        files = {"settings.json": fx.get(NODE_SETTINGS, fx.get("settings")) or {}, "tools.json": g.tools, "limits.json": {}}
        for name, doc in files.items():
            (self.private / name).write_text(json.dumps(doc), encoding="utf-8")
        every = fx.get(CANARIES) or {}
        self.secrets = {n: every[n] for n in w.secrets if n in every}
        self.token = os.urandom(16).hex()
        short = man.module.id.rsplit(".", 1)[-1]
        self.env = {**portable.os_env(self.run, self.run / "tmp"), **{k: v for k, v in g.env().items() if "PROXY" in k.upper()},
                    "OARBANK_WORKLOAD": w.name, "OARBANK_MODULE": short, "OARBANK_NODE_ID": "conform",
                    "OARBANK_PLATFORM": portable.host_platform(), "OARBANK_WORKLOAD_RUN": str(self.run),
                    "OARBANK_MODULE_DATA": str(self.data), "OARBANK_CONTROL_FILE": str(self.private / "control.json"),
                    "OARBANK_EVENTS_FILE": str(self.run / "events.ndjson"),
                    "OARBANK_SETTINGS_FILE": str(self.private / "settings.json"),
                    "OARBANK_TOOLS_FILE": str(self.private / "tools.json"),
                    "OARBANK_LIMITS_FILE": str(self.private / "limits.json"), TOKEN_ENV: self.token}
        if self.secrets:
            fd = os.open(self.private / "secrets.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.secrets, f)
            self.env["OARBANK_SECRETS_FILE"] = str(self.private / "secrets.json")
        if w.identity == "per_node":
            seat = hashlib.sha256(f"{man.module.id}/{w.name}/{self.token}".encode()).hexdigest()[:16]
            self.env.update({"OARBANK_SEAT_ID": f"s_{seat}", "OARBANK_SEAT_FENCE": "1"})
        self.nudge = _Nudge()
        self.env.update(self.nudge.env())
        self.host = EH.ServiceHost() if w.listeners else None     # its endpoint channel: inbound connections arrive on it
        if self.host:
            self.env.update(self.host.env)
        self.p = None
        self.job = None
        self.log = self.base / "workload.log"

    def _write_control(self):
        tmp = self.private / "control.json.tmp"
        tmp.write_text(json.dumps(self.doc), encoding="utf-8")
        os.replace(tmp, self.private / "control.json")

    def _launch(self, op: str) -> list[str]:
        argv = _argv(self.w.exec, self.root) + [op]
        if SANDBOX["on"]:
            import dataclasses
            from . import sandbox as S
            sdk = str(Path(__file__).resolve().parents[1])
            pol = S.node_policy(self.man.module.id, self.root, None, self.data, sandbox=self.man.sandbox, kind="workload",
                                tool_paths=[i["path"] for ps in self.g.tools.values() for i in ps] + [sdk],
                                proxy_port=self.g.proxy.port if self.g.proxy else None)
            text, params = S.render(dataclasses.replace(pol, rd=[str(self.private)]))
            argv = S.launch_argv(S.write_profile(text, self.base / f"{op}.sb"), params, argv)
        return argv

    def start(self) -> str | None:
        kwargs = _merged(self.nudge.popen_kwargs(), self.host.popen_kwargs if self.host else {})
        with open(self.log, "wb") as out:
            try:
                if os.name == "nt":
                    self.job = _WinJob()
                    self.p = subprocess.Popen(self.nudge.argv(self._launch("run")), cwd=self.run, env=self.env,
                                              stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                              creationflags=0x4 | subprocess.CREATE_NEW_PROCESS_GROUP, **kwargs)
                    self.job.assign(self.p._handle)                    # before its first instruction: CREATE_SUSPENDED
                    from .workload import _resume
                    _resume(self.p.pid)
                else:
                    self.p = subprocess.Popen(self.nudge.argv(self._launch("run")), cwd=self.run, env=self.env,
                                              stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
                                              **kwargs)
            except OSError as e:
                return f"{type(e).__name__}: {e}"
            finally:
                if self.host:
                    self.host.spawned()
        return None

    def wait_exit(self, timeout: float):
        try:
            return self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def log_tail(self) -> str:
        try:
            return self.log.read_text(encoding="utf-8", errors="replace")[-400:].strip()
        except OSError:
            return ""

    def control(self, **change) -> float:
        """Replace the control document with a newer seq and nudge the leader; returns when (monotonic)."""
        self.seq += 1
        self.doc = {**self.doc, **change, "seq": self.seq}
        self._write_control()
        t = time.monotonic()
        self.nudge.send(self.p)
        return t

    def status_op(self) -> tuple[object, dict | None, str]:
        """`<exec> status` as the agent runs it: (exit code or "timeout", its last JSON line, its stderr tail)."""
        t = self.w.health.timeout_s if self.w.health else 5.0
        try:
            p = subprocess.run(self._launch("status"), cwd=self.run, env={k: v for k, v in self.env.items()
                                                                           if k != "OARBANK_CONTROL_EVENT"},
                               capture_output=True, text=True, timeout=t, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return "timeout", None, ""
        doc = None
        for line in reversed(p.stdout.strip().splitlines()):
            try:
                doc = json.loads(line)
                break
            except ValueError:
                continue
        return p.returncode, doc, p.stderr[-300:]

    def pids(self) -> list[int]:
        """The run's processes: its process group and every process carrying its token (POSIX), the job's (Windows)."""
        if os.name == "nt":
            return self.job.pids() if self.job else []
        return sorted({p for p, _ in _group_members(self.p.pid)} | {p for p, _ in (_tokened(self.token) or [])})

    def escaped(self) -> list[str] | None:
        """The run's processes outside the leader's process group, described; None when the kit cannot see the token
        (then the check cannot be made)."""
        if os.name == "nt":
            return []
        found = _tokened(self.token)
        if not found or self.p.pid not in {p for p, _ in found}:
            return None
        return [f"process {p} in process group {g}" for p, g in found if g != self.p.pid]

    def paused_state(self) -> bool | None:
        """Whether the workload shows a cooperative pause: its supervisor's status file, else its last `paused` or
        `resumed` progress event, else its status op."""
        doc = _read_json(self.run / ".supervisor.json")
        if isinstance(doc, dict) and "paused" in doc:
            return bool(doc["paused"])
        for e in reversed(_events(self.run / "events.ndjson")):
            if e.get("type") == "progress" and e.get("event") in ("paused", "resumed"):
                return e["event"] == "paused"
        if self.w.health:
            code, doc, _ = self.status_op()
            if code == 0 and isinstance(doc, dict) and doc.get("paused") is not None:
                return bool(doc["paused"])
        return None

    def cleanup(self):
        """End whatever is left of the run: its group, every process carrying its token, its job."""
        if self.p is not None:
            if os.name == "nt":
                if self.job:
                    self.job.terminate()
                    self.job.close()
            else:
                try:
                    os.killpg(self.p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                for pid, _ in _tokened(self.token) or []:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            try:
                self.p.kill()
                self.p.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.nudge.close()
        if self.host:
            self.host.close()


def _freeze(run: "_WorkloadRun", on: bool):
    """The agent's freeze of the whole container: SIGSTOP or SIGCONT to the process group (every job member suspended or
    resumed on Windows)."""
    if os.name == "nt":
        from .workload import _resume, _suspend
        for pid in run.job.pids():
            (_suspend if on else _resume)(pid)
        return
    try:
        os.killpg(run.p.pid, signal.SIGSTOP if on else signal.SIGCONT)
    except (ProcessLookupError, PermissionError):
        pass


def check_workloads(root: Path, man, fx: dict, r: Report):
    """The workload suite (spec/workload-protocol.md): each workload on this host's platform, run as an agent runs it."""
    host = portable.host_platform()
    todo = [w for w in man.workloads if pf.matches(host, w.platforms)]
    if not todo:
        return
    g = Grants(man, fx, root)
    try:
        for w in todo:
            _check_workload(root, man, fx, w, g, r)
    finally:
        g.close()


def _check_workload(root: Path, man, fx: dict, w, g: "Grants", r: Report):
    from ._service_run import listening
    from .workload_protocol import Event, Status
    label = f"workload {w.name}"
    if w.gpu.use != "none" and not gpu.fits(w.gpu.apis_any, host_gpu_apis()):
        r.add("workload", label, None, f"not run here: it needs one of {', '.join(w.gpu.apis_any)} on the host; this host "
              f"provides {', '.join(host_gpu_apis()) or 'no GPU API'}, so an agent here would not offer it")
        return
    run = _WorkloadRun(root, man, fx, w, g)
    try:
        why = run.start()
        if why:
            r.add("workload", f"{label}: starts", False, why)
            return
        code = run.wait_exit(WORKLOAD_SETTLE_S)
        if code is not None:
            tail = run.log_tail()
            r.add("workload", f"{label}: keeps running after it starts", False,
                  f"exited {code} within {WORKLOAD_SETTLE_S:g} s (when its leader exits, the run has ended): {tail}")
            if any(x in tail.lower() for x in ("permission", "not permitted")) and any(x in tail for x in ("bind", "listen")):
                r.add("workload", f"{label}: never listens", False, f"the sandbox refused it a listening socket: {tail}")
            return
        r.add("workload", f"{label}: keeps running after it starts", True, f"still running {WORKLOAD_SETTLE_S:g} s after `run`")
        escaped, blind = [], False
        listens = listening(run.pids())

        def look():
            nonlocal blind
            e = run.escaped()
            if e is None:
                blind = True
            else:
                escaped.extend(x for x in e if x not in escaped)
        look()
        if w.health:
            got, last = None, None
            for _ in range(3):
                code, doc, err = run.status_op()
                try:
                    st = Status.model_validate(doc) if code == 0 and isinstance(doc, dict) else None
                except ValidationError:
                    st = None
                last = (code, doc, err)
                if st is not None and st.healthy:
                    got = st
                    break
                time.sleep(1.0)
            code, doc, err = last
            r.add("workload", f"{label}: `status` answers healthy within health.timeout_s", got is not None,
                  got.detail if got else (f"timed out after {w.health.timeout_s:g} s" if code == "timeout" else
                                          f"exit {code}: {json.dumps(doc)[:200] if doc is not None else err or 'no JSON'}"))
        if "freeze_ok" in w.control:
            _freeze(run, True)
            if os.name == "nt":
                frozen, detail = True, "every member of its Job Object suspended"
            else:
                members = _until(lambda: (lambda m: m if m and all(s.startswith("T") for _, s in m) else None)(
                    _group_members(run.p.pid)), WORKLOAD_REACTION_S)
                frozen = members is not None
                detail = f"{len(members)} process(es) stopped" if members else "its process group did not stop"
            look()
            _freeze(run, False)
            alive = run.wait_exit(1.0) is None
            r.add("workload", f"{label}: survives a freeze of its process container (freeze_ok)", frozen and alive,
                  detail if alive else f"it exited {run.p.returncode} after the freeze ended: {run.log_tail()}")
        if "cooperative_pause" in w.control and run.p.poll() is None:
            run.control(pause=True, reason="conformance")
            on = _until(lambda: run.paused_state() is True, WORKLOAD_REACTION_S, 0.2)
            look()
            r.add("workload", f"{label}: shows a cooperative pause within {WORKLOAD_REACTION_S:g} s", bool(on),
                  "" if on else "nothing said paused: its .supervisor.json, a `paused` progress event or its status op")
            run.control(pause=False, reason=None)
            off = _until(lambda: run.paused_state() is False, WORKLOAD_REACTION_S, 0.2)
            r.add("workload", f"{label}: resumes within {WORKLOAD_REACTION_S:g} s", bool(off) and run.p.poll() is None,
                  "" if off else "it still shows paused after the pause was cleared")
        look()
        listens += listening(run.pids())
        if os.name == "nt":
            r.add("workload", f"{label}: stays in its process container", True,
                  "the kit runs it in a Job Object that refuses breakaway, as the agent does")
        elif blind:
            r.add("workload", f"{label}: stays in its process container", None,
                  f"the kit could not read the environment of the run's processes here (it looks for {TOKEN_ENV})")
        else:
            r.add("workload", f"{label}: stays in its process container", not escaped,
                  "every process carrying the run's token is in the leader's process group" if not escaped else
                  f"left the leader's process group (a daemon that calls setsid and forks): {'; '.join(escaped)[:400]}")
        r.add("workload", f"{label}: never listens", not listens,
              "; ".join(sorted(set(listens)))[:600] or "no listening socket while it ran")
        events = _events(run.run / "events.ndjson")
        bad = []
        for i, e in enumerate(events):
            try:
                Event.model_validate(e)
            except ValidationError as x:
                bad.append(f"line {i + 1}: {str(x).splitlines()[0]}")
        r.add("workload", f"{label}: its events are workload-event-1 lines", not bad,
              "; ".join(bad[:3]) if bad else f"{len(events)} event(s)")
        if w.secrets:
            files = [f.read_bytes() for f in run.data.rglob("*") if f.is_file()]
            leaked = _leaked([_read_bytes(run.log), *files], run.secrets)
            r.add("workload", f"{label}: no secret in its log, events or files", not leaked,
                  f"{leaked} appear in what it wrote" if leaked else "")
        if run.p.poll() is None:
            t = run.control(stop=True, reason="conformance")
            code = run.wait_exit(w.stop_timeout_s)
            dt = time.monotonic() - t
            r.add("workload", f"{label}: a stop ends it within stop_timeout_s", code is not None,
                  f"exit {code}, {dt:.1f} s after the nudge" if code is not None else
                  f"still running {w.stop_timeout_s:g} s after the stop request; the agent kills its container then")
        else:
            r.add("workload", f"{label}: a stop ends it within stop_timeout_s", False,
                  f"it had already exited {run.p.returncode} before the stop: {run.log_tail()}")
    finally:
        run.cleanup()


def conform_workloads(root, fixtures: dict | None = None, sandbox: bool | None = None) -> Report:
    """Only the workload suite (with the manifest it needs): what `oarbank-sdk conform --only workload` runs.
    `sandbox`: run the workload under the module sandbox (default: on macOS) for this call only."""
    before = SANDBOX["on"]
    if sandbox is not None:
        SANDBOX["on"] = sandbox and sys.platform == "darwin"
    outer = SCRATCH.get("dir")
    try:
        with tempfile.TemporaryDirectory(prefix="conform-run-", ignore_cleanup_errors=True) as td:
            SCRATCH["dir"] = td
            return _conform_workloads(Path(root).resolve(), fixtures)
    finally:
        SANDBOX["on"] = before
        if outer is None:
            SCRATCH.pop("dir", None)
        else:
            SCRATCH["dir"] = outer


def _conform_workloads(root: Path, fixtures: dict | None) -> Report:
    fx = fixtures if fixtures is not None else (json.loads((root / "conformance.json").read_text(encoding="utf-8"))
                                                if (root / "conformance.json").exists() else {})
    r = Report()
    HOST_GPU.clear()
    try:
        man = mf.load(root / B.MANIFEST_FILE)
    except (ValidationError, OSError, ValueError) as e:
        r.add("manifest", "schema", False, str(e).splitlines()[0])
        return r
    r.module = f"{man.module.id} {man.module.version}"
    fx = {**fx, CANARIES: canaries(man, fx), **check_settings(root, man, fx, r)}
    if not man.workloads:
        r.add("workload", "workloads", None, "the module declares no [[workloads]]")
    check_workloads(root, man, fx, r)
    return r


class ListenerSpecExpect(StrictContract):
    status: int | None = None
    contains: str | None = None


class ListenerSpec(StrictContract):
    """A conformance fixture's client of an inbound listener (`listener_specs`): raw bytes (`send`, UTF-8, read until
    the answer contains `expect.contains` or the service closes) or, with `path`, one HTTP/1.1 request (`method`,
    default GET; `expect.status`, default 200), sent through a connection the kit hands the service as the agent would."""
    name: str = Field(min_length=1, max_length=80)
    listener: Name
    send: str | None = None
    method: str = "GET"
    path: str | None = None
    body: dict | list | None = None
    expect: ListenerSpecExpect = Field(default_factory=ListenerSpecExpect)


KIT_PEER = "198.51.100.23:51000"            # the client every listener connection the kit hands over comes from
KIT_ACCEPTED_ON = "192.0.2.10:41000"        # the address the kit's listener accepted on (a PROXY v2 destination)


def kit_announcement(x) -> dict:
    """The announcement the kit sends for a listener after hello: documentation addresses at its `port_hint` (else
    41000), state `reachable`."""
    port = x.port_hint or 41000
    return {"external": f"203.0.113.7:{port}", "external_v6": f"[2001:db8::7]:{port}", "state": "reachable"}


def _read_until(conn, want: str | None, secs: float = 10) -> bytes:
    conn.settimeout(secs)
    got = b""
    deadline = time.monotonic() + secs
    wait = getattr(conn, "wait_readable", None)            # a Windows pipe: settimeout does nothing there
    while time.monotonic() < deadline and len(got) < 65536:
        if wait is not None and not wait(deadline - time.monotonic()):
            break
        try:
            chunk = conn.recv(4096)
        except (TimeoutError, OSError):
            break
        if not chunk:
            break
        got += chunk
        if want is not None and want.encode() in got:
            break
    return got


def _listener_spec(conn, spec: "ListenerSpec") -> tuple[bool, str]:
    if spec.path is not None:
        status, body = _request(conn, spec.method, spec.path, spec.body)
        want = 200 if spec.expect.status is None else spec.expect.status
        ok = status == want and (spec.expect.contains is None or spec.expect.contains.encode() in body)
        return ok, f"status {status}: {body[:200]!r}"
    if spec.send:
        conn.sendall(spec.send.encode())
    got = _read_until(conn, spec.expect.contains)
    conn.close()
    ok = spec.expect.contains is None or spec.expect.contains.encode() in got
    return ok, f"answered {got[:200]!r}" + ("" if ok else f", without {spec.expect.contains!r}")


def check_listeners(root: Path, man, fx: dict, r: Report, g: "Grants"):
    """The listener suite: each endpoint service that lists inbound listeners, run as an agent runs it with them
    (spec/service-protocol.md, "Listeners")."""
    host = portable.host_platform()
    specs, sspecs = [], []
    for i, raw in enumerate(fx.get("listener_specs") or []):
        try:
            specs.append(ListenerSpec.model_validate(raw))
        except ValidationError as e:
            r.add("listener", f"listener spec #{i}: fixture", False, str(e).replace("\n", " ")[:300])
    for raw in fx.get("service_specs") or []:
        try:
            sspecs.append(ServiceSpec.model_validate(raw))
        except ValidationError:
            pass                                            # the service suite reports it
    declared = {x.name for x in man.sandbox.net.inbound}
    for spec in specs:
        if spec.listener not in declared:
            r.add("listener", f"listener spec {spec.name}: fixture", False, f"{spec.listener!r} is no inbound listener")
    for svc in man.services:
        if not svc.listeners or not pf.matches(host, svc.platforms):
            continue
        label = f"service {svc.name}"
        if svc.gpu.use != "none" and not gpu.fits(svc.gpu.apis_any, host_gpu_apis()):
            r.add("listener", label, None, f"not run here: it needs one of {', '.join(svc.gpu.apis_any)} on the host")
            continue
        _check_listener_service(root, man, svc, specs, sspecs, r, g)


def _check_listener_service(root: Path, man, svc, specs: list, sspecs: list, r: Report, g: "Grants"):
    label = f"service {svc.name}"
    listeners = [man.sandbox.net.listener(n) for n in svc.listeners]
    fds = {}
    for x in listeners:
        if x.handover != "listen_fd":
            continue
        if os.name == "nt":
            r.add("listener", f"{label}: listener {x.name}", None, "handover listen_fd: Windows nodes refuse this listener "
                  "(LISTENER_HANDOVER_UNSUPPORTED), so the kit does not run it here")
            continue
        fds[x.name] = EH.ListenFdHost(x.name)
    run = _service_run(root, man, svc, g)
    path = g.dir / f"listeners-{svc.name}.json"
    EH.write_listeners_file(path, {x.name: {"external": None, "external_v6": None, "state": "unmapped",
                                            "since": time.time()} for x in listeners})
    run.extra_env = {"OARBANK_LISTENERS_FILE": str(path), **{k: v for h in fds.values() for k, v in h.env.items()}}
    run.extra_fds = [h.sock.fileno() for h in fds.values()]
    run.own_sockets = [h.path for h in fds.values()]
    try:
        why = run.start()
        r.add("listener", f"{label}: starts with its listeners, says hello on its endpoint channel and becomes ready",
              why is None, why or "")
        if why:
            if any(w in why.lower() for w in ("permission", "not permitted")) and any(w in why for w in ("bind", "listen")):
                r.add("listener", f"{label}: the service never listens itself", False,
                      f"the sandbox refused it a listening socket: {why}")
            return
        listens = run.listening()
        for x in listeners:
            if x.handover == "listen_fd" and x.name not in fds:
                continue
            ann = kit_announcement(x)
            run.host.announce(x.name, ann["external"], ann["external_v6"], ann["state"])
            proxy = EH.proxy_for(KIT_PEER, KIT_ACCEPTED_ON) if x.proxy() else None
            mine = [s for s in specs if s.listener == x.name]

            def hand():
                if x.name in fds:
                    return None, fds[x.name].connect(KIT_PEER, proxy)
                return run.host.listener_connection(x.name, KIT_PEER, proxy)
            try:
                n, conn = hand()
                ok = True if n is None else run.host.wait_accepted(n, 10)
                r.add("listener", f"{label}: listener {x.name}: takes a connection the agent hands it", ok,
                      "" if ok else "no `accepted` for the connection within 10 s")
                if not mine:
                    conn.close()
                    r.add("listener", f"{label}: listener {x.name}: answers", None,
                          "no `listener_specs` for this listener: the kit checked only that it takes connections")
            except OSError as e:
                r.add("listener", f"{label}: listener {x.name}: takes a connection the agent hands it", False, repr(e))
                continue
            for spec in mine:
                try:
                    _, conn = hand()
                    ok, detail = _listener_spec(conn, spec)
                except (OSError, ValueError) as e:
                    ok, detail = False, repr(e)
                r.add("listener", f"{label}: listener spec {spec.name}", ok, detail)
        for spec in [s for s in sspecs if s.service == svc.name and svc.provides.pools]:
            try:
                status, body = _request(run.connect(attempt=7), spec.method, spec.path, spec.body)
                r.add("listener", f"{label}: service spec {spec.name}", status == spec.expect.status,
                      f"status {status}: {body[:200]!r}")
            except OSError as e:
                r.add("listener", f"{label}: service spec {spec.name}", False, repr(e))
        if run.host.allows:
            bad = [m for m in run.host.allows if m.get("listener") not in svc.listeners or not _cidrs_ok(m.get("cidrs"))]
            r.add("listener", f"{label}: listener_allow names its own listeners and IP networks", not bad,
                  json.dumps(bad[:2])[:300] if bad else f"{len(run.host.allows)} message(s)")
        listens += run.listening()
        r.add("listener", f"{label}: the service never listens itself", not listens,
              "; ".join(sorted(set(listens)))[:600] or "no listening socket of its own after ready and after its "
              "connections")
    finally:
        stopped = run.stop()
        for h in fds.values():
            h.close()
        r.add("listener", f"{label}: stops", stopped is None, stopped or "")


def _cidrs_ok(cidrs) -> bool:
    import ipaddress
    if cidrs is None:
        return True
    if not isinstance(cidrs, list):
        return False
    try:
        for c in cidrs:
            ipaddress.ip_network(c, strict=False)
    except (TypeError, ValueError):
        return False
    return True


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


def conform(root, fixtures: dict | None = None, runner: bool = True, sandbox: bool | None = None,
            workloads: bool = True) -> Report:
    """`sandbox`: run the runner under the module sandbox (default: on macOS). `runner = False` skips every suite that
    runs node processes (runner, gpu, service, listener, workload); `workloads = False` only the workload suite.
    Everything the run writes (workdirs, data directories, grants, checkpoints, sandbox profiles) goes into one scratch
    directory, removed when it returns or raises."""
    outer = SCRATCH.get("dir")
    with tempfile.TemporaryDirectory(prefix="conform-run-", ignore_cleanup_errors=True) as td:
        SCRATCH["dir"] = td
        try:
            return _conform(Path(root).resolve(), fixtures, runner, sandbox, workloads)
        finally:
            if outer is None:
                SCRATCH.pop("dir", None)
            else:
                SCRATCH["dir"] = outer


def _conform(root: Path, fixtures: dict | None, runner: bool, sandbox: bool | None, workloads: bool = True) -> Report:
    if sandbox is not None:
        SANDBOX["on"] = sandbox and sys.platform == "darwin"
    fx = fixtures if fixtures is not None else (json.loads((root / "conformance.json").read_text(encoding="utf-8"))
                                                if (root / "conformance.json").exists() else {})
    r = Report()
    HOST_GPU.clear()
    man = check_manifest(root, r)
    if man is None:
        return r
    check_bundle(root, r)
    fx = {**fx, CANARIES: canaries(man, fx), **check_settings(root, man, fx, r)}
    if man.sandbox.container_sets:
        check_images(root, man, fx, r)
    runs = check_protocol(root, man, fx, r)
    check_ui_suite(root, man, r)
    if runner:
        check_runner(root, man, fx, runs, r)
        if workloads:
            check_workloads(root, man, fx, r)
    return r


def check_ui_suite(root: Path, man, r: Report):
    """The ui suite: every declared view computed from fixtures/ui/inputs.json and validated as oarbankd validates it
    (ui.validate_view, artifact references included); every page and panel rendered with the fixtures and the panel
    context; axe over them when Node with axe-core and jsdom is available."""
    if not (man.ui.pages or man.ui.panels or man.ui.views):
        return
    from .preview import Preview
    try:
        pv = Preview(str(root / B.MANIFEST_FILE), port=0)
    except Exception as e:                                  # noqa: BLE001 (a module that cannot start fails the suite)
        r.add("ui", "coordinator starts for the preview", False, str(e))
        return
    try:
        for vid, problems in pv.view_problems.items():
            r.add("ui", f"view {vid} matches its declaration", not problems, "; ".join(problems[:3]))
        rendered = True
        for d in [*man.ui.pages, *man.ui.panels]:
            try:
                html = pv.page_html(d)
            except Exception as e:                          # noqa: BLE001 - a page that cannot render fails
                r.add("ui", f"{d.slot} {d.id} renders", False, f"{type(e).__name__}: {e}")
                rendered = False
                continue
            r.add("ui", f"{d.slot} {d.id} renders", True)
            if "mod-placeholder" in html:
                r.warn("ui", f"{d.slot} {d.id}", "a component shows a placeholder with the fixtures (add rows or inputs)")
        if rendered:
            found, ran = pv.axe()
            r.add("ui", "accessibility (axe)", None if found is None else not found,
                  ran if found is None else "; ".join(found[:3]) or ran)
    finally:
        pv.close()
