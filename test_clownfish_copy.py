"""Clownfish copies the Ocean it is attached to — scoring AND its ★.

The card reads "Copies the Ocean's ability this card is attached to", and it
means all of it. A Clownfish IS a second copy of its host Ocean:

  1. SCORING. A Clownfish on a Coral Reef makes your reefs count as one higher
     on the Coral Reef chart — and NOT a second reading of the chart. The chart
     is a single score for the whole collection (1=1, 2=4, 3=9, 4=16, 5=0,
     6+=35), so "counts as one, does not multiply" is a real distinction:
     3 reefs + a Clownfish must pay 16, not 9 twice and not 9+16. Same shape on
     a Kelp Forest, where the Clownfish both counts toward the "4 or more" and
     scores its own +5.

  2. THE ★. Two Oceans carry a star — Mangrove and Arctic Ocean, both "play
     again" — so a Clownfish attached to one of them has that star, and paying
     its cost with a card matching the CLOWNFISH's own symbol grants the extra
     play. This half did not work: has_star_ability() reads a card's own text,
     the Clownfish's carries no ★, so the star twin was never offered and a
     forced use_star=True was rejected outright with "has no STAR ability".

  3. THE COUNT. It counts as one more of that Ocean everywhere an Ocean is
     counted — "the most piers", "oceans you control" — not only on the two
     charts that happened to special-case it. What it cannot do is be a new
     ocean TYPE: it duplicates the name it sits on, so it can never be the
     missing eighth for the Mangrove's "all 8 oceans".

  4. THE ON-PLAY ABILITY. Playing a Clownfish onto a Deep Ocean or a Kelp
     Forest draws a card, because that is what those Oceans do. Their points
     are NOT re-added to the running tally when it fires — final_points already
     reads the host's text through the Clownfish, so counting it here too would
     show the Ocean twice on the live scoreboard.

Every part is driven through the real engine — real card data, real
legal_actions, real apply_action — because "the rulebook says so" is exactly
the thing that was already true while the game disagreed.

Run:  python3 test_clownfish_copy.py
"""

import importlib.util
import sys
from typing import Dict, List, Optional, Tuple

spec = importlib.util.spec_from_file_location("fish", "fish_game_all_in_one.py")
fish = importlib.util.module_from_spec(spec)
sys.modules["fish"] = fish
spec.loader.exec_module(fish)

CARD_DB: Dict[int, "fish.CardDef"] = fish.load_card_db()

FAILURES: List[str] = []
CHECKS = 0


def check(cond: bool, label: str) -> bool:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


# The two engine helpers this rule added. Wrapped so that on an engine without
# them the BEHAVIOURAL checks below still run and fail honestly, instead of the
# whole file dying on an AttributeError.
def star_for_play(gs, card, ocean_uid) -> bool:
    fn = getattr(fish, "has_star_ability_for_play", None)
    return bool(fn(gs, card, ocean_uid)) if fn else fish.has_star_ability(card)


def star_text_for_play(gs, card, ocean_uid) -> str:
    fn = getattr(fish, "star_text_for_play", None)
    if fn is None:
        _, star = fish.split_main_and_star(card.text)
        return star.strip()
    return str(fn(gs, card, ocean_uid) or "")


def uid_of(name: str) -> int:
    for uid in sorted(CARD_DB):
        if CARD_DB[uid].name.strip().lower() == name:
            return uid
    raise AssertionError("no card named " + name)


def uids_of(name: str, n: int) -> List[int]:
    out = [uid for uid in sorted(CARD_DB) if CARD_DB[uid].name.strip().lower() == name][:n]
    assert len(out) == n, f"only {len(out)} copies of {name}"
    return out


def new_match() -> Tuple["fish.GameState", "fish.MatchState", "fish.PlayerState"]:
    pair_map, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
    ms = fish.MatchState(pair_primary_to_faces=pair_map, face_to_primary=face_map)
    me = fish.PlayerState(name="Tester")
    opp = fish.PlayerState(name="Rival")
    gs = fish.GameState(card_db=CARD_DB, players=[me, opp], deck=[])
    gs.deck = [uid for uid in sorted(CARD_DB) if fish.canonical_entry_uid(ms, uid) == uid][:60]
    return gs, ms, me


