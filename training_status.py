#!/usr/bin/env python3
"""Is the training running, and is it still finding anything?

    python3 training_status.py

Two different questions. A process list answers the first; only the champions
answer the second, and a run that has stopped finding things looks exactly like
a healthy one from the outside.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fish_training", "evolve")


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def _cpu_seconds(pids) -> float:
    """Total CPU time these processes have used, in seconds."""
    out = sh(["ps", "-o", "time=", "-p", ",".join(pids)])
    total = 0.0
    for line in out.splitlines():
        parts = line.strip().replace("-", ":").split(":")
        try:
            nums = [float(x) for x in parts]
        except ValueError:
            continue
        secs = 0.0
        for n in nums:
            secs = secs * 60 + n
        total += secs
    return total


def cores_in_use(pids) -> float:
    """Cores busy RIGHT NOW, from a short sample of CPU time.

    Not ps's %cpu, which is an average over each process's whole life: the
    trainer builds a fresh worker pool for every confirming batch, so for the
    first minutes of each batch that average reads near zero and the run looks
    stalled when it is working perfectly. It said "1.0 of 12 cores" once while
    eleven workers were each near a full core.
    """
    t0 = _cpu_seconds(pids)
    time.sleep(2.0)
    return max(0.0, (_cpu_seconds(pids) - t0) / 2.0)


def main() -> None:
    pids = [p for p in sh(["pgrep", "-f", "bot_training_rotation"]).split() if p.isdigit()]
    if pids:
        up = sh(["ps", "-o", "etime=", "-p", pids[0]]).strip()
        print(f"RUNNING  pid {pids[0]}, up {up}")
    else:
        print("NOT RUNNING")

    pids = [p for p in sh(["pgrep", "-f", "multiprocessing.spawn"]).split() if p.isdigit()]
    if pids:
        print(f"         {len(pids)} workers, using {cores_in_use(pids):.1f} of "
              f"{os.cpu_count()} cores")

    try:
        st = json.load(open(os.path.join(OUT, "rotation_state.json")))
    except Exception:
        print("no state file yet")
        return
    cells = st.get("cells", {})
    total = sum(int(v.get("promotions", 0)) for v in cells.values())
    print(f"\ncycle {st.get('cycle')} · {total} champion(s) crowned in all\n")
    print(f"  {'cell':20s} {'tier':>4s} {'visits':>7s} {'champions':>10s}")
    for name in sorted(cells):
        v = cells[name]
        mark = "  SETTLED" if v.get("settled") else ""
        print(f"  {name:20s} {v.get('tier', 0):>4d} {v.get('visits', 0):>7d} "
              f"{v.get('promotions', 0):>10d}{mark}")

    log = os.path.join(OUT, "rotation.log")
    if os.path.exists(log):
        lines = [l.rstrip() for l in open(log, errors="replace").read().splitlines()
                 if l.strip()]
        print("\nlast few log lines:")
        for l in lines[-5:]:
            print("   " + l)
        age = (time.time() - os.path.getmtime(log)) / 60
        print(f"\nlog last written {age:.0f} min ago"
              + ("   <- a cell is mid-run; long cells are normal" if age > 5 else ""))


if __name__ == "__main__":
    main()
