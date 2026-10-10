"""Service endpoints (spec/service-protocol.md, "Endpoints"): jobs reach a warm per-node service, such as a model server,
without either side ever listening. Standard library only.

A job whose stage reserves a pool of an endpoint service finds a connector in `OARBANK_SERVICE_<NAME>`, an inherited,
already-connected stream to the agent. Each connection it asks for arrives as a fresh connected byte stream; the agent
hands the other end to the service. HTTP/1.1 runs over it:

    from oarbank_sdk import service_endpoint as ep
    r = ep.request("model", "POST", "/v1/generate", {"prompt": "..."})
    text = r.json()["text"]

The service gets `OARBANK_ENDPOINT_CHANNEL` when the agent starts it, says hello on it, and accepts the connections the
agent hands over:

    ep.serve_http(Handler)            # an http.server.BaseHTTPRequestHandler class, a thread per connection

An endpoint service that lists inbound listeners (spec/service-protocol.md, "Listeners") gets the connections the agent
accepts from the internet on the same channel, each with its listener and the client's address (`Origin`), and the
agent's announcements of each listener's external address (`Acceptor.listeners`, `on_listener`, seeded from
OARBANK_LISTENERS_FILE). `Acceptor.narrow` narrows who may connect; `read_proxy_v2` reads the PROXY protocol v2 header
of a listener with `proxy_protocol`; `listen_socket` and `exec_with_listen_fds` serve a `listen_fd` listener.

On macOS and Linux handles travel as file descriptors (`fd:<n>`, SCM_RIGHTS); on Windows as handle values the agent has
already placed in the receiving process (`handle:<n>`). Children of a runner see the connector only if it is passed on
(subprocess `pass_fds`, or `handle_list` on Windows).
"""
import http.client
import io
import ipaddress
import json
import os
import socket
import struct
import sys
import threading
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field

CHANNEL_ENV = "OARBANK_ENDPOINT_CHANNEL"      # the service's endpoint channel
PREFIX = "OARBANK_SERVICE_"                   # + the service name, upper-cased: a job's connector
MAX_LINE = 4096                               # every message is one JSON line of at most this many bytes
MAX_PER_ATTEMPT = 64                          # open connections the acceptor keeps per attempt
LISTENERS_ENV = "OARBANK_LISTENERS_FILE"      # the service's listeners' announcements when it started
LISTEN_FD_PREFIX = "OARBANK_LISTEN_FD_"       # + the listener name, upper-cased: a listen_fd listener's socket
LISTENER_STATES = ("unmapped", "mapped_unverified", "reachable", "unreachable")
WINDOWS = sys.platform == "win32"


class EndpointError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class ChannelClosed(EndpointError):
    """The agent closed the channel or connector (it is stopping the service, or the attempt ended)."""

    def __init__(self, detail: str = "the agent closed the channel"):
        super().__init__("closed", detail)


def env_name(service: str) -> str:
    return PREFIX + service.upper()


def available(service: str) -> bool:
    return bool(os.environ.get(env_name(service)))


def _handle(value: str) -> int:
    kind, _, n = value.partition(":")
    if kind != ("handle" if WINDOWS else "fd") or not n.isdigit():
        raise EndpointError("bad_endpoint", f"{value!r} is not an endpoint handle on this OS")
    return int(n)


# ---------------------------------------------------------------------------- streams

