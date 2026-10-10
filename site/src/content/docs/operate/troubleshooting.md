---
title: Troubleshooting
description: Find out why a job waits, a node takes no work or setup stalls, where each computer keeps its logs, and the fixes for the problems people meet most on macOS, Linux and Windows.
type: troubleshooting
platforms: [macos, linux, windows]
core: '2.7.0'
sidebar:
  order: 3
---

Start by asking Oarbank. It records why it does what it does, so the answer is usually one command away. The commands
are in the [command-line reference](/oarbank/reference/cli), and every code they print is in
[Reason codes and alerts](/oarbank/reference/reason-codes).

## First steps

On the coordinator:

1. `oarbank fleet`: is every node online and active, and are enrollments waiting for approval?
2. `oarbank explain job <id>` for a job that does not run, or `oarbank explain node <node>` for a node that takes no
   work. The answer is the scheduler's own reason codes, with the remedy for each.
3. `oarbank node show <node>`: the node's doctor results, GPU APIs, containers, services, folders and sandbox.
4. `oarbank alerts list`: what the fleet already noticed, each alert with its runbook line.
5. `oarbank verify`: protocol invariants and fleet health (exit 1 on a violation).

The console shows the same: each node's page, each job's attempts and the Verify page.

## Logs

| | Where |
|---|---|
| Coordinator, macOS | `~/Library/Application Support/Oarbank/coordinator/logs`; services `dev.codonic.oarbank.oarbankd` and `dev.codonic.oarbank.console` (`launchctl print gui/$(id -u)/dev.codonic.oarbank.oarbankd`) |
| Coordinator, Linux | `${XDG_DATA_HOME:-~/.local/share}/oarbank/coordinator/logs`; systemd user services (`systemctl --user status 'dev.codonic.oarbank.*'`); each service writes `<service>.log` there |
| Coordinator, Windows | `C:\ProgramData\Oarbank\coordinator\logs` (administrators only); services `dev.codonic.oarbank.oarbankd` and `dev.codonic.oarbank.console` |
| Node, macOS | `~/Library/Application Support/Oarbank/agent/logs` (personal scope) or `/Library/Application Support/Oarbank/agent/logs` (system scope); service `dev.codonic.oarbank.agent` |
| Node, Linux | `/var/lib/oarbank/agent/logs`; `systemctl status dev.codonic.oarbank.agent` |
| Node, Windows | `C:\ProgramData\Oarbank\agent\logs`; services `dev.codonic.oarbank.agent` and `OarbankHelper` |

A job's own output is in its attempts: `oarbank job show <id>`, or the job's page in the console.

Logs can contain host names, user names and paths. Remove them before you paste a log anywhere public.

## Setup

**The setup wizard closed before it finished.** Choose **Open web app** in the menu bar or tray icon again. It shows
the saved address and account; enter the original password to continue. Accounts and keys made so far are kept.

