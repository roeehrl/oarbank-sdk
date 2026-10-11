# Make a node reachable

A module with an inbound listener ([Accept connections from the internet](inbound-listeners.md)) accepts connections
from the internet only on nodes you assign it to. The agent on the node listens, asks the router to forward the port,
and has the listener dialled from outside to prove it works. Module code never listens itself. This page is what you do
as the owner, what the node page's **Network** card shows, and what each result means. It needs core 2.10 on the
coordinator and on the node.

## 1. Approve the grant

Installing a module version whose `[sandbox].net.inbound` asks for a listener shows the request at approval, for
example "accepts connections from the internet on nodes the owner assigns: listener peer (TCP, port 9000 wanted,
stable; at most 256 connections, 16 per address, 4 new a second per address, idle 120 s)". Approving it opens nothing
yet.

## 2. Assign the listener to a node

On the node page's **Network** card, **Assign or change a listener…**, or from a terminal:

```text
oarbank listener set build-07 example/peer --external-port 9000
oarbank listener set build-07 example/peer --fallback next_free:9000-9015 --limit max_conns=64 --allow 198.51.100.0/24
```

The entry becomes part of the node's statement. In signing mode, sign it (`oarbank node sign build-07`): the node applies
only a statement you signed, so a compromised coordinator cannot open a port. Per node you may set:

