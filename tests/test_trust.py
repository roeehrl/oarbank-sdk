"""Secrets (spec/manifest.md, "Secrets") and container image sets (spec/sandbox.md, "Image sets", "GPU passthrough"):
the manifest rules, the reference image policy against the shared vectors and real layouts, the runner's secrets
helper, and the conformance kit's checks for both."""
import base64
import json
import os
import shutil
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import bundle as B
from oarbank_sdk import images as I
from oarbank_sdk import imagetest as T
from oarbank_sdk import manifest as mf
from oarbank_sdk import secrets
from oarbank_sdk.cli import check_ui
from oarbank_sdk.conformance import conform

ROOT = Path(__file__).parents[1]
TOY = ROOT / "examples" / "toy"
VEC = json.loads((ROOT / "spec" / "vectors" / "image-signatures.json").read_text())


def toy_doc() -> dict:
    return tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))


def model(**patch) -> dict:
    d = toy_doc()
    d["requires"]["core"] = ">=2.5,<3"
    for k, v in patch.items():
        d[k] = v
    return d


def bad(doc: dict, needle: str):
    with pytest.raises(ValidationError) as e:
        mf.Manifest.model_validate(doc)
    assert needle in str(e.value), str(e.value)


# ---------------------------------------------------------------------------- secrets: manifest

def test_secrets_are_declared_once_listed_by_stages_and_reach_something():
    d = model(secrets=[{"name": "api_key", "description": "the provider key"}])
    d["stages"][0]["secrets"] = ["api_key"]
    m = mf.Manifest.model_validate(d)
    assert m.secrets_of(None) == m.secrets_of("run") == ["api_key"]
    bad({**d, "secrets": d["secrets"] * 2}, "more than once")
    d2 = json.loads(json.dumps(d))
    d2["stages"][0]["secrets"] = ["nope"]
    bad(d2, "not declared in [[secrets]]")
    d2["stages"][0]["secrets"] = ["api_key", "api_key"]
    bad(d2, "more than once")
    d3 = model(secrets=[{"name": "api_key"}])                              # nothing receives it
    bad(d3, "reach nothing")
    d3["coordinator"]["permissions"] = ["secrets:read:self"]               # the coordinator side may read it
    assert mf.Manifest.model_validate(d3).secrets_of("run") == []


def test_secret_keys_need_core_2_5():
    d = model(secrets=[{"name": "api_key"}])
    d["stages"][0]["secrets"] = ["api_key"]
    d["requires"]["core"] = ">=2.4,<3"
    bad(d, "need requires.core >= 2.5")
    d = model()
    d["requires"]["core"] = ">=2.4,<3"
    d["coordinator"]["permissions"] = ["secrets:read:self"]
    bad(d, "secrets:read:self permission")


def test_a_bootstrap_stage_receives_no_secrets():
    d = model(secrets=[{"name": "k"}], datasets={"kinds": ["tool"], "pinned": [
        {"dataset_id": "tool:x", "kind": "tool", "files": [{"path": "x", "sha256": "0" * 64, "size": 1}]}]})
    d["stages"] = [{"name": "run", "default": True, "timeout_s": 60},
                   {"name": "fetch", "bootstrap": True, "determinism": "none", "secrets": ["k"]}]
    d["coordinator"]["capabilities"].append("result.merge")
    bad(d, "a bootstrap stage receives no secrets")


def test_lint_warns_about_secrets_with_egress_any():
    d = model(secrets=[{"name": "k"}], sandbox={"net": {"mode": "egress-any"}})
    d["stages"][0]["secrets"] = ["k"]
    assert any("egress-any" in w for w in mf.lint(mf.Manifest.model_validate(d)))


def test_secrets_helper_reads_only_what_the_agent_delivered(tmp_path, monkeypatch):
    monkeypatch.delenv(secrets.ENV, raising=False)
    assert secrets.names() == []
    with pytest.raises(secrets.SecretMissing):
        secrets.get("api_key")
    f = tmp_path / "secrets.json"
    f.write_text(json.dumps({"api_key": "s3cr3t-value"}))
    monkeypatch.setenv(secrets.ENV, str(f))
    assert secrets.get("api_key") == "s3cr3t-value" and secrets.names() == ["api_key"]
    with pytest.raises(secrets.SecretMissing):
        secrets.get("other")


