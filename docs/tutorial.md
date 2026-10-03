# Build an Oarbank module in a day

This tutorial builds **primes**, a module that counts the primes below `n` on the fleet's nodes. You will
write the manifest, the coordinator side (Python, on `oarbank_sdk`), the runner (anything executable; here
Python on the host's managed runtime), goldens and schemas. Then you pass the conformance kit, build a bundle and
hand it to an operator. [`examples/toy`](../examples/toy) is the complete reference: when in doubt, copy it.

You need Python 3.12 and this SDK (`uv pip install -e path/to/oarbank-sdk` or a path dependency).

## 1. The pieces

| Piece | Runs on | Talks | You write |
|---|---|---|---|
| Manifest | nowhere (read by every host component) | TOML | `oarbank-module.toml` |
| Coordinator side | the coordinator, one long-lived process | module protocol 1 (JSON-RPC over stdio), [spec](../spec/module-protocol.md) | `primes_module.py` using `oarbank_sdk.server.Module` |
| Runner | each node, one process per job | runner protocol 1 (argv, files, exit codes), [spec](../spec/runner-protocol.md) | `primes_runner.py` |
| Goldens | both | a known answer per node class | `goldens/*.json` |
| Schemas | the host | JSON Schema | `schemas/result.schema.json`, `schemas/count.json` |

The host owns all state. The coordinator side is stateless between calls: every verb is a pure function
of its input, plus read-only host callbacks. It changes nothing directly; it returns **effects**
(`campaigns.create`, `jobs.enqueue`, `store.write`, …) that the host checks and applies.

## 2. The manifest

```toml
manifest = 1

[module]
id = "dev.example.primes"          # reverse DNS, never reused
version = "1.0.0"
compat = "primes1"                 # bump when results would differ: every job key changes with it
publisher = "you"
license = "Apache-2.0"
codeowners = ["you"]
stability = "experimental"
description = "Counts primes below n"

[requires]
core = ">=2.1,<3"
platforms = ["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"]   # node platforms the runner supports
module_protocol = [1]
runner_protocol = [1]

[coordinator]
exec = ["python", "-I", "{bundle}/primes_module.py"]
runtime = { kind = "python" }                    # the host's managed CPython, with oarbank_sdk
capabilities = ["op.plan", "op.apply"]           # the optional verbs you implement

[runner]
exec = ["python", "-I", "{bundle}/primes_runner.py"]
runtime = { kind = "python" }
capabilities = ["cancellable", "deterministic_output"]

[[stages]]
name = "run"
timeout_s = 600
requires.resources = { cpu = 1, mem_gb = 0.2 }

[results]
schema = "schemas/result.schema.json"
schema_version = 1
determinism = "exact"
digest = { version = 1, over = ["count"] }
value = { field = "count", direction = "maximize" }
fields = [{ name = "count", type = "integer", ui = { column = "Primes", format = ",d" } }]

[goldens]
fixtures = "goldens/*.json"

[[operations]]
verb = "count"
title = "Count primes"
tier = "T1"
target = "none"
params_schema = "schemas/count.json"
effects = ["campaigns.create", "jobs.enqueue"]
```

`{bundle}` is the module's unpacked bundle; `runtime = { kind = "python" }` runs the `python` token on the host's
managed CPython, which has `oarbank_sdk`. Check the manifest as you go: `oarbank-sdk check oarbank-module.toml`
(typos are errors, and capabilities the agent does not know are warnings). Every field is documented in
[spec/manifest-reference.md](../spec/manifest-reference.md).

The result schema validates the payload your runner writes, and the operation's params schema what an operator
submits:

```json
{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
 "properties": {"count": {"type": "integer", "minimum": 0}}, "required": ["count"]}
```

```json
{"type": "object", "properties": {"ns": {"type": "array", "items": {"type": "integer", "minimum": 2, "maximum": 50000000}, "minItems": 1}},
 "required": ["ns"]}
```

## 3. The coordinator side

Required verbs: `params.check`, `job.plan`, `spec.build`, `result.evaluate` and `golden.list`. Operations
(`op.plan`, `op.apply`) are how an operator's click becomes jobs.

```python
import hashlib
from pathlib import Path

from oarbank_sdk import effects as fx
from oarbank_sdk import goldens
from oarbank_sdk import module_protocol as mp
from oarbank_sdk.keys import job_key
from oarbank_sdk.server import Module

ID, VERSION, COMPAT = "dev.example.primes", "1.0.0", "primes1"
HERE = Path(__file__).parent
module = Module(ID, VERSION)


@module.verb("params.check")
def params_check(p: mp.ParamsCheckParams, ctx):
    n = p.params.get("n")
    ok = isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 50_000_000
    return mp.ParamsCheckResult(ok=ok, normalized_params={"n": n} if ok else None,
                                errors=[] if ok else [mp.Issue(path="/n", message="n: an integer in 2..5e7", code="primes/n_range")])


@module.verb("job.plan")
def job_plan(p: mp.JobPlanParams, ctx):
    return mp.JobPlanResult(jobs=[mp.PlanItem(key_inputs={"n": p.params["n"]}, stages=["run"])])


@module.verb("spec.build")
def spec_build(p: mp.SpecBuildParams, ctx):
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1,
        stages=[mp.StageSpec(stage="run", payload={"n": j.key_inputs["n"]})]) for j in p.jobs])


def digest(count: int) -> str:
    return hashlib.sha256(str(count).encode()).hexdigest()


@module.verb("result.evaluate")
def result_evaluate(p: mp.ResultEvaluateParams, ctx):
    count = (p.result.get("payload") or {}).get("count")
    if not isinstance(count, int):
        return mp.ResultEvaluateResult(verdict="reject", reason="primes/no_count")
    return mp.ResultEvaluateResult(verdict="accept", value=float(count), digest=digest(count), digest_version=1,
                                   summary={"count": count}, fields={"count": count})


@module.verb("golden.list")
def golden_list(p: mp.GoldenListParams, ctx):
    return mp.GoldenListResult(goldens=goldens.load(HERE, "goldens/*.json", p.node_class))


@module.verb("op.plan")
def op_plan(p: mp.OpPlanParams, ctx):
    return mp.OpPlanResult(summary=f"count primes below {len(p.params.get('ns') or [])} values of n")


@module.verb("op.apply")
def op_apply(p: mp.OpApplyParams, ctx):
    ns = p.params.get("ns") or []
    cid = "c_primes_" + hashlib.sha256(repr(ns).encode()).hexdigest()[:8]
    jobs = [fx.job(job_key(ID, COMPAT, {"n": n}), {"n": n}, labels={"n": n}) for n in ns]
    return mp.OpApplyResult(effects=[fx.campaign_create(cid, "primes"),
                                     mp.Effect(kind="jobs.enqueue", args={"campaign_id": cid, "jobs": jobs})],
                            response="redirect", result={"campaign_id": cid})


if __name__ == "__main__":
    module.run()
```

Rules that keep you out of trouble:

- **Job keys.** Compute them with `oarbank_sdk.keys.job_key(module_id, compat, key_inputs)`. Equal inputs
  are one job, and the host serves a canonical result from its cache instead of running the job again.
- **Specs.** A job's `spec` is your payload. Put `datasets`, `mounts`, `resources` and `timeout_s` on the
  item (`oarbank_sdk.effects.job` takes them). The runner receives a `SpecEnvelope` whose `payload` is your spec.
- **Reading.** Read through `ctx.host` callbacks, each gated by a manifest permission: datasets, your own
  settings, your own store documents, nodes, and your own campaigns' jobs. Never read the host's database.
- **Codes.** Reason codes of your own are namespaced: `primes/<code>`.

## 4. The runner

The agent runs `<runner exec> run --spec S --workdir W --out R` in a clean environment. Read the
envelope's `payload`; write a `ResultEnvelope` atomically; exit 0. `doctor --json` prints a
`DoctorOutput`, whose `health` the agent reads.

```python
import argparse
import json
import os
import sys
from pathlib import Path

from oarbank_sdk.control import Control, Stopped


def count_primes(n: int, ctl: Control) -> int:
    sieve = bytearray([1]) * n
    sieve[:2] = b"\0\0"
    for i in range(2, int(n ** 0.5) + 1):
        ctl.safe_point()                   # cancellable: a stop request ends the job here
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(range(i * i, n, i)))
    return sieve.count(1)


def write(path: Path, doc: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)                  # atomic: the agent never reads half a file


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    for a in ("--spec", "--workdir", "--out"):
        r.add_argument(a, required=True)
    r.add_argument("--events")
    sub.add_parser("doctor").add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "doctor":
        print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy", "checks": []}))
        return 0
    env = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    ctl = Control(a.workdir)
    ctl.phase("sieve")                     # the job is underway
    try:
        count = count_primes(int(env["payload"]["n"]), ctl)
    except Stopped:
        return ctl.acknowledge_stop()      # failure.json (fault transient), exit 75
    write(Path(a.out), {"envelope": 1, "schema": "primes/result@1", "module_version": env["module_version"],
                        "protocol": 1, "payload": {"count": count}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`cancellable` promises to honour a stop request promptly. The agent asks through `control.json` and then nudges the
runner (SIGUSR1 on POSIX, an inherited event on Windows); `Control` re-reads the document when nudged and
`safe_point()` raises `Stopped`, so the same code stops on every platform. `acknowledge_stop()` records the stop in
`failure.json` and returns 75; the conformance kit times that acknowledgement (at most 2 s after the nudge, counted
from your first `phase`) and expects the exit within `stop_grace_s`. Create `Control` on the main thread. Exit codes: 2 means the spec can never succeed, 3 a missing node dependency, 75 a transient
failure; each counts only with a `failure.json`. Keep your results deterministic if you declare
`determinism = "exact"`: the conformance kit and the host's replica checks compare digests across nodes.

## 5. Goldens

A golden is a job with a known answer. A node runs it before it may run your module, and again after
every upgrade. `golden.list` returns the goldens for a node class: its platform, OS version, CPU, GPUs,
capabilities and pools, never its identity. Keep them as `Golden` JSON in `goldens/*.json` and load them with
`oarbank_sdk.goldens.load`: your own bundle files are immutable, so reading them keeps the verb pure, while host
state is only reachable through callbacks. `goldens/primes-below-100k.json` (9592 primes; the digest is
`sha256("9592")`):

```json
{
 "name": "primes-below-100k",
 "key_inputs": {
  "n": 100000
 },
 "stages": [
  "run"
 ],
 "expected": {
  "digest": "454df0b799320283814e09c66752efdf334c0205f55f6334c40896e9a48f0712",
  "digest_version": 1
 }
}
```

The conformance kit runs your first golden through the runner and proves `cancellable` by stopping it after 0.3 s.
A golden that finishes sooner, like this one, leaves that check skipped; give a slower module a golden that runs
longer. If your goldens need datasets, give the kit a `conformance.json` with local copies (see
[spec/conformance.md](../spec/conformance.md)).

## 6. Conform, bundle, hand over

```bash
oarbank-sdk conform .              # manifest, bundle, protocol, runner: must say "0 failed"
oarbank-sdk bundle build .         # dist/primes-1.0.0.mfb
```

The operator then runs `oarbank module install dist/primes-1.0.0.mfb`, reviews the plan, and runs
`oarbank module enable primes@1.0.0`. Nodes install the release, doctor, run your golden and become
certified, and `mod.primes.count` appears in the console. A new version goes through
`oarbank module canary primes@1.1.0 --node <node>`, then `promote`, with `rollback` one command away (see
[spec/bundles.md](../spec/bundles.md)).

## 7. More than one platform

`requires.platforms` lists the node platforms your runner supports (`darwin-arm64`, `linux-amd64`, `windows-amd64`, …).
When a platform needs something different, say so in the manifest instead of branching at run time
([spec/platforms.md](../spec/platforms.md#per-platform-declarations)); these keys need `requires.core >= 2.2`:

```toml
[requires]
core = ">=2.2,<3"
platforms = ["darwin-arm64", "linux-amd64", "windows-amd64"]
coordinator_platforms = ["darwin-arm64", "linux-amd64"]      # where the coordinator side runs (absent: anywhere)
unsupported = { runner = { windows-arm64 = "no arm64 build of the sieve" } }

[runner]
exec = ["python", "-I", "{bundle}/primes_runner.py"]
runtime = { kind = "python" }
env = { OMP_NUM_THREADS = "1" }                              # added after the agent's own variables

[runner.variants.windows-amd64]                               # most specific key wins: token, then OS
exec = ["{bundle}/native/windows-amd64/primes.exe"]
runtime = { kind = "native" }

[[stages]]
name = "run"
timeout_s = 600
requires.resources = { cpu = 1, mem_gb = 0.2 }

[stages.variants.windows]                                     # this stage on Windows nodes
timeout_s = 900

[bundle.platform_files]
"native/windows-amd64/**" = ["windows-amd64"]                 # only Windows nodes receive these files
```

If results legitimately differ per platform, give goldens an `expected_by_platform` and load them with
`oarbank_sdk.goldens.load(HERE, "goldens/*.json", p.node_class)`. To keep each campaign on one platform class, add
`[placement] mix = "same-os"` (or `same-platform`), or create one campaign per platform:
`fx.campaign_create(cid, name, placement=fx.placement("same-platform", pin=plat))` for each `plat` in
`ctx.host.fleet_platforms()` ([spec/platforms.md](../spec/platforms.md#placement)). Run the conformance kit on every
platform you declare: it runs your runner as this host's platform would.

## 8. Next

- **More stages.** A chain with a stage that runs `after` another (for example, computing on every node
  and scoring where a service provides a pool) is in module-protocol.md, "Jobs on the wire".
- **Your own pages.** Pages, panels and views in the console are in [spec/ui-contract.md](../spec/ui-contract.md).
  Preview them with `oarbank-sdk preview oarbank-module.toml`.
- **Services and probes.** A VM or a daemon your jobs need is in [spec/service-protocol.md](../spec/service-protocol.md).
