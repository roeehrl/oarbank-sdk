"""UI contract 1.2 (spec/ui-contract.md): what a page reads that cores 2.2 to 2.5 added, typed links that resolve, the
viewer's role, frames with enforced capabilities and context, one query shaper, preview parity (CSRF, panel context,
validated views, axe) and the conformance kit's ui suite."""
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import manifest as mf, ui as U
from oarbank_sdk.conformance import Report, check_ui_suite
from oarbank_sdk.preview import Preview
from oarbank_sdk.render import Host, filter_params, fmt_value, render_page, shape

EX = Path(__file__).parents[1] / "examples"
TOY, REEL = EX / "toy", EX / "reel"
UI_JS = Path(__file__).parents[1] / "src" / "oarbank_sdk" / "render" / "static" / "ui.js"
NODE_MODULES = os.environ.get("OARBANK_SDK_NODE_MODULES")
OPS = {"mod.toy.set_favorite": {"id": "mod.toy.set_favorite", "title": "Set the favorite n", "tier": "T1", "summary": "s",
                                "min_role": "operator"},
       "nodes.retire": {"id": "nodes.retire", "title": "Retire a node", "tier": "T3", "summary": "s", "min_role": "admin"}}


def page(body, contract="1.2"):
    return U.Page.model_validate({"ui_contract": contract, "body": body})


def host(rows=None, ctx=None, links=None):
    seen = {}

    def resolve(src, c):
        seen["src"] = src
        return {"rows": rows if rows is not None else [{"n": 5}]}
    h = Host(resolve=resolve, operation=OPS.get, op_url=lambda o: f"/do/{o}",
             link_url=links or (lambda l: f"/datasets/{l.dataset}" if l.dataset else None),
             frame=lambda v: {"src": f"http://127.0.0.1:7402/f/toy/{v}/", "bridge": ["read.view", "navigate"],
                              "base": f"/m/toy/_bridge/{v}"} if v == "explorer" else None,
             module="toy", context=ctx or {})
    return h, seen


# ------------------------------------------------------------------------------------------ the page check

def test_1_2_sources_fields_and_links_need_requires():
    man = mf.load(REEL / "oarbank-module.toml")
    needs = [
        {"type": "table", "source": {"query": "secrets"}, "columns": [{"key": "name"}]},                 # a 1.2 source
        {"type": "table", "source": {"query": "nodes"}, "columns": [{"key": "gpu_apis_host"}]},          # a 1.2 field
        {"type": "kv", "source": {"query": "datasets"}, "items": [{"label": "size", "field": "size"}]},
        {"type": "table", "source": {"query": "campaigns"}, "columns": [{"key": "campaign_id", "type": "campaign_ref", "download": True}]},
        {"type": "link", "text": "Secrets", "to": {"tab": "health"}},
        {"type": "link", "text": "Download", "to": {"dataset": "asset:clip", "download": True}},
    ]
    for c in needs:
        errs = U.check_page(page([c]), man)
        assert any('requires = "1.2"' in e for e in errs), (c, errs)
        assert U.check_page(page([{**c, "requires": "1.2", "fallback": "drop"}]), man) == [], c
    # a page that reads only 1.1 fields of an older source keeps working on a 1.1 host without `requires`
    assert U.check_page(page([{"type": "table", "source": {"query": "nodes"}, "columns": [{"key": "hostname"}]}]), man) == []