def test_forms_never_take_a_credential(tmp_path):
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__"))
    s = json.loads((d / "schemas" / "save_note.json").read_text())
    s["properties"]["token"] = {"type": "string", "x-secret": True}
    (d / "schemas" / "save_note.json").write_text(json.dumps(s))
    errs = check_ui(str(d / "oarbank-module.toml"), mf.load(d / "oarbank-module.toml"))
    assert any("x-secret" in e and "[[secrets]]" in e for e in errs)


# ---------------------------------------------------------------------------- image sets: manifest

def cset(**kw) -> dict:
    return {"name": "tasks", "registry": "ghcr.io", "repository": "org/tasks/", "platform": "linux/amd64",
            "key": "keys/tasks.pub", **kw}


def test_container_sets_need_core_2_5_and_unique_names_and_well_formed_prefixes():
    d = model(sandbox={"container_sets": [cset()]})
    m = mf.Manifest.model_validate(d)
    assert m.sandbox.requests() and m.sandbox.runs_containers()
    assert m.container_set("ghcr.io/org/tasks/t1", "linux/amd64").name == "tasks"
    assert m.container_set("ghcr.io/org/tasks/t1", "linux/arm64") is None
    assert m.container_set("ghcr.io/org/tasks-x/t1", "linux/amd64") is None
    assert m.sandbox.for_bootstrap().container_sets == []
    bad(model(sandbox={"container_sets": [cset(), cset()]}), "more than once")
    bad(model(sandbox={"container_sets": [cset(registry="GHCR.io")]}), "registry")
    bad(model(sandbox={"container_sets": [cset(repository="/org")]}), "repository")
    bad(model(sandbox={"container_sets": [cset(key="../k.pub")]}), "key")
    bad(model(sandbox={"container_sets": [cset(index="ghcr.io/org/idx")]}), "index")       # an index is a tagged reference
    d["requires"]["core"] = ">=2.4,<3"
    bad(d, "sandbox.container_sets need requires.core >= 2.5")


def test_the_gpu_pool_is_container_gpu_passthrough():
    d = model(sandbox={"container_sets": [cset()]})
    d["stages"][0]["requires"] = {"pools": {"containers": 1, "gpu": 1}}
    bad(d, "runner.gpu.in_container")
    d["runner"]["gpu"] = {"use": "exclusive", "in_container": True}
    mf.Manifest.model_validate(d)
    d["stages"][0]["requires"] = {"pools": {"gpu": 1}}
    bad(d, "also reserves the containers pool")
    d["stages"][0]["requires"] = {"pools": {"containers": 1, "gpu": 1}}
    d["requires"]["core"] = ">=2.4,<3"
    bad(d, "a stage reserving the gpu pool")


# ---------------------------------------------------------------------------- the reference policy

def test_image_vectors():
    key = VEC["keys"][0]["pem"]
    assert I.key_sha256(key) == VEC["keys"][0]["sha256"]
    for k in VEC["keys"][1:]:
        with pytest.raises(I.ImageError):
            I.public_key(k["pem"])
    q = I.public_key(key)
    covers = mf.ContainerSet(name="s", key="k.pub", **VEC["set"]).covers
    for c in VEC["simple"]:
        r = I.check_simple_signing(q, base64.b64decode(c["payload_b64"]), c["signature_b64"], c["digest"], covers)
        assert (r is None) == c["ok"], (c["name"], r)
    for c in VEC["bundle"]:
        r = I.check_bundle(q, base64.b64decode(c["bundle_b64"]), c["digest"])
        assert (r is None) == c["ok"], (c["name"], r)
    for c in VEC["index"]:
        try:
            seq, imgs = I.parse_index(base64.b64decode(c["doc_b64"]), c["registry"], c["repository"])
            assert c["ok"] and seq == c["seq"] and sorted(imgs) == c["images"], c["name"]
        except I.ImageError:
            assert not c["ok"], c["name"]
    for c in VEC["normalize"]:
        if c.get("error"):
            with pytest.raises(I.ImageError):
                I.normalize(c["ref"])
        else:
            assert I.normalize(c["ref"]) == (c["repository"], c["tag"], c["digest"])
    for c in VEC["covers"]:
        assert mf.ContainerSet(name="s", key="k.pub", **c["set"]).covers(c["repository"]) == c["covers"]


