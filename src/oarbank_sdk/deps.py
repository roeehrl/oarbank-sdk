"""A module's Python dependencies (spec/bundles.md, "Dependencies"): hash-pinned requirements and the wheels for them,
shipped inside the bundle so that installing a module downloads nothing and runs none of its code.

- `requirements.txt` (coordinator, bundle root) and a `requirements.txt` beside the runner script list every
  distribution, transitive ones included, as `name==version --hash=sha256:<64 hex>` (pip's hash-checking mode; the
  output of `uv pip compile --generate-hashes`).
- `wheels/` holds a wheel for every pinned distribution and every declared platform (a `py3-none-any` wheel covers
  all). Source distributions are refused: building one runs arbitrary code.
- Hosts install with `--offline --no-index --find-links <bundle>/wheels --require-hashes --only-binary :all:
  --no-deps`, inside the module sandbox where the host has one.

A requirements file serves the platforms that install it (`file_platforms`): the runner's, every node platform whose
runner runs the script beside it; the coordinator's, the coordinator platforms (absent: the node platforms).
`oarbank-sdk deps compile` resolves a requirements.in once per platform with uv and writes one marker-free, hash-pinned
file for all of them; `oarbank-sdk bundle wheels` downloads the wheels for those platforms into `wheels/`.
"""
import functools
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import portable

WHEELS_DIR = "wheels"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([A-Za-z0-9.+!_-]+)(.*)$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
WHEEL = re.compile(r"^(?P<dist>[^-]+)-(?P<ver>[^-]+)(-\d[^-]*)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")
PY_TAG = "cp312"                                  # the hosts' managed CPython (spec/manifest.md, runtime kind python)
PY_VERSION = "3.12"
# uv's --python-platform per platform token: the newest wheel tags `download` asks pip for (manylinux_2_28, macOS 14)
UV_TARGETS = {"darwin-arm64": "aarch64-apple-darwin", "darwin-amd64": "x86_64-apple-darwin",
              "linux-amd64": "x86_64-manylinux_2_28", "linux-arm64": "aarch64-manylinux_2_28",
              "windows-amd64": "x86_64-pc-windows-msvc", "windows-arm64": "aarch64-pc-windows-msvc"}
MACOS_TARGET = "14.0"


class DepsError(ValueError):
    pass


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@functools.cache
def host_provided() -> frozenset[str]:
    """What the host's environment provides, so a module never pins it: the installed oarbank-sdk and, transitively,
    every requirement not behind an `extra` marker (pydantic, jsonschema, jinja2 and theirs). Other markers are kept
    (a dependency some platform's host has is never pinned)."""
    import importlib.metadata as md
    seen, todo = set(), ["oarbank-sdk"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            reqs = md.requires(name) or []
        except md.PackageNotFoundError:
            if name == "oarbank-sdk":
                raise DepsError("the oarbank-sdk distribution is not installed here, so what the host provides is unknown")
            continue                                  # not installed here (its marker is false on this machine)
        for r in reqs:
            req, _, marker = r.partition(";")
            if not re.search(r"\bextra\s*==", marker):
                todo.append(norm(re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req).group(1)))
    return frozenset(seen)