def test_upload_links_name_a_kind_and_an_importer():
    man = mf.load(REEL / "oarbank-module.toml")
    ok = {"type": "link", "requires": "1.2", "text": "Upload", "to": {"upload": {"kind": "upload", "then": "self.adopt_upload"}}}
    assert U.check_page(page([ok]), man) == []
    bad_kind = {**ok, "to": {"upload": {"kind": "nope"}}}
    assert any("upload kind 'nope'" in e for e in U.check_page(page([bad_kind]), man))
    not_importer = {**ok, "to": {"upload": {"kind": "upload", "then": "self.queue_render"}}}   # target none, not dataset
    assert any('target = "dataset"' in e for e in U.check_page(page([not_importer]), man))
    with pytest.raises(ValidationError, match="exactly one target"):
        U.Link.model_validate({"dataset": "a", "tab": "health"})
    with pytest.raises(ValidationError, match="download goes with"):
        U.Link.model_validate({"job": 3, "download": True})
    with pytest.raises(ValidationError, match="download goes with"):
        U.Column.model_validate({"key": "x", "type": "job_ref", "download": True})


def test_validate_view_keeps_declared_columns_and_finds_bad_references():
    decl = U.ViewDecl(shape="rows", columns=[{"key": "frame", "type": "artifact_ref"}, {"key": "i", "type": "integer"}], max_rows=2)
    doc = U.validate_view(decl, {"rows": [{"frame": {"job": 1, "artifact": "f", "path": "a.png"}, "i": 0, "secret": "x"},
                                           {"frame": {"job": "x"}, "i": 1}]})
    assert all(set(r) == {"frame", "i"} for r in doc["rows"])                  # only declared columns reach the console
    assert len(U.view_ref_problems(decl, doc)) == 1
    with pytest.raises(ValueError, match="max_rows"):
        U.validate_view(decl, {"rows": [{}, {}, {}]})


# ------------------------------------------------------------------------------------------ the renderer

def test_links_resolve_or_render_as_text_never_hash():
    h, _ = host(rows=[{"d": "asset:clip", "j": "not a job"}])
    html = render_page(page([{"type": "table", "source": {"query": "datasets"}, "columns": [
        {"key": "d", "type": "dataset_ref"}, {"key": "d", "type": "dataset_ref", "download": True, "label": "dl"},
        {"key": "j", "type": "job_ref"}], "requires": "1.2"},
        {"type": "link", "text": "nowhere", "to": {"tab": "health"}, "requires": "1.2"}]), h)
    assert 'href="/datasets/asset:clip"' in html and ">download</a>" in html
    assert 'href="#"' not in html and "<span>nowhere</span>" in html and ">not a job<" in html


def test_the_viewer_role_gates_buttons_and_when():
    body = [{"type": "action", "action": {"op": "nodes.retire", "target": "mini"}},
            {"type": "action", "action": {"op": "self.set_favorite"}},
            {"type": "text", "text": "admins only", "when": "user.role == \"admin\""}]
    viewer = render_page(page(body), host(ctx={"user": {"role": "operator"}})[0])
    assert 'disabled title="needs the admin role">Retire a node</button>' in viewer and "admins only" not in viewer
    assert '<form class="inline mod-op" method="post" action="/do/mod.toy.set_favorite"' in viewer
    admin = render_page(page(body), host(ctx={"user": {"role": "admin"}})[0])
    assert "disabled" not in admin and "admins only" in admin


def test_frames_carry_their_capabilities_base_and_context():
    h, _ = host(ctx={"job": {"id": 42, "module": "toy"}, "var": {"region": "eu"}})
    html = render_page(page([{"type": "iframe", "view": "explorer", "title": "x"}, {"type": "iframe", "view": "ghost", "title": "y"}]), h)
    assert 'data-bridge-caps="read.view navigate"' in html and 'data-bridge-base="/m/toy/_bridge/explorer"' in html
    assert 'data-context="{&#34;job&#34;:42,&#34;var&#34;:{&#34;region&#34;:&#34;eu&#34;}}"' in html
    assert html.count("<iframe") == 1 and "no such frame" in html


def test_subjects_interpolate_to_their_ids():
    h, seen = host(ctx={"job": {"id": 42, "module": "toy", "state": "done"}})
    render_page(page([{"type": "kv", "source": {"query": "results", "params": {"job_id": "$job"}}, "items": [{"label": "n", "field": "n"}]}]), h)
    assert seen["src"].params == {"job_id": 42}


