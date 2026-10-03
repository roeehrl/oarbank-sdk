"""Spawn a module and talk to it: for tests, the conformance kit and tools.

The oarbank core has its own supervising module host (restarts, breaker, metrics); this client is the
minimal, unsupervised version with the same wire behaviour.

    with ModuleClient.spawn(["bin/toy-module"], cwd=bundle) as m:
        m.initialize()
        r = m.call("params.check", {"params": {"n": 3}})
"""
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from . import MODULE_PROTOCOL
from . import module_protocol as mp
from .rpc import Peer, Request, RpcError

HostCallback = Callable[[dict], Any]


class ModuleClient:
    def __init__(self, proc: subprocess.Popen, callbacks: dict[str, HostCallback] | None = None,
                 permissions: set[str] | None = None, host_version: str = "0.0.0", max_workers: int = 4,
                 host_platform: str | None = None, host_capabilities: list[str] | None = None):
        self.proc = proc
        self.callbacks = callbacks or {}
        self.permissions = permissions            # None = allow every registered callback
        self.host_version = host_version
        self.host_platform = host_platform        # sent as initialize host.platform when set
        self.host_capabilities = list(host_capabilities or [])
        self.logs: list[dict] = []
        self.stderr: list[str] = []
        self.info: mp.InitializeResult | None = None
        self.peer = Peer(proc.stdout, proc.stdin, self._on_request, self._on_notification,
                         max_workers=max_workers, name=f"client-{proc.pid}").start()
        self._err_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._err_thread.start()

    @classmethod
    def spawn(cls, argv: list[str], cwd: str | Path | None = None, env: dict | None = None, **kw) -> "ModuleClient":
        base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
        proc = subprocess.Popen(argv, cwd=cwd, env={**base, **(env or {})}, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        return cls(proc, **kw)

    # ------------------------------------------------------------------ module -> host
    def _on_request(self, req: Request):
        cb = self.callbacks.get(req.method)
        perm = mp.HOST_CALLBACKS.get(req.method, (None, None, None))[2]
        if cb is None:
            raise RpcError(mp.ERR_METHOD_NOT_FOUND, f"host does not provide {req.method}")
        if self.permissions is not None and perm not in self.permissions:
            raise RpcError(mp.ERR_PERMISSION_DENIED, f"{req.method} needs permission {perm}")
        return cb(req.params)

    def _on_notification(self, method: str, params: dict):
        if method == "log":
            self.logs.append(params)

    def _drain_stderr(self):
        for line in iter(self.proc.stderr.readline, b""):
            self.stderr.append(line.decode(errors="replace").rstrip("\n"))

    # ------------------------------------------------------------------ host -> module
    def initialize(self, settings: dict | None = None, protocol_versions: list[int] | None = None,
                   timeout: float = 10.0) -> mp.InitializeResult:
        host = {"name": "oarbank", "version": self.host_version, "capabilities": self.host_capabilities}
        if self.host_platform:
            host["platform"] = self.host_platform
        r = self.peer.request("initialize", {"protocol_versions": protocol_versions or [MODULE_PROTOCOL],
                                             "host": host, "settings": settings or {}}, timeout=timeout)
        self.info = mp.InitializeResult.model_validate(r)
        self.peer.notify("initialized")
        return self.info

    def call(self, method: str, params: dict, timeout: float = 30.0) -> Any:
        return self.peer.request(method, params, timeout=timeout)

    def close(self, grace: float = 5.0) -> int:
        try:
            if not self.peer.closed:
                self.peer.notify("shutdown")
        except Exception:
            pass
        self.peer.close()
        try:
            return self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
