"""modelserver runner (runner protocol 1): a `generate` job sends each prompt to the node's warm model server through
its service endpoint (OARBANK_SERVICE_MODEL) and returns the completions. Stdlib and the SDK only."""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from oarbank_sdk import service_endpoint as ep


def atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def run(spec_path: Path, ws: Path, out: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    prompts = spec.get("payload", {}).get("prompts")
    if not isinstance(prompts, list) or not prompts or not all(isinstance(p, str) for p in prompts):
        atomic_write(ws / "failure.json", {"reason": "modelserver/bad_spec", "detail": f"prompts={prompts!r}"})
        return 2
    (ws / "phase").write_text("generating", encoding="utf-8")
    try:
        with ThreadPoolExecutor(4) as pool:              # concurrent connections, each its own stream
            answers = list(pool.map(lambda p: ep.request("model", "POST", "/v1/generate", {"prompt": p}).json(), prompts))
    except ep.EndpointError as e:
        atomic_write(ws / "failure.json", {"reason": "modelserver/no_service", "detail": str(e), "fault": "transient"})
        return 75
    time.sleep(float(spec["payload"].get("hold_s", 0)))     # tests overlap jobs with it
    texts = [a["text"] for a in answers]
    atomic_write(out, {"envelope": 1, "schema": "modelserver/result@1", "module_version": "0.1.0", "protocol": 1,
                       "payload": {"texts": texts, "digest": hashlib.sha256(json.dumps(texts).encode()).hexdigest()},
                       "provenance": {"service_pids": sorted({a["pid"] for a in answers}),
                                      "loads": max(a["loads"] for a in answers)}})
    return 0


def doctor() -> int:
    print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy", "capabilities": [],
                      "checks": [{"name": "python", "ok": True, "detail": sys.version.split()[0]}]}))
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