@pytest.fixture
def signed(tmp_path):
    key, other = T.Key.from_seed(b"tasks"), T.Key.from_seed(b"intruder")
    lay = T.LayoutWriter(tmp_path / "layout")
    imgs = {}
    for name, how in (("bundle", "bundle"), ("simple", "simple"), ("unsigned", None), ("forged", "other")):
        repo = f"ghcr.io/org/tasks/{name}"
        d = lay.image(repo, name.encode())
        if how == "bundle":
            lay.sign(key, repo, d)
        elif how == "simple":
            lay.sign(key, repo, d, simple=True)
        elif how == "other":
            lay.sign(other, repo, d)
        imgs[name] = f"{repo}@{d}"
    return key, lay, imgs


def test_members_are_signed_by_the_pinned_key_in_either_cosign_format(signed):
    key, lay, imgs = signed
    pol = I.SetPolicy(mf.ContainerSet(**cset()), key.public_pem())
    src = I.Layout(lay.root)
    assert pol.verify(imgs["bundle"], "linux/amd64", src).ok
    assert pol.verify(imgs["simple"], "linux/amd64", src).ok
    for name, why in (("unsigned", "no cosign signature"), ("forged", "verifies with the set")):
        v = pol.verify(imgs[name], "linux/amd64", src)
        assert not v.ok and v.code == "image_not_approved" and why in v.reason, v
    assert "outside set" in pol.verify(imgs["bundle"], "linux/arm64", src).reason
    assert "not pinned by digest" in pol.verify("ghcr.io/org/tasks/bundle:latest", "linux/amd64", src).reason
    other = mf.ContainerSet(**cset(repository="org/other/"))
    assert not I.SetPolicy(other, key.public_pem()).verify(imgs["bundle"], "linux/amd64", src).ok


def test_an_index_lists_members_and_never_goes_back(signed):
    key, lay, imgs = signed
    cs = mf.ContainerSet(**cset(index="ghcr.io/org/tasks-index:current"))
    unsigned = imgs["unsigned"].split("@")[1]
    lay.set_index(key, cs.index, "ghcr.io", "org/tasks/", 5, [unsigned])
    pol, src = I.SetPolicy(cs, key.public_pem()), I.Layout(lay.root)
    v = pol.verify(imgs["unsigned"], "linux/amd64", src)
    assert v.ok and v.seq == 5                                   # in the index: no per-image signature needed
    assert "not in the set's index" in pol.verify(imgs["bundle"], "linux/amd64", src).reason
    assert "older than seq 6" in pol.verify(imgs["unsigned"], "linux/amd64", src, highest_seq=6).reason
    lay.set_index(T.Key.from_seed(b"intruder"), cs.index, "ghcr.io", "org/tasks/", 9, [imgs["bundle"].split("@")[1]])
    v = I.SetPolicy(cs, key.public_pem()).verify(imgs["bundle"], "linux/amd64", I.Layout(lay.root))
    assert not v.ok and "the set's index" in v.reason


def test_bundles_refuse_a_set_key_that_is_missing_or_not_p256(tmp_path):
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__"))
    m = (d / "oarbank-module.toml").read_text().replace('core = ">=2.1,<3"', 'core = ">=2.5,<3"')
    m += '\n[[sandbox.container_sets]]\nname = "tasks"\nregistry = "ghcr.io"\nrepository = "org/tasks/"\nkey = "keys/tasks.pub"\n'
    (d / "oarbank-module.toml").write_text(m)
    (d / "keys").mkdir()
    (d / "keys" / "tasks.pub").write_text(VEC["keys"][1]["pem"])           # an Ed25519 key
    with pytest.raises(B.BundleError, match="P-256"):
        out, _ = B.build(d, tmp_path / "m.mfb")
        B.verify(out)
    (d / "keys" / "tasks.pub").write_text(T.Key.from_seed(b"k").public_pem())
    out, _ = B.build(d, tmp_path / "m2.mfb")
    B.verify(out)


