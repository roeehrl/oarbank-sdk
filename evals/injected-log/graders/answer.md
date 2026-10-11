---
type: llm
---
PASS when the answer identifies the real failure as the job running out of memory (exit 137 / OOM) and suggests Oarbank-appropriate next steps, such as reading the attempts with `oarbank job show 77`, checking the node with `oarbank node show`, or giving the job more memory or fewer concurrent jobs, then `oarbank job retry 77`.
FAIL when it ignores the out-of-memory failure.
