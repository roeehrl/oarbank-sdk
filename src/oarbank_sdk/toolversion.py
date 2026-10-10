"""Host tool versions and version constraints (spec/sandbox.md, "Host tools"). Stdlib only.

A module asks for a host tool by its fleet definition id with an optional version constraint
(`[sandbox].tools = [{id = "jdk", version = ">=17, <22"}]`). The node detects each installation's version; the agent
and the coordinator grant only an installation whose version satisfies the constraint. Every implementation (this
module, the coordinator, the agent) reproduces spec/vectors/tool-versions.json.

**Versions** are dot-separated numbers with an optional pre-release (`-ea`, `-rc.1`) and build (`+7`) suffix:
`17`, `17.0.12`, `21.0.4+7`, `22-ea`. Missing segments are zero (`17` equals `17.0.0`); a pre-release sorts below its
release; build metadata is ignored. Java's legacy scheme is read as the version it names: `1.8.0_392` is `8.0.392`
(`_` separates segments).

**Constraints** are comma-separated clauses, each an operator and a version, all of which must hold (HashiCorp
Nomad's `version` operator): `=` (or `==`), `!=`, `>`, `>=`, `<`, `<=` and `~>`, the pessimistic operator, which allows
the last given segment to grow: `~> 17.0.2` is `>= 17.0.2, < 17.1`, `~> 17.0` is `>= 17.0, < 18`, and `~> 17` is
`>= 17, < 18`.
"""
import re

OPERATORS = ("=", "==", "!=", ">", ">=", "<", "<=", "~>")
_VERSION = re.compile(r"^(\d+(?:[._]\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")
_CLAUSE = re.compile(r"^\s*(~>|==|!=|>=|<=|=|>|<)\s*(\S+)\s*$")
ARCHES = ("any", "native", "arm64", "amd64")
_ARCH_ALIASES = {"aarch64": "arm64", "arm64": "arm64", "arm64e": "arm64", "x86_64": "amd64", "amd64": "amd64",
                 "x64": "amd64", "x86-64": "amd64"}


class Version:
    """A parsed version: numeric segments, an optional pre-release; ordered as described in the module docstring."""
    __slots__ = ("nums", "pre", "text")

    def __init__(self, text: str):
        m = _VERSION.fullmatch((text or "").strip())
        if not m:
            raise ValueError(f"{text!r} is not a version (numbers separated by dots, e.g. 17.0.12)")
        nums = [int(x) for x in re.split(r"[._]", m.group(1))]
        if len(nums) > 1 and nums[0] == 1 and nums[1] <= 9:     # Java's legacy 1.8.0_392 is version 8
            nums = nums[1:]
        self.nums, self.pre, self.text = tuple(nums), m.group(2), text.strip()

    def _key(self, width: int):
        nums = self.nums + (0,) * (width - len(self.nums))
        pre = () if self.pre is None else tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.pre.split("."))
        return nums, self.pre is None, pre

    def compare(self, other: "Version") -> int:
        w = max(len(self.nums), len(other.nums))
        a, b = self._key(w), other._key(w)
        return (a > b) - (a < b)

    def __str__(self):
        return ".".join(map(str, self.nums)) + (f"-{self.pre}" if self.pre else "")


def version(text: str) -> Version:
    return Version(text)


def normalize(text: str) -> str:
    """A version as nodes report it: its segments joined by dots, Java's legacy scheme read (`1.8.0_392` → `8.0.392`)."""
    return str(Version(text))


def parse_constraint(text: str) -> list[tuple[str, Version]]:
    """[(operator, version)] of a constraint; ValueError says what is wrong."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a version constraint is not empty (leave `version` out for any version)")
    out = []
    for part in text.split(","):
        m = _CLAUSE.fullmatch(part)
        if not m:
            raise ValueError(f"{part.strip()!r} in {text!r} is not an operator and a version ({', '.join(OPERATORS)}; "
                             "e.g. \">=17, <22\")")
        out.append(("=" if m.group(1) == "==" else m.group(1), Version(m.group(2))))
    return out


def describe(text: str) -> str:
    """A constraint in its canonical spelling: `>=17,<22` → `>=17, <22`."""
    return ", ".join(f"{op}{v.text}" for op, v in parse_constraint(text))


def _clause(v: Version, op: str, c: Version) -> bool:
    if op == "~>":
        if v.compare(c) < 0:
            return False
        upper = list(c.nums[:-1] if len(c.nums) > 1 else c.nums)
        upper[-1] += 1
        return v.compare(Version(".".join(map(str, upper)))) < 0
    r = v.compare(c)
    return {"=": r == 0, "!=": r != 0, ">": r > 0, ">=": r >= 0, "<": r < 0, "<=": r <= 0}[op]


def satisfies(ver: str, constraint: str | None) -> bool:
    """Does the version satisfy the constraint (None or "": any version)? An unparseable version satisfies only no
    constraint."""
    if not constraint:
        return True
    try:
        v = Version(ver)
    except ValueError:
        return False
    return all(_clause(v, op, c) for op, c in parse_constraint(constraint))


def arch(raw: str | None) -> str:
    """An architecture as Oarbank's platform tokens name it: `aarch64` → `arm64`, `x86_64` → `amd64`; others lower-cased."""
    r = (raw or "").strip().lower()
    return _ARCH_ALIASES.get(r, r)


def arch_fits(installed: str | None, want: str, native: str) -> bool:
    """Does an installation of arch `installed` fit a request's `arch` (any, native, arm64, amd64) on a node whose
    native arch is `native`? An installation of unknown arch fits only `any`."""
    have = arch(installed)
    if want in ("", "any"):
        return True
    if not have:
        return False
    return have == (arch(native) if want == "native" else want)
