---
title: Updating and removing
description: Update the coordinator and the nodes' agents, move the coordinator to another computer, and remove a node or the coordinator on macOS, Linux and Windows without losing your owner keys by accident.
type: how-to
platforms: [macos, linux, windows]
core: '2.8.0'
sidebar:
  order: 5
---

This page covers Oarbank 2.8.0. The full install guide, including building the packages, is
[docs/install.md](https://github.com/roeehrl/oarbank/blob/v2.8.0/docs/install.md) in the core's repository.

## Update the coordinator

Download the new installer from the [releases](https://github.com/roeehrl/oarbank/releases) and check it. Each
package set has its own checksum file beside it, for example `SHA256SUMS-coordinator-packages-2.7.0-linux-amd64` for
the Linux `.deb` and `.rpm`, or `SHA256SUMS-coordinator-msi-2.7.0-windows-x64` for the Windows MSI:

```sh
sha256sum --ignore-missing -c SHA256SUMS-coordinator-packages-2.7.0-linux-amd64   # macOS: shasum -a 256 --ignore-missing -c …
```

Then run the installer as you did the first time.

- **macOS and Linux:** the new build becomes `current`, the services restart, and agents reconnect by themselves.
  Modules' Python environments are rebuilt on the new build's interpreter when it starts. Earlier builds stay beside it
  for going back.
- **Windows:** install the new MSI. It restarts the coordinator services with the updated payload.

A coordinator installed from an **archive** (the only way before 2.6, and the advanced setup since) updates with the
new archive's helper instead: `bash install-oarbankd.sh --build <archive> --agent-bind <address>` (Windows: elevated
`install-oarbankd.ps1 -Build <archive> -AgentBind <address>`), with `--dry-run` / `-DryRun` to see the plan first.
Earlier builds stay beside it.

If the services do not come back, run the bundled `oarbank-setup` (paths in the
[command-line reference](/oarbank/reference/cli#where-the-commands-are)). It refreshes the existing installation and never
makes a new fleet.

## Update the nodes

Nodes update their agent from the coordinator; nobody needs to log in to them.

Upload the release's agent binary for each platform your nodes run (`oarbank-agent-2.7.0-darwin-arm64`,
`-darwin-amd64`, `-linux-amd64`, `-linux-arm64`, `-windows-x64.exe`, `-windows-arm64.exe`; each has a
`SHA256SUMS-agent-…` file), then sign, try and promote it:

```sh
oarbank agent upload oarbank-agent-2.7.0-linux-amd64
oarbank agent list                               # the uploaded builds, with their sha256
oarbank agent sign <build>
oarbank agent canary <build> --node <node>       # try it on one node first
oarbank agent promote <build>
```

`<build>` is the version (`2.7.0`) when only one uploaded build has it; with one build per platform, use the first 8 or
more characters of its sha256 from `oarbank agent list`. `oarbank agent list` also shows which version each node
runs. The launcher keeps the previous version and rolls back a build
that does not confirm itself within 10 minutes. Installing a newer node package also works: it replaces the launcher and
restarts the service.

## Move the coordinator to another computer

```sh
oarbank coordinator prepare --to <node>
oarbank coordinator move          # with `sign` and the owner key in signing mode
```

The target node's agent installs the standby from a signed coordinator build. Agents verify the move and follow it after
its time lock (24 hours by default). `oarbank coordinator status` shows where it is, and `cancel` stops it. To move to
a Windows computer, prepare with its URL (`--to https://<host>:7443`) and run the installer there with the printed
pairing code.

## Remove a node

To take a node out of the fleet but keep the software, run `sudo oarbank-node leave` on it (on Windows, from an
elevated prompt): the node forgets the coordinator, and its key, certificate, caches and logs go. It can join a fleet
again with a new code. Retire it on the coordinator's Fleet page too. To remove the software as well:

| | |
|---|---|
| macOS | `sudo /Library/Oarbank/bin/oarbank-uninstall --purge` unloads the service, deletes the node's home (its key, certificate, caches and logs) and removes the programs and **Oarbank Node.app**. Without `--purge` the home stays. |
| Linux | `sudo apt remove oarbank-agent` or `sudo dnf remove oarbank-agent` stops and removes the service and keeps the node's home, `/var/lib/oarbank`; `sudo apt purge oarbank-agent` also deletes it. |
| Windows | Uninstall **Oarbank agent** from Installed apps. |

## Remove the coordinator

Removing the coordinator ends the fleet. Its state and your **owner keys** are kept unless you delete them. Keep the
keys unless you mean to destroy them: they sign releases and coordinator moves.

| | |
|---|---|
| macOS | `launchctl bootout gui/$(id -u)/dev.codonic.oarbank.oarbankd` and `launchctl bootout gui/$(id -u)/dev.codonic.oarbank.console`, then remove their two plists from `~/Library/LaunchAgents` and delete `/Applications/Oarbank Coordinator.app`. Repeat the service steps for each user who set it up. State stays in `~/Library/Application Support/Oarbank/coordinator`, keys in `~/Library/Application Support/Oarbank/keys`. |
| Linux | Remove `oarbank-coordinator` with your package manager. Its removal stops and removes the user services that use it, and keeps data, keys, logs and lingering. If cleanup fails, removal stops so you can fix the service and retry. |
| Windows | Uninstall **Oarbank Coordinator** from Installed apps. It stops and removes both services and the firewall rule, and keeps the state in `C:\ProgramData\Oarbank\coordinator` and the signing keys. |
