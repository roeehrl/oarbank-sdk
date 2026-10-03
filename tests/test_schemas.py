"""The published JSON Schemas are generated from the models and must never drift from them."""
import json
import tomllib
from pathlib import Path

import jsonschema
import pytest

from oarbank_sdk import module_protocol as mp
from oarbank_sdk.schemas import ROOT, render_all

EXAMPLES = sorted([*(Path(__file__).parent / "fixtures" / "manifests").glob("*.toml"), *(Path(__file__).parents[1] / "examples").glob("*/oarbank-module.toml")])


def test_schemas_are_fresh():
    rendered = render_all()
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in ROOT.glob("*.schema.json")}
    stale = sorted(n for n in rendered if on_disk.get(n) != rendered[n])
    extra = sorted(set(on_disk) - set(rendered))
    assert not stale and not extra, f"run `oarbank-sdk export-schemas` (stale={stale}, extra={extra})"


@pytest.mark.parametrize("name", sorted(render_all()))
def test_schemas_are_valid_draft_2020_12(name):
    jsonschema.Draft202012Validator.check_schema(json.loads(render_all()[name]))


def test_every_verb_and_callback_has_schemas():
    names = set(render_all())
    for method in [*mp.VERBS, *mp.HOST_CALLBACKS]:
        slug = method.replace(".", "-")
        assert f"module-{slug}-params-1.schema.json" in names
        assert f"module-{slug}-result-1.schema.json" in names


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("variant", ["manifest-1.schema.json", "manifest-1.strict.schema.json"])
def test_examples_validate_against_published_schema(path, variant):
    """Non-Python tooling (editors, other-language SDKs) sees the same verdict as the Python models,
    except for the cross-field rules only the models enforce."""
    schema = json.loads(render_all()[variant])
    jsonschema.validate(tomllib.loads(path.read_text(encoding="utf-8")), schema, cls=jsonschema.Draft202012Validator)


def test_strict_schema_catches_typos():
    schema = json.loads(render_all()["manifest-1.strict.schema.json"])
    doc = tomllib.loads(EXAMPLES[0].read_text(encoding="utf-8"))
    doc["module"]["verison"] = "1.0.0"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema, cls=jsonschema.Draft202012Validator)


def test_manifest_reference_is_fresh():
    from oarbank_sdk.schemas import REFERENCE, render_reference
    assert REFERENCE.read_text(encoding="utf-8") == render_reference(), "run `oarbank-sdk export-schemas`"
