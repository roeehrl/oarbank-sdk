"""A module's settings schema (spec/manifest.md, "Settings"): what the core reads from it, what check, bundle build and
bundle verify refuse, and what runners and the coordinator side receive."""
import json
import shutil
from pathlib import Path

import pytest

from oarbank_sdk import bundle as B
from oarbank_sdk import manifest as m
from oarbank_sdk import settings as S
from oarbank_sdk.cli import main

TOY = Path(__file__).parents[1] / "examples" / "toy"


def schema(**props):
    return {"type": "object", "properties": props}


def test_keys_carry_type_scope_default_and_the_owners_words():
    sch = S.parse(schema(
        vm_mem_gb={"type": "number", "minimum": 2, "maximum": 64, "default": 8, "title": "VM memory",
                   "description": "Memory the VM gets.", "x-oarbank": {"scope": "node", "unit": "GB", "advanced": True}},
        api_url={"type": ["string", "null"], "format": "uri", "x-oarbank": {"required": True}},
        hosts={"type": "array", "items": {"type": "string"}, "maxItems": 4}))
    vm, api, hosts = sch.keys
    assert (vm.name, vm.type, vm.scope, vm.unit, vm.advanced, vm.default, vm.has_default, vm.label, vm.help) == \
        ("vm_mem_gb", "number", "node", "GB", True, 8, True, "VM memory", "Memory the VM gets.")
    assert "x-oarbank" not in vm.schema and vm.schema["maximum"] == 64
    assert (api.scope, api.required, api.nullable, api.has_default, api.label) == ("fleet", True, True, False, "Api url")
    assert hosts.type == "array" and not hosts.required
    assert S.SettingKey.from_dict(vm.as_dict()) == vm


def test_a_key_a_campaign_may_override():
    (k, j) = S.parse(schema(depth={"type": "integer", "default": 2, "x-oarbank": {"scope": "node", "campaign": True}},
                            n={"type": "integer"})).keys
    assert (k.campaign, j.campaign) == (True, False)
    assert S.SettingKey.from_dict(k.as_dict()) == k


def test_the_schemas_required_list_marks_keys_required():
    sch = S.parse({**schema(token_count={"type": "integer"}, x={"type": "boolean"}), "required": ["token_count"]})
    assert [k.required for k in sch.keys] == [True, False]


def test_values_validate_against_the_whole_property_schema():
    (k,) = S.parse(schema(n={"type": "integer", "minimum": 1, "multipleOf": 2})).keys
    assert S.check_value(k, 4) is None
    assert "minimum" in S.check_value(k, 0) or "less than" in S.check_value(k, 0)
    assert S.check_value(k, 3)                                   # multipleOf: the whole JSON Schema applies
    assert S.check_value(k, "4")
    assert S.check_values([k], {"n": 2, "m": 1}) == ["m: not a setting this module declares"]


@pytest.mark.parametrize("props, match", [
    ({"Bad-Name": {"type": "integer"}}, "lower-case"),
    ({"n": {"type": "integer", "x-oarbank": {"scope": "group"}}}, "scope must be fleet or node"),
    ({"n": {"type": "integer", "x-oarbank": {"campaigns": True}}}, "unknown x-oarbank fields"),
    ({"n": {"type": "integer", "x-oarbank": {"campaign": "yes"}}}, "campaign must be true or false"),
    ({"n": {"type": "integer", "default": "x"}}, "default does not validate"),
    ({"n": {"type": "integer", "default": 1, "x-oarbank": {"required": True}}}, "required setting has no default"),
    ({"n": {"type": ["integer", "string"]}}, "type must be one of"),
    ({"n": {}}, "type must be one of"),
    ({"n": {"type": "integer", "minimum": "low"}}, "not a valid JSON Schema"),
    ({"key": {"type": "string", "writeOnly": True}}, "never a setting"),
    ({"key": {"type": "string", "x-oarbank": {"secret": True}}}, "never a setting"),
    ({"n": {"type": "integer", "x-oarbank": {"unit": "a very long unit name"}}}, "short word"),
])
def test_refused_schemas_say_why(props, match):
    with pytest.raises(S.SettingsSchemaError, match=match):
        S.parse(schema(**props))


