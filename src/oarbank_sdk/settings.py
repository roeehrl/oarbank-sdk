"""A module's settings (spec/manifest.md, "Settings"): the keys a module declares in its `[settings].schema` file, and
what each runner and coordinator verb receives.

The schema file is a JSON Schema object whose `properties` are the module's settings. Each property is one key: its
JSON Schema (type, `default`, `minimum`, `enum`, `pattern`, ... ) is what the core validates every value against, its
`title` and `description` are what the owner reads, and its `x-oarbank` annotation says where the key may be set:

    "vm_mem_gb": {"type": "number", "minimum": 2, "maximum": 64, "default": 8,
                  "title": "VM memory", "description": "Memory the module's VM gets on a node.",
                  "x-oarbank": {"scope": "node", "unit": "GB"}}

- `scope`: `fleet` (the default) is one value for the whole fleet, read by the module's coordinator side
  (`host.settings.get`, `ctx.settings`); `node` is a value per node, set for the fleet, a node group or one node, and
  handed to the module's runners and services on that node in `OARBANK_SETTINGS_FILE`.
- `required`: the owner must set it before the module's work runs (also accepted: the schema's own `required` list).
  A required key has no default.
- `unit` (a short word beside the value), `advanced` (shown under the section's Advanced disclosure).

Credentials are never settings: settings are stored and shown in clear. Declare them in `[[secrets]]`.

`load(root, man)` reads and checks the file (`oarbank-sdk check`, `bundle build` and `bundle verify` refuse an invalid
one), `effective(keys, values, scope)` is what a runner or coordinator verb receives for given owner values."""
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCOPES = ("fleet", "node")
TYPES = ("boolean", "integer", "number", "string", "array", "object")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ANNOTATIONS = {"scope", "required", "unit", "advanced"}
MAX_KEYS = 128
# keys the core already has per module: the owner sets them as `[module] <key>` (spec/manifest.md, "Settings")
CORE_QUALIFIED = ("enabled", "pipeline", "replica_rate", "services")
CREDENTIAL = re.compile(r"(^|_)(token|password|passwd|secret|api_?key|private_?key|credentials?)($|_)")
_MISSING = object()


class SettingsSchemaError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class SettingKey:
    """One declared setting."""
    name: str
    schema: dict                      # the property's JSON Schema, without the x-oarbank annotation
    type: str                         # the non-null JSON type
    nullable: bool
    scope: str = "fleet"
    required: bool = False
    label: str = ""
    help: str = ""
    unit: str | None = None
    advanced: bool = False
    default: Any = None
    has_default: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "schema": self.schema, "type": self.type, "nullable": self.nullable, "scope": self.scope,
                "required": self.required, "label": self.label, "help": self.help, "unit": self.unit,
                "advanced": self.advanced, "default": self.default, "has_default": self.has_default}

    @classmethod
    def from_dict(cls, d: dict) -> "SettingKey":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class SettingsSchema:
    keys: list[SettingKey] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def key(self, name: str) -> SettingKey | None:
        return next((k for k in self.keys if k.name == name), None)


def _validator(schema: dict):
    import jsonschema
    return jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)(schema)


def check_value(k: SettingKey, value) -> str | None:
    """None when `value` is valid for the key, else what is wrong (the first error, in the owner's words)."""
    err = next(iter(_validator(k.schema).iter_errors(value)), None)
    if err is None:
        return None
    where = "/".join(map(str, err.absolute_path))
    return (f"{where}: " if where else "") + err.message


