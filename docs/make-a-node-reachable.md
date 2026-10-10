# Make a node reachable

A module with an inbound listener ([Accept connections from the internet](inbound-listeners.md)) is reachable only on
nodes you assign it to. The agent on the node listens, asks the router to forward the port, and has the listener
dialled from outside to prove it works. This page is what you do as the owner, and what each result means.

## 1. Approve the grant

Installing a module version whose `[sandbox].net.inbound` asks for a listener shows the request at approval, for
example "accepts connections from the internet on nodes the owner assigns: listener peer (TCP, port 9000 wanted,
stable; at most 256 connections, 16 per address, 4 new a second per address, idle 120 s)". Approving it opens nothing
yet.

## 2. Assign the listener to a node

On the node page (Network), or with the `listeners.configure` operation, add an entry for `<module>/<listener>`. The
entry becomes part of the node's statement; in signing mode sign it (`oarbank node sign <node>`), because the node
applies only a statement you signed, so a compromised coordinator cannot open a port. Per node you may set:

| Setting | What it does |
|---|---|
| external port | the port to ask the router for (default: the module's `port_hint`) |
| fallback | when the router holds the port for another device: `refuse` (the default for a published, stable port), `next_free:<a>-<b>` (the first free port of a range, the same one on every start while it is free) or `router_choice` |
| mapping | `auto` (try PCP, NAT-PMP, then UPnP), one of them, `manual` (you forward the port yourself) or `none` (the machine has a public address) |
| internal port | the port the agent binds; set it when you forward by hand, so the forward never moves |
| IPv6 | `auto` also accepts on the node's global IPv6 address and asks for a firewall pinhole; `off` |
| limits | lower the module's connection and rate limits; never above them |
| allow | only these client networks may connect |

`listeners.remove` takes the entry away; the agent closes the listener and deletes the mapping it made.
`listeners.disable` (one node or the whole fleet) is the kill switch, `listeners.resume` undoes it.

## 3. Read the result

The node page and `oarbank listener show` give the listener's state and one result, with the
fix:

| Result | Meaning | What to do |
|---|---|---|
| `REACHABLE` | a node on another network dialled it at the announced address | nothing |
| `PUBLIC_ADDRESS` | the machine has a public address, no router mapping is needed | make sure no firewall blocks the port |
| `MAPPED_UNVERIFIED` | the router holds the mapping; nothing outside has checked it yet | add a node on another network, or run a probe endpoint (below) |
| `NO_MAPPING_PROTOCOL` | the router answers neither PCP, NAT-PMP nor UPnP | turn on UPnP or NAT-PMP in the router's settings, or forward the port by hand and set mapping to `manual` |
| `DOUBLE_NAT` | the machine is behind two routers | put the outer device in bridge mode, forward the port on both, or use another machine |
| `CGNAT` | the provider shares one public address among many customers | port forwarding cannot work: ask for a public IPv4 address, use IPv6 if the workload accepts it, or run it elsewhere |
| `PORT_TAKEN` | the router forwards the port to another device | free it on the router, choose another external port, or allow a fallback range |
| `MAPPED_UNREACHABLE` | the mapping verified but dial-backs fail | check the router's own firewall, an upstream firewall, or whether the provider blocks inbound ports |
| `FIREWALL_BLOCKED` | this machine's firewall blocks the agent | allow it: `oarbank-agent firewall allow` (as an administrator) |
| `MANUAL_UNVERIFIED` | you forward the port by hand and nothing has checked it yet | wait for the next dial-back, or check now from the node page |

Flags add detail: `DYNAMIC_ADDRESS` (the public address changes; the workload is told each new one),
`PERMANENT_ONLY_ROUTER` (the router keeps mappings until they are deleted: uninstall Oarbank properly, or delete the
mapping on the router when you retire the machine), `IPV6_REACHABLE` and `IPV6_PINHOLE_NEEDED`.

## 4. Forward a port by hand

When the router refuses automatic mapping, forward TCP from the external port to the node's LAN address and the
listener's internal port (shown on the node page, for example `192.0.2.20:41000`), set the listener's mapping to
`manual` and its internal port to that port, and sign the statement again.

## 5. Proving reachability

A dial-back must come from outside your network: a router that loops a connection back from inside proves nothing. The
coordinator picks an online node whose public address differs from the target's. With only one network, run a probe
endpoint on a machine elsewhere and name it in the fleet settings (`listener_probe_endpoint`):

```text
oarbank-agent probe-endpoint --bind 203.0.113.40:8443
```

Nothing else is ever contacted unless you name it (a STUN server for the address check is off by default).

## 6. What the agent never does

It never maps a port for another device or below 1024, never deletes a mapping it did not make, and keeps its mappings
short-lived and renewed, so a machine that disappears leaves nothing open for long. `oarbank-agent portmap status` lists
what it holds and what the router says about each; uninstalling Oarbank runs `oarbank-agent portmap release-all`.
