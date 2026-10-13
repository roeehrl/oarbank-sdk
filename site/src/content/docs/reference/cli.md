---
title: Command-line reference
description: Every Oarbank command an operator or module author runs, what each one does, where it is installed on macOS, Linux and Windows, and the flags that confirm or preview a change.
type: reference
platforms: [macos, linux, windows]
core: '2.10.2'
sidebar:
  order: 1
---

Oarbank has six commands. Every one prints its full usage with `--help`, and `oarbank <command> --help` shows a
command's own arguments. This page lists them as of Oarbank 2.10.2 and module SDK 1.7.0.

| Command | Runs on | What it is |
|---|---|---|
| `oarbank` | the coordinator | The operator CLI: nodes, settings, groups, sites, protection, jobs, campaigns, modules, workloads, inbound listeners, host tools, datasets, secrets, accounts, signing and moves |
| `oarbank-setup` | the coordinator | Opens the first-run setup wizard, or refreshes the services of an existing wizard-managed installation |
| `oarbankd` | the coordinator | The coordinator service itself; the installers run it, you normally do not |
| `oarbank-node` | each node | Joins the node to a fleet, checks a code, shows where the node is, and leaves |
| `oarbank-agent`, `oarbank-launcher`, `oarbank-uninstall` | each node | The node's agent, the launcher that installs, runs and rolls back agent builds (`oarbank-node` is the launcher under a second name), and the macOS node uninstaller |
| `oarbank-sdk` | a module author's computer | The module SDK's tools: validate, preview, bundle and test modules |

## Where the commands are

| | `oarbank` | `oarbank-setup` |
|---|---|---|
| macOS | `/usr/local/bin/oarbank`, a link to `/Applications/Oarbank Coordinator.app/Contents/Resources/coordinator/bin/oarbank` | `/usr/local/bin/oarbank-setup` |
| Linux | `/usr/bin/oarbank`, a link to `/opt/oarbank/coordinator/bin/oarbank` | `/usr/bin/oarbank-setup` |
| Windows | `oarbank` from `C:\Program Files\Oarbank\Coordinator\package\cli` on the system PATH, which runs `C:\Program Files\Oarbank\Coordinator\current\bin\oarbank.cmd` (an elevated prompt) | `C:\Program Files\Oarbank\Coordinator\package\oarbank-setup.ps1` (asks for elevation) |

From 2.9 the coordinator packages put `oarbank` on the `PATH` (open a new terminal after installing); the full paths
work the same. The node packages put `oarbank-node` on it: `/usr/local/bin/oarbank-node` on macOS,
`/usr/bin/oarbank-node` on Linux, and `C:\Program Files\Oarbank\oarbank-node.exe` on Windows.

