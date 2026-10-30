---
title: Delegated processes
description: Let host protection pause and lower a background program of your own, started outside Oarbank, for the programs you protect, by naming it with a code signature or a trusted exact path in a delegate rule.
type: how-to
platforms: [macos, linux, windows]
core: '2.11.1'
sidebar:
  order: 10
---

Host protection only ever acts on processes the node's agent started itself. Some of the background load on a machine
is yours, though: a program you run that is fine to slow down whenever a protected program needs the machine. From
2.10 you can **delegate** such a program to host protection. It is then treated like fleet work: paused and lowered
for your protected programs, never stopped. This page covers Oarbank 2.11.1.

Delegation is the owner's choice only. A module can never declare it or widen it.

## 1. Turn it on for the node

Delegation is off by default. Turn the node setting `delegated_processes` on where you want it (a reviewed change):

```sh
oarbank settings set delegated_processes true --node build-07 --dry-run
oarbank settings set delegated_processes true --node build-07
```

With the setting off, delegate rules match nothing. A machine's managed policy may force it off
([Settings](/oarbank/operate/settings#managed-on-this-machine)).

## 2. Name the program in a delegate rule

A delegate rule names a process tree and what protection may do to it, `pause`, `lower` or both. It has no other
action and protects nothing:

```toml
[[rule]]
id       = "example-sync"
match    = { team_id = "ABCDE12345", identifier = "com.example.sync" }   # macOS: a code-signing identity
tree     = "descendants"
delegate = { actions = ["pause", "lower"] }
```

```toml
[[rule]]
id       = "example-indexer"
match    = { path_prefix = "/opt/example/bin/example-indexer" }        # Linux and Windows: one executable's exact path
delegate = { actions = ["lower"] }
```

Add it to the node's protection section like any rule ([Host protection](/oarbank/operate/host-protection)):
`oarbank protection set build-07 rules.toml`, after `oarbank protection preview build-07 rules.toml`.

The matcher must be one a module cannot copy. Anything else is refused when you set the rule on that node:

| OS | Accepted matcher | Also checked at every match |
|---|---|---|
| macOS | A code-signing identity: `requirement`, or `team_id` with `identifier` | The signature |
| Linux | `path_prefix` naming one executable's exact path | The file and every directory above it are owned by root and writable by no one else |
| Windows | `path_prefix` naming one executable's exact path | The file is under Program Files or the Windows folder |

A process whose details cannot be read is never delegated.

## What protection does with it

Delegated processes count as fleet work, not as your load:

- When a protected program needs the machine, the processes are **paused** or **lowered** to background scheduling,
  exactly when fleet jobs would be, and resumed or restored when it no longer does.
- They are **never stopped**. One that has been paused for the node's longest pause (10 minutes by default) is resumed
  and lowered instead until the pause would have ended (`DELEGATE_PAUSE_LIMIT`).
- The agent acts on one process at a time and checks its start time first, so a reused process id is never touched.
- When the agent stops, it resumes and restores everything it paused or lowered.

Each action is in the node's decision timeline: `DELEGATE_PAUSE`, `DELEGATE_RESUME`, `DELEGATE_LOWER`,
`DELEGATE_RESTORE` and `DELEGATE_PAUSE_LIMIT` ([Reason codes](/oarbank/reference/reason-codes#protection)).

## Programs that run in your login

On a system install the agent runs as its own service account, which may not signal your processes. When its own action
is refused, your **session helper** (the small helper that runs in your login) pauses, resumes, lowers or restores the
process on the agent's request, after checking its start time. The timeline records such actions as done through the
session helper. While nobody is logged in, no helper can act on that person's processes.

## Limits in this release

- `stop` is not offered: delegation allows `pause` and `lower` only.
- Windows matches by trusted path only (no publisher check yet), and the session-helper relay works on macOS and Linux.
  On Windows the agent acts on delegated processes of its own session.
- On Linux an agent without the right to raise priority back (`CAP_SYS_NICE`) cannot always undo a lowering. It
  restores what it can and records the rest.
