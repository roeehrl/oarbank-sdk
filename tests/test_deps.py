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