def parse_requirements(text: str) -> list[dict]:
    """[{name, version, hashes}] from a hash-pinned requirements file; DepsError on anything else."""
    out, errors = [], []
    joined = re.sub(r"\\\n", " ", text)
    for n, raw in enumerate(joined.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            errors.append(f"line {n}: options are not allowed ({line.split()[0]}): the host installs offline from wheels/")
            continue
        m = PIN.fullmatch(line)
        if not m:
            errors.append(f"line {n}: {line[:60]!r} is not `name==version --hash=sha256:...`")
            continue
        name, ver, rest = norm(m.group(1)), m.group(3), m.group(4)
        if ";" in rest.split("--hash")[0]:
            errors.append(f"line {n}: environment markers are not allowed; ship wheels for every platform instead")
        hashes = HASH.findall(rest)
        if not hashes:
            errors.append(f"line {n}: {name}=={ver} has no --hash=sha256 (generate with `uv pip compile --generate-hashes`)")
        if name in host_provided():
            errors.append(f"line {n}: {name} is provided by the host (the SDK's own dependencies); do not pin it")
        out.append({"name": name, "version": ver, "hashes": set(hashes)})
    if errors:
        raise DepsError("; ".join(errors))
    return out


def wheel_tags(filename: str) -> dict | None:
    m = WHEEL.fullmatch(filename)
    if not m:
        return None
    return {"dist": norm(m["dist"]), "version": m["ver"], "py": m["py"].split("."), "abi": m["abi"].split("."),
            "plat": m["plat"].split(".")}


def _plat_ok(tag: str, platform: str) -> bool:
    os_, arch = portable.split_platform(platform)
    if tag == "any":
        return True
    if os_ == "darwin":
        return tag.startswith("macosx_") and (tag.endswith("_universal2") or tag.endswith(
            "_arm64" if arch == "arm64" else "_x86_64") or (arch == "amd64" and tag.endswith("_intel")))
    if os_ == "linux":
        a = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch)
        return (tag.startswith(("manylinux", "musllinux", "linux_"))) and tag.endswith("_" + a)
    if os_ == "windows":
        return tag == {"amd64": "win_amd64", "arm64": "win_arm64"}.get(arch)
    return False


def _py_ok(t: dict, py: str = PY_TAG) -> bool:
    """Pure wheels (py3/none), wheels for exactly this CPython, and abi3 wheels for this CPython or older."""
    mine = int(py[2:])
    for p in t["py"]:
        for a in t["abi"]:
            if p.startswith("py3") and a == "none":
                return True
            if p == py and a in (py, "none", "abi3"):
                return True
            if a == "abi3" and p.startswith("cp3") and p[2:].isascii() and p[2:].isdigit() and int(p[2:]) <= mine:
                return True
    return False


def wheel_fits(filename: str, platform: str, py: str = PY_TAG) -> bool:
    t = wheel_tags(filename)
    return bool(t) and _py_ok(t, py) and any(_plat_ok(p, platform) for p in t["plat"])


def _runner_file(root: Path, argv: list[str]) -> Path | None:
    script = next((a for a in argv[1:] if a.startswith("{bundle}/") and a.endswith(".py")), None)
    return root / Path(script[len("{bundle}/"):]).parent / "requirements.txt" if script else None


def requirement_files(root: Path, man) -> list[Path]:
    """The bundle's requirements files: the coordinator's (root) and the runner's (beside the runner script)."""
    root = Path(root)
    out = [root / "requirements.txt"] if (root / "requirements.txt").is_file() else []
    for argv in [man.runner.exec] + [v.exec for v in man.runner.variants.values() if v.exec]:
        p = _runner_file(root, argv)
        if p is not None and p.is_file() and p not in out:
            out.append(p)
    return out


def file_platforms(root: Path, man, req_file: Path) -> list[str]:
    """The platforms that install a requirements file: for the runner's, every node platform whose runner (its variant
    applied) runs the script beside it; for the coordinator's (the bundle root's), requires.coordinator_platforms, else
    requires.platforms (any coordinator platform: the node platforms stand in). A file that is both serves both."""
    root, want = Path(root), Path(req_file).resolve()
    out = set()
    if want == (root / "requirements.txt").resolve():
        out |= set(man.requires.coordinator_platforms or man.requires.platforms)
    for plat in man.requires.platforms:
        p = _runner_file(root, man.runner.for_platform(plat).exec)
        if p is not None and p.resolve() == want:
            out.add(plat)
    return sorted(out)


