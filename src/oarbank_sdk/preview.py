"""`oarbank-sdk preview <oarbank-module.toml>`: render a module's pages exactly as the console will,
without a fleet.

- Pages and panels are drawn by the published renderer (oarbank_sdk.render) under the console's CSP.
- Host queries read fixtures: `<bundle>/fixtures/ui/queries/<source>.json` (a list of rows).
- Module views are computed by the module itself (`ui.view.compute`) from `<bundle>/fixtures/ui/inputs.json`.
- Operations are *not* executed: the preview shows the plan (op.plan) and the effects op.apply would ask
  the host to make.
- Sandboxed frames are served from a second origin (port + 1) with the console's frame CSP and bridge.
Standard library HTTP server only; it binds 127.0.0.1.
"""
import html
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import manifest as mf, ui as U
from .client import ModuleClient
from .render import CSS_PATH, HERE as RENDER_HERE, Host, render_page

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
CONFIRM_JS = """document.addEventListener("submit",function(e){var f=e.target;if(f.dataset&&f.dataset.confirm&&!confirm(f.dataset.confirm))e.preventDefault();},true);"""


def console_csp(frame_origin: str) -> str:
    return ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
            f"connect-src 'self'; frame-src {frame_origin}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def frame_csp(console_origin: str) -> str:
    return ("sandbox allow-scripts allow-forms; default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors {console_origin}")


