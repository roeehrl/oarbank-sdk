"""The UI contract renderer (Apache-2.0): turns a module page (oarbank_sdk.ui.Page) into
escaped HTML. The oarbank console and `oarbank-sdk preview` both use it, so a module author sees
exactly what the console will draw.

The host supplies a `Host` with three callbacks, so this package never touches a database or a network:
- resolve(source, ctx) -> rows (list of dicts) | kv (dict) | series (dict of lists) | None, plus meta;
- operation(op_id) -> {id, title, tier, summary} (registry metadata; the label a button shows);
- urls: how typed links, operation forms and frames become URLs.

Security properties (see spec/ui-contract.md): Jinja autoescape everywhere; no `|safe` on module data;
formats from a whitelist; tones map to fixed classes; links only from typed references; every module
region is wrapped in `hx-disable` so htmx never processes attributes inside it.
"""
import datetime as _dt
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jinja2

from .. import ui as U

HERE = Path(__file__).parent
CSS_PATH = HERE / "ui.css"
_WHEN = re.compile(r"^\s*([a-z_]+(?:\.[a-z_]+)*)\s*(==|!=|>=|<=|>|<)\s*(\"[^\"]*\"|-?\d+(?:\.\d+)?|true|false|null|self)\s*$")


@dataclass
class Host:
    resolve: Callable[[U.Source, dict], dict]            # -> {"rows"|"kv"|"series"|"stat": ..., "computed_at"?, "stale"?}
    operation: Callable[[str], dict | None]              # op id -> {id, title, tier, summary} or None
    op_url: Callable[[str], str]                         # op id -> form action URL
    link_url: Callable[[U.Link], str]
    frame_url: Callable[[str], str]                      # iframe view id -> src on the module origin
    schema: Callable[[str], dict] = lambda path: {}     # bundle path -> JSON Schema (forms)
    module: str = "module"
    context: dict = field(default_factory=dict)          # route/job/node/user/setting values for `when` and $-params
    new_key: Callable[[], str] = lambda: __import__("uuid").uuid4().hex
    return_to: str = ""


# ------------------------------------------------------------------------------------------ formatting

def fmt_value(v, type_: str = "text", spec: str | None = None, unit: str | None = None) -> str:
    """Whitelisted formatting of raw module values. Returns plain text (escaped by the template)."""
    if v is None:
        return "—"
    try:
        if type_ in ("number", "integer", "percent"):
            x = float(v)
            if math.isnan(x):
                return "—"
            if spec == "d/d" and isinstance(v, (list, tuple)) and len(v) == 2:
                return f"{int(v[0])}/{int(v[1])}"
            if type_ == "percent" and not spec:
                spec = ".1%"
            if spec:
                plus = spec.startswith("+")
                s = format(int(x) if spec in ("d", ",d") else x, spec.lstrip("+"))
                s = ("+" + s) if plus and x > 0 else s
            else:
                s = f"{int(x):,}" if type_ == "integer" or x.is_integer() else f"{x:.4g}"
            return s + (f" {unit}" if unit else "")
        if type_ == "bytes":
            x = float(v)
            for u in ("B", "KB", "MB", "GB", "TB"):
                if abs(x) < 1024 or u == "TB":
                    return f"{x:.0f} {u}" if u == "B" else f"{x:.1f} {u}"
                x /= 1024
        if type_ == "duration":
            s = float(v)
            return f"{s:.0f}s" if s < 90 else f"{s / 60:.0f}m" if s < 5400 else f"{s / 3600:.1f}h" if s < 172800 else f"{s / 86400:.1f}d"
        if type_ in ("relative_time", "timestamp"):
            t = float(v) if not isinstance(v, str) else _dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            if type_ == "timestamp":
                return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
            d = __import__("time").time() - t
            return (fmt_value(abs(d), "duration") + (" ago" if d >= 0 else " from now"))
        if type_ == "bool":
            return "yes" if bool(v) else "no"
        if type_ == "digest":
            s = str(v)
            return s[:12] + "…" if len(s) > 12 else s
        return str(v) if spec is None or type_ not in ("text", "code") else format(str(v), spec)
    except (TypeError, ValueError, OverflowError):
        return "—"


def when_ok(expr: str | None, ctx: dict, row: dict | None = None) -> bool:
    """The `when` grammar: <path> <op> <literal>. Evaluated in Python; anything else is False."""
    if not expr:
        return True
    m = _WHEN.match(expr)
    if not m:
        return False
    path, op, lit = m.groups()
    scope = {"row": row or {}, **ctx}
    cur: Any = scope
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    lit_v: Any = {"true": True, "false": False, "null": None, "self": ctx.get("self")}.get(lit, None)
    if lit not in ("true", "false", "null", "self"):
        lit_v = lit[1:-1] if lit.startswith('"') else float(lit)
    try:
        return {"==": cur == lit_v, "!=": cur != lit_v, ">=": cur >= lit_v, "<=": cur <= lit_v,
                ">": cur > lit_v, "<": cur < lit_v}[op]
    except TypeError:
        return False


def interpolate(v, ctx: dict, row: dict | None = None):
    if isinstance(v, str) and v.startswith("$"):
        if v.startswith("$row."):
            return (row or {}).get(v[5:])
        path = v[1:].split(".")
        cur: Any = ctx
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur
    return v


def md_lite(text: str) -> str:
    """The markdown subset: paragraphs, **bold**, *em*, `code`, "- " lists. Escapes first, so no HTML survives."""
    import html
    out, para, items = [], [], []

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
    for line in text.splitlines():
        if line.strip().startswith("- "):
            if para:
                flush()
            items.append(line.strip()[2:])
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return jinja2.utils.markupsafe.Markup("".join(out))


def resolve(host: "Host", source: U.Source, ctx: dict, row: dict | None = None) -> dict:
    """Interpolate $-params, then ask the host. Failures become {'error': ...} (the component shows a placeholder)."""
    try:
        params = {k: interpolate(v, ctx, row) for k, v in source.params.items()}
        return host.resolve(source.model_copy(update={"params": params}), ctx) or {}
    except Exception as e:                                    # a broken source never breaks the page
        return {"error": f"{type(e).__name__}"}


TONE_CLASS = {"ok": "ok", "warn": "warn", "error": "bad", "info": "acc", "neutral": "", "running": "acc"}
TIER_CLASS = {"T0": "", "T1": "", "T2": "pri", "T3": "danger"}


def environment() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")), autoescape=True,
                             undefined=jinja2.ChainableUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(link_obj=lambda d: U.Link.model_validate(d),
                       extract_col=lambda col, rows: [r.get(col) for r in rows],
                       fmt=fmt_value, md=md_lite, tojson_compact=lambda v: json.dumps(v, separators=(",", ":"), default=str))
    env.globals.update(TONE_CLASS=TONE_CLASS, TIER_CLASS=TIER_CLASS, when_ok=when_ok, interpolate=interpolate, resolve=resolve)
    return env


_ENV = environment()


def render_page(page: U.Page, host: Host) -> str:
    """HTML for a module page or panel (a fragment; the host supplies the surrounding layout)."""
    return _ENV.get_template("page.html").render(page=page, host=host, ctx={**host.context, "self": host.module})


def render_component(component, host: Host) -> str:
    return _ENV.get_template("page.html").render(page=U.Page(body=[component]), host=host,
                                                 ctx={**host.context, "self": host.module})
