"""The agent's side of the endpoint protocol (spec/service-protocol.md, "Endpoints"), for the conformance kit and tests:
an endpoint channel for one service run, connectors for jobs, and connections handed to both. The agent's own
implementation is in Rust; this one follows the same wire rules with the standard library."""
import json
import os
import secrets
import socket
import subprocess
import threading

from .service_endpoint import CHANNEL_ENV, WINDOWS, ChannelClosed, EndpointError, _Lines, _Pipe, _wrap, env_name


class _OvPipe:
    """Our own end of a Windows pipe pair, overlapped, so one thread reads it while others write (as the agent's are)."""

    def __init__(self, handle: int):
        self._h = handle

    def fileno(self):
        return self._h

    def recv(self, n: int) -> bytes:
        import _winapi
        try:
            ov, _ = _winapi.ReadFile(self._h, n, overlapped=True)
            ov.GetOverlappedResult(True)
            return ov.getbuffer()
        except OSError as e:
            if getattr(e, "winerror", None) in (109, 232, 233, 995):
                return b""
            raise

    def sendall(self, data) -> None:
        import _winapi
        mv = memoryview(bytes(data))
        while mv:
            ov, _ = _winapi.WriteFile(self._h, bytes(mv), overlapped=True)
            n, _ = ov.GetOverlappedResult(True)
            mv = mv[n:]

    def shutdown(self, how=None):
        import ctypes
        k = ctypes.WinDLL("kernel32")
        k.CancelIoEx(ctypes.c_void_p(self._h), None)
        k.DisconnectNamedPipe(ctypes.c_void_p(self._h))

    def close(self):
        import _winapi
        h, self._h = self._h, None
        if h is not None:
            _winapi.CloseHandle(h)


def _pair(ours_overlapped: bool = True):
    """(our end, the other end): a socketpair on POSIX; on Windows a named-pipe pair whose other end is synchronous and
    ours overlapped (or synchronous too, for a connection whose both ends go to others)."""
    if not WINDOWS:
        return socket.socketpair()
    import _winapi
    name = r"\\.\pipe\oarbank-kit-" + secrets.token_hex(16)
    srv = _winapi.CreateNamedPipe(name, _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
                                  | (_winapi.FILE_FLAG_OVERLAPPED if ours_overlapped else 0),
                                  0x8,                       # byte type, byte reads, blocking, remote clients refused
                                  1, 65536, 65536, 0, _winapi.NULL)
    cli = _winapi.CreateFile(name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0,
                             _winapi.NULL)
    try:
        if ours_overlapped:
            _winapi.ConnectNamedPipe(srv, True).GetOverlappedResult(True)
        else:
            _winapi.ConnectNamedPipe(srv, False)
    except OSError as e:
        if e.winerror != 535:                                    # ERROR_PIPE_CONNECTED: the client is already there
            raise
    return (_OvPipe(srv) if ours_overlapped else _Pipe(srv)), _Pipe(cli)


def _value(end) -> str:
    return f"handle:{end.fileno()}" if WINDOWS else f"fd:{end.fileno()}"


def here(env: dict) -> dict:
    """`env` for a client or service in this process instead of a child: each handle duplicated, so that ours and theirs
    close independently (tests)."""
    out = {}
    for k, v in env.items():
        kind, _, n = v.partition(":")
        if WINDOWS:
            import _winapi
            me = _winapi.GetCurrentProcess()
            out[k] = f"handle:{_winapi.DuplicateHandle(me, int(n), me, 0, False, _winapi.DUPLICATE_SAME_ACCESS)}"
        else:
            out[k] = f"fd:{os.dup(int(n))}"
    return out


def _inherit(end) -> dict:
    """Popen arguments that pass exactly this end to the child."""
    if WINDOWS:
        os.set_handle_inheritable(end.fileno(), True)
        si = subprocess.STARTUPINFO()
        si.lpAttributeList = {"handle_list": [end.fileno()]}
        return {"startupinfo": si, "close_fds": True}
    return {"pass_fds": [end.fileno()]}


def _place(end, pid: int) -> int:
    """Windows: a duplicate of `end` in process `pid` (its handle value there); our copy is closed."""
    import _winapi
    proc = _winapi.OpenProcess(0x0040, False, pid)               # PROCESS_DUP_HANDLE
    try:
        return _winapi.DuplicateHandle(_winapi.GetCurrentProcess(), end.fileno(), proc, 0, False,
                                       _winapi.DUPLICATE_SAME_ACCESS)
    finally:
        _winapi.CloseHandle(proc)
        end.close()


