---
title: Host protection
description: How Oarbank keeps fleet work from harming the people using a node, and how to set its mode and protected-process rules for the fleet, a group or one node, roll rules out through a canary group, and read what it did.
type: how-to
platforms: [macos, linux, windows]
core: '2.10.4'
sidebar:
  order: 9
---

Nodes are computers people use. Every few seconds, host protection decides how much fleet work a node may run without
harming its owner's own work: it holds new jobs back, lowers or pauses fleet jobs, or evicts them, and it only ever
touches processes the agent started itself, or processes you delegated to it. You set it; a module can never declare or
loosen it. This page covers Oarbank 2.10.4.

## Protection is three settings

From 2.9 protection lives on the [settings](/oarbank/operate/settings) chain, at fleet, group or node scope:

| Setting | What it is | How scopes combine |
|---|---|---|
| `protection.mode` | `fleet_first`, `moderate` (the default) or `strict_yield`: how strongly the node yields to the person using it | The most specific value wins; a fleet or group value may lock it |
| `protection.rules` | Protected-process rules | Every scope's rules apply together: the fleet's, each group's and the node's. A rule only ever protects more |
| `protection.node` | The rest of the node section: memory guard, timing, GPU jobs, longest pause (advanced) | The most specific value wins |

```sh
oarbank settings set protection.mode strict_yield --group Laptops
oarbank node mode build-07 fleet_first                 # a node's own mode
oarbank protection show build-07                       # its effective rules, and where each comes from
oarbank protection preview fleet rules.toml            # what replacing the fleet's rules would change, per node
oarbank protection set fleet rules.toml                # replace the fleet's own rules
oarbank protection set group:Laptops laptop-rules.toml
oarbank protection set build-07 node-rules.toml        # a node's own section
```

`set` replaces one scope's own section (a node, `fleet` or `group:<group>`) from a JSON or TOML file, as one change
with a per-node review. Rule ids must be unique on a node: a change that would bring the same id from two scopes is
refused. A rule the node's OS cannot run (a code-signing matcher on Linux) is refused when set on that node, and skipped,
by name, when it comes from the fleet or a group.

In the console, a node's **Protection** page shows its effective rules with their source and the rules skipped there,
and edits the node's own section. The fleet's section is on **Settings**, a group's on its page.

## Roll rules out through a canary group

The per-node canary, promote and restore of 2.8 are gone. Instead, put new rules on a small group, watch those
nodes, then promote:

```sh
oarbank groups create Canary --member build-07
oarbank protection set group:Canary new-rules.toml
oarbank settings promote protection.rules --group Canary
```

Promoting joins the group's rules to the fleet's (a rule with the same id becomes the group's version) and removes them
from the group in one change, so the canary nodes see no change. Each change to a node's effective rules is kept in its
protection history.

## A rule

A rule has a matcher, a tree scope, an activity condition, actions and timing:

```toml
[[rule]]
id          = "xcode-builds"
match       = { requirement = 'anchor apple and identifier "com.apple.dt.Xcode"' }
tree        = "descendants"
active_when = { cpu_cores_gt = 1.0, for_s = 4 }
lower_fleet = { to = "background" }

[[rule]]
id           = "calls"
match        = { bundle_id = ["us.zoom.xos", "com.apple.FaceTime"] }
active_when  = "present"
pause_fleet  = { scope = "all" }
exit_after_s = 120
```

- **Matchers:** a code-signing identity (Team ID and signing identifier, or a designated requirement), a bundle id, a
  path prefix or substring, and an argument pattern. A signature survives updates and moves; a path does not.
- **Trees:** `self`, `descendants`, or `same_team` (helpers signed by the same team).
- **Activity:** `present`, or thresholds on CPU cores, memory footprint or GPU activity, or `frontmost`, held for
  `for_s` seconds.
- **Actions** include `lower_fleet`, `cap_fleet`, `reserve` and `pause_fleet` (scope `all`, `cpu`, `gpu` or `io`).
- **Protect** (`protect`) names what to keep from harm, for example `metric = "progress_rate"` with a source the
  program writes, and `max_slowdown`.
- **During** blocks (`[[rule.during]]`) act only while a phase of the program is on, read from a source; `tags` label the
  phase (`uplink` reaches the node's [site](/oarbank/operate/sites)).
- **`immediate = true`** makes the rule's phases act the moment they begin, between protection's two-second ticks.
- **`delegate`** names a program of your own that protection may pause and lower as fleet work
  ([Delegated processes](/oarbank/operate/delegated-processes)).

From 2.9, while a `pause_fleet` rule is active the node also **takes none of the work it pauses**: its capacity names the
paused scopes (`paused`, `paused_by`), and the coordinator grants it no job the rule would pause at once (`all` and
`io`: no job; `gpu`: no GPU job; `cpu`: the others). Before, such a job was paused, released after the longest pause
and granted straight back to the same node. Explain shows the job or the node as `PROTECTION_ACTIVE`, naming the rule.

A process that runs a new program (an exec) is identified by that new program, so a path or argument rule matches the
app a launcher turns into.

The console's process picker writes a matcher from the processes a node reports, and previews what it matches.

### Processes whose details cannot be read

The agent cannot always read another account's process path or arguments. Such a process counts as matching, so a
failed lookup never leaves one of your processes unprotected, and the rule's report counts it as `unreadable`
(`PROTECTION_UNREADABLE`). From 2.9, a process matched that way takes part **from its second sighting on** (the next
check, a few seconds later). Almost always, a process whose details fail to read is one that is exiting between the
listing and the read; on a busy machine those used to switch such rules on and off every few seconds.

## New in 2.10

- **Sources are watched.** Progress and phase sources (an event file, a log, a program) are read as they change, a
  glob follows the newest file, and a source in a person's home is read by that person's session helper on a system
  install. A source that cannot be read holds fleet work back and the node says why (`PROTECTION_SOURCE_UNREADABLE`,
  `PROTECTION_SOURCE_NEEDS_HELPER`). [Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy#sources-are-watched-and-immediate-rules-act-at-once)
  lists the kinds of source.
- **A rule that protects `progress_rate` holds GPU work back** while its program uses the GPU, as a `gpu_share` rule
  does (except under `gpu_jobs = "always"`).
- **Measured co-tenancy.** The paired on/off harness measures what fleet work costs a protected program; a passing run
  is a certificate, and `gpu_jobs = "certified"` lets cooperative GPU work run beside a protected GPU program up to the
  certified duty, under the GPU duty governor ([Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy)).
- **Workloads** are fleet work with their own ladder: throttled, lowered, paused only up to what they tolerate, then
  stopped and held ([Workloads](/oarbank/operate/workloads#host-protection-and-workloads)).
- **Delegated processes**: with the node setting `delegated_processes` on, a `delegate` rule lets protection pause and
  lower a program of yours ([Delegated processes](/oarbank/operate/delegated-processes)).

## The node's own rules

Besides the rules from the coordinator, a node can carry a local file, `protection.json` beside the agent's home. The
agent combines both, and the stricter setting wins on every dimension, so the two never conflict. Managed policy cannot
set protection; this file is how a machine's administrator adds protection locally.

## Related

- Memory guard, caps and "someone is using it" settings: [Settings](/oarbank/operate/settings#the-keys).
- What each protection code means: [Reason codes](/oarbank/reference/reason-codes#protection).
- Measuring co-tenancy from a terminal: `oarbank protection ab start|status|show|abort|sweep`, `oarbank protection
  certificates` and `oarbank protection duty` ([Command-line reference](/oarbank/reference/cli#nodes)).
