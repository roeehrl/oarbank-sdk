---
title: Settings
description: How Oarbank settings work for owners from 2.9 on, with fleet, group and node scopes, node groups and labels, locks, bulk changes, canary then promote, campaign overrides, settings as code, reports and managed policy that only tightens.
type: how-to
platforms: [macos, linux, windows]
core: '2.10.3'
sidebar:
  order: 5
---

Every owner choice in Oarbank, from how much memory a node keeps for its user to whether a module runs on laptops, is
a **setting**. There is one list of settings, one store of the values you set, and one rule that says, for every node,
which value is in effect and where it comes from. The console and the `oarbank settings` command show the same thing.

This page covers Oarbank 2.10.3. In 2.8 and earlier, policy and caps were stored per node (`oarbank node policy`,
`oarbank node limits`); 2.9 converts them once, on upgrade, and those commands are gone
([Upgrading](/oarbank/operate/upgrading#settings-after-the-upgrade)).

## Scopes

A value can be set at three scopes. A value you do not set is inherited from the scope above it.

| Scope | Set where | Reaches |
|---|---|---|
| **Fleet** | **Settings → Node defaults** in the console; `oarbank settings set <key> <value>` | every node |
| **Group** | the group's page under **Groups**; `--group <group>` | the group's members |
| **Node** | the node's **Settings** tab; `--node <node>` | that node only |

On top of those, two layers apply only in special cases: a **campaign** may override a few keys for its own jobs while
it runs ([Campaign overrides](#campaign-overrides)), and a machine's **managed policy** may make settings stricter on
that machine ([Managed on this machine](#managed-on-this-machine)).

**How a node's value is found.** Oarbank starts from the key's default (a fixed one, or one computed from the
machine: the memory kept for the system depends on how much RAM the machine has, and says so), then applies the
fleet's value, each group the node belongs to (lowest rank first, so the highest rank wins), then the node's own value.
For most keys the most specific value wins. Caps work differently: every scope's cap applies and the lowest one binds,
so a group or node can only tighten a fleet cap (`enforce` takes the strictest: hard over soft). Protection rules
add up from every scope ([Host protection](/oarbank/operate/host-protection)).

**Reset means inherit.** A value exists only where you set one. **Reset to inherited** (or `oarbank settings reset`)
deletes it, so a later fleet change reaches that node again. A value equal to today's default is still your choice,
and stays.

**Every save is one change.** A save gets one revision number and one audit entry with who, when, each value before
and after, and the reason. A change that reaches more than one node shows its preview first: which nodes' value
changes (old and new), which keep theirs and why, then a button that says what it does ("Save for 4 nodes").

## Look at the settings

On a node's **Settings** tab, every row shows the value in effect, where it comes from (`Default · 24 GB RAM`, `Fleet`,
`Group: Laptops`, `This node`, `On this machine (managed)`), whether the node has applied it, and an **Explain**
disclosure with the whole chain. **Override** sets a value for that node; **Reset to inherited** removes it.

From a terminal:

```sh
oarbank settings get --node build-07                 # every key in effect on the node, with its source
oarbank settings explain run_on_battery --node build-07   # the whole chain: default, fleet, groups, node
oarbank settings overrides user_present_slots        # who overrides the fleet value, and with what
oarbank settings schema                              # every key: type, unit, scopes, default, help
```

`oarbank node show <node>` lists what the node sets itself, each with the command that resets it.

### The keys

| Section | Keys |
|---|---|
| Memory | `os_reserve_gb` (memory kept for the system; computed from RAM), `user_reserve_gb` (kept for the person using it), `job_mem_gb` (memory per job slot), `mem_in_use_bound` (fit jobs into the memory free now) |
| When someone is using it | `user_present_slots`, `user_idle_s`, `screen_sharing_present`, `run_on_battery` |
| Jobs | `threads_per_job`, `max_slots`, `workloads` (assigned [workloads](/oarbank/operate/workloads) may run; on by default); advanced: `nice` (not applied by agents yet), `hard_limits` |
| Caps (off by default) | `cpu_cores`, `mem_gb`, `jobs`, `schedule`, `enforce` (`soft` or `hard`); advanced: `vm_mem_gb`, `vm_cpus`, `disk_gb`, `staging_mbps` (the rate of dataset staging and result and checkpoint uploads, enforced from 2.10) |
| Protection | `protection.mode`, `protection.rules`, `protection.node` ([Host protection](/oarbank/operate/host-protection)), `delegated_processes` (off by default: [Delegated processes](/oarbank/operate/delegated-processes)) |
| Network | `inbound_listeners` (assigned listeners may open; on by default); advanced: `listener_port_range` (the internal ports listeners bind, default `41000-41999`). Fleet only: `listener_probe_endpoint` (an `https://` probe endpoint you run elsewhere), `listener_stun_server` (`host:port`; none by default) ([Make a node reachable](/oarbank/operate/make-a-node-reachable)) |
| Host tools | `tool.<id>.path` ([Host tools](/oarbank/operate/host-tools)) |
| Fleet only | `ntfy.url`, `ntfy.click_base` (notifications), `console_listen` (where the console listens: `localhost`, `tailscale`, IP addresses, `all`), `console_https` (`off` or `tailscale`: https on the machine's MagicDNS name, for passkeys), `console_hosts` (extra console host names) ([Open the console from another device](/oarbank/operate/coordinator#open-the-console-from-another-device)), `replica_rate` (0 to 1) |
| Every module | `enabled`, `services.disabled`, `pipeline`, `replica_rate`, set with `--module <module>` ([Module settings](#module-settings)) |

Each node card says in one line how much work the node takes and why, for example "2 slots while someone is using
this Mac (14 when idle) · 9.2 GB free for jobs (apps and the system use 46 GB)". A slot is a performance core, or two
efficiency cores; hardware threads never count. Jobs get only memory the machine has free now, less a margin that
keeps the memory guard from stepping in, and never more than the memory kept for the system and for the person using
it allows. The settings behind that line are on the node's Settings tab.

The ntfy token is not a setting: it is a write-only secret, set with `oarbank settings set-secret ntfy_token` (it
asks twice, hidden) and removed with `clear-secret ntfy_token`.

## Change a setting

```sh
oarbank settings set user_present_slots 1                     # the fleet
oarbank settings set user_present_slots 3 --node build-07     # one node
oarbank settings set run_on_battery false --group Laptops     # a group
oarbank settings reset user_present_slots --node build-07     # back to inherited
oarbank settings set jobs 2 --node build-07 --dry-run         # what it would change, nothing saved
```

A value is JSON (`true`, `2`, `1.5`, `null`, `["a"]`) or plain text; a list setting also takes `a,b`. `set` and
`reset` always show the per-node impact first, then apply (`--yes` skips the question). Without a scope they act on
the fleet. `--comment TEXT` is kept with the value; `--reason TEXT` goes to the audit log.

A save is refused, with every problem at once, when a key cannot be set at that scope, a value has the wrong type or
range, a lock holds above it, or the merged values would not work on a node it reaches (the system reserve must leave
memory for jobs, a job slot must fit in RAM, a node's own cap may not exceed its hardware). A save is refused only for
problems it introduces, so an existing problem never blocks an unrelated change.

**When nodes get it.** The coordinator sends each node its complete settings with the next heartbeat. Each row shows
"Applied on the node · rev 41", "Pending: the node applies rev 42 at its next heartbeat", "Pending: node offline" or
"Refused by the node: …". A node agent older than 2.9 shows "Pending: this agent does not report what it applied
(update it)".

## Groups and labels

A **group** is a set of nodes chosen by a rule (a selector) over the nodes' facts and labels, plus any nodes you list by
name. Every group has a unique **rank**: where groups disagree about a key, the highest rank wins.

The built-in groups are **macOS**, **Linux**, **Windows** and **Coordinator host** (the coordinator's own machine, which
runs every module service). They have the lowest ranks and fixed membership, and you can set values on them. Your own
groups rank above them; a new group goes on top.

| Selector term | A node matches when |
|---|---|
| `--os darwin\|linux\|windows` | its OS is this one (repeat for any of several) |
| `--arch arm64\|amd64` | its architecture is this one |
| `--label <label>` | it carries the label (repeat: every one) |
| `--hostname <glob>` | its name matches, ignoring case (`mac-*`) |
| `--battery` / `--no-battery` | it has a system battery (a laptop), or not |
| `--ram-min`, `--ram-max`, `--cores-min` | its RAM is at least / at most, its cores at least |

```sh
oarbank groups create Laptops --battery
oarbank groups create "Render boxes" --label render --ram-min 64
oarbank groups create Lab --member build-07 --member build-08
oarbank groups list                       # by rank, with members and labels
oarbank groups show Laptops               # its members and why each is in
oarbank groups rank Laptops top           # up, down, top or bottom
oarbank groups update Laptops --description "Every laptop"
oarbank groups delete Lab                 # deletes its values and its secrets' values too
```

Membership is worked out whenever values are resolved, so a node whose facts change moves at once and nothing keeps a
stale copy. The console's new-group form shows a live preview: each node, and why it is in or not ("Laptops because has
a battery").

**Labels** are short owner words on a node (1 to 63 of `a-z 0-9 _ . : -`): `oarbank node label build-07 render,gpu`,
or `--remove`. Oarbank also adds labels from facts, such as `laptop` for a node with a battery; you can match them but
not remove them. Facts come from the node itself, so a group that locks a safety value should select on your own
labels, listed members or the OS. Removing a label that takes a node out of a group with a lock needs the admin role,
so a label is never a way around a lock.

## Locks

A **lock** is a fleet or group value that lower scopes may not change: `--enforce` on `set`, or the lock toggle beside
a fleet or group value in the console. Adding a lock is a high-tier change that asks you to type a confirmation.

```sh
oarbank settings set run_on_battery false --group Laptops --enforce
```

While the lock holds, it wins outright. Values set below it are kept but ignored (and apply again when the lock goes);
the preview of a new lock lists them. A node or lower group that tries to set the key is refused ("locked by the group
Laptops: change it there"); a node can still reset its own value. The fleet's lock comes before any group's, and a
higher-ranked group's before a lower one's. Locks hold for campaign overrides too.

## Bulk changes

One key set or reset on many nodes is one change with one revision and one preview listing every node's old and new
value:

```sh
oarbank settings set jobs 2 --nodes build-07,build-08,build-09
oarbank settings reset jobs --label render
```

The console's **Bulk changes** page (filtered by group or label) does the same, and adds or removes labels. Resetting
to inherited is the safer default it offers. A change to more than ten nodes' own values is one tier higher.

## Canary to a group, then promote

To try a change on a few nodes first, set it on a small group, watch those nodes, then promote it:

```sh
oarbank groups create Canary --member build-07
oarbank settings set user_idle_s 600 --group Canary
oarbank settings promote user_idle_s --group Canary          # to the fleet; --to <group> for a wider group
```

Promotion is one change: the value is set at the fleet (or the wider group) and deleted from the canary group, so the
canary nodes see no change and the rest get it. Protection rules join the target's rules instead of replacing them, and
a lock moves with its value. A group page has **Promote to fleet…** on each value set there. A module version's canary
can target a group too: `oarbank module canary <module>@<version> --group Canary`.

## Campaign overrides

A campaign may override a few settings for its own jobs while it runs or is paused:

- the core keys `replica_rate` (it can only raise verification), `jobs`, `run_on_battery` and `user_present_slots`;
- a module's own keys that its manifest marks overridable by campaigns.

Safety keys only tighten: a campaign value looser than the fleet's is refused ("a campaign may only tighten Run jobs on
battery (off is stricter)"), and a node whose own value is already stricter keeps it. A lock ignores the campaign's
value. A job held back by its campaign's settings waits with `CAMPAIGN_SETTING_HOLDS`.

```sh
oarbank settings get --campaign <campaign>
oarbank settings set replica_rate 0.2 --campaign <campaign>
oarbank settings reset jobs --campaign <campaign>
```

The campaign's page has a **While this campaign runs** section with the same rows. Once the campaign is done or
cancelled its values are kept and shown as inactive, but apply to nothing. A campaign's priority, weight and placement
stay campaign properties (`oarbank campaign priority|weight|placement`), not settings.

## Module settings

Every module has the same four core keys, set with `--module`:

| Key | Scopes | What it does |
|---|---|---|
| `enabled` | fleet (lockable), group, node; on by default | Off for the fleet is the module's kill switch (`oarbank module disable` writes it): nothing is dispatched, running attempts are released and its services stop. Off for a group or node does the same there only |
| `services.disabled` | fleet, group, node | The module's services a node does not run. A change re-doctors and re-certifies that module on the nodes whose value changed |
| `pipeline` | fleet | `single` or `split` (a module whose stages form a chain); switching to `split` splits its queued jobs. Replaces `oarbank pipeline` |
| `replica_rate` | fleet | The module's own replica rate; it can only raise the fleet's |

A module can also declare its **own** settings (a VM size, a threshold). The module's **Settings** tab shows them under
**Its settings**, with the readiness checklist on top; the node's Settings tab ends with a section per module. A
setting the module marks **required** has no default: until it has a value for a node, the module's work there waits
with `SETTINGS_NOT_SET`, and its checklist step **Required settings** says which nodes miss what.

```sh
oarbank settings get --module reel                        # all of the module's keys and where they come from
oarbank settings set vm_mem_gb 12 --module reel --node build-07
oarbank settings set enabled false --module reel --group Laptops
oarbank settings set pipeline split --module reel
```

When a new module version stops declaring a key, its values are kept but unused, listed under **Values the module no
longer declares** with a Delete each; a value the new version's schema refuses is dropped and named in the module's
event. Module authors: [Declare module settings](/oarbank/build/module-settings).

Module **secrets** resolve the same way: a node's own value, else the highest-ranked group's, else the module's
(`oarbank secret set <module> <name> --group <group>`). They stay write-only.

## Managed on this machine

An organization can make settings **stricter** on a machine through its managed policy (MDM, Group Policy or a file),
and the machine's agent enforces it. A managed value applies only where it is stricter than what the coordinator
sends; a looser one has no effect. So an organization can guarantee, for example, that a laptop never runs jobs on
battery, while the owner can still tighten caps from the console.

| Key | Type | Stricter |
|---|---|---|
| `run_on_battery`, `inbound_listeners`, `workloads`, `delegated_processes` | boolean | off |
| `screen_sharing_present`, `mem_in_use_bound`, `hard_limits` | boolean | on |
| `user_present_slots`, `max_slots`, `jobs`, `vm_cpus` | integer | lower |
| `cpu_cores`, `mem_gb`, `vm_mem_gb`, `disk_gb`, `staging_mbps` | number | lower |
| `os_reserve_gb`, `user_reserve_gb`, `user_idle_s` | number | higher |
| `enforce` | `soft` or `hard` | hard |

Any other key (`job_mem_gb`, a schedule, protection) is refused and reported; protection has its own local file on the
node ([Host protection](/oarbank/operate/host-protection#the-nodes-own-rules)).

| | Where |
|---|---|
| macOS | a configuration profile for the preference domain `dev.codonic.oarbank.agent`, with a `Settings` dictionary keyed by the setting's key |
| Windows | values under `HKLM\SOFTWARE\Policies\Codonic\Oarbank\Agent\Settings` (`REG_DWORD`, or `REG_SZ` for a decimal number); the release's ADMX template has one policy per key under **Codonic › Oarbank › Node › Node settings (tighten-only)** |
| Linux | a `"Settings"` object in `/etc/oarbank/policy.json` |

A macOS profile payload:

```xml
<dict>
  <key>PayloadType</key><string>dev.codonic.oarbank.agent</string>
  <key>ManagedByOrganizationName</key><string>Example Org</string>
  <key>Settings</key>
  <dict>
    <key>run_on_battery</key><false/>
    <key>jobs</key><integer>2</integer>
    <key>os_reserve_gb</key><real>8</real>
  </dict>
</dict>
```

On Windows, from an elevated prompt or through Group Policy:

```powershell
reg add HKLM\SOFTWARE\Policies\Codonic\Oarbank\Agent\Settings /v run_on_battery /t REG_DWORD /d 0
reg add HKLM\SOFTWARE\Policies\Codonic\Oarbank\Agent\Settings /v os_reserve_gb /t REG_SZ /d 8.5
```

On Linux, `/etc/oarbank/policy.json`:

```json
{"Settings": {"run_on_battery": false, "jobs": 2}}
```

The agent re-reads the policy at most once a minute. On the node, `oarbank-agent policy` prints what the machine's
policy sets (a managed join code shows as `(set)`, never its value). In the console, a row the policy tightens carries
"Managed on this machine: off (applies)", its source badge names the organization, and **Settings → Applied drift**
lists the machines whose policy tightens settings. These keys need a 2.9 agent and, on Windows, the 2.9 ADMX template.
The other managed policy keys (the join code, `ShowStatusIcon` and so on) are in
[Adding machines](/oarbank/operate/adding-machines#many-machines-mdm-intune-ansible).

## Settings as code

Export a scope's settings as YAML, keep the file in version control, and import it into this or another fleet:

```sh
oarbank settings export --out fleet.yaml                      # the fleet, every group, node labels and values, tool definitions
oarbank settings export --scope group:Laptops --out laptops.yaml
oarbank settings export --scope node:build-07 --module reel
oarbank settings import fleet.yaml --dry-run                  # exit 0: no changes; exit 2: there are changes
oarbank settings import fleet.yaml
```

```yaml
oarbank: settings/v1
scope: fleet
fleet:
  settings:
    job_mem_gb: 2
    user_idle_s: !locked 600          # a lock at this scope
groups:
  laptops: {name: Laptops, rank: 100, selector: {"battery": true}, members: [], settings: {run_on_battery: !locked false}}
nodes:
  build-07: {node_id: n_…, labels: ["render"], settings: {jobs: 3}}
```

An import applies only the differences, as one operation: groups, node labels (a node's `labels` list is its whole set
of owner labels), host tool definitions, then every value as one change, checked and previewed as usual. A key the
file leaves out is never touched; `key: !reset` deletes a value. Nodes match by `node_id`, then by name; a node or group
the file names that this fleet does not have refuses the import. Secrets are exported as fingerprints, never values,
and are never written by an import; the folder registry and dataset origins are reported, not written. Importing needs
the admin role, and a file that adds or changes a lock asks for a typed confirmation. The console has the same under
**Settings → Export and import**.

## Reports

- **Shadowed values** (`oarbank settings shadowed`, **Settings → Shadowed values**): values that change no node's
  effective value, because a lock holds above them, they equal what the scope inherits, or they have no effect (a node
  cap above a stricter fleet cap). Each has a Reset; `--yes` (or **Reset all**) resets them all as one change. Nothing
  is reset by itself.
- **Applied drift** (`oarbank settings drift`, **Settings → Applied drift**): per node, the revision the coordinator
  sent and the one the agent applied, how long it has lagged, the keys it refused, and what the machine's managed
  policy tightens.

Neither report raises an alert.

## Related

- [Host protection](/oarbank/operate/host-protection): protection rules on the same chain.
- [Host tools](/oarbank/operate/host-tools): `tool.<id>.path` and search paths.
- [Command-line reference](/oarbank/reference/cli#settings-groups-and-labels): every `oarbank settings` and
  `oarbank groups` command.
