---
title: Troubleshooting
description: Find out why a job waits, a node takes no work or setup stalls, where each computer keeps its logs, and the fixes for the problems people meet most on macOS, Linux and Windows.
type: troubleshooting
platforms: [macos, linux, windows]
core: '2.11.0'
sidebar:
  order: 20
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
| Coordinator, macOS | `/Library/Application Support/Oarbank/coordinator/logs` (read with `sudo`); launchd daemons `dev.codonic.oarbank.oarbankd` and `dev.codonic.oarbank.console` (`sudo launchctl print system/dev.codonic.oarbank.oarbankd`) |
| Coordinator, Linux | `/var/lib/oarbank/coordinator/logs` (read with `sudo`); systemd system units (`systemctl status dev.codonic.oarbank.oarbankd dev.codonic.oarbank.console`) |
| Coordinator, Windows | `C:\ProgramData\Oarbank\coordinator\logs` (administrators only); services `dev.codonic.oarbank.oarbankd` and `dev.codonic.oarbank.console` |
| Node, macOS | `~/Library/Application Support/Oarbank/agent/logs` (personal scope) or `/Library/Application Support/Oarbank/agent/logs` (system scope); service `dev.codonic.oarbank.agent` |
| Node, Linux | `/var/lib/oarbank/agent/logs`; `systemctl status dev.codonic.oarbank.agent` |
| Node, Windows | `C:\ProgramData\Oarbank\agent\logs`; services `dev.codonic.oarbank.agent` and `OarbankHelper` |

A job's own output is in its attempts: `oarbank job show <id>`, or the job's page in the console.

Logs can contain host names, user names and paths. Remove them before you paste a log anywhere public.

## Setup

**The setup wizard closed before it finished.** Choose **Open web app** in the menu bar or tray icon again. It shows
the saved address and account; enter the original password to continue. Accounts and keys made so far are kept.