def test_every_problem_comes_back_at_once():
    with pytest.raises(S.SettingsSchemaError) as e:
        S.parse(schema(a={"type": "nope"}, b={"type": "integer", "default": "x"}))
    assert len(e.value.errors) == 2


def test_lint_warns_about_core_key_names_and_credentials():
    sch = S.parse(schema(pipeline={"type": "string", "enum": ["single", "split"]}, api_token={"type": "string"}))
    assert any("[module] pipeline" in w for w in sch.warnings)
    assert any("credential" in w and "api_token" in w for w in sch.warnings)


def test_effective_values_split_by_scope_with_defaults():
    keys = S.parse(schema(a={"type": "integer", "default": 1}, b={"type": "integer", "x-oarbank": {"scope": "node"}},
                          c={"type": "string", "default": "x", "x-oarbank": {"scope": "node"}})).keys
    assert S.effective(keys) == {"a": 1, "c": "x"}                     # a key with no value and no default is absent
    assert S.effective(keys, {"b": 5}, "node") == {"b": 5, "c": "x"}
    assert S.effective(keys, {"a": 7}, "fleet") == {"a": 7}


def test_read_gives_a_runner_its_settings(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"greeting": "hi"}), encoding="utf-8")
    monkeypatch.setenv("OARBANK_SETTINGS_FILE", str(f))
    assert S.read() == {"greeting": "hi"}
    monkeypatch.delenv("OARBANK_SETTINGS_FILE")
    assert S.read() == {}


def _toy(tmp_path, mutate) -> Path:
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("dist", "__pycache__"))
    p = d / "schemas" / "settings.schema.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    mutate(doc)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return d


def test_the_toy_declares_its_settings():
    sch = S.load(TOY, m.load(TOY / "oarbank-module.toml"))
    assert [(k.name, k.scope) for k in sch.keys] == [("favorite_n", "fleet"), ("greeting", "node")]


def test_check_build_and_verify_refuse_an_invalid_settings_schema(tmp_path, capsys):
    d = _toy(tmp_path, lambda doc: doc["properties"]["favorite_n"].update(default="many"))
    assert main(["check", str(d / "oarbank-module.toml")]) == 1
    assert "INVALID (settings schema)" in capsys.readouterr().out
    with pytest.raises(B.BundleError, match="default does not validate"):
        B.build(d, tmp_path / "out.mfb")
    # a bundle built with a valid schema, then tampered with, never verifies (the digest); verify reads the schema too
    good, _ = B.build(TOY, tmp_path / "good.mfb")
    assert B.verify(good).manifest.settings.schema_ == "schemas/settings.schema.json"


def test_verify_refuses_a_bundle_whose_settings_schema_is_invalid(tmp_path, monkeypatch):
    d = _toy(tmp_path, lambda doc: doc["properties"]["greeting"].update({"x-oarbank": {"scope": "everywhere"}}))
    monkeypatch.setattr(S, "load", lambda root, man: S.SettingsSchema())      # build without the check
    out, _ = B.build(d, tmp_path / "bad.mfb")
    monkeypatch.undo()
    with pytest.raises(B.BundleError, match="scope must be fleet or node"):
        B.verify(out)


def test_check_prints_settings_lint_as_warnings(tmp_path, capsys):
    d = _toy(tmp_path, lambda doc: doc["properties"].update(enabled={"type": "boolean"}))
    assert main(["check", str(d / "oarbank-module.toml")]) == 0
    assert "warning: settings.schema schemas/settings.schema.json: enabled: the core already has" in capsys.readouterr().out


def test_conform_settings_suite_checks_fixtures_and_hands_on_scoped_values(tmp_path):
    from oarbank_sdk import conformance as K
    man = m.load(TOY / "oarbank-module.toml")
    r = K.Report()
    out = K.check_settings(TOY, man, {"settings": {"favorite_n": 3}}, r)
    assert r.ok and out["settings"] == {"favorite_n": 3, "greeting": "hello"}
    assert out[K.NODE_SETTINGS] == {"greeting": "hello"}                  # runners: the node-scoped keys only
    r = K.Report()
    K.check_settings(TOY, man, {"settings": {"favorite_n": -1, "nope": 1}}, r)
    assert not r.ok
    failed = [c for c in r.as_dict()["checks"] if c["status"] == "fail"]
    assert failed and "nope" in failed[0]["detail"] and "favorite_n" in failed[0]["detail"]