class _Pipe:
    """A Windows pipe handle with the socket methods http.client and http.server use. Synchronous ReadFile and WriteFile
    through ctypes (the agent creates every handle it hands out without FILE_FLAG_OVERLAPPED); a pipe whose other end is
    gone reads as end of file. As for a socket, `close` waits for the files `makefile` returned to close too, so an HTTP
    response is still read after its connection was closed. Windows serialises synchronous I/O on one handle: one thread
    at a time reads or writes (a request, then its response)."""

    _BROKEN = (109, 232, 233)          # ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED

    def __init__(self, handle: int):
        import ctypes
        from ctypes import wintypes
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ct, self._wt = ctypes, wintypes
        self._h = handle
        self._lock = threading.Lock()
        self._refs, self._closed = 0, False
        self.timeout = None

    def fileno(self):
        return self._h

    def recv(self, n: int) -> bytes:
        if self._h is None:
            return b""
        buf = self._ct.create_string_buffer(max(1, n))
        got = self._wt.DWORD(0)
        if not self._k.ReadFile(self._wt.HANDLE(self._h), buf, max(1, n), self._ct.byref(got), None):
            err = self._ct.get_last_error()
            if err in self._BROKEN:
                return b""
            if err != 234:                                       # ERROR_MORE_DATA: a message pipe's partial read
                raise OSError(err, f"ReadFile: {self._ct.FormatError(err)}")
        return buf.raw[:got.value]

    def recv_into(self, b) -> int:
        data = self.recv(len(b))
        b[:len(data)] = data
        return len(data)

    def sendall(self, data) -> None:
        mv = memoryview(bytes(data))
        with self._lock:
            while mv:
                if self._h is None:
                    raise BrokenPipeError("the pipe is closed")
                put = self._wt.DWORD(0)
                if not self._k.WriteFile(self._wt.HANDLE(self._h), bytes(mv[:1 << 20]), min(len(mv), 1 << 20),
                                         self._ct.byref(put), None):
                    err = self._ct.get_last_error()
                    raise BrokenPipeError(err, "the other end is gone") if err in self._BROKEN else OSError(err, "WriteFile")
                mv = mv[put.value:]

    def send(self, data) -> int:
        self.sendall(data)
        return len(data)

    def makefile(self, mode="rb", buffering=None, **_):
        with self._lock:
            self._refs += 1
        if "w" in mode:
            return _SocketLike.writer(self)
        return io.BufferedReader(_SocketLike(self), buffer_size=buffering if buffering and buffering > 0 else 65536)

    def _unref(self):
        with self._lock:
            self._refs -= 1
            last = self._closed and self._refs <= 0
        if last:
            self._real_close()

    def settimeout(self, t):
        self.timeout = t                                         # synchronous pipes have no timeouts: the agent bounds waits

    def setsockopt(self, *a):
        pass

    def shutdown(self, how=None):
        """End the connection for both sides at once (the service holds the pipe's server end)."""
        if self._h is not None:
            self._k.DisconnectNamedPipe(self._wt.HANDLE(self._h))

    def close(self):
        with self._lock:
            self._closed = True
            last = self._refs <= 0
        if last:
            self._real_close()

    def _real_close(self):
        h, self._h = self._h, None
        if h is not None:
            self._k.CloseHandle(self._wt.HANDLE(h))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _SocketLike(io.RawIOBase):
    """The read side of a _Pipe for io.BufferedReader (closing it leaves the pipe open, as a socket's makefile does)."""

    def __init__(self, pipe: _Pipe):
        self._p = pipe

    def readable(self):
        return True

    def readinto(self, b):
        return self._p.recv_into(b)

    def close(self):
        if not self.closed:
            self._p._unref()
        super().close()

    @staticmethod
    def writer(pipe: _Pipe):
        class _W(io.RawIOBase):
            def writable(self):
                return True

            def write(self, b):
                pipe.sendall(b)
                return len(b)

            def close(self):
                if not self.closed:
                    pipe._unref()
                super().close()
        return io.BufferedWriter(_W())


def _wrap(handle: int):
    """A received connection: a socket (POSIX) or a socket-like pipe (Windows)."""
    return _Pipe(handle) if WINDOWS else socket.socket(fileno=handle)


class _Lines:
    """One JSON object per line from a stream, with the descriptors that arrive with them (POSIX: SCM_RIGHTS)."""

    def __init__(self, stream):
        self.s, self.buf, self.fds = stream, b"", []

    def send(self, obj: dict):
        self.s.sendall(json.dumps(obj, separators=(",", ":")).encode() + b"\n")

    def read(self) -> dict | None:
        while b"\n" not in self.buf:
            if len(self.buf) > MAX_LINE:
                raise EndpointError("bad_message", "a line longer than the protocol allows")
            if WINDOWS:
                chunk = self.s.recv(MAX_LINE)
            else:
                chunk, fds, _, _ = socket.recv_fds(self.s, MAX_LINE, 8)
                self.fds.extend(fds)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def take(self, msg: dict) -> int:
        """The handle a message carries: the next descriptor received (POSIX), or its `handle` (Windows)."""
        if WINDOWS:
            return int(msg["handle"])
        if not self.fds:
            raise EndpointError("bad_message", "a connection without its descriptor")
        return self.fds.pop(0)