def board_with(ocean_name: str, count: int, clownfish_on_first: bool):
    """`count` copies of one ocean, optionally with a Clownfish on the first."""
    gs, ms, me = new_match()
    oceans = uids_of(ocean_name, count)
    for uid in oceans:
        me.board_oceans.append(uid)
        me.ocean_slots[uid] = fish.OceanSlots()
    if clownfish_on_first:
        cf = uid_of("clownfish")
        direction = CARD_DB[cf].direction.strip().lower()
        me.ocean_slots[oceans[0]].slot(direction).append(cf)
    return gs, ms, me, oceans


# ── 1. Scoring: counts as one, does not multiply ───────────────────────────
print("\na Clownfish counts as one more of its Ocean")

# The chart printed on the card. Read once, for the whole collection.
REEF_CHART = {1: 1, 2: 4, 3: 9, 4: 16, 5: 0, 6: 35}

for n in (1, 2, 3, 4, 5, 6):
    gs, ms, me, _ = board_with("coral reef", n, False)
    plain = fish.final_points(gs, me)
    check(plain == REEF_CHART[n], f"{n} Coral Reef(s) alone score {REEF_CHART[n]} (got {plain})")

for n in (1, 2, 3, 4, 5):
    gs, ms, me, _ = board_with("coral reef", n, True)
    got = fish.final_points(gs, me)
    want = REEF_CHART[n + 1]
    check(got == want,
          f"{n} Coral Reef(s) + a Clownfish score as {n + 1} → {want} (got {got})")

# The distinction the rulebook now spells out: the Clownfish moves you UP the
# chart, it does not earn the chart again. If it multiplied, 3 reefs + a
# Clownfish would pay 9 + 16 (or 2x9), not 16.
gs, ms, me, _ = board_with("coral reef", 3, True)
tripled = fish.final_points(gs, me)
check(tripled == 16, f"3 reefs + Clownfish is one score of 16, not a second chart reading (got {tripled})")
check(tripled != 9 + 16 and tripled != 18,
      "the Coral Reef chart is not scored twice when a Clownfish joins the count")

# Kelp Forest is the same shape: the Clownfish counts toward "4 or more" and
# then scores its own +5 like any other Kelp Forest.
gs, ms, me, _ = board_with("kelp forest", 3, False)
check(fish.final_points(gs, me) == 0, "3 Kelp Forests are below the 4+ threshold")
gs, ms, me, _ = board_with("kelp forest", 3, True)
check(fish.final_points(gs, me) == 20, "3 Kelp Forests + a Clownfish reach 4+ and pay 4x5")
gs, ms, me, _ = board_with("kelp forest", 4, True)
check(fish.final_points(gs, me) == 25, "4 Kelp Forests + a Clownfish pay 5x5")

# A Clownfish on a starless, countless ocean must not invent points.
gs, ms, me, _ = board_with("tide pool", 2, False)
plain_tide = fish.final_points(gs, me)
gs, ms, me, _ = board_with("tide pool", 2, True)
check(fish.final_points(gs, me) >= plain_tide,
      "a Clownfish never costs points on an ocean with nothing to copy")


# ── 2. The ★ it borrows from Mangrove / Arctic Ocean ───────────────────────
print("\na Clownfish borrows its host Ocean's ★")

CLOWNFISH = uid_of("clownfish")
STARRED_OCEANS = ["mangrove", "arctic ocean"]
PLAIN_OCEANS = ["coral reef", "deep ocean", "kelp forest", "pier", "tide pool", "artificial reef"]

check(not fish.has_star_ability(CARD_DB[CLOWNFISH]),
      "the Clownfish card still carries no ★ of its own")
for name in STARRED_OCEANS:
    check(fish.has_star_ability(CARD_DB[uid_of(name)]), f"{name} has a ★ to copy")
for name in PLAIN_OCEANS:
    check(not fish.has_star_ability(CARD_DB[uid_of(name)]), f"{name} has no ★ to copy")


