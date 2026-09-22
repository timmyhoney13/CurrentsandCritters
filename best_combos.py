#!/usr/bin/env python3
"""What the combo strategies have learned, and which of them is strongest.

Run in the morning:  python3 best_combos.py
"""
import glob, io, json, os, sys
sys.path.insert(0, "/Users/timmyhoney1/cc-bots-overnight")
os.chdir("/Users/timmyhoney1/cc-bots-overnight")
import fish_game_all_in_one as fish
import reef_planner as rp
import bot_evolve as be

OUT = "fish_training/evolve"
rows = []
for lab in sorted(rp.COMBO_PARENTS):
    f = os.path.join(OUT, f"champion_{lab}.json")
    if not os.path.exists(f):
        continue
    d = json.load(open(f))
    rows.append((lab, d))

print("COMBO STRATEGIES — what training has proved about each\n")
print(f"{'combo':20s} {'built on':32s} {'gen':>4s} {'margin':>8s} {'95% low':>8s} {'wins':>8s} {'games':>6s}")
for lab, d in sorted(rows, key=lambda r: -float(r[1].get("margin_edge", 0))):
    par = " + ".join(rp.COMBO_PARENTS[lab])
    print(f"{lab:20s} {par:32s} {d.get('generation', 0):4d} "
          f"{d.get('margin_edge', 0):+8.3f} {d.get('margin_edge_low', 0):+8.3f} "
          f"{d.get('win_edge', 0):+8.4f} {d.get('games', 0):6d}")

print("\nWhat each one has learned about table size "
      "(these start at zero and only move on proof):")
for lab, d in rows:
    w = d["weights"]
    moved = {k: round(float(w.get(k, 0.0)), 3) for k in be.COUNT_FOCUS
             if abs(float(w.get(k, 0.0))) > 1e-9}
    print(f"   {lab:20s} {moved or 'nothing yet'}")

print("\nHow hard each has been pushed (from the rotation's own bookkeeping):")
try:
    st = json.load(open(os.path.join(OUT, "rotation_state.json")))
    for lab, _ in rows:
        c = st["cells"].get(lab)
        if c:
            print(f"   {lab:20s} tier {c['tier']} · {c['visits']} visit(s) · "
                  f"{c['promotions']} champion(s)"
                  + ("  SETTLED" if c.get("settled") else ""))
        else:
            print(f"   {lab:20s} not visited yet this run")
except Exception as exc:
    print("   (no state file:", exc, ")")

print("\nReminder: a combo's champion is seeded from its two parents', so a combo")
print("is only as good as the two plans under it. Parents' champions:")
for lab, _ in rows:
    for p in rp.COMBO_PARENTS[lab]:
        f = os.path.join(OUT, f"champion_{p}.json")
        if os.path.exists(f):
            pd = json.load(open(f))
            print(f"   {p:22s} gen {pd.get('generation',0)} "
                  f"margin {pd.get('margin_edge',0):+.3f}")
    break