# ---------------------------------------------------------------------------- the job's side

class _Connector:
    def __init__(self, service: str):
        value = os.environ.get(env_name(service))
        if not value:
            raise EndpointError("no_endpoint", f"this job has no endpoint for service {service!r} (its stage reserves none "
                                               "of the service's pools, or the service is not an endpoint)")
        h = _handle(value)
        self.lines = _Lines(_Pipe(h) if WINDOWS else socket.socket(fileno=h))
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            try:
                self.lines.send({"op": "connect", "pid": os.getpid()})
                a = self.lines.read()
            except OSError:
                a = None
            if a is None:
                raise ChannelClosed("the agent closed the connector (the attempt is ending)")
            if not a.get("ok"):
                for fd in self.lines.fds:
                    os.close(fd)
                self.lines.fds.clear()
                raise EndpointError(a.get("error") or "refused", a.get("detail") or "")
            conn = _wrap(self.lines.take(a))
            self.lines.send({"op": "received", "conn": a["conn"]})     # the agent may drop its copy now
            return conn


_CONNECTORS: dict[str, _Connector] = {}
_CONNECTORS_LOCK = threading.Lock()


def connect(service: str):
    """A new connection to the service: a connected socket (POSIX) or a socket-like pipe (Windows). Raises EndpointError
    when the agent refuses (`service_unavailable`, `rate_limited`, `ended`, ...)."""
    with _CONNECTORS_LOCK:
        c = _CONNECTORS.get(service) or _CONNECTORS.setdefault(service, _Connector(service))
    return c.connect()


class HTTPConnection(http.client.HTTPConnection):
    """http.client over a service endpoint; keeps the connection for further requests (HTTP/1.1 keep-alive)."""

    def __init__(self, service: str, timeout: float | None = None):
        super().__init__("localhost", timeout=timeout)
        self.service = service

    def connect(self):
        self.sock = connect(self.service)
        if self.timeout is not None:
            self.sock.settimeout(self.timeout)


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""

    def json(self):
        return json.loads(self.body)


def request(service: str, method: str, path: str, body=None, headers: dict | None = None, timeout: float | None = 600) -> Response:
    """One HTTP/1.1 request on a fresh connection. A dict or list body is sent as JSON."""
    h = dict(headers or {})
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        body = body.encode()
    h.setdefault("Connection", "close")
    c = HTTPConnection(service, timeout=timeout)
    try:
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        c.close()


# ---------------------------------------------------------------------------- the service's side

def inherit_channel() -> dict:
    """subprocess.Popen arguments that pass the endpoint channel, and the listening sockets of `listen_fd` listeners
    (OARBANK_LISTEN_FD_*), and nothing else, to the process a `start` leaves running (POSIX `pass_fds`; Windows a handle
    list). Its environment must keep OARBANK_ENDPOINT_CHANNEL (and the OARBANK_LISTEN_FD_* variables)."""
    value = os.environ.get(CHANNEL_ENV)
    if not value:
        raise EndpointError("no_channel", f"no {CHANNEL_ENV}: only an endpoint service's `start` has one")
    h = _handle(value)
    if WINDOWS:
        import subprocess
        si = subprocess.STARTUPINFO()
        si.lpAttributeList = {"handle_list": [h]}
        return {"startupinfo": si, "close_fds": True}
    listen = [_handle(v) for k, v in sorted(os.environ.items()) if k.startswith(LISTEN_FD_PREFIX) and v]
    return {"pass_fds": [h, *listen]}


