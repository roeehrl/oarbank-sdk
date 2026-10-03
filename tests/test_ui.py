"""UI contract 1 (spec/ui-contract.md): pages as data, host-rendered, operations host-mediated."""
import json
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import manifest as mf, module_protocol as mp, ui
from oarbank_sdk.cli import main
from oarbank_sdk.client import ModuleClient

TOY = Path(__file__).parents[1] / "examples" / "toy"


def page(body):
    return ui.Page.model_validate({"body": body})


def test_toy_manifest_and_pages_check():
    assert main(["check", str(TOY / "oarbank-module.toml")]) == 0
    man = mf.load(TOY / "oarbank-module.toml")
    assert [p.id for p in man.ui.pages] == ["overview"] and man.operations[0].verb == "set_favorite"
    p = ui.Page.model_validate(json.loads((TOY / "ui/pages/overview.json").read_text(encoding="utf-8")))
    types = {c.type for c in ui.walk(p.body)}
    assert {"stat", "table", "chart", "iframe", "action", "columns", "section"} <= types


@pytest.mark.parametrize("body,match", [
    ([{"type": "html", "html": "<b>x</b>"}], "html"),                                            # no raw HTML component
    ([{"type": "text", "text": "x", "style": "color:red"}], "Extra inputs"),                   # no styles
    ([{"type": "table", "source": {"query": "audit"}, "columns": [{"key": "a"}]}], "query"),    # not in the catalogue of sources
    ([{"type": "table", "source": {"query": "results", "params": {"x": "$env.HOME"}}, "columns": [{"key": "a"}]}], "interpolation"),
    ([{"type": "table", "source": {"query": "results", "view": "v"}, "columns": [{"key": "a"}]}], "exactly one"),
    ([{"type": "table", "source": {"query": "results"}, "columns": [{"key": "a", "format": "%s"}]}], "whitelist"),
    ([{"type": "link", "text": "x", "to": {"url": "javascript:alert(1)"}}], "url"),
    ([{"type": "link", "text": "x", "to": {"url": "http://example.com"}}], "url"),
    ([{"type": "action", "action": {"op": "rm -rf"}}], "op"),
    ([{"type": "chart", "kind": "line", "source": {"query": "results"}, "x": "at", "y": ["value"]}], "summary"),
    ([{"type": "text", "text": "x" * 3001}], "3000"),
])
def test_forbidden_constructs(body, match):
    with pytest.raises(ValidationError, match=match):
        page(body)


def test_cross_references_are_checked():
    man = mf.load(TOY / "oarbank-module.toml")
    p = page([{"type": "table", "source": {"view": "nope"}, "columns": [{"key": "a"}]},
              {"type": "action", "action": {"op": "self.nope"}},
              {"type": "iframe", "view": "nope", "title": "x"},
              {"type": "link", "text": "x", "to": {"url": "https://unlisted.example"}}])
    errs = ui.check_page(p, man.ui, man.operations)
    assert len(errs) == 4 and any("view 'nope'" in e for e in errs) and any("self.nope" in e for e in errs)


def test_effective_tier_floors_and_preview_rule():
    op = ui.OperationDecl(verb="wipe", title="Wipe", tier="T0", effects=["datasets.delete"])
    assert op.effective_tier() == "T2"
    doc = tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))
    doc["operations"][0].update(effects=["datasets.delete"], preview=False)
    with pytest.raises(ValidationError, match="requires preview"):
        mf.Manifest.model_validate(doc)


def test_slot_limits_and_placement_kinds():
    with pytest.raises(ValidationError, match="at most 1"):
        ui.UISection(pages=[{"id": "a", "title": "A", "slot": "module.overview", "file": "a.json"},
                            {"id": "b", "title": "B", "slot": "module.overview", "file": "b.json"}])
    with pytest.raises(ValidationError, match="detail slot"):
        ui.UISection(panels=[{"id": "a", "title": "A", "slot": "module.page", "file": "a.json"}])


def test_manifest_needs_capabilities_for_views_and_operations():
    doc = tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))
    moves = ["move.preflight", "move.postflight", "move.cancelled"]
    doc["coordinator"]["capabilities"] = ["op.plan", "op.apply", *moves]
    with pytest.raises(ValidationError, match="ui.view.compute"):
        mf.Manifest.model_validate(doc)
    doc["coordinator"]["capabilities"] = ["ui.view.compute", "op.plan", *moves]
    with pytest.raises(ValidationError, match="op.apply"):
        mf.Manifest.model_validate(doc)


def test_toy_view_and_operation_verbs():
    with ModuleClient.spawn([sys.executable, "-I", "toy_module.py"], cwd=TOY) as m:
        info = m.initialize(settings={"favorite_n": 7})
        assert {"ui.view.compute", "op.plan", "op.apply"} <= set(info.capabilities)
        v = mp.ViewComputeResult.model_validate(m.call("ui.view.compute", {
            "view_id": "sums", "data_version": 42,
            "inputs": {"results": [{"key_inputs": {"n": 4}, "value": 6}, {"key_inputs": {"n": 5}, "value": 9}]}}))
        assert v.data_version == 42 and v.rows == [{"n": 5, "sum": 9, "ok": False}, {"n": 4, "sum": 6, "ok": True}]
        plan = mp.OpPlanResult.model_validate(m.call("op.plan", {"verb": "set_favorite", "params": {"n": 9}, "actor": "local"}))
        assert plan.effects[0].kind == "module_settings.update" and plan.diff[0]["before"] == 7
        r = mp.OpApplyResult.model_validate(m.call("op.apply", {"verb": "set_favorite", "params": {"n": 9}, "actor": "local"}))
        assert r.response == "refresh" and r.effects[0].args == {"favorite_n": 9}
        bad = mp.OpApplyResult.model_validate(m.call("op.apply", {"verb": "set_favorite", "params": {"n": -1}, "actor": "local"}))
        assert bad.response == "errors" and bad.errors[0].path == "/n"


def test_iframe_frame_script_uses_only_the_bridge():
    js = (TOY / "ui/frames/explorer.js").read_text(encoding="utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "document.cookie", "window.parent.document", "localStorage", "innerHTML"):
        assert banned not in js, banned
    assert "ev.ports[0]" in js and 'type !== "oarbank.bridge"' in js
