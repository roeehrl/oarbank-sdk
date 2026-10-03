"""Dependencies ship as hash-pinned wheels (spec/bundles.md, "Dependencies"); a bundle missing one is not built."""
import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from oarbank_sdk import bundle as B, deps

TOY = Path(__file__).parents[1] / "examples" / "toy"


def test_pins_and_hashes_are_required():
    assert deps.parse_requirements("a==1 --hash=sha256:" + "a" * 64)[0]["version"] == "1"
    for bad in ("a==1", "a>=1 --hash=sha256:" + "a" * 64, "--extra-index-url https://x", "pydantic==2 --hash=sha256:" + "a" * 64):
        with pytest.raises(deps.DepsError):
            deps.parse_requirements(bad)


def test_wheels_cover_every_declared_platform(tmp_path):
    src = tmp_path / "toy"
    shutil.copytree(TOY, src)
    (src / "wheels").mkdir()
    w = src / "wheels" / "dep-1.0-cp312-cp312-macosx_11_0_arm64.whl"
    with zipfile.ZipFile(w, "w") as z:
        z.writestr("dep/__init__.py", "")
    h = hashlib.sha256(w.read_bytes()).hexdigest()
    (src / "requirements.txt").write_text(f"dep==1.0 --hash=sha256:{h}\n", encoding="utf-8", newline="\n")
    with pytest.raises(B.BundleError, match="no wheel in wheels/ for dep==1.0 on linux-amd64"):
        B.build(src, tmp_path / "x.mfb")                     # toy declares six platforms; one wheel covers one
    m = (src / "oarbank-module.toml").read_text(encoding="utf-8")
    import re
    (src / "oarbank-module.toml").write_text(re.sub(r'platforms = \[[^\]]*\]', 'platforms = ["darwin-arm64"]', m, count=1), encoding="utf-8", newline="\n")
    B.build(src, tmp_path / "x.mfb")


def test_the_host_provided_closure_comes_from_the_installed_sdk():
    host = deps.host_provided()
    assert {"oarbank-sdk", "pydantic", "pydantic-core", "jsonschema", "attrs", "referencing", "rpds-py", "jinja2",
            "markupsafe", "typing-extensions"} <= host
    assert not {"email-validator", "babel", "webcolors"} & host                # behind extras
    with pytest.raises(deps.DepsError, match="markupsafe is provided by the host"):
        deps.parse_requirements("MarkupSafe==3.0 --hash=sha256:" + "a" * 64)


def _toy(tmp_path, **requires):
    import tomllib
    from oarbank_sdk import manifest as mf
    d = tmp_path / "mod"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    doc = tomllib.loads((d / "oarbank-module.toml").read_text(encoding="utf-8"))
    doc["requires"].update(requires)
    return d, mf.Manifest.model_validate(doc)


def test_which_platforms_install_a_requirements_file(tmp_path):
    d, man = _toy(tmp_path, platforms=["darwin-arm64", "linux-amd64", "windows-amd64"], core=">=2.2,<3",
                  coordinator_platforms=["darwin-arm64"])
    assert deps.file_platforms(d, man, d / "requirements.txt") == ["darwin-arm64", "linux-amd64", "windows-amd64"]
    man = man.model_copy(update={"runner": man.runner.model_copy(update={
        "exec": ["python", "-I", "{bundle}/node/run.py"],
        "variants": {"windows": mf_variant(["python", "-I", "{bundle}/node_win/run.py"])}})})
    assert deps.file_platforms(d, man, d / "requirements.txt") == ["darwin-arm64"]          # the coordinator's only
    assert deps.file_platforms(d, man, d / "node" / "requirements.txt") == ["darwin-arm64", "linux-amd64"]
    assert deps.file_platforms(d, man, d / "node_win" / "requirements.txt") == ["windows-amd64"]
    assert deps.file_platforms(d, man, d / "elsewhere" / "requirements.txt") == []


def mf_variant(argv):
    from oarbank_sdk import manifest as mf
    return mf.RunnerVariant(exec=argv)


