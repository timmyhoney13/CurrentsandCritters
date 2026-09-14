#!/bin/bash
# Strategy training for the grade ladder: every main strategy until it is
# solid, then the combos built on them, round and round until stopped.
#
# WHAT IT TRAINS ON. Every game is played through the deep chooser the live game
# uses (--chooser live), at grade B's search (--grade william_beebe). B is the
# lowest grade that plays with a strategy's trained weights, and every grade
# above it reuses those same weights with more rollouts on top -- so weights
# trained at B are the ones S++ plays with, and training at S++'s search would
# cost many times more for the same weights.
#
# ORDER MATTERS.
#   1. Mains with no champion yet go first -- they have the most to gain, and a
#      combo cannot be built on an untrained half.
#   2. Every main is refined.
#   3. Combos last: a combo's first champion is the average of its two parents'.
#
# Every strategy trains against the CURRENT champion of all the others (read
# back off disk), so the table gets harder as training goes on.
#
# Invertebrates only unlocks at 5+ players, so it trains at 6P; the rest at 4P.
#
# Budgets are sized for margin-based selection, which needs far fewer games than
# win-based did: 8 mutants screened on 40 deals, survivors confirmed in batches
# of 150 up to 450.
#
# Stop with:  pkill -f strategy_rotation
cd "$(dirname "$0")"
mkdir -p fish_training/evolve
LOG=fish_training/evolve/strategy_rotation.log

MAINS="mammals yellowfin_tuna birds_of_a_feather crustaceans baitfish_barrage cephalopods coral king_salmon goby_moon_shot"

train() {   # train <strategy> <players> <generations> <phase>
  echo "[$(date '+%F %T')] $4 -> $1 at ${2}P, $3 generations" >> "$LOG"
  python3 bot_evolve.py --count "$2" --strategy "$1" --generations "$3" \
    --chooser live --grade william_beebe \
    --mutants 8 --screen-games 40 --confirm-games 150 \
    --max-confirm-games 450 --jobs 12 \
    >> "fish_training/evolve/console_$1.log" 2>&1
  echo "[$(date '+%F %T')] $4 -> $1 done (exit $?)" >> "$LOG"
}

echo "[$(date '+%F %T')] ===== ladder training: live chooser, grade B =====" >> "$LOG"
CYCLE=0
while true; do
  CYCLE=$((CYCLE+1))
  echo "[$(date '+%F %T')] ===== cycle $CYCLE =====" >> "$LOG"

  # 1. mains with no champion yet
  for s in $MAINS; do
    [ -e "fish_training/evolve/champion_$s.json" ] || train "$s" 4 2 "main(new)"
  done
  [ -e "fish_training/evolve/champion_invertebrates.json" ] || train invertebrates 6 2 "main(new)"

  # 2. every main, refined
  for s in $MAINS; do
    train "$s" 4 2 "main"
  done
  train invertebrates 6 2 "main"

  # 3. combos, seeded from the mains above
  for s in birds_crustaceans coral_cephalopods birds_coral; do
    train "$s" 4 2 "combo"
  done
done
