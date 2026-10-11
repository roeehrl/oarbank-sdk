---
type: llm
---
PASS when the answer tells the user to run `oarbank explain job 4821` on the coordinator and explains that it prints reason codes, and names at least two real Oarbank reasons a job stays pending (for example no eligible node, a module not ready or not certified on the node, secrets not set, an agent too old, the fleet or campaign paused).
FAIL when the answer relies on Kubernetes, Slurm or generic queue advice, or invents commands that are not `oarbank` subcommands.
