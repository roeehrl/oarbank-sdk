"""The conformance kit: what a module must pass before a host installs it (spec/conformance.md).

    oarbank-sdk conform <module-dir> [--fixtures conformance.json] [--no-runner] [--json]

Suites:

- **manifest**: strict schema, UI contract references, no unknown fields, the lint warnings, and a resolved view per
  declared platform (runner exec, runtime, env and file subset) and per coordinator platform (coordinator exec);
- **bundle**: the module builds into a bundle (no core imports, no symlinks) that verifies;
- **protocol**: the coordinator starts and completes `initialize`; it advertises every capability the
  manifest declares and every required verb; verbs are pure (the same input twice gives the same output);
  `golden.list` works for each node class (by default one per declared platform, with `platform` set), its goldens'
  per-platform keys are declared platforms, and `spec.build` turns every golden into the stage it names;
  `integrity.check` and the move verbs (when advertised) are pure, answer valid results and ask only for the
  effects `coordinator.move.effects` declares;
- **runner**: for the first golden whose datasets are available, the real runner runs the golden's
  SpecEnvelope in a fresh workdir with a clean environment, **under the module sandbox** with the grants its
  manifest declares (spec/sandbox.md), and writes a valid ResultEnvelope, which
  `result.evaluate` accepts and the golden check passes; a second run with another locale gives the same
  digest (`determinism = "exact"`); a control.json stop request, nudged as the agent nudges, ends a running job
  within `STOP_REACTION_S` (`cancellable`);
  `doctor --json` is a valid DoctorOutput whose `attrs.platform`, when present, is OARBANK_PLATFORM. The runner runs
  with this host's runner variant (exec and env), the stage's variant (timeout, resources) and the golden's expected
  value resolved for this host's platform.

Fixtures (optional `conformance.json` next to the manifest) feed the fake host:
`{"datasets": {id: {"kind", "attrs", "dir"?}}, "settings": {...}, "node_classes": [{"platform"?, "pools": {...},
"capabilities": [...]}], "params": [...examples for params.check...], "store": {"<collection>/<key>": doc},
"files": {"<path>": "<text content>"}}`.
A dataset with a `dir` is mounted (copied) into the runner's workdir under the golden spec's mount name.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from . import bundle as B
from . import platform as pf
from . import portable
from . import manifest as mf
from . import module_protocol as mp
from .envelopes import ResultEnvelope, SpecEnvelope
from .keys import job_key
from .runner_protocol import DoctorOutput


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
            return {"datasets": [{"id": i, "kind": d.get("kind", "data"), "attrs": d.get("attrs") or {}} for i, d in rows]}

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

        return {"host.files.list": lambda p: {"files": [entry(k) for k in sorted(files) if k.startswith(p.get("prefix") or "")]},
                "host.files.stat": lambda p: ({"exists": True, "file": entry(p["path"])} if p.get("path") in files
                                              else {"exists": False, "file": None}),
                "host.files.read": files_read,
                "host.datasets.query": datasets_query, "host.settings.get": lambda p: {"value": (self.fx.get("settings") or {}).get(p.get("key"))},
                "host.store.get": store_get, "host.store.query": store_query,
                "host.nodes.query": nodes_query,
                "host.jobs.query": lambda p: {"jobs": []}, "host.blobs.stat": lambda p: {"exists": False, "size": None}}


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
    runs = []
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
                exps = [gm.expected, *gm.expected_by_platform.values()]
                if not (all(e.get("digest") for e in exps) or "golden.compare" in adv):
                    r.add("protocol", f"golden {g['name']}: comparable", False, "expected.digest missing and no golden.compare")
                if good:
                    runs.append((nc, g, st, b1["specs"][0]))
        check_lifecycle_verbs(cli, man, adv, r)
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


def _envelope(man, g, st, built) -> dict:
    key = job_key(man.module.id, man.module.compat, g["key_inputs"], st.get("stage"))
    host = portable.host_platform()
    stage = next((s for s in man.stages if s.name == st.get("stage")), man.stages[0]).for_platform(host)
    return SpecEnvelope.model_validate({
        "envelope": 1, "schema": f"{man.module.id.rsplit('.', 1)[-1]}/spec@{built.get('spec_version') or 1}",
        "module_id": man.module.id, "module_version": man.module.version, "job_key": key, "stage": st.get("stage"),
        "protocol": 1, "datasets": st.get("datasets") or g.get("datasets") or [], "mounts": st.get("mounts") or {},
        "inputs": {}, "resources": {"cpu": stage.requires.resources.cpu, "mem_gb": stage.requires.resources.mem_gb},
        "timeout_s": stage.timeout_s, "platform": host, "payload": st["payload"]}).model_dump(mode="json", by_alias=True)


class Grants:
    """What the agent would give the runner beyond its directories, from the module's [sandbox] and the fixtures:
    the tool paths (fixtures `tools`: {id: path}, resolved as the agent does), the settings file (fixtures `settings`)
    and, for egress-allowlist, a real allowlist proxy (egress_proxy)."""

    def __init__(self, man, fx: dict):
        self.dir = Path(tempfile.mkdtemp(prefix="conform-grants-"))
        want = [t.id for t in man.sandbox.tools]
        given = fx.get("tools") or {}
        self.missing = [t for t in want if t not in given]
        self.tools = {t: [os.path.realpath(given[t])] for t in want if t in given}
        (self.dir / "tools.json").write_text(json.dumps(self.tools), encoding="utf-8")
        (self.dir / "settings.json").write_text(json.dumps(fx.get("settings") or {}), encoding="utf-8")
        self.proxy = None
        if man.sandbox.net.mode == "egress-allowlist":
            from .egress_proxy import AllowlistProxy
            self.proxy = AllowlistProxy(man.sandbox.net.allow).start()

    def env(self) -> dict:
        e = {"OARBANK_TOOLS_FILE": str(self.dir / "tools.json"), "OARBANK_SETTINGS_FILE": str(self.dir / "settings.json")}
        if self.proxy:
            url = f"http://127.0.0.1:{self.proxy.port}"
            e.update({"HTTPS_PROXY": url, "HTTP_PROXY": url, "ALL_PROXY": url, "https_proxy": url, "http_proxy": url,
                      "NO_PROXY": ""})
        return e

    def policy(self, man, root, ws, data, kind="runner"):
        from . import sandbox as S
        # the grants directory (tools.json, settings.json) is readable like the agent's per-job files
        return S.node_policy(man.module.id, root, ws, data, sandbox=man.sandbox, kind=kind,
                             tool_paths=[p for ps in self.tools.values() for p in ps] + [str(self.dir)],
                             proxy_port=self.proxy.port if self.proxy else None)

    def close(self):
        if self.proxy:
            self.proxy.stop()


GRANTS: dict = {}


STOP_REACTION_S = 0.5      # a cancellable runner exits this soon after a nudged stop request


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


def _run(root: Path, man, env_doc: dict, fx: dict, locale: str = "C", kill_after: float | None = None):
    ws = Path(tempfile.mkdtemp(prefix="conform-"))
    for did in env_doc["datasets"]:
        src = (fx.get("datasets") or {}).get(did, {}).get("dir")
        mount = env_doc["mounts"].get(did, did.replace(":", "_"))
        if src:
            shutil.copytree(src, ws / mount)
    (ws / "spec.json").write_text(json.dumps(env_doc), encoding="utf-8", newline="\n")
    (ws / "tmp").mkdir()
    data = Path(tempfile.mkdtemp(prefix="conform-data-"))
    g = GRANTS["g"]
    nudge = _Nudge()
    env = {**job_env(man, ws, data, locale), **man.runner.for_platform(portable.host_platform()).env, **g.env(), **nudge.env()}
    (ws / "control.json").write_text(json.dumps({"seq": 0}), encoding="utf-8")
    argv = _argv(man.runner.for_platform(portable.host_platform()).exec, root)
    argv += ["run", "--spec", str(ws / "spec.json"), "--workdir", str(ws), "--out", str(ws / "result.json")]
    if SANDBOX["on"]:
        from . import sandbox as S
        text, params = S.render(g.policy(man, root, ws, data))
        argv = S.launch_argv(S.write_profile(text, data.parent / f"{data.name}.sb"), params, argv)
    try:
        p = _spawn(nudge.argv(argv), ws, env, nudge.popen_kwargs())
        if kill_after is not None:
            time.sleep(kill_after)
            if p.poll() is None:
                t = _request_stop(p, ws, nudge)        # control.json stop and the nudge, nothing else
                try:
                    p.wait(man.runner.stop_grace_s)
                    return "stopped", p.returncode, time.monotonic() - t
                except subprocess.TimeoutExpired:
                    _kill_tree(p)
                    p.wait()
                    return "ignored_stop", p.returncode, None
            return "finished_first", p.returncode, None
        try:
            _, err = p.communicate(timeout=float(env_doc.get("timeout_s") or 1800))
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            return None, "timeout", ws
    finally:
        nudge.close()
    res = json.loads((ws / "result.json").read_text(encoding="utf-8")) if p.returncode == 0 and (ws / "result.json").exists() else None
    return res, (err.decode(errors="replace")[-400:] if p.returncode else ""), ws


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


def _request_stop(p, ws: Path, nudge: _Nudge) -> float:
    """Replace control.json with a stop request and nudge the runner; returns when (monotonic) it was nudged."""
    doc = json.loads((ws / "control.json").read_text(encoding="utf-8"))
    tmp = ws / "control.json.tmp"
    tmp.write_text(json.dumps({**doc, "seq": int(doc.get("seq", 0)) + 1, "stop": True}), encoding="utf-8")
    os.replace(tmp, ws / "control.json")
    t = time.monotonic()
    nudge.send(p)
    return t


def _kill_tree(p):
    if os.name == "posix":
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        p.kill()


def check_runner(root: Path, man, fx: dict, runs: list, r: Report):
    g = GRANTS["g"] = Grants(man, fx)
    try:
        _check_runner(root, man, fx, runs, r, g)
    finally:
        g.close()


def _needs_broker(man, stage_name) -> bool:
    st = next((s for s in man.stages if s.name == stage_name), None)
    return bool(st and ("containers" in st.requires.pools or "containers" in st.requires.needs_pools))


def _check_runner(root: Path, man, fx: dict, runs: list, r: Report, g: "Grants"):
    if g.missing:
        r.add("runner", "host tools", None, f"fixtures `tools` do not map {g.missing}: the runner may fail without them")
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
            res, err, ws = _run(root, man, env_doc, fx)
            if not r.add("runner", f"{label}: result envelope", res is not None, err):
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
            if man.results.determinism == "exact":
                res2, err2, _ = _run(root, man, env_doc, fx, locale="en_US.UTF-8")
                v2 = cli.call("result.evaluate", {"spec": env_doc, "result": res2, "stage": st.get("stage")}) if res2 else {}
                r.add("runner", f"{label}: deterministic across locales/workdirs", bool(res2) and v2.get("digest") == v.get("digest"),
                      err2)
            if "cancellable" in man.runner.capabilities:
                how, code, took = _run(root, man, env_doc, fx, kill_after=0.3)
                name = f"{label}: a nudged stop ends the job within {STOP_REACTION_S} s"
                if how == "finished_first":
                    r.add("runner", name, None, "the golden finished within 0.3 s; make one golden run longer to prove cancellation")
                elif how == "stopped":
                    r.add("runner", name, code != 0 and took < STOP_REACTION_S, f"exit {code}, {took:.3f} s after the nudge")
                else:
                    r.add("runner", name, False, f"still running {man.runner.stop_grace_s} s after the nudge (stop_grace_s)")
            break                                           # one golden per module is enough for the runner suite
    finally:
        cli.close()
    if not ran:
        r.add("runner", "golden runs", None, "every golden needs datasets the fixtures do not provide")


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
    runs = check_protocol(root, man, fx, r)
    if runner:
        check_runner(root, man, fx, runs, r)
    return r
