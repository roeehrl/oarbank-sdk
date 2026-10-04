"""reel runner (runner protocol 1): renders frames, one per step, with portable checkpoints. Stdlib only, besides the
SDK's vendorable control module.

- Every `every` frames, and when the agent asks for a checkpoint before it stops the job, the runner writes a checkpoint:
  the frames so far, their thumbnails and `state.json`. The agent takes it away and uploads it.
- An attempt that resumes (the spec's `resume`, the files under <W>/checkpoint/) starts after the last checkpointed
  frame, so a render interrupted on one node finishes on another with the same digest.
- It pauses at its safe points (between frames) and stops promptly.
- With an `asset` (a dataset mounted as asset/), it lists each of the asset's files with its sha256 in an `asset`
  artifact: how a render shows it read a dataset that nodes fetched from its origin.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reel_frames as F  # noqa: E402
from oarbank_sdk.control import Checkpoints, Control, Stopped, replace_text  # noqa: E402

VERSION = "0.1.0"


def run(spec_path: Path, workdir: Path, out: Path, events: str | None) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    p = spec.get("payload") or {}
    frames, seed, step_ms, every = p.get("frames"), p.get("seed", 0), p.get("step_ms", 0), p.get("every", 4)
    if not isinstance(frames, int) or not 1 <= frames <= 240:
        replace_text(workdir / "failure.json", json.dumps({"reason": "reel/bad_spec", "detail": f"frames={frames!r}"}))
        return 2
    ctl = Control(workdir)
    ckpt = Checkpoints(workdir, events, spec)
    done_dir, thumbs_dir = workdir / "out" / "frames", workdir / "out" / "thumbs"
    done_dir.mkdir(parents=True)
    thumbs_dir.mkdir()
    digests, start = [], 0
    if ckpt.resume():
        state = json.loads((ckpt.resume_dir() / "state.json").read_text(encoding="utf-8"))
        digests, start = list(state["digests"]), int(state["next"])
        for i in range(start):                          # the frames so far and their thumbnails
            for sub, dst in (("frames", done_dir), ("thumbs", thumbs_dir)):
                shutil.copyfile(ckpt.resume_dir() / sub / F.frame_name(i), dst / F.frame_name(i))

    def checkpoint(next_index: int):
        with ckpt.write({"next": next_index}) as d:
            for sub, src in (("frames", done_dir), ("thumbs", thumbs_dir)):
                (d / sub).mkdir()
                for i in range(next_index):
                    shutil.copyfile(src / F.frame_name(i), d / sub / F.frame_name(i))
            (d / "state.json").write_text(json.dumps({"next": next_index, "digests": digests}), encoding="utf-8")

    ctl.phase("render")
    i = start
    try:
        while i < frames:
            ctl.safe_point()
            rgb = F.pixels(seed, i)
            (done_dir / F.frame_name(i)).write_bytes(F.png(rgb))
            (thumbs_dir / F.frame_name(i)).write_bytes(F.png(F.thumbnail(rgb), F.WIDTH // F.THUMB, F.HEIGHT // F.THUMB))
            digests.append(F.frame_digest(seed, i))
            i += 1
            if step_ms:
                time.sleep(step_ms / 1000)
            if i % every == 0 and i < frames:
                checkpoint(i)
    except Stopped:
        if ctl.checkpoint_requested and i > start:
            checkpoint(i)
        return ctl.acknowledge_stop()
    ctl.phase("finishing")
    log = workdir / "out" / "render.txt"
    log.write_text(f"reel {VERSION}: {frames} frames, seed {seed}, resumed at frame {start}\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).parent / "assets" / "clip.webm", workdir / "out" / "clip.webm")
    arts = []
    if p.get("asset"):
        root = workdir / spec["mounts"][p["asset"]]
        lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(root).as_posix()}"
                 for f in sorted(root.rglob("*")) if f.is_file()]
        (workdir / "out" / "asset.txt").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
        arts.append({"name": "asset", "files": [{"path": "asset.txt", "local": "out/asset.txt"}]})
    arts += [{"name": "frames", "files": [{"path": F.frame_name(k), "local": f"out/frames/{F.frame_name(k)}",
                                          "thumbnail": {"local": f"out/thumbs/{F.frame_name(k)}"}} for k in range(frames)]},
            {"name": "clip", "files": [{"path": "clip.webm", "local": "out/clip.webm",
                                        "thumbnail": {"local": f"out/thumbs/{F.frame_name(0)}"}}]},
            {"name": "log", "files": [{"path": "render.txt", "local": "out/render.txt"}]}]
    replace_text(out, json.dumps({"envelope": 1, "schema": "reel/result@1", "module_version": VERSION, "protocol": 1,
                                  "effective": {"frames": frames, "resumed_at": start}, "artifacts": arts,
                                  "payload": {"frames": frames, "digest": F.render_digest(digests)}}))
    return 0


def doctor() -> int:
    print(json.dumps({"runner_protocol": {"supported": [1]}, "health": "healthy", "capabilities": [],
                      "attrs": {"platform": os.environ.get("OARBANK_PLATFORM")},
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
    return run(Path(a.spec), Path(a.workdir), Path(a.out), a.events) if a.cmd == "run" else doctor()


if __name__ == "__main__":
    sys.exit(main())
