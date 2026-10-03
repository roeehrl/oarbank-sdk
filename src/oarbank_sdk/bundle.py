"""Module bundles (spec/bundles.md): one immutable, digest-addressed file per module version.

    oarbank-sdk bundle build <module-dir> [-o out.mfb]
    oarbank-sdk bundle verify <file.mfb>

A bundle is a gzip tar of the module directory plus `bundle.json`:

    {"bundle": 2, "module_id", "name", "version", "compat",
     "files": [{"path", "sha256", "mode"}], "content_digest": "h2:<hex>", "sdk_version"}

`content_digest` covers the *contents and modes* (sorted `<sha256> <mode> <path>` lines, mode 644 or 755), not the
archive, so the same identity verifies a tarball, a mirror or an unpacked directory. Modes come from the manifest's
`[bundle].executables` and native execs, never from the build host. Every path is a PortablePath (spec/platforms.md),
case-fold unique, so a bundle unpacks identically and safely on macOS, Linux and Windows.
An optional detached signature (`<file>.sig`) is verified by the host, not by this module.

`[bundle.platform_files]` names the files only some platforms' nodes receive (`subset`); the bundle itself and its
digest stay whole, and the host builds each platform's release from the subset.
"""
import fnmatch
import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import __version__
from . import manifest as mf
from . import portable

BUNDLE_FORMAT = 2
BUNDLE_FILE = "bundle.json"
MANIFEST_FILE = "oarbank-module.toml"
IGNORE_DIRS = {"__pycache__", ".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "tests",
               "node_modules", ".idea", ".vscode"}
IGNORE_SUFFIXES = (".pyc", ".pyo", ".mfb", ".sig")
IGNORE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", BUNDLE_FILE, ".gitignore", ".git"}   # .git: a FILE in a worktree
IGNORE_PREFIXES = ("._",)                                            # macOS resource-fork companions
MAX_FILES, MAX_BYTES = 20_000, 2 * 1024 ** 3


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class BundleInfo:
    module_id: str
    name: str
    version: str
    compat: str
    content_digest: str
    files: tuple
    manifest: mf.Manifest

    @property
    def short_digest(self) -> str:
        return self.content_digest.split(":", 1)[1][:12]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _mode(m) -> str:
    if isinstance(m, bool) or not isinstance(m, (str, int)):
        raise BundleError(f"file mode {m!r}: an octal string or an integer")
    v = int(m, 8) if isinstance(m, str) else m
    if v not in (0o644, 0o755):
        raise BundleError(f"file mode {oct(v)}: only 644 and 755")
    return "755" if v == 0o755 else "644"


def content_digest(files) -> str:
    lines = "".join(f"{f['sha256']} {_mode(f['mode'])} {f['path']}\n"
                    for f in sorted(files, key=lambda f: f["path"].encode()))
    return "h2:" + hashlib.sha256(lines.encode()).hexdigest()


def _ignored(rel: PurePosixPath) -> bool:
    return (any(part in IGNORE_DIRS or part == "__MACOSX" for part in rel.parts[:-1]) or rel.name in IGNORE_NAMES
            or rel.name.endswith(IGNORE_SUFFIXES) or rel.name.startswith(IGNORE_PREFIXES))


def executables(man) -> list[str]:
    """Globs and paths of the bundle's executables: [bundle].executables plus the argv[0] of every native exec."""
    pats = list(man.bundle.executables)
    for argv in man.execs():
        if argv[0].startswith(mf.BUNDLE_TOKEN + "/"):
            pats.append(argv[0][len(mf.BUNDLE_TOKEN) + 1:])
    return pats


def _is_exec(rel: str, pats) -> bool:
    return any(rel == p or fnmatch.fnmatchcase(rel, p) for p in pats)


def check_paths(paths) -> None:
    for p in paths:
        try:
            portable.check_portable_path(p, allow_dotfiles=True)
        except portable.PathError as e:
            raise BundleError(f"not a portable path: {e}")
    clash = portable.casefold_collisions(paths)
    if clash:
        raise BundleError(f"paths collide on case-insensitive filesystems: {clash[:3]}")


