"""`oarbank-sdk preview <oarbank-module.toml>`: render a module's pages exactly as the console will,
without a fleet.

- Pages and panels are drawn by the published renderer (oarbank_sdk.render) under the console's CSP.
- Host queries read fixtures: `<bundle>/fixtures/ui/queries/<source>.json` (a list of rows), filtered by the params the
  console filters on and shaped by the console's own shaper (render.shape: group_by, agg, order_by, fields, limit).
- Module views are computed by the module itself (`ui.view.compute`) from `<bundle>/fixtures/ui/inputs.json` and
  validated against their declarations as oarbankd validates them (ui.validate_view), so a view shows only its declared
  columns, as in the console.
- Panels render with `<bundle>/fixtures/ui/context.json` (`{"job": {...}, "node": {...}, "campaign": {...}, "user":
  {"role": ...}}`), so `$job`, `$node`, `$campaign` and `when` behave as on a job, node or campaign page; `?role=`
  switches the viewer's role.
- Operations are *not* executed: the preview shows the plan (op.plan) and the effects op.apply would ask the host to
  make. Forms carry a CSRF token as the console's do, and a post without it is refused (403), so a page that works here
  works in the console.
- Sandboxed frames are served from a second origin (port + 1, or any free port when port is 0) with the console's
  frame CSP and bridge, its capabilities checked per frame as the console checks them.
- Media components resolve artifact references from `<bundle>/fixtures/ui/media/`: `{job, artifact, path}` is the file
  `<artifact>/<path>` there, `{digest}` the file with that sha256. The second origin serves them as the console's module
  origin does: only what oarbank_sdk.media allows, sniffed from the bytes, with nosniff and a sandboxing CSP.
- Typed links to console pages (jobs, nodes, datasets, campaigns, the module's core tabs, uploads) go to stand-in pages
  that say what the console shows there, with the fixture row they name.
- `--check` renders every page and panel without a server and runs axe-core over them (Node with axe-core and jsdom
  from OARBANK_SDK_NODE_MODULES); serious or critical violations fail it.
Standard library HTTP server only; it binds 127.0.0.1.
"""
import hashlib
import html
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from . import manifest as mf, media as M, ui as U
from .client import ModuleClient
from .render import (CSS_PATH, HERE as RENDER_HERE, Host, console_csp, filter_params, frame_csp, interpolate, media_csp,
                     render_page, shape)

BASE_CSS = """:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--mut:#5f6b7c;--line:#e4e7ec;--ok:#067647;--warn:#b54708;--bad:#b42318;--acc:#2563d4;--chip:#eef2f7;--okf:#067647;--warnf:#b54708;--badf:#b42318;--accf:#2563d4}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e7e9ee;--mut:#98a2b3;--line:#2a2f3a;--chip:#232833;--acc:#6ea0ff;--ok:#32d583;--warn:#fdb022;--bad:#f97066}}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
body{margin:0;font:14px/1.45 -apple-system,sans-serif;background:var(--bg);color:var(--fg)} main{padding:16px;max-width:1200px;margin:0 auto}
header{padding:10px 16px;border-bottom:1px solid var(--line);background:var(--card)} a{color:var(--acc)}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--chip);font-size:12px;color:var(--mut)}
.chip.ok{background:var(--okf);color:#fff}.chip.warn{background:var(--warnf);color:#fff}.chip.bad{background:var(--badf);color:#fff}.chip.acc{background:var(--accf);color:#fff}
table{width:100%;border-collapse:collapse;background:var(--card)} th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
td.num,th.num{text-align:right} .mut{color:var(--mut)} .small{font-size:12px} .pos{color:var(--ok)} .neg{color:var(--bad)}
button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:7px;padding:4px 10px}
button.pri{background:var(--accf);color:#fff} button.danger{color:var(--bad)} form.inline{display:inline}
.bar{height:8px;background:var(--chip);border-radius:4px;overflow:hidden}.bar>i{display:block;height:100%;background:var(--acc)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px} pre{white-space:pre-wrap;background:var(--chip);padding:8px;border-radius:6px}"""
# the console's form behaviour (its app.js): the CSRF field on every POST form, then the T1 confirmation
FORMS_JS = """document.addEventListener("submit",function(e){var f=e.target;if((f.method||"").toLowerCase()==="post"&&!f.querySelector('input[name="csrf"]')){var c=document.createElement("input");c.type="hidden";c.name="csrf";c.value=document.querySelector('meta[name="csrf-token"]').content;f.appendChild(c);}if(f.dataset&&f.dataset.confirm&&!confirm(f.dataset.confirm))e.preventDefault();},true);"""
STAND_IN = {"job": ("jobs", "job_id", "the job's page: its state, attempts, result and logs"),
            "node": ("nodes", "node_id", "the node's page: its hardware, services, modules and attempts"),
            "dataset": ("datasets", "dataset_id", "the dataset's page: its owner, files and downloads"),
            "campaign": ("campaigns", "campaign_id", "the campaign's page: its jobs, results and artifacts download")}