| Setting | Flag | What it does |
|---|---|---|
| external port | `--external-port` | the port to ask the router for (default: the module's `port_hint`) |
| fallback | `--fallback` | when the router holds the port for another device: `refuse` (the default for a published, stable port), `next_free:<a>-<b>` (the first free port of a range, the same one on every start while it is free) or `router_choice` |
| mapping | `--mapping` | `auto` (try PCP, NAT-PMP, then UPnP), one of `pcp`, `natpmp`, `upnp`, `manual` (you forward the port yourself) or `none` (the machine has a public address) |
| internal port | `--internal-port` | the port the agent binds; set it when you forward by hand, so the forward never moves. Without it the agent takes a port from `listener_port_range` (default 41000–41999), the same one on every start while it is free |
| bind | `--bind` | `default_route` (the node's address on its default route, followed when the network changes) or one of the node's addresses |
| IPv6 | `--ipv6` | `auto` also accepts on the node's global IPv6 address and asks for a firewall pinhole; `off` |
| limits | `--limit KEY=VALUE` | lower the module's `max_conns`, `max_conns_per_ip`, `new_conns_per_ip_per_s`, `idle_timeout_s` or `max_bytes_per_s`; never above the module's |
| allow | `--allow CIDR` | only these client networks may connect (the module may narrow it further, never widen it) |

`oarbank listener remove build-07 example/peer` (the card's **Remove**) takes the entry away; the agent closes the
listener and deletes the mapping it made. A listener also needs an enabled service or workload of the module that
serves it ([Workloads](https://docs.codonic.dev/oarbank/operate/workloads)): a workload's listener opens on nodes the
workload is assigned to.

### When a listener stays closed

The card's state column says `listening`, `held`, `closed` or `refused` with a code, and `oarbank explain node` names
the listener with `LISTENER_UNAVAILABLE`:

| Code | Why | What to do |
|---|---|---|
| `LISTENER_NOT_GRANTED` | the module version on this node does not ask for that listener | enable a version that does, and approve its grant |
| `LISTENER_NO_TARGET` | no enabled service or workload of the module serves it | enable the service, or assign the workload to this node |
| `MODULE_DISABLED` | the module is disabled | enable it |
| `LISTENER_HANDOVER_UNSUPPORTED` | the listener hands over a listening socket, which Windows cannot do | use a macOS or Linux node |
| `LISTENERS_DISABLED` | the kill switch is on for the node or the fleet | `oarbank listener resume` (below) |
| `LISTENERS_FORBIDDEN` | the machine's managed policy sets `inbound_listeners` off | ask the machine's administrator |
| `LISTENER_STRICT_YIELD` | the node's protection mode is `strict_yield` | choose another mode for the node, or another node |
| `LISTENER_BIND_FAILED` | the OS refused the port (the error is shown) | choose another internal port or free the port |

### The kill switch

```text
oarbank listener disable --node build-07     # close every listener on the node and remove its router mappings now
oarbank listener disable --all               # the same for the whole fleet
oarbank listener resume --node build-07      # or --all
```

The card's **Disable listeners** and **Resume listeners** do the same. The node setting `inbound_listeners` (on by
default) is the lasting switch: off, no listener opens on that computer and every mapping is removed, and a machine's
managed policy can force it off.

## 3. Read the result

The **Network** card and `oarbank listener show build-07 example/peer` give the listener's state, where it is
reachable, its router mapping, its connections (open, accepted, refused by cause) and one result with the fix, in the
agent's own words. The card also shows the router's gateway and public address, which mapping protocols it answers and
whether this machine's firewall allows the agent. Opening a listener's row (or running `show`) asks the node for its top
client addresses of the last 24 hours.

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
| `MANUAL_UNVERIFIED` | you forward the port by hand and nothing has checked it yet | wait for the next dial-back, or **Check now** |

Flags add detail: `DYNAMIC_ADDRESS` (the public address changes; the workload is told each new one),
`PERMANENT_ONLY_ROUTER` (the router keeps mappings until they are deleted: uninstall Oarbank properly, or delete the
mapping on the router when you retire the machine), `IPV6_REACHABLE` and `IPV6_PINHOLE_NEEDED`.

Two alerts watch listeners: `listener_unreachable` (P3, a listener cannot be reached from the internet for 10 minutes)
and `listener_saturated` (P4, a listener keeps refusing connections at its caps: if the clients are legitimate, raise
the limits; if not, narrow `--allow`).

## 4. Forward a port by hand

When the router refuses automatic mapping, forward TCP from the external port to the node's LAN address and the
listener's internal port (shown on the Network card, for example `192.0.2.20:41000`), then fix both ends:

```text
oarbank listener set build-07 example/peer --mapping manual --internal-port 41000
oarbank node sign build-07                   # in signing mode
```

## 5. Proving reachability

A dial-back must come from outside your network: a router that loops a connection back from inside proves nothing. The
coordinator picks an online node whose public address differs from the target's, after a mapping is made, every 30
minutes while it holds, and on **Check now** (`oarbank listener probe build-07 example/peer`). A node that turns out to
be on the same network says so, and another is tried.

With only one network, run a probe endpoint on a machine elsewhere. It speaks plain HTTP, so put it behind HTTPS (a
reverse proxy), then name its `https://` URL in the fleet setting `listener_probe_endpoint`:

```text
oarbank-agent probe-endpoint --bind 127.0.0.1:7480
oarbank settings set listener_probe_endpoint https://probe.example.net/
```

Nothing else is ever contacted unless you name it: the address check through a STUN server (`listener_stun_server`,
`host:port`) is off by default.

## 6. Firewalls

| OS | What happens |
|---|---|
| macOS | With the Application Firewall on, the node package lists the agent as allowed when it installs, so a first connection raises no prompt. `oarbank-agent firewall status` shows the state; `sudo oarbank-agent firewall allow` adds the agent again |
| Linux | `oarbank-agent firewall status` says whether nftables, ufw or firewalld is active; `sudo oarbank-agent firewall allow --port 41000` opens the listener ports for the agent only |
| Windows | The node's elevated helper adds an inbound firewall rule for the agent and the exact port when a listener opens, and removes it when it closes |

## 7. What the agent never does

It never maps a port for another device or below 1024, never deletes a mapping it did not make (the Network card lists
the router's other mappings as "not Oarbank's"), and keeps its mappings short-lived (two hours, one for UPnP) and
renewed, so a machine that disappears leaves nothing open for long. It maps again after a router restart, an address or
network change and a wake from sleep.

`oarbank listener mappings` lists every router mapping the fleet's agents hold. On a node, `oarbank-agent portmap
status` lists what the agent holds. Stopping the agent deletes its mappings, and removing Oarbank from a node runs
`oarbank-agent portmap release-all`.
