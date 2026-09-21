#!/usr/bin/env python3
"""Is the training healthy, and is what it has produced sound?

    python3 check_training.py

Everything the run writes is read back by something else later, usually in
another process, often hours afterwards. This checks each of those handoffs
rather than trusting them, because every one of them has been broken at least
once: weights written and silently dropped on read, a champion file read
half-written, two copies of a table that had to agree, a budget that did not
apply, a plan trained that no bot could choose.

Exit code 0 if everything is sound, 1 if anything is not.
"""
from __future__ import annotations

import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTHONHASHSEED", "0")

import fish_game_all_in_one as fish
import reef_planner as rp
import bot_evolve as be
import bot_training_rotation as rot

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fish_training", "evolve")
PASS = FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n          {extra}" if extra else ""))


def section(t):
    print(f"\n{t}")


# ── the brain ───────────────────────────────────────────────────────────────
section("the brain the game reads")
brain = None
try:
    brain = fish.load_brain(fish.BRAIN_PATH)
    check(True, "fish_ai_brain.json parses and loads")
except Exception as exc:
    check(False, "fish_ai_brain.json parses and loads", str(exc)[:200])

if brain is not None:
    raw = json.load(open(fish.BRAIN_PATH))
    dropped = sorted(k for k in raw if k not in brain)
    check(not dropped, "load_brain keeps every key the file holds",
          f"silently dropped: {dropped}")

    by = brain.get("by_strategy") or {}
    check(bool(by), "the brain carries per-strategy weights at all",
          "by_strategy is empty: every graded bot falls back to the shared vector")
    bad = {k: v for k, v in by.items()
           if not isinstance(v, dict) or
           any(not isinstance(x, (int, float)) for x in v.values())}
    check(not bad, "every strategy vector is numbers all the way down", f"{sorted(bad)}")
    unbounded = []
    for lab, vec in by.items():
        fixed = fish.stabilize_weights(dict(vec))
        unbounded += [f"{lab}.{k}" for k in vec
                      if abs(float(vec[k]) - float(fixed[k])) > 1e-9]
    check(not unbounded, "no weight sits outside the range it is allowed",
          f"{unbounded[:6]}")
    nan = [f"{lab}.{k}" for lab, vec in by.items() for k, v in vec.items()
           if v != v or abs(float(v)) == float("inf")]
    check(not nan, "no weight is NaN or infinite", f"{nan[:6]}")

# ── the champions on disk ───────────────────────────────────────────────────
section("the champion files")
champs = sorted(glob.glob(os.path.join(OUT, "champion_*.json")))
check(bool(champs), "there are champion files to check")
broken, missing_keys = [], []
for f in champs:
    lab = os.path.basename(f)[len("champion_"):-len(".json")]
    try:
        d = json.load(open(f))
        w = d["weights"]
        assert isinstance(w, dict) and w
    except Exception as exc:
        broken.append(f"{lab}: {exc}")
        continue
    if lab == "planner":
        stray = [k for k in w if k not in rp.TUNABLE_BOUNDS]
        out_of = [k for k, v in w.items()
                  if k in rp.TUNABLE_BOUNDS
                  and not (rp.TUNABLE_BOUNDS[k][0] - 1e-9 <= float(v)
                           <= rp.TUNABLE_BOUNDS[k][1] + 1e-9)]
        if stray or out_of:
            missing_keys.append(f"planner: stray={stray} out-of-range={out_of}")
    else:
        # A champion written before a weight existed simply does not mention it,
        # and that is not damage: stabilize_weights fills anything missing from
        # the defaults on the way in, which is the same path promote and the
        # trainer both use. What matters is that the file loads into a COMPLETE
        # vector that is in range -- so check that, not the raw key list.
        filled = fish.stabilize_weights(dict(w))
        gaps = [k for k in fish.default_weights() if k not in filled]
        out_of = [k for k, v in w.items()
                  if isinstance(v, (int, float)) and k in filled
                  and abs(float(v) - float(filled[k])) > 1e-9]
        if gaps or out_of:
            missing_keys.append(f"{lab}: gaps={gaps[:3]} out-of-range={out_of[:3]}")
check(not broken, "every champion file parses and has weights", "; ".join(broken[:3]))
check(not missing_keys, "every champion loads into a complete, in-range vector",
      "; ".join(missing_keys[:3]))

# Informational, not a fault: which plans have not yet had the table-size
# weights moved off zero. Early in a run that is most of them.
_untrained = []
for f in champs:
    lab = os.path.basename(f)[len("champion_"):-len(".json")]
    if lab == "planner":
        continue
    w = json.load(open(f))["weights"]
    if all(abs(float(w.get(k, 0.0))) < 1e-9 for k in be.COUNT_FOCUS):
        _untrained.append(lab)
