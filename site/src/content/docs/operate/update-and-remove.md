---
title: Updating and removing
description: Update the whole fleet from the coordinator with one rollout, or a coordinator or node by hand, move the coordinator to another computer, and remove a node or the coordinator on macOS, Linux and Windows without losing your owner keys by accident.
type: how-to
platforms: [macos, linux, windows]
core: '2.10.1'
sidebar:
  order: 17
---

This page covers Oarbank 2.10.1. Coming from an older release, read [Upgrading](/oarbank/operate/upgrading) first:
from 2.8 the macOS and Linux coordinator moves to a system service once, and from 2.9 nodes need the 2.10 agent for the
new features. The full install guide, including building the packages, is
[docs/install.md](https://github.com/roeehrl/oarbank/blob/v2.10.1/docs/install.md) in the core's repository.

## Update the fleet

From 2.10 the coordinator updates every node's whole package (launcher, helpers, Oarbank Node app, runtime and agent)
and itself, ring by ring, rolling back by itself what does not come up healthy. Nobody logs in to the machines.

1. **Get the release.** On the **Updates** page, **Fetch** the newest release, or import one you downloaded: its
   `oarbank-fleet-<version>.json` and the packages your nodes and coordinator need, in one directory.

   ```sh
   oarbank update fetch 2.10.1          # from the release source (the project's GitHub releases by default)
   oarbank update import ./oarbank-2.10.1/
   oarbank update sign 2.10.1           # signing mode: with your owner key, on the machine that holds it
   ```

2. **Start it.** **Update fleet** on the Updates page, or `oarbank update start 2.10.1`, shows the plan first: the
   coordinator, then the `canary`, `early` and `broad` rings with the nodes in each, their update windows, and the
   nodes skipped and why (held, pinned, offline, no updater). Confirm it and the rollout runs by itself:
   - the coordinator updates first. It snapshots its database, tries the new build's migrations on a copy, then installs
     the new build beside the old one and keeps it only if it is healthy within five minutes;
   - then each ring, one node at a time by default. A node finishes its running jobs, installs the package, and keeps it
     only once its new agent has checked in, otherwise it goes back to the package it had. The next ring starts after the
     ring has baked;
   - a failure halts the rollout and rolls back the nodes it moved.

3. **Watch or steer it.** The Updates page shows each ring's progress and gates. `oarbank update status`, `pause`,
   `resume`, `halt`, `rollback` and `skip <node>` do what its buttons do.

**Policy.** Put machines in rings with the `update_ring` setting (a group of test machines as `canary`). Limit when
nodes may update with `update_window`. Hold a node with `oarbank update hold <node> --reason … --until …`, or pin it to
a version with `oarbank update pin`. Holds and pins expire. `update_auto = on` starts a rollout by itself when a signed
release is ready. The default, `notify`, tells you and waits for your click. A machine's managed policy (MDM, Group
Policy, `/etc/oarbank/policy.json`) can hold it or turn automatic updates off for it.

**The first time.** A node gets its update runner and pins the owner keys it trusts when its 2.10 package is installed.
A node that joined after its package was installed needs `sudo oarbank-launcher update-trust --from-agent` once. The
Updates page marks nodes without an updater and says why. Personal installs and development builds keep updating their
agent only (below).

## Update the coordinator by hand

