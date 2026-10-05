# Serve jobs from a warm service

Loading a model can take a minute and many GB. Load it once per node, in a service, and let every job on the node send
it work. [`examples/modelserver`](../examples/modelserver) is the complete reference: a stand-in model server that jobs
share. The contract is [service-protocol.md, "Endpoints"](../spec/service-protocol.md#endpoints).

Nothing listens. The agent hands the service every connection a job opens, so the sandbox rules stay as they are: no
loopback, no listening socket, no socket path or pipe name anyone could find.

## 1. Declare the service and the pool

```toml
[requires]
core = ">=2.5,<3"
service_protocol = [1]

[[services]]
name = "model"
exec = ["python", "-I", "{bundle}/model_service.py"]
lifecycle = "on_demand"          # started when a job needs it, stopped after idle_timeout_s
idle_timeout_s = 600
endpoint = true                  # jobs reach it through the agent
provides = { pools = ["model"] }
reserves_host_memory = true      # fingerprint reserve.mem_gb is charged while it runs
yieldable = true                 # host protection may stop it, releasing the jobs that use it
# gpu = { use = "shared", apis_any = ["metal", "cuda"] }   # with [sandbox].devices.gpu = "compute"

[[stages]]
name = "generate"
requires.pools = { model = 1 }   # each job holds a token and gets OARBANK_SERVICE_MODEL
```

`fingerprint.pools.model` sets how many jobs may use the service at once on a node.

## 2. The service: accept, never listen

`start` gets `OARBANK_ENDPOINT_CHANNEL`. Leave a process running that inherits it, says hello, loads the model and serves
HTTP on every connection it is handed:

```python
from http.server import BaseHTTPRequestHandler
from oarbank_sdk import service_endpoint as ep

def serve():
    acc = ep.Acceptor()                  # says hello: the agent may hand connections from now on
    load_the_model()
    READY.write_text("1")                # `ready` answers true from now on
    ep.serve_http(Handler, acc)          # a thread per connection; returns when the agent closes the channel

# in `start`:
subprocess.Popen([sys.executable, "-I", __file__, "serve"], stdout=log, stderr=log, **ep.inherit_channel())
```

`ready` must answer true only once the model is loaded; the agent also waits for the hello before any job's runner
starts. When the agent stops the service it closes the channel: `serve_http` returns and the process exits. A service
the agent finds running after its own restart has no channel, so the agent stops it and starts it again.

## 3. The job: one call

```python
from oarbank_sdk import service_endpoint as ep

r = ep.request("model", "POST", "/v1/generate", {"prompt": "..."})
text = r.json()["text"]
```

Each `request` opens its own connection, so threads can send in parallel; `ep.HTTPConnection("model")` keeps one
connection for several requests. A runner's child process reaches the service only if the runner passes the connector on
(`pass_fds`, or `handle_list` on Windows).

## 4. Test it

`oarbank-sdk conform` starts the service as an agent does, runs your goldens through it, sends your `service_specs`
(in `conformance.json`) through connections it hands the service, and fails a service that listens itself:

```json
{"service_specs": [{"name": "generate", "service": "model", "path": "/v1/generate", "body": {"prompt": "hello"}}]}
```

## Show it on your module's pages

Every agent reports its services in each heartbeat; the `services` host query (UI contract 1.2) gives your page one row
per node and service, with its `state` (`ready`, `starting`, `stopped`), `health`, `stopped_reason` (held by host
protection, disabled, withdrawn after failures, a GPU API missing, or idle) and the jobs using it. modelserver's
overview ([`ui/pages/overview.json`](../examples/modelserver/ui/pages/overview.json)) is a table of them, and its node
panel shows the one service on that node (`"params": {"node_id": "$node", "service": "model"}`):

```json
{"type": "table", "requires": "1.2", "fallback": "placeholder", "source": {"query": "services"},
 "columns": [{"key": "hostname", "label": "node"}, {"key": "service"}, {"key": "state", "type": "status"},
             {"key": "health", "type": "status"}, {"key": "stopped_reason", "label": "why stopped"}]}
```

## On each operating system

The same code runs everywhere. On macOS and Linux the handles are file descriptors passed over a socket; on Windows the
agent duplicates pipe handles straight into your processes. On Windows a connection is a synchronous pipe: one thread at a
time reads or writes it, which suits HTTP's request then response.
