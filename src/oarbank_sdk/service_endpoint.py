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

On macOS and Linux handles travel as file descriptors (`fd:<n>`, SCM_RIGHTS); on Windows as handle values the agent has
already placed in the receiving process (`handle:<n>`). Children of a runner see the connector only if it is passed on
(subprocess `pass_fds`, or `handle_list` on Windows).
"""
import http.client
import io
import json
import os
import socket
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

CHANNEL_ENV = "OARBANK_ENDPOINT_CHANNEL"      # the service's endpoint channel
PREFIX = "OARBANK_SERVICE_"                   # + the service name, upper-cased: a job's connector
MAX_LINE = 4096                               # every message is one JSON line of at most this many bytes
MAX_PER_ATTEMPT = 64                          # open connections the acceptor keeps per attempt
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
    """subprocess.Popen arguments that pass the endpoint channel, and nothing else, to the process a `start` leaves
    running (POSIX `pass_fds`; Windows a handle list). Its environment must keep OARBANK_ENDPOINT_CHANNEL."""
    value = os.environ.get(CHANNEL_ENV)
    if not value:
        raise EndpointError("no_channel", f"no {CHANNEL_ENV}: only an endpoint service's `start` has one")
    h = _handle(value)
    if WINDOWS:
        import subprocess
        si = subprocess.STARTUPINFO()
        si.lpAttributeList = {"handle_list": [h]}
        return {"startupinfo": si, "close_fds": True}
    return {"pass_fds": [h]}


class Acceptor:
    """The service's end of its endpoint channel: says hello, then yields each connection the agent hands over."""

    def __init__(self, max_per_attempt: int = MAX_PER_ATTEMPT):
        value = os.environ.get(CHANNEL_ENV)
        if not value:
            raise EndpointError("no_channel", f"no {CHANNEL_ENV}: the service is not an endpoint, or this is not the "
                                              "process the agent's `start` left running")
        h = _handle(value)
        os.environ.pop(CHANNEL_ENV, None)            # children the service starts do not see it
        self.lines = _Lines(_Pipe(h) if WINDOWS else socket.socket(fileno=h))
        self.max_per_attempt = max_per_attempt
        self.open: dict[int, OrderedDict] = {}
        self.lock = threading.Lock()
        self.lines.send({"op": "hello", "pid": os.getpid()})

    def accept(self):
        """The next connection and the attempt it belongs to. Raises ChannelClosed when the agent closes the channel."""
        while True:
            msg = self.lines.read()
            if msg is None:
                self.close()
                raise ChannelClosed()
            op = msg.get("op")
            if op == "connection":
                conn, aid = _wrap(self.lines.take(msg)), int(msg["attempt"])
                with self.lock:
                    self.lines.send({"op": "accepted", "conn": msg["conn"]})   # the agent may drop its copy now
                    mine = self.open.setdefault(aid, OrderedDict())
                    mine[int(msg["conn"])] = conn
                    while len(mine) > self.max_per_attempt:
                        _cut(mine.popitem(last=False)[1])
                return conn, aid
            if op == "ended":
                with self.lock:
                    left = self.open.pop(int(msg["attempt"]), {})
                for c in left.values():
                    _cut(c)

    def __iter__(self):
        while True:
            try:
                yield self.accept()
            except ChannelClosed:
                return

    def close(self):
        with self.lock:
            every = [c for m in self.open.values() for c in m.values()]
            self.open.clear()
        for c in every:
            _cut(c)
        self.lines.s.close()


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


def serve_http(handler, acceptor: Acceptor | None = None) -> None:
    """Run `handler` (an http.server.BaseHTTPRequestHandler class) on every connection, a thread each, until the agent
    closes the channel."""
    acc = acceptor or Acceptor()

    def one(conn, aid):
        try:
            handler(conn, (f"attempt-{aid}", 0), acc)
        except (OSError, ValueError):
            pass                                     # the job went away mid-request, or the attempt ended
        finally:
            conn.close()                             # a plain close: the job still reads what was written

    for conn, aid in acc:
        threading.Thread(target=one, args=(conn, aid), daemon=True).start()
