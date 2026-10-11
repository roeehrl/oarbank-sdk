# Contributing

Contributions are welcome under the Apache License 2.0.

## Developer Certificate of Origin

Every commit must be signed off, which certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change, or otherwise have the right to submit it under this project's license.

```
git commit -s -m "your message"
```

This adds a `Signed-off-by: Name <email>` trailer. CI rejects commits without one.

## Contract changes

Anything under `spec/` or `schemas/`, and any Pydantic model they are generated from, is a **public contract**. Changes must follow [spec/versioning.md](spec/versioning.md):
- additive only within a major;
- no reused or retyped fields;
- stability labels on everything new;
- a schema-compatibility check against the previous release.

## Hardware reports

The hardware compatibility table ([docs/compatibility.md](docs/compatibility.md)) is generated from
[docs/compatibility/reports.json](docs/compatibility/reports.json). How a contributor's report becomes a row is in
[docs/compatibility/README.md](docs/compatibility/README.md).

## The agent skill

[skills/oarbank/SKILL.md](skills/oarbank/SKILL.md) is the Oarbank skill for coding agents; Claude Code and Codex
install it through [.claude-plugin/](.claude-plugin/), other agents through `npx skills`. It routes agents to docs
pages, so the docs build checks it (`python3 scripts/check_skill.py`, run by the `docs` CI job):
- renaming or removing a docs page the skill names fails the build until the skill is updated;
- any change to `skills/oarbank/` needs a new version, the same in `metadata.version` (SKILL.md) and in
  `.claude-plugin/plugin.json`, or installed copies never update;
- after merging a new version, tag it `skill-v<version>`: the `skill` workflow attaches `oarbank-skill.zip` to a
  release for the Claude app.

Before changing the skill's wording, run its evals with and without it (Claude Code 2.1.269 or later; they use your
own model credentials):

```sh
claude plugin eval . --allow-tools WebFetch --no-publish
```

The `skill` workflow also checks the skill against the live docs every day and opens an issue labelled
`agent skill` when a page it names is gone.
