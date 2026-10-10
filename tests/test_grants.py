"""What a runner gets beyond its directories: host tools (OARBANK_TOOLS_FILE), the settings file, and egress through
the allowlist proxy; and what the conformance kit gives a runner in their place."""
import json
import shutil
import socket
import threading
import tomllib
from pathlib import Path

import pytest

from oarbank_sdk import conformance, egress_proxy as EP, tools

TOY = Path(__file__).parents[1] / "examples" / "toy"


@pytest.mark.parametrize("host,port,ok", [
    ("api.example.org", 443, True), ("api.example.org", 80, False), ("x.files.example.org", 8443, True),
    ("files.example.org", 8443, False), ("evil.example.org", 443, False), ("127.0.0.1", 443, False),
    ("[::1]", 443, False), ("API.Example.org.", 443, True),
])
def test_allow_list_matching(host, port, ok):
    assert EP.allowed(["api.example.org", "*.files.example.org:8443"], host, port) is ok


def test_names_that_resolve_to_local_addresses_are_refused():
    with pytest.raises(PermissionError):
        EP.resolve_public("localhost", 443)


def _connect(port, target):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    data = s.recv(4096)
    s.close()
    return data.decode(errors="replace")


def test_the_proxy_refuses_hosts_outside_the_list_and_local_services():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    local = srv.getsockname()[1]
    with EP.AllowlistProxy(["localhost:%d" % local, "api.example.org"]) as p:
        assert "403" in _connect(p.port, "evil.example.org:443")
        assert "403" in _connect(p.port, f"127.0.0.1:{local}")             # an IP literal
        assert "403" in _connect(p.port, f"localhost:{local}")             # allowed by name, but it is loopback
        assert len(p.refused) == 3
    srv.close()


def test_tools_file(tmp_path, monkeypatch):
    (tmp_path / "t.json").write_text(json.dumps({"renderer4": [{"path": "/opt/renderer4", "version": "4.2", "arch": "arm64"}]}),
                                     encoding="utf-8", newline="\n")
    monkeypatch.setenv("OARBANK_TOOLS_FILE", str(tmp_path / "t.json"))
    assert tools.path("renderer4") == "/opt/renderer4" and tools.paths("nope") == []
    with pytest.raises(tools.ToolMissing):
        tools.path("nope")


def test_conform_gives_the_runner_its_grants(tmp_path):
    """egress-allowlist starts the proxy, `tools` fixtures are granted and listed, `settings` becomes a file."""
    src = tmp_path / "toy"
    shutil.copytree(TOY, src)
    m = (src / "oarbank-module.toml").read_text(encoding="utf-8")
    m += '\n[sandbox]\nnet = { mode = "egress-allowlist", allow = ["api.example.org"] }\ntools = [{ id = "shell", trust = "code-exec" }]\n'
    (src / "oarbank-module.toml").write_text(m, encoding="utf-8", newline="\n")
    r = (src / "toy_runner.py").read_text(encoding="utf-8")                 # its doctor must be able to read both files (issue #8)
    r = r.replace('"health": "healthy"', '"health": "healthy" if json.load(open(__import__("os").environ["OARBANK_TOOLS_FILE"], encoding="utf-8"))'
                  '.get("shell") and json.load(open(__import__("os").environ["OARBANK_SETTINGS_FILE"], encoding="utf-8")) == {"x": 1} else "unhealthy"')
    (src / "toy_runner.py").write_text(r, encoding="utf-8", newline="\n")
    rep = conformance.conform(src, {"tools": {"shell": "/bin"}, "settings": {"x": 1}})
    runner = {c["name"]: c for c in rep.as_dict()["checks"] if c["suite"] == "runner"}
    assert runner["doctor --json"]["status"] == "pass", runner
    assert runner["doctor --json"]["detail"] == "health healthy", runner
    assert rep.ok, rep.text()
