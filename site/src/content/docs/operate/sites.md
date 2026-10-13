---
title: Sites and shared internet lines
description: Group the nodes that share one internet line into a site, so fleet transfers and network-heavy jobs pause on all of them while a protected program at the site uploads, and keep to an uplink budget otherwise.
type: how-to
platforms: [macos, linux, windows]
core: '2.10.2'
sidebar:
  order: 15
---

Machines behind one internet line slow each other's large transfers down. A large upload that takes about 40 s alone can
take four times as long while another machine on the same line uploads too. Host protection only acts on the node it
runs on, so a program you protect on one machine can still miss an upload deadline because fleet work on *another*
machine at the same place started a large transfer.

A **site** groups the nodes that share a line. While a protected process at the site is in a phase its rule tags
`uplink`, network-heavy fleet work pauses on **every** node of the site. Outside such phases the site's **uplink budget**
limits how much of it runs at once. A node is at one site at most.

## 1. Tag the upload phase

The protected program's rule says when it uploads, from a source the program writes (its own event log, for example),
and tags that phase `uplink` ([Host protection](/oarbank/operate/host-protection)):

```toml
[[rule]]
id        = "uploader"
match     = { path_prefix = "/Applications/Example Uploader.app/" }
immediate = true
[[rule.during]]
source    = { jsonl = "~/Library/Application Support/Example/events-*.jsonl", event = "status",
              field = "phase", on = ["uploading"] }
cap_fleet = { staging_mbps = 0 }
tags      = ["uplink"]
```

`cap_fleet` and `pause_fleet` in the block still act on that node only; the tag is what reaches the rest of the site. A
phase source that cannot be read counts as uploading, and so does a node with such a phase that stops reporting for a
minute: the site holds rather than guesses.

## 2. Create the site

The **Sites** page suggests sites from what the nodes observe: a default gateway they share, or a public address they
connect from. Only keyed fingerprints are kept, never an address. Accept a suggestion, or create a site yourself:

```sh
oarbank sites suggest                                   # nodes that look like they share a line
oarbank sites accept 3f9a1c0b2d4e5f60 --name home       # a suggestion as a new site
oarbank sites create home --node mac-01 --node mac-02   # or by hand
oarbank sites add home laptop                           # a node at another site moves here
oarbank sites show home
```

Two routers on one line (double NAT) look like two gateways: create one site and add both groups of nodes to it.

`oarbank sites remove laptop` takes a node out of its site, `oarbank sites delete home` deletes a site, and
`oarbank sites dismiss <key>` drops a suggestion. On the console, the **Sites** page offers **Create site**, **Add to
<site>** and **Dismiss** for each suggestion, and a site's page has **Add**, **Remove**, the budget form and **Delete**.
Creating, changing and adding are previewed: the preview names each node that joins or moves.

## 3. Set the budget

| Budget | Default | Meaning |
|---|---|---|
| Transfers at once (`--transfers`) | 1 | Dataset staging, result and checkpoint uploads the agents make at the same time |
| Network-heavy jobs at once (`--heavy-jobs`) | 1 | Jobs whose stage declares `network = "heavy"`; 0 admits none at the site |
| Fleet rate (`--mbps`) | no limit | The rate the fleet's transfers may use together; each transfer is paced at its share |

```sh
oarbank sites update home --transfers 2 --mbps 150
```

Each transfer is paced at the smallest of its share of the site's rate, the node's `staging_mbps` cap (a setting that
is enforced from 2.10) and a protection rule's staging cap. Only large transfers (8 MiB and more) take part; small ones
are never held.

The site's page shows how fast large transfers there have gone ("large transfers here reached 230 Mbit/s up"): a
ceiling to start from, not the line's capacity, since many of them go to the coordinator on your own network.

## What pauses, and how fast

While the site holds:

- **Transfers** stop between chunks on every node and resume where they stopped when the phase ends. The node whose
  program uploads stops its own at once; the others within about 5 seconds (measured: well under a second on a busy
  Mac).
- **Network-heavy jobs** pause (their runners pause on request or are frozen). A job that can do neither, or that would
  wait longer than the node's longest pause, is released and requeued without counting as a failure.
- **New network-heavy jobs** are not started at the site. `oarbank explain job <id>` says why: "Network-heavy: site home
  holds such work while mac-01's rule uploader is in an upload phase" (`SITE_UPLINK_BUSY`). Outside a hold, a job that
  would exceed the budget waits with `SITE_UPLINK_BUDGET` ("site home already runs 1 of its 1 network-heavy jobs"), whose
  remedy is raising the budget (`oarbank sites update home --heavy-jobs 2`).

- **Network-heavy workloads** ([Workloads](/oarbank/operate/workloads)) pause too, for at most their pause tolerance,
  then stop and hold until the phase ends; one that cannot pause stops and holds at once. They are not started while the
  site holds. Running network-heavy workloads count against the site's budget of network-heavy jobs, but being
  assigned by you, they are never refused for it.

A released job ends as `PREEMPT_SITE`: requeued, never charged to the job or the node. Inbound listeners keep relaying
connections during a hold; a workload that serves heavy traffic declares itself network-heavy and is paused at the
source.

A module author marks a stage network-heavy in its manifest (`requires = { resources = { network = "heavy" } }`,
[manifest reference](/oarbank/spec/manifest#network-heavy-stages)). The agent's own transfers need no declaration.

## Read what happened

- The **Sites** page: each site's state in words ("paused because mac-01's rule uploader is in an upload phase"), its
  members, budget and transfers. A site's page adds each member's `uplink` phases and its agent's transfers, the leases in
  force and recent holds. A node's page has a **Site** card naming its site, or "at no site".
- `oarbank sites list` and `oarbank sites show home` print the same.
- The node's decision timeline: `TRANSFER_PAUSED` and `TRANSFER_RESUMED`, with why and for how long, and `SITE_PAUSE`
  and `SITE_RESUME` for network-heavy jobs paused and resumed for the site.
- `oarbank verify` checks that a site's leases never exceed its budget and none is live while it holds (S27).

Releases and agent updates are not paused, and container image pulls are made by the container runtime outside the
agent; they are occasional. Routers are never touched: the fleet only holds itself back.
