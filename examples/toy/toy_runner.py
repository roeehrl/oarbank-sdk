"""toy runner (runner protocol 1): `run --spec S --workdir W --out R` and `doctor --json`. Stdlib only."""
import argparse
import json
import os
import sys
from pathlib import Path


def atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def run(spec_path: Path, workdir: Path, out: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    n = spec.get("payload", {}).get("n")
    if not isinstance(n, int) or n < 0:
        atomic_write(workdir / "failure.json", {"reason": "toy/bad_spec", "detail": f"n={n!r}"})
        return 2
    total = n * (n - 1) // 2
    atomic_write(out, {"envelope": 1, "schema": "toy/result@1", "module_version": "0.1.0", "protocol": 1,
                       "effective": {"n": n}, "payload": {"sum": str(total)}})
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
