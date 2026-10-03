"""conform runs a module's non-golden runner specs (fixtures `runner_specs`) as it runs goldens: sandboxed, through the
egress proxy, checked against their expected outcome (spec/conformance.md, "Fixtures")."""
import http.server
import shutil
import socket
import threading
from pathlib import Path

import pytest

from oarbank_sdk import egress_proxy as EP
from oarbank_sdk.conformance import conform

TOY = Path(__file__).parents[1] / "examples" / "toy"

# A fetch task for the toy runner: download payload.url (through HTTP(S)_PROXY, as urllib does) into one artifact.
FETCH = '''
    if "url" in spec.get("payload", {}):
        import urllib.request
        try:
            data = urllib.request.urlopen(spec["payload"]["url"], timeout=20).read()
        except OSError as e:
            data = repr(e).encode()                    # coping with a refusal does not hide it from the kit
        (workdir / "out").mkdir()
        (workdir / "out" / "f.bin").write_bytes(data)
        atomic_write(out, {"envelope": 1, "schema": "toy/result@1", "module_version": "0.1.0", "protocol": 1,
                           "artifacts": [{"name": spec["payload"].get("artifact", "files"),
                                          "files": [{"path": "f.bin", "local": "out/f.bin"}]}],
                           "payload": {"bytes": len(data)}})
        return 0
'''


def toy(tmp_path, allow: list[str] | None = None) -> Path:
    d = tmp_path / "toy"
    shutil.copytree(TOY, d, ignore=shutil.ignore_patterns("__pycache__", "dist"))
    r = (d / "toy_runner.py").read_text(encoding="utf-8")
    (d / "toy_runner.py").write_text(r.replace('    n = spec.get("payload", {}).get("n")\n',
                                               FETCH + '    n = spec.get("payload", {}).get("n")\n'), encoding="utf-8", newline="\n")
    if allow:
        m = (d / "oarbank-module.toml").read_text(encoding="utf-8")
        m += "\n[sandbox]\nnet = { mode = \"egress-allowlist\", allow = [" + ", ".join(f'"{a}"' for a in allow) + "] }\n"
        (d / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    return d


def checks(rep, prefix: str) -> dict:
    return {c.name[len(prefix) + 2:]: (c.status, c.detail) for c in rep.checks if c.name.startswith(prefix + ":") or c.name == prefix}


def test_specs_pass_or_fail_on_exit_code_failure_reason_and_artifact_names(tmp_path):
    rep = conform(toy(tmp_path), {"runner_specs": [
        {"name": "sum", "payload": {"n": 5}, "expect": {"artifacts": []}},
        {"name": "bad-spec", "payload": {"n": -1}, "expect": {"exit": 2, "reason": "toy/bad_spec"}},
        {"name": "wrong-exit", "payload": {"n": -1}},
        {"name": "wrong-reason", "payload": {"n": -1}, "expect": {"exit": 2, "reason": "toy/other"}},
        {"name": "bad-name", "payload": {"url": "http://example.invalid/", "artifact": "Files-1"}},
        {"name": "missing", "payload": {"n": 1}, "datasets": ["scene:nowhere"]},
        {"name": "typo", "stage": "run", "payload": {}, "expect": {"exti": 1}},
    ]})
    assert checks(rep, "runner spec sum") == {"exit 0": ("pass", "exit 0"), "artifacts": ("pass", "wrote [], expected []")}
    assert {k: v[0] for k, v in checks(rep, "runner spec bad-spec").items()} == {"exit 2": "pass", "failure reason": "pass"}
    assert checks(rep, "runner spec wrong-exit")["exit 0"] == ("fail", "exit 2")
    assert checks(rep, "runner spec wrong-reason")["failure reason"][0] == "fail"
    bad = checks(rep, "runner spec bad-name")
    assert bad["result envelope valid"][0] == "fail" and "name" in bad["result envelope valid"][1]   # not a Name
    assert checks(rep, "runner spec missing")[""][0] == "skip"
    assert any(c.name == "runner spec #6: fixture" and c.status == "fail" for c in rep.checks)       # the typo `exti`
    assert not rep.ok


class _Origin(http.server.BaseHTTPRequestHandler):
    """release.test redirects to assets.test, which serves the file; the proxy forwards absolute-URI requests."""
    hits: list = []

    def do_GET(self):
        host = self.path.split("/")[2].split(":")[0]
        _Origin.hits.append(host)
        if host == "release.test":
            self.send_response(302)
            self.send_header("Location", f"http://assets.test:{self.server.server_port}/file")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b"asset bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin(monkeypatch):
    """A local origin for the *.test names: the proxy resolves them to it (real names must resolve publicly)."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = EP.resolve_public
    monkeypatch.setattr(EP, "resolve_public", lambda host, port: socket.getaddrinfo("127.0.0.1", port, type=socket.SOCK_STREAM)
                        if host.endswith(".test") else real(host, port))
    _Origin.hits.clear()
    yield srv.server_port
    srv.shutdown()


def test_a_redirect_to_an_allowed_host_works_and_egress_outside_the_list_fails(tmp_path, origin):
    port = origin
    d = toy(tmp_path, allow=[f"release.test:{port}", f"assets.test:{port}"])
    rep = conform(d, {"runner_specs": [
        {"name": "fetch-tools", "payload": {"url": f"http://release.test:{port}/dl"}, "expect": {"artifacts": ["files"]}},
        {"name": "sneaky", "payload": {"url": f"http://evil.test:{port}/x"}},
    ]})
    fetch = checks(rep, "runner spec fetch-tools")
    assert fetch == {"exit 0": ("pass", "exit 0"), "artifacts": ("pass", "wrote ['files'], expected ['files']")}, rep.text()
    assert _Origin.hits == ["release.test", "assets.test"]              # followed the redirect, through the proxy
    sneaky = checks(rep, "runner spec sneaky")
    assert sneaky["exit 0"][0] == "pass"                                # the runner coped with the 403
    assert sneaky["egress within the allowlist"] == ("fail", f"evil.test:{port} is not in the module's allow list")
    assert not rep.ok
