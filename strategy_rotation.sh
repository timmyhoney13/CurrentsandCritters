#!/bin/bash
# Overnight strategy training: the main strategies until they are solid, then
# the combos built on them, round and round until stopped.
#
# ORDER MATTERS.
#   1. Mains that have never been crowned go first -- they have the most to
#      gain and nothing to build a combo from yet.
#   2. Mains that already have a champion are refined next.
#   3. Combos last. A combo's first champion is the average of its two
#      parents' champions, so it is only worth training once both halves have
#      actually learned something.
#
# Every strategy trains against the CURRENT best of all the others (each
# champion is read back off disk), so the table gets harder as the night goes
# on rather than staying at the untrained starting weights.
#
# Invertebrates only unlocks at 5+ players, so it trains at 6P; everything else
# at 4P, the most common table.
#
# Stop with:  pkill -f strategy_rotation
cd "$(dirname "$0")"
mkdir -p fish_training/evolve
LOG=fish_training/evolve/strategy_rotation.log

train() {   # train <strategy> <players> <generations> <phase>
  echo "[$(date '+%F %T')] $4 -> $1 at ${2}P, $3 generations" >> "$LOG"
  python3 bot_evolve.py --count "$2" --strategy "$1" --generations "$3" \
    --mutants 8 --screen-games 70 --confirm-games 250 \
    --max-confirm-games 750 --jobs 12 \
    >> "fish_training/evolve/console_$1.log" 2>&1
  echo "[$(date '+%F %T')] $4 -> $1 done (exit $?)" >> "$LOG"
}

echo "[$(date '+%F %T')] ===== overnight: mains, then combos =====" >> "$LOG"
CYCLE=0
while true; do
  CYCLE=$((CYCLE+1))
  echo "[$(date '+%F %T')] ===== cycle $CYCLE =====" >> "$LOG"

  # 1. mains with no champion yet
  for s in mammals birds_of_a_feather baitfish_barrage cephalopods coral goby_moon_shot; do
    [ -e "fish_training/evolve/champion_$s.json" ] || train "$s" 4 2 "main(new)"
  done
  [ -e "fish_training/evolve/champion_invertebrates.json" ] || train invertebrates 6 2 "main(new)"

  # 2. every main, refined
  for s in yellowfin_tuna crustaceans king_salmon mammals birds_of_a_feather \
           baitfish_barrage cephalopods coral goby_moon_shot; do
    train "$s" 4 2 "main"
  done
  train invertebrates 6 2 "main"

  # 3. combos, seeded from the mains above
  for s in birds_crustaceans coral_cephalopods birds_coral; do
    train "$s" 4 2 "combo"
  done
done
