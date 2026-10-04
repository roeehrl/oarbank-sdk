"""Service endpoints (spec/service-protocol.md, "Endpoints"): the job's client and the service's acceptor, against the
SDK's stand-in for the agent's side (`_endpoint_host`, which the conformance kit uses too). On every OS the SDK suite
runs on: descriptors on macOS and Linux, handles on Windows."""
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from oarbank_sdk import _endpoint_host as H
from oarbank_sdk import service_endpoint as ep

SRC = str(Path(__file__).parents[1] / "src")
MODELSERVER = Path(__file__).parents[1] / "examples" / "modelserver"


class Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                                # keep-alive

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        out = json.dumps({"path": self.path, "got": json.loads(body), "who": self.client_address[0]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def service(monkeypatch):
    """An in-process endpoint service (the Echo handler) on a ServiceHost; yields the host."""
    ep._CONNECTORS.clear()
    host = H.ServiceHost()
    monkeypatch.setenv(ep.CHANNEL_ENV, H.here(host.env)[ep.CHANNEL_ENV])
    host.spawned()
    acc = ep.Acceptor()
    assert ep.CHANNEL_ENV not in os.environ                      # what the service starts does not see it
    host.wait_hello()
    assert host.pid == os.getpid()
    t = threading.Thread(target=ep.serve_http, args=(Echo, acc), daemon=True)
    t.start()
    yield host
    host.close()
    t.join(10)
    assert not t.is_alive(), "serve_http returns when the agent closes the channel"
    ep._CONNECTORS.clear()


def test_a_job_and_a_service_talk_http_over_connections_the_agent_hands_out(service, monkeypatch):
    conn = H.ConnectorHost("echo", service, attempt=7)
    monkeypatch.setenv("OARBANK_SERVICE_ECHO", H.here(conn.env)["OARBANK_SERVICE_ECHO"])
    conn.spawned()
    assert ep.available("echo") and not ep.available("other")
    r = ep.request("echo", "POST", "/v1/x", {"n": 1})
    assert r.status == 200 and r.json() == {"path": "/v1/x", "got": {"n": 1}, "who": "attempt-7"}
    # concurrent requests share the connector, each on its own connection
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda i: ep.request("echo", "POST", "/v1/y", {"i": i}).json()["got"]["i"], range(16)))
    assert got == list(range(16)) and conn.served == 17
    # keep-alive: three requests, one connection
    c = ep.HTTPConnection("echo")
    for i in range(3):
        c.request("POST", "/v1/z", body=json.dumps({"i": i}))
        assert json.loads(c.getresponse().read())["got"] == {"i": i}
    c.close()
    assert conn.served == 18
    conn.close()
    with pytest.raises(ep.ChannelClosed):
        ep.connect("echo")                                       # the attempt ended: the connector is closed


def test_ended_cuts_what_is_left_of_an_attempt_and_the_acceptor_caps_open_connections(monkeypatch):
    ep._CONNECTORS.clear()
    host = H.ServiceHost()
    monkeypatch.setenv(ep.CHANNEL_ENV, H.here(host.env)[ep.CHANNEL_ENV])
    host.spawned()
    acc = ep.Acceptor(max_per_attempt=2)
    host.wait_hello()
    accepted = []

    def take(n):
        for _ in range(n):
            accepted.append(acc.accept())
    jobs = [H.connect_here(host, 3) for _ in range(3)]
    take(3)
    assert [a for _, a in accepted] == [3, 3, 3]
    assert jobs[0].recv(1) == b""                                # the oldest beyond the cap was cut
    jobs[1].sendall(b"x")
    assert accepted[1][0].recv(1) == b"x"
    host.ended(3)
    t = threading.Thread(target=take, args=(1,))
    t.start()                                                    # `ended` is handled inside accept, which then waits
    for j in jobs[1:]:
        j.settimeout(5)
        assert j.recv(1) == b""                                  # what was left of attempt 3 was cut
    late = H.connect_here(host, 4)
    t.join(5)
    assert accepted[-1][1] == 4
    late.sendall(b"y")
    assert accepted[-1][0].recv(1) == b"y"
    host.close()
    with pytest.raises(ep.ChannelClosed):
        acc.accept()


