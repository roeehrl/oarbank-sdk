---
title: Command-line reference
description: Every Oarbank command an operator or module author runs, what each one does, where it is installed on macOS, Linux and Windows, and the flags that confirm or preview a change.
type: reference
platforms: [macos, linux, windows]
core: '2.8.0'
sidebar:
  order: 1
---

Oarbank has six commands. Every one prints its full usage with `--help`, and `oarbank <command> --help` shows a
command's own arguments. This page lists them as of Oarbank 2.8.0 and module SDK 1.5.0.

| Command | Runs on | What it is |
|---|---|---|
| `oarbank` | the coordinator | The operator CLI: nodes, jobs, campaigns, modules, datasets, secrets, accounts, signing and moves |
| `oarbank-setup` | the coordinator | Opens the first-run setup wizard, or refreshes the services of an existing wizard-managed installation |
| `oarbankd` | the coordinator | The coordinator service itself; the installers run it, you normally do not |
| `oarbank-node` | each node | Joins the node to a fleet, checks a code, shows where the node is, and leaves |
| `oarbank-agent`, `oarbank-launcher`, `oarbank-uninstall` | each node | The node's agent, the launcher that installs, runs and rolls back agent builds (`oarbank-node` is the launcher under a second name), and the macOS node uninstaller |
| `oarbank-sdk` | a module author's computer | The module SDK's tools: validate, preview, bundle and test modules |

## Where the commands are

| | `oarbank` | `oarbank-setup` |
|---|---|---|
| macOS | `/Applications/Oarbank Coordinator.app/Contents/Resources/coordinator/bin/oarbank` | `…/Contents/Resources/coordinator/bin/oarbank-setup` |
| Linux | `/opt/oarbank/coordinator/bin/oarbank` | `/usr/bin/oarbank-setup` |
| Windows | `C:\Program Files\Oarbank\Coordinator\current\bin\oarbank.cmd` (an elevated prompt) | `C:\Program Files\Oarbank\Coordinator\package\oarbank-setup.ps1` (asks for elevation) |

The installers do not put `oarbank` on the `PATH`: call it by its full path, or add its directory yourself. The node
packages do put `oarbank-node` on it: `/usr/local/bin/oarbank-node` on macOS, `/usr/bin/oarbank-node` on Linux, and
`C:\Program Files\Oarbank\oarbank-node.exe` on Windows.

The CLI talks to the coordinator through its local owner channel, so it works on the coordinator for the account that
set it up (Windows: an elevated prompt). Anywhere else, sign in with `oarbank console login` or use a personal access
token from `oarbank token create`.

On a node, the programs are in `/Library/Oarbank/bin/` on macOS, in `/usr/lib/oarbank/` on Linux (with
`oarbank-launcher` linked into `/usr/bin`), and in `C:\Program Files\Oarbank` on Windows.

## Confirming and previewing changes

Commands that change the fleet share these flags:

- `--reason TEXT` records why in the audit log (or set `OARBANK_REASON`). Some operations require it.
- `--yes` (or `-y`) skips the interactive confirmation.
- `--confirm NAME` is the extra confirmation of the most dangerous operations: it must name the resource, for
  example the campaign or the coordinator move's target.
- `--dry-run` shows what would change and changes nothing. `oarbank op --dry-run` exits 2 when there are changes.
- `--json` (on `show` and `explain`) prints the document instead of the summary.

`oarbank ops` lists every operation with its tier and reason policy.

## `oarbank`

### Looking at the fleet