@dataclass(frozen=True)
class Origin:
    """Where a connection the agent handed over comes from: a job's attempt (`attempt`), or a client on the internet that
    an inbound listener accepted (`listener`, and `peer`, the client's `addr:port`). `proxied` is the (source,
    destination) a PROXY v2 header carried, once `serve_http` has read it."""
    conn: int
    attempt: int | None = None
    listener: str | None = None
    peer: str | None = None
    proxied: tuple | None = None

    @property
    def client_address(self) -> tuple:
        """What an http.server handler gets as `client_address`: the client's (host, port) for a listener connection
        (the PROXY header's source when there is one), ("attempt-<id>", 0) for a job's."""
        if self.proxied and self.proxied[0]:
            return self.proxied[0]
        if self.listener is not None:
            return split_addr(self.peer or "")
        return (f"attempt-{self.attempt}", 0)


def split_addr(addr: str) -> tuple:
    """`198.51.100.7:9000` or `[2001:db8::7]:9000` as (host, port); ("", 0) for what is neither."""
    host, sep, port = addr.rpartition(":")
    if not sep or not port.isdigit():
        return ("", 0)
    return (host[1:-1] if host.startswith("[") and host.endswith("]") else host, int(port))


def _read_listeners_file() -> dict:
    path = os.environ.get(LISTENERS_ENV)
    if not path:
        return {}
    try:
        doc = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}
    return {k: dict(v) for k, v in doc.items() if isinstance(v, dict)} if isinstance(doc, dict) else {}


class Acceptor:
    """The service's end of its endpoint channel: says hello, then yields each connection the agent hands over, as
    (connection, Origin). Listener announcements update `listeners` ({name: {external, external_v6, state, since}},
    seeded from OARBANK_LISTENERS_FILE) and call `on_listener(name, announcement)` on the accepting thread."""

    def __init__(self, max_per_attempt: int = MAX_PER_ATTEMPT, on_listener=None):
        value = os.environ.get(CHANNEL_ENV)
        if not value:
            raise EndpointError("no_channel", f"no {CHANNEL_ENV}: the service is not an endpoint, or this is not the "
                                              "process the agent's `start` left running")
        h = _handle(value)
        os.environ.pop(CHANNEL_ENV, None)            # children the service starts do not see it
        self.lines = _Lines(_Pipe(h) if WINDOWS else socket.socket(fileno=h))
        self.max_per_attempt = max_per_attempt
        self.on_listener = on_listener
        self.listeners: dict[str, dict] = _read_listeners_file()
        self.open: dict[int, OrderedDict] = {}
        self.from_listeners = weakref.WeakSet()      # listener connections still referenced, cut on close
        self.lock = threading.Lock()
        self.lines.send({"op": "hello", "pid": os.getpid()})

    def accept(self):
        """The next connection and its Origin. Raises ChannelClosed when the agent closes the channel."""
        while True:
            msg = self.lines.read()
            if msg is None:
                self.close()
                raise ChannelClosed()
            op = msg.get("op")
            if op == "connection":
                conn, n = _wrap(self.lines.take(msg)), int(msg["conn"])
                if msg.get("listener") is not None:
                    origin = Origin(conn=n, listener=str(msg["listener"]), peer=msg.get("peer"))
                    with self.lock:
                        self.lines.send({"op": "accepted", "conn": n})          # the agent may drop its copy now
                        self.from_listeners.add(conn)
                    return conn, origin
                aid = int(msg["attempt"])
                with self.lock:
                    self.lines.send({"op": "accepted", "conn": n})
                    mine = self.open.setdefault(aid, OrderedDict())
                    mine[n] = conn
                    while len(mine) > self.max_per_attempt:
                        _cut(mine.popitem(last=False)[1])
                return conn, Origin(conn=n, attempt=aid)
            if op == "ended":
                with self.lock:
                    left = self.open.pop(int(msg["attempt"]), {})
                for c in left.values():
                    _cut(c)
            elif op == "listener" and isinstance(msg.get("name"), str):
                ann = {k: msg.get(k) for k in ("external", "external_v6", "state", "since")}
                self.listeners[msg["name"]] = ann
                if self.on_listener is not None:
                    self.on_listener(msg["name"], ann)

    def __iter__(self):
        while True:
            try:
                yield self.accept()
            except ChannelClosed:
                return

    def external(self, listener: str) -> str | None:
        """The listener's latest announced external IPv4 address (`addr:port`), or None."""
        return (self.listeners.get(listener) or {}).get("external")

    def narrow(self, listener: str, cidrs, append: bool = False) -> int:
        """Allow only these client networks on `listener` (`listener_allow`): the agent intersects them with the owner's
        allowlist, so a module only ever narrows. `cidrs` None drops the module's narrowing; `append` adds to it instead
        of replacing it. A long list goes in several messages, each line under 4096 bytes. Returns the messages sent."""
        msgs = listener_allow_messages(listener, cidrs, append)
        with self.lock:
            for m in msgs:
                self.lines.send(m)
        return len(msgs)

    def close(self):
        with self.lock:
            every = [c for m in self.open.values() for c in m.values()] + list(self.from_listeners)
            self.open.clear()
        for c in every:
            _cut(c)
        self.lines.s.close()


