#!/usr/bin/env python3
"""Is the AI actually changing, and was each change proved before it landed?

    python3 check_updating.py                 # what has moved since last time
    python3 check_updating.py --snapshot      # ... and record where we are now

Two separate questions, because either one alone is misleading:

  IS IT MOVING?   Compare the live brain, every champion file and the measured
                  plan priors against a snapshot, BY VALUE. A map's size is not
                  the answer: a learning pass can rewrite two thousand synergy
                  numbers without changing how many there are, and the first
                  version of the supervisor's status display reported exactly
                  that -- counts -- so fourteen cycles of real work read as
                  "nothing has changed" when strategy vectors and plan priors
                  had both moved.

  WAS IT EARNED?  Every mover here is supposed to come with its own proof: a
                  champion only exists because it beat the last one with the
                  margin's 95% lower bound above zero, and a plan prior is only
                  written when its interval clears zero. This prints that
                  evidence next to the change, so a silent regression cannot
                  hide behind the word "updated".

It also says plainly when NOTHING has moved, which is the failure the
supervisor cannot see from the inside: a phase can burn eleven hours retrying
one plan whose generation never fits its budget, and every log line along the
way looks healthy.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish

SNAP = os.path.join(HERE, "fish_training", "forever", "updating_snapshot.json")
EVOLVE = os.path.join(HERE, "fish_training", "evolve")
# Weights whose value is worth printing when it moves: a vector has 44 numbers
# and a wall of them is not a report.
WATCH = ("future_value", "plan_fit_bonus", "deny_bonus", "synergy_bonus",
         "strategy_bonus", "species_bonus", "same_ocean_bonus")


def _vec_drift(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[int, float]:
    keys = set(a) | set(b)
    moved = 0
    total = 0.0
    for k in keys:
        try:
            x, y = float(a.get(k, 0.0)), float(b.get(k, 0.0))
        except (TypeError, ValueError):
            continue
        if abs(x - y) > 1e-9:
            moved += 1
            total += abs(x - y)
    return moved, total


def read_state() -> Dict[str, Any]:
    """Everything worth watching, as values rather than counts."""
    out: Dict[str, Any] = {"when": time.strftime("%F %T")}
    try:
        with open(os.path.join(HERE, "fish_ai_brain.json"), encoding="utf-8") as fh:
            brain = json.load(fh)
    except Exception as exc:
        out["brain_error"] = str(exc)
        brain = {}
    out["brain"] = {
        "games_played": brain.get("games_played", 0),
        "weights": {k: float(v) for k, v in (brain.get("weights") or {}).items()
                    if isinstance(v, (int, float))},
        "by_strategy": {lab: {k: float(v) for k, v in (vec or {}).items()
                              if isinstance(v, (int, float))}
                        for lab, vec in (brain.get("by_strategy") or {}).items()},
        "by_count_weights": {c: {k: float(v) for k, v in ((cell or {}).get("weights") or {}).items()
                                 if isinstance(v, (int, float))}
                             for c, cell in (brain.get("by_count") or {}).items()},
        "map_sizes": {m: len(brain.get(m) or {}) for m in fish.TRAIN_POLICY_MAP_KEYS},
    }
    champs: Dict[str, Any] = {}
    for path in sorted(glob.glob(os.path.join(EVOLVE, "champion_*.json"))):
        label = os.path.basename(path)[len("champion_"):-len(".json")]
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        champs[label] = {
            "generation": int(d.get("generation", 0) or 0),
            "margin": float(d.get("margin_edge", 0.0) or 0.0),
            "margin_low": float(d.get("margin_edge_low", 0.0) or 0.0),
            "games": int(d.get("games", 0) or 0),
            "weights": {k: float(v) for k, v in (d.get("weights") or {}).items()
                        if isinstance(v, (int, float))},
        }
    out["champions"] = champs
    try:
        with open(os.path.join(HERE, "family_prior.json"), encoding="utf-8") as fh:
            out["family_prior"] = {k: float(v) for k, v
                                   in (json.load(fh).get("prior") or {}).items()}
    except Exception:
        out["family_prior"] = {}
    return out


def report(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """Print what moved. Returns True if anything did."""
    moved_any = False
    print(f"since {old.get('when', '(no snapshot)')}  ->  {new['when']}")
    print("")

    ob, nb = old.get("brain") or {}, new["brain"]
    lines: List[str] = []
    g0, g1 = ob.get("games_played", 0), nb.get("games_played", 0)
    if g1 != g0:
        lines.append(f"  games learned      {g0} -> {g1}")
    m, tot = _vec_drift(ob.get("weights") or {}, nb.get("weights") or {})
    if m:
        lines.append(f"  shared weights     {m} moved, total drift {tot:.3f}")
        for k in WATCH:
            x = float((ob.get("weights") or {}).get(k, 0.0))
            y = float((nb.get("weights") or {}).get(k, 0.0))
            if abs(x - y) > 1e-9:
                lines.append(f"       {k:20s} {x:+8.3f} -> {y:+8.3f}")
    for c in sorted(set(ob.get("by_count_weights") or {}) | set(nb.get("by_count_weights") or {})):
        m, tot = _vec_drift((ob.get("by_count_weights") or {}).get(c) or {},
                            (nb.get("by_count_weights") or {}).get(c) or {})
        if m:
            lines.append(f"  {c}P cell weights    {m} moved, total drift {tot:.3f}")
    for lab in sorted(set(ob.get("by_strategy") or {}) | set(nb.get("by_strategy") or {})):
        m, tot = _vec_drift((ob.get("by_strategy") or {}).get(lab) or {},
                            (nb.get("by_strategy") or {}).get(lab) or {})
        if m:
            lines.append(f"  by_strategy {lab:18s} {m} weights moved, drift {tot:.3f}")
    om, nm = ob.get("map_sizes") or {}, nb.get("map_sizes") or {}
    for k in sorted(set(om) | set(nm)):
        if om.get(k) != nm.get(k):
            lines.append(f"  map {k:24s} {om.get(k)} -> {nm.get(k)} entries")
    if lines:
        moved_any = True
        print("THE BRAIN")
        print("\n".join(lines))
        print("")

    oc, nc = old.get("champions") or {}, new["champions"]
    lines = []
    for lab in sorted(set(oc) | set(nc)):
        a, b = oc.get(lab), nc.get(lab)
        if b is None:
            lines.append(f"  {lab:22s} GONE")
            continue
        if a is None:
            lines.append(f"  {lab:22s} NEW   gen {b['generation']}  "
                         f"margin {b['margin']:+.3f} (low {b['margin_low']:+.3f})  "
                         f"{b['games']} games")
            continue
        if b["generation"] != a["generation"] or abs(b["margin"] - a["margin"]) > 1e-9:
            proof = "PROVED" if b["margin_low"] > 0.0 else "NOT PROVED"
            lines.append(f"  {lab:22s} gen {a['generation']} -> {b['generation']}  "
                         f"margin {a['margin']:+.3f} -> {b['margin']:+.3f} "
                         f"(low {b['margin_low']:+.3f}, {b['games']} games)  {proof}")
    if lines:
        moved_any = True
        print("CHAMPIONS  (a new one exists only because it beat the last)")
        print("\n".join(lines))
        print("")

    op, np_ = old.get("family_prior") or {}, new["family_prior"]
    lines = []
    for lab in sorted(set(op) | set(np_)):
        x, y = op.get(lab), np_.get(lab)
        if x is None:
            lines.append(f"  {lab:22s} NEW   {y:+.3f}")
        elif y is None:
            lines.append(f"  {lab:22s} REMOVED (was {x:+.3f})")
        elif abs(x - y) > 1e-9:
            lines.append(f"  {lab:22s} {x:+.3f} -> {y:+.3f}")
    if lines:
        moved_any = True
        print("PLAN VALUES  (written only when the interval cleared zero)")
        print("\n".join(lines))
        print("")

    if not moved_any:
        print("NOTHING HAS MOVED.")
        print("Not a clean bill of health -- it is the shape of a run that is")
        print("spinning. Things to look at, in order:")
        print("  * is a phase retrying one unit of work that never fits its")
        print("    budget? grep 'budget reached' fish_training/forever/progress.log")
        print("  * is a phase writing nothing? grep 'no measurement written'")
        print("  * is anything running at all? pgrep -fl train_forever")
    return moved_any


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true",
                    help="record the current state as the new baseline")
    ap.add_argument("--quiet-if-moving", action="store_true",
                    help="print only the one-line verdict when something moved")
    a = ap.parse_args()

    old: Dict[str, Any] = {}
    if os.path.exists(SNAP):
        try:
            with open(SNAP, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
    new = read_state()
    if a.quiet_if_moving:
        import io as _io
        import contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            moved = report(old, new)
        if moved:
            nc = new["champions"]
            proved = sum(1 for d in nc.values() if d["margin_low"] > 0.0)
            print(f"updating: yes · {len(nc)} champions ({proved} with a lower "
                  f"bound above zero) · {len(new['family_prior'])} measured plan "
                  f"values · brain weights drifting")
        else:
            print(buf.getvalue(), end="")
    else:
        report(old, new)

    if a.snapshot:
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        tmp = SNAP + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(new, fh, indent=1, sort_keys=True)
        os.replace(tmp, SNAP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