**Services need restarting after an upgrade or a move to another folder.** Run the bundled `oarbank-setup` (paths in
the [command-line reference](/oarbank/reference/cli#where-the-commands-are)). It refreshes an existing installation and
never makes a new fleet.

**Windows says the installer is from an unknown publisher.** The MSIs are not code-signed yet, so SmartScreen warns.
Check the file against the release's `SHA256SUMS` before you run it.

**Windows asks for elevation.** Installing, and setting up or recovering services, need an administrator. Opening a
configured, running web app does not. The `oarbank` CLI on Windows needs an elevated prompt.

**A Linux coordinator stops when you log out.** Its services are systemd user services. Run
`sudo loginctl enable-linger <user>` to keep them running from boot.

**The Linux package does not start: a glibc error.** The published Linux packages need glibc 2.39 or newer (Ubuntu
24.04 or later, and equivalents). Check with `ldd --version`.

## Nodes

**A node does not appear.** A join code is single-use and expires (`--ttl`). Make a new one with
`oarbank join-code --label <node>`. A node set up with a coordinator URL instead waits for approval: `oarbank fleet`
lists it under PENDING, and `oarbank node approve <enrollment>` admits it.

**The coordinator is not found on the local network (macOS).** macOS 15 and later can block the coordinator's
announcement or the node's search under Local Network privacy. `oarbank-agent discover` says so. Allow the program in
System Settings, Privacy & Security, Local Network, or enroll with a join code, which names the coordinator's address and
needs no discovery. A Tailscale or VPN address is not "local network".

**A node is offline** (alert `node_offline`). Check the machine is awake and reaches the coordinator on port 7443/tcp,
then that its agent service `dev.codonic.oarbank.agent` runs (launchctl on macOS, systemctl on Linux, the service
manager on Windows). Its leases are already back in the queue.

**A node takes no work.** Run `oarbank explain node <node>`. Common answers: it is paused or draining
(`oarbank node state <node> active`), quarantined after wrong answers (investigate before clearing), still installing a
release, its clock is off (`CLOCK_SKEW`: set the time), or host protection is yielding to the person using it
(`oarbank node mode`).

**GPU jobs never go to a node** (`GPU_API_MISSING`). `oarbank node show <node>` lists the GPU APIs the node provides,
with evidence, and `oarbank-agent gpu-apis` on the node says why one is missing (on Linux:
`sudo -u oarbank /usr/lib/oarbank/oarbank-agent gpu-apis`). On Linux, add the `oarbank` account to
the groups that own the GPU device files (`sudo usermod -aG render,video oarbank`) and restart the agent. On a Mac
with Apple silicon, GPU containers need krunkit (see [Requirements](/oarbank/operate/requirements)).

**Containers do not run on Windows.** The agent runs its own WSL container session. It needs Windows 10 2004 or later,
WSL 2.9.3 or later, the Virtual Machine Platform and hardware virtualization. `oarbank-agent containers doctor` lists
each missing piece with its fix, and `oarbank-agent containers install` installs them.

**Containers on a Linux node with Podman fail on user ids.** The `oarbank` account needs subordinate ids:
`sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 oarbank`.

## Jobs and modules

**A job stays pending.** `oarbank explain job <id>` names the reason. The frequent ones:

| Code | What to do |
|---|---|
| `NO_ELIGIBLE_NODE`, `PLATFORM_UNSUPPORTED` | No online node meets the job's platform, pool or capability needs: add or wake one |
| `MODULE_NOT_READY` | The module's doctor fails on the node: `oarbank node show <node>` lists the failing checks; fix them, then re-run the doctor |
| `MODULE_NOT_CERTIFIED` | The node has not passed the module's golden jobs yet; it does this on its own once the doctor passes |
| `SECRETS_NOT_SET` | `oarbank secret set <module> <name>` |
| `AGENT_TOO_OLD` | Update the node's agent: `oarbank agent list`, then `canary` and `promote` |
| `OARBANK_PAUSED`, `CAMPAIGN_PAUSED` | `oarbank resume --all`, or `oarbank campaign resume <id>` |
| `RETRIES_EXHAUSTED` | Read the attempts in `oarbank job show <id>`, fix the cause, then `oarbank job retry <id>` |

**A job fails.** The attempt's end code says how (`EXIT_NONZERO`, `TIMEOUT`, `OOM`, `INVALID_SPEC`,
`MISSING_DEPENDENCY`…). `oarbank job show <id>` shows each attempt's output.

**Jobs keep failing on one node** (alert `breaker`). Read that node's attempts. It re-runs its doctor by itself; if it
keeps tripping, quarantine it.

**A secret cannot be read** (alert `secret_unreadable`). The coordinator's secrets key did not come with a restored
backup or a copied home. Set the secret again with `oarbank secret set`.

**A module will not install.** `oarbank module install <bundle> --dry-run` shows why. A module whose `requires.core`
excludes your coordinator's version is refused: install a coordinator that meets it (see
[Versions and compatibility](/oarbank/operate/versions)). Module authors run `oarbank-sdk check` and
`oarbank-sdk conform` before bundling.

## Asking for help

Open an issue on [roeehrl/oarbank](https://github.com/roeehrl/oarbank/issues) for the coordinator and nodes, or on
[roeehrl/oarbank-sdk](https://github.com/roeehrl/oarbank-sdk/issues) for the SDK and these docs. Include the versions
([how to find them](/oarbank/operate/versions#find-the-installed-versions)), the operating system, the command and its
output. Report security problems privately to support@codonic.dev, never in a public issue.
