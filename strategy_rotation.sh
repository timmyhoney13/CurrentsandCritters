#!/bin/bash
# Train each of the ten strategies in turn, for as long as this runs.
#
# One strategy at a time, each keeping its own champion on disk, so a cycle
# picks up where the last left off. The seat under test is forced onto the
# strategy; the rest of the table plays their own plans, so what gets measured
# is "does this play the plan better", not "does this beat a mirror".
#
# Invertebrates only unlocks at 5+ players, so it trains at a big table.
# Everything else trains at 4P, the most common size.
#
# Stop with:  pkill -f strategy_rotation
cd "$(dirname "$0")"
mkdir -p fish_training/evolve
LOG=fish_training/evolve/strategy_rotation.log
echo "[$(date '+%F %T')] strategy rotation started" >> "$LOG"

CYCLE=0
while true; do
  CYCLE=$((CYCLE+1))
  echo "[$(date '+%F %T')] ===== cycle $CYCLE =====" >> "$LOG"
  for spec in "mammals:4" "yellowfin_tuna:4" "birds_of_a_feather:4" "crustaceans:4" \
              "baitfish_barrage:4" "cephalopods:4" "coral:4" "king_salmon:4" \
              "goby_moon_shot:4" "invertebrates:6"; do
    STRAT="${spec%%:*}"
    COUNT="${spec##*:}"
    echo "[$(date '+%F %T')] cycle $CYCLE -> $STRAT at ${COUNT}P" >> "$LOG"
    python3 bot_evolve.py --count "$COUNT" --strategy "$STRAT" --generations 2 \
      --mutants 8 --screen-games 70 --confirm-games 250 \
      --max-confirm-games 600 --jobs 12 \
      >> "fish_training/evolve/console_${STRAT}.log" 2>&1
    echo "[$(date '+%F %T')] cycle $CYCLE -> $STRAT done (exit $?)" >> "$LOG"
  done
done