print(f"  --    table-size weights still at zero for {len(_untrained)} of "
      f"{len(champs) - 1} plans" + (f": {', '.join(_untrained[:5])}"
      + ("..." if len(_untrained) > 5 else "") if _untrained else ""))

# ── is it actually reaching the game? ───────────────────────────────────────
section("is what was trained reaching the game?")
if brain is not None:
    by = brain.get("by_strategy") or {}
    stale = []
    for f in champs:
        lab = os.path.basename(f)[len("champion_"):-len(".json")]
        if lab in ("planner",) or lab not in by:
            continue
        w = json.load(open(f))["weights"]
        live = by[lab]
        drift = [k for k in w if abs(float(w[k]) - float(live.get(k, 0.0))) > 1e-6]
        if drift:
            stale.append(f"{lab} ({len(drift)} weights)")
    check(not stale, "the brain matches every champion on disk",
          f"not promoted yet: {stale} — run promote_champions.py --write")

    trained = {os.path.basename(f)[9:-5] for f in champs} - {"planner"}
    check(trained <= set(by), "every trained plan has a vector in the brain",
          f"missing: {sorted(trained - set(by))}")

tuned = os.path.join(OUT, "champion_planner.json")
check(os.path.exists(tuned), "the planner's knobs file exists for the image to carry")
if os.path.exists(tuned):
    applied = rp.load_tuned_params(tuned, into=dict(rp.PARAMS))
    check(True, f"the planner reads it ({len(applied)} knob(s) differ from shipped)")

# ── the tables that have to agree ───────────────────────────────────────────
section("the tables two modules both rely on")
check(be.PLANNER_BOUNDS == dict(rp.TUNABLE_BOUNDS),
      "the tuner and the planner agree on every knob and its range")
check(be.COMBO_PARENTS == dict(rp.COMBO_PARENTS),
      "the trainer and the planner agree on what a combo is made of")
check(set(rot.MAINS + rot.COMBOS) == set(rp.STRATEGY_FAMILIES),
      "the rotation trains exactly the plans a bot can commit to",
      f"difference: {sorted(set(rot.MAINS + rot.COMBOS) ^ set(rp.STRATEGY_FAMILIES))}")
for c in rot.COMBOS:
    check(rp.family_accepts(c) == rp.COMBO_PARENTS[c],
          f"{c} counts its parents' cards as its own")

# ── the run's own bookkeeping ───────────────────────────────────────────────
section("the run's bookkeeping")
sp = os.path.join(OUT, "rotation_state.json")
if os.path.exists(sp):
    try:
        st = json.load(open(sp))
        check(isinstance(st.get("cells"), dict), "the state file parses and has cells")
        order = rot.interleave_planner(rot.MAINS + rot.COMBOS)
        nxt = int(st.get("next_index", 0) or 0)
        check(0 <= nxt <= len(order), "the resume point is inside the running order",
              f"{nxt} of {len(order)}")
        want = st.get("next_cell", "")
        check(not want or want in order, "the resume cell is one that still exists",
              f"{want!r}")
        bad_t = {k: v.get("tier") for k, v in st["cells"].items()
                 if not (0 <= int(v.get("tier", 0)) <= rot.SETTLED_TIER)}
        check(not bad_t, "every cell's tier is on the ladder", f"{bad_t}")
    except Exception as exc:
        check(False, "the state file parses", str(exc)[:200])
else:
    print("  --    no state file yet (the run has not finished a cell)")

# ── does a game still play? ─────────────────────────────────────────────────
section("do the bots still play a whole game?")
try:
    db = fish.load_card_db()
    for count in (2, 6):
        random.seed(9)
        maps = fish._train_policy_maps_from_cbrain(fish.get_count_brain(brain, count))
        pol = fish._train_make_policy(maps, epsilon=0.0)
        gs, _ = fish.run_match(card_db=db, player_names=[f"P{i}" for i in range(count)],
            action_policies=[pol]*count, seed=9, max_turns=500, human_index=None,
            verbose=False, verbose_state=False,
            ai_difficulties=[fish.DEFAULT_BOT_GRADE]*count,
            online_weights=None, online_state=None, online_state_path=None)
        pts = [float(fish.final_points(gs, p)) for p in gs.players]
        check(max(pts) > 0, f"a {count}P game finishes with a real score", f"{pts}")
except Exception as exc:
    check(False, "a game plays end to end", str(exc)[:300])

print(f"\n{'=' * 52}\n{PASS} sound, {FAIL} not")
raise SystemExit(1 if FAIL else 0)
