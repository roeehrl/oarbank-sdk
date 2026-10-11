# Accept connections from the internet

Some workloads must be reachable: a peer of a network that publishes its address, a public endpoint, a relay. A module
still never listens. It declares an **inbound listener**; the agent listens on the node, maps the port on the router
when it can, checks from outside that it is reachable, and hands every connection it accepts to your service.
[`examples/publicecho`](../examples/publicecho) is the complete reference: a line echo server reachable from the
internet. The contracts are [sandbox.md, "Inbound listeners"](../spec/sandbox.md#inbound-listeners) and
[service-protocol.md, "Listeners"](../spec/service-protocol.md#listeners).

## 1. Declare the listener and the service that serves it

```toml
[requires]
core = ">=2.10,<3"
service_protocol = [1]

[[sandbox.net.inbound]]
name = "peer"
port_hint = 9000                 # the external port you would like; the owner decides per node
port_policy = "stable"           # "flexible" when nothing publishes the port
max_conns = 256                  # the limits are ceilings: the owner may lower them per node
max_conns_per_ip = 16
new_conns_per_ip_per_s = 4
idle_timeout_s = 120
# proxy_protocol = true          # a PROXY v2 header carries the client's address in-band
# handover = "listen_fd"         # macOS and Linux: an inherited listening socket instead (below)

[[services]]
name = "node"
exec = ["python", "-I", "{bundle}/node_service.py"]
lifecycle = "always"             # a listener is wanted for as long as the module is enabled
endpoint = true                  # connections and announcements arrive on the endpoint channel
listeners = ["peer"]
```

Each listener is listed by exactly one service; that service is an endpoint service with `lifecycle = "always"`. It
needs no pool, though it may also provide one and serve jobs. `inbound` needs no network mode: answering a connection is
not egress. Use TOML tables (`[[sandbox.net.inbound]]`) rather than an inline table spread over several lines, which
TOML 1.0 does not allow.

The grant is approved by digest like every other: the operator reads "accepts connections from the internet on nodes
the owner assigns: listener peer (TCP, port 9000 wanted, stable; at most 256 connections, 16 per address, 4 new a second
per address, idle 120 s)".

## 2. Serve it

`start` leaves a process running that inherits the endpoint channel (`ep.inherit_channel()`), says hello and accepts.
Listener connections arrive on the same acceptor as jobs' connections, each with its `Origin`:

```python
from oarbank_sdk import service_endpoint as ep

def serve():
    acc = ep.Acceptor()                       # says hello
    READY.write_text("1")
    for conn, origin in acc:                  # ends when the agent closes the channel: exit then
        if origin.listener == "peer":         # origin.peer is the client, "203.0.113.5:51000"
            threading.Thread(target=handle_peer, args=(conn,), daemon=True).start()
```

For HTTP, `ep.serve_http(Handler, acc)` serves listener connections too: `self.client_address` is the client and
`self.server.origin` says which listener (or which job attempt) the connection came from.

## 3. Read the external address

The agent writes `OARBANK_LISTENERS_FILE` before the service starts and announces each listener after the hello and on
every change. The acceptor keeps the latest announcement per listener:

```python
def changed(name, ann):
    # ann = {"external": "198.51.100.7:9000", "external_v6": "[2001:db8::7]:9000",
    #        "state": "reachable", "since": 1790000000.0}
    if ann["state"] in ("reachable", "mapped_unverified") and ann["external"] != published.get(name):
        publish_my_address(ann["external"])   # within your network's own rate limits

acc = ep.Acceptor(on_listener=changed)
acc.external("peer")                          # the latest external IPv4 address, or None
```

`state` is `unmapped`, `mapped_unverified` (the router holds the mapping; nothing outside has confirmed it yet),
`reachable` (a dial-back from another network succeeded) or `unreachable`. Announcements are handled on the thread that
iterates the acceptor, in order with connections.

## 4. Narrow who may connect

If only some networks should reach you (a set of validators, say), narrow the listener. The agent intersects your list
with the owner's allowlist, so you can only ever narrow:

```python
acc.narrow("peer", ["198.51.100.0/24", "2001:db8:1::/48"])     # replace the narrowing
acc.narrow("peer", ["203.0.113.0/24"], append=True)            # add to it
acc.narrow("peer", None)                                       # drop it
```

Long lists are split into several messages, each line under 4096 bytes.

## 5. The client's address in-band: PROXY v2

With `proxy_protocol = true` the agent writes a PROXY protocol v2 header before the client's first byte. Read it first:

```python
src, dst = ep.read_proxy_v2(conn)             # (("203.0.113.5", 51000), ("192.0.2.10", 41000))
```

or let `serve_http(Handler, acc, proxy=["peer"])` read it for you. The header always comes from the agent: nothing from
the network is passed through as one.

## 6. A third-party server: `handover = "listen_fd"`

Some servers cannot take connections from an Oarbank channel but serve on an inherited listening socket. With
`handover = "listen_fd"` (macOS and Linux), the agent creates a unix socket in a directory only its own account can
enter, listens on it, and your service's `start` inherits it in `OARBANK_LISTEN_FD_<NAME>`. The service only accepts on
it; its sandbox still refuses `listen` and `bind`. `proxy_protocol` defaults to true here, because the header is the only
way the server learns the client's address.

```python
sock = ep.listen_socket("peer")               # accept() on it
# or execute a server that follows the systemd convention: fds 3.., LISTEN_FDS, LISTEN_FDNAMES, LISTEN_PID
ep.exec_with_listen_fds([str(BUNDLE / "bin" / "server"), "--proxy-protocol"], ["peer"])
```

`inherit_channel()` passes the listening sockets on with the channel. The endpoint channel still carries announcements
and narrowing, so a server you execute in place of your process no longer reads them; to keep them, start the server as
a child that calls `exec_with_listen_fds` and keep the acceptor in the parent. Windows nodes refuse a `listen_fd`
listener (`LISTENER_HANDOVER_UNSUPPORTED`), and `oarbank-sdk check` warns when such a module declares a Windows platform.

## 7. What the owner does on the node

Nothing listens until the owner assigns the listener to a node: the node's signed statement lists `<module>/<listener>`
with the external port, a fallback when the router holds the port for someone else, lower limits and an allowlist. The
agent then maps the port on the router (PCP, NAT-PMP or UPnP IGD), or the owner forwards it by hand, and a node on
another network dials back to prove it reachable. See [Make a node reachable](make-a-node-reachable.md).

## 8. Test it

`oarbank-sdk conform` runs the **listener** suite: it starts the service as the agent does, announces each listener
(`203.0.113.7:<port_hint>`), hands it a connection on each listener from `198.51.100.23:51000`, sends your
`listener_specs`, and fails a service that listens itself:

```json
{"listener_specs": [{"name": "one-line", "listener": "echo", "send": "hello\n",
                     "expect": {"contains": "echo hello 203.0.113.7:9000"}},
                    {"name": "status", "listener": "api", "method": "GET", "path": "/status",
                     "expect": {"status": 200}}]}
```

In your own tests, `oarbank_sdk._endpoint_host.ServiceHost` plays the agent: `listener_connection`, `announce`, and the
`listener_allow` messages it received in `allows`.
