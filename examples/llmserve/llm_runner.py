"""llmserve runner (runner protocol 1). A job runs its prompts through one model backend (llm_backends.py) and holds the
agent's control document: `pause` and `stop` at every unit's safe point, `gpu_duty` with the SDK's duty governor
(oarbank_sdk.duty), which also reports the duty it held as `gpu_duty` metric events.

payload: {"prompts": [str], "max_tokens": int, "backend": "auto" | "mlx" | "llamacpp" | "stub", "model": "<name under
models/ in the module's data directory>", "work": int (stub only: the size of its unit of work)}. The `selftest` stage
always runs the stub. Stdlib and the SDK only; MLX and llama.cpp are imported only when a job asks for them."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # -I leaves the bundle off sys.path; its own files are here

import llm_backends as B  # noqa: E402
from oarbank_sdk.control import Control, Stopped  # noqa: E402
from oarbank_sdk.duty import Duty  # noqa: E402

VERSION = "0.1.0"


def atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def fail(ws: Path, reason: str, detail: str, fault: str = "job") -> int:
    atomic_write(ws / "failure.json", {"reason": f"llmserve/{reason}", "detail": detail[:500], "fault": fault})
    return {"job": 2, "host": 3, "transient": 75}[fault]


def digest(completions: list[str]) -> str:
    return hashlib.sha256(json.dumps(completions, ensure_ascii=False).encode()).hexdigest()


def run(spec_path: Path, ws: Path, out: Path, events: Path | None) -> int:
    ctl = Control(ws)                       # first: it installs the nudge handler, then reads control.json
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    p = spec.get("payload") or {}
    stage = spec.get("stage") or "selftest"
    prompts = p.get("prompts")
    if not isinstance(prompts, list) or not prompts or not all(isinstance(x, str) for x in prompts):
        return fail(ws, "bad_spec", f"prompts={prompts!r}")
    max_tokens = int(p.get("max_tokens") or 32)
    asked = "stub" if stage == "selftest" else str(p.get("backend") or "auto")
    try:
        backend = B.choose(asked)
    except ValueError as e:
        return fail(ws, "no_backend", str(e), "host")
    model_path = None
    if backend != "stub":
        name = str(p.get("model") or "")
        data = os.environ.get("OARBANK_MODULE_DATA") or ""
        model_path = str(Path(data) / "models" / name) if name and data else None
    ctl.phase("loading")
    try:
        model = B.load(backend, model_path, int(p.get("work") or 64), float(spec.get("resources", {}).get("mem_gb") or 0) or None)
    except FileNotFoundError as e:
        return fail(ws, "no_model", str(e))
    duty = Duty(ctl, events=events, device="cpu" if backend == "stub" else "gpu")
    completions, tokens = [], 0
    ctl.phase("generating")
    started = time.monotonic()
    try:
        for i, prompt in enumerate(prompts):
            text = []
            for piece in model.generate(prompt, max_tokens, duty, ctl):
                text.append(piece)
                tokens += 1
            completions.append("".join(text))
            if events is not None:
                with open(events, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": time.time(), "kind": "progress", "fraction": round((i + 1) / len(prompts), 4)}) + "\n")
    except Stopped:
        duty.close()
        return ctl.acknowledge_stop()
    duty.close()
    busy = duty.busy_s
    atomic_write(out, {"envelope": 1, "schema": "llmserve/result@1", "module_version": VERSION, "protocol": 1,
                       "payload": {"completions": completions, "backend": backend, "model": p.get("model"), "tokens": tokens,
                                   "digest": digest(completions), "busy_s": round(busy, 3),
                                   "duty_idle_s": round(duty.idle_s, 3),
                                   "tokens_per_busy_s": round(tokens / busy, 3) if busy > 0 else None},
                       "provenance": {"wall_s": round(time.monotonic() - started, 3), "paused_s": round(ctl.paused_s, 3)}})
    return 0


def doctor() -> int:
    have = B.available()
    print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy", "capabilities": [],
                      "attrs": {"platform": os.environ.get("OARBANK_PLATFORM", ""), "backends": ",".join(have)},
                      "checks": [{"name": "python", "ok": True, "detail": sys.version.split()[0]},
                                 {"name": "backends", "ok": True, "detail": ", ".join(have)}]}))
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
    if a.cmd == "run":
        return run(Path(a.spec), Path(a.workdir), Path(a.out), Path(a.events) if a.events else None)
    return doctor()


if __name__ == "__main__":
    sys.exit(main())