def wheel(where: Path, name: str, version: str, tag: str = "py3-none-any", requires: tuple = ()) -> Path:
    """A minimal wheel: enough metadata for uv to resolve it."""
    dist = f"{name.replace('-', '_')}-{version}"
    w = where / f"{dist}-{tag}.whl"
    with zipfile.ZipFile(w, "w") as z:
        z.writestr(f"{name.replace('-', '_')}/__init__.py", "")
        z.writestr(f"{dist}.dist-info/METADATA", "Metadata-Version: 2.1\nName: %s\nVersion: %s\n%s" % (
            name, version, "".join(f"Requires-Dist: {r}\n" for r in requires)))
        z.writestr(f"{dist}.dist-info/WHEEL", f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n")
        z.writestr(f"{dist}.dist-info/RECORD", "")
    return w


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


needs_uv = pytest.mark.skipif(not (__import__("os").environ.get("UV") or shutil.which("uv")), reason="deps compile runs uv")


@pytest.fixture
def index(tmp_path):
    d = tmp_path / "index"
    d.mkdir()
    return d


def compile_(mod, man, index, text):
    (mod / "requirements.in").write_text(text, encoding="utf-8", newline="\n")
    return deps.compile(mod, man, mod / "requirements.in", uv_args=("--no-index", "--find-links", str(index), "--no-cache"))


@needs_uv
def test_compile_unifies_platforms_into_one_marker_free_file_check_accepts(tmp_path, index):
    plats = ["darwin-arm64", "linux-amd64", "windows-amd64"]
    mod, man = _toy(tmp_path, platforms=plats)
    natives = [wheel(index, "nat", "2.0", t) for t in ("cp312-cp312-macosx_11_0_arm64", "cp312-cp312-manylinux_2_17_x86_64",
                                                       "cp312-cp312-win_amd64")]
    wheel(index, "pydantic", "2.99")                                     # resolved, never emitted: the host has it
    pure = wheel(index, "pure", "1.0", requires=("nat>=2", "pydantic", 'winonly; sys_platform == "win32"'))
    winonly = wheel(index, "winonly", "0.4")                             # a Windows-only dependency, pure Python
    rep = compile_(mod, man, index, "pure\n")
    text = (mod / "requirements.txt").read_text(encoding="utf-8")
    assert rep["platforms"] == plats and rep["pins"] == 3 and rep["partial"] == {"winonly": ["windows-amd64"]}
    reqs = {r["name"]: r for r in deps.parse_requirements(text)}         # no markers, nothing the host provides
    assert set(reqs) == {"nat", "pure", "winonly"} and ";" not in text and "pydantic==" not in text
    assert reqs["nat"]["hashes"] == {sha(w) for w in natives} and reqs["winonly"]["hashes"] == {sha(winonly)}
    assert "# winonly: needed on windows-amd64 (installed everywhere)" in text
    (mod / "wheels").mkdir()
    for w in [*natives, pure, winonly]:
        shutil.copy(w, mod / "wheels")
    assert deps.check(mod, man) == []                                    # as is: every pin, every platform


@needs_uv
def test_compile_reports_every_version_conflict_and_a_partial_package_without_wheels(tmp_path, index):
    mod, man = _toy(tmp_path, platforms=["darwin-arm64", "linux-amd64"])
    wheel(index, "nat", "2.0", "cp312-cp312-macosx_11_0_arm64")
    wheel(index, "nat", "1.0", "cp312-cp312-manylinux_2_17_x86_64")       # Linux only has the older version
    with pytest.raises(deps.DepsError, match=r"nat: 1\.0 on linux-amd64; 2\.0 on darwin-arm64"):
        compile_(mod, man, index, "nat\n")
    assert not (mod / "requirements.txt").exists()
    wheel(index, "macbin", "1.0", "cp312-cp312-macosx_11_0_arm64")        # a macOS-only binary dependency
    wheel(index, "tool", "1.0", requires=('macbin; sys_platform == "darwin"',))
    with pytest.raises(deps.DepsError, match=r"macbin==1\.0 \(needed on darwin-arm64\) on linux-amd64"):
        compile_(mod, man, index, "tool\n")


@needs_uv
def test_the_cli_compiles_beside_the_input(tmp_path, index):
    from oarbank_sdk.cli import main
    mod, _ = _toy(tmp_path)
    m = (mod / "oarbank-module.toml").read_text(encoding="utf-8")
    import re
    (mod / "oarbank-module.toml").write_text(re.sub(r'platforms = \[[^\]]*\]', 'platforms = ["linux-arm64"]', m, count=1),
                                             encoding="utf-8", newline="\n")
    wheel(index, "pure", "1.0")
    (mod / "requirements.in").write_text("pure\n", encoding="utf-8", newline="\n")
    assert main(["deps", "compile", str(mod), str(mod / "requirements.in"), "--", "--no-index", "--find-links", str(index),
                 "--no-cache"]) == 0
    assert deps.parse_requirements((mod / "requirements.txt").read_text(encoding="utf-8"))[0]["name"] == "pure"
