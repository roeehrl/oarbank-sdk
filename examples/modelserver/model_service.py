"""modelserver's `model` service (service protocol 1, an endpoint service). Its state is files in the module's data
directory: model.up (running), model.ready (the model is loaded and the service accepts), model.loads (one line per
load), model.log (the daemon's output).

`start` leaves a daemon running (`serve`) that inherits the endpoint channel, says hello, loads the model once and then
answers every connection the agent hands it: POST /v1/generate {"prompt"} -> {"text", "loads", "pid"}. It never
listens. When the agent closes the channel (it stops the service) the daemon exits. Stdlib and the SDK only."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from oarbank_sdk import service_endpoint as ep

DATA = Path(os.environ.get("OARBANK_MODULE_DATA", "."))
UP, READY, LOADS, LOG = (DATA / n for n in ("model.up", "model.ready", "model.loads", "model.log"))


def settings() -> dict:
    try:
        return json.loads(Path(os.environ["OARBANK_SETTINGS_FILE"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError):
        return {}


def complete(prompt: str) -> str:
    """The stand-in model: a deterministic completion."""
    return f"{prompt} -> {hashlib.sha256(prompt.encode()).hexdigest()[:16]}"


class Model:
    loads = 0
    served = 0
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path != "/v1/generate" or not isinstance(body.get("prompt"), str):
            self.send_error(404 if self.path != "/v1/generate" else 400)
            return
        delay = float(settings().get("generate_s", 0))
        if delay:
            time.sleep(delay)
        with Model.lock:
            Model.served += 1
        out = json.dumps({"text": complete(body["prompt"]), "loads": Model.loads, "pid": os.getpid(),
                          "served": Model.served}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def serve() -> int:
    acc = ep.Acceptor()                                 # hello: the agent may hand connections from now on
    time.sleep(float(settings().get("load_s", 0.5)))   # "load the model"
    Model.loads += 1
    with LOADS.open("a", encoding="utf-8") as f:
        f.write(f"{os.getpid()}\n")
    READY.write_text(str(os.getpid()), encoding="utf-8")
    try:
        ep.serve_http(Handler, acc)                     # returns when the agent closes the channel
    finally:
        for p in (READY, UP):
            p.unlink(missing_ok=True)
    return 0


def emit(doc: dict) -> int:
    print(json.dumps(doc))
    return 0


def main(op: str) -> int:
    if op == "serve":
        return serve()
    if op == "fingerprint":
        return emit({"service_protocol": {"supported": [1]}, "health": "healthy",
                     "pools": {"model": int(settings().get("slots", 4))},
                     "reserve": {"mem_gb": float(settings().get("model_gb", 0.5))}, "running": UP.exists()})
    if op == "start":
        if not UP.exists():
            UP.write_text("1", encoding="utf-8")
            READY.unlink(missing_ok=True)
            with LOG.open("ab") as log:
                subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "serve"], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, **ep.inherit_channel())
        return emit({"ok": True})
    if op == "stop":
        for p in (UP, READY):
            p.unlink(missing_ok=True)
        return emit({"ok": True})                      # the agent then closes the channel and ends the daemon's group
    if op == "status":
        return emit({"running": UP.exists(), "ready": READY.exists()})
    if op == "ready":
        return emit({"running": UP.exists(), "ready": UP.exists() and READY.exists()})
    if op == "list_owned":
        return emit({"objects": []})
    if op == "destroy":
        return emit({"ok": True})
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
