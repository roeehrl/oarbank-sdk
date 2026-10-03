import gzip
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from oarbank_sdk import bundle as B

TOY = Path(__file__).parents[1] / "examples" / "toy"


def test_build_is_reproducible_and_verifies(tmp_path):
    a, info = B.build(TOY, tmp_path / "a.mfb")
    b, _ = B.build(TOY, tmp_path / "b.mfb")
    assert a.read_bytes() == b.read_bytes()
    assert info.module_id == "dev.codonic.oarbank.toy" and info.content_digest.startswith("h2:")
    v = B.verify(a, tmp_path / "unpacked")
    assert v.content_digest == info.content_digest
    assert B.verify_dir(tmp_path / "unpacked").content_digest == info.content_digest
    assert not any("__pycache__" in f["path"] for f in info.files)


def test_digest_is_over_contents_not_the_archive(tmp_path):
    out, info = B.build(TOY, tmp_path / "t.mfb")
    files = [dict(f) for f in info.files]
    assert B.content_digest(list(reversed(files))) == info.content_digest


def _rewrite(src, dst, mutate):
    with tarfile.open(src, "r:gz") as t:
        items = [(m, t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for m, data in mutate(items):
            t.addfile(m, io.BytesIO(data) if data is not None else None)
    dst.write_bytes(gzip.compress(raw.getvalue()))


def test_tampering_is_refused(tmp_path):
    out, _ = B.build(TOY, tmp_path / "t.mfb")

    def change(items):
        for m, d in items:
            if m.name == "toy_module.py":
                d = d + b"\n# evil\n"
                m.size = len(d)
            yield m, d
    _rewrite(out, tmp_path / "x.mfb", change)
    with pytest.raises(B.BundleError, match="sha256 mismatch"):
        B.verify(tmp_path / "x.mfb")

    def extra(items):
        yield from items
        m = tarfile.TarInfo("extra.py")
        m.size = 3
        yield m, b"x=1"
    _rewrite(out, tmp_path / "y.mfb", extra)
    with pytest.raises(B.BundleError, match="file list mismatch"):
        B.verify(tmp_path / "y.mfb")

    def escape(items):
        yield from items
        m = tarfile.TarInfo("../../etc/evil")
        m.size = 1
        yield m, b"x"
    _rewrite(out, tmp_path / "z.mfb", escape)
    with pytest.raises(B.BundleError, match="unsafe path"):
        B.verify(tmp_path / "z.mfb")


def test_core_imports_are_refused(tmp_path):
    mod = tmp_path / "mod"
    shutil.copytree(TOY, mod, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    (mod / "helper.py").write_text("import json\nfrom oarbank.coordinator import core\n", encoding="utf-8", newline="\n")
    with pytest.raises(B.BundleError, match="helper.py:2: imports oarbank"):
        B.build(mod)
    (mod / "helper.py").write_text("from oarbank_sdk import manifest\n", encoding="utf-8", newline="\n")
    B.build(mod)


def test_symlinks_are_refused(tmp_path):
    mod = tmp_path / "mod"
    shutil.copytree(TOY, mod, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    (mod / "link").symlink_to("/etc/passwd")
    with pytest.raises(B.BundleError, match="symlinks"):
        B.build(mod)


def test_a_git_worktree_pointer_file_is_not_bundled(tmp_path):
    """In a git worktree `.git` is a file ("gitdir: ..."), not a directory; it must not enter the bundle
    (it did, building a module from a worktree, and changed the digest)."""
    import shutil
    from oarbank_sdk import bundle as B
    src = tmp_path / "toy"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples" / "toy", src,
                    ignore=shutil.ignore_patterns("__pycache__", "dist"))
    plain = B.content_digest(B.list_files(src))
    (src / ".git").write_text("gitdir: /somewhere/.git/worktrees/toy\n", encoding="utf-8", newline="\n")
    assert ".git" not in {f["path"] for f in B.list_files(src)}
    assert B.content_digest(B.list_files(src)) == plain