def list_files(root: Path, man=None) -> list[dict]:
    """The files a bundle of `root` contains, with hashes and modes from the manifest (symlinks are refused)."""
    root = Path(root).resolve()
    if man is None:
        man = mf.load(root / MANIFEST_FILE)
    pats = executables(man)
    out = []
    for p in sorted(root.rglob("*")):
        rel = PurePosixPath(p.relative_to(root).as_posix())
        if _ignored(rel) or (p.is_dir() and not p.is_symlink()):
            continue
        if p.is_symlink():
            raise BundleError(f"{rel}: symlinks are not allowed in a bundle")
        out.append({"path": str(rel), "sha256": sha256_file(p), "mode": "755" if _is_exec(str(rel), pats) else "644"})
    if not any(f["path"] == MANIFEST_FILE for f in out):
        raise BundleError(f"{root}: no {MANIFEST_FILE}")
    check_paths([f["path"] for f in out])
    for argv in man.execs():                 # a native exec must name a file in the bundle
        if argv[0].startswith(mf.BUNDLE_TOKEN + "/") and not any(f["path"] == argv[0][len(mf.BUNDLE_TOKEN) + 1:] for f in out):
            raise BundleError(f"exec {argv[0]!r}: no such file in the bundle")
    check_platform_files(man)
    return out


def subset(files, man, platform: str) -> list:
    """The files (paths or file dicts) a node of `platform` receives under [bundle.platform_files]."""
    return [f for f in files if man.bundle.receives(f["path"] if isinstance(f, dict) else f, platform)]


def check_platform_files(man) -> None:
    """The manifest goes to every node, so no platform_files glob may match it."""
    if any(MANIFEST_FILE == g or fnmatch.fnmatchcase(MANIFEST_FILE, g) for g in man.bundle.platform_files):
        raise BundleError(f"bundle.platform_files: {MANIFEST_FILE} goes to every node; no glob may match it")


def platform_lint(man, paths) -> list[str]:
    """Warnings for [bundle.platform_files]: a glob that matches no file (often a typo)."""
    return [f"bundle.platform_files {g!r} matches no file" for g in man.bundle.platform_files
            if not any(p == g or fnmatch.fnmatchcase(p, g) for p in paths)]


def check_sdk_only(root: Path, forbidden=("oarbank",)) -> list[str]:
    """Module code may import the SDK but never the core: `import oarbank` / `from oarbank`
    (and submodules) are violations; `oarbank_sdk` is fine."""
    import re
    pat = re.compile(r"^\s*(?:from|import)\s+(" + "|".join(re.escape(f) for f in forbidden) + r")(?:\.|\s|$)", re.M)
    bad = []
    for p in sorted(Path(root).rglob("*.py")):
        rel = PurePosixPath(p.relative_to(root).as_posix())
        if _ignored(rel) or rel.parts[0] == "vendor":
            continue
        for m in pat.finditer(p.read_text(encoding="utf-8", errors="replace")):
            line = p.read_text(encoding="utf-8", errors="replace")[:m.start()].count("\n") + 1
            bad.append(f"{rel}:{line}: imports {m.group(1)} (modules are built only on oarbank_sdk)")
    return bad


def build(root, out=None) -> tuple[Path, BundleInfo]:
    """Build a bundle from a module directory (validated manifest, SDK-only imports)."""
    root = Path(root).resolve()
    man = mf.load(root / MANIFEST_FILE)
    bad = check_sdk_only(root)
    if bad:
        raise BundleError("core imports in module code:\n  " + "\n  ".join(bad))
    from . import deps
    dep_issues = deps.check(root, man)
    if dep_issues:
        raise BundleError("dependencies (spec/bundles.md, \"Dependencies\"):\n  " + "\n  ".join(dep_issues))
    files = list_files(root, man)
    total = sum((root / f["path"]).stat().st_size for f in files)
    if len(files) > MAX_FILES or total > MAX_BYTES:
        raise BundleError(f"bundle too large: {len(files)} files, {total} bytes")
    name = man.module.id.rsplit(".", 1)[-1]
    meta = {"bundle": BUNDLE_FORMAT, "module_id": man.module.id, "name": name, "version": man.module.version,
            "compat": man.module.compat, "files": files, "content_digest": content_digest(files), "sdk_version": __version__}
    out = Path(out) if out else root / "dist" / f"{name}-{man.module.version}.mfb"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as t:
        def add(arc, data: bytes, mode: int):
            ti = tarfile.TarInfo(arc)
            ti.size, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(data), mode, 0, 0, 0, "", ""
            t.addfile(ti, io.BytesIO(data))
        add(BUNDLE_FILE, json.dumps(meta, indent=1, sort_keys=True).encode(), 0o644)
        for f in files:
            add(f["path"], (root / f["path"]).read_bytes(), int(f["mode"], 8))
    out.write_bytes(gzip.compress(raw.getvalue(), mtime=0))      # no name or time in the header: reproducible bytes
    return out, _info(meta, man)


