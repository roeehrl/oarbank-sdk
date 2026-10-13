"""Regenerate spec/vectors/*.json from the reference implementation (run after a deliberate contract change).

    uv run python scripts/gen_vectors.py

Every implementation (the SDK, the coordinator, the agents) must reproduce these files exactly; tests/test_vectors.py
checks the SDK against them.
"""
import hashlib
import json
from pathlib import Path

from oarbank_sdk import images as I, imagetest as T, keys, platform as pf, portable, toolversion as tv

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


def image_vectors() -> dict:
    """spec/vectors/image-signatures.json: what images.py and oarbank-core images.rs decide for container sets. Signatures
    come from imagetest's deterministic test keys, so the file is reproducible."""
    import base64
    b64 = lambda b: base64.b64encode(b).decode()
    key, other = T.Key.from_seed(b"vector-key"), T.Key.from_seed(b"other-key")
    d1 = "sha256:" + hashlib.sha256(b"image one").hexdigest()
    d2 = "sha256:" + hashlib.sha256(b"image two").hexdigest()
    ref = f"ghcr.io/org/tasks/t1@{d1}"
    sset = {"registry": "ghcr.io", "repository": "org/tasks/"}
    simple = []

    def sc(name, payload, sig, digest=d1):
        q = I.public_key(key.public_pem())
        ok = I.check_simple_signing(q, payload, b64(sig), digest, lambda r: r.startswith("ghcr.io/org/tasks/")) is None
        simple.append({"name": name, "payload_b64": b64(payload), "signature_b64": b64(sig), "digest": digest, "ok": ok})
    good = T.simple_payload(ref, d1)
    sc("valid", good, key.sign(good))
    sc("signed by another key", good, other.sign(good))
    sc("payload names another digest", T.simple_payload(ref, d2), key.sign(T.simple_payload(ref, d2)))
    tampered = good.replace(b"cosign container", b"cosign Container")
    sc("payload changed after signing", tampered, key.sign(good))
    wrong_type = good.replace(b"cosign container image signature", b"atomic container signature")
    sc("not a cosign signature type", wrong_type, key.sign(wrong_type))
    outside = T.simple_payload(f"ghcr.io/org/other/t1@{d1}", d1)
    sc("docker-reference outside the set", outside, key.sign(outside))
    sig = key.sign(good)                              # SEQUENCE { INTEGER r, INTEGER s }: pad r with a needless zero
    r_len = sig[3]
    padded = b"\x02" + bytes([r_len + 1]) + b"\0" + sig[4:4 + r_len] + sig[4 + r_len:]
    sc("signature not minimal DER", good, b"\x30" + bytes([len(padded)]) + padded)
    sc("valid, request for another digest", good, key.sign(good), d2)
    bundles = []

    def bc(name, b, digest=d1):
        ok = I.check_bundle(I.public_key(key.public_pem()), b, digest) is None
        bundles.append({"name": name, "bundle_b64": b64(b), "digest": digest, "ok": ok})
    bc("valid", T.bundle(key, ref, d1))
    bc("signed by another key", T.bundle(other, ref, d1))
    bc("another digest", T.bundle(key, ref, d2))
    bc("an attestation, not a signature", T.bundle(key, ref, d1, predicate="https://slsa.dev/provenance/v1"))
    env = json.loads(T.bundle(key, ref, d1))
    env["dsseEnvelope"]["payloadType"] = "application/json"
    bc("not an in-toto payload", json.dumps(env).encode())
    env = json.loads(T.bundle(key, ref, d1))
    st = json.loads(base64.b64decode(env["dsseEnvelope"]["payload"]))
    st["subject"][0]["digest"]["sha256"] = d2.split(":", 1)[1]
    env["dsseEnvelope"]["payload"] = b64(json.dumps(st).encode())
    bc("statement changed after signing", json.dumps(env).encode(), d2)
    bc("no DSSE envelope", b'{"messageSignature": {}}')
    index = []

    def ic(name, doc, reg="ghcr.io", repo="org/tasks/"):
        try:
            seq, imgs = I.parse_index(doc, reg, repo)
            index.append({"name": name, "doc_b64": b64(doc), "registry": reg, "repository": repo, "ok": True,
                          "seq": seq, "images": sorted(imgs)})
        except I.ImageError:
            index.append({"name": name, "doc_b64": b64(doc), "registry": reg, "repository": repo, "ok": False})
    ic("valid", I.index_document("ghcr.io", "org/tasks/", 7, [d2, d1]))
    ic("another set's index", I.index_document("ghcr.io", "org/other/", 7, [d1]))
    ic("another registry", I.index_document("docker.io", "org/tasks/", 7, [d1]))
    ic("negative seq", json.dumps({"type": I.INDEX_DOC_TYPE, "registry": "ghcr.io", "repository": "org/tasks/", "seq": -1,
                                   "images": [d1]}).encode())
    ic("not a digest", json.dumps({"type": I.INDEX_DOC_TYPE, "registry": "ghcr.io", "repository": "org/tasks/", "seq": 1,
                                   "images": ["sha256:abc"]}).encode())
    ic("wrong type", json.dumps({"type": "something/v1", "registry": "ghcr.io", "repository": "org/tasks/", "seq": 1,
                                 "images": [d1]}).encode())
    norm = []
    for r in ("ghcr.io/org/tasks/t1@" + d1, "org/tool:1.2@" + d1, "busybox@" + d1, "index.docker.io/org/x@" + d1,
              "localhost:5000/a/b:v1@" + d1, "registry.example.org:8443/x", "Upper/case@" + d1, "ghcr.io/org/t@sha256:abc"):
        try:
            repo, tag, dig = I.normalize(r)
            norm.append({"ref": r, "repository": repo, "tag": tag, "digest": dig})
        except I.ImageError:
            norm.append({"ref": r, "error": True})
    from oarbank_sdk.manifest import ContainerSet
    covers = [{"set": {"registry": reg, "repository": prefix}, "repository": repo,
               "covers": ContainerSet(name="s", registry=reg, repository=prefix, key="k.pub").covers(repo)}
              for reg, prefix, repo in [("ghcr.io", "org/tasks/", "ghcr.io/org/tasks/t1"),
                                        ("ghcr.io", "org/tasks/", "ghcr.io/org/tasks-extra/t1"),
                                        ("ghcr.io", "org/tasks/", "docker.io/org/tasks/t1"),
                                        ("ghcr.io", "org/tasks", "ghcr.io/org/tasks"),
                                        ("ghcr.io", "org/tasks", "ghcr.io/org/tasks/t1"),
                                        ("localhost:5000", "a/", "localhost:5000/a/b")]]
    keys_ = [{"pem": key.public_pem(), "sha256": I.key_sha256(key.public_pem())},
             {"pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAGb9ECWmEzf6FQbrBZ9w7lshQhqowtrbLDFw4rXAxZuE=\n-----END PUBLIC KEY-----\n",
              "error": "an Ed25519 key"},
             {"pem": "not a key", "error": "no PEM"}]
    return {"set": sset, "keys": keys_, "simple": simple, "bundle": bundles, "index": index, "normalize": norm, "covers": covers,
            "note": "A set member's digest carries a cosign signature by the pinned ECDSA P-256 key: a simple-signing payload "
                    "(its digest, type and an in-set docker-reference) or a Sigstore bundle (a DSSE envelope over an in-toto "
                    "statement with predicate " + I.COSIGN_PREDICATE + " naming the digest). Implementations agree on `ok`; "
                    "their reasons may differ. Index documents belong to exactly one set."}