The CLI talks to the coordinator through its local admin channel, which admits the members of the coordinator's
owners' group: `_oarbankadmin` on macOS, `oarbank-admin` on Linux, administrators in an elevated prompt on Windows
([The coordinator service](/oarbank/operate/coordinator#who-may-use-the-cli)). Anywhere else, sign in with
`oarbank console login` or use a personal access token from `oarbank token create`.

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
| `oarbank node show <node>` | One node: doctor, GPU APIs with evidence, containers, services, folders, host tools, sandbox, the settings it sets itself, agent version |
| `oarbank job show <id>` | One job: attempts, checkpoint, results, and why it is where it is |
| `oarbank explain job <id>` | Why a job is pending, as the scheduler's own reason codes ([Reason codes](/oarbank/reference/reason-codes)) |
| `oarbank explain node <id>` | Why a node takes no work |
| `oarbank verify [--strict]` | Checks protocol invariants and fleet health; exits 1 on a violation (`--strict`: also on warnings) |
| `oarbank alerts list` | Open alerts; `ack`, `snooze`, `resolve <id>` with `--useful` or `--noise`; `precision` for the monthly review |
| `oarbank audit log` | The audit log (`--limit`, `--op`, `--target`); `audit verify [--against FILE]` checks its hash chain |
| `oarbank modules` | The installed modules with their versions and what they require |
| `oarbank module ready [<name>]` | A module's readiness checklist, every step with its status, reason and fix (every module without a name) |

### Nodes

| Command | What it does |
|---|---|
| `oarbank join-code [--label NAME] [--ttl SECONDS] [--uses N] [--approve\|--no-approve] [--system] [--containers]` | A join code for new machines: single use and approved at once by default (4 hours); `--uses N` lets N machines join, each waiting for approval unless `--approve` |
| `oarbank join-codes [--all]` | Outstanding join codes, their uses and the machines that used them |
| `oarbank join-code revoke <id>` | Revoke a code; machines that already joined with it stay |
| `oarbank node approve <enrollment>` / `reject` | Decide a pending enrollment (a machine from a multi-use code, or one joined by the coordinator's address) |
| `oarbank node approve-code <CODE>` | Approve the waiting machine that shows this 8-letter code |
| `oarbank node state <node> active\|paused\|draining` | Take work, stop taking work, or finish current work and then stop |
| `oarbank node mode <node> fleet_first\|moderate\|strict_yield` | The node's own protection mode (how strongly it yields to the person using it) |
| `oarbank node label <node> <a,b> [--remove]` | Add or remove owner labels |
| `oarbank node sign <node>` | Sign the node's statement (folders and tool paths it did not find itself), in signing mode |
| `oarbank node confirm-identity <node>` | Confirm a node's identity after a key change |
| `oarbank protection show <node>` | The node's effective protection rules and where each comes from |
| `oarbank protection set\|preview <node\|fleet\|group:G> <file>` | Replace (or preview replacing) one scope's own protection section, from a JSON or TOML file |
| `oarbank protection probe <node>` | Run a protection probe now |
| `oarbank protection ab start <node> --rule <id> [--work gpu\|cpu\|all\|<module>\|<module>/<workload>] [--duty D] [--blocks N] [--window S] [--washout S] [--margin M] [--off paused\|evicted] [--max-hours H]` | Measure what fleet work costs a protected program with the paired on/off harness (2.10, [Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy)) |
| `oarbank protection ab sweep <node> --rule <id> [--levels 0.1,0.2,…]` | Run the harness at rising `gpu_duty` levels; the highest passing level is the node's certified duty |
| `oarbank protection ab status [<node>]`, `oarbank protection ab show\|abort <run>` | Harness runs and their reports; abort one |
| `oarbank protection certificates [<node>]` | Co-tenancy certificates: valid, expired or stale, and why |
| `oarbank protection duty [<node>]` | The GPU duty governor: budget against the certified ceiling, baselines, each unit's asked and measured duty, recent steps |

Per-node caps and policy are settings from 2.9: `oarbank node limits` and `oarbank node policy` are gone (below).

### Settings, groups and labels

[Settings](/oarbank/operate/settings) explains the model. Without a scope flag, `set` and `reset` act on the fleet;
both preview the per-node impact, then apply.

| Command | What it does |
|---|---|
| `oarbank settings get [<key>] [--node N] [--module M] [--campaign C] [--json]` | Effective values with their source; one key's chain with `--explain` |
| `oarbank settings explain <key> [--node N] [--module M] [--campaign C]` | The whole chain for one key |
| `oarbank settings set <key> <value> [--node N \| --group G \| --nodes A,B \| --label L \| --campaign C] [--module M] [--enforce] [--comment TEXT] [--dry-run]` | Set a value; `--enforce` locks it at the fleet or a group |
| `oarbank settings reset <key> [the same scope flags] [--module M]` | Remove a value, so the scope inherits again |
| `oarbank settings overrides <key> [--module M]` | Who overrides the fleet value, with what |
| `oarbank settings promote <key> --group G [--to <group>]` | Move a group's value (a canary) to the fleet or a wider group |
| `oarbank settings schema` | Every key, every installed module's keys included |
| `oarbank settings set-secret\|clear-secret ntfy_token` | The write-only ntfy token (asked for hidden, twice) |
| `oarbank settings export [--scope fleet\|group:G\|node:N] [--module M] [--out FILE]` | Settings as YAML |
| `oarbank settings import FILE [--dry-run]` | Apply a file's differences as one change (`--dry-run` exits 0 with no changes, 2 with changes) |
| `oarbank settings shadowed [--yes]` | Values that change nothing; `--yes` resets them |
| `oarbank settings drift` | Nodes not running their latest settings |
| `oarbank groups list\|show [G]` | Groups by rank, with members and why |
| `oarbank groups create <name> [--os --arch --label --hostname --battery\|--no-battery --ram-min --ram-max --cores-min --selector JSON --member …]` | A node group |
| `oarbank groups update <G> [selector flags] [--name --description --no-selector --no-members]` | Change a group |
| `oarbank groups rank <G> up\|down\|top\|bottom`, `oarbank groups delete <G>` | Reorder or delete a group (deleting deletes its values) |
| `oarbank sites list\|show [S]\|suggest` | Sites (nodes sharing one internet line) with their budget and state, and suggested sites (2.10, [Sites](/oarbank/operate/sites)) |
| `oarbank sites create <name> --node N … [--transfers N --heavy-jobs N --mbps M]`, `oarbank sites update <S> […]` | A site and its uplink budget ([Sites](/oarbank/operate/sites)) |
| `oarbank sites add <S> <node>…`, `oarbank sites remove <node>…`, `oarbank sites delete <S>` | Members of a site |
| `oarbank sites accept <key> [--name N \| --site S]`, `oarbank sites dismiss <key>` | Take or drop a suggestion |

### Inbound listeners

From 2.10 ([Make a node reachable](/oarbank/operate/make-a-node-reachable)). A listener is `<module>/<listener>`.

| Command | What it does |
|---|---|
| `oarbank listener list [<node>]` | Each node's listeners: state, where reachable, mapping, result, connections |
| `oarbank listener show <node> <module>/<listener>` | One listener: its result and fix, the owner's entry, router mappings and top client addresses |
| `oarbank listener mappings [<node>]` | The router mappings the agents hold, and other mappings the routers report (never touched) |
| `oarbank listener set <node> <module>/<listener> [--external-port P] [--fallback refuse\|next_free:A-B\|router_choice] [--mapping auto\|pcp\|natpmp\|upnp\|manual\|none] [--internal-port P] [--bind ADDR] [--ipv6 auto\|off] [--limit KEY=VALUE …] [--allow CIDR …]` | Assign a listener to a node, or change its entry (sign the statement in signing mode) |
| `oarbank listener remove <node> <module>/<listener>` | Take the entry away: the listener closes and its mapping goes |
| `oarbank listener probe <node> <module>/<listener>` | Check it from outside now (the Network card's **Check now**) |
| `oarbank listener disable\|resume --node N \| --all` | The kill switch: close every listener and remove every mapping on a node or the fleet, and undo it |

### Host tools

| Command | What it does |
|---|---|
| `oarbank tools [--node N] [--module M] [--json]` | Tool definitions, what each node found, and each module's resolution |
| `oarbank tools detect <node> [--wait [S]]` | Ask a node to detect again; `--wait` prints what it found |
| `oarbank tools define <id> --search <scope>=<pattern> [--kind jdk\|python\|executable --version-args … --version-regex …]` | Define a tool, or add search patterns (scope `fleet`, `darwin`, `linux`, `windows`) |
| `oarbank tools delete <id>` | Delete a definition (a built-in loses only your extra patterns) |
| `oarbank settings set\|reset tool.<id>.path <path> --node <node> [--module M]` | Choose or add the installation a node grants ([Host tools](/oarbank/operate/host-tools)) |

### Work

| Command | What it does |
|---|---|
| `oarbank campaign list` / `show <id>` | Campaigns: every module's grouped work |
| `oarbank campaign pause\|resume\|cancel\|retry-failed <id>` | Control a campaign |
| `oarbank campaign weight\|priority <id> <value>` | Share of the fleet, and order |
| `oarbank campaign rebind <id> --platform <token>` / `placement <id> --mix same-os\|same-arch\|same-platform` | Where its work may run |
| `oarbank campaign download <id> [dir]` | Download its artifacts |
| `oarbank job retry\|cancel <id>` | One job |
| `oarbank workload list [--node N] [--module M]` | Workloads and their state on each node: seat, lease, held or paused, last error (2.10, [Workloads](/oarbank/operate/workloads)) |
| `oarbank workload assign <module>/<workload> --node N \| --count C [--dry-run]` | Assign a workload to a node, or place C copies of a `seat = "fleet"` workload |
| `oarbank workload unassign\|restart <module>/<workload> --node N` | Stop it on a node for good, or restart it there (also lifts a withdrawal) |
| `oarbank workload logs <module>/<workload> --node N [--follow]` | Its log tail |
| `oarbank pause --all` / `halt --all` / `resume --all` | Stop handing out work / stop work now / start again, fleet-wide |

### Modules, data and secrets

| Command | What it does |
|---|---|
| `oarbank module install <bundle>` | Install a module bundle (`--dry-run` first) |
| `oarbank module list` / `show <name>` / `verify <name>` / `check <name> [--deep]` | Inspect installed modules |
| `oarbank module approve\|enable\|canary\|promote\|rollback\|disable\|pin\|unpin\|uninstall <name>[@<version>]` | The module lifecycle: canary on chosen nodes (`--node`) or a group's members (`--group`), promote, roll back |
| `oarbank mod <module> <verb> [target] -p key=value` | Run one of a module's own operations |
| `oarbank cli <module> [args…]` | Run a module's own command line on the coordinator, sandboxed |
| `oarbank dataset upload <folder>` / `download <id> [dir]` / `list` / `show <id>` / `register <file.json>` | Datasets |
| `oarbank folders list` / `map <folder> --node <node>=<path>` / `sign <node>` | Folders modules may read or write on nodes |
| `oarbank secret set\|clear\|list <module> [name] [--node NODE \| --group GROUP]` | Module secrets, write-only: `set` reads the value from stdin or a no-echo prompt |

### Accounts, signing, updates and moves

| Command | What it does |
|---|---|
| `oarbank account list\|create\|disable\|enable\|role\|reset-totp\|password [name]` | Console accounts (`--role admin\|operator\|viewer`) |
| `oarbank token create\|revoke` | Personal access tokens |
| `oarbank console login [--open]` | A console sign-in link |
| `oarbank agent list\|upload\|sign\|canary\|promote\|rollback` | Node agent self-update; `list` shows the version every node runs |
| `oarbank update status\|releases\|import\|fetch\|sign\|plan\|start\|pause\|resume\|halt\|rollback\|skip\|hold\|unhold\|pin\|unpin\|coordinator` | Fleet updates: whole node packages and the coordinator, ring by ring ([Update the fleet](/oarbank/operate/update-and-remove#update-the-fleet)) |
| `oarbank release build\|keygen\|sign\|promote\|list` | Release bundles and the release signing key |
| `oarbank owner show\|set\|disable\|rescue-move` | The owner key set (signing mode) |
| `oarbank coordinator status\|prepare\|move\|sign\|cancel\|finalize` | How the coordinator runs (`status`), and moving it to another machine |
| `oarbank coordinator migrate [--status]` | Move a 2.8 per-user coordinator to the system service (macOS, Linux); `--status` shows what is installed and the migration record |
| `oarbank coordinator-build list\|upload\|sign`, `oarbank vendor-metadata upload <dir>` | Coordinator builds and update metadata for moves and agents |
| `oarbank op <operation> [target]` / `oarbank ops` | Any operation from the registry, and the registry itself |

### Removed in 2.9

| 2.8 | 2.9 |
|---|---|
| `oarbank node policy`, `oarbank node limits` | `oarbank settings get\|set\|reset … --node <node>` |
| `oarbank pipeline single\|split --module <name>` | `oarbank settings set pipeline split --module <name>` |
| `oarbank protection restore\|canary\|promote` | a canary group, then `oarbank settings promote protection.rules --group <group>` |

## `oarbank-setup` and `oarbankd`

`oarbank-setup --root <dir> [--no-browser] [--port PORT]` opens the setup wizard on loopback. The coordinator
app runs it for you; run it yourself only to start the services again after they stopped. It asks for an
administrator and never creates a second fleet over an existing one.

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
| `oarbank-agent policy` | The managed policy in force, as JSON; a join code shows as `(set)` |
| `oarbank-agent tools detect` | Detect the node's host tools now and print what was found (run as the agent's account) |
| `oarbank-launcher --version` | The launcher's version (the agent itself updates through the coordinator: see `oarbank agent list`) |
| `oarbank-launcher update-run`, `update-trust` | The node's root update runner, started by its launchd job, systemd path unit or the Windows helper; `sudo oarbank-launcher update-trust --from-agent` pins update trust on a node that joined after its package was installed |
| `oarbank-agent discover` | Coordinators announcing themselves on the local network, and whether macOS Local Network privacy blocks it |
| `oarbank-agent gpu-apis` | The GPU APIs this node provides and why any is missing (Linux: `sudo -u oarbank /usr/lib/oarbank/oarbank-agent gpu-apis`) |
| `oarbank-agent containers doctor [--probe] [--gpu]` | The node's container runtime: its state, each missing piece with its fix (exit 3 while something is missing); `--probe` runs real containers. On macOS run it as the agent's account ([Containers on nodes](/oarbank/operate/containers)) |
| `oarbank-agent containers install` | Windows: install the WSL container prerequisites (administrator) |
| `oarbank-agent portmap status` | The router mappings this node's agent requested, from its journal, as JSON (2.10) |
| `oarbank-agent portmap release-all` | Delete every mapping the agent made (only its own); node removal runs it. Exit 3 when a router could not be asked |
| `oarbank-agent firewall status` | Whether the host firewall lets connections reach the agent, as JSON (2.10) |
| `oarbank-agent firewall allow [--port P …]` | Let connections reach the agent (administrator): macOS lists the agent in the Application Firewall; Linux opens the given ports for it |
| `oarbank-agent probe-endpoint [--bind ADDR:PORT]` | Serve dial-back checks for the fleet's listeners from a machine on another network (default `127.0.0.1:7480`; put it behind HTTPS and name it in `listener_probe_endpoint`) |
| `oarbank-uninstall [--purge]` | macOS: unload the node's service; `--purge` deletes its home; with `sudo` also removes the programs |
| `oarbank-launcher service refresh [--system]` | Render the agent's installed launchd job or systemd unit again with this launcher's settings, keeping its program, account and environment, and reload it. Node package upgrades run it for you |
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
