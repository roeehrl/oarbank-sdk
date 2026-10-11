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
    assert [o.attempt for _, o in accepted] == [3, 3, 3]
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
    assert accepted[-1][1].attempt == 4
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


# ---------------------------------------------------------------------------- inbound listeners

def _service_with_listeners(monkeypatch, tmp_path, seed: dict | None = None, **kw):
    """An in-process acceptor on a ServiceHost, started with OARBANK_LISTENERS_FILE (`seed`)."""
    host = H.ServiceHost()
    monkeypatch.setenv(ep.CHANNEL_ENV, H.here(host.env)[ep.CHANNEL_ENV])
    if seed is not None:
        monkeypatch.setenv(ep.LISTENERS_ENV, H.write_listeners_file(tmp_path / "listeners.json", seed))
    host.spawned()
    acc = ep.Acceptor(**kw)
    host.wait_hello()
    return host, acc


def test_listener_connections_and_announcements_arrive_on_the_endpoint_channel(monkeypatch, tmp_path):
    seen = []
    seed = {"peer": {"external": None, "external_v6": None, "state": "unmapped", "since": 1.0}}
    host, acc = _service_with_listeners(monkeypatch, tmp_path, seed, on_listener=lambda n, a: seen.append((n, a["state"])))
    try:
        assert acc.listeners == seed and acc.external("peer") is None
        ann = host.announce("peer", "198.51.100.7:9000", "[2001:db8::7]:9000", "reachable", since=5.0)
        n, client = host.listener_connection("peer", "203.0.113.5:51000")
        conn, origin = acc.accept()                              # the announcement was handled first
        assert seen == [("peer", "reachable")] and acc.listeners["peer"] == ann
        assert acc.external("peer") == "198.51.100.7:9000"
        assert (origin.conn, origin.attempt, origin.listener, origin.peer) == (n, None, "peer", "203.0.113.5:51000")
        assert origin.client_address == ("203.0.113.5", 51000)
        assert host.wait_accepted(n)
        client.sendall(b"ping")
        assert conn.recv(4) == b"ping"
        conn.sendall(b"pong")
        assert client.recv(4) == b"pong"
        # a job's connection on the same channel still names its attempt
        job = H.connect_here(host, 9)
        jconn, jorigin = acc.accept()
        assert (jorigin.attempt, jorigin.listener, jorigin.client_address) == (9, None, ("attempt-9", 0))
        # `ended` concerns attempts only: a listener connection stays up
        host.ended(9)
        host.announce("peer", "198.51.100.8:9000", None, "mapped_unverified")
        late, _ = host.listener_connection("peer", "[2001:db8::5]:4000")
        _, o2 = acc.accept()
        assert o2.client_address == ("2001:db8::5", 4000) and acc.external("peer") == "198.51.100.8:9000"
        job.settimeout(5)
        assert job.recv(1) == b""                                # the attempt's connection was cut
        client.sendall(b"x")
        assert conn.recv(1) == b"x"
        with pytest.raises(ValueError):
            host.announce("peer", None, state="mapped")
    finally:
        host.close()
    with pytest.raises(ep.ChannelClosed):
        acc.accept()
    client.settimeout(5)
    assert client.recv(1) == b""                                 # closing the acceptor cut the listener connection


def test_an_unreadable_listeners_file_starts_empty(monkeypatch, tmp_path):
    (tmp_path / "bad.json").write_text("[not json")
    host = H.ServiceHost()
    monkeypatch.setenv(ep.CHANNEL_ENV, H.here(host.env)[ep.CHANNEL_ENV])
    monkeypatch.setenv(ep.LISTENERS_ENV, str(tmp_path / "bad.json"))
    host.spawned()
    acc = ep.Acceptor()
    host.wait_hello()
    assert acc.listeners == {}
    host.close()
    acc.close()


