"""Regenerate spec/vectors/*.json from the reference implementation (run after a deliberate contract change).

    uv run python scripts/gen_vectors.py

Every implementation (the SDK, the coordinator, the agents) must reproduce these files exactly; tests/test_vectors.py
checks the SDK against them.
"""
import hashlib
import json
from pathlib import Path

from oarbank_sdk import keys, platform as pf, portable

OUT = Path(__file__).resolve().parents[1] / "spec" / "vectors"

CANONICAL = [
    {"a": 3.0, "b": 3, "c": 1e16, "d": 1e-7, "e": "é", "g": 2.5e15},
    {"z": 1, "€": 2, "é": 3, "a": {"y": [], "x": {}}},
    [1e21, 1e-6, 1e-7, 123456789012345680000.0, 0.1, -0.0, 4.5e21, 5e-324, 1.7976931348623157e308],
    {"s": "line\nbreak \"quoted\" back\\slash \u0001 tab\t", "n": None, "t": True, "f": False},
    {"\U0001F600": "astral", "\uFFFF": "bmp", "a": "ascii"},
    [9007199254740992, -9007199254740992, 0, -1],
    [2330662528422.65625, 0.5, 1.5, 2.5, 1e21, 1e-7, 123456789012345680000.0],   # exact ties round to even
]
REFUSED = [
    {"value": "NaN", "why": "NaN has no canonical form"},
    {"value": "Infinity", "why": "infinities have no canonical form"},
    {"value": 9007199254740993, "why": "integer beyond 2^53"},
]
JOB_KEYS = [
    ("dev.codonic.oarbank.toy", "toy1", {"n": 1000}, None),
    ("dev.codonic.oarbank.toy", "toy1", {"n": 1000.0}, None),
    ("dev.example.render", "render3", {"params": {"a": 0.5, "b": [1, 2, 3]}, "dataset": "scene:atrium@2026-10-02T13:05"}, "score"),
]
PATHS_OK = ["a", "notes/x.txt", "bin/linux-amd64/runner", "x..y", "A-b_c+d@e", "deep/" * 20 + "f"]
PATHS_OK_DOTFILE = [".gitattributes", "cfg/.env.example"]
PATHS_BAD = ["", "a\n", "a\n/b", "/abs", "a/", "a//b", "./a", "a/../b", "..", "a\\b", "C:/x", "c:x", "a:b", "CON", "con.txt", "a/NUL",
             "lpt9.log", "COM¹", "trail.", "trail ", "sp ace", "naïve", "e\u0301", "x" * 101, "y/" * 101 + "z",
             "*star", "q?", "pipe|", "lt<", "gt>", 'quo"te']
PLATFORMS_OK = ["darwin-arm64", "linux-amd64", "windows-arm64", "freebsd-amd64", "linux-riscv64"]
PLATFORMS_BAD = ["darwin", "linux-amd64\n", "Darwin-arm64", "linux_amd64", "linux-", "-arm64", "linux-amd64-musl!", ""]

# variant resolution: which key of a per-platform table applies, and how a section's variants overlay its base
PICKS = [(["linux", "linux-amd64", "windows"], p) for p in ("linux-amd64", "linux-arm64", "windows-arm64", "darwin-arm64")]
PICKS += [(["darwin-arm64"], "darwin-amd64"), (["darwin"], "darwin-arm64"), ([], "linux-amd64")]
PY = {"kind": "python"}
NATIVE = {"kind": "native"}
OVERLAYS = [
    ("runner",
     {"exec": ["python", "-I", "{bundle}/node/main.py"], "runtime": PY, "stop_grace_s": 20.0,
      "env": {"MKL_CBWR": "COMPATIBLE", "OMP_NUM_THREADS": "1"}},
     {"windows": {"exec": ["{bundle}/native/windows/scorer.exe"], "runtime": NATIVE, "env": {"KMP_AFFINITY": "disabled"}},
      "windows-amd64": {"stop_grace_s": 5.0, "env": {"OMP_NUM_THREADS": "2"}}},
     ["windows-amd64", "windows-arm64", "linux-amd64"]),
    ("runner",                                   # a variant's gpu (like its runtime) replaces the base value whole
     {"exec": ["python", "-I", "{bundle}/node/main.py"], "runtime": PY,
      "gpu": {"use": "exclusive", "apis_any": ["metal"]}, "capabilities": ["cancellable", "freeze_ok"]},
     {"linux": {"gpu": {"use": "shared"}, "capabilities": ["cancellable"]}},
     ["linux-arm64", "darwin-arm64"]),
    ("coordinator",
     {"exec": ["python", "-I", "{bundle}/coordinator.py"], "runtime": PY, "timeouts_s": {"default": 10.0}, "concurrency": 1},
     {"linux": {"timeouts_s": {"job.plan": 60.0}, "concurrency": 2, "env": {"OMP_NUM_THREADS": "1"}},
      "linux-arm64": {"exec": ["{bundle}/bin/linux-arm64/coordinator"], "runtime": NATIVE, "timeouts_s": {"default": 20.0}}},
     ["linux-arm64", "linux-amd64", "darwin-arm64"]),
    ("stage",
     {"name": "render", "timeout_s": 3600.0, "retry": {"max_attempts": 3},
      "requires": {"pools": {"scorer": 1}, "resources": {"cpu": 4.0, "mem_gb": 8.0}}},
     {"windows": {"timeout_s": 5400.0, "requires": {"resources": {"mem_gb": 10.0}}, "retry": {"max_attempts": 4}},
      "windows-arm64": {"requires": {"resources": {"cpu": 2.0}}}},
     ["windows-amd64", "windows-arm64", "darwin-arm64"]),
]

