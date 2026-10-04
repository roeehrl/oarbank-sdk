"""Write the coordinator side of a module in a few lines.

    from oarbank_sdk.server import Module
    from oarbank_sdk import module_protocol as mp

    module = Module("dev.example.toy", "0.1.0")

    @module.verb("params.check")
    def check(p: mp.ParamsCheckParams, ctx) -> mp.ParamsCheckResult:
        ...

    if __name__ == "__main__":
        module.run()

Handlers receive the verb's typed params and a `Context`, and return the typed result (or a plain dict).
Optional verbs are advertised automatically from what you register. While `run()` is active,
`sys.stdout` is redirected to stderr, so a stray `print` can never corrupt the protocol stream; stderr
goes to the module's log on the host.
"""
import os
import sys
import threading
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from . import MODULE_PROTOCOL
from . import module_protocol as mp
from . import platform as pf
from .rpc import ConnectionClosed, Peer, Request, RpcError

REQUIRED = [m for m, (_, _, req, _) in mp.VERBS.items() if req]


class Host:
    """Typed host callbacks. Each needs the matching permission in the manifest."""

    def __init__(self, peer: Peer, timeout: float = 30.0):
        self._peer, self._timeout = peer, timeout

    def _call(self, method: str, params: BaseModel, result_model):
        return result_model.model_validate(
            self._peer.request(method, params.model_dump(mode="json", by_alias=True), timeout=self._timeout))

    def datasets_query(self, kind: str | None = None, attrs: dict | None = None, limit: int = 100,
                       ids: list[str] | None = None, with_files: bool = False) -> mp.DatasetsQueryResult:
        return self._call("host.datasets.query", mp.DatasetsQueryParams(kind=kind, attrs=attrs or {}, limit=limit, ids=ids or [],
                                                                        with_files=with_files), mp.DatasetsQueryResult)

    def blobs_stat(self, digest: str) -> mp.BlobStatResult:
        return self._call("host.blobs.stat", mp.BlobStatParams(digest=digest), mp.BlobStatResult)

    def settings_get(self, key: str) -> Any:
        return self._call("host.settings.get", mp.SettingsGetParams(key=key), mp.SettingsGetResult).value

    def secret(self, name: str) -> str | None:
        """The module's value of a declared secret (None: not set). Needs `secrets:read:self`; never log or return it."""
        r = self._call("host.secrets.get", mp.SecretsGetParams(name=name), mp.SecretsGetResult)
        return r.value if r.set else None

    def store_get(self, collection: str, key: str) -> dict | None:
        return self._call("host.store.get", mp.StoreGetParams(collection=collection, key=key), mp.StoreGetResult).doc

    def store_query(self, collection: str, where: dict | None = None, limit: int = 500) -> list[dict]:
        return self._call("host.store.query", mp.StoreQueryParams(collection=collection, where=where or {}, limit=limit),
                          mp.StoreQueryResult).docs

    def jobs_query(self, campaign_id: str, states: list[str] | None = None, limit: int = 5000) -> list[dict]:
        return self._call("host.jobs.query", mp.JobsQueryParams(campaign_id=campaign_id, states=states or [], limit=limit),
                          mp.JobsQueryResult).jobs

    def files_list(self, prefix: str = "", limit: int = 1000) -> list[mp.FileEntry]:
        return self._call("host.files.list", mp.FilesListParams(prefix=prefix, limit=limit), mp.FilesListResult).files

    def files_stat(self, path: str) -> mp.FileEntry | None:
        return self._call("host.files.stat", mp.FilesStatParams(path=path), mp.FilesStatResult).file

    def files_read(self, path: str) -> bytes:
        """A whole module file (read in chunks of at most 1 MiB)."""
        import base64
        out, off = bytearray(), 0
        while True:
            r = self._call("host.files.read", mp.FilesReadParams(path=path, offset=off), mp.FilesReadResult)
            chunk = base64.b64decode(r.content_b64)
            out += chunk
            off += len(chunk)
            if r.eof or not chunk:
                return bytes(out)

    def nodes_query(self, certified_for_self: bool = True, platforms: list[str] | None = None) -> list[dict]:
        """Ready nodes; `platforms` (tokens or OSes) keeps only those platforms (host capability nodes.platform)."""
        return self._call("host.nodes.query", mp.NodesQueryParams(certified_for_self=certified_for_self, platforms=platforms or []),
                          mp.NodesQueryResult).nodes

    def fleet_platforms(self, certified: bool = True) -> dict[str, int]:
        """{platform token: number of ready nodes}, by default only nodes certified for this module. Nodes whose platform
        the host does not report (no nodes.platform capability) are left out."""
        out: dict[str, int] = {}
        for n in self.nodes_query(certified_for_self=certified):
            if n.get("platform"):
                out[n["platform"]] = out.get(n["platform"], 0) + 1
        return dict(sorted(out.items()))


