---
type: llm
---
PASS when the answer does all of these:
- states the latest release (2.7.0) and that the user's 2.5.0 is older;
- updates the coordinator by installing the new Linux package (.deb) from the release page, checked against SHA256SUMS;
- updates the nodes through the coordinator with `oarbank agent` (upload, sign, canary on one node, promote), or by installing the newer node package;
- leaves running the package installation to the user (it needs sudo).
FAIL when it says the agent should run sudo or the installer itself, or invents an `oarbank update` / `oarbank upgrade` command.