def test_one_shaper():
    rows = [{"g": "a", "v": 1}, {"g": "a", "v": 3}, {"g": "a", "v": 8}, {"g": "b", "v": 2}, {"g": "b", "v": True}]
    src = U.Source(query="results", group_by="g", agg={"v": "median"}, order_by="g", descending=False)
    assert shape(rows, src) == [{"g": "a", "v": 3}, {"g": "b", "v": 2}]               # booleans are not numbers
    assert shape(rows, U.Source(query="results", agg={"v": "sum"})) == [{"v": 14}]
    assert shape(rows, U.Source(query="results", agg={"v": "p95"})) == [{"v": 8}]
    assert shape(rows, U.Source(query="results", order_by="v", fields=["v"], limit=2)) == [{"v": 8}, {"v": 3}]
    assert filter_params(U.Source(query="jobs", params={"state": "done", "node_id": "n", "campaign": None})) == {"state": "done"}
    assert fmt_value(["metal", "opencl"]) == "metal, opencl" and fmt_value([]) == "—"


# ------------------------------------------------------------------------------------------ the preview

@pytest.fixture
def pv():
    p = Preview(str(TOY / "oarbank-module.toml"), port=0)
    srv = p.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield p
    p.close()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data is not None else None, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_preview_enforces_csrf_like_the_console(pv):
    base = f"http://127.0.0.1:{pv.port}"
    _, body = call(base + "/")
    assert f'<meta name="csrf-token" content="{pv.csrf}">' in body
    form = {"params": json.dumps({"n": 42}), "return_to": "/"}
    st, body = call(base + "/op/mod.toy.set_favorite", form)
    assert st == 403 and "csrf" in body
    st, body = call(base + "/op/mod.toy.set_favorite", {**form, "csrf": pv.csrf}, {"Origin": "http://evil.example"})
    assert st == 403 and "origin" in body
    st, body = call(base + "/op/mod.toy.set_favorite", {**form, "csrf": pv.csrf})
    assert st == 200 and "nothing is executed" in body and "favorite_n" in body and "42" in body


def test_preview_bridge_checks_capabilities_and_interpolates_context(pv):
    base = f"http://127.0.0.1:{pv.port}/bridge"
    q = urllib.parse.urlencode({"spec": json.dumps({"query": "results", "params": {"job_id": "$job"}}), "ctx": json.dumps({"job": 2})})
    st, body = call(f"{base}/explorer/query?{q}")
    assert st == 200 and [r["job_id"] for r in json.loads(body)["rows"]] == [2]
    assert call(f"{base}/explorer/media?ref=%7B%7D&kind=image")[0] == 403          # read.media is not declared
    assert call(f"{base}/ghost/view/sums")[0] == 404
    st, body = call(f"{base}/explorer/link?" + urllib.parse.urlencode({"to": json.dumps({"job": 2})}))
    assert st == 200 and json.loads(body)["href"] == "/console/job?id=2"
    assert call(f"{base}/explorer/link?" + urllib.parse.urlencode({"to": json.dumps({"url": "https://x.example"})}))[0] == 404
    assert call(f"{base}/explorer/op/mod.other.thing")[0] == 403


def test_preview_panels_get_their_context_and_views_are_validated(pv):
    st, body = call(f"http://127.0.0.1:{pv.port}/page/job_sum")
    assert st == 200 and ">499,500<" in body and ">6<" not in body                  # $job from fixtures/ui/context.json
    st, body = call(f"http://127.0.0.1:{pv.port}/console/job?id=2")
    assert st == 200 and "its state, attempts, result and logs" in body and "fixtures/ui/queries/jobs.json" in body   # a stand-in page
    assert pv.view_problems == {"sums": []} and set(pv.views["sums"]["rows"][0]) == {"n", "sum", "ok"}