def _info(meta: dict, man) -> BundleInfo:
    return BundleInfo(meta["module_id"], meta["name"], meta["version"], meta["compat"], meta["content_digest"],
                      tuple(meta["files"]), man)


def _safe(name: str) -> str:
    """A bundle member name: must be a PortablePath exactly as written (so it is safe on every OS, including Windows,
    where `\\`, drive letters and device names would otherwise escape or misbehave)."""
    try:
        return portable.check_portable_path(name, allow_dotfiles=True)
    except portable.PathError as e:
        raise BundleError(f"unsafe path in bundle: {e}")


def verify(path, dest=None) -> BundleInfo:
    """Verify a bundle end to end (safe paths, regular files only, every hash, the file list, the digest,
    the manifest) and optionally unpack it into `dest` (which must not exist yet)."""
    import zlib
    try:
        return _verify(Path(path), dest)
    except (tarfile.TarError, OSError, EOFError, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleError(f"{path}: corrupt bundle ({type(e).__name__}: {e})")


def _verify(path: Path, dest) -> BundleInfo:
    with tarfile.open(path, "r:gz") as t:
        members = {}
        for m in t.getmembers():
            if m.isdir():
                continue
            if not m.isfile():
                raise BundleError(f"{m.name}: only regular files are allowed")
            n = _safe(m.name)
            if n in members:
                raise BundleError(f"{n}: duplicate entry")
            members[n] = m
        if BUNDLE_FILE not in members:
            raise BundleError(f"{path}: no {BUNDLE_FILE}")
        meta = json.loads(t.extractfile(members[BUNDLE_FILE]).read())
        if meta.get("bundle") != BUNDLE_FORMAT:
            raise BundleError(f"unsupported bundle format {meta.get('bundle')!r}")
        listed = {_safe(f["path"]): f for f in meta.get("files") or []}
        check_paths(list(listed))
        actual = set(members) - {BUNDLE_FILE}
        if set(listed) != actual:
            raise BundleError(f"file list mismatch: extra {sorted(actual - set(listed))[:5]}, missing {sorted(set(listed) - actual)[:5]}")
        data = {}
        for n, f in listed.items():
            b = t.extractfile(members[n]).read()
            if hashlib.sha256(b).hexdigest() != f["sha256"]:
                raise BundleError(f"{n}: sha256 mismatch")
            data[n] = b
        if content_digest(meta["files"]) != meta.get("content_digest"):
            raise BundleError("content digest mismatch")
    if MANIFEST_FILE not in data:
        raise BundleError(f"no {MANIFEST_FILE}")
    import tomllib
    man = mf.Manifest.model_validate(tomllib.loads(data[MANIFEST_FILE].decode()))
    if (man.module.id, man.module.version, man.module.compat) != (meta["module_id"], meta["version"], meta["compat"]):
        raise BundleError("bundle.json disagrees with the manifest (id, version or compat)")
    if dest is not None:
        dest = Path(dest)
        tmp = dest.with_name(dest.name + ".partial")
        if dest.exists():
            raise BundleError(f"{dest} exists")
        for n, b in data.items():
            p = tmp.joinpath(*n.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
            if os.name == "posix":
                os.chmod(p, int(_mode(listed[n]["mode"]), 8))
        (tmp / BUNDLE_FILE).write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        tmp.rename(dest)
    return _info(meta, man)


def verify_dir(root) -> BundleInfo:
    """Re-verify an unpacked bundle directory against its bundle.json (tamper check at load time)."""
    root = Path(root)
    meta = json.loads((root / BUNDLE_FILE).read_text(encoding="utf-8"))
    for f in meta["files"]:
        p = root.joinpath(*_safe(f["path"]).split("/"))
        if not p.is_file() or sha256_file(p) != f["sha256"]:
            raise BundleError(f"{f['path']}: missing or modified")
        if os.name == "posix" and oct(stat.S_IMODE(p.stat().st_mode)) != oct(int(_mode(f["mode"]), 8)):
            raise BundleError(f"{f['path']}: mode changed")      # exact bits: 0o600 is not "644"
    if content_digest(meta["files"]) != meta["content_digest"]:
        raise BundleError("content digest mismatch")
    return _info(meta, mf.load(root / MANIFEST_FILE))