def test_refusals_and_bad_messages():
    ep._CONNECTORS.clear()
    mine, theirs = H._pair()
    os.environ.update(H.here({"OARBANK_SERVICE_NOPE": H._value(theirs)}))
    try:
        lines = H._Lines(mine)

        def refuse():
            assert lines.read()["op"] == "connect"
            lines.send({"ok": False, "error": "service_unavailable", "detail": "not ready"})
        t = threading.Thread(target=refuse)
        t.start()
        with pytest.raises(ep.EndpointError) as e:
            ep.connect("nope")
        t.join()
        assert e.value.code == "service_unavailable"
        with pytest.raises(ep.EndpointError, match="no_endpoint"):
            ep.connect("missing")
        mine.sendall(b"x" * (ep.MAX_LINE + 10))
        with pytest.raises(ep.EndpointError, match="longer than the protocol allows"):
            H._Lines(theirs).read()
    finally:
        del os.environ["OARBANK_SERVICE_NOPE"]
        ep._CONNECTORS.clear()
        mine.close()
    with pytest.raises(ep.EndpointError, match="bad_endpoint"):
        ep._handle("unix:/tmp/x")


def _wait(cond, secs=30):
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _op(env, data, op, **kw):
    return subprocess.run([sys.executable, "-I", str(MODELSERVER / "model_service.py"), op], env={**env, **kw.pop("extra", {})},
                          cwd=MODELSERVER, capture_output=True, text=True, timeout=60, **kw)


JOB = """
import json, sys
from concurrent.futures import ThreadPoolExecutor
from oarbank_sdk import service_endpoint as ep
with ThreadPoolExecutor(3) as pool:
    got = list(pool.map(lambda p: ep.request("model", "POST", "/v1/generate", {"prompt": p}).json(), sys.argv[1:]))
print(json.dumps(got))
"""


def test_the_modelserver_loads_once_for_two_concurrent_jobs(tmp_path):
    """The reference module's service, started as an agent starts it: its daemon inherits the channel, two job processes
    with their own connectors send prompts at once, and the model is loaded once."""
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "settings.json").write_text(json.dumps({"load_s": 0.3, "generate_s": 0.2}))
    env = {**os.environ, "PYTHONPATH": SRC, "OARBANK_MODULE_DATA": str(data), "OARBANK_SETTINGS_FILE": str(tmp_path / "settings.json")}
    env.pop("PYTHONSAFEPATH", None)
    host = H.ServiceHost()
    try:
        r = subprocess.run([sys.executable, str(MODELSERVER / "model_service.py"), "start"], env={**env, **host.env},
                           cwd=MODELSERVER, capture_output=True, text=True, timeout=60, **host.popen_kwargs)
        host.spawned()
        assert r.returncode == 0 and json.loads(r.stdout) == {"ok": True}, r.stderr
        daemon = host.wait_hello()
        assert _wait(lambda: json.loads(_op(env, data, "ready").stdout)["ready"]), (data / "model.log").read_text()
        conns = [H.ConnectorHost("model", host, attempt=a) for a in (11, 12)]
        jobs = []
        for i, c in enumerate(conns):
            p = subprocess.Popen([sys.executable, "-c", JOB, f"a{i}", f"b{i}", f"c{i}"], env={**env, **c.env},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **c.popen_kwargs)
            c.spawned()
            jobs.append(p)
        outs = [p.communicate(timeout=60) for p in jobs]
        assert all(p.returncode == 0 for p in jobs), outs
        answers = [json.loads(o) for o, _ in outs]
        assert {a["pid"] for got in answers for a in got} == {daemon}
        assert [a["text"].split(" -> ")[0] for a in answers[0]] == ["a0", "b0", "c0"]
        assert (data / "model.loads").read_text().split() == [str(daemon)]      # loaded once, for both jobs
        assert sorted(c.served for c in conns) == [3, 3]
        for c in conns:
            c.close()
    finally:
        assert json.loads(_op(env, data, "stop").stdout) == {"ok": True}
        host.close()                                             # the agent closes the channel: the daemon exits
    assert _wait(lambda: not (data / "model.ready").exists() and not (data / "model.up").exists())