class Preview:
    def __init__(self, manifest_path: str, port: int = 8700, python: str = sys.executable):
        self.path = Path(manifest_path).resolve()
        self.root = self.path.parent
        self.man = mf.load(self.path)
        self.name = self.man.module.id.rsplit(".", 1)[-1]
        self.port, self.frame_port = port, port + 1
        self.fix = self.root / "fixtures" / "ui"
        self.settings = self._json(self.fix / "settings.json", {})
        argv = mf.resolve_exec(self.man.coordinator.exec, self.root, python)
        self.client = ModuleClient.spawn(argv, cwd=self.root)
        self.client.initialize(settings=self.settings)
        self.views = {}
        self.recompute()

    @staticmethod
    def _json(p: Path, default):
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    # ---------------------------------------------------------------- data
    def recompute(self):
        inputs = self._json(self.fix / "inputs.json", {})
        for vid, decl in self.man.ui.views.items():
            try:
                self.views[vid] = self.client.call("ui.view.compute", {"view_id": vid, "params": {}, "data_version": 1,
                                                                       "inputs": {k: inputs.get(k, []) for k in decl.inputs}})
            except Exception as e:                      # noqa: BLE001 - shown as a placeholder
                self.views[vid] = {"error": str(e)}

    def resolve(self, src: U.Source, ctx) -> dict:
        if src.view:
            return self.views.get(src.view) or {"error": "not computed"}
        rows = self._json(self.fix / "queries" / f"{src.query}.json", [])
        for k, v in src.params.items():
            if v is not None and k in ("job_id", "node_id", "state", "kind", "dataset_id"):
                rows = [r for r in rows if str(r.get(k)) == str(v)]
        if src.fields:
            rows = [{k: r.get(k) for k in set(src.fields) | set(src.agg) | ({src.group_by} if src.group_by else set())} for r in rows]
        if src.agg:
            rec = {}
            for f, fn in src.agg.items():
                vals = [r.get(f) for r in rows if isinstance(r.get(f), (int, float))]
                rec[f] = len(rows) if fn == "count" else (max(vals) if fn == "max" else min(vals) if fn == "min" else
                                                          (sum(vals) / len(vals) if vals else None))
            rows = [rec]
        return {"rows": rows[:src.limit]}

    def operation(self, op_id: str):
        for o in self.man.operations:
            if op_id == f"mod.{self.name.replace('-', '_')}.{o.verb}":
                return {"id": op_id, "title": o.title, "tier": o.effective_tier(), "summary": o.title}
        return {"id": op_id, "title": op_id, "tier": "T1", "summary": "core operation (not executed in preview)"}

    def host(self, return_to: str) -> Host:
        return Host(resolve=self.resolve, operation=self.operation, op_url=lambda op: f"/op/{op}",
                    link_url=lambda l: f"/page/{l.page}" if l.page else (l.url or "#"),
                    frame_url=lambda v: f"http://127.0.0.1:{self.frame_port}/f/{v}/",
                    schema=lambda p: self._json(self.root / p, {}), module=self.name,
                    context={"bridge_base": "/bridge", "self": self.name}, return_to=return_to)

    def page_html(self, decl: U.PageDecl) -> str:
        page = U.Page.model_validate(json.loads((self.root / decl.file).read_text(encoding="utf-8")))
        return render_page(page, self.host(f"/page/{decl.id}"))

    def shell(self, title: str, body: str) -> str:
        nav = " · ".join(f'<a href="/page/{d.id}">{html.escape(d.title)}</a>' for d in [*self.man.ui.pages, *self.man.ui.panels])
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
                '<link rel="stylesheet" href="/base.css"><link rel="stylesheet" href="/ui.css">'
                '<script src="/confirm.js" defer></script><script src="/ui.js" defer></script></head>'
                f'<body><header><b>preview: {html.escape(self.name)}</b> · {nav}</header><main>{body}</main></body></html>')

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
                parts = u.path.strip("/").split("/")
                static = {"base.css": (BASE_CSS, "text/css"), "ui.css": (CSS_PATH.read_text(encoding="utf-8"), "text/css"),
                          "ui.js": ((RENDER_HERE / "static" / "ui.js").read_text(encoding="utf-8"), "text/javascript"),
                          "confirm.js": (CONFIRM_JS, "text/javascript")}
                if len(parts) == 1 and parts[0] in static:
                    return self.send(200, static[parts[0]][0], static[parts[0]][1])
                if u.path in ("/", "") or (parts[0] == "page" and len(parts) == 2):
                    decls = [*pv.man.ui.pages, *pv.man.ui.panels]
                    decl = decls[0] if u.path in ("/", "") and decls else next((d for d in decls if d.id == parts[-1]), None)
                    if decl is None:
                        return self.send(404, pv.shell("not found", "<p>no such page</p>"))
                    return self.send(200, pv.shell(decl.title, pv.page_html(decl)))
                if parts[0] == "bridge":
                    if parts[1] == "view" and len(parts) == 3:
                        return self.send(200, json.dumps(pv.views.get(parts[2], {"error": "unknown view"})), "application/json")
                    if parts[1] == "query":
                        src = U.Source.model_validate(json.loads(parse_qs(u.query).get("spec", ["{}"])[0]))
                        return self.send(200, json.dumps(pv.resolve(src, {})), "application/json")
                    if parts[1] == "op" and len(parts) == 3:
                        return self.send(200, json.dumps(pv.operation(parts[2])), "application/json")
                return self.send(404, "not found", "text/plain")

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("content-length") or 0)
                form = parse_qs(self.rfile.read(n).decode())
                if not u.path.startswith("/op/"):
                    return self.send(404, "not found", "text/plain")
                op_id = u.path[4:]
                params = {k[2:]: v[0] for k, v in form.items() if k.startswith("p.")}
                params.update({k[3:]: json.loads(v[0]) for k, v in form.items() if k.startswith("pj.") and v[0].strip()})
                verb = op_id.rsplit(".", 1)[-1]
                decl = next((o for o in pv.man.operations if op_id.endswith("." + o.verb)), None)
                out = {"operation": op_id, "params": params, "note": "preview: nothing is executed"}
                if decl:
                    try:
                        if decl.preview:
                            out["plan"] = pv.client.call("op.plan", {"verb": verb, "params": params, "actor": "preview"})
                        out["apply"] = pv.client.call("op.apply", {"verb": verb, "params": params, "actor": "preview"})
                    except Exception as e:              # noqa: BLE001
                        out["error"] = str(e)
                body = f"<h2>{html.escape(pv.operation(op_id)['title'])}</h2><pre>{html.escape(json.dumps(out, indent=1))}</pre>"
                back = form.get("return_to", ["/"])[0]
                return self.send(200, pv.shell("operation", body + f'<p><a href="{html.escape(back)}">back</a></p>'))

        class Frames(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = urlparse(self.path).path.strip("/").split("/", 2)
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
        threading.Thread(target=self.frames.serve_forever, daemon=True).start()
        return self.console

    def close(self):
        for s in (getattr(self, "console", None), getattr(self, "frames", None)):
            if s:
                s.shutdown()
                s.server_close()
        self.client.close()


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