def play_clownfish(host_name: str, use_star: bool):
    """Attach a Clownfish to `host_name`, paying with a matching symbol."""
    gs, ms, me = new_match()
    host = uid_of(host_name)
    me.board_oceans.append(host)
    me.ocean_slots[host] = fish.OceanSlots()

    entry = fish.canonical_entry_uid(ms, CLOWNFISH)
    me.hand.append(entry)
    sym = fish.normalize_symbol(CARD_DB[CLOWNFISH].symbol)
    payments: List[int] = []
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid or uid == entry:
            continue
        if fish.entry_is_ocean(ms, gs, uid):
            continue
        if not fish.symbol_match_for_entry(ms, gs, uid, sym):
            continue
        payments.append(uid)
        if len(payments) == max(0, CARD_DB[CLOWNFISH].cost):
            break
    for uid in payments:
        me.hand.append(uid)

    offered = any(
        a.kind == "play_to_ocean" and a.face_uid == CLOWNFISH
        and a.ocean_uid == host and a.use_star
        for a in fish.legal_actions(gs, ms, me, include_draw=False)
    )
    action = fish.Action(kind="play_to_ocean", card_uid=entry, face_uid=CLOWNFISH,
                         ocean_uid=host, use_star=use_star, payment_uids=list(payments))
    ts = fish.TurnState()
    reasons: List[str] = []
    ok = fish.apply_action(gs, ms, me, action, ts, fish.choose_payment_ai, fail_reason=reasons)
    replays = fish._flag_count(me.flags.get("play_again", 0))
    return offered, ok, replays, gs, me, host


for name in STARRED_OCEANS:
    offered, ok, replays, gs, me, host = play_clownfish(name, use_star=True)
    check(offered, f"on a {name}, the ★ play is offered in legal_actions")
    check(ok, f"on a {name}, a ★ play is accepted")
    check(replays == 1, f"on a {name}, the ★ grants exactly one extra play (got {replays})")
    check(any("play again" in line.lower() and "clownfish" in line.lower() for line in gs.log),
          f"on a {name}, the log credits the Clownfish for the extra play")
    check(star_for_play(gs, CARD_DB[CLOWNFISH], host),
          f"has_star_ability_for_play says yes on a {name}")
    check(star_text_for_play(gs, CARD_DB[CLOWNFISH], host).lower() == "play again",
          f"the ★ text sent to the client on a {name} is 'play again'")

for name in PLAIN_OCEANS:
    offered, ok, replays, gs, me, host = play_clownfish(name, use_star=True)
    check(not offered, f"on a {name}, no ★ play is offered")
    check(not ok, f"on a {name}, a forced ★ play is rejected")
    check(replays == 0, f"on a {name}, nothing grants an extra play")
    check(not star_for_play(gs, CARD_DB[CLOWNFISH], host),
          f"has_star_ability_for_play says no on a {name}")
    check(star_text_for_play(gs, CARD_DB[CLOWNFISH], host) == "",
          f"no ★ text is offered for a {name}")

# The plain play must still work on a starred ocean — the ★ is opt-in.
offered, ok, replays, gs, me, host = play_clownfish("mangrove", use_star=False)
check(ok, "a Clownfish can still be played onto a Mangrove without its ★")
check(replays == 0, "a plain play on a Mangrove grants no extra play (the ★ is opt-in)")


# ── 3. Nothing else changed ────────────────────────────────────────────────
print("\nno other card borrows anything")

# Only the Clownfish copies. Any other animal on a Mangrove keeps its own ★
# (or its own lack of one).
gs, ms, me = new_match()
mangrove = uid_of("mangrove")
me.board_oceans.append(mangrove)
me.ocean_slots[mangrove] = fish.OceanSlots()
borrowers = 0
for uid in sorted(CARD_DB):
    card = CARD_DB[uid]
    if fish.is_ocean(card):
        continue
    if fish.has_star_ability(card):
        continue                      # it has its own ★, nothing to borrow
    if star_for_play(gs, card, mangrove):
        borrowers += 1
        check(card.name.strip().lower() == "clownfish",
              f"{card.name} must not borrow the Mangrove's ★")
check(borrowers > 0, "the Clownfish faces do borrow it (sanity: the check above can fail)")

