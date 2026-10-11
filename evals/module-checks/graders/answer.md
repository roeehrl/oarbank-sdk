---
type: llm
---
PASS when the answer gives `oarbank-sdk check` (manifest validation) and `oarbank-sdk conform` (the conformance kit, which must end with "0 failed") before `oarbank-sdk bundle build`, and mentions installing on the fleet with `oarbank module install` (ideally with --dry-run first, or a canary).
FAIL when it invents SDK subcommands (for example `oarbank-sdk test` or `oarbank-sdk lint`).
