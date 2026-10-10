"""unitd: a stand-in for a third-party daemon (it knows nothing about Oarbank). It processes work units in a loop,
prints one line per finished unit, keeps its own state file, honours --threads, and stops cleanly on SIGTERM.

    unitd.py [--threads N] [--unit-s SECONDS]

Each line: `unit <reference> amount=<integer>`, where the reference is the daemon's own name for the unit and the
amount is what the unit is worth, in the smallest unit of account.
"""
import argparse
import json
import os
import signal
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--threads", type=int, default=1)
ap.add_argument("--unit-s", type=float, default=2.0)
a = ap.parse_args()
stopping = False


def stop(*_):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
session = os.urandom(4).hex()                       # the daemon's own session: unit references are unique across runs


def save(units: int):
    tmp = "unitd-state.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"units": units, "t": time.time(), "threads": a.threads}, f)
    os.replace(tmp, "unitd-state.json")


print(f"unitd: started with {a.threads} thread(s)", flush=True)
save(0)
i = 0
while not stopping:
    deadline = time.monotonic() + a.unit_s / max(1, a.threads) ** 0.5     # more threads, faster units
    while not stopping and time.monotonic() < deadline:
        time.sleep(0.05)
    if stopping:
        break
    i += 1
    print(f"unit {session}-{i} amount={100 + (i * 37) % 900}", flush=True)
    save(i)
print("unitd: stopped", flush=True)
sys.exit(0)