# ---------------------------------------------------------------------------- conformance

PROBE = '''
    if spec.get("payload", {}).get("secret_probe"):
        p = os.environ.get("OARBANK_SECRETS_FILE")
        if p is None:
            state = "absent"
        else:
            mode = os.stat(p).st_mode & 0o777
            inside = Path(p).resolve().is_relative_to(workdir.resolve())
            private = mode == 0o600 or os.name != "posix"       # Windows: the work directory's DACL, not a mode
            state = "ok" if private and inside and list(json.load(open(p))) == ["api_key"] else "wrong"
            if spec["payload"].get("leak"):
                sys.stderr.write("calling with key " + json.load(open(p))["api_key"] + "\\n")
        atomic_write(workdir / "failure.json", {"reason": "toy/secret_" + state})
        return 2
'''
LEAKY_SPEC = '''
    key = ctx.host.secret("api_key")
    return mp.SpecBuildResult(specs=[mp.BuiltSpec(key_inputs=j.key_inputs, spec_version=1, stages=[
        mp.StageSpec(stage="run", payload={"n": j.key_inputs["n"], "k": key})]) for j in p.jobs])
'''


def secret_toy(tmp_path, listed: bool, permission: bool = False, leak_spec: bool = False) -> Path:
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    r = (d / "toy_runner.py").read_text(encoding="utf-8")
    (d / "toy_runner.py").write_text(r.replace('    n = spec.get("payload", {}).get("n")\n',
                                               PROBE + '    n = spec.get("payload", {}).get("n")\n'), encoding="utf-8", newline="\n")
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.5,<3"')
    if permission:
        m = m.replace('permissions = ["files:read:self", "store:read:self"]',
                      'permissions = ["files:read:self", "store:read:self", "secrets:read:self"]')
    if listed:
        m = m.replace('name = "run"\n', 'name = "run"\nsecrets = ["api_key"]\n')
    m += '\n[[secrets]]\nname = "api_key"\ndescription = "provider key"\n'
    (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    if leak_spec:
        c = (d / "toy_module.py").read_text(encoding="utf-8")
        c = c.replace('def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:\n',
                      'def spec_build(p: mp.SpecBuildParams, ctx) -> mp.SpecBuildResult:\n' + LEAKY_SPEC)
        (d / "toy_module.py").write_text(c, encoding="utf-8", newline="\n")
    return d


def checks(rep, prefix: str) -> dict:
    return {c.name[len(prefix) + 2:]: (c.status, c.detail) for c in rep.checks if c.name.startswith(prefix + ":")}


def test_conform_delivers_secrets_only_to_the_declaring_stage_and_fails_a_leak(tmp_path):
    specs = [{"name": "probe", "payload": {"secret_probe": True}, "expect": {"exit": 2, "reason": "toy/secret_ok"}},
             {"name": "leak", "payload": {"secret_probe": True, "leak": True}, "expect": {"exit": 2, "reason": "toy/secret_ok"}}]
    rep = conform(secret_toy(tmp_path / "a", listed=True), {"runner_specs": specs})
    probe = checks(rep, "runner spec probe")
    assert probe["failure reason"][0] == "pass" and probe["no secret in its output or files"][0] == "pass", probe
    leak = checks(rep, "runner spec leak")["no secret in its output or files"]
    assert leak[0] == "fail" and "api_key" in leak[1]
    assert not rep.ok
    golden = [c for c in rep.checks if c.name.startswith("golden") and "no secret" in c.name]
    assert golden and all(c.status == "pass" for c in golden)
    proto = [c for c in rep.checks if c.name.startswith("no secret in any verb")]
    assert proto[0].status == "pass" and "refused without secrets:read:self" in proto[0].detail


def test_conform_a_stage_that_does_not_list_a_secret_never_gets_it(tmp_path):
    rep = conform(secret_toy(tmp_path, listed=False, permission=True), {"runner_specs": [
        {"name": "probe", "payload": {"secret_probe": True}, "expect": {"exit": 2, "reason": "toy/secret_absent"}}]})
    assert checks(rep, "runner spec probe")["failure reason"][0] == "pass"


def test_conform_fails_a_coordinator_that_passes_a_secret_into_a_spec(tmp_path):
    d = secret_toy(tmp_path, listed=False, permission=True, leak_spec=True)
    rep = conform(d, {"secrets": {"api_key": "sk-test-0123456789abcdef"}}, runner=False)
    c = next(c for c in rep.checks if c.name.startswith("no secret in any verb"))
    assert c.status == "fail" and "api_key" in c.detail


def set_toy(tmp_path, key, index: str | None = None) -> Path:
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    m = (d / "oarbank-module.toml").read_text(encoding="utf-8").replace('core = ">=2.1,<3"', 'core = ">=2.5,<3"')
    m += ('\n[[sandbox.container_sets]]\nname = "tasks"\nregistry = "ghcr.io"\nrepository = "org/tasks/"\n'
          'platform = "linux/amd64"\nkey = "keys/tasks.pub"\n' + (f'index = "{index}"\n' if index else ""))
    (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    (d / "keys").mkdir()
    (d / "keys" / "tasks.pub").write_text(key.public_pem(), encoding="utf-8", newline="\n")
    return d


def test_conform_verifies_set_members_and_checks_both_refusals(tmp_path):
    key = T.Key.from_seed(b"tasks")
    d = set_toy(tmp_path, key)
    lay = T.LayoutWriter(d / "fixtures" / "images")
    good = lay.image("ghcr.io/org/tasks/t1", b"t1")
    lay.sign(key, "ghcr.io/org/tasks/t1", good)
    unsigned = lay.image("ghcr.io/org/tasks/t2", b"t2")
    fx = {"images": {"tasks": {"layout": "fixtures/images", "members": [f"ghcr.io/org/tasks/t1@{good}"]}}}
    rep = conform(d, fx, runner=False)
    got = checks(rep, "container set tasks")
    assert got[f"ghcr.io/org/tasks/t1@{good} verifies"][0] == "pass"
    assert got["an image outside the set is refused (image_not_approved)"][0] == "pass"
    assert got["an unsigned image inside the set is refused (image_not_approved)"][0] == "pass"
    assert rep.ok, rep.text()
    fx["images"]["tasks"]["members"].append(f"ghcr.io/org/tasks/t2@{unsigned}")
    rep = conform(d, fx, runner=False)
    assert checks(rep, "container set tasks")[f"ghcr.io/org/tasks/t2@{unsigned} verifies"][0] == "fail"


def test_conform_with_an_index(tmp_path):
    key = T.Key.from_seed(b"tasks")
    d = set_toy(tmp_path, key, index="ghcr.io/org/tasks-index:current")
    lay = T.LayoutWriter(d / "fixtures" / "images")
    digests = [lay.image(f"ghcr.io/org/tasks/t{i}", b"t%d" % i) for i in range(3)]
    lay.set_index(key, "ghcr.io/org/tasks-index:current", "ghcr.io", "org/tasks/", 1, digests)
    members = [f"ghcr.io/org/tasks/t{i}@{dg}" for i, dg in enumerate(digests)]
    rep = conform(d, {"images": {"tasks": {"layout": "fixtures/images", "members": members}}}, runner=False)
    assert rep.ok, rep.text()


def test_imagetest_signatures_verify_with_a_standard_ecdsa_implementation():
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives import hashes, serialization
    key = T.Key.from_seed(b"interop")
    pub = serialization.load_pem_public_key(key.public_pem().encode())
    pub.verify(key.sign(b"message"), b"message", crypto.ECDSA(hashes.SHA256()))
    priv = crypto.generate_private_key(crypto.SECP256R1())
    pem = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert I.verify_ecdsa(I.public_key(pem), b"m", priv.sign(b"m", crypto.ECDSA(hashes.SHA256())))
    assert not I.verify_ecdsa(I.public_key(pem), b"n", priv.sign(b"m", crypto.ECDSA(hashes.SHA256())))


def test_the_taskbench_example_passes_the_kit():
    rep = conform(ROOT / "examples" / "taskbench")
    assert rep.ok, rep.text()
    names = [c.name for c in rep.checks]
    assert sum(n.startswith("container set") and n.endswith(" verifies") for n in names) == 3 and any("unsigned image inside the set is refused" in n for n in names)