def test_narrowing_goes_in_lines_under_the_limit_and_the_agent_keeps_the_intersection_input(monkeypatch, tmp_path):
    nets = [f"2001:db8:{i + 1:x}::/48" for i in range(400)] + ["2001:db8::/32", "192.0.2.7"]
    msgs = ep.listener_allow_messages("peer", nets)
    assert len(msgs) > 1
    assert all(len(json.dumps(m, separators=(",", ":")).encode()) + 1 <= ep.MAX_LINE for m in msgs)
    assert [m["append"] for m in msgs] == [False] + [True] * (len(msgs) - 1)
    assert [c for m in msgs for c in m["cidrs"]] == nets[:-1] + ["192.0.2.7/32"]
    assert ep.listener_allow_messages("peer", None) == [{"op": "listener_allow", "listener": "peer", "cidrs": None,
                                                        "append": False}]
    assert ep.listener_allow_messages("peer", []) == [{"op": "listener_allow", "listener": "peer", "cidrs": [],
                                                      "append": False}]   # nobody may connect
    assert ep.listener_allow_messages("peer", [], append=True) == []
    with pytest.raises(ValueError, match="not an IP network"):
        ep.listener_allow_messages("peer", ["validators.example.org"])
    with pytest.raises(TypeError):
        ep.listener_allow_messages("peer", "198.51.100.0/24")
    host, acc = _service_with_listeners(monkeypatch, tmp_path)
    try:
        sent = acc.narrow("peer", nets)
        assert sent == len(msgs) and host.wait_allows(sent)
        assert host.narrowed["peer"] == nets[:-1] + ["192.0.2.7/32"]
        acc.narrow("peer", ["198.51.100.0/24"], append=True)
        assert host.wait_allows(sent + 1) and host.narrowed["peer"][-1] == "198.51.100.0/24"
        acc.narrow("peer", ["203.0.113.0/24"])
        assert host.wait_allows(sent + 2) and host.narrowed["peer"] == ["203.0.113.0/24"]
        acc.narrow("peer", None)
        assert host.wait_allows(sent + 3) and "peer" not in host.narrowed
    finally:
        host.close()
        acc.close()


SIG = b"\r\n\r\n\x00\r\nQUIT\n"


def _feed(data: bytes):
    a, b = H._pair(ours_overlapped=False)
    a.sendall(data)
    return a, b


@pytest.mark.parametrize("data, want", [
    # PROXY, TCP over IPv4: 203.0.113.5:51000 -> 192.0.2.10:41000
    (SIG + b"\x21\x11\x00\x0c" + bytes([203, 0, 113, 5, 192, 0, 2, 10]) + (51000).to_bytes(2, "big")
     + (41000).to_bytes(2, "big"), (("203.0.113.5", 51000), ("192.0.2.10", 41000))),
    # PROXY, TCP over IPv6, with a TLV (type 0x04, NOOP, 3 bytes) after the addresses
    (SIG + b"\x21\x21\x00\x2a" + bytes.fromhex("20010db8000000000000000000000005") + bytes.fromhex("20010db8000000000000000000000007")
     + (4000).to_bytes(2, "big") + (9000).to_bytes(2, "big") + b"\x04\x00\x03abc",
     (("2001:db8::5", 4000), ("2001:db8::7", 9000))),
    # LOCAL (a health check from the proxy itself), no addresses
    (SIG + b"\x20\x00\x00\x00", (None, None)),
    # PROXY over an unspecified family
    (SIG + b"\x21\x00\x00\x00", (None, None)),
])
def test_read_proxy_v2_reads_the_header_and_nothing_after_it(data, want):
    a, b = _feed(data + b"GET / HTTP/1.1\r\n")
    try:
        assert ep.read_proxy_v2(b) == want
        assert b.recv(5) == b"GET /"
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("data, match", [
    (b"PROXY TCP4 203.0.113.5 192.0.2.10 51000 41000\r\n", "no PROXY v2 signature"),
    (SIG + b"\x11\x11\x00\x0c" + bytes(12), "not v2 LOCAL or PROXY"),
    (SIG + b"\x22\x11\x00\x0c" + bytes(12), "not v2 LOCAL or PROXY"),
    (SIG + b"\x21\x11\x00\x04" + bytes(4), "family 0x11 with 4 address bytes"),
    (SIG + b"\x21\x11\x00\x0c" + bytes(5), "ended after 5 of 12"),
])
def test_read_proxy_v2_refuses_what_is_not_a_v2_header(data, match):
    a, b = _feed(data)
    a.close()                                                    # what was sent is all there is
    try:
        with pytest.raises(ep.ProxyHeaderError, match=match):
            ep.read_proxy_v2(b)
    finally:
        b.close()


def test_the_agent_header_is_the_spec_header():
    v4 = ep.proxy_v2_header(("203.0.113.5", 51000), ("192.0.2.10", 41000))
    assert v4 == (SIG + b"\x21\x11\x00\x0c" + bytes([203, 0, 113, 5, 192, 0, 2, 10]) + bytes.fromhex("c738a028"))
    v6 = ep.proxy_v2_header(("2001:db8::5", 4000), ("2001:db8::7", 9000))
    assert v6[12:16] == b"\x21\x21\x00\x24" and len(v6) == 16 + 36
    assert H.proxy_for("203.0.113.5:51000", "192.0.2.10:41000") == (("203.0.113.5", 51000), ("192.0.2.10", 41000))


