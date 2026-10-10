"""Host tools as version constraints (spec/sandbox.md, "Host tools"): the shared version and constraint vectors, the
manifest's `[sandbox].tools` requests, the tools file a runner reads, and the conformance kit's tool fixtures."""
import copy
import functools
import json
import shutil
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import conformance, manifest as m, tools, toolversion as tv

ROOT = Path(__file__).parents[1]
VEC = json.loads((ROOT / "spec" / "vectors" / "tool-versions.json").read_text(encoding="utf-8"))
TOY = ROOT / "examples" / "toy"


def test_versions_normalize_and_order_as_the_vectors_say():
    for raw, want in VEC["normalized"].items():
        assert tv.normalize(raw) == want, raw
    order = sorted(VEC["ascending"], key=functools.cmp_to_key(lambda a, b: tv.version(a).compare(tv.version(b))))
    assert order == VEC["ascending"]
    for bad in VEC["versions_bad"]:
        with pytest.raises(ValueError):
            tv.version(bad)


def test_constraints_match_as_the_vectors_say():
    for c in VEC["constraints"]:
        assert tv.describe(c["constraint"]) == c["canonical"]
        for v, ok in c["matches"].items():
            assert tv.satisfies(v, c["constraint"]) is ok, (v, c["constraint"])
    for bad in VEC["constraints_bad"]:
        with pytest.raises(ValueError):
            tv.parse_constraint(bad)
    assert tv.satisfies("anything", None) and tv.satisfies("x", "") and not tv.satisfies("not-a-version", ">=1")


def test_arch_fits_as_the_vectors_say():
    for a in VEC["arch"]:
        assert tv.arch_fits(a["installed"], a["want"], a["native"]) is a["fits"], a


def _toy():
    return tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))


def test_a_tool_request_carries_a_version_constraint_and_an_arch():
    d = _toy()
    d["sandbox"] = {"tools": [{"id": "jdk", "version": ">=17,<22", "arch": "native", "trust": "code-exec"},
                              {"id": "python"}]}
    man = m.Manifest.model_validate(d)
    jdk, py = man.sandbox.tools
    assert (jdk.id, jdk.version, jdk.arch, jdk.trust) == ("jdk", ">=17, <22", "native", "code-exec")   # canonical spelling
    assert (py.version, py.arch, py.trust) == (None, "any", "read")
    for tool, why in [({"id": "jdk", "version": "17"}, "not an operator and a version"),
                      ({"id": "jdk", "version": ""}, "not empty"),
                      ({"id": "jdk", "arch": "x86_64"}, "arch"),
                      ({"id": "Java17"}, "id")]:
        bad = copy.deepcopy(d)
        bad["sandbox"] = {"tools": [tool]}
        with pytest.raises(ValidationError, match=why):
            m.Manifest.model_validate(bad)
    bad = copy.deepcopy(d)
    bad["sandbox"] = {"tools": [{"id": "jdk"}, {"id": "jdk", "version": ">=21"}]}
    with pytest.raises(ValidationError, match="more than once"):
        m.Manifest.model_validate(bad)


def test_the_tools_file_names_each_granted_installation(tmp_path, monkeypatch):
    (tmp_path / "t.json").write_text(json.dumps({"jdk": [{"path": "/opt/jdk-17", "version": "17.0.12", "arch": "aarch64"}]}),
                                     encoding="utf-8", newline="\n")
    monkeypatch.setenv("OARBANK_TOOLS_FILE", str(tmp_path / "t.json"))
    assert tools.path("jdk") == "/opt/jdk-17" and tools.version("jdk") == "17.0.12" and tools.arch("jdk") == "aarch64"
    assert tools.paths("jdk") == ["/opt/jdk-17"] and tools.installations("python") == []
    with pytest.raises(tools.ToolMissing):
        tools.path("python")
    monkeypatch.delenv("OARBANK_TOOLS_FILE")
    assert tools.all_tools() == {}


def test_conform_refuses_a_tool_fixture_whose_version_the_module_does_not_accept(tmp_path):
    src = tmp_path / "toy"
    shutil.copytree(TOY, src)
    man = (src / "oarbank-module.toml").read_text(encoding="utf-8")
    (src / "oarbank-module.toml").write_text(man + '\n[sandbox]\ntools = [{ id = "jdk", version = ">=17", trust = "code-exec" }]\n',
                                             encoding="utf-8", newline="\n")
    jdk = tmp_path / "jdk-11"
    jdk.mkdir()
    rep = conformance.conform(src, {"tools": {"jdk": {"path": str(jdk), "version": "11.0.2", "arch": "aarch64"}}})
    check = next(c for c in rep.as_dict()["checks"] if c["name"] == "host tool versions")
    assert check["status"] == "fail" and "jdk 11.0.2 (needs >=17)" in check["detail"]