| Command | What it does |
|---|---|
| `oarbank fleet` | Every node: state, online, jobs and slots, protection, modules, GPU APIs, caps; pending enrollments; campaigns |
| `oarbank node show <node>` | One node: doctor, GPU APIs with evidence, containers, services, folders, sandbox, agent version |
| `oarbank job show <id>` | One job: attempts, checkpoint, results, and why it is where it is |
| `oarbank explain job <id>` | Why a job is pending, as the scheduler's own reason codes ([Reason codes](/oarbank/reference/reason-codes)) |
| `oarbank explain node <id>` | Why a node takes no work |
| `oarbank verify [--strict]` | Checks protocol invariants and fleet health; exits 1 on a violation (`--strict`: also on warnings) |
| `oarbank alerts list` | Open alerts; `ack`, `snooze`, `resolve <id>` with `--useful` or `--noise`; `precision` for the monthly review |
| `oarbank audit log` | The audit log (`--limit`, `--op`, `--target`); `audit verify [--against FILE]` checks its hash chain |
| `oarbank modules` | The installed modules with their versions and what they require |

### Nodes

| Command | What it does |
|---|---|
| `oarbank join-code [--label NAME] [--ttl SECONDS] [--uses N] [--approve\|--no-approve] [--system] [--containers]` | A join code for new machines: single use and approved at once by default (4 hours); `--uses N` lets N machines join, each waiting for approval unless `--approve` |
| `oarbank join-codes [--all]` | Outstanding join codes, their uses and the machines that used them |
| `oarbank join-code revoke <id>` | Revoke a code; machines that already joined with it stay |
| `oarbank node approve <enrollment>` / `reject` | Decide a pending enrollment (a machine from a multi-use code, or one joined by the coordinator's address) |
| `oarbank node approve-code <CODE>` | Approve the waiting machine that shows this 8-letter code |
| `oarbank node state <node> active\|paused\|draining` | Take work, stop taking work, or finish current work and then stop |
| `oarbank node mode <node> fleet_first\|moderate\|strict_yield` | How strongly host protection yields to the person using the machine |
| `oarbank node limits <node> --cpu-cores N --mem-gb N --jobs N …` | Caps on what the fleet may use (`off` removes one, `--clear-all` removes all; `--enforce soft\|hard`) |
| `oarbank node policy`, `confirm-identity` | A node's policy settings; confirm its identity after a key change |
| `oarbank protection show\|set\|preview\|restore\|canary\|promote\|probe <node> [rules-file]` | The node's protected-process rules |

### Work

| Command | What it does |
|---|---|
| `oarbank campaign list` / `show <id>` | Campaigns: every module's grouped work |
| `oarbank campaign pause\|resume\|cancel\|retry-failed <id>` | Control a campaign |
| `oarbank campaign weight\|priority <id> <value>` | Share of the fleet, and order |
| `oarbank campaign rebind <id> --platform <token>` / `placement <id> --mix same-os\|same-arch\|same-platform` | Where its work may run |
| `oarbank campaign download <id> [dir]` | Download its artifacts |
| `oarbank job retry\|cancel <id>` | One job |
| `oarbank pipeline single\|split --module <name>` | Run a module as one stage, or as its stage chain across nodes |
| `oarbank pause --all` / `halt --all` / `resume --all` | Stop handing out work / stop work now / start again, fleet-wide |

### Modules, data and secrets

| Command | What it does |
|---|---|
| `oarbank module install <bundle>` | Install a module bundle (`--dry-run` first) |
| `oarbank module list` / `show <name>` / `verify <name>` / `check <name> [--deep]` | Inspect installed modules |
| `oarbank module approve\|enable\|canary\|promote\|rollback\|disable\|pin\|unpin\|uninstall <name>[@<version>]` | The module lifecycle: canary on chosen nodes (`--node`), promote, roll back |
| `oarbank mod <module> <verb> [target] -p key=value` | Run one of a module's own operations |
| `oarbank cli <module> [args…]` | Run a module's own command line on the coordinator, sandboxed |
| `oarbank dataset upload <folder>` / `download <id> [dir]` / `list` / `show <id>` / `register <file.json>` | Datasets |
| `oarbank folders list` / `map <folder> --node <node>=<path>` / `sign <node>` | Folders modules may read or write on nodes |
| `oarbank secret set\|clear\|list <module> [name] [--node NODE]` | Module secrets, write-only: `set` reads the value from stdin or a no-echo prompt |

### Accounts, signing, updates and moves

| Command | What it does |
|---|---|
| `oarbank account list\|create\|disable\|enable\|role\|reset-totp\|password [name]` | Console accounts (`--role admin\|operator\|viewer`) |
| `oarbank token create\|revoke` | Personal access tokens |
| `oarbank console login [--open]` | A console sign-in link |
| `oarbank agent list\|upload\|sign\|canary\|promote\|rollback` | Node agent self-update; `list` shows the version every node runs |
| `oarbank release build\|keygen\|sign\|promote\|list` | Release bundles and the release signing key |
| `oarbank owner show\|set\|disable\|rescue-move` | The owner key set (signing mode) |
| `oarbank coordinator status\|prepare\|move\|sign\|cancel\|finalize` | Move the coordinator to another machine |
| `oarbank coordinator-build list\|upload\|sign`, `oarbank vendor-metadata upload <dir>` | Coordinator builds and update metadata for moves and agents |
| `oarbank op <operation> [target]` / `oarbank ops` | Any operation from the registry, and the registry itself |

## `oarbank-setup` and `oarbankd`

`oarbank-setup --root <dir> [--no-browser] [--port PORT]` opens the setup wizard on loopback. The coordinator
app runs it for you; run it yourself only to restart the services after an upgrade or relocation. It never creates a
second fleet over an existing one.

`oarbankd` is the coordinator service. Its flags (`--agent-bind`, `--agent-port`, `--standby --pair … --from …
--from-ca …` for a move target) are set by the installers and the move procedure.

## Node programs

| Command | What it does |
|---|---|
| `oarbank-node join` | Join this machine: prompts for the code (hidden), or `--code-stdin`, `--code-file PATH`; `--coordinator URL` joins by address with a device code. Checks first, then waits until joined (`--no-wait`, `--wait SECONDS`). macOS: `--scope system` (the default with `sudo`) or `personal`. Also `--name`, `--containers` (Windows), `--json`, `--force` |
| `oarbank-node check` | The same checks without joining: the code's format and expiry, DNS, the port, the coordinator's identity and certificate authority, the clock |
| `oarbank-node status [--json] [--follow]` | Where the node is: not joined, waiting for approval, connected, or why joining failed |
| `oarbank-node leave` | Forget the coordinator: the node's key, certificate, caches and logs go |
| `oarbank-node doctor [--json]` | Status, the checks against the node's coordinator, and the container runtime |
| `oarbank-launcher --version` | The launcher's version (the agent itself updates through the coordinator: see `oarbank agent list`) |
| `oarbank-agent discover` | Coordinators announcing themselves on the local network, and whether macOS Local Network privacy blocks it |
| `oarbank-agent gpu-apis` | The GPU APIs this node provides and why any is missing (Linux: `sudo -u oarbank /usr/lib/oarbank/oarbank-agent gpu-apis`) |
| `oarbank-agent containers install\|doctor [--probe]` | Windows: install or check the agent's own container runtime |
| `oarbank-uninstall [--purge]` | macOS: unload the node's service; `--purge` deletes its home; with `sudo` also removes the programs |
| `oarbank-launcher remove --scope system [--purge]` | Stop and remove the node's service (the Linux package's removal runs it); `--purge` also deletes the node's home |

## `oarbank-sdk`

| Command | What it does |
|---|---|
| `oarbank-sdk check <module-dir>` | Validate a module's manifests strictly |
| `oarbank-sdk conform <module-dir>` | Run the conformance kit: the module's runner as each platform would run it. It must end with `0 failed` |
| `oarbank-sdk preview <module-dir>` | Render the module's console pages against fixtures |
| `oarbank-sdk bundle build\|verify\|wheels` | Build or verify a module bundle; `wheels` downloads the pinned wheels for every declared platform |
| `oarbank-sdk deps` | A module's Python dependencies |
| `oarbank-sdk gpu-apis` | The GPU APIs this computer provides, as a node's agent would detect them (JSON) |
| `oarbank-sdk export-schemas` | Regenerate `schemas/` from the models (SDK development) |

[Build a module in a day](/oarbank/build/tutorial) uses each of them in order.
