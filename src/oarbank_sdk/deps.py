"""A module's Python dependencies (spec/bundles.md, "Dependencies"): hash-pinned requirements and the wheels for them,
shipped inside the bundle so that installing a module downloads nothing and runs none of its code.

- `requirements.txt` (coordinator, bundle root) and a `requirements.txt` beside the runner script list every
  distribution, transitive ones included, as `name==version --hash=sha256:<64 hex>` (pip's hash-checking mode; the
  output of `uv pip compile --generate-hashes`).
- `wheels/` holds a wheel for every pinned distribution and every declared platform (a `py3-none-any` wheel covers
  all). Source distributions are refused: building one runs arbitrary code.
- Hosts install with `--offline --no-index --find-links <bundle>/wheels --require-hashes --only-binary :all:
  --no-deps`, inside the module sandbox where the host has one.

`oarbank-sdk bundle wheels` downloads the wheels for the declared platforms into `wheels/`.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from . import portable

WHEELS_DIR = "wheels"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([A-Za-z0-9.+!_-]+)(.*)$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
WHEEL = re.compile(r"^(?P<dist>[^-]+)-(?P<ver>[^-]+)(-\d[^-]*)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")
HOST_PROVIDED = {"oarbank-sdk", "pydantic", "pydantic-core", "annotated-types", "typing-extensions", "typing-inspection"}
PY_TAG = "cp312"                                  # the hosts' managed CPython (spec/manifest.md, runtime kind python)


class DepsError(ValueError):
    pass


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
        if name in HOST_PROVIDED:
            errors.append(f"line {n}: {name} is provided by the host; do not pin it")
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


def requirement_files(root: Path, man) -> list[Path]:
    """The bundle's requirements files: the coordinator's (root) and the runner's (beside the runner script)."""
    root = Path(root)
    out = [root / "requirements.txt"] if (root / "requirements.txt").is_file() else []
    for argv in [man.runner.exec] + [v.exec for v in man.runner.variants.values() if v.exec]:
        script = next((a for a in argv[1:] if a.startswith("{bundle}/") and a.endswith(".py")), None)
        if script:
            p = root / Path(script[len("{bundle}/"):]).parent / "requirements.txt"
            if p.is_file() and p not in out:
                out.append(p)
    return out


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
            for plat in man.requires.platforms:
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
    """Fill wheels/ with a wheel per pinned distribution and declared platform (a build-machine tool: it uses pip and
    the network). Returns the platforms it fetched for."""
    root = Path(root)
    (root / WHEELS_DIR).mkdir(exist_ok=True)
    tags = {"darwin-arm64": ["macosx_11_0_arm64", "macosx_14_0_arm64", "macosx_11_0_universal2"],
            "darwin-amd64": ["macosx_10_12_x86_64", "macosx_11_0_x86_64", "macosx_10_9_universal2"],
            "linux-amd64": ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"],
            "linux-arm64": ["manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"],
            "windows-amd64": ["win_amd64"], "windows-arm64": ["win_arm64"]}
    done = []
    for req_file in requirement_files(root, man):
        for plat in man.requires.platforms:
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