@pytest.mark.skipif(not NODE_MODULES, reason="set OARBANK_SDK_NODE_MODULES to a node_modules with axe-core and jsdom")
def test_preview_check_runs_axe_and_catches_a_violation(monkeypatch):
    p = Preview(str(REEL / "oarbank-module.toml"), port=0)
    try:
        found, ran = p.axe(NODE_MODULES)
        assert found == [] and "3 pages and panels" in ran
        monkeypatch.setattr(p, "documents", lambda: {"bad": '<!doctype html><html lang="en"><title>x</title><img src="x.png"></html>'})
        found, _ = p.axe(NODE_MODULES)
        assert found and "image-alt" in found[0]
    finally:
        p.close()


def test_preview_check_without_node_says_it_skipped(monkeypatch, capsys):
    from oarbank_sdk.preview import check
    monkeypatch.delenv("OARBANK_SDK_NODE_MODULES", raising=False)
    assert check(str(TOY / "oarbank-module.toml"), node_modules=None) == 0
    assert "axe skipped: needs Node with axe-core and jsdom" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------ the bridge in a browser

@pytest.mark.skipif(not (NODE_MODULES and shutil.which("node")), reason="needs Node and jsdom (OARBANK_SDK_NODE_MODULES)")
def test_ui_js_bridge_enforces_capabilities_and_posts_with_csrf_and_json_params():
    out = subprocess.run(["node", str(Path(__file__).parent / "js" / "bridge.cjs"), str(UI_JS)], capture_output=True, text=True,
                         env={**os.environ, "NODE_PATH": NODE_MODULES}, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["hello"] == {"type": "oarbank.bridge", "module": "toy", "view": "explorer", "context": {"job": 42}}
    assert d["replies"]["1"]["error"] == "method not allowed" and d["replies"]["6"]["error"] == "method not allowed"
    assert not any("/view/" in f for f in d["fetches"])                             # refused before any request
    assert "ctx=%7B%22job%22%3A42%7D" in d["fetches"][0] and d["replies"]["2"]["result"]["rows"] == [{"job_id": 42}]
    form = d["forms"][0]
    assert form["action"] == "/do/mod.toy.set_favorite" and form["fields"]["csrf"] == "tok-123"
    assert json.loads(form["fields"]["params"]) == {"n": 7, "code": "007", "tags": ["a"], "on": True}
    assert form["fields"]["target"] == "x:1" and "on x:1" in d["confirms"][0]
    assert d["replies"]["4"]["result"] == {"ok": True} and d["replies"]["5"]["error"] == "bad link" and d["navigations"] == 1


# ------------------------------------------------------------------------------------------ conformance

@pytest.mark.parametrize("module", ["toy", "reel", "modelserver", "gpuinfo", "taskbench"])
def test_the_ui_suite_passes_for_the_examples(module):
    r = Report()
    check_ui_suite(EX / module, mf.load(EX / module / "oarbank-module.toml"), r)
    assert not [c for c in r.checks if c.status == "fail"], r.text()
    assert any(c.name.endswith("renders") and c.status == "pass" for c in r.checks)
    assert not [c for c in r.checks if c.status == "warn"], r.text()                # every component has fixture data


def test_the_ui_suite_fails_a_view_that_breaks_its_declaration(tmp_path):
    d = tmp_path / "reel"
    shutil.copytree(REEL, d, ignore=shutil.ignore_patterns("__pycache__"))
    code = (d / "reel_module.py").read_text(encoding="utf-8").replace(
        '"frame": _ref(latest["job_id"], "frames", F.frame_name(i)), "index": i}', '"frame": {"job": "x"}, "index": i}')
    (d / "reel_module.py").write_text(code, encoding="utf-8")
    r = Report()
    check_ui_suite(d, mf.load(d / "oarbank-module.toml"), r)
    assert any(c.status == "fail" and c.name == "view frames matches its declaration" for c in r.checks), r.text()
