#!/usr/bin/env python3
"""Learn from games already on disk, and split the result into its two halves.

    python3 split_learning.py --games fish_training/forever/run/games.jsonl \
        --out-dir fish_training/diag

A learning pass changes two different things at once:

  * the WEIGHTS -- about 44 numbers per table size, the policy's own knobs;
  * the MAPS -- synergy, same-ocean synergy, species synergy, strategy value
    and the strategy transitions, tens of thousands of learned numbers.

Both move a long way in one pass (measured: 1,634 synergy keys at 4P and
future_value from 1.95 to 0.00 at 2P), the pass as a whole benchmarks WORSE
than the brain it started from, and there is no way to tell from that which
half is doing the damage. There is reason to think the weights are RIGHT: an
independent measurement of hauling future_value back up to its old floor cost
1.77 points a game, which is the learner's direction, not the floor's.

So this writes four brains from one learning pass, all starting from the live
brain and differing only in which part of what was learned is kept:

    learned_full      everything (what the pipeline would promote)
    learned_weights   the weights only, maps left as they are
    learned_maps      the maps only, weights left as they are
    learned_gentle    everything, at a much smaller learning rate

Measure each against the live brain with ab_brain.py and the answer says where
to spend the effort. No games are played here; it re-uses the ones the last run
generated, so a comparison costs one benchmark instead of one training run.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish

MAPS = fish.TRAIN_POLICY_MAP_KEYS


def load_games(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def learn(base: Dict[str, Any], records: List[Dict[str, Any]], lr_w: float,
          log) -> Dict[str, Any]:
    """One learning pass over `records`, exactly as train_learn does it, but at
    a chosen learning rate."""
    cand = copy.deepcopy(base)
    scale = fish.train_run_scale(records)
    scored = []
    for r in records:
        q, agg = fish.train_score_game_quality(r, scale)
        scored.append((q, agg, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    learned = pruned = 0
    for q, agg, r in scored:
        if q <= 0.0:
            pruned += 1
            continue
        fish._train_learn_one(cand, r, q, lr_w=lr_w, top_decisions=[])
        learned += 1
    cand["weights"] = fish.stabilize_weights(dict(cand.get("weights", {})))
    for slot in (cand.get("by_count") or {}).values():
        if isinstance(slot, dict) and isinstance(slot.get("weights"), dict):
            slot["weights"] = fish.stabilize_weights(dict(slot["weights"]))
    fish.ensure_priority_anchor_brain_rules(cand)
    log(f"  lr {lr_w}: {learned} games learned, {pruned} pruned")
    return cand


def graft(base: Dict[str, Any], learned: Dict[str, Any], what: str) -> Dict[str, Any]:
    """`base`, with only `what` ("weights" or "maps") taken from `learned`."""
    out = copy.deepcopy(base)
    keys = ("weights",) if what == "weights" else MAPS
    for k in keys:
        if isinstance(learned.get(k), dict):
            out[k] = copy.deepcopy(learned[k])
    lb = learned.get("by_count") or {}
    for c, cell in lb.items():
        if not isinstance(cell, dict):
            continue
        # A count with no cell in the base gets one, because the learning pass
        # creates one too and leaving it out would measure a different change.
        # It has to be created the way the pipeline creates it -- seeded from
        # the BASE's top-level maps -- and only then overwritten with the half
        # under test. Deep-copying the learned cell instead is how the first
        # draft of this leaked both halves into both variants: six of the seven
        # counts have no cell, so "weights only" came back carrying 42,277
        # changed map entries.
        dest = fish.get_count_brain(out, int(c))
        for k in keys:
            if isinstance(cell.get(k), dict):
                dest[k] = copy.deepcopy(cell[k])
    fish.ensure_priority_anchor_brain_rules(out)
    return out


def summarise(name: str, base: Dict[str, Any], cand: Dict[str, Any], log) -> None:
    moved_w = 0
    for c in sorted((cand.get("by_count") or {})):
        a = ((base.get("by_count") or {}).get(c) or {}).get("weights") or base.get("weights", {})
        b = ((cand.get("by_count") or {}).get(c) or {}).get("weights") or {}
        moved_w += sum(1 for k in b if abs(float(b[k]) - float(a.get(k, 0.0))) > 1e-6)
    moved_m = 0
    for c in sorted((cand.get("by_count") or {})):
        for mk in MAPS:
            a = ((base.get("by_count") or {}).get(c) or {}).get(mk) or base.get(mk, {})
            b = ((cand.get("by_count") or {}).get(c) or {}).get(mk) or {}
            moved_m += sum(1 for k in set(a) | set(b)
                           if abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) > 1e-9)
    log(f"  {name:18s} {moved_w:5d} weights moved, {moved_m:7d} map entries moved")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="fish_training/forever/run/games.jsonl")
    ap.add_argument("--brain", default="fish_ai_brain.json")
    ap.add_argument("--out-dir", default="fish_training/diag")
    ap.add_argument("--lr", type=float, default=0.02, help="the pipeline's own rate")
    ap.add_argument("--gentle-lr", type=float, default=0.0015)
    a = ap.parse_args()

    def log(m: str) -> None:
        print(m, flush=True)

    os.makedirs(a.out_dir, exist_ok=True)
    records = load_games(a.games)
    log(f"{len(records)} games from {a.games}")
    if not records:
        log("nothing to learn from")
        return 1
    base = fish.load_brain(a.brain)
    log(f"base {a.brain}: {base.get('games_played', 0)} games learned, "
        f"counts {sorted((base.get('by_count') or {}).keys())}")

    full = learn(base, records, a.lr, log)
    gentle = learn(base, records, a.gentle_lr, log)
    variants = {
        "learned_full": full,
        "learned_weights": graft(base, full, "weights"),
        "learned_maps": graft(base, full, "maps"),
        "learned_gentle": gentle,
    }
    log("")
    for name, cand in variants.items():
        summarise(name, base, cand, log)
        path = os.path.join(a.out_dir, f"{name}.json")
        fish.save_brain(cand, path)
    log("")
    log("wrote four brains. Measure each against the live one, e.g.:")
    log(f"  python3 ab_brain.py --games 300 --counts 2,4,6 "
        f"--new {a.out_dir}/learned_weights.json --old {a.brain} "
        f"--grade william_beebe --label weights-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
