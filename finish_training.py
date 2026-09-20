#!/usr/bin/env python3
"""Stop the training run and leave the ladder in a state you can trust.

    python3 finish_training.py            # say what it would do
    python3 finish_training.py --run      # do it

Training produces files. None of them reach a player until three things happen
in order, and each one has failed silently before, which is why this exists
rather than living in somebody's head:

  1. STOP CLEANLY. The rotation is signalled, and it takes the whole process
     group with it. Stopping the old rotation by name left eleven workers
     running on every core, orphaned and invisible.

  2. PROMOTE. A champion in fish_training/evolve is a file nothing plays
     against. From grade B up a bot reads brain["by_strategy"], and only
     promote_champions.py writes it.

  3. RE-MEASURE THE LADDER. Every Elo the game shows a player is supposed to be
     measured, and every measurement ever taken was taken with by_strategy
     empty -- so the numbers on the ladder describe bots that no longer exist.
     calibrate_bots.py sits the grades down against each other and fits a
     Plackett-Luce rating to how they actually finish.

Step 3 is the long one and it is left for last on purpose: it wants the whole
machine, which it only has once training has stopped.

Nothing here writes the fitted Elo into the ladder on its own. The script
reports what was measured; a number that disagrees with the ladder's ordering
is a finding to look at, not something to paper over, and calibrate_bots
refuses --write in that case anyway.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "fish_training", "evolve")
ROTATION = "bot_training_rotation"


def sh(cmd, **kw):
    return subprocess.run(cmd, cwd=HERE, text=True, capture_output=True, **kw)


def rotation_pids() -> list:
    out = sh(["pgrep", "-f", ROTATION]).stdout.split()
    return [int(p) for p in out if p.isdigit()]


def stop_rotation(run: bool) -> None:
    pids = rotation_pids()
    if not pids:
        print("  the rotation is not running")
        return
    print(f"  rotation running as {pids}")
    if not run:
        print("  would signal it, and wait for it to take its workers down with it")
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(60):
        time.sleep(2)
        if not rotation_pids():
            break
    left = sh(["pgrep", "-f", "bot_evolve.py"]).stdout.split()
    if left:
        print(f"  ! {len(left)} trainer processes still up — signalling them directly")
        sh(["pkill", "-f", "bot_evolve.py"])
        time.sleep(5)
    left = sh(["pgrep", "-f", "bot_evolve.py"]).stdout.split()
    print("  stopped, nothing left running" if not left
          else f"  ! {len(left)} still up: {left}")


def promote(run: bool) -> None:
    cmd = [sys.executable, "promote_champions.py"] + (["--write"] if run else [])
    out = sh(cmd).stdout.strip()
    for line in out.splitlines():
        print("  " + line.strip())


def calibrate(run: bool, games: int, seats: int, workers: int) -> None:
    results = os.path.join(OUT_DIR, f"calibration_{time.strftime('%Y%m%d_%H%M%S')}.json")
    cmd = [sys.executable, "calibrate_bots.py", "--games", str(games),
           "--seats", str(seats), "--results", results]
    if workers:
        cmd += ["--workers", str(workers)]
    print("  " + " ".join(cmd))
    if not run:
        print("  (not run: this is the long one, and it wants the whole machine)")
        return
    print("  measuring — this takes a while; raw results are saved as it goes")
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in proc.stdout:
        sys.stdout.write(line)
        tail.append(line)
    proc.wait()
    print(f"  calibration exited {proc.returncode}; raw results in {results}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="actually do it")
    ap.add_argument("--games", type=int, default=400, help="calibration matches")
    ap.add_argument("--seats", type=int, default=4)
    ap.add_argument("--workers", type=int, default=0, help="0 = calibrate_bots decides")
    ap.add_argument("--skip-calibration", action="store_true")
    a = ap.parse_args()

    print("\n1. stop the training run")
    stop_rotation(a.run)
    print("\n2. put the champions where the game reads them")
    promote(a.run)
    if a.skip_calibration:
        print("\n3. re-measure the ladder — skipped")
    else:
        print("\n3. re-measure the ladder")
        calibrate(a.run, a.games, a.seats, a.workers)
    print("\ndone." if a.run else "\nnothing was changed — pass --run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
