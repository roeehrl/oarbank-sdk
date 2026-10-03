"""Export the JSON Schemas (draft 2020-12) of every public contract into schemas/.

The schemas are generated from the Pydantic models, committed, and checked for freshness in CI
(tests/test_schemas.py), so the models and the published schemas can never drift apart.
"""
import json
from pathlib import Path

from . import envelopes, manifest, module_protocol as mp, runner_protocol as rp, service_protocol as sp, ui

_REPO = Path(__file__).resolve().parents[2]
_DATA = Path(__file__).resolve().parent / "_data"          # where an installed wheel carries schemas/ and spec/
ROOT = _REPO / "schemas" if (_REPO / "schemas").is_dir() else _DATA / "schemas"
BASE = "https://codonic.dev/oarbank/schemas/"

CONTRACTS: dict[str, type] = {
    "manifest-1": manifest.Manifest,
    "ui-page-1": ui.Page,
    "spec-envelope-1": envelopes.SpecEnvelope,
    "result-envelope-1": envelopes.ResultEnvelope,
    "runner-doctor-1": rp.DoctorOutput,
    "runner-event-1": rp.Event,
    "runner-control-1": rp.Control,
    "runner-failure-1": rp.Failure,
    "service-fingerprint-1": sp.Fingerprint,
    "service-status-1": sp.Status,
    "service-owned-1": sp.OwnedList,
    "service-op-result-1": sp.OpResult,
    "module-rpc-request-1": mp.Request,
    "module-rpc-response-1": mp.Response,
    "module-rpc-notification-1": mp.Notification,
    "module-initialize-params-1": mp.InitializeParams,
    "module-initialize-result-1": mp.InitializeResult,
}
for method, (params, result, _req, _cap) in mp.VERBS.items():
    slug = method.replace(".", "-")
    CONTRACTS[f"module-{slug}-params-1"] = params
    CONTRACTS[f"module-{slug}-result-1"] = result
for method, (params, result, _perm) in mp.HOST_CALLBACKS.items():
    slug = method.replace(".", "-")
    CONTRACTS[f"module-{slug}-params-1"] = params
    CONTRACTS[f"module-{slug}-result-1"] = result


def schema_for(name: str) -> dict:
    s = CONTRACTS[name].model_json_schema(by_alias=True, mode="validation")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": BASE + name + ".schema.json", **s}


def strict(schema: dict) -> dict:
    """The authoring variant: every object forbids unknown properties, so editors flag typos the way
    `oarbank-sdk check` does. The core itself reads with the lenient schema."""
    def walk(n):
        if isinstance(n, dict):
            if n.get("additionalProperties") is True:
                n["additionalProperties"] = False
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    s = json.loads(json.dumps(schema))
    walk(s)
    s["$id"] = s["$id"].replace(".schema.json", ".strict.schema.json")
    return s


STRICT = ("manifest-1", "ui-page-1")     # contracts authors write by hand


def render_all() -> dict[str, str]:
    dump = lambda d: json.dumps(d, indent=2, sort_keys=True) + "\n"
    out = {f"{n}.schema.json": dump(schema_for(n)) for n in sorted(CONTRACTS)}
    out.update({f"{n}.strict.schema.json": dump(strict(schema_for(n))) for n in STRICT})
    return out


def export(root: Path = ROOT) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    written = []
    rendered = render_all()
    for f in root.glob("*.schema.json"):
        if f.name not in rendered:
            f.unlink()
    for name, text in rendered.items():
        p = root / name
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8", newline="\n")
            written.append(name)
    ref = render_reference()
    if not REFERENCE.exists() or REFERENCE.read_text(encoding="utf-8") != ref:
        REFERENCE.write_text(ref, encoding="utf-8", newline="\n")
        written.append(REFERENCE.name)
    return written


# ---------------------------------------------------------------------------- generated field reference

REFERENCE = (_REPO / "spec" if (_REPO / "spec").is_dir() else _DATA / "spec") / "manifest-reference.md"


def _type_name(ann) -> str:
    import typing
    origin = typing.get_origin(ann)
    if origin is typing.Annotated:
        return _type_name(typing.get_args(ann)[0])
    if origin is typing.Literal:
        return " \\| ".join(f"`{a!r}`".replace("'", '"') for a in typing.get_args(ann))
    if origin in (list, tuple):
        return "list of " + _type_name(typing.get_args(ann)[0])
    if origin is dict:
        k, v = typing.get_args(ann)
        return f"table {_type_name(k)} → {_type_name(v)}"
    if origin in (typing.Union, __import__("types").UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return " or ".join(_type_name(a) for a in args) + (" (optional)" if len(args) < len(typing.get_args(ann)) else "")
    return getattr(ann, "__name__", str(ann))


def render_reference() -> str:
    from pydantic import BaseModel
    from pydantic_core import PydanticUndefined
    rows = []

    def walk(model, prefix):
        for name, f in model.model_fields.items():
            key = f.alias or name
            path = f"{prefix}{key}"
            ann = f.annotation
            default = "required" if f.is_required() else (
                "" if f.default in (PydanticUndefined, None) and f.default_factory is None else
                f"`{json.dumps(f.default_factory() if f.default_factory else f.default, default=str)}`")
            if default.startswith("`{") and len(default) > 40:
                default = "see fields"
            desc = (f.description or "").replace("|", "\\|")
            rows.append(f"| `{path}` | {_type_name(ann)} | {default} | {desc} |")
            sub = _submodel(ann)
            if sub is not None:
                walk(sub, path + ("[]." if _is_list(ann) else "."))

    def _is_list(ann):
        import typing
        return typing.get_origin(ann) in (list, tuple)

    def _submodel(ann):
        import typing
        for a in [ann, *typing.get_args(ann)]:
            if isinstance(a, type) and issubclass(a, BaseModel):
                return a
            if typing.get_origin(a) in (list, tuple):
                inner = typing.get_args(a)[0]
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    return inner
        return None

    walk(manifest.Manifest, "")
    head = ("# Manifest field reference (generated)\n\n"
            "Generated from `oarbank_sdk.manifest` by `oarbank-sdk export-schemas`. Do not edit by hand.\n"
            "Cross-field rules are in [manifest.md](manifest.md).\n\n"
            "| Key | Type | Default | Description |\n|---|---|---|---|\n")
    return head + "\n".join(rows) + "\n"