# An ocean played on its own is unaffected by the new ocean_uid parameter.
for name in STARRED_OCEANS + PLAIN_OCEANS:
    card = CARD_DB[uid_of(name)]
    check(star_for_play(gs, card, None) == fish.has_star_ability(card),
          f"playing a {name} itself answers exactly as before")


# ── 4. It counts as one more of that Ocean, everywhere ────────────────────
print("\nit counts as one more of that Ocean wherever Oceans are counted")


def attach(player, ocean_uid: int, face_uid: int) -> None:
    player.ocean_slots[ocean_uid].slot(CARD_DB[face_uid].direction.strip().lower()).append(face_uid)


def put(gs, player, ocean_name: str, n: int) -> List[int]:
    out = []
    for uid in [u for u in sorted(CARD_DB)
                if CARD_DB[u].name.strip().lower() == ocean_name]:
        if uid in player.board_oceans:
            continue
        # never hand the same physical card to two players
        if any(uid in p.board_oceans for p in gs.players):
            continue
        player.board_oceans.append(uid)
        player.ocean_slots[uid] = fish.OceanSlots()
        out.append(uid)
        if len(out) == n:
            break
    return out


# "the most piers": one Pier plus a Clownfish must tie two Piers.
gs, ms, me = new_match()
opp = gs.players[1]
mine = put(gs, me, "pier", 1)
put(gs, opp, "pier", 2)
before_me = fish.final_points(gs, me)
attach(me, mine[0], CLOWNFISH)
after_me = fish.final_points(gs, me)
check(before_me < fish.final_points(gs, opp),
      "one Pier loses the count to two Piers")
check(after_me == fish.final_points(gs, opp),
      f"one Pier + a Clownfish ties two Piers ({after_me} vs {fish.final_points(gs, opp)})")

# "oceans you control": a Tide Pool pays +1 per every two.
gs, ms, me = new_match()
tps = put(gs, me, "tide pool", 2)
put(gs, me, "pier", 1)                       # 3 real oceans
three = fish.final_points(gs, me)
check(three == 6, f"2 Tide Pools + a Pier score 6 (1+1+4) (got {three})")
attach(me, tps[0], CLOWNFISH)                # → counts as a 4th ocean
four = fish.final_points(gs, me)
# 4 oceans controlled → each Tide Pool pays 4//2 = 2, and so does the Clownfish
# copying one: 2+2+4+2 = 10. Merely copying the ability without joining the
# count would pay 1+1+4+1 = 7, which is what it used to do.
check(four == 10,
      f"a Clownfish is a 4th ocean for '+1 per every two oceans' — 10, not 7 (got {four})")

# …but never a new ocean TYPE. Seven types plus a duplicate is still seven.
gs, ms, me = new_match()
mangrove_uid = None
for name in ["pier", "deep ocean", "coral reef", "mangrove",
             "artificial reef", "arctic ocean", "kelp forest"]:
    got = put(gs, me, name, 1)[0]
    if name == "mangrove":
        mangrove_uid = got
seven = fish.final_points(gs, me)
attach(me, mangrove_uid, CLOWNFISH)
check(fish.final_points(gs, me) == seven,
      "a Clownfish cannot be the eighth ocean TYPE for the Mangrove's +10")

# One source for all of it.
check(hasattr(fish, "effective_ocean_names"),
      "effective_ocean_names is the single source for every ocean count")
if hasattr(fish, "effective_ocean_names"):
    gs, ms, me = new_match()
    reefs = put(gs, me, "coral reef", 2)
    attach(me, reefs[0], CLOWNFISH)
    names = fish.effective_ocean_names(gs, me)
    check(sorted(names) == ["coral reef"] * 3,
          f"2 reefs + a Clownfish on one = 3 effective Coral Reefs (got {sorted(names)})")


# ── 5. The Ocean's own on-play ability fires too ──────────────────────────
print("\nplaying it fires the Ocean's own on-play ability")

DRAW_OCEANS = ["deep ocean", "kelp forest"]        # the only two that say "Draw one"


