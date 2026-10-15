#!/usr/bin/env python3
"""Write what ab_plan.py measured about each plan into family_prior.json.

    python3 apply_family_prior.py fish_training/forever/plans_2026....json
    python3 apply_family_prior.py <file> --write

Only a plan whose 95% interval EXCLUDES ZERO is written, and the value written
is half what was measured. Both rules exist for the same reason: a measured
edge is always partly luck, and a paired deal in this game has a standard
deviation of about 35 points, so an unfiltered unshrunk number would feed the
planner mostly noise and call it knowledge.

The file is an overlay over reef_planner.FAMILY_PRIOR. Deleting it puts the
bots back exactly as they were, which is the property that makes writing it
automatically safe to do.

A plan already carrying a measured value is moved only PART of the way to the
new one (see BLEND), so a plan does not lurch between two noisy estimates on
consecutive nights; repeated agreeing measurements converge on the answer and
repeated disagreeing ones stay near the middle.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reef_planner as rp

PATH = os.path.join(HERE, "family_prior.json")
# Half of what was measured, for the same reason the September numbers were
# halved: the estimate is an estimate.
SHRINK = 0.5
# How far an existing measured value moves towards a new one.
BLEND = 0.5


def load() -> Dict[str, Any]:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("measurement", help="a JSON written by ab_plan.py --out")
    ap.add_argument("--write", action="store_true", help="actually update family_prior.json")
    ap.add_argument("--min-n", type=int, default=240,
                    help="ignore a plan measured on fewer paired deals than this")
    a = ap.parse_args()

    with open(a.measurement, encoding="utf-8") as f:
        m = json.load(f)
    if float(m.get("temperature", -1.0)) != 0.0:
        print(f"refusing: this measurement was taken at temperature "
              f"{m.get('temperature')}, not 0.0. Grade A's temperature is a ~18 "
              f"point handicap and a plan measured through it is not measured.")
        return 2

    doc = load()
    prior: Dict[str, float] = dict(doc.get("prior") or {})
    history = list(doc.get("history") or [])
    changes = []
    skipped = []
    for plan, cell in sorted((m.get("summary") or {}).items()):
        allc = cell.get("all") or {}
        n = int(allc.get("n", 0))
        mean = float(allc.get("mean", 0.0))
        lo, hi = [float(x) for x in (allc.get("ci") or [0.0, 0.0])]
        if n < a.min_n:
            skipped.append((plan, f"only {n} paired deals"))
            continue
        if lo <= 0.0 <= hi:
            skipped.append((plan, f"no proof ({mean:+.2f} [{lo:+.2f},{hi:+.2f}])"))
            continue
        target = mean * SHRINK
        if plan in prior:
            new = prior[plan] + BLEND * (target - prior[plan])
        else:
            new = target
        new = max(-12.0, min(12.0, new))
        was = prior.get(plan, rp.FAMILY_PRIOR.get(plan, 0.0))
        if abs(new - prior.get(plan, 1e9)) > 1e-6:
            changes.append((plan, was, new, mean, lo, hi, n))
            prior[plan] = new

    print(f"measurement: {a.measurement}")
    print(f"  grade {m.get('grade')} temperature {m.get('temperature')} "
          f"counts {m.get('counts')} · {m.get('when')}")
    if changes:
        print(f"\n{len(changes)} plan(s) measured with an interval clear of zero:")
        for plan, was, new, mean, lo, hi, n in changes:
            print(f"   {plan:22s} prior {was:+6.2f} -> {new:+6.2f}   "
                  f"(measured {mean:+.2f} [{lo:+.2f},{hi:+.2f}] over {n} deals)")
    else:
        print("\nnothing measured clear of zero; the prior is unchanged.")
    if skipped:
        print(f"\nleft alone ({len(skipped)}):")
        for plan, why in skipped:
            print(f"   {plan:22s} {why}")

    if changes and a.write:
        history.append({"when": time.strftime("%F %T"),
                        "measurement": os.path.basename(a.measurement),
                        "changed": {p: round(new, 4) for p, _w, new, *_ in changes}})
        out = {"written": time.strftime("%F %T"),
               "note": "Measured by ab_plan.py, shrunk by apply_family_prior.py. "
                       "An overlay over reef_planner.FAMILY_PRIOR -- delete this "
                       "file to put the bots back as they were.",
               "shrink": SHRINK, "blend": BLEND,
               "prior": {k: round(v, 4) for k, v in sorted(prior.items())},
               "history": history[-40:]}
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True)
        os.replace(tmp, PATH)
        print(f"\nwrote {os.path.relpath(PATH, HERE)}")
    elif changes:
        print("\nnothing written -- pass --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
