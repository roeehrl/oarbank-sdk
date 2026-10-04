"""taskbench runner. `score` counts the PASSED lines of a task log (the golden stage). `attempt` runs its task image
through the agent's broker with the provider key, which only this stage receives, in the container's environment; the
key never reaches a log, the result or an artifact. Stdlib and oarbank_sdk only."""
import argparse
import json
import os
import sys
from pathlib import Path


def write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def run(spec_path: Path, ws: Path, out: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    res = {"envelope": 1, "schema": "taskbench/result@1", "module_version": "0.1.0", "protocol": 1}
    payload = spec.get("payload") or {}
    if spec.get("stage") == "attempt":
        from oarbank_sdk import broker, secrets
        r = broker.run(payload["image"], ["/task/run"], platform="linux/amd64", network=True,
                       env={"PROVIDER_KEY": secrets.get("provider_key")}, mounts=[broker.Mount("out", "/out")])
        log = (ws / r.stdout_path).read_text(encoding="utf-8", errors="replace")
        write(out, {**res, "payload": {"passed": sum(1 for x in log.splitlines() if x.endswith(" PASSED")),
                                       "exit_code": r.exit_code}})
        return 0
    log = payload.get("log")
    if not isinstance(log, str):
        write(ws / "failure.json", {"reason": "taskbench/bad_spec", "detail": "payload.log must be a string"})
        return 2
    write(out, {**res, "payload": {"passed": sum(1 for x in log.splitlines() if x.endswith(" PASSED"))}})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    for a in ("--spec", "--workdir", "--out"):
        r.add_argument(a, required=True)
    r.add_argument("--events")
    sub.add_parser("doctor").add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy", "capabilities": [], "checks": []}))
        return 0
    return run(Path(a.spec), Path(a.workdir), Path(a.out))


if __name__ == "__main__":
    sys.exit(main())