def listener_allow_messages(listener: str, cidrs, append: bool = False) -> list[dict]:
    """The `listener_allow` messages that set (or with `append` extend) a listener's narrowing to `cidrs`, each line at
    most MAX_LINE bytes; `cidrs` None drops the narrowing. Each entry must parse as an IP network (an address is a /32 or
    a /128); it is sent normalized."""
    def line(m):
        return len(json.dumps(m, separators=(",", ":")).encode()) + 1
    if cidrs is None:
        return [{"op": "listener_allow", "listener": listener, "cidrs": None, "append": False}]
    if isinstance(cidrs, str):
        raise TypeError("cidrs is a list of networks, not one string")
    nets = []
    for c in cidrs:
        try:
            nets.append(str(ipaddress.ip_network(str(c), strict=False)))
        except ValueError as e:
            raise ValueError(f"listener_allow {c!r}: not an IP network ({e})") from None
    out, cur = [], {"op": "listener_allow", "listener": listener, "cidrs": [], "append": append}
    for n in nets:
        cur["cidrs"].append(n)
        if line(cur) > MAX_LINE:
            cur["cidrs"].pop()
            if not cur["cidrs"]:
                raise ValueError("a listener name this long leaves no room for a network in one line")
            out.append(cur)
            cur = {"op": "listener_allow", "listener": listener, "cidrs": [n], "append": True}
    if cur["cidrs"] or not append:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------- PROXY protocol v2

PROXY_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"


class ProxyHeaderError(EndpointError):
    def __init__(self, detail: str):
        super().__init__("bad_proxy_header", detail)


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProxyHeaderError(f"the connection ended after {len(buf)} of {n} header bytes")
        buf += chunk
    return buf


def read_proxy_v2(sock) -> tuple:
    """Read a PROXY protocol v2 header from the start of `sock`, and nothing after it: (source, destination), each a
    (host, port) tuple, or (None, None) for a LOCAL command or an address family other than TCP over IPv4 or IPv6. TLVs
    are skipped. Raises ProxyHeaderError for anything that is not a v2 header."""
    head = _recv_exact(sock, 16)
    if head[:12] != PROXY_V2_SIGNATURE:
        raise ProxyHeaderError("no PROXY v2 signature")
    ver_cmd, fam, length = head[12], head[13], struct.unpack("!H", head[14:16])[0]
    if ver_cmd >> 4 != 2 or ver_cmd & 0x0F not in (0, 1):
        raise ProxyHeaderError(f"version/command byte {ver_cmd:#04x} is not v2 LOCAL or PROXY")
    body = _recv_exact(sock, length) if length else b""
    if ver_cmd & 0x0F == 0:
        return None, None
    if fam == 0x11 and length >= 12:
        src, dst = socket.inet_ntop(socket.AF_INET, body[0:4]), socket.inet_ntop(socket.AF_INET, body[4:8])
        sp, dp = struct.unpack("!HH", body[8:12])
        return (src, sp), (dst, dp)
    if fam == 0x21 and length >= 36:
        src, dst = socket.inet_ntop(socket.AF_INET6, body[0:16]), socket.inet_ntop(socket.AF_INET6, body[16:32])
        sp, dp = struct.unpack("!HH", body[32:36])
        return (src, sp), (dst, dp)
    if fam in (0x11, 0x21):
        raise ProxyHeaderError(f"family {fam:#04x} with {length} address bytes")
    return None, None