class Context:
    def __init__(self, module: "Module", req: Request):
        self._module, self._req = module, req
        self.host = module.host
        self.settings = module.settings

    def host_has(self, capability: str) -> bool:
        """Whether the host advertised `capability` at initialize (e.g. mp.HOST_PLACEMENT before passing placement,
        group or platforms in effects from a module that also runs on older hosts)."""
        return capability in self._module.host_capabilities

    @property
    def host_platform(self) -> str:
        """The coordinator host's platform token: initialize's host.platform, else OARBANK_PLATFORM, else this machine."""
        info = self._module.host_info
        return (info.platform if info and info.platform else None) or pf.current()

    @property
    def cancelled(self) -> bool:
        """True once the host cancelled this request; long handlers should check it and stop."""
        return self._req.cancelled.is_set()

    def log(self, msg: str, level: str = "info", **data):
        self._module.log(msg, level, **data)


class Module:
    def __init__(self, id: str, version: str, protocol_versions: tuple[int, ...] = (MODULE_PROTOCOL,), concurrency: int = 1,
                 features: tuple[str, ...] = ()):
        """`features`: capabilities that are not verbs, advertised with the verbs (mp.FEATURES, e.g. campaign.tick.results)."""
        unknown = sorted(set(features) - set(mp.FEATURES))
        if unknown:
            raise ValueError(f"features {unknown}: not module protocol features; known: {list(mp.FEATURES)}")
        self.id, self.version = id, version
        self.features = tuple(features)
        self.protocol_versions = protocol_versions
        self.concurrency = concurrency
        self._verbs: dict[str, Callable] = {}
        self.settings: dict = {}
        self.host_info: mp.HostInfo | None = None
        self.host: Host | None = None
        self.peer: Peer | None = None
        self.initialized = threading.Event()
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------ registration
    def verb(self, method: str):
        if method not in mp.VERBS:
            raise ValueError(f"{method!r} is not a module protocol verb; known: {sorted(mp.VERBS)}")

        def deco(fn):
            self._verbs[method] = fn
            return fn
        return deco

    @property
    def host_capabilities(self) -> set[str]:
        return set(self.host_info.capabilities) if self.host_info else set()

    @property
    def capabilities(self) -> list[str]:
        return sorted({cap for m, (_, _, req, cap) in mp.VERBS.items() if not req and m in self._verbs} | set(self.features))

    def missing_required(self) -> list[str]:
        return [m for m in REQUIRED if m not in self._verbs]

    # ------------------------------------------------------------------ protocol
    def log(self, msg: str, level: str = "info", **data):
        if self.peer and not self.peer.closed:
            try:
                self.peer.notify("log", {"level": level, "msg": msg, "data": data})
            except ConnectionClosed:
                pass

    def _on_request(self, req: Request):
        if req.method == "initialize":
            return self._initialize(req.params)
        if not self.initialized.is_set():
            raise RpcError(mp.ERR_INVALID_REQUEST, "initialize first")
        spec = mp.VERBS.get(req.method)
        if spec is None:
            raise RpcError(mp.ERR_METHOD_NOT_FOUND, f"unknown method {req.method}")
        params_model, result_model, required, cap = spec
        fn = self._verbs.get(req.method)
        if fn is None:
            raise RpcError(mp.ERR_CAPABILITY_MISSING if cap else mp.ERR_METHOD_NOT_FOUND, f"{req.method} not implemented")
        try:
            params = params_model.model_validate(req.params)
        except ValidationError as e:
            raise RpcError(mp.ERR_INVALID_PARAMS, "invalid params", {"errors": e.errors(include_url=False)})
        out = fn(params, Context(self, req))
        try:
            out = result_model.model_validate(out.model_dump() if isinstance(out, BaseModel) else out)
        except ValidationError as e:
            raise RpcError(mp.ERR_INTERNAL, f"{req.method} returned an invalid result", {"errors": e.errors(include_url=False)})
        return out.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _initialize(self, params: dict):
        try:
            p = mp.InitializeParams.model_validate(params)
        except ValidationError as e:
            raise RpcError(mp.ERR_INVALID_PARAMS, "invalid initialize params", {"errors": e.errors(include_url=False)})
        shared = [v for v in p.protocol_versions if v in self.protocol_versions]
        if not shared:
            raise RpcError(mp.ERR_UNSUPPORTED_PROTOCOL, "no shared module protocol version",
                           {"supported": list(self.protocol_versions), "requested": p.protocol_versions})
        self.settings = dict(p.settings)
        self.host_info = p.host
        return mp.InitializeResult(protocol_version=max(shared), module=mp.ModuleIdentity(id=self.id, version=self.version),
                                   capabilities=self.capabilities).model_dump(mode="json")

    def _on_notification(self, method: str, params: dict):
        if method == "initialized":
            self.initialized.set()
        elif method == "shutdown":
            self._shutdown.set()

    # ------------------------------------------------------------------ run
    def serve(self, rfile, wfile) -> Peer:
        """Serve on the given streams (tests use pipes); returns the running peer."""
        missing = self.missing_required()
        if missing:
            raise RuntimeError(f"module {self.id} does not implement required verbs: {missing}")
        self.peer = Peer(rfile, wfile, self._on_request, self._on_notification,
                         max_workers=self.concurrency, name=self.id, on_close=self._shutdown.set)
        self.host = Host(self.peer)
        return self.peer.start()

    def run(self):
        """Serve module protocol on stdin/stdout until the host shuts us down or closes stdin."""
        proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
        sys.stdout = sys.stderr                        # prints go to the log, never into the protocol
        self.serve(sys.stdin.buffer, proto_out)
        self._shutdown.wait()
        self.peer.close()
