---
title: Running modules
description: Install, approve, enable and sign a module's releases, follow its readiness checklist to running work, and understand which stages run before a node is certified and why a failed doctor check stops only some jobs.
type: how-to
platforms: [macos, linux, windows]
core: '2.9.0'
sidebar:
  order: 6
---

Oarbank runs no work of its own: every job type is a **module**, delivered as a bundle (`.mfb`). This page takes a
module from its bundle to running jobs, as of Oarbank 2.9.0. The commands work from the console's **Modules** page too.

## From bundle to running work

1. **Install** the bundle. Installing enables nothing.

   ```sh
   oarbank module install reel-1.2.0.mfb --dry-run     # what it is and what it asks for
   oarbank module install reel-1.2.0.mfb
   ```

2. **Approve its sandbox grants**, if it asks for any: network hosts, host tools, the GPU, containers, folders. The
   approval is for that exact version; a new version that asks for something new needs a new approval.

   ```sh
   oarbank module approve reel@1.2.0
   ```

3. **Enable** it. Oarbank builds a **release** for every platform in the fleet: the module bundles that platform's
   nodes run (a platform the module does not support gets a release without it).

   ```sh
   oarbank module enable reel@1.2.0
   ```

4. **Sign each platform's release** (see below). With release signing on, which is the default, no node gets anything
   until you sign.

5. **Finish what its checklist asks.** The module's page opens with **Getting this module running**; `oarbank module
   ready reel` prints the same.

## Sign each platform's release

Enabling, upgrading, canarying, pinning or rolling back a module changes what nodes run, so the coordinator builds a
new release for every platform. Each one waits as a **candidate** until you sign it, on the computer that holds your
owner key (the coordinator's computer, if the setup wizard made your keys there):

```sh
oarbank release list                         # each release's platform, status, signature and contents; the waiting ones with their command
oarbank release sign <release> --promote     # once per waiting platform release
oarbank release sign <release>               # a canary or pinned node's own release: never --promote it
```

Until a release is signed, Oarbank names it everywhere you look:

- a banner on the **Fleet**, **Modules** and **Settings** pages, with each waiting release's platform, contents and
  command;
- the alert `release_awaiting_owner` after five minutes (it clears once the release is signed and current, or a newer
  build replaces it);
- each waiting node's card ("release needs your signature") and `oarbank explain node <node>`, with one of the reason
  codes `NO_RELEASE` (no module enabled yet), `RELEASE_UNSIGNED` (its release waits for you, with the command) or
  `RELEASE_PENDING` (the node is installing it);
- the module's readiness checklist.

The coordinator builds a platform's release once per set of modules, so nodes connecting while a candidate waits change
nothing. Signing, the owner key set and developer mode are explained in
[release-signing.md](https://github.com/roeehrl/oarbank/blob/v2.9.0/docs/release-signing.md) in the core's repository.

## The readiness checklist

**Getting this module running** walks the whole path from install to work, computed from the fleet as it is now. Each
step has a status (**done**, **blocked**: you must act, **waiting**: the fleet is working on it or an earlier step comes
first) with the reason and the fix, as a button in the console and a command in `oarbank module ready <module>`.
Without a name, `oarbank module ready` checks every module.

| Step | What it checks |
|---|---|
| Installed | Which version, installed by whom |
| Sandbox grants approved | What the version asks for, and whether you approved it |
| Enabled | Whether it is the current version on every node, or disabled fleet-wide |
| Required settings | The settings the module marks required: set for the fleet, or on each node ([Settings](/oarbank/operate/settings#module-settings)) |
| Where it runs | The fleet's platforms the module supports |
| Release built per platform | Every platform it runs on has a release with this version |
| Release signed and current per platform | Each of those releases is signed and current |
| Host tools on the nodes | Per tool it asks for, how many nodes have a version it accepts, and the install command for the others ([Host tools](/oarbank/operate/host-tools)) |
| Nodes that can run it | For each stage, the nodes that meet its needs and why the others do not: a capability not reported, no container runtime, a platform the module does not support |
| Certified nodes | Each stage that needs certification has a certified node |
| Start work | The module's own operations and pages |

## Which stages run before certification

A node runs a module's work once the module is **certified** there: its doctor is healthy and its golden jobs passed.
Certification is what lets a node's results be compared with other nodes' and served from the result cache. From 2.9,
two kinds of stage do not wait for it, because no other node's result is ever checked against theirs:

- a **bootstrap stage**, which fetches the module's pinned datasets (its result counts only if it is exactly the pinned
  files);
- a stage that **compares nothing and needs nothing a node must prove**: its determinism is `none` and it needs no
  capability and no pool. An ingestion job that pulls a moving feed is the usual case.

Their jobs run on any node where the module's runner starts, even while its doctor reports `unhealthy` or
`undetected` there. Everything else still applies: platforms, the sandbox, secrets, placement, retries, and the breaker
that revokes a module on a node whose jobs keep failing. A stage whose results are compared, or one that needs a
capability (a JDK, say) or a pool (containers), always waits for certification.

**A failed doctor check stops only what it proves.** A doctor check named after a capability (for example a check
`java17` beside a `java17` probe) that fails takes that capability away from the module on that node: stages that need
it wait there with `STAGE_CAPABILITY_MISSING`, and the other stages keep running. A failed check that names no
capability (say `disk_free_20gb`) makes the doctor unhealthy, so the module is not certified and the stages that need
certification wait, while the exempt stages run.

What you see:

- `oarbank explain job <id>` for a job no node can take names the requirement and the nodes it covers by platform,
  commonest first: "No node can run this job right now: Its stage needs java17; this node's services, probes and module
  doctor do not provide java17 (5 nodes: 5 darwin-arm64)".
- A job of an exempt stage that cannot run shows whether the module's runner started on the node ("undetected, runner
  started" or "runner not started"), so "its runner did not start" and "not certified" read differently.
- `oarbank node show <node>` and the node page print every doctor check's detail in full; when a doctor prints nothing
  usable, the agent keeps the last 4000 characters of its error output.
- A missing host tool or folder keeps off only the jobs that would use it: a missing tool spares the exempt stages
  (they get no tools), a missing folder spares bootstrap jobs only.

These rules need no change to a module and no new module version. Module authors: the rule is in
[Stages that run before certification](/oarbank/spec/manifest#stages-that-run-before-certification), and how to name
doctor checks after capabilities is in the runner protocol's [doctor](/oarbank/spec/runner-protocol#doctor) section.

## Try a new version first

```sh
oarbank module canary reel@1.3.0 --node build-07     # or --group Canary: the group's members when the canary starts
oarbank module promote reel                          # once every canary node is certified on it
oarbank module rollback reel                         # abandon the canary, or go back a version
```

Each of these changes the releases, so sign the new candidates as above. To stop a module everywhere at once,
`oarbank module disable reel` (the kill switch); to stop it on some nodes only, turn its `enabled` setting off for a
group or node ([Settings](/oarbank/operate/settings#module-settings)).

## macOS: modules with native Python packages

From 2.9 the interpreters that run module code on Macs (the node runtime's and the coordinator's Python) may load
third-party native libraries, such as the extension modules in wheels from PyPI. In 2.8.0, a Developer ID signed build
refused them ("mapping process and mapped file (non-platform) have different Team IDs"), so modules that import numpy
or similar packages failed their doctor on Macs. Module processes are still confined by the macOS sandbox; only the
interpreters carry the change, not the agent or the apps. Nodes get it from the 2.9 node package
([Upgrading](/oarbank/operate/upgrading#nodes)).
