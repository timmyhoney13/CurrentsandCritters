#!/usr/bin/env python3
"""Put the trained strategy champions into the brain the game actually reads.

    python3 promote_champions.py            # say what would change
    python3 promote_champions.py --write    # change it, after a backup

WHY THIS EXISTS. bot_evolve.py crowns a champion by playing it against the
current one on paired deals and only promoting it when the margin's 95% lower
bound clears zero. It writes that champion to fish_training/evolve/champion_
<strategy>.json. Nothing then read it. `--promote` writes into the live brain,
but only at the end of a run that crowned somebody, and the rotation script
never passed it, so from grade B up -- the whole half of the ladder that is
meant to play with its strategy's own trained weights -- every bot was falling
back to the shared vector:

    decision_weights = strategy_weights.get(label) or weights   (multiplayer_server)

and strategy_weights was built from brain["by_strategy"], which was empty. Five
days and five cycles of training had never reached a single game. This is the
step that connects the two, and the rotation runs it after every cycle.

It is a straight copy of what was measured, not a new judgement: a champion only
exists because it beat the thing it replaced.
"""
from __future__ import annotations

import argparse
import glob
import re
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fish_game_all_in_one as fish

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "fish_training", "evolve")
# The planner's knobs live in their own file, which reef_planner reads directly.
SKIP = {"planner"}
_COUNT_VECTOR = re.compile(r"\d+p")


# How many brain backups to keep. The brain is 5.8MB and the rotation now
# promotes the moment a champion is crowned rather than once a cycle, so an
# unpruned backup for every promotion is hundreds of megabytes over a long run
# -- for files whose whole purpose is "the last few states, in case the most
# recent write was wrong".
KEEP_BACKUPS = 10


def prune_backups() -> None:
    pattern = f"{fish.BRAIN_PATH}.promote_backup_*.json"
    found = sorted(glob.glob(pattern))
    for old in found[:-KEEP_BACKUPS]:
        try:
            os.unlink(old)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="actually update the brain")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    brain = fish.load_brain(fish.BRAIN_PATH)
    # The 4P brain is what a strategy vector is seeded from when it has never
    # existed, exactly as bot_evolve seeds it.
    base = fish._train_policy_maps_from_cbrain(fish.get_count_brain(brain, 4))["weights"]

    changed = []
    for path in sorted(glob.glob(os.path.join(a.out_dir, "champion_*.json"))):
        label = os.path.basename(path)[len("champion_"):-len(".json")]
        # champion_4p.json is the shared per-count vector, not a strategy. Matched
        # on the digits, because "ends in p" would one day silently swallow a
        # strategy named for something that does.
        if label in SKIP or _COUNT_VECTOR.fullmatch(label):
            continue
        try:
            champ = json.load(open(path))["weights"]
        except Exception as exc:
            print(f"  ! {label}: unreadable ({exc}) — skipped")
            continue
        slot = fish.get_strategy_weights(brain, label, base)
        before = dict(slot)
        slot.update({k: float(v) for k, v in champ.items() if isinstance(v, (int, float))})
        fish.stabilize_weights(slot)
        moved = sum(1 for k in slot if abs(float(slot[k]) - float(before.get(k, 0.0))) > 1e-9)
        if moved:
            changed.append((label, moved, len(slot)))

    if not a.quiet:
        for label, moved, total in changed:
            print(f"  {label:20s} {moved:2d}/{total} weights differ from the live brain")
        if not changed:
            print("  the live brain already matches every champion")

    if changed and a.write:
        backup = f"{fish.BRAIN_PATH}.promote_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        shutil.copy2(fish.BRAIN_PATH, backup)
        fish.save_brain(brain, fish.BRAIN_PATH)
        prune_backups()
        print(f"  wrote {len(changed)} strategy vectors into {fish.BRAIN_PATH} "
              f"(backup {os.path.basename(backup)})")
    elif changed:
        print("  nothing written — pass --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