class _Held:
    """Our copies of connection ends sent over a socket, each kept until the receiver says it has it: macOS disposes of a
    socket in flight whose last outside reference closes while the receiver is still installing it."""

    def __init__(self):
        self.ends, self.lock = {}, threading.Lock()

    def keep(self, key, end):
        with self.lock:
            self.ends[key] = end

    def drop(self, key):
        with self.lock:
            end = self.ends.pop(key, None)
        if end is not None:
            end.close()

    def close(self):
        with self.lock:
            ends, self.ends = list(self.ends.values()), {}
        for e in ends:
            e.close()


class ServiceHost:
    """One run of an endpoint service: give `env` and `popen_kwargs` to its `start`, call `spawned`, then `wait_hello`."""

    def __init__(self):
        self.mine, self.theirs = _pair()
        self.env = {CHANNEL_ENV: _value(self.theirs)}
        self.popen_kwargs = _inherit(self.theirs)
        self.lines = _Lines(self.mine)
        self.pid, self.conn, self.lock = None, 0, threading.Lock()
        self.held = _Held()

    def spawned(self):
        self.theirs.close()

    def wait_hello(self) -> int:
        msg = self.lines.read()
        if msg is None:
            raise ChannelClosed("the service closed its endpoint channel before saying hello")
        if msg.get("op") != "hello" or not isinstance(msg.get("pid"), int):
            raise EndpointError("bad_message", f"expected hello, got {msg!r}")
        self.pid = msg["pid"]
        threading.Thread(target=self._acks, daemon=True).start()
        return self.pid

    def _acks(self):
        while True:
            try:
                msg = self.lines.read()
            except (OSError, EndpointError, ValueError):
                msg = None
            if msg is None:
                self.held.close()
                return
            if msg.get("op") == "accepted":
                self.held.drop(msg.get("conn"))

    def connection(self, attempt: int, job_pid: int | None = None):
        """A new connection for `attempt`: the service end goes to the service; returns (its number, the job end: a
        stream in this process, or on Windows with `job_pid` its handle value in that process)."""
        ours, svc = _pair(ours_overlapped=False)              # both ends go to others (or to a client here)
        with self.lock:
            self.conn += 1
            n = self.conn
            msg = {"op": "connection", "conn": n, "attempt": attempt}
            if WINDOWS:
                msg["handle"] = _place(svc, self.pid)
                self.lines.send(msg)
            else:
                self.held.keep(n, svc)
                socket.send_fds(self.mine, [(json.dumps(msg) + "\n").encode()], [svc.fileno()])
        if job_pid is not None and WINDOWS:
            return n, _place(ours, job_pid)
        return n, ours

    def ended(self, attempt: int):
        with self.lock:
            self.lines.send({"op": "ended", "attempt": attempt})

    def close(self):
        try:
            self.mine.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.mine.close()
        self.held.close()
        try:
            self.theirs.close()
        except OSError:
            pass


class ConnectorHost:
    """One attempt's connector to a ServiceHost: give `env` and `popen_kwargs` to the job, call `spawned`, and it answers
    connects on a thread of its own until `close`."""

    def __init__(self, service: str, host: ServiceHost, attempt: int):
        self.host, self.attempt = host, attempt
        self.mine, self.theirs = _pair()
        self.env = {env_name(service): _value(self.theirs)}
        self.popen_kwargs = _inherit(self.theirs)
        self.served = 0
        self.held = _Held()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def spawned(self):
        self.theirs.close()

    def _serve(self):
        lines = _Lines(self.mine)
        while True:
            try:
                req = lines.read()
            except (OSError, EndpointError, ValueError):
                return
            if req is None:
                self.held.close()
                return
            if req.get("op") == "received":
                self.held.drop(req.get("conn"))
                continue
            if req.get("op") != "connect":
                lines.send({"ok": False, "error": "bad_request", "detail": "unknown op"})
                continue
            try:
                n, end = self.host.connection(self.attempt, req.get("pid") if WINDOWS else None)
            except (OSError, EndpointError) as e:
                lines.send({"ok": False, "error": "service_unavailable", "detail": str(e)})
                continue
            self.served += 1
            if WINDOWS:
                lines.send({"ok": True, "conn": n, "handle": end})
            else:
                self.held.keep(n, end)
                socket.send_fds(self.mine, [(json.dumps({"ok": True, "conn": n}) + "\n").encode()], [end.fileno()])

    def close(self):
        try:
            self.mine.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.mine.close()
        try:
            self.theirs.close()
        except OSError:
            pass
        self.thread.join(5)
        self.host.ended(self.attempt)


def connect_here(host: ServiceHost, attempt: int):
    """A connection for a client in this process (the kit's probe): the job end as a socket or socket-like pipe."""
    return host.connection(attempt)[1]

