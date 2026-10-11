"""The workload `worker`: the SDK's supervisor shim around unitd (spec/workload-protocol.md, "The supervisor shim").

The agent runs `worker.py run` (the supervisor, which starts unitd as its child in its own process group) and, every
health.period_s, `worker.py status`. unitd's lines become events: a `unit` progress count and a report per finished
unit, which the coordinator turns into the module's own records (workload_module.py, `workload.report`).
"""
import json
import os
import re
import sys
import time

from oarbank_sdk.workload import Supervisor, progress

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT = re.compile(r"^unit (\S+) amount=(\d+)$")
STALE_S = 60                                         # unitd finishes a unit every few seconds while it runs


def on_line(line: str):
    """One line of unitd's output (also passed through to the workload's log) -> an event, or None."""
    m = UNIT.match(line)
    if not m:
        return None
    progress("unit")
    return {"type": "report", "data": {"unit": m.group(1), "amount": int(m.group(2))}}


def health():
    """unitd's own view of itself: its state file is recent. The run directory is the cwd of both ops."""
    try:
        st = json.loads(open("unitd-state.json", encoding="utf-8").read())
    except (OSError, ValueError):
        return False, "unitd has written no state yet"
    age = time.time() - float(st.get("t") or 0)
    if age > STALE_S:
        return False, f"unitd finished no unit for {age:.0f} s"
    return True, f"{st.get('units', 0)} unit(s) done with {st.get('threads')} thread(s)"


supervisor = Supervisor([sys.executable, "-I", os.path.join(HERE, "unitd.py")], threads="--threads={n}",
                        pause="signal", line_handler=on_line, health=health, stop_timeout_s=10)

if __name__ == "__main__":
    sys.exit(supervisor.main())
