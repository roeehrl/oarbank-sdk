"""The published renderer: module pages become escaped HTML with host-drawn actions."""
import json
from pathlib import Path

from oarbank_sdk import ui
from oarbank_sdk.render import Host, fmt_value, md_lite, render_page, when_ok

TOY = Path(__file__).parents[1] / "examples" / "toy"
OPS = {"mod.toy.set_favorite": {"id": "mod.toy.set_favorite", "title": "Set the favorite n", "tier": "T1", "summary": "s"},
       "nodes.retire": {"id": "nodes.retire", "title": "Retire a node", "tier": "T3", "summary": "s"}}


def host(rows=None, error=False, **kw):
    def resolve(src, ctx):
        if error:
            raise RuntimeError("down")
        return {"rows": rows if rows is not None else [{"n": 5, "sum": 10, "ok": True, "value": 3}]}
    return Host(resolve=resolve, operation=OPS.get, op_url=lambda o: f"/do/{o}", link_url=lambda l: "/x",
                frame=lambda v: {"src": f"http://127.0.0.1:7402/f/toy/{v}/", "bridge": ["read.view", "resize"],
                                 "base": f"/m/toy/_bridge/{v}"}, module="toy", **kw)


def render(body, **kw):
    return render_page(ui.Page.model_validate({"body": body}), host(**kw))


def test_toy_overview_renders_without_script_and_inside_hx_disable():
    html = render_page(ui.Page.model_validate(json.loads((TOY / "ui/pages/overview.json").read_text(encoding="utf-8"))), host())
    assert html.startswith('<div class="mod-region" hx-disable data-module="toy"')
    assert "<script" not in html and "onclick" not in html
    assert 'sandbox="allow-scripts allow-forms"' in html and "allow-same-origin" not in html


def test_module_text_is_escaped_everywhere():
    evil = '<img src=x onerror=alert(1)>"\'><script>alert(2)</script>'
    html = render([{"type": "text", "text": evil}, {"type": "markdown", "text": evil + " **bold**"},
                   {"type": "callout", "text": evil},
                   {"type": "table", "source": {"query": "results"}, "columns": [{"key": "n", "label": evil}]}],
                  rows=[{"n": evil}])
    assert "<script>" not in html and "<img" not in html and "onerror=alert" not in html.replace("onerror=alert(1)&gt;", "")
    assert "&lt;script&gt;" in html and "<strong>bold</strong>" in html


def test_markdown_subset_escapes_first():
    out = str(md_lite("hi <b>x</b>\n\n- one\n- `two`"))
    assert out == "<p>hi &lt;b&gt;x&lt;/b&gt;</p><ul><li>one</li><li><code>two</code></li></ul>"


def test_actions_are_labelled_by_the_registry_not_the_module():
    html = render([{"type": "action", "action": {"op": "self.set_favorite", "params": {"n": 3}, "hint": "Delete everything!"}},
                   {"type": "action", "action": {"op": "nodes.retire", "target": "mini"}},
                   {"type": "action", "action": {"op": "self.not_registered"}}])
    assert ">Set the favorite n</button>" in html and 'data-confirm="Set the favorite n?"' in html
    assert 'class="danger"' in html and ">Retire a node</button>" in html          # T3 styling from the registry
    assert "mod.toy.not_registered?" in html                                          # unknown op: a visible marker, no button
    assert html.count("<button") == 2


def test_data_errors_and_when_degrade_to_placeholders():
    html = render([{"type": "table", "source": {"query": "results"}, "columns": [{"key": "n"}]},
                   {"type": "text", "text": "hidden", "when": "node.online == true"}], error=True)
    assert "data unavailable" in html and "hidden" not in html


def test_row_interpolation_and_row_when():
    html = render([{"type": "table", "source": {"query": "results"}, "columns": [{"key": "n"}],
                    "row_actions": [{"op": "self.set_favorite", "params": {"n": "$row.n"}, "when": "row.ok == true"}]}],
                  rows=[{"n": 7, "ok": True}, {"n": 8, "ok": False}])
    assert 'name="p.n" value="7"' in html and 'name="p.n" value="8"' not in html


def test_when_grammar_is_tiny_and_safe():
    ctx = {"node": {"online": True, "load": 3}, "self": "toy"}
    assert when_ok("node.online == true", ctx) and when_ok("node.load < 4", ctx)
    assert not when_ok("__import__('os').system('x')", ctx) and not when_ok("node.online == true or 1", ctx)


def test_formatting_whitelist():
    assert fmt_value(0.94751, "number", ".4f") == "0.9475" and fmt_value(1234567, "integer", ",d") == "1,234,567"
    assert fmt_value(0.051, "number", "+.1%") == "+5.1%" and fmt_value(3 * 2**30, "bytes") == "3.0 GB"
    assert fmt_value("not a number", "number") == "—" and fmt_value(None) == "—"
    assert fmt_value("a" * 64, "digest") == "a" * 12 + "…"