# placement classes
CLASS_TOKENS = ["darwin-arm64", "linux-amd64", "windows-arm64", "freebsd-riscv64"]
CLASS_MIXES = ["any", "same-os", "same-arch", "same-platform", "same-rack", None]
FEASIBLE = [
    (["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"], [], [], "any"),
    (["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"], [], [], "same-os"),
    (["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"], [], [], "same-arch"),
    (["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"], [[], ["darwin-arm64", "linux-amd64"]], [], "same-platform"),
    (["darwin-arm64", "linux-amd64", "linux-arm64", "windows-amd64"], [["linux-amd64", "linux-arm64", "darwin-arm64"]], ["linux"], "same-os"),
    (["darwin-arm64", "linux-amd64"], [["windows-amd64"]], [], "any"),
    (["darwin-arm64", "linux-amd64"], [], ["windows"], "same-platform"),
    (["darwin-arm64", "linux-amd64"], [], [], "same-rack"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    canon = {"cases": [{"input": c, "canonical": keys.canonical_json(c),
                        "sha256": hashlib.sha256(keys.canonical_bytes(c)).hexdigest()} for c in CANONICAL],
             "refused": REFUSED,
             "note": "RFC 8785 plus three refusals; inputs are JSON documents (numbers parsed as IEEE-754 doubles, "
                     "except integers within ±2^53)."}
    (OUT / "canonical-json.json").write_text(json.dumps(canon, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    jk = {"cases": [{"module_id": m, "compat": c, "key_inputs": k, "stage": s, "job_key": keys.job_key(m, c, k, s)}
                    for m, c, k, s in JOB_KEYS]}
    (OUT / "job-key.json").write_text(json.dumps(jk, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    pp = {"accept": PATHS_OK, "accept_with_dotfiles": PATHS_OK_DOTFILE,
          "reject": [p for p in PATHS_BAD if not portable.is_portable_path(p)],
          "casefold_collisions": [["Readme.md", "README.md"], ["a/B", "a/b"]]}
    assert len(pp["reject"]) == len(PATHS_BAD), [p for p in PATHS_BAD if portable.is_portable_path(p)]
    assert all(portable.is_portable_path(p) for p in PATHS_OK)
    assert all(portable.is_portable_path(p, allow_dotfiles=True) for p in PATHS_OK_DOTFILE)
    (OUT / "portable-path.json").write_text(json.dumps(pp, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    pt = {"accept": PLATFORMS_OK, "reject": PLATFORMS_BAD, "known": list(portable.KNOWN_PLATFORMS)}
    assert all(portable.is_platform_token(p) for p in PLATFORMS_OK) and not any(portable.is_platform_token(p) for p in PLATFORMS_BAD)
    (OUT / "platform-token.json").write_text(json.dumps(pt, indent=1) + "\n", encoding="utf-8", newline="\n")
    vr = {"pick": [{"keys": k, "platform": p, "key": next((x for x in reversed(pf.variant_keys(p)) if x in k), None)}
                   for k, p in PICKS],
          "merged": list(pf.MERGED),
          "overlay": [{"section": sec, "base": base, "variants": var, "platform": p, "resolved": pf.apply_variants(base, var, p)}
                      for sec, base, var, plats in OVERLAYS for p in plats],
          "note": "A per-platform table applies its token's entry, else its OS's entry, else the base. A section's "
                  "variants apply OS first, then token; each field a variant sets replaces the base value whole, except "
                  "the tables in `merged`, which merge key by key."}
    (OUT / "variant-resolution.json").write_text(json.dumps(vr, indent=1) + "\n", encoding="utf-8", newline="\n")
    pc = {"mixes": list(pf.MIXES),
          "class_key": [{"platform": t, "mix": m, "class": pf.class_key(t, m)} for t in CLASS_TOKENS for m in CLASS_MIXES],
          "applied": [{"mix": m, "applied": pf.normalize(m)} for m in CLASS_MIXES],
          "stricter": [{"a": a, "b": b, "stricter": pf.stricter(a, b)} for a in CLASS_MIXES for b in CLASS_MIXES],
          "scope_mix": [{"scope": s, "mix": pf.scope_mix(s)} for s in ("global", "os", "arch", "platform", "rack")],
          "feasible": [{"platforms": p, "stage_platforms": sp, "job_platforms": jp, "mix": m,
                        "classes": pf.feasible_classes(p, sp, jp, m)} for p, sp, jp, m in FEASIBLE],
          "note": "Absent mix is `any`; an unknown mix (or determinism scope) applies as `same-platform`. Strictness: any < "
                  "same-os, same-arch < same-platform; same-os with same-arch is same-platform. Feasible classes: the "
                  "module's platforms where every stage the unit runs and the job's platforms (tokens or OSes) allow, "
                  "grouped by class; an empty list allows every platform."}
    (OUT / "placement-class.json").write_text(json.dumps(pc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("vectors written to", OUT)


if __name__ == "__main__":
    main()