Download the new installer from the [releases](https://github.com/roeehrl/oarbank/releases) and check it. Each
package set has its own checksum file beside it, for example `SHA256SUMS-coordinator-packages-2.10.1-linux-amd64` for
the Linux `.deb` and `.rpm`, or `SHA256SUMS-coordinator-msi-2.10.1-windows-x64` for the Windows MSI:

```sh
sha256sum --ignore-missing -c SHA256SUMS-coordinator-packages-2.10.1-linux-amd64   # macOS: shasum -a 256 --ignore-missing -c …
```

Then run the installer as you did the first time.

- **macOS and Linux:** the package writes the system services again from the record of how they were set up and
  restarts them on the new build; agents reconnect by themselves. Modules' Python environments are rebuilt on the new
  build's interpreter when it starts.
- **Windows:** install the new MSI. It restarts the coordinator services with the updated payload.

A coordinator installed from an **archive** (the only way before 2.6, and the advanced setup since) updates with the
new archive's helper instead, as root: `sudo bash install-oarbankd.sh --build <archive> --agent-bind <address>`
(Windows: elevated `install-oarbankd.ps1 -Build <archive> -AgentBind <address>`), with `--dry-run` / `-DryRun` to see the
plan first. Earlier builds stay beside it. `--refresh` writes the services again from the record of the install.

If the services do not come back, run `oarbank-setup` (on the PATH on macOS and Linux; paths in the
[command-line reference](/oarbank/reference/cli#where-the-commands-are)). It asks for an administrator, starts the
existing installation again and never makes a new fleet. [The coordinator service](/oarbank/operate/coordinator#check-and-restart-it)
shows how to look at the services and their logs.

## Update only a node's agent

For nodes without an update runner (a personal install, a development build) the coordinator can still replace the
agent binary alone; nobody needs to log in to them.

Upload the release's agent binary for each platform your nodes run (`oarbank-agent-2.10.1-darwin-arm64`,
`-darwin-amd64`, `-linux-amd64`, `-linux-arm64`, `-windows-x64.exe`, `-windows-arm64.exe`; each has a
`SHA256SUMS-agent-…` file), then sign, try and promote it:

```sh
oarbank agent upload oarbank-agent-2.10.1-linux-amd64
oarbank agent list                               # the uploaded builds, with their sha256
oarbank agent sign <build>
oarbank agent canary <build> --node <node>       # try it on one node first
oarbank agent promote <build>
```

`<build>` is the version (`2.10.1`) when only one uploaded build has it; with one build per platform, use the first 8 or
more characters of its sha256 from `oarbank agent list`. `oarbank agent list` also shows which version each node
runs. The launcher keeps the previous version and rolls back a build
that does not confirm itself within 10 minutes. Installing a newer node package also works: it replaces the launcher and
renders the agent's service definition again (`oarbank-launcher service refresh`; a Windows MSI recreates the service),
restarts the service, and on Macs and Windows it also brings the newer Oarbank Node app (and on Macs its administrator
helper and the node runtime), which an agent update does not.

## Move the coordinator to another computer

```sh
oarbank coordinator prepare --to <node>
oarbank coordinator move          # with `sign` and the owner key in signing mode
```

The target node's agent fetches and verifies the signed coordinator build. A node's agent runs unprivileged and system
services are root's, so it then reports the one command to run on that computer, as root:
`sudo bash …/install-oarbankd.sh --build … --agent-bind <address> --pair <code> --from … --from-ca …`, which installs the
standby as a system service. Agents verify the move and follow it after its time lock (24 hours by default).
`oarbank coordinator status` shows where it is, and `cancel` stops it. To move to a Windows computer, prepare with its
URL (`--to https://<host>:7443`) and run the installer there with the printed pairing code.

## Remove a node

To take a node out of the fleet but keep the software, run `sudo oarbank-node leave` on it (on Windows, from an
elevated prompt): the node forgets the coordinator, and its key, certificate, caches and logs go. It can join a fleet
again with a new code. Retire it on the coordinator's Fleet page too. To remove the software as well:

| | |
|---|---|
| macOS | `sudo /Library/Oarbank/bin/oarbank-uninstall --purge` unloads the service, deletes the node's home (its key, certificate, caches and logs) and removes the programs and **Oarbank Node.app**. Without `--purge` the home stays. |
| Linux | `sudo apt remove oarbank-agent` or `sudo dnf remove oarbank-agent` stops and removes the service and keeps the node's home, `/var/lib/oarbank`; `sudo apt purge oarbank-agent` also deletes it. |
| Windows | Uninstall **Oarbank agent** from Installed apps. |

Removing the node service first stops the node's [workloads](/oarbank/operate/workloads) and deletes the router port
mappings its agent made for [inbound listeners](/oarbank/operate/make-a-node-reachable) (`oarbank-agent stop-workloads`,
`oarbank-agent portmap release-all`). A mapping on a router that cannot be reached at that moment stays until its lease
runs out (at most two hours). Retiring a node that is offline warns that its router mappings may persist until then.

## Remove the coordinator

Removing the coordinator ends the fleet. Its state and your **owner keys** are kept unless you delete them. Keep the
keys unless you mean to destroy them: they sign releases and coordinator moves.

| | |
|---|---|
| macOS | `sudo bash "/Applications/Oarbank Coordinator.app/Contents/Resources/coordinator/install-oarbankd.sh" --uninstall --keep-programs` stops and removes the two daemons. Then delete `/Applications/Oarbank Coordinator.app` and the package's `oarbank` and `oarbank-setup` links, which point into it: `sudo find /usr/local/bin -lname '/Applications/Oarbank Coordinator.app/*' -delete`. State stays in `/Library/Application Support/Oarbank/coordinator` until you delete it with `sudo`, as do the `_oarbankd` account and the `_oarbankadmin` group (`sudo dscl . -delete /Users/_oarbankd`, `/Groups/_oarbankd`, `/Groups/_oarbankadmin`). Your keys stay in `~/Library/Application Support/Oarbank/keys`. |
| Linux | Remove `oarbank-coordinator` with your package manager (this also removes `/usr/bin/oarbank` and `/usr/bin/oarbank-setup`). Its removal stops and removes the system units, then any per-user units of an earlier release that still run its payload. It keeps `/var/lib/oarbank/coordinator`, the `oarbankd` account, the `oarbank-admin` group and your keys. If cleanup fails, removal stops so you can fix the service and retry. |
| Windows | Uninstall **Oarbank Coordinator** from Installed apps. It stops and removes both services and the firewall rule and takes `oarbank` off the PATH, and keeps the state in `C:\ProgramData\Oarbank\coordinator` and the signing keys. |