AXE_NODE_MODULES = "OARBANK_SDK_NODE_MODULES"


class Preview:
    def __init__(self, manifest_path: str, port: int = 8700, python: str = sys.executable):
        self.path = Path(manifest_path).resolve()
        self.root = self.path.parent
        self.man = mf.load(self.path)
        self.name = self.man.module.id.rsplit(".", 1)[-1]
        self.port, self.frame_port = port, port + 1 if port else 0
        self.fix = self.root / "fixtures" / "ui"
        self.settings = self._json(self.fix / "settings.json", {})
        self.context = self._json(self.fix / "context.json", {})
        self.csrf = secrets.token_urlsafe(24)
        argv = mf.resolve_exec(self.man.coordinator.exec, self.root, python)
        self.client = ModuleClient.spawn(argv, cwd=self.root)
        self.client.initialize(settings=self.settings)
        self.views, self.view_problems = {}, {}
        self.recompute()

    @staticmethod
    def _json(p: Path, default):
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    # ---------------------------------------------------------------- data
    def recompute(self):
        """Compute every declared view from the fixture inputs and keep what the console would store: the answer validated
        against its declaration, or the error (`view_problems` also lists artifact references that are not)."""
        inputs = self._json(self.fix / "inputs.json", {})
        for vid, decl in self.man.ui.views.items():
            try:
                doc = U.validate_view(decl, self.client.call("ui.view.compute", {
                    "view_id": vid, "params": {}, "data_version": 1, "inputs": {k: inputs.get(k, []) for k in decl.inputs}}))
                self.views[vid], self.view_problems[vid] = doc, U.view_ref_problems(decl, doc)
            except Exception as e:                      # noqa: BLE001 - shown as a placeholder
                self.views[vid], self.view_problems[vid] = {"error": str(e)}, [str(e)]

    def rows(self, src: U.Source) -> list[dict]:
        rows = self._json(self.fix / "queries" / f"{src.query}.json", [])
        for k, v in filter_params(src).items():
            rows = [r for r in rows if str(r.get({"campaign": "campaign_id"}.get(k, k))) == str(v)]
        return rows

    def resolve(self, src: U.Source, ctx) -> dict:
        if src.view:
            return self.views.get(src.view) or {"error": "not computed"}
        return {"rows": shape(self.rows(src), src)}

    def operation(self, op_id: str):
        for o in self.man.operations:
            if op_id == f"mod.{self.name.replace('-', '_')}.{o.verb}":
                return {"id": op_id, "title": o.title, "tier": o.effective_tier(), "summary": o.title, "min_role": o.min_role}
        return {"id": op_id, "title": op_id, "tier": "T1", "summary": "core operation (not executed in preview)",
                "min_role": "operator"}

    def media_file(self, ref) -> Path | None:
        """The fixture file an artifact reference names (fixtures/ui/media/), or None."""
        base = (self.fix / "media").resolve()
        if not isinstance(ref, dict) or not base.is_dir():
            return None
        if ref.get("digest"):
            for f in sorted(base.rglob("*")):
                if f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == ref["digest"]:
                    return f
            return None
        f = (base / str(ref.get("artifact") or "") / str(ref.get("path") or "")).resolve()
        return f if base in f.parents and f.is_file() else None

    def media(self, ref, kind: str) -> dict | None:
        try:
            r = U.ArtifactRef.model_validate(ref)
        except ValueError:                              # not a reference: a placeholder
            return None
        f = self.media_file(ref)
        if f is None or kind not in M.KINDS:
            return None
        url = lambda k, path: f"http://127.0.0.1:{self.frame_port}/b/{k}/{path.relative_to((self.fix / 'media').resolve()).as_posix()}"
        thumb = self.media_file({"digest": r.thumbnail}) if r.thumbnail else None
        return {"src": url(kind, f), "thumb": url(M.THUMBNAIL, thumb) if thumb else None, "job": r.job}

    def link_url(self, link: U.Link) -> str | None:
        """Where a typed reference goes in the preview: the module's own pages, or stand-ins for console pages."""
        for kind in ("job", "node", "dataset", "campaign"):
            v = getattr(link, kind)
            if v is not None:
                return f"/console/{kind}?" + urlencode({"id": v, **({"download": 1} if link.download else {})})
        if link.page:
            return f"/page/{link.page}" if any(d.id == link.page for d in self.man.ui.pages) else None
        if link.tab:
            return f"/console/tab?tab={link.tab}" if link.tab != "secrets" or self.man.secrets else None
        if link.upload:
            return ("/console/upload?" + urlencode({"kind": link.upload.kind, **({"then": link.upload.then} if link.upload.then else {})})
                    if link.upload.kind in self.man.datasets.kinds else None)
        return link.url if link.url and any(link.url.startswith(u) for u in self.man.ui.external_urls) else None

    def frame(self, view: str) -> dict | None:
        decl = next((f for f in self.man.ui.iframes if f.id == view), None)
        return None if decl is None else {"src": f"http://127.0.0.1:{self.frame_port}/f/{view}/", "bridge": list(decl.bridge),
                                          "base": f"/bridge/{view}"}

    def host(self, return_to: str, context: dict) -> Host:
        return Host(resolve=self.resolve, operation=self.operation, op_url=lambda op: f"/op/{op}", media=self.media,
                    link_url=self.link_url, frame=self.frame, schema=lambda p: self._json(self.root / p, {}), module=self.name,
                    context=context, return_to=return_to)

    def context_for(self, decl: U.PageDecl, role: str | None = None) -> dict:
        """A page's or panel's render context: panels get the fixture subject of their slot; every page the viewer."""
        slot = {"job.detail.panel": "job", "node.detail.panel": "node", "campaign.panel": "campaign"}.get(decl.slot)
        ctx = {k: v for k, v in self.context.items() if k == slot}
        user = {**(self.context.get("user") or {}), **({"role": role} if role else {})}
        return {**ctx, **({"user": user} if user else {})}

    def page_html(self, decl: U.PageDecl, role: str | None = None) -> str:
        page = U.Page.model_validate(json.loads((self.root / decl.file).read_text(encoding="utf-8")))
        return render_page(page, self.host(f"/page/{decl.id}", self.context_for(decl, role)))

    def shell(self, title: str, body: str) -> str:
        nav = " · ".join(f'<a href="/page/{d.id}">{html.escape(d.title)}</a>' for d in [*self.man.ui.pages, *self.man.ui.panels])
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="csrf-token" content="{self.csrf}">'
                f'<title>{html.escape(title)}</title><link rel="stylesheet" href="/base.css"><link rel="stylesheet" href="/ui.css">'
                '<script src="/forms.js" defer></script><script src="/ui.js" defer></script></head>'
                f'<body><header><b>preview: {html.escape(self.name)}</b> · <nav aria-label="pages">{nav}</nav></header>'
                f'<main>{body}</main></body></html>')

    def stand_in(self, kind: str, q: dict) -> str:
        """A console page the preview does not have, with the fixture row it names."""
        if kind in STAND_IN:
            source, key, what = STAND_IN[kind]
            row = next((r for r in self._json(self.fix / "queries" / f"{source}.json", []) if str(r.get(key)) == q.get("id")), None)
            what = "its download (a zip of its files)" if q.get("download") else what
            return (f"<h1>{kind} {html.escape(q.get('id', ''))}</h1><p>In the console this link opens {what}.</p>"
                    + (f"<pre>{html.escape(json.dumps(row, indent=1))}</pre>" if row else
                       f"<p class=\"mut\">No row with this id in fixtures/ui/queries/{source}.json.</p>"))
        if kind == "tab":
            return f"<h1>{html.escape(q.get('tab', ''))}</h1><p>In the console this link opens {self.name}'s own core tab.</p>"
        if kind == "upload":
            then = f", then offers {html.escape(q['then'])} on the new dataset" if q.get("then") else ""
            return (f"<h1>Upload a folder</h1><p>In the console this link opens the folder upload with {self.name} and kind "
                    f"<code>{html.escape(q.get('kind', ''))}</code> filled in{then}.</p>")
        return "<p>no such page</p>"

    # ---------------------------------------------------------------- the bridge (the console's checks)
    def bridge(self, frame: str, method: str, q: dict) -> tuple[int, dict]:
        decl = next((f for f in self.man.ui.iframes if f.id == frame), None)
        cap = {"view": "read.view", "query": "read.query", "media": "read.media", "link": "navigate",
               "op": "request.operation"}.get(method.split("/", 1)[0])
        if decl is None:
            return 404, {"error": "unknown frame"}
        if cap not in decl.bridge:
            return 403, {"error": "capability", "detail": f"frame {frame} does not declare {cap}"}
        ctx = json.loads(q.get("ctx", "{}") or "{}")
        if method.startswith("view/"):
            return 200, self.views.get(method[5:], {"error": "unknown view"})
        if method == "query":
            src = U.Source.model_validate(json.loads(q.get("spec", "{}")))
            if src.view:
                return 400, {"error": "invalid query"}
            src = src.model_copy(update={"params": {k: interpolate(v, ctx) for k, v in src.params.items()}})
            return 200, self.resolve(src, ctx)
        if method == "media":
            hit = self.media(json.loads(q.get("ref", "{}")), q.get("kind", ""))
            return (200, hit) if hit else (404, {"error": "not this module's artifact"})
        if method == "link":
            link = U.Link.model_validate(json.loads(q.get("to", "{}")))
            href = None if link.url else self.link_url(link)
            return (200, {"href": href}) if href else (404, {"error": "no such page"})
        meta = self.operation(method[3:])
        if meta["id"].startswith("mod.") and not meta["id"].startswith(f"mod.{self.name.replace('-', '_')}."):
            return 403, {"error": "another module's operation"}
        return 200, meta

    def csrf_refusal(self, headers, form: dict) -> str | None:
        """Why a POST is refused as the console refuses it (None: it is not): the session's CSRF token, Fetch Metadata and
        Origin."""
        got = (form.get("csrf") or [None])[0] or headers.get("x-csrf-token")
        if not got or not secrets.compare_digest(str(got), self.csrf):
            return "missing or wrong CSRF token"
        site, origin = headers.get("sec-fetch-site"), headers.get("origin")
        if site and site not in ("same-origin", "none"):
            return f"sec-fetch-site {site}"
        if not site and origin and origin.split("://", 1)[-1] != headers.get("host"):
            return f"origin {origin}"
        return None

    # ---------------------------------------------------------------- accessibility
    def documents(self) -> dict[str, str]:
        """Every page and panel as the full document the preview serves (what axe checks)."""
        return {d.id: self.shell(d.title, self.page_html(d)) for d in [*self.man.ui.pages, *self.man.ui.panels]}

    def axe(self, node_modules: str | None = None) -> tuple[list[str] | None, str]:
        """(serious or critical axe violations per page, what ran) or (None, why it was skipped)."""
        nm = node_modules or os.environ.get(AXE_NODE_MODULES)
        node = shutil.which("node")
        if not node or not nm or not (Path(nm) / "axe-core").is_dir() or not (Path(nm) / "jsdom").is_dir():
            return None, f"needs Node with axe-core and jsdom (set {AXE_NODE_MODULES} to their node_modules)"
        with tempfile.TemporaryDirectory() as d:
            files = []
            for pid, doc in self.documents().items():
                f = Path(d) / f"{pid}.html"
                f.write_text(doc, encoding="utf-8")
                files.append(str(f))
            out = subprocess.run([node, str(RENDER_HERE / "axe_run.cjs"), *files], capture_output=True, text=True,
                                 env={**os.environ, "NODE_PATH": nm}, timeout=600)
        if out.returncode != 0:
            return [f"axe did not run: {out.stderr.strip()[-400:]}"], "axe"
        bad = [f"{Path(r['file']).stem}: {v['id']} ({v['impact']}) {v['help']} at {', '.join(v['nodes'][:2])}"
               for r in json.loads(out.stdout) for v in r["violations"] if v["impact"] in ("serious", "critical")]
        return bad, f"axe over {len(files)} pages and panels"

    # ---------------------------------------------------------------- servers
    def serve(self):
        pv = self

        class Console(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Security-Policy", console_csp(f"http://127.0.0.1:{pv.frame_port}"))
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                parts = u.path.strip("/").split("/")
                static = {"base.css": (BASE_CSS, "text/css"), "ui.css": (CSS_PATH.read_text(encoding="utf-8"), "text/css"),
                          "ui.js": ((RENDER_HERE / "static" / "ui.js").read_text(encoding="utf-8"), "text/javascript"),
                          "forms.js": (FORMS_JS, "text/javascript")}
                if len(parts) == 1 and parts[0] in static:
                    return self.send(200, static[parts[0]][0], static[parts[0]][1])
                if u.path in ("/", "") or (parts[0] == "page" and len(parts) == 2):
                    decls = [*pv.man.ui.pages, *pv.man.ui.panels]
                    decl = decls[0] if u.path in ("/", "") and decls else next((d for d in decls if d.id == parts[-1]), None)
                    if decl is None:
                        return self.send(404, pv.shell("not found", "<p>no such page</p>"))
                    return self.send(200, pv.shell(decl.title, pv.page_html(decl, q.get("role"))))
                if parts[0] == "console" and len(parts) == 2:
                    return self.send(200, pv.shell("console page", pv.stand_in(parts[1], q)))
                if parts[0] == "bridge" and len(parts) >= 3:
                    status, doc = pv.bridge(parts[1], "/".join(parts[2:]), q)
                    return self.send(status, json.dumps(doc), "application/json")
                return self.send(404, "not found", "text/plain")

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("content-length") or 0)
                form = parse_qs(self.rfile.read(n).decode())
                if not u.path.startswith("/op/"):
                    return self.send(404, "not found", "text/plain")
                why = pv.csrf_refusal({k.lower(): v for k, v in self.headers.items()}, form)
                if why:
                    return self.send(403, json.dumps({"error": "csrf", "detail": why}), "application/json")
                op_id = u.path[4:]
                params = json.loads(form["params"][0]) if form.get("params", [""])[0].strip() else {}
                params.update({k[2:]: v[0] for k, v in form.items() if k.startswith("p.")})
                params.update({k[3:]: json.loads(v[0]) for k, v in form.items() if k.startswith("pj.") and v[0].strip()})
                verb = op_id.rsplit(".", 1)[-1]
                decl = next((o for o in pv.man.operations if op_id.endswith("." + o.verb)), None)
                target = form.get("target", [""])[0] or None
                out = {"operation": op_id, "target": target, "params": params, "note": "preview: nothing is executed"}
                if decl:
                    try:
                        if decl.preview:
                            out["plan"] = pv.client.call("op.plan", {"verb": verb, "params": params, "target": target,
                                                                     "actor": "preview"})
                        out["apply"] = pv.client.call("op.apply", {"verb": verb, "params": params, "target": target,
                                                                   "actor": "preview"})
                    except Exception as e:              # noqa: BLE001
                        out["error"] = str(e)
                body = f"<h2>{html.escape(pv.operation(op_id)['title'])}</h2><pre>{html.escape(json.dumps(out, indent=1))}</pre>"
                back = form.get("return_to", ["/"])[0]
                back = back if back.startswith("/") and not back.startswith("//") else "/"
                return self.send(200, pv.shell("operation", body + f'<p><a href="{html.escape(back)}">back</a></p>'))

        class Frames(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def media(self, kind: str, rel: str):
                base = (pv.fix / "media").resolve()
                f = (base / rel).resolve()
                if base not in f.parents or not f.is_file() or kind not in M.CAPS:
                    self.send_response(404)
                    self.end_headers()
                    return
                head = f.read_bytes()[:M.HEAD_BYTES]
                why = M.problem(kind, head, f.stat().st_size)
                self.send_response(415 if why and "type" in why else 413 if why else 200)
                self.send_header("Content-Type", M.sniff(kind, head) or "text/plain; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", media_csp(f"http://127.0.0.1:{pv.port}"))
                self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
                self.end_headers()
                self.wfile.write(why.encode() if why else f.read_bytes())

            def do_GET(self):
                parts = urlparse(self.path).path.strip("/").split("/", 2)
                if len(parts) == 3 and parts[0] == "b":
                    return self.media(parts[1], parts[2])
                decl = next((f for f in pv.man.ui.iframes if len(parts) >= 2 and parts[0] == "f" and f.id == parts[1]), None)
                ok = False
                if decl:
                    entry = (pv.root / decl.entry).resolve()
                    target = entry if len(parts) < 3 or not parts[2] else (entry.parent / parts[2]).resolve()
                    ok = target.parent == entry.parent and target.is_file() and target.suffix in (".html", ".js", ".css", ".json", ".png", ".svg")
                if not ok:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                                                  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml"}[target.suffix])
                self.send_header("Content-Security-Policy", frame_csp(f"http://127.0.0.1:{pv.port}"))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(target.read_bytes())

        self.console = ThreadingHTTPServer(("127.0.0.1", self.port), Console)
        self.frames = ThreadingHTTPServer(("127.0.0.1", self.frame_port), Frames)
        self.port, self.frame_port = self.console.server_address[1], self.frames.server_address[1]
        threading.Thread(target=self.frames.serve_forever, daemon=True).start()
        return self.console

    def close(self):
        for s in (getattr(self, "console", None), getattr(self, "frames", None)):
            if s:
                s.shutdown()
                s.server_close()
        self.client.close()


def check(manifest_path: str, node_modules: str | None = None) -> int:
    """`oarbank-sdk preview --check`: render every page and panel with the fixtures, report the views the console would
    refuse, and run axe. 0 when nothing serious was found (a skipped axe run is said, and not a failure)."""
    pv = Preview(manifest_path, port=0)
    try:
        bad = [f"view {v}: {p}" for v, ps in pv.view_problems.items() for p in ps]
        try:
            pv.documents()
        except Exception as e:                          # noqa: BLE001 - a page that cannot render is the finding
            bad.append(f"render: {type(e).__name__}: {e}")
        found, ran = pv.axe(node_modules) if not bad else (None, "not run: fix the render first")
        for line in bad + (found or []):
            print(f"FAIL {line}")
        print(f"{pv.name}: {ran}" if found is not None else f"{pv.name}: axe skipped: {ran}")
        return 1 if bad or found else 0
    finally:
        pv.close()


def main(manifest_path: str, port: int = 8700) -> int:
    pv = Preview(manifest_path, port)
    srv = pv.serve()
    print(f"preview of {pv.name}: http://127.0.0.1:{pv.port}/  (frames on :{pv.frame_port}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        pv.close()
    return 0