def parse(doc, source: str = "settings schema") -> SettingsSchema:
    """The keys a settings schema declares, or SettingsSchemaError with every problem at once."""
    import jsonschema
    errors, warnings, keys = [], [], []
    if not isinstance(doc, dict):
        raise SettingsSchemaError([f"{source}: a JSON object (a JSON Schema with `properties`)"])
    if doc.get("type", "object") != "object":
        errors.append(f"{source}: type must be \"object\"")
    props = doc.get("properties") or {}
    if not isinstance(props, dict):
        raise SettingsSchemaError([f"{source}: properties must be an object"])
    if len(props) > MAX_KEYS:
        errors.append(f"{source}: at most {MAX_KEYS} settings")
    top_required = doc.get("required") or []
    if not isinstance(top_required, list) or not all(isinstance(x, str) for x in top_required):
        errors.append(f"{source}: required must be a list of property names")
        top_required = []
    for name in top_required:
        if name not in props:
            errors.append(f"{source}: required names {name!r}, which is not a property")
    for name, p in props.items():
        where = f"{source}: {name}"
        if not NAME.fullmatch(name):
            errors.append(f"{where}: a setting's name is lower-case letters, digits and _, starting with a letter (at "
                          "most 64)")
            continue
        if not isinstance(p, dict):
            errors.append(f"{where}: a JSON Schema object")
            continue
        p = dict(p)
        ann = p.pop("x-oarbank", {})
        if not isinstance(ann, dict):
            errors.append(f"{where}: x-oarbank must be an object")
            continue
        unknown = sorted(set(ann) - ANNOTATIONS)
        if unknown:
            errors.append(f"{where}: unknown x-oarbank fields {unknown} (known: {', '.join(sorted(ANNOTATIONS))})")
        if p.get("writeOnly") or p.get("x-secret") or ann.get("secret"):
            errors.append(f"{where}: a credential is never a setting (settings are shown in clear): declare it in "
                          "[[secrets]] and the owner sets it with `oarbank secret set`")
            continue
        t = p.get("type")
        ts = t if isinstance(t, list) else [t]
        real = [x for x in ts if x != "null"]
        if len(real) != 1 or real[0] not in TYPES or any(x not in (*TYPES, "null") for x in ts):
            errors.append(f"{where}: type must be one of {', '.join(TYPES)} (optionally with \"null\"), got {t!r}")
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(p)
        except jsonschema.SchemaError as e:
            errors.append(f"{where}: not a valid JSON Schema: {e.message}"[:300])
            continue
        scope = ann.get("scope", "fleet")
        if scope not in SCOPES:
            errors.append(f"{where}: x-oarbank.scope must be fleet or node, got {scope!r}")
            continue
        required = ann.get("required", name in top_required)
        if not isinstance(required, bool):
            errors.append(f"{where}: x-oarbank.required must be true or false")
            continue
        unit = ann.get("unit")
        if unit is not None and (not isinstance(unit, str) or not 0 < len(unit) <= 16):
            errors.append(f"{where}: x-oarbank.unit is a short word (at most 16 characters)")
            continue
        advanced = ann.get("advanced", False)
        if not isinstance(advanced, bool):
            errors.append(f"{where}: x-oarbank.advanced must be true or false")
            continue
        label, help_ = p.get("title") or "", p.get("description") or ""
        if not isinstance(label, str) or not isinstance(help_, str) or len(label) > 80 or len(help_) > 500:
            errors.append(f"{where}: title (at most 80 characters) and description (at most 500) are text")
            continue
        has_default = "default" in p
        k = SettingKey(name=name, schema=p, type=real[0], nullable="null" in ts, scope=scope, required=required,
                       label=label or name.replace("_", " ").capitalize(), help=help_, unit=unit, advanced=advanced,
                       default=copy.deepcopy(p.get("default")), has_default=has_default)
        if has_default:
            why = check_value(k, k.default)
            if why:
                errors.append(f"{where}: its default does not validate: {why}")
                continue
        if required and has_default:
            errors.append(f"{where}: a required setting has no default (the owner must choose its value); drop one of them")
            continue
        if name in CORE_QUALIFIED:
            warnings.append(f"{where}: the core already has `{name}` for every module (the owner sets it as "
                            f"[module] {name}{'.disabled' if name == 'services' else ''}); this key is a separate one, "
                            "which owners will confuse with it")
        if CREDENTIAL.search(name):
            warnings.append(f"{where}: looks like a credential; settings are stored and shown in clear: declare a "
                            "credential in [[secrets]]")
        keys.append(k)
    if errors:
        raise SettingsSchemaError(errors)
    return SettingsSchema(keys=keys, warnings=warnings)


def load(root, man) -> SettingsSchema:
    """The settings a module directory's manifest declares (none without `[settings].schema`)."""
    rel = man.settings.schema_
    if not rel:
        return SettingsSchema()
    path = Path(root) / rel
    if not path.is_file():
        raise SettingsSchemaError([f"settings.schema {rel}: no such file in the bundle"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise SettingsSchemaError([f"settings.schema {rel}: not JSON ({e})"[:300]]) from None
    return parse(doc, f"settings.schema {rel}")


def load_bytes(data: bytes, rel: str) -> SettingsSchema:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise SettingsSchemaError([f"settings.schema {rel}: not JSON ({e})"[:300]]) from None
    return parse(doc, f"settings.schema {rel}")


def effective(keys: list[SettingKey], values: dict | None = None, scope: str | None = None) -> dict:
    """What a module process receives: every key (of `scope`, when given) with the owner's value, else its default; a
    key with neither is absent. `values` are the owner's values, already resolved for the node or the fleet."""
    values = values or {}
    out = {}
    for k in keys:
        if scope and k.scope != scope:
            continue
        if k.name in values:
            out[k.name] = values[k.name]
        elif k.has_default:
            out[k.name] = copy.deepcopy(k.default)
    return out


def check_values(keys: list[SettingKey], values: dict) -> list[str]:
    """Every problem with a set of owner values: undeclared keys and values that do not validate."""
    by = {k.name: k for k in keys}
    out = []
    for name, v in (values or {}).items():
        k = by.get(name)
        if k is None:
            out.append(f"{name}: not a setting this module declares")
            continue
        why = check_value(k, v)
        if why:
            out.append(f"{name}: {why}")
    return out


def read(path: str | Path | None = None) -> dict:
    """A runner's or service's settings for this node (`OARBANK_SETTINGS_FILE`): {} when there is none."""
    import os
    p = path or os.environ.get("OARBANK_SETTINGS_FILE")
    if not p:
        return {}
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
