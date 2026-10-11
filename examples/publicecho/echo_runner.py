"""publicecho runner (runner protocol 1): an `echo` job computes the answers the echo service gives its lines before any
address is announced. Stdlib only."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def run(spec_path: Path, ws: Path, out: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    lines = spec.get("payload", {}).get("lines")
    if not isinstance(lines, list) or not lines or not all(isinstance(x, str) for x in lines):
        atomic_write(ws / "failure.json", {"reason": "publicecho/bad_spec", "detail": f"lines={lines!r}"})
        return 2
    answers = [f"echo {x} -" for x in lines]
    atomic_write(out, {"envelope": 1, "schema": "publicecho/result@1", "module_version": "0.1.0", "protocol": 1,
                       "payload": {"answers": answers,
                                   "digest": hashlib.sha256(json.dumps(answers).encode()).hexdigest()}})
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
