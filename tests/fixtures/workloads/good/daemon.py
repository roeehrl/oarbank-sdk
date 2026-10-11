"""A stand-in third-party daemon: it works through units in a loop, prints a line per unit, and stops on SIGTERM."""
import argparse
import signal
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--threads", type=int, default=0)
a = ap.parse_args()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
i = 0
while True:
    i += 1
    print(f"unit {i} threads={a.threads or 'any'}", flush=True)
    time.sleep(0.2)
