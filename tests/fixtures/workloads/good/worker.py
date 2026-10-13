"""The workload: the SDK's supervisor shim around daemon.py."""
import os
import sys

from oarbank_sdk.workload import Supervisor

HERE = os.path.dirname(os.path.abspath(__file__))


def on_line(line):
    return {"type": "progress", "event": "unit", "n": 1} if line.startswith("unit ") else None


sup = Supervisor([sys.executable, "-I", os.path.join(HERE, "daemon.py")], threads="--threads={n}", line_handler=on_line,
                 stop_timeout_s=5)
sys.exit(sup.main())
