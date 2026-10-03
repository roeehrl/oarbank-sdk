"""A bidirectional, newline-delimited JSON-RPC 2.0 peer over a pair of byte streams.

Both sides of module protocol 1 use it: the module (`oarbank_sdk.server`) and the host (the core's
module host, or `oarbank_sdk.client` for tests and tools). Either side may send requests while it is
handling one, which is how host callbacks work: a module handling `job.plan` can call
`host.datasets.query` and wait for the answer.

One reader thread parses lines. Responses resolve the waiting caller, notifications run inline on the
reader thread (they must be quick), and requests run on a small worker pool, `max_workers` wide.
"""
import itertools
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, BinaryIO, Callable

from . import module_protocol as mp

MAX_LINE = 16 * 1024 * 1024          # a single message may not exceed 16 MiB


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.data = code, message, data

    def to_json(self) -> dict:
        e = {"code": self.code, "message": self.message}
        if self.data is not None:
            e["data"] = self.data
        return e


class ConnectionClosed(Exception):
    pass


class Request:
    """Context for one incoming request."""

    def __init__(self, peer: "Peer", id_, method: str, params: dict):
        self.peer, self.id, self.method, self.params = peer, id_, method, params
        self.cancelled = threading.Event()


RequestHandler = Callable[[Request], Any]          # returns the result, or raises RpcError
NotificationHandler = Callable[[str, dict], None]


class Peer:
    def __init__(self, rfile: BinaryIO, wfile: BinaryIO, on_request: RequestHandler,
                 on_notification: NotificationHandler | None = None, max_workers: int = 1, name: str = "peer",
                 on_close: Callable[[], None] | None = None):
        self._r, self._w = rfile, wfile
        self._on_request, self._on_notification, self._on_close = on_request, on_notification, on_close
        self._wlock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[Any, Future] = {}
        self._incoming: dict[Any, Request] = {}
        self._plock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"{name}-rpc")
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name=f"{name}-reader", daemon=True)
        self.name = name

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "Peer":
        self._reader.start()
        return self

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        with self._plock:
            pending, self._pending = self._pending, {}
        for f in pending.values():
            if not f.done():
                f.set_exception(ConnectionClosed(f"{self.name}: connection closed"))
        try:
            self._w.close()
        except Exception:
            pass
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._on_close:
            self._on_close()

    # ------------------------------------------------------------------ outgoing
    def _send(self, msg: dict):
        data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"
        if len(data) > MAX_LINE:
            raise RpcError(mp.ERR_INTERNAL, f"message of {len(data)} bytes exceeds the {MAX_LINE}-byte limit")
        with self._wlock:
            if self._closed.is_set():
                raise ConnectionClosed(f"{self.name}: connection closed")
            try:
                self._w.write(data)
                self._w.flush()
            except (BrokenPipeError, ValueError, OSError) as e:
                self.close()
                raise ConnectionClosed(f"{self.name}: {e}") from e

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        f = self.request_async(method, params)
        try:
            return f.result(timeout)
        except TimeoutError:
            self.cancel(f.rpc_id)
            raise

    def request_async(self, method: str, params: dict | None = None) -> Future:
        id_ = next(self._ids)
        f: Future = Future()
        f.rpc_id = id_
        with self._plock:
            self._pending[id_] = f
        try:
            self._send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
        except Exception:
            with self._plock:
                self._pending.pop(id_, None)
            raise
        return f

    def cancel(self, id_):
        with self._plock:
            f = self._pending.pop(id_, None)
        if f and not f.done():
            f.set_exception(TimeoutError(f"{self.name}: request {id_} timed out"))
        try:
            self.notify("$/cancel", {"id": id_})
        except ConnectionClosed:
            pass

    def notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ------------------------------------------------------------------ incoming
    def _read_loop(self):
        try:
            while not self._closed.is_set():
                line = self._r.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    self._send_error(None, mp.ERR_INVALID_REQUEST, "message too large")
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    self._send_error(None, mp.ERR_PARSE, f"parse error: {e}")
                    continue
                self._dispatch(msg)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def _dispatch(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._send_error(msg.get("id") if isinstance(msg, dict) else None, mp.ERR_INVALID_REQUEST, "not a JSON-RPC 2.0 message")
            return
        if "method" in msg:
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                if "id" in msg:
                    self._send_error(msg["id"], mp.ERR_INVALID_PARAMS, "params must be an object")
                return
            if "id" not in msg:
                if msg["method"] == "$/cancel":
                    with self._plock:
                        req = self._incoming.get(params.get("id"))
                    if req:
                        req.cancelled.set()
                if self._on_notification:
                    try:
                        self._on_notification(msg["method"], params)
                    except Exception:
                        pass
                return
            req = Request(self, msg["id"], msg["method"], params)
            with self._plock:
                self._incoming[req.id] = req
            try:
                self._pool.submit(self._run_request, req)
            except RuntimeError:          # pool shut down
                pass
            return
        id_ = msg.get("id")
        with self._plock:
            f = self._pending.pop(id_, None)
        if f is None or f.done():
            return                        # late answer to a cancelled or timed-out request
        if msg.get("error") is not None:
            e = msg["error"] or {}
            f.set_exception(RpcError(int(e.get("code", mp.ERR_INTERNAL)), str(e.get("message", "")), e.get("data")))
        else:
            f.set_result(msg.get("result"))

    def _run_request(self, req: Request):
        try:
            result = self._on_request(req)
            if req.cancelled.is_set():
                raise RpcError(mp.ERR_CANCELLED, "cancelled")
            out = {"jsonrpc": "2.0", "id": req.id, "result": result}
        except RpcError as e:
            out = {"jsonrpc": "2.0", "id": req.id, "error": e.to_json()}
        except Exception as e:          # a handler bug is an internal error, never a crash of the peer
            out = {"jsonrpc": "2.0", "id": req.id, "error": {"code": mp.ERR_INTERNAL, "message": f"{type(e).__name__}: {e}"}}
        finally:
            with self._plock:
                self._incoming.pop(req.id, None)
        try:
            self._send(out)
        except (ConnectionClosed, RpcError):
            pass

    def _send_error(self, id_, code, message):
        try:
            self._send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})
        except ConnectionClosed:
            pass