class WhoAmI(BaseHTTPRequestHandler):
    def do_GET(self):
        o = self.server.origin
        out = json.dumps({"client": list(self.client_address), "listener": o.listener, "peer": o.peer,
                          "attempt": o.attempt, "proxied": o.proxied and [list(x) for x in o.proxied]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _get(conn) -> dict:
    conn.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    data = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
    return json.loads(data.split(b"\r\n\r\n", 1)[1])


def test_serve_http_serves_listener_connections_with_their_origin_and_proxy_header(monkeypatch, tmp_path):
    host, acc = _service_with_listeners(monkeypatch, tmp_path)
    t = threading.Thread(target=ep.serve_http, args=(WhoAmI, acc), kwargs={"proxy": ["relay"]}, daemon=True)
    t.start()
    try:
        _, c = host.listener_connection("web", "198.51.100.9:6000")
        assert _get(c) == {"client": ["198.51.100.9", 6000], "listener": "web", "peer": "198.51.100.9:6000",
                           "attempt": None, "proxied": None}
        _, c = host.listener_connection("relay", "192.0.2.99:1", proxy=(("203.0.113.5", 51000), ("192.0.2.10", 41000)))
        assert _get(c) == {"client": ["203.0.113.5", 51000], "listener": "relay", "peer": "192.0.2.99:1", "attempt": None,
                           "proxied": [["203.0.113.5", 51000], ["192.0.2.10", 41000]]}
        assert _get(H.connect_here(host, 4))["client"] == ["attempt-4", 0]
    finally:
        host.close()
        t.join(10)
    assert not t.is_alive()


posix = pytest.mark.skipif(sys.platform == "win32", reason="listen_fd listeners exist on macOS and Linux only")


@posix
def test_a_listen_fd_listener_is_an_inherited_listening_socket_the_service_only_accepts_on(monkeypatch):
    lf = H.ListenFdHost("peer")
    try:
        assert oct(os.stat(lf.dir).st_mode & 0o777) == "0o700"
        monkeypatch.setenv("OARBANK_LISTEN_FD_PEER", H.here(lf.env)["OARBANK_LISTEN_FD_PEER"])
        srv = ep.listen_socket("peer")
        c = lf.connect("203.0.113.5:51000", H.proxy_for("203.0.113.5:51000", "192.0.2.10:41000"))
        conn, _ = srv.accept()
        assert ep.read_proxy_v2(conn) == (("203.0.113.5", 51000), ("192.0.2.10", 41000))
        c.sendall(b"hi")
        assert conn.recv(2) == b"hi"
        conn.close()
        c.close()
        srv.close()
        with pytest.raises(ep.EndpointError, match="no_listener"):
            ep.listen_socket("other")
    finally:
        lf.close()


SYSTEMD_SERVER = """
import os, socket, sys
assert os.environ["LISTEN_PID"] == str(os.getpid()), (os.environ["LISTEN_PID"], os.getpid())
assert os.environ["LISTEN_FDS"] == "2" and os.environ["LISTEN_FDNAMES"] == "peer:admin"
assert not any(k.startswith("OARBANK_LISTEN_FD_") for k in os.environ)
for fd, word in ((3, b"peer"), (4, b"admin")):
    s = socket.socket(fileno=fd)
    c, _ = s.accept()
    c.sendall(word)
    c.close()
"""

EXEC = """
import sys
from oarbank_sdk import service_endpoint as ep
ep.exec_with_listen_fds([sys.executable, "-c", sys.argv[1]], ["peer", "admin"])
"""


@posix
def test_exec_with_listen_fds_follows_the_socket_activation_convention(tmp_path):
    hosts = [H.ListenFdHost("peer"), H.ListenFdHost("admin")]
    try:
        env = {**os.environ, "PYTHONPATH": SRC, **hosts[0].env, **hosts[1].env}
        env.pop("PYTHONSAFEPATH", None)
        p = subprocess.Popen([sys.executable, "-c", EXEC, SYSTEMD_SERVER], env=env, stderr=subprocess.PIPE,
                             pass_fds=[h.sock.fileno() for h in hosts])
        for h, word in zip(hosts, (b"peer", b"admin")):
            c = h.connect()
            assert c.recv(16) == word
            c.close()
        _, err = p.communicate(timeout=30)
        assert p.returncode == 0, err.decode()
    finally:
        for h in hosts:
            h.close()
