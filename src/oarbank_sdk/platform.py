"""Per-platform declarations and placement classes (spec/platforms.md, "Per-platform declarations" and "Placement").

    from oarbank_sdk import platform as pf
    pf.current()                                   # "linux-amd64": OARBANK_PLATFORM, else this machine
    pf.resolve({"linux": 2, "linux-amd64": 3}, "linux-arm64", default=1)     # 2: token, then OS, then default
    pf.class_key("linux-amd64", "same-os")         # "linux"
    pf.stricter("same-os", "same-arch")            # "same-platform"

Every rule here is pinned by spec/vectors/variant-resolution.json and spec/vectors/placement-class.json, which the
coordinator and the agents reproduce.
"""
import os

from . import portable

ENV_PLATFORM = "OARBANK_PLATFORM"

# ---------------------------------------------------------------------------- the current platform


def current() -> str:
    """The platform this code runs on: OARBANK_PLATFORM (set by the agent for runners and by the host for the coordinator
    side) when it is a well-formed token, else this machine's (portable.host_platform)."""
    v = os.environ.get(ENV_PLATFORM, "")
    return v if portable.is_platform_token(v) else portable.host_platform()


def os_(token: str | None = None) -> str:
    """The OS part of a token (default: the current platform)."""
    return portable.split_platform(token or current())[0]


def arch(token: str | None = None) -> str:
    """The arch part of a token (default: the current platform)."""
    return portable.split_platform(token or current())[1]


def is_key(key: str) -> bool:
    """A per-platform table key: a platform token (`linux-amd64`) or an OS name (`linux`)."""
    return isinstance(key, str) and bool(portable.PLATFORM_KEY.fullmatch(key))


def matches(token: str, keys) -> bool:
    """True when `token` is listed in `keys` by its token or by its OS. An empty list matches every platform."""
    keys = list(keys or [])
    return not keys or token in keys or portable.split_platform(token)[0] in keys


def declared(key: str, tokens) -> bool:
    """True when `key` names one of `tokens` or the OS of one (the rule for variant and file-subset keys)."""
    tokens = set(tokens)
    return key in tokens or key in {portable.split_platform(t)[0] for t in tokens}


# ---------------------------------------------------------------------------- resolution (most specific wins)


def variant_keys(token: str) -> tuple[str, str]:
    """The keys that apply to `token`, least specific first: its OS, then the token itself."""
    return portable.split_platform(token)[0], token


def resolve(mapping: dict, token: str, default=None):
    """The value for `token` in a per-platform table: the token's entry, else its OS's entry, else `default`."""
    mapping = mapping or {}
    for key in reversed(variant_keys(token)):
        if key in mapping:
            return mapping[key]
    return default


# Tables of named values that a variant merges key by key instead of replacing: environment variables, per-verb
# timeouts, and a stage's requires (whose resources merge per field). Every other field a variant sets replaces the
# base value whole (exec, runtime, capabilities, gpu, retry, ...).
MERGED = ("env", "timeouts_s", "requires", "requires.resources")


def overlay(base: dict, *layers: dict, merged=MERGED, _path: str = "") -> dict:
    """`base` with each layer applied in order (a layer is one variant's fields, as written). See MERGED."""
    out = dict(base)
    for layer in layers:
        for k, v in (layer or {}).items():
            path = f"{_path}{k}"
            if path in merged and isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = overlay(out[k], v, merged=merged, _path=path + ".")
            else:
                out[k] = v
    return out


def apply_variants(base: dict, variants: dict, token: str) -> dict:
    """The resolved view of a section for `token`: its OS's variant, then its own, over `base`."""
    return overlay(base, *[variants[k] for k in variant_keys(token) if k in (variants or {})])


# ---------------------------------------------------------------------------- placement classes

MIXES = ("any", "same-os", "same-arch", "same-platform")
STRICTEST = "same-platform"
# The class keys a determinism scope compares within, as a mix (results.determinism_scope).
SCOPE_MIX = {"global": "any", "os": "same-os", "arch": "same-arch", "platform": "same-platform"}


def known_mix(mix: str) -> bool:
    return mix in MIXES


def normalize(mix: str | None) -> str:
    """A mix as the scheduler applies it: absent is `any`; an unknown value is the strictest (fail safe)."""
    if mix is None:
        return "any"
    return mix if mix in MIXES else STRICTEST


def scope_mix(scope: str | None) -> str:
    """The mix a results.determinism_scope implies (an unknown scope is the strictest)."""
    return SCOPE_MIX.get(scope or "global", STRICTEST)


def class_key(token: str, mix: str | None) -> str:
    """The platform class of `token` under `mix`: `*` (any), the OS (same-os), the arch (same-arch) or the token."""
    m = normalize(mix)
    o, a = portable.split_platform(token)
    return {"any": "*", "same-os": o, "same-arch": a}.get(m, token)


def strictness(mix: str | None) -> int:
    """0 for any, 1 for same-os and same-arch (not comparable with each other), 2 for same-platform."""
    return {"any": 0, "same-os": 1, "same-arch": 1}.get(normalize(mix), 2)


def stricter(a: str | None, b: str | None) -> str:
    """The mix that satisfies both: the stricter one; same-os with same-arch is same-platform."""
    a, b = normalize(a), normalize(b)
    if a == b or strictness(b) < strictness(a):
        return a
    if strictness(a) < strictness(b):
        return b
    return STRICTEST                              # same-os with same-arch


def looser(a: str | None, b: str | None) -> bool:
    """True when mix `a` allows a unit to span classes that `b` keeps apart (a is not at least as strict as b)."""
    return stricter(a, b) != normalize(a)


def feasible_classes(platforms, stage_platforms=(), job_platforms=(), mix: str | None = None) -> list[str]:
    """The classes a unit of work may bind to: the module's node platforms, kept where every stage the unit runs can run
    (each list of `stage_platforms`; an empty list means every platform) and where the job's platforms allow (tokens or
    OSes; empty means every platform), grouped by `mix`. Sorted."""
    ok = [p for p in dict.fromkeys(platforms) if all(matches(p, sp) for sp in stage_platforms) and matches(p, job_platforms)]
    return sorted({class_key(p, mix) for p in ok})


def feasible(manifest, stages=None, *, platforms=(), mix: str | None = None) -> list[str]:
    """`feasible_classes` for a manifest: `stages` names the stages the unit runs (default: every stage), `platforms`
    are the job's or dataset's platforms, and `mix` defaults to the manifest's [placement].mix (`any` when absent)."""
    by_name = {s.name: s for s in manifest.stages}
    names = list(stages) if stages else list(by_name)
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"unknown stages {unknown}")
    if mix is None:
        mix = manifest.placement.mix if manifest.placement else "any"
    return feasible_classes(manifest.requires.platforms, [by_name[n].requires.platforms for n in names], platforms, mix)
