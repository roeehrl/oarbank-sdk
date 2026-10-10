"""A daemon that daemonizes itself the classic way: fork, setsid, fork again. The grandchild does the work outside the
process group it was started in; the parent stays to keep the supervisor content."""
import os
import signal
import sys
import time

if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        while True:                   # the escaped worker: still carries the run's environment
            time.sleep(0.2)
    os._exit(0)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
i = 0
while True:
    i += 1
    print(f"unit {i}", flush=True)
    time.sleep(0.2)
