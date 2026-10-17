#!/usr/bin/env python3
"""Are the games the trainer plays still real games, and is the deck intact?

    python3 check_games_valid.py              # audit, plus 12 fresh games
    python3 check_games_valid.py --games 0    # audit what is on disk only
    python3 check_games_valid.py --quiet      # one line, for a supervisor log

Exits non-zero if anything fails, so a training run can stop rather than spend
the night selecting bots on broken games.

WHY THIS IS SEPARATE FROM THE TEST SUITES. The suites check the engine as
written. This checks the engine as it is RUNNING, against the artifacts a long
training run keeps rewriting -- the brain, the champion files, the measured
plan priors. Training writes all three, every one of them feeds back into how
the next game is played, and a run is perfectly capable of grinding for hours
on games that are no longer valid while every log line looks healthy.

What it checks, in the order that a failure matters:

  1. THE DECK. 253 faces over 62 distinct cards, every uid distinct, every
     card carrying the fields the scorer reads. A deck that lost a card is a
     different game.
  2. THE SCORER. reef_planner.fast_points is a hand-written copy of
     final_points that caches what does not change, and the planner chooses
     every move by it. If the two ever disagree, grades A and up are
     optimising a game nobody is playing -- and they would still look fine,
     because they would be winning the game they think they are in.
  3. THE ARTIFACTS. The brain loads and passes its own validation, every
     champion's weights are finite, and the measured plan priors are inside
     the range the planner will accept.
  4. THE GAMES ON DISK. Rules errors, stalls, games that never reached END
     GAME, impossible scores, turn counts outside what the deck can produce.
  5. FRESH GAMES. A handful played now, at every table size in use, to confirm
     the engine is still producing valid games with the artifacts as they
     currently stand -- not as they stood when the suites last ran.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Tuple

os.environ.setdefault("PYTHONHASHSEED", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish
import reef_planner as rp

# What the deck is. Not read from the files it is checking, or the check would
# agree with any deck at all.
EXPECT_FACES = 253
EXPECT_NAMES = 62
# Turn counts the deck can produce. The game is deck-bound -- the END GAME card
# sits in the bottom of the deck and is DRAWN, so every table size runs a
# similar number of turns regardless of how many players share them. Measured
# 116 to 154 over 116 games; this is that with room either side, so a game
# ending in twenty turns or running to four hundred is caught.
TURNS_LO, TURNS_HI = 70, 320

FAILS: List[str] = []
WARNS: List[str] = []
NOTES: List[str] = []


def ok(cond: bool, what: str, detail: str = "", warn_only: bool = False) -> bool:
    if cond:
        NOTES.append(f"  ok    {what}")
        return True
    (WARNS if warn_only else FAILS).append(f"  {'WARN' if warn_only else 'FAIL'}  {what}"
                                           + (f"  -> {detail}" if detail else ""))
    return False


def check_deck() -> Dict[int, Any]:
    db = fish.load_card_db()
    ok(len(db) == EXPECT_FACES, f"the deck has {EXPECT_FACES} faces",
       f"found {len(db)}")
    names = {fish.card_name_lc(c) for c in db.values()}
    ok(len(names) == EXPECT_NAMES, f"over {EXPECT_NAMES} distinct cards",
       f"found {len(names)}")
    ok(len(set(db.keys())) == len(db), "every card uid is distinct")
    blank = [u for u, c in db.items() if not fish.card_name_lc(c)]
    ok(not blank, "no card has a blank name", f"{len(blank)} blank")
    nospecies = [fish.card_name_lc(c) for c in db.values()
                 if not fish.card_species_lc(c)]
    ok(not nospecies, "every card has a species", f"{sorted(set(nospecies))[:6]}")
    # Every card the scorer will be asked about must have a profile it can read.
    bad = []
    for u, c in db.items():
        try:
            fish._score_profile(c, None)
        except Exception as exc:
            bad.append((fish.card_name_lc(c), repr(exc)[:60]))
    ok(not bad, "every card has a score profile the scorer can read", f"{bad[:3]}")
    return db


def check_scorer(db: Dict[int, Any], games: int) -> None:
    """The planner's copy of the scorer must agree with the real one, exactly.

    Played with the planner itself, so the boards are the shape the planner
    actually builds rather than whatever a random policy leaves behind.
    """
    if games <= 0:
        NOTES.append("  --    scorer check skipped (--games 0)")
        return
    params = rp.params_for_grade("eugenie_clark") or dict(rp.PARAMS)
    params["temperature"] = 0.0

    def pol(gs, ms, p):
        return rp.choose_action(gs, ms, p, params=dict(params))

    mismatches: List[str] = []
    boards = 0
    for i in range(games):
        count = (3, 4, 6)[i % 3]
        random.seed(5000 + i)
        try:
            gs, _ms = fish.run_match(
                card_db=db, player_names=[f"P{j}" for j in range(count)],
                action_policies=[pol] * count, seed=5000 + i, max_turns=500,
                human_index=None, verbose=False, verbose_state=False,
                ai_difficulties=["eugenie_clark"] * count,
                online_weights=None, online_state=None, online_state_path=None)
        except Exception as exc:
            FAILS.append(f"  FAIL  a planner game at {count}P raised {exc!r}")
            continue
        for p in gs.players:
            others = rp.others_summary(gs, p)
            a = float(fish.final_points(gs, p))
            b = float(rp.fast_points(gs, p, others))
            boards += 1
            if abs(a - b) > 1e-6:
                mismatches.append(f"{count}P seat: final={a} fast={b}")
    ok(not mismatches,
       f"fast_points == final_points on all {boards} boards the planner built",
       f"{mismatches[:4]}")


def check_artifacts() -> None:
    try:
        brain = fish.load_brain(os.path.join(HERE, "fish_ai_brain.json"))
        ok(True, "the brain loads and passes its own validation")
    except Exception as exc:
        ok(False, "the brain loads and passes its own validation", repr(exc)[:160])
        return
    w = brain.get("weights") or {}
    bad = [k for k, v in w.items()
           if not isinstance(v, (int, float)) or v != v or abs(float(v)) > 1e6]
    ok(not bad, "every shared weight is a finite number", f"{bad[:6]}")
    for c, cell in sorted((brain.get("by_count") or {}).items()):
        cw = (cell or {}).get("weights") or {}
        bad = [k for k, v in cw.items()
               if not isinstance(v, (int, float)) or v != v or abs(float(v)) > 1e6]
        ok(not bad, f"every weight in the {c}P cell is finite", f"{bad[:6]}")
    for lab, vec in sorted((brain.get("by_strategy") or {}).items()):
        bad = [k for k, v in (vec or {}).items()
               if not isinstance(v, (int, float)) or v != v or abs(float(v)) > 1e6]
        ok(not bad, f"every weight in the {lab} vector is finite", f"{bad[:6]}")

    for path in sorted(glob.glob(os.path.join(HERE, "fish_training", "evolve",
                                              "champion_*.json"))):
        label = os.path.basename(path)[len("champion_"):-len(".json")]
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as exc:
            ok(False, f"champion_{label}.json parses", repr(exc)[:120])
            continue
        vals = (d.get("weights") or {})
        bad = [k for k, v in vals.items()
               if not isinstance(v, (int, float)) or v != v or abs(float(v)) > 1e6]
        ok(not bad, f"champion {label} holds finite weights", f"{bad[:6]}")

    # The measured plan priors have to be inside what the planner will take, or
    # the overlay silently clamps and the file stops describing the bots.
    measured = rp.FAMILY_PRIOR_MEASURED
    outside = {k: v for k, v in measured.items() if abs(float(v)) > 12.0}
    ok(not outside, "every measured plan prior is inside the planner's range",
       f"{outside}")
    unknown = [k for k in measured if k not in rp.STRATEGY_FAMILIES]
    ok(not unknown, "every measured plan prior names a real plan", f"{unknown}")
    if measured:
        NOTES.append("  --    measured plan priors: "
                     + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(measured.items())))


def check_logged_games() -> None:
    paths = sorted(set(glob.glob(os.path.join(HERE, "fish_training", "*", "run",
                                              "games.jsonl"))))
    if not paths:
        NOTES.append("  --    no game logs on disk yet")
        return
    total = 0
    problems: Dict[str, int] = {}
    turns_out: List[str] = []
    scores_bad: List[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    problems["unparseable"] = problems.get("unparseable", 0) + 1
                    continue
                total += 1
                if r.get("error"):
                    problems["error"] = problems.get("error", 0) + 1
                if int(r.get("rules_errors", 0) or 0) > 0:
                    problems["rules_errors"] = problems.get("rules_errors", 0) + 1
                if r.get("stalled"):
                    problems["stalled"] = problems.get("stalled", 0) + 1
                if not r.get("end_game_triggered"):
                    problems["no_natural_end"] = problems.get("no_natural_end", 0) + 1
                t = int(r.get("turns", 0) or 0)
                if not (TURNS_LO <= t <= TURNS_HI):
                    turns_out.append(f"{r.get('num_players')}P turns={t}")
                fs = r.get("final_scores") or []
                if not fs or min(fs) < 0 or max(fs) <= 0:
                    scores_bad.append(f"{r.get('num_players')}P scores={fs[:4]}")
                if int(r.get("num_players", 0) or 0) < 3:
                    problems["two_player_game"] = problems.get("two_player_game", 0) + 1
    NOTES.append(f"  --    {total} logged games audited")
    ok(not problems.get("unparseable"), "every logged game parses",
       f"{problems.get('unparseable')} bad lines")
    ok(not problems.get("error"), "no logged game crashed",
       f"{problems.get('error')} crashed")
    ok(not problems.get("rules_errors"), "no logged game broke a rule",
       f"{problems.get('rules_errors')} with rules errors")
    ok(not problems.get("stalled"), "no logged game stalled",
       f"{problems.get('stalled')} stalled")
    ok(not problems.get("no_natural_end"),
       "every logged game reached END GAME on its own",
       f"{problems.get('no_natural_end')} did not")
    ok(not turns_out, "every logged game ran a plausible number of turns",
       f"{turns_out[:4]}")
    ok(not scores_bad, "every logged game scored sanely", f"{scores_bad[:4]}")
    ok(not problems.get("two_player_game"),
       "no two-player games are being played",
       f"{problems.get('two_player_game')} found", warn_only=True)


def check_fresh_games(db: Dict[int, Any], games: int, counts: List[int]) -> None:
    """Play a few now, with the artifacts exactly as they currently stand."""
    if games <= 0:
        NOTES.append("  --    fresh games skipped (--games 0)")
        return
    weights = fish.stabilize_weights(dict(fish.default_weights()))
    bad: List[str] = []
    played = 0
    for i in range(games):
        count = counts[i % len(counts)]
        out: Dict[str, Any] = {}
        pol = fish._train_make_policy({"weights": weights}, epsilon=0.0)
        random.seed(9100 + i)
        try:
            gs, ms = fish.run_match(
                card_db=db, player_names=[f"AI{j}" for j in range(count)],
                action_policies=[pol] * count, seed=9100 + i, max_turns=500,
                human_index=None, verbose=False, verbose_state=False,
                online_weights=None, online_state=None, online_state_path=None,
                training_out=out)
            placement = fish.validate_end_game_placement(gs, ms, where=f"valid-{count}p")
        except Exception as exc:
            bad.append(f"{count}P raised {type(exc).__name__}: {exc}")
            continue
        played += 1
        if int(out.get("rules_errors", 0) or 0) > 0:
            bad.append(f"{count}P had {out['rules_errors']} rules errors")
        if placement:
            bad.append(f"{count}P END GAME placement: {placement[:2]}")
        fs = out.get("final_scores") or [float(fish.final_points(gs, p)) for p in gs.players]
        if not fs or max(fs) <= 0 or min(fs) < 0:
            bad.append(f"{count}P scored {fs[:4]}")
    ok(not bad, f"all {played} fresh games at {counts} were valid", f"{bad[:4]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=12,
                    help="fresh games to play (0 = audit what is on disk only)")
    ap.add_argument("--counts", default="3,4,6,8")
    ap.add_argument("--quiet", action="store_true", help="one line of verdict")
    a = ap.parse_args()
    counts = [int(c) for c in a.counts.split(",") if c.strip()]

    t0 = time.time()
    db = check_deck()
    check_artifacts()
    check_logged_games()
    check_fresh_games(db, a.games, counts)
    check_scorer(db, max(0, a.games // 2))

    if not a.quiet:
        print("GAME VALIDITY")
        print("=" * 66)
        for line in NOTES:
            print(line)
        for line in WARNS:
            print(line)
        for line in FAILS:
            print(line)
        print("=" * 66)
    good = len(NOTES)
    if FAILS:
        print(f"NOT VALID: {len(FAILS)} failure(s), {len(WARNS)} warning(s) "
              f"({time.time()-t0:.0f}s)")
        for line in FAILS:
            print(line)
        return 1
    print(f"valid: {good} checks passed, {len(WARNS)} warning(s) "
          f"({time.time()-t0:.0f}s)")
    if WARNS and a.quiet:
        for line in WARNS:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