def proxy_v2_header(src: tuple, dst: tuple) -> bytes:
    """The PROXY v2 header the agent writes for a TCP connection from `src` accepted on `dst` ((host, port) each)."""
    v6 = ":" in src[0]
    fam, af = (0x21, socket.AF_INET6) if v6 else (0x11, socket.AF_INET)
    body = socket.inet_pton(af, src[0]) + socket.inet_pton(af, dst[0]) + struct.pack("!HH", src[1], dst[1])
    return PROXY_V2_SIGNATURE + bytes([0x21, fam]) + struct.pack("!H", len(body)) + body


# ---------------------------------------------------------------------------- listen_fd listeners (macOS, Linux)

def _listen_fd(name: str, env=None) -> int:
    var = LISTEN_FD_PREFIX + name.upper()
    value = (os.environ if env is None else env).get(var)
    if WINDOWS:
        raise EndpointError("unsupported", "listen_fd listeners exist on macOS and Linux nodes only")
    if not value:
        raise EndpointError("no_listener", f"no {var}: listener {name!r} is not a listen_fd listener of this service, or "
                                           "this is not the process the agent's `start` left running")
    return _handle(value)


def listen_socket(name: str) -> socket.socket:
    """A `listen_fd` listener's inherited listening socket (OARBANK_LISTEN_FD_<NAME>): accept on it, never listen or bind
    anything yourself. Each accepted connection starts with a PROXY v2 header unless the manifest turned it off."""
    return socket.socket(fileno=_listen_fd(name))


def exec_with_listen_fds(argv: list[str], names: list[str], env: dict | None = None):
    """Execute a server that follows the systemd socket-activation convention on the named `listen_fd` listeners'
    sockets: they become descriptors 3, 4, ... in order, with LISTEN_FDS, LISTEN_FDNAMES (the names joined by `:`) and
    LISTEN_PID (this process, which the server becomes) set. Every other descriptor this process inherited, the endpoint
    channel included, stays as it is. POSIX only; never returns."""
    import fcntl
    env = dict(os.environ if env is None else env)
    src = [_listen_fd(n, env) for n in names]
    high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 3 + len(src)) for fd in src]   # out of the way of 3.. first
    for i, fd in enumerate(high):
        os.dup2(fd, 3 + i, inheritable=True)
        os.close(fd)
    for fd in src:
        if fd >= 3 + len(src):
            os.set_inheritable(fd, False)
    for n in names:
        env.pop(LISTEN_FD_PREFIX + n.upper(), None)
    env.update(LISTEN_FDS=str(len(src)), LISTEN_FDNAMES=":".join(names), LISTEN_PID=str(os.getpid()))
    os.execvpe(argv[0], argv, env)


def _cut(conn):
    """End a connection for both sides at once, whatever is unread (an attempt that ended, one over the cap)."""
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


class Served:
    """What `serve_http` gives a handler as `server`: the acceptor and the connection's Origin (`self.server.origin`)."""

    def __init__(self, acceptor: Acceptor, origin: Origin):
        self.acceptor, self.origin = acceptor, origin


def serve_http(handler, acceptor: Acceptor | None = None, proxy=()) -> None:
    """Run `handler` (an http.server.BaseHTTPRequestHandler class) on every connection, a thread each, until the agent
    closes the channel: jobs' connections and listener connections alike. The handler finds the connection's Origin in
    `self.server.origin` and the client in `client_address`. `proxy`: the listeners whose connections start with a PROXY
    v2 header, which is read first (its source becomes `client_address`)."""
    acc = acceptor or Acceptor()
    proxied = set(proxy)

    def one(conn, origin):
        try:
            if origin.listener in proxied:
                origin = Origin(origin.conn, origin.attempt, origin.listener, origin.peer, read_proxy_v2(conn))
            handler(conn, origin.client_address, Served(acc, origin))
        except (OSError, ValueError, EndpointError):
            pass                                     # the client went away mid-request, or the attempt ended
        finally:
            conn.close()                             # a plain close: the client still reads what was written

    for conn, origin in acc:
        threading.Thread(target=one, args=(conn, origin), daemon=True).start()
