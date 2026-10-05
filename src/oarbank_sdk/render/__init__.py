"""The UI contract renderer (Apache-2.0): turns a module page (oarbank_sdk.ui.Page) into
escaped HTML. The oarbank console and `oarbank-sdk preview` both use it, so a module author sees
exactly what the console will draw.

The host supplies a `Host` with these callbacks, so this package never touches a database or a network:
- resolve(source, ctx) -> rows (list of dicts) | kv (dict) | series (dict of lists) | None, plus meta;
- operation(op_id) -> {id, title, tier, summary, min_role} (registry metadata; the label a button shows);
- urls: how typed links and operation forms become URLs; frame(view) -> {src, bridge, base} for a declared iframe view
  (its URL on the module origin, its declared bridge capabilities and its bridge base on the console), or None;
- media(ref, kind) -> {src, thumb, job} for an artifact reference the host checked belongs to the module (URLs on the
  module origin, never the console's), or None: the component then shows a placeholder.
`shape` is the one query shaper: the console and the preview both apply it to a host query's rows.

Security properties (see spec/ui-contract.md): Jinja autoescape everywhere; no `|safe` on module data;
formats from a whitelist; tones map to fixed classes; links only from typed references; every module
region is wrapped in `hx-disable` so htmx never processes attributes inside it.
"""
import datetime as _dt
import json
import math
import re
import statistics
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
    link_url: Callable[[U.Link], str | None]             # None: the link renders as text
    frame: Callable[[str], dict | None]                  # iframe view id -> {src, bridge, base} or None (undeclared)
    media: Callable[[Any, str], dict | None] = lambda ref, kind: None   # artifact reference, kind -> {src, thumb, job}
    ui_minor: int = int(U.UI_CONTRACT.split(".")[1])     # the UI contract minor this host renders
    schema: Callable[[str], dict] = lambda path: {}     # bundle path -> JSON Schema (forms)
    module: str = "module"
    context: dict = field(default_factory=dict)          # route/job/node/campaign/user/setting values for `when` and $-params
    new_key: Callable[[], str] = lambda: __import__("uuid").uuid4().hex
    return_to: str = ""


# ------------------------------------------------------------------------------------------ the policies (one copy)

def console_csp(module_origin: str) -> str:
    """The console page's CSP: no inline script; frames and module media only from the module origin."""
    return ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
            f"img-src 'self' data: {module_origin}; media-src {module_origin}; connect-src 'self'; frame-src {module_origin}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def frame_csp(console_origin: str) -> str:
    """A module frame's CSP on the module origin: sandboxed (scripts and forms, never same-origin), no network
    (`connect-src 'none'`: data comes only over the bridge), images and media only from the module origin."""
    return ("sandbox allow-scripts allow-forms; default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'; "
            f"frame-ancestors {console_origin}")


def media_csp(console_origin: str) -> str:
    """Media bytes on the module origin: inert if a browser opens one as a document."""
    return f"sandbox; default-src 'none'; frame-ancestors {console_origin}"


# ------------------------------------------------------------------------------------------ formatting

def fmt_value(v, type_: str = "text", spec: str | None = None, unit: str | None = None) -> str:
    """Whitelisted formatting of raw module values. Returns plain text (escaped by the template)."""
    if v is None:
        return "—"
    if isinstance(v, (list, tuple)) and type_ in ("text", "code", "status") and spec is None:
        return ", ".join(fmt_value(x, type_) for x in v) or "—"
    if type_ == "artifact_ref":
        return (f"{v.get('artifact')}/{v.get('path')}" if v.get("path") else str(v.get("digest", ""))[:12]) if isinstance(v, dict) else "—"
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


SUBJECTS = ("job", "node", "campaign")


def subject(ctx: dict, name: str):
    """The id of the page's subject (`$job`, `$node`, `$campaign`): a job, node or campaign panel's context holds the
    subject as an object with an `id` (what `when` reads, e.g. job.module)."""
    v = ctx.get(name)
    return v.get("id") if isinstance(v, dict) else v


def interpolate(v, ctx: dict, row: dict | None = None):
    if isinstance(v, str) and v.startswith("$"):
        if v.startswith("$row."):
            return (row or {}).get(v[5:])
        if v[1:] in SUBJECTS:
            return subject(ctx, v[1:])
        path = v[1:].split(".")
        cur: Any = ctx
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur
    return v