def check(root: Path, man) -> list[str]:
    """Problems with a module directory's dependencies (empty: fine). Every pinned distribution needs a wheel in
    wheels/ whose sha256 is one of its hashes, for every platform the requirement applies to."""
    root = Path(root)
    issues = []
    wheels = {p.name: p for p in (root / WHEELS_DIR).glob("*.whl")} if (root / WHEELS_DIR).is_dir() else {}
    if (root / WHEELS_DIR).is_dir() and any(not p.name.endswith(".whl") for p in (root / WHEELS_DIR).iterdir()
                                            if p.is_file() and not p.name.startswith(".")):
        issues.append("wheels/ may hold only .whl files (source distributions are refused)")
    digests = {}
    for req_file in requirement_files(root, man):
        rel = req_file.relative_to(root).as_posix()
        try:
            reqs = parse_requirements(req_file.read_text(encoding="utf-8"))
        except DepsError as e:
            issues.append(f"{rel}: {e}")
            continue
        for r in reqs:
            for plat in file_platforms(root, man, req_file):
                cands = [n for n, p in wheels.items() if (wheel_tags(n) or {}).get("dist") == r["name"]
                         and (wheel_tags(n) or {}).get("version") == r["version"] and wheel_fits(n, plat)]
                if not cands:
                    issues.append(f"{rel}: no wheel in wheels/ for {r['name']}=={r['version']} on {plat}"
                                  " (oarbank-sdk bundle wheels)")
                    continue
                for n in cands:
                    if n not in digests:
                        digests[n] = hashlib.sha256(wheels[n].read_bytes()).hexdigest()
                if not any(digests[n] in r["hashes"] for n in cands):
                    issues.append(f"{rel}: the wheel for {r['name']}=={r['version']} on {plat} matches none of its hashes")
    return issues


def install_args(bundle: Path, req_file: Path) -> list[str]:
    """`uv pip install` arguments every host uses: offline, wheels only, hashes required, nothing resolved."""
    return ["--offline", "--no-index", "--find-links", str(Path(bundle) / WHEELS_DIR), "--require-hashes",
            "--only-binary", ":all:", "--no-deps", "--no-cache", "-r", str(req_file)]


def download(root: Path, man, python_version: str = "3.12") -> list[str]:
    """Fill wheels/ with a wheel per pinned distribution and platform that installs its file (a build-machine tool: it
    uses pip and the network). Returns the platforms it fetched for."""
    root = Path(root)
    (root / WHEELS_DIR).mkdir(exist_ok=True)
    tags = {"darwin-arm64": ["macosx_11_0_arm64", "macosx_14_0_arm64", "macosx_11_0_universal2"],
            "darwin-amd64": ["macosx_10_12_x86_64", "macosx_11_0_x86_64", "macosx_10_9_universal2"],
            "linux-amd64": ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"],
            "linux-arm64": ["manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"],
            "windows-amd64": ["win_amd64"], "windows-arm64": ["win_arm64"]}
    done = []
    for req_file in requirement_files(root, man):
        for plat in file_platforms(root, man, req_file):
            cmd = [sys.executable, "-m", "pip", "download", "--quiet", "--no-deps", "--only-binary", ":all:",
                   "--require-hashes", "--python-version", python_version, "--implementation", "cp",
                   "-d", str(root / WHEELS_DIR), "-r", str(req_file)]
            for t in tags.get(plat, []):
                cmd += ["--platform", t]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 and "No module named pip" in (r.stderr or ""):
                raise DepsError("`bundle wheels` needs pip in this environment (uv venvs have none): "
                                "uv run --with pip oarbank-sdk bundle wheels <dir>")
            if r.returncode != 0:
                raise DepsError(f"pip download for {plat} failed (pin versions that publish wheels for every declared "
                                f"platform; compile with --only-binary :all:): {(r.stderr or r.stdout)[-600:]}")
            done.append(plat)
    return sorted(set(done))


# ---------------------------------------------------------------------------- compile


def _uv() -> str:
    uv = os.environ.get("UV") or shutil.which("uv")          # `uv run` sets UV to itself
    if not uv:
        raise DepsError("deps compile needs uv (https://docs.astral.sh/uv/) on PATH")
    return uv


def _uv_compile(src: Path, plat: str, extra: list[str]) -> subprocess.CompletedProcess:
    cmd = [_uv(), "pip", "compile", str(src), "--python-platform", UV_TARGETS[plat], "--python-version", PY_VERSION,
           "--only-binary", ":all:", "--no-header", "--no-annotate", "--quiet", *extra]
    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": MACOS_TARGET} if plat.startswith("darwin-") else None
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _tail(r: subprocess.CompletedProcess) -> str:
    return " ".join((r.stderr or r.stdout).split())[-600:]