# host tool versions and constraints (spec/sandbox.md, "Host tools")
TOOL_VERSIONS = ["17", "17.0.12", "17.0.12+7", "21.0.4", "22-ea", "22", "8.0.392", "11.0.2", "17.0.0", "21-ea.1", "21-ea.12",
                 "21-rc", "1.9", "1.10.2", "3.12.4", "11.0.2_7"]
TOOL_VERSIONS_BAD = ["", "v17", "17.", ".17", "17..0", "seventeen", "17 0", "17.0.x", "-ea"]
TOOL_CONSTRAINTS = [">=17", ">=17, <22", "~> 17", "~> 17.0", "~> 17.0.2", "= 17.0.12", "== 17", "!= 11.0.2", "> 17", "<= 21",
                    "<22", ">=17,<22", ">=1.10", "~> 1.9", ">= 8"]
TOOL_CONSTRAINTS_BAD = ["", "17", ">= ", "=> 17", ">=17;<22", ">=17,", "~ 17", ">=v17", "<>17"]
TOOL_ARCHES = [("aarch64", "native", "arm64"), ("aarch64", "native", "amd64"), ("x86_64", "any", "arm64"),
               ("x86_64", "amd64", "arm64"), ("", "any", "arm64"), ("", "native", "arm64"), ("arm64", "arm64", "amd64"),
               ("x64", "native", "amd64"), ("ppc64le", "native", "arm64")]


def tool_vectors() -> dict:
    vs = []
    for c in TOOL_CONSTRAINTS:
        vs.append({"constraint": c, "canonical": tv.describe(c),
                   "matches": {v: tv.satisfies(v, c) for v in TOOL_VERSIONS}})
    order = sorted(TOOL_VERSIONS, key=lambda v: __import__("functools").cmp_to_key(lambda a, b: a.compare(b))(tv.version(v)))
    return {"normalized": {v: tv.normalize(v) for v in TOOL_VERSIONS},
            "versions_bad": TOOL_VERSIONS_BAD, "constraints": vs, "constraints_bad": TOOL_CONSTRAINTS_BAD,
            "ascending": order,
            "arch": [{"installed": i, "want": w, "native": n, "fits": tv.arch_fits(i, w, n)} for i, w, n in TOOL_ARCHES],
            "note": "Versions: dot-separated numbers, optional -pre and +build; missing segments are zero, a pre-release sorts "
                    "below its release (its identifiers numerically when numeric), build metadata is ignored, `_` separates "
                    "segments. Constraints: comma-separated clauses with "
                    "=, ==, !=, >, >=, <, <=, ~> (pessimistic: the last given segment may grow; one segment: that segment). "
                    "`ascending` lists equal versions in input order. Arch: aarch64/arm64 and x86_64/amd64/x64 are "
                    "one each; an unknown arch fits only `any`."}


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
    iv = image_vectors()
    (OUT / "image-signatures.json").write_text(json.dumps(iv, indent=1) + "\n", encoding="utf-8", newline="\n")
    for bad in TOOL_VERSIONS_BAD:
        try:
            tv.version(bad)
            raise AssertionError(f"version {bad!r} parsed")
        except ValueError:
            pass
    for bad in TOOL_CONSTRAINTS_BAD:
        try:
            tv.parse_constraint(bad)
            raise AssertionError(f"constraint {bad!r} parsed")
        except ValueError:
            pass
    (OUT / "tool-versions.json").write_text(json.dumps(tool_vectors(), indent=1) + "\n", encoding="utf-8", newline="\n")
    print("vectors written to", OUT)


if __name__ == "__main__":
    main()