def frame_context(ctx: dict) -> dict:
    """What a frame is told about where it is shown (the bridge's first message): its subject and the page's route and
    filter variables. A convenience for interpolation, never an authority: the host scopes every read to the module."""
    out = {k: subject(ctx, k) for k in SUBJECTS if subject(ctx, k) is not None}
    for k in ("route", "var"):
        if ctx.get(k):
            out[k] = {a: b for a, b in ctx[k].items() if isinstance(b, (str, int, float, bool))}
    return out


ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def role_ok(meta: dict | None, ctx: dict) -> bool:
    """Whether the viewer's role (ctx user.role) meets an operation's min_role. A host that names no role (a preview
    without one) is not restricted; oarbankd checks every operation anyway."""
    role = (ctx.get("user") or {}).get("role")
    need = (meta or {}).get("min_role") or "viewer"
    return role is None or ROLE_RANK.get(role, -1) >= ROLE_RANK.get(need, 0)


AGG = {"count": len, "mean": lambda v: statistics.mean(v) if v else None,
       "median": lambda v: statistics.median(v) if v else None, "min": lambda v: min(v) if v else None,
       "max": lambda v: max(v) if v else None, "sum": lambda v: sum(v) if v else 0,
       "p95": lambda v: sorted(v)[min(len(v) - 1, int(0.95 * len(v)))] if v else None}


def shape(rows: list[dict], src: U.Source) -> list[dict]:
    """A host query's rows shaped as its source asks (spec/ui-contract.md, Data): `group_by` with `agg`, then `order_by`
    (`descending`), then `fields`, then `limit`. The console and the preview both use it."""
    if src.agg:
        groups: dict = {}
        for x in rows:
            groups.setdefault(x.get(src.group_by) if src.group_by else None, []).append(x)
        out = []
        for g, xs in groups.items():
            rec = {src.group_by: g} if src.group_by else {}
            for f, fn in src.agg.items():
                vals = xs if fn == "count" else [x.get(f) for x in xs
                                                 if isinstance(x.get(f), (int, float)) and not isinstance(x.get(f), bool)]
                rec[f] = AGG[fn](vals)
            out.append(rec)
        rows = out
    if src.order_by:
        rows = sorted(rows, key=lambda x: (x.get(src.order_by) is None, x.get(src.order_by)), reverse=src.descending)
    if src.fields:
        keep = set(src.fields) | ({src.group_by} if src.group_by else set()) | set(src.agg)
        rows = [{k: v for k, v in x.items() if k in keep} for x in rows]
    return rows[:src.limit]


def filter_params(src: U.Source) -> dict:
    """The params a host query filters on (ui.QUERY_PARAMS), with interpolated values; the rest are ignored."""
    return {k: v for k, v in src.params.items() if k in U.QUERY_PARAMS.get(src.query or "", ()) and v is not None}


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


def first_row(d: dict) -> dict:
    """The row a single-value component reads: a kv answer, else the first row."""
    return d.get("kv") or ((d.get("rows") or [{}])[0]) or {}


TONE_CLASS = {"ok": "ok", "warn": "warn", "error": "bad", "info": "acc", "neutral": "", "running": "acc"}
TIER_CLASS = {"T0": "", "T1": "", "T2": "pri", "T3": "danger"}


def _link(d: dict) -> U.Link | None:
    """A typed reference from a cell value, or None when the value is not one (the cell then shows it as text)."""
    try:
        return U.Link.model_validate(d)
    except ValueError:
        return None


def environment() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")), autoescape=True,
                             undefined=jinja2.ChainableUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(link_obj=_link,
                       extract_col=lambda col, rows: [r.get(col) for r in rows],
                       fmt=fmt_value, md=md_lite, tojson_compact=lambda v: json.dumps(v, separators=(",", ":"), default=str))
    env.globals.update(TONE_CLASS=TONE_CLASS, TIER_CLASS=TIER_CLASS, when_ok=when_ok, interpolate=interpolate, resolve=resolve,
                       minor_of=U.minor_of, first_row=first_row, role_ok=role_ok, frame_context=frame_context)
    return env


_ENV = environment()


def render_page(page: U.Page, host: Host) -> str:
    """HTML for a module page or panel (a fragment; the host supplies the surrounding layout)."""
    return _ENV.get_template("page.html").render(page=page, host=host, ctx={**host.context, "self": host.module})


def render_component(component, host: Host) -> str:
    return _ENV.get_template("page.html").render(page=U.Page(body=[component]), host=host,
                                                 ctx={**host.context, "self": host.module})
