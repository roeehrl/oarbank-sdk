"""gpuinfo runner (runner protocol 1): sums the squares of its numbers and records which of its GPU APIs it reached.
Its doctor is `undetected` where none of them is reachable from its sandbox. Stdlib and the SDK only."""
import argparse
import json
import os
import sys
from pathlib import Path

from oarbank_sdk import gpu, platform as pf

# the runner's GPU APIs per OS, as oarbank-module.toml declares them ([runner].gpu and its variants)
APIS = {"darwin": ["metal"], "windows": ["cuda", "rocm", "vulkan", "opencl", "directml"]}
DEFAULT = ["cuda", "rocm", "vulkan", "opencl"]


def reachable() -> tuple[list[str], dict]:
    """The declared APIs this process reaches, and the probes' evidence."""
    found = gpu.detect()
    return [a for a in APIS.get(pf.os_(), DEFAULT) if a in found["host"]], found["evidence"]


def atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def run(spec_path: Path, ws: Path, out: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    numbers = spec.get("payload", {}).get("numbers")
    if not isinstance(numbers, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in numbers):
        atomic_write(ws / "failure.json", {"reason": "gpuinfo/bad_spec", "detail": f"numbers={numbers!r}"})
        return 2
    apis, evidence = reachable()
    if not apis:                                   # the core placed it here, so the node's report and this disagree
        atomic_write(ws / "failure.json", {"reason": "gpuinfo/no_gpu_api", "fault": "host",
                                           "detail": json.dumps(evidence)[:900]})
        return 3
    (ws / "phase").write_text("summing", encoding="utf-8")
    atomic_write(out, {"envelope": 1, "schema": "gpuinfo/result@1", "module_version": "0.1.0", "protocol": 1,
                       "payload": {"total": sum(n * n for n in numbers)},
                       "provenance": {"gpu_api": apis[0], "device": evidence[apis[0]]}})
    return 0


def doctor() -> int:
    apis, evidence = reachable()
    want = APIS.get(pf.os_(), DEFAULT)
    checks = [{"name": f"gpu api {a}", "ok": a in apis, "detail": evidence.get(a, "")} for a in want]
    print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy" if apis else "undetected",
                      "capabilities": [], "checks": checks, "attrs": {"platform": pf.current(), "gpu_apis": apis}}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--spec", required=True)
    r.add_argument("--workdir", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--events")
    d = sub.add_parser("doctor")
    d.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return run(Path(a.spec), Path(a.workdir), Path(a.out)) if a.cmd == "run" else doctor()


if __name__ == "__main__":
    sys.exit(main())