def compile(root: Path, man, src: Path, out: Path | None = None, uv_args: tuple[str, ...] = ()) -> dict:
    """Resolve `src` (a requirements.in) once per platform that installs `out` (default: requirements.txt beside `src`)
    with uv, wheels only, CPython 3.12, leaving out what the host provides; unify the results into one marker-free,
    hash-pinned file and write it. DepsError, naming packages and platforms, when the platforms resolve different
    versions, or when a package only some platforms need has no wheel on the others (every platform installs every
    line). Returns {out, platforms, pins, partial (package -> platforms that need it), host_provided}."""
    root, src = Path(root), Path(src)
    out = Path(out) if out else src.with_name("requirements.txt")
    plats = file_platforms(root, man, out)
    if not plats:
        raise DepsError(f"{out}: neither the coordinator's requirements.txt (the bundle root) nor the one beside a runner "
                        "script, so no platform installs it")
    unknown = [p for p in plats if p not in UV_TARGETS]
    if unknown:
        raise DepsError(f"no wheel target known for {unknown}; known: {sorted(UV_TARGETS)}")
    host = sorted(host_provided())
    skip = [a for h in host for a in ("--no-emit-package", h)]
    per = {}
    for plat in plats:
        r = _uv_compile(src, plat, ["--generate-hashes", *skip, *uv_args])
        if r.returncode:
            raise DepsError(f"uv cannot resolve {src.name} for {plat} (wheels only, CPython {PY_VERSION}): {_tail(r)}")
        per[plat] = {d["name"]: d for d in parse_requirements(r.stdout)}
    conflicts, pins, partial = [], {}, {}
    for name in sorted(set().union(*per.values())):
        got = {p: per[p][name] for p in plats if name in per[p]}
        versions: dict = {}
        for p, d in got.items():
            versions.setdefault(d["version"], []).append(p)
        if len(versions) > 1:
            conflicts.append(f"{name}: " + "; ".join(f"{v} on {', '.join(ps)}" for v, ps in sorted(versions.items())))
            continue
        pins[name] = (next(iter(versions)), set().union(*(d["hashes"] for d in got.values())))
        if len(got) < len(plats):
            partial[name] = sorted(got)
    if conflicts:
        raise DepsError(f"the platforms resolve {src.name} to different versions; pin one version that has wheels for "
                        f"every platform in {src.name}:\n  " + "\n  ".join(conflicts))
    missing = []
    for plat in plats:
        need = [n for n, ps in partial.items() if plat not in ps]
        if not need:
            continue
        with tempfile.TemporaryDirectory() as td:
            pinned = Path(td) / "pinned.in"
            pinned.write_text("".join(f"{n}=={pins[n][0]}\n" for n in need), encoding="utf-8")
            r = _uv_compile(pinned, plat, ["--no-deps", *uv_args])
        if r.returncode:
            missing.append(f"{', '.join(f'{n}=={pins[n][0]} (needed on {', '.join(partial[n])})' for n in need)} on {plat}: "
                           f"{_tail(r)}")
    if missing:
        raise DepsError("a package only some platforms need has no wheel on the others (the file has no markers, so every "
                        "platform installs every line):\n  " + "\n  ".join(missing))
    lines = [f"# Generated by `oarbank-sdk deps compile` from {src.name} for {', '.join(plats)} (CPython {PY_VERSION}, wheels only).",
             "# Every platform installs every line (no markers). Provided by the host, never pinned:",
             f"#   {', '.join(host)}"]
    for name, (ver, hashes) in pins.items():
        if name in partial:
            lines.append(f"# {name}: needed on {', '.join(partial[name])} (installed everywhere)")
        lines.append(f"{name}=={ver} \\")
        lines += [f"    --hash=sha256:{h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(sorted(hashes))]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"out": out, "platforms": plats, "pins": len(pins), "partial": partial, "host_provided": host}