**Services need restarting after an upgrade.** Run `oarbank-setup` (on the PATH on macOS and Linux; paths in the
[command-line reference](/oarbank/reference/cli#where-the-commands-are)). It asks for an administrator, starts the
existing installation again and never makes a new fleet.

**`oarbank` cannot reach the coordinator** (macOS, Linux). Only members of the coordinator's owners' group may use its
local admin channel: `_oarbankadmin` on macOS, `oarbank-admin` on Linux. Add yourself
([Who may use the CLI](/oarbank/operate/coordinator#who-may-use-the-cli)); on Linux, log in again afterwards. Otherwise
use `oarbank console login` or a token.

**The coordinator is not back after a restart.** It starts at boot, but binds the agent address only once that address
exists, retrying every 10 seconds: use a LAN address, or a VPN that connects before anyone logs in. A Mac with
FileVault that lost power waits at the disk unlock screen until someone types a password
([The coordinator service](/oarbank/operate/coordinator#setting-up)).

**The upgrade from 2.8 did not move the coordinator** (macOS). If the package could not reach your Keychain, the
coordinator keeps running in its per-user form and Oarbank Coordinator offers **Move the coordinator to a system
service…**; `oarbank coordinator migrate` does the same. If the move failed, it rolled back to the per-user
coordinator; `oarbank coordinator migrate --status` shows the error ([Upgrading](/oarbank/operate/upgrading#if-the-move-fails)).

**Windows says the installer is from an unknown publisher.** The MSIs are not code-signed yet, so SmartScreen warns.
Check the file against its `SHA256SUMS-…` file on the release page before you run it.

**Windows asks for elevation.** Installing, and setting up or recovering services, need an administrator. Opening a
configured, running web app does not. The `oarbank` CLI on Windows needs an elevated prompt.

**The Linux package does not start: a glibc error.** The published Linux packages need glibc 2.39 or newer (Ubuntu
24.04 or later, and equivalents). Check with `ldd --version`.

## Nodes

**A node does not join.** Run `oarbank-node status` on it (or look at **Oarbank Node**): it names the check that
failed and an error code, listed in [Adding machines](/oarbank/operate/adding-machines#when-joining-fails). The usual
ones: the code expired or was already used (`E_CODE_EXPIRED`, `E_CODE_USED`: make a new one), the port does not answer
(`E_TCP`: a firewall, or the coordinator is off; the node keeps trying until the code expires), or a TLS-inspecting
proxy is in the way (`E_TLS_PIN_MISMATCH`). A machine from a multi-use code, or one joined by the coordinator's address,
waits for approval: `oarbank fleet` lists it under PENDING, and `oarbank node approve <enrollment>` or
`oarbank node approve-code <CODE>` admits it.

**The coordinator is not found on the local network (macOS).** macOS 15 and later can block the coordinator's
announcement or the node's search under Local Network privacy. `oarbank-agent discover` says so. Allow the program in
System Settings, Privacy & Security, Local Network, or enroll with a join code, which names the coordinator's address and
needs no discovery. A Tailscale or VPN address is not "local network".

**A node is offline** (alert `node_offline`). Check the machine is awake and reaches the coordinator on port 7443/tcp,
then that its agent service `dev.codonic.oarbank.agent` runs (launchctl on macOS, systemctl on Linux, the service
manager on Windows). Its leases are already back in the queue.

**A node takes no work.** Run `oarbank explain node <node>`. Common answers: it is paused or draining
(`oarbank node state <node> active`), quarantined after wrong answers (investigate before clearing), it has no release
yet (`NO_RELEASE`: install and enable a module) or its release waits for your signature (`RELEASE_UNSIGNED`: run the
`oarbank release sign` command it names), it is still installing a release, its clock is off (`CLOCK_SKEW`: set the
time), or host protection is yielding to the person using it (`oarbank node mode`).

**A node does not apply a setting.** Its Settings tab, or `oarbank settings drift`, says why: pending until its next
heartbeat, offline, refused by the node (with the reason), a stricter value from the machine's managed policy, or an
agent older than 2.9 that does not report what it applied ([Settings](/oarbank/operate/settings#change-a-setting)).

**GPU jobs never go to a node** (`GPU_API_MISSING`). `oarbank node show <node>` lists the GPU APIs the node provides,
with evidence, and `oarbank-agent gpu-apis` on the node says why one is missing (on Linux:
`sudo -u oarbank /usr/lib/oarbank/oarbank-agent gpu-apis`). On Linux, add the `oarbank` account to
the groups that own the GPU device files (`sudo usermod -aG render,video oarbank`) and restart the agent. On a Mac
with Apple silicon, GPU containers need krunkit (see [Requirements](/oarbank/operate/requirements)).

**Containers do not run on a Mac.** `oarbank node show <node>` shows the runtime's state: `missing` lists each piece
with its fix (usually `brew install colima docker`, or Rosetta 2 on Apple silicon), `failed` shows the end of Colima's
log. Never start the `oarbank` Colima profile yourself; the agent does ([Containers on nodes](/oarbank/operate/containers#macos)).

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
| `MODULE_NOT_CERTIFIED` | The node has not passed the module's golden jobs yet; it does this on its own once the doctor passes. Stages that need no certification run before ([Running modules](/oarbank/operate/modules#which-stages-run-before-certification)) |
| `SECRETS_NOT_SET` | `oarbank secret set <module> <name>` |
| `SETTINGS_NOT_SET` | The module has a required setting with no value for the node: `oarbank settings set <key> <value> --module <module>` ([Settings](/oarbank/operate/settings#module-settings)) |
| `TOOL_NOT_FOUND`, `TOOL_VERSION_UNMET`, `TOOL_REFUSED` | A host tool the module asks for is missing, too old or refused on the node: install it, then `oarbank tools detect <node>` ([Host tools](/oarbank/operate/host-tools)) |
| `STAGE_CAPABILITY_MISSING` | The stage needs a capability the node does not provide, often because a doctor check named after it fails: `oarbank node show <node>` |
| `CAMPAIGN_SETTING_HOLDS` | The campaign's own settings hold it on this node (its `jobs`, `run_on_battery` or `user_present_slots`): `oarbank settings get --campaign <id>` |
| `AGENT_TOO_OLD` | Update the node's agent: `oarbank agent list`, then `canary` and `promote` |
| `OARBANK_PAUSED`, `CAMPAIGN_PAUSED` | `oarbank resume --all`, or `oarbank campaign resume <id>` |
| `RETRIES_EXHAUSTED` | Read the attempts in `oarbank job show <id>`, fix the cause, then `oarbank job retry <id>` |
| `SITE_UPLINK_BUSY`, `SITE_UPLINK_BUDGET` | A network-heavy job waits for its site: a protected program there uploads, or the site's budget is full ([Sites](/oarbank/operate/sites)) |
| `GPU_BLOCKED` | A GPU job waits beside a protected GPU program. From 2.10 a rule that protects `progress_rate` holds GPU work back too; to run GPU work beside it, measure a duty ([Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy)) |

**A job was requeued with `AGENT_STOPPED`.** The node's agent was stopped or restarted (an upgrade, a reboot, its
service manager). It stopped the job first, checkpointing where the module supports it; the job runs again and nothing
counts against it or the node.

**A job fails.** The attempt's end code says how (`EXIT_NONZERO`, `TIMEOUT`, `OOM`, `INVALID_SPEC`,
`MISSING_DEPENDENCY`…). `oarbank job show <id>` shows each attempt's output.

**Jobs keep failing on one node** (alert `breaker`). Read that node's attempts. It re-runs its doctor by itself; if it
keeps tripping, quarantine it.

**A secret cannot be read** (alert `secret_unreadable`). The coordinator's secrets key did not come with a restored
backup or a copied home. Set the secret again with `oarbank secret set`.

**A module is enabled but nothing runs.** `oarbank module ready <module>` prints its readiness checklist with the step
that blocks and its fix: an unsigned release, a required setting, a host tool, no node a stage can run on
([Running modules](/oarbank/operate/modules#the-readiness-checklist)).

**A module will not install.** `oarbank module install <bundle> --dry-run` shows why. A module whose `requires.core`
excludes your coordinator's version is refused: install a coordinator that meets it (see
[Versions and compatibility](/oarbank/operate/versions)). Module authors run `oarbank-sdk check` and
`oarbank-sdk conform` before bundling.

## Workloads, listeners and GPU co-tenancy

**A workload does not run.** `oarbank workload list --module <module>` shows its state and why on each node; the
`WORKLOAD_…` codes are in [Reason codes](/oarbank/reference/reason-codes#workloads). A withdrawn workload (alert
`workload_withdrawn`) needs its cause fixed and `oarbank workload restart <module>/<workload> --node <node>`
([Workloads](/oarbank/operate/workloads)).

**A listener is not reachable** (alert `listener_unreachable`). `oarbank listener show <node> <module>/<listener>`, or
the node page's **Network** card, names the result and its fix: a router without automatic mapping, two routers, a
provider's shared address, a firewall. Not checked yet means no node on another network could dial back
([Make a node reachable](/oarbank/operate/make-a-node-reachable)).

**GPU work waits under `gpu_jobs = "certified"`.** `oarbank protection duty <node>` says why: not certified for a rule
(`GPU_NOT_CERTIFIED`: run a sweep), the budget at 0 after a take-back, or a unit held for exceeding its duty or because
its GPU time cannot be read ([Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy)).

**Fleet work stays held under a rule with a source.** `PROTECTION_SOURCE_NEEDS_HELPER`: the source is in a home folder
and nobody is logged in to read it. `PROTECTION_SOURCE_UNREADABLE`: the agent cannot read the file. Either way
protection assumes the worst until it reads again.

## Asking for help

Open an issue on [roeehrl/oarbank](https://github.com/roeehrl/oarbank/issues) for the coordinator and nodes, or on
[roeehrl/oarbank-sdk](https://github.com/roeehrl/oarbank-sdk/issues) for the SDK and these docs. Include the versions
([how to find them](/oarbank/operate/versions#find-the-installed-versions)), the operating system, the command and its
output. Report security problems privately to support@codonic.dev, never in a public issue.