def play_clownfish_plain(host_name: str) -> int:
    """Attach a Clownfish to `host_name` and report how many cards it drew."""
    gs, ms, me = new_match()
    host = uid_of(host_name)
    me.board_oceans.append(host)
    me.ocean_slots[host] = fish.OceanSlots()
    entry = fish.canonical_entry_uid(ms, CLOWNFISH)
    me.hand.append(entry)
    for uid in sorted(CARD_DB):                     # one filler card to pay the cost
        if (fish.canonical_entry_uid(ms, uid) == uid and uid != entry
                and not fish.entry_is_ocean(ms, gs, uid)):
            me.hand.append(uid)
            break
    before = len(me.hand)
    ts = fish.TurnState()
    ok = fish.apply_action(gs, ms, me,
                           fish.Action(kind="play_to_ocean", card_uid=entry,
                                       face_uid=CLOWNFISH, ocean_uid=host),
                           ts, fish.choose_payment_ai)
    assert ok, f"could not play a Clownfish onto a {host_name}"
    return len(me.hand) - (before - 2)               # -1 played, -1 paid, +N drawn


for name in DRAW_OCEANS:
    check(CARD_DB[uid_of(name)].text.lower().count("draw one") == 1,
          f"{name} still says Draw one")
    check(play_clownfish_plain(name) == 1,
          f"a Clownfish onto a {name} draws the card that Ocean draws")
for name in [o for o in STARRED_OCEANS + PLAIN_OCEANS if o not in DRAW_OCEANS]:
    check(play_clownfish_plain(name) == 0,
          f"a Clownfish onto a {name} draws nothing (that Ocean draws nothing)")

# The copy must not double the Ocean's POINTS into the running tally, which is
# what the live scoreboard shows mid-game.
gs, ms, me = new_match()
deep = uid_of("deep ocean")
me.board_oceans.append(deep)
me.ocean_slots[deep] = fish.OceanSlots()
entry = fish.canonical_entry_uid(ms, CLOWNFISH)
me.hand.append(entry)
for uid in sorted(CARD_DB):
    if (fish.canonical_entry_uid(ms, uid) == uid and uid != entry
            and not fish.entry_is_ocean(ms, gs, uid)):
        me.hand.append(uid)
        break
running_before = me.score
fish.apply_action(gs, ms, me,
                  fish.Action(kind="play_to_ocean", card_uid=entry,
                              face_uid=CLOWNFISH, ocean_uid=deep),
                  fish.TurnState(), fish.choose_payment_ai)
check(me.score == running_before,
      f"the copied Ocean does not add its points to the running tally twice "
      f"({running_before} → {me.score})")


# ── 6. The scoreboard and its breakdown never disagree ────────────────────
print("\nthe per-card breakdown still matches the score")

import random as _random
mismatches = []
for seed in range(40):
    r = _random.Random(seed)
    gs, ms, me = new_match()
    opp = gs.players[1]
    for name in r.sample(["pier", "deep ocean", "coral reef", "mangrove",
                          "artificial reef", "arctic ocean", "kelp forest", "tide pool"],
                         r.randint(1, 5)):
        put(gs, me, name, 1)
    for name in r.sample(["pier", "coral reef", "kelp forest"], r.randint(0, 2)):
        put(gs, opp, name, 1)
    clowns = uids_of("clownfish", 3)
    for i, ocean in enumerate(list(me.board_oceans)):
        if r.random() < 0.5 and i < len(clowns):
            attach(me, ocean, clowns[i])
    scored = fish.final_points(gs, me)
    bd = fish.full_score_breakdown(gs, me)
    shown = int(bd.get("total", 0))
    rows = sum(int(row.get("total", 0)) for row in bd.get("card_rows", []))
    if rows != shown:
        mismatches.append(f"seed {seed}: breakdown total {shown} vs its own rows {rows}")
    if scored != shown:
        mismatches.append(f"seed {seed}: score {scored} vs breakdown {shown}")
check(not mismatches,
      "final_points and full_score_breakdown agree on every board" +
      (" — " + "; ".join(mismatches[:3]) if mismatches else ""))


print("\n" + "=" * 46)
if FAILURES:
    print(f"FAILED {len(FAILURES)} of {CHECKS}:")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print(f"clownfish copy checks: {CHECKS}")
print("clownfish OK")
