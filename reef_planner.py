"""The Reef Planner: a bot that plans the board it will finish with.

Every point in Currents and Critters is scored off the FINAL board, so the only
question a move has to answer is "how good is the board I can still build from
here?". The weighted chooser in fish_game_all_in_one never asks it. It adds up
hints about a move (is it a star, does it stack, does the species match), so it
cannot see that a third cephalopod is worth nine points and a second one
nothing, or that a Penguin in hand is worth more with every bird that lands.
That is where "no synergy, no rhythm" came from.

The planner asks it directly, for every legal move:

  1. Play the move for real on the live state, inside a transaction, in a world
     where the cards this player cannot see (the deck and the other hands) have
     been reshuffled. It never peeks at the real deck.
  2. Finish the turn: a play again, a free mammal, a Reef Trigger dump or the
     second draw is searched as part of the same move.
  3. Value the position the turn ends in as
        the exact score of the board now (final_points)
      + the best plan it can still complete in the turns it has left, built
        from its hand AND from the cards that world says it will draw: which
        cards go where, what they cost, which cards pay for them, how many
        turns that takes, whether a star refunds the play
      - what the cards it fed the Pool are worth to the players after it.

The plan is built by placing cards on the real board and scoring it with the
real scoring function, so every rule the scorer knows (charts, thresholds,
per-species counts, shared-ocean bonuses, full oceans, the Coral Reef chart's
dead five) is understood without being written down twice. Sets that only pay
together (three cephalopods, four Kelp Forests, a Razorbill pair, a baitfish
run) are tried as bundles, because one at a time each of them scores nothing.
Because the plan includes the cards still to come, holding the first Mandarin
Goby is worth something: the world says how often the rest turn up.
"""
from __future__ import annotations

import json
import os
import random
from time import monotonic as _monotonic
from typing import Dict, List, Optional, Tuple

import fish_game_all_in_one as fish
from fish_game_all_in_one import (
    Action, GameState, MatchState, OceanSlots, PlayerState, TurnState,
    HAND_LIMIT, add_to_pool, build_non_ocean_pair_maps, can_attach_to_ocean,
    capture_action_tx, card_direction_lc, card_name_lc, card_species_lc, clone_action,
    consume_replay_actions, entry_faces, final_points, has_multi_play_window, is_ocean,
    legal_actions, normalize_symbol, pending_replay_actions, restore_action_tx,
    split_main_and_star, symbol_match_for_entry, _apply_action_uncommitted,
)

PLANNER_NAME = "reef"

PARAMS: Dict[str, float] = {
    # Shadow price of a turn, in points: a card joins the plan when it pays more
    # than the turns it takes. With adaptive_turn_value on, this is only the
    # ceiling; the price is measured each move (see measure_turn_value).
    "turn_value": 3.0,
    "adaptive_turn_value": 0.0,
    "turn_value_floor": 0.5,
    # Deck cards drawn per player-turn, before the table has shown its pace.
    "deck_rate_prior": 0.9,
    # A card fed to the Pool counts against us at this share of the best that
    # any opponent could score with it.
    "denial": 0.0,
    # Only the part of a threat above this many points counts for denial.
    "denial_threshold": 0.0,
    # A planned card that draws pulls that many future cards into the plan.
    "engine_draws": 0.0,
    # The best this-many Pool cards head the plan's future draws.
    "pool_in_stream": 0,
    # A card drawn by an ability, relative to a card drawn with a turn.
    "draw_card_value": 1.0,
    # Share of the turns the hand cannot use that the plan spends drawing.
    "draw_frac": 0.5,
    # Most future cards the plan may draw into.
    "stream_max": 14,
    # Deepest chain of follow-up plays searched inside one turn, how many
    # follow-ups each link keeps, and a cap on positions valued per move.
    "max_chain_depth": 4,
    "chain_beam": 5,
    "node_budget": 60,
    # Top-level plays searched (after a cheap pre-rank), first draws searched
    # through their second draw, and pool picks carried into a second draw.
    "top_width": 12,
    "first_draw_width": 3,
    # Pool picks valued as a first draw at all (after the cheap pre-rank).
    "pool_pick_width": 4,
    "second_draw_width": 4,
    # Candidates re-scored per step of the plan (lazy greedy).
    "plan_rescore": 3,
    # Worlds (reshuffles of the unseen cards) each move is played in, and the
    # total worlds the leading confirm_top moves are re-judged in (0 = off).
    "worlds": 1,
    "confirm_worlds": 0,
    "confirm_top": 4,
    # Points that are only in the plan count at this share of points already on
    # the board: the plan can be cut short, the Pool raided, the draws miss.
    "plan_discount": 1.0,
    # Every card of the bot's own strategy, on the board or in the plan, is
    # worth this much more to it than its printed points. This is what makes a
    # bot a Coral bot rather than a points bot that sometimes plays coral: it
    # collects its pieces, keeps them out of its payments, takes them from the
    # Pool. Kept small, so real points still decide close calls.
    "loyalty": 1.5,
    # Worlds the opening choice of strategy is made over.
    "family_worlds": 4,
    # Another bot at the table already on a strategy makes it look this much
    # worse, per bot, when choosing one: two Cephalopod players starve each other.
    "crowding": 0.25,
    # ...and this many points per bot already on it, however strong the hand.
    "crowding_points": 0.0,
    # How much the measured priors below count when choosing a plan.
    "family_prior_weight": 0.0,
    # Re-score every available card once before the plan stops or draws more.
    "final_sweep": 0.0,
    # Weight each planned card by the chance the game lasts long enough to play
    # it. END GAME is somewhere in the bottom fifteen; near the end that is the
    # difference between one turn left and four.
    "survival": 0.0,
    # A deck draw is valued on this many different top cards, not one: a single
    # imagined card is the loudest noise in a draw decision.
    "deck_samples": 1,
    # How much the strongest opponent's projected finish counts against us.
    # The game is won on finishing order, not points, and the length of the
    # game is the one thing every player's plays change for everybody: a
    # leader wants the deck gone, a player behind wants it to last.
    "rival_weight": 0.0,

    # ── What the table size is worth ────────────────────────────────────────
    # Six of the knobs above answer questions whose answer depends on how many
    # people are sitting at the table, and until now they gave the same answer
    # at every size. Feeding the Pool costs you one opponent's pick at 2P and
    # five at 6P. A plan that needs four more turns is ordinary at 2P and
    # fantasy at 6P, where the same deck is drained three times as fast. Coming
    # second means nothing at either, but at 2P there is exactly one player to
    # beat and at 6P the best of five is mostly whoever got the luckiest deal.
    #
    # Each of these is added to its knob, times how far the table is from the
    # 4P the planner was tuned at (see params_for_table). They start at zero, so
    # a 4P table plays exactly as it did before, and so does every other size
    # until training has shown -- on paired deals at each size -- that moving
    # one of them wins games. That is what makes them safe to add switched off
    # rather than guessed at.
    "denial_per_rival": 0.0,
    "rival_weight_per_rival": 0.0,
    "plan_discount_per_rival": 0.0,
    "turn_value_per_rival": 0.0,
    "survival_per_rival": 0.0,
    "crowding_per_rival": 0.0,
}

# knob -> the per-rival term that shifts it, and the range the result is held to.
COUNT_SHAPED: Dict[str, Tuple[str, float, float]] = {
    "denial":        ("denial_per_rival",        0.0, 2.0),
    "rival_weight":  ("rival_weight_per_rival",  0.0, 1.5),
    "plan_discount": ("plan_discount_per_rival", 0.2, 1.2),
    "turn_value":    ("turn_value_per_rival",    0.5, 9.0),
    "survival":      ("survival_per_rival",      0.0, 1.0),
    "crowding":      ("crowding_per_rival",      0.0, 1.5),
}

# Every knob a tuning run is allowed to move, and the range it is held to. The
# search widths are absent on purpose: those are what separate one planner grade
# from the next, and buying strength with more search is not learning anything.
TUNABLE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "turn_value": (0.5, 9.0), "plan_discount": (0.2, 1.2), "loyalty": (0.0, 6.0),
    "crowding": (0.0, 1.5), "crowding_points": (0.0, 8.0), "denial": (0.0, 2.0),
    "denial_threshold": (0.0, 8.0), "rival_weight": (0.0, 1.5), "survival": (0.0, 1.0),
    "draw_frac": (0.0, 1.0), "draw_card_value": (0.2, 2.5), "deck_rate_prior": (0.4, 2.0),
    "engine_draws": (0.0, 3.0), "family_prior_weight": (0.0, 3.0), "final_sweep": (0.0, 1.0),
    "adaptive_turn_value": (0.0, 1.0), "turn_value_floor": (0.1, 3.0),
    "denial_per_rival": (-1.5, 1.5), "rival_weight_per_rival": (-1.5, 1.5),
    "plan_discount_per_rival": (-0.8, 0.8), "turn_value_per_rival": (-4.0, 4.0),
    "survival_per_rival": (-1.0, 1.0), "crowding_per_rival": (-1.0, 1.0),
}

# The table size the planner's numbers were measured at. A table this size gets
# no correction at all, so `params_for_table` is the identity there.
TUNED_AT_PLAYERS = 4.0


def params_for_table(params: Dict[str, float], n_players: int) -> Dict[str, float]:
    """`params` as they apply at a table of `n_players`.

    Returns the same dict object when nothing would change, which is the common
    case (a 4P table, or every per-rival term still at zero), so this costs
    nothing on the path it is called from once per move."""
    rivals = (float(max(1, n_players)) - TUNED_AT_PLAYERS) / 3.0
    if rivals == 0.0:
        return params
    out: Optional[Dict[str, float]] = None
    for knob, (per, lo, hi) in COUNT_SHAPED.items():
        step = float(params.get(per, 0.0))
        if step == 0.0:
            continue
        v = float(params.get(knob, 0.0)) + step * rivals
        v = lo if v < lo else (hi if v > hi else v)
        if out is None:
            out = dict(params)
        out[knob] = v
    return out if out is not None else params

# ── What training has proved ────────────────────────────────────────────────
# The knobs above are the planner as it was reasoned out. This is the planner as
# it has been MEASURED: bot_evolve.py --planner plays generations of paired
# deals at every table size and only ever writes this file when a challenger
# beat the settings in it on games it could not have won by luck. Reading it
# here is what lets a night of training actually reach the bots people play.
#
# It is deliberately timid about what it will accept. Only knobs the tuner is
# allowed to move are read, each one is clamped to that knob's range, and any
# problem at all -- no file, half-written file, a key that is not a number --
# leaves the planner exactly as this module wrote it. A tuning run can make the
# bots better; a damaged file cannot make them worse.
TUNED_PARAMS_PATH = os.environ.get(
    "FISH_PLANNER_TUNED",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "fish_training", "evolve", "champion_planner.json"),
)


def load_tuned_params(path: Optional[str] = None,
                      into: Optional[Dict[str, float]] = None) -> List[str]:
    """Fold the measured knobs into `into` (PARAMS by default). Returns the
    names it changed, which is what the tests read it for."""
    target = PARAMS if into is None else into
    try:
        with open(path or TUNED_PARAMS_PATH, "r", encoding="utf-8") as fh:
            found = json.load(fh)
    except Exception:
        return []
    knobs = found.get("weights") if isinstance(found, dict) else None
    if not isinstance(knobs, dict):
        return []
    changed: List[str] = []
    for k, v in knobs.items():
        k = str(k)
        if k not in TUNABLE_BOUNDS or not isinstance(v, (int, float)):
            continue
        lo, hi = TUNABLE_BOUNDS[k]
        v = float(v)
        v = lo if v < lo else (hi if v > hi else v)
        if v != float(target.get(k, 0.0)):
            target[k] = v
            changed.append(k)
    return changed


_TRAITS: Dict[int, Tuple] = {}
_DECK_TOTAL: Dict[int, Tuple] = {}
_NEG = float("-inf")


# ── Card traits ─────────────────────────────────────────────────────────────

def card_traits(card) -> Tuple:
    """(play_again_star, star_draws, main_draws, free_species, free_count,
    free_is_star, paid_chain), parsed once per card."""
    got = _TRAITS.get(card.uid)
    if got is not None:
        return got
    main, star = split_main_and_star(card.text)
    m = main.lower()
    s = star.lower()
    play_again = ("play again" in s) or ("go again" in s)
    star_draws = 0.0
    if "draw one for each yellowfin" in s:
        star_draws = -1.0          # resolved against the board
    elif "draw three" in s:
        star_draws = 3.0
    elif "draw two" in s or "draw 2" in s:
        star_draws = 2.0
    elif "draw one" in s:
        star_draws = 1.0
    main_draws = 0.0
    if "draw one" in m and "when" not in m and "for each" not in m:
        main_draws = 1.0
    if "draw two" in m or "draw 2" in m:
        main_draws += 2.0
    free_species, free_count, free_is_star = "", 0, True
    if "play any number of cephalopods for free" in s:
        free_species, free_count = "cephalopod", 99
    elif "play a free cephalopod" in s:
        free_species, free_count = "cephalopod", 1
    elif "play a free mammal" in s:
        free_species, free_count = "mammal", 1
    elif "play a free baitfish" in s:
        free_species, free_count = "baitfish", 1
    elif "play a free game fish" in s:
        free_species, free_count = "game fish", 1
    elif "free crustacean" in s:
        free_species, free_count = "crustacean", 1
    elif "free invertebrate" in s:
        free_species, free_count = "invertebrate", 1
    elif "play a free coral" in s:
        free_species, free_count = "coral", 1
    if "play any number of baitfish" in m:
        free_species, free_count, free_is_star = "baitfish", 99, False
    paid_chain = "play any number of cards by paying" in m
    got = (play_again, star_draws, main_draws, free_species, free_count, free_is_star, paid_chain)
    _TRAITS[card.uid] = got
    return got


# ── The clock ───────────────────────────────────────────────────────────────

def _deck_total(gs: GameState) -> int:
    got = _per_db(_DECK_TOTAL, gs)
    if got is None:
        _, face_to_primary = build_non_ocean_pair_maps(gs.card_db)
        got = sum(1 for uid in gs.card_db if face_to_primary.get(uid, uid) == uid)
        _store_per_db(_DECK_TOTAL, gs, got)
    return got


def note_turn(gs: GameState, ms: MatchState, player: PlayerState) -> None:
    """Remember whether END GAME was already out when this turn began: that is
    the difference between this being the last turn and there being one more.

    Also counts IDLE turns: turns that began with this player's board and the
    deck exactly as they were at the start of its previous turn. Only the deck
    ends the game, so a table that only trades cards with the Pool never ends;
    see candidates()."""
    key = [int(gs.round_count), int(gs.turn_index)]
    if player.flags.get("_planner_turn_key") != key:
        player.flags["_planner_turn_key"] = key
        player.flags["_planner_eg_at_start"] = bool(ms.end_game_triggered)
        progress = [len(gs.deck), sum(len(s.all_cards()) for s in player.ocean_slots.values())
                    + len(player.board_oceans)]
        if player.flags.get("_planner_progress") == progress:
            player.flags["_planner_idle"] = int(player.flags.get("_planner_idle", 0) or 0) + 1
        else:
            player.flags["_planner_idle"] = 0
        player.flags["_planner_progress"] = progress


def turns_left_after_turn(gs: GameState, ms: MatchState, player: PlayerState,
                          params: Optional[Dict[str, float]] = None) -> float:
    """Own turns this player still gets after the one in progress."""
    params = params or PARAMS
    if ms.end_game_triggered:
        return 0.0 if player.flags.get("_planner_eg_at_start") else 1.0
    n = max(1, len(gs.players))
    deck_len = len(gs.deck)
    # END GAME sits somewhere in the bottom fifteen, and nobody knows where.
    k = min(15, deck_len)
    before_end = max(0.0, deck_len - (k + 1) / 2.0)
    dealt = _deck_total(gs) - 8 * n
    drawn = max(0, dealt - deck_len)
    elapsed = gs.round_count * n + gs.turn_index + 1
    prior = float(params.get("deck_rate_prior", 0.9))
    # Never slower than half the usual pace: only the deck ends a game, and a
    # table that stops drawing from it would otherwise look like it had all
    # the time in the world.
    rate = max(0.5 * prior, (drawn + prior * 2 * n) / float(elapsed + 2 * n))
    return (before_end / rate) / n + 1.0


def turns_range_after_turn(gs: GameState, ms: MatchState, player: PlayerState,
                           params: Optional[Dict[str, float]] = None) -> Tuple[float, float]:
    """The fewest and the most own turns this player can still get after the
    one in progress. END GAME is somewhere in the bottom fifteen, and where
    exactly is the difference between one turn and four at the end of a game."""
    params = params or PARAMS
    if ms.end_game_triggered:
        x = 0.0 if player.flags.get("_planner_eg_at_start") else 1.0
        return x, x
    n = max(1, len(gs.players))
    deck_len = len(gs.deck)
    k = min(15, deck_len)
    dealt = _deck_total(gs) - 8 * n
    drawn = max(0, dealt - deck_len)
    elapsed = gs.round_count * n + gs.turn_index + 1
    prior = float(params.get("deck_rate_prior", 0.9))
    rate = max(0.5 * prior, (drawn + prior * 2 * n) / float(elapsed + 2 * n))
    lo = (max(0, deck_len - k) / rate) / n + 1.0
    hi = (max(0, deck_len - 1) / rate) / n + 1.0
    return lo, max(lo, hi)


def survival(horizon: Optional[Tuple[float, float]], t: float) -> float:
    """Chance the game still gives this player its t-th turn from now."""
    if horizon is None:
        return 1.0
    lo, hi = horizon
    if t <= lo:
        return 1.0
    if t > hi:
        return 0.0
    return (hi - t) / (hi - lo) if hi > lo else 1.0


# ── Worlds ──────────────────────────────────────────────────────────────────

def _unseen_entries(gs: GameState, ms: MatchState, player: PlayerState) -> List[int]:
    """Every card this player cannot see. Sorted, so the reshuffle that follows
    depends only on WHICH cards are hidden, never on the order the real deck is
    in: the planner cannot learn the deck by accident."""
    out: List[int] = [u for u in gs.deck if u != ms.end_game_uid]
    for other in gs.players:
        if other is not player:
            out.extend(u for u in other.hand if u != ms.end_game_uid)
    out.sort()
    return out


def world_deck(gs: GameState, ms: MatchState, player: PlayerState, rng: random.Random) -> List[int]:
    """A deck this player could be facing: the cards it cannot see, reshuffled,
    with END GAME (if it is still to come) somewhere in the bottom fifteen."""
    deck_len = len(gs.deck)
    unseen = _unseen_entries(gs, ms, player)
    rng.shuffle(unseen)
    has_end = ms.end_game_uid is not None and ms.end_game_uid in gs.deck
    deck = unseen[:max(0, deck_len - (1 if has_end else 0))]
    if has_end:
        pos = deck_len - rng.randint(1, max(1, min(15, deck_len)))
        deck.insert(max(0, min(len(deck), pos)), ms.end_game_uid)
    return deck


def world_stream(gs: GameState, ms: MatchState, deck: List[int], params: Dict[str, float]) -> List[int]:
    """The cards this player will draw into later, in that world. Skips the top
    of the deck (this turn's own draws come from there) and stops at END GAME,
    since nothing below it is ever drawn."""
    cap = int(params.get("stream_max", 14))
    out: List[int] = []
    for uid in deck[6:]:
        if uid == ms.end_game_uid:
            break
        out.append(uid)
        if len(out) >= cap:
            break
    return out


# ── Scoring, fast ───────────────────────────────────────────────────────────
# final_points, line for line, for a planner that calls it thousands of times a
# move. Two things make it slow for that job and neither changes during one
# decision: it re-derives every card's name, species and symbol, and it rescans
# every opponent's board to answer "who has the most Piers / Oceans / animals".
# Both are cached here. test_reef_planner.py holds this to final_points exactly.

_CARD_INFO: Dict[int, Tuple] = {}


def _per_db(cache: Dict[int, Tuple], gs: GameState):
    """The entry for this card database, or None. Entries hold their database,
    so an id can never be reused for a different one while it is cached."""
    got = cache.get(id(gs.card_db))
    if got is not None and got[0] is gs.card_db:
        return got[1]
    return None


def _store_per_db(cache: Dict[int, Tuple], gs: GameState, value) -> None:
    if len(cache) >= 4:
        cache.clear()
    cache[id(gs.card_db)] = (gs.card_db, value)


def _card_info(gs: GameState) -> Dict[int, Tuple]:
    """uid -> (name, species, is_animal, symbol, card, score profile, strategy)."""
    got = _per_db(_CARD_INFO, gs)
    if got is None:
        got = {}
        for uid, c in gs.card_db.items():
            name = card_name_lc(c)
            got[uid] = (name, card_species_lc(c), card_direction_lc(c) != "n/a",
                        normalize_symbol(c.symbol), c, fish._score_profile(c, None),
                        fish._STRATEGY_OF_NAME.get(name) or fish._STRATEGY_OF_SPECIES.get(card_species_lc(c)) or "")
        _store_per_db(_CARD_INFO, gs, got)
    return got


def _ocean_names(info: Dict[int, Tuple], player: PlayerState) -> List[str]:
    """effective_ocean_names: each Ocean, plus one more per Clownfish on it."""
    names: List[str] = []
    for ocean_uid in player.board_oceans:
        row = info.get(ocean_uid)
        if row is None:
            continue
        host = row[0]
        names.append(host)
        if row[2] and row[1] != "ocean":
            continue          # not an ocean card (effective_ocean_names skips clownfish copies then)
        slots = player.ocean_slots.get(ocean_uid)
        if not slots:
            continue
        for u in slots.all_cards():
            r = info.get(u)
            if r is not None and r[0] == "clownfish":
                names.append(host)
    return names


def others_summary(gs: GameState, player: PlayerState) -> Tuple:
    """(has_others, most oceans, most piers, most animals) among the OTHER
    players, which is all final_points ever asks about them."""
    info = _card_info(gs)
    most_oceans = most_piers = most_animals = 0
    has_others = False
    for other in gs.players:
        if other is player:
            continue
        has_others = True
        names = _ocean_names(info, other)
        most_oceans = max(most_oceans, len(names))
        most_piers = max(most_piers, sum(1 for n in names if n == "pier"))
        animals = 0
        for o in other.board_oceans:
            sl = other.ocean_slots.get(o)
            if sl:
                for u in sl.all_cards():
                    r = info.get(u)
                    if r is not None and r[2]:
                        animals += 1
        most_animals = max(most_animals, animals)
    return (has_others, most_oceans, most_piers, most_animals)


_profile = fish._score_profile
_threshold = fish._threshold_value


def fast_points(gs: GameState, player: PlayerState, others: Tuple,
                family: Optional[str] = None, bonus: float = 0.0) -> float:
    """final_points(gs, player), given others_summary(gs, player); plus `bonus`
    for every card of strategy `family` on the board when one is given."""
    info = _card_info(gs)
    db = gs.card_db
    board: List[Tuple[int, Tuple, int]] = []
    names: List[str] = []
    species: Dict[str, int] = {}
    name_count: Dict[str, int] = {}
    by_ocean: Dict[int, List[Tuple]] = {}
    animals = 0
    full = 0
    coral_on_reef = 0
    loyal = 0
    slots_map = player.ocean_slots
    for o in player.board_oceans:
        orow = info.get(o)
        if orow is None:
            continue
        board.append((o, orow, o))
        host = orow[0]
        names.append(host)
        name_count[host] = name_count.get(host, 0) + 1
        sl = slots_map.get(o)
        if not isinstance(sl, OceanSlots):
            continue
        host_is_ocean = (not orow[2]) or orow[1] == "ocean"
        reef = host == "coral reef"
        if sl.up and sl.down and sl.left and sl.right:
            full += 1
        same = None
        for lane in (sl.up, sl.down, sl.left, sl.right):
            for u in lane:
                row = info.get(u)
                if row is None:
                    continue
                board.append((u, row, o))
                n = row[0]
                name_count[n] = name_count.get(n, 0) + 1
                if row[2]:
                    animals += 1
                    sp = row[1]
                    species[sp] = species.get(sp, 0) + 1
                    if same is None:
                        same = []
                        by_ocean[o] = same
                    same.append(row)
                if reef and row[1] == "coral":
                    coral_on_reef += 1
                if host_is_ocean and n == "clownfish":
                    names.append(host)
                if family is not None and row[6] == family:
                    loyal += 1
    if not board:
        return 0

    ocean_count = len(names)
    has_others, o_oceans, o_piers, o_animals = others
    most_oceans = (ocean_count >= o_oceans) if has_others else True
    my_piers = names.count("pier")
    most_piers = (my_piers >= o_piers) if has_others else True
    most_animals = (animals >= o_animals) if has_others else True
    coral_reefs = names.count("coral reef")
    kelps = names.count("kelp forest")
    distinct_types = len(set(names))
    na_group = species.get("n/a", 0) + species.get("uncharted", 0) + species.get("crosscurrent", 0)
    ceph = species.get("cephalopod", 0)

    reef_table_done = False
    tables_done = None
    total = 0
    empty = ()
    for uid, row, ocean_uid in board:
        name = row[0]
        if name == "clownfish":
            host = db.get(ocean_uid)
            p = _profile(row[4], host)
        else:
            host = None
            p = row[5]
        if p.trivial and name != "coral reef":
            total += p.flat
            continue
        pts = p.flat
        same = by_ocean.get(ocean_uid, empty)
        if p.share_goliath and any(c[0] == "goliath grouper" for c in same):
            pts += 8
        if p.share_salmon and any(c[0] == "king salmon" for c in same):
            pts += 4
        if p.share_ceph is not None and any(c[1] == "cephalopod" for c in same):
            pts += p.share_ceph
        if p.share_bait is not None and any(c[1] in ("baitfish", "bait fish") for c in same):
            pts += p.share_bait
        if p.share_mahi and any(c[0] == "mahi mahi" for c in same):
            pts += 9
        if p.only_creature and len(same) == 1:
            pts += 10
        if p.ceph3 is not None and ceph >= 3:
            pts += p.ceph3
        if p.ceph4 is not None and ceph >= 4:
            pts += p.ceph4
        if p.most_oceans is not None and most_oceans:
            pts += p.most_oceans
        if p.most_animals and most_animals:
            pts += 4
        if p.all8 is not None and distinct_types >= 8:
            pts += p.all8
        if p.pier is not None:
            pts += p.pier if most_piers else 2
        if p.two_oceans:
            pts += ocean_count // 2
        if p.full_ocean:
            pts += 5 * full
        if p.per_attached:
            pts += 2 * len(same)
        if p.kelp4 and kelps >= 4:
            pts += 5
        if p.coral_reef_attached is not None:
            pts += p.coral_reef_attached * coral_on_reef
        for mult, spec in p.per_species:
            pts += mult * (na_group if spec is fish._NA_GROUP else species.get(spec, 0))
        for mult, nm in p.per_name:
            pts += mult * name_count.get(nm, 0)
        if p.match_symbol:
            sym = row[3]
            if sym not in ("", "n/a"):
                pts += 2 * sum(1 for u2, r2, _o in board if r2[2] and r2[3] == sym and u2 != uid)
        if p.uniq_symbol_ocean:
            syms = {c[3] for c in same}
            syms.discard("")
            syms.discard("n/a")
            pts += 2 * len(syms)
        if p.uniq_species_ocean:
            pts += 2 * len({c[1] for c in same if c[4].species.strip()})
        if tables_done is None:
            tables_done = set()
        if p.baitfish_chart:
            if "baitfish_species_chart" not in tables_done:
                tables_done.add("baitfish_species_chart")
                kinds = {r2[0] for _u2, r2, _o in board if r2[2] and r2[1] == "baitfish"}
                pts += _threshold(p.table_pairs, len(kinds))
        elif name == "coral reef":
            if not reef_table_done:
                pts += _threshold(p.table_pairs, coral_reefs)
                reef_table_done = True
        elif p.num_table:
            host_is_reef = name == "clownfish" and host is not None and card_name_lc(host) == "coral reef"
            if not host_is_reef and name not in tables_done:
                tables_done.add(name)
                pts += _threshold(p.table_pairs, name_count.get(name, 0))
        total += pts
    if family is not None and bonus:
        return total + bonus * loyal
    return total


# ── Strategies ──────────────────────────────────────────────────────────────
# The ten plans a bot can commit to (the strategy guide's animal plans; Oceans
# are the ground every one of them is built on, not a plan of their own), and
# which of them each card belongs to: the same map that names a finished board.

# What each plan has actually been worth to a planner that committed to it,
# in points against the table average, measured over 516 six-player planner
# seats (2026-09-15) and shrunk halfway to zero. The projection cannot see a
# plan's ceiling on its own: it plans with a dozen future cards, and a dozen
# random cards nearly always hold a bird and almost never the other three
# Mandarin Gobies. Measured, bots chose Birds 175 times (64 points, 13% wins)
# and Shooting the Moon 32 times (82 points, 38% wins).
FAMILY_PRIOR: Dict[str, float] = {
    "goby_moon_shot": 6.0, "baitfish_barrage": 2.0, "yellowfin_tuna": 1.5, "king_salmon": 1.5,
    "invertebrates": 1.5, "coral": 0.5, "mammals": -0.5, "cephalopods": -1.0,
    "birds_of_a_feather": -1.5, "crustaceans": -3.0,
}

STRATEGY_FAMILIES: Tuple[str, ...] = (
    "mammals", "yellowfin_tuna", "baitfish_barrage", "birds_of_a_feather", "crustaceans",
    "cephalopods", "coral", "king_salmon", "invertebrates", "goby_moon_shot",
)
_FAMILY_OF: Dict[int, Tuple] = {}


def family_of_uid(gs: GameState, uid: int) -> str:
    table = _per_db(_FAMILY_OF, gs)
    if table is None:
        table = {}
        for u, c in gs.card_db.items():
            table[u] = (fish._STRATEGY_OF_NAME.get(card_name_lc(c))
                        or fish._STRATEGY_OF_SPECIES.get(card_species_lc(c)) or "")
        _store_per_db(_FAMILY_OF, gs, table)
    return table.get(uid, "")


def family_cards_on_board(gs: GameState, player: PlayerState, family: str) -> int:
    n = 0
    for o in player.board_oceans:
        sl = player.ocean_slots.get(o)
        if sl:
            for u in sl.all_cards():
                if family_of_uid(gs, u) == family:
                    n += 1
    return n


def crowd_by_family(gs: GameState, player: PlayerState) -> Dict[str, int]:
    """How many opponents are already chasing each plan.

    Two Cephalopod players starve each other -- there are only so many
    cephalopods in the deck -- and that is true of an opponent whatever brain is
    deciding its moves.

    This used to count only opponents that were planners themselves
    (`other.flags["_planner"]`), which is a detail of how a seat is played
    leaking into a decision about the game. At a table of planners it read
    correctly; at every mixed table -- a planner against lower grades, which is
    most Casual tables -- it saw nobody and happily committed to the plan two
    opponents were already on. Every bot is assigned a family from its opening
    hand, so the flag is there to be read either way.

    Humans are never assigned one, so a person is never counted. That is the
    right answer and not a shortcoming: nothing here may see a person's plan.
    """
    crowd: Dict[str, int] = {}
    for other in gs.players:
        if other is player:
            continue
        fam = str(other.flags.get("_strategy_family", "") or "")
        if fam:
            crowd[fam] = crowd.get(fam, 0) + 1
    return crowd


def choose_family(gs: GameState, ms: MatchState, player: PlayerState, params: Dict[str, float],
                  rng: random.Random, worlds: int = 4) -> Tuple[str, Dict[str, float]]:
    """Commit to the plan this hand, and the cards still to come, can do most
    with, and that the rest of the table is not already chasing.

    Each plan is tried by planning the game as a bot loyal to it would (its
    cards worth a bonus while the plan is chosen) and counting the REAL points
    that plan scores, so a plan is picked for what it can actually earn, not
    for how many of its cards are in hand."""
    n_players = len(gs.players)
    allowed = [f for f in STRATEGY_FAMILIES
               if not (f == "invertebrates" and n_players < fish.INVERTEBRATE_MIN_PLAYERS)]
    crowd = crowd_by_family(gs, player)
    others = others_summary(gs, player)
    turns = turns_left_after_turn(gs, ms, player, params) + 1.0
    decks = [world_deck(gs, ms, player, rng) for _ in range(max(1, worlds))]
    streams = [world_stream(gs, ms, d, params) for d in decks]
    probe = dict(params)
    probe["loyalty"] = max(3.0, 2.0 * float(params.get("loyalty", 1.5)))
    results: Dict[str, float] = {}
    bonus = float(probe["loyalty"])
    for fam in allowed:
        earned = 0.0
        for stream in streams:
            score_biased = lambda pl, _f=fam: fast_points(gs, pl, others, _f, bonus)
            gains: Dict[int, float] = {}
            projection(gs, ms, player, turns, stream, probe, score=score_biased, gains_out=gains)
            # What this plan's own cards would really score, with the loyalty
            # bonus that got them chosen taken back out.
            earned += sum(g - bonus for face, g in gains.items() if family_of_uid(gs, face) == fam)
        value = earned / float(len(streams))
        prior_w = float(params.get("family_prior_weight", 0.0))
        if prior_w > 0.0:
            # A plan's ceiling only counts for a hand that can start it.
            held = sum(1 for e in player.hand
                       if any(family_of_uid(gs, f) == fam for f in entry_faces(ms, e)))
            value += prior_w * FAMILY_PRIOR.get(fam, 0.0) * min(1.0, float(held))
        value -= float(params.get("crowding", 0.25)) * abs(value) * crowd.get(fam, 0)
        value -= float(params.get("crowding_points", 0.0)) * crowd.get(fam, 0)
        results[fam] = value
    jitter = {f: rng.random() for f in results}
    best = max(results, key=lambda f: (results[f], jitter[f]))
    return best, results


def reconsider_family(gs: GameState, ms: MatchState, player: PlayerState, params: Dict[str, float]) -> None:
    """Once a turn, from the third round: if the board is plainly scoring as a
    different plan than the one committed to (half as much again, and by a real
    margin), that is the plan now. A good player commits, and a good player
    also notices when the pieces went somewhere else."""
    if gs.round_count < 3:
        return
    key = [int(gs.round_count), int(gs.turn_index)]
    if player.flags.get("_planner_reconsidered") == key:
        return
    player.flags["_planner_reconsidered"] = key
    try:
        by_family = fish.strategy_points_by_family(gs, player)
    except Exception:
        return
    by_family.pop("ocean_all_blue", None)
    allowed = {f for f in STRATEGY_FAMILIES
               if not (f == "invertebrates" and len(gs.players) < fish.INVERTEBRATE_MIN_PLAYERS)}
    by_family = {f: v for f, v in by_family.items() if f in allowed}
    if not by_family:
        return
    current = str(player.flags.get("_strategy_family", "") or "")
    top, top_pts = max(by_family.items(), key=lambda kv: kv[1])
    if top != current and top_pts >= 1.5 * by_family.get(current, 0.0) + float(params.get("switch_margin", 4.0)):
        player.flags["_strategy_family_prev"] = current
        player.flags["_strategy_family"] = top
        player.flags["_strategy_family_source"] = "planner_switch"


# ── Placing cards on the board, and taking them back off ────────────────────

def _ocean_sig(gs: GameState, player: PlayerState, ocean_uid: int) -> Tuple:
    slots = player.ocean_slots.get(ocean_uid)
    ocean = gs.card_db.get(ocean_uid)
    if slots is None or ocean is None:
        return (ocean_uid,)
    names = tuple(sorted(card_name_lc(gs.card_db[u]) for u in slots.all_cards() if u in gs.card_db))
    return (card_name_lc(ocean), bool(slots.up), bool(slots.down), bool(slots.left),
            bool(slots.right), names)


def _board_key(player: PlayerState) -> Tuple:
    return tuple((o, tuple(s.up), tuple(s.down), tuple(s.left), tuple(s.right))
                 for o, s in ((o, player.ocean_slots.get(o)) for o in player.board_oceans) if s is not None)


def _place(gs: GameState, player: PlayerState, face: int, ocean: Optional[int]) -> None:
    if ocean is None:
        player.board_oceans.append(face)
        player.ocean_slots[face] = OceanSlots()
    else:
        player.ocean_slots[ocean].slot(card_direction_lc(gs.card_db[face])).append(face)


def _unplace(gs: GameState, player: PlayerState, face: int, ocean: Optional[int]) -> None:
    if ocean is None:
        player.board_oceans.remove(face)
        player.ocean_slots.pop(face, None)
    else:
        lane = player.ocean_slots[ocean].slot(card_direction_lc(gs.card_db[face]))
        for i in range(len(lane) - 1, -1, -1):
            if lane[i] == face:
                lane.pop(i)
                break


def best_placement(gs: GameState, ms: MatchState, player: PlayerState, entry_uid: int,
                   cur_fp: int, only=None, score=None) -> Tuple[float, Optional[int], Optional[int]]:
    """Best (gain, face, ocean) for one card on the board as it stands; ocean is
    None for an Ocean card. `only(card)` limits which faces may be used, and
    `score(player)` is the scorer (final_points unless the caller has a faster
    exact one)."""
    score = score or (lambda pl: final_points(gs, pl))
    best = (_NEG, None, None)
    db = gs.card_db
    for face in entry_faces(ms, entry_uid):
        card = db.get(face)
        if card is None or (only is not None and not only(card)):
            continue
        if is_ocean(card):
            if face in player.ocean_slots:
                continue
            _place(gs, player, face, None)
            try:
                gain = score(player) - cur_fp
            finally:
                _unplace(gs, player, face, None)
            if gain > best[0]:
                best = (float(gain), face, None)
            continue
        direction = card_direction_lc(card)
        if direction not in ("up", "down", "left", "right"):
            continue
        seen = set()
        for ocean_uid in player.board_oceans:
            if not can_attach_to_ocean(gs, player, face, ocean_uid):
                continue
            sig = _ocean_sig(gs, player, ocean_uid)
            if sig in seen:
                continue
            seen.add(sig)
            lane = player.ocean_slots[ocean_uid].slot(direction)
            lane.append(face)
            try:
                gain = score(player) - cur_fp
            finally:
                lane.pop()
            if gain > best[0]:
                best = (float(gain), face, ocean_uid)
    return best


# ── The plan ────────────────────────────────────────────────────────────────

def plan_turns(gs: GameState, ms: MatchState, selected: List[Tuple[int, int]],
               avail: List[int], draw_turns: int, params: Dict[str, float]) -> float:
    """Turns a set of planned plays takes: the plays, less the ones a play again
    or a free play refunds, plus the draws needed to afford them and the draw
    turns spent reaching future cards."""
    if not selected:
        return float(draw_turns)
    info = _card_info(gs)
    chosen = {e for e, _f in selected}
    fodder_syms: Dict[str, int] = {}
    pairs = ms.pair_primary_to_faces
    for e in avail:
        if e in chosen:
            continue
        faces = pairs.get(e)
        for f in (faces if faces is not None else (e,)):
            row = info.get(f)
            if row is not None:
                fodder_syms[row[3]] = fodder_syms.get(row[3], 0) + 1
    species_count: Dict[str, int] = {}
    yellowfin = 0
    rows = []
    for _e, f in selected:
        row = info[f]
        rows.append(row)
        species_count[row[1]] = species_count.get(row[1], 0) + 1
        if row[0] == "yellowfin tuna":
            yellowfin += 1
    refunds = 0.0
    free_cap: Dict[str, int] = {}
    drawn = 0.0
    paid_chain = False
    cards_needed = 0.0
    for row in rows:
        c = row[4]
        play_again, star_draws, main_draws, free_sp, free_n, free_star, chain = card_traits(c)
        sym = row[3]
        star_ok = c.cost > 0 and sym not in ("", "n/a") and fodder_syms.get(sym, 0) > 0
        if play_again and star_ok:
            refunds += 1.0
        if free_sp and (star_ok or not free_star):
            free_cap[free_sp] = free_cap.get(free_sp, 0) + free_n
        if chain:
            paid_chain = True
        drawn += main_draws
        if star_ok:
            drawn += float(yellowfin) if star_draws < 0 else star_draws
        if c.cost > 0:
            cards_needed += float(c.cost)
    free_used = 0
    for sp, cap in free_cap.items():
        use = min(cap, species_count.get(sp, 0))
        if use > 0:
            free_used += use
            costs = sorted((row[4].cost for row in rows if row[1] == sp), reverse=True)
            cards_needed -= float(sum(costs[:use]))
    plays = float(len(selected)) - refunds - float(free_used)
    if paid_chain:
        plays -= min(2.0, max(0.0, plays - 1.0))
    if plays < 1.0:
        plays = 1.0
    if float(params.get("engine_draws", 0.0)) > 0.0:
        drawn = 0.0          # the drawn cards are already in `avail`, see projection()
    fodder = float(len(avail) - len(selected)) + drawn * float(params.get("draw_card_value", 1.0))
    deficit = (cards_needed if cards_needed > 0 else 0.0) - fodder
    return plays + (deficit / 2.0 if deficit > 0 else 0.0) + float(draw_turns)


_SET_CARD: Dict[int, bool] = {}


def _is_set_card(gs: GameState, ms: MatchState, entry_uid: int) -> bool:
    """Does any face of this card belong to a set that only pays in numbers?"""
    got = _SET_CARD.get(entry_uid)
    if got is None:
        got = False
        for f in entry_faces(ms, entry_uid):
            c = gs.card_db.get(f)
            if c is None:
                continue
            p = _profile(c, None)
            if (p.num_table or p.baitfish_chart or p.kelp4 or p.ceph3 is not None
                    or card_name_lc(c) == "coral reef" or card_species_lc(c) in ("cephalopod", "baitfish")):
                got = True
                break
        _SET_CARD[entry_uid] = got
    return got


def _best_bundle(gs, ms, player, remaining, selected, avail, draw_turns, cur_fp, t_used, turns, params, score):
    """Sets that only score together, tried as one: every group of two or more
    cards sharing a species or a name."""
    mu = float(params.get("turn_value", 1.6))
    db = gs.card_db
    groups: Dict[str, List[int]] = {}
    # Two kinds of set are worth more together than card by card:
    #   * sets that pay in numbers: a chart or threshold (gobies, mantis shrimp,
    #     razorbills, Coral Reef, Kelp Forest), three cephalopods, a baitfish run;
    #   * a payoff card with the cards it counts: an Emperor Penguin with birds,
    #     a California Gull with crustaceans, a Whale Shark with baitfish.
    # Everything else scores card by card, and the single-card plan finds it.
    payoff_filter: Dict[str, Tuple[int, frozenset, frozenset]] = {}
    faces_of: Dict[int, List] = {}
    for e in remaining:
        faces_of[e] = [db[f] for f in entry_faces(ms, e) if f in db]
    for e in remaining:
        for c in faces_of[e]:
            p = _profile(c, None)
            if p.num_table or p.kelp4 or card_name_lc(c) == "coral reef":
                groups.setdefault("n:" + card_name_lc(c), []).append(e)
            sp = card_species_lc(c)
            if sp in ("cephalopod", "baitfish") and not is_ocean(c):
                groups.setdefault("s:" + sp, []).append(e)
            wanted_species = {spec for _m, spec in p.per_species if spec is not fish._NA_GROUP}
            wanted_names = {nm for _m, nm in p.per_name}
            if wanted_species or wanted_names:
                members = [e]
                for other in remaining:
                    if other == e:
                        continue
                    if any((card_species_lc(oc) in wanted_species) or (card_name_lc(oc) in wanted_names)
                           for oc in faces_of[other]):
                        members.append(other)
                if len(members) >= 2:
                    key = "m:%d:%d" % (e, c.uid)
                    groups[key] = members
                    payoff_filter[key] = (c.uid, frozenset(wanted_species), frozenset(wanted_names))
    for k in list(groups):
        seen_e = []
        for e in groups[k]:
            if e not in seen_e:
                seen_e.append(e)
        groups[k] = seen_e
    best = None
    for key, members in groups.items():
        if len(members) < 2:
            continue
        kind, value = key[0], key[2:]
        if kind == "s":
            only = lambda c, v=value: (not is_ocean(c)) and card_species_lc(c) == v
        elif kind == "n":
            only = lambda c, v=value: card_name_lc(c) == v
        else:
            pay_uid, w_sp, w_nm = payoff_filter[key]
            only = (lambda c, u=pay_uid, s=w_sp, n=w_nm:
                    c.uid == u or card_species_lc(c) in s or card_name_lc(c) in n)
        placed: List[Tuple[int, int, Optional[int]]] = []
        try:
            fp_now = cur_fp
            for e in members[:6]:
                gain, face, ocean = best_placement(gs, ms, player, e, fp_now, only=only, score=score)
                if face is None:
                    continue
                _place(gs, player, face, ocean)
                placed.append((e, face, ocean))
                fp_now = score(player)
            if len(placed) >= 2:
                t_new = plan_turns(gs, ms, selected + [(e, f) for e, f, _o in placed], avail, draw_turns, params)
                if t_new <= turns + 0.5:
                    net = (fp_now - cur_fp) - mu * (t_new - t_used)
                    if net > 0 and (best is None or net > best[0]):
                        best = (net, list(placed), t_new)
        finally:
            for e, f, o in reversed(placed):
                _unplace(gs, player, f, o)
    return best


def projection(gs: GameState, ms: MatchState, player: PlayerState, turns: float,
               stream: List[int], params: Dict[str, float],
               keep_out: Optional[Dict[int, float]] = None, score=None,
               selected_out: Optional[List[Tuple[int, int]]] = None,
               gains_out: Optional[Dict[int, float]] = None,
               horizon: Optional[Tuple[float, float]] = None,
               bp_cache: Optional[Dict[Tuple, Tuple[float, Optional[int], Optional[int]]]] = None) -> float:
    """Points still to come: the best plan over the hand and the cards this
    world will deal, in `turns` own turns, net of the turns it spends. Leaves
    the board exactly as it found it."""
    mu = float(params.get("turn_value", 1.6))
    hand = [e for e in player.hand if e != ms.end_game_uid]
    if turns <= 0.05 or (not hand and not stream):
        if keep_out is not None:
            for e in hand:
                keep_out[e] = 0.0
        return 0.0
    in_hand = set(hand)
    upcoming = [e for e in stream if e not in in_hand]
    avail: List[int] = list(hand)
    remaining: List[int] = list(hand)
    selected: List[Tuple[int, int]] = []
    placed: List[Tuple[int, Optional[int]]] = []
    accepted: Dict[int, float] = {}
    draw_turns = 0
    t_used = 0.0
    rescore = max(1, int(params.get("plan_rescore", 3)))
    draw_frac = float(params.get("draw_frac", 0.5))
    score = score or (lambda pl: final_points(gs, pl))
    bkey = [_board_key(player)] if bp_cache is not None else None

    def place_best(e: int):
        """best_placement, remembered per board shape for the whole move: the
        same card on the same board comes up for every draw option compared."""
        if bp_cache is None:
            return best_placement(gs, ms, player, e, cur_fp, score=score)
        k = (bkey[0], e)
        got = bp_cache.get(k)
        if got is None:
            got = best_placement(gs, ms, player, e, cur_fp, score=score)
            bp_cache[k] = got
        return got

    engine_draws = float(params.get("engine_draws", 0.0)) > 0.0

    def draw_for(face: int) -> None:
        """A planned card that draws brings the next cards of this world into
        the plan: an engine is worth the plays it feeds, not just the fodder."""
        nonlocal upcoming
        if not engine_draws or not upcoming:
            return
        c = gs.card_db.get(face)
        if c is None:
            return
        play_again, star_draws, main_draws, _sp, _n, _fs, _ch = card_traits(c)
        n = main_draws
        if star_draws:
            sym = normalize_symbol(c.symbol)
            chosen_e = {e2 for e2, _f2 in selected}
            has_sym = c.cost > 0 and sym not in ("", "n/a") and any(
                symbol_match_for_entry(ms, gs, e2, sym) for e2 in avail if e2 not in chosen_e)
            if has_sym:
                if star_draws < 0:
                    n += sum(1 for _e2, f2 in selected if card_name_lc(gs.card_db[f2]) == "yellowfin tuna")
                else:
                    n += star_draws
        k = min(int(n), len(upcoming))
        if k <= 0:
            return
        new_cards = upcoming[:k]
        upcoming = upcoming[k:]
        for e2 in new_cards:
            avail.append(e2)
            g2 = place_best(e2)[0]
            if g2 <= 0.0 and not _is_set_card(gs, ms, e2):
                continue
            stale[e2] = g2
            remaining.append(e2)

    cur_fp = score(player)
    base_fp = cur_fp
    pool_k = int(params.get("pool_in_stream", 0))
    if pool_k > 0 and ms.pool:
        # The Pool is face up: the best few cards in it are the first ones a
        # draw turn would take, ahead of anything blind off the deck.
        in_pool = [u for u in ms.pool if u != ms.end_game_uid and u not in in_hand]
        head = sorted(in_pool, key=lambda u: -max(0.0, place_best(u)[0]))[:pool_k]
        upcoming = head + [e for e in upcoming if e not in head]
    # Points the plan adds, each weighted by the chance the game lasts long
    # enough to play it (see survival). Without a horizon every weight is 1.
    earned = 0.0
    swept = False
    stale: Dict[int, float] = {}
    try:
        for e in remaining:
            stale[e] = place_best(e)[0]
        while True:
            best = None
            if remaining:
                order = sorted(remaining, key=lambda x: stale[x], reverse=True)
                for e in order[:rescore]:
                    gain, face, ocean = place_best(e)
                    stale[e] = gain
                    if face is None:
                        continue
                    t_new = plan_turns(gs, ms, selected + [(e, face)], avail, draw_turns, params)
                    if t_new > turns + 0.5:
                        continue
                    net = gain - mu * (t_new - t_used)
                    if best is None or net > best[0]:
                        best = (net, e, face, ocean, t_new)
            if best is not None and best[0] > 0.0:
                net, e, face, ocean, t_new = best
                earned += float(stale[e]) * survival(horizon, t_new)
                if gains_out is not None:
                    gains_out[face] = float(stale[e])
                _place(gs, player, face, ocean)
                placed.append((face, ocean))
                selected.append((e, face))
                remaining.remove(e)
                accepted[e] = net
                cur_fp = score(player)
                if bkey is not None:
                    bkey[0] = _board_key(player)
                t_used = t_new
                swept = False
                draw_for(face)
                continue
            bundle = None
            if len(remaining) >= 2:
                bundle = _best_bundle(gs, ms, player, remaining, selected, avail, draw_turns,
                                      cur_fp, t_used, turns, params, score)
            if bundle is not None:
                b_net, members, t_new = bundle
                share = b_net / float(len(members))
                b_before = cur_fp
                if gains_out is not None:
                    before_b = cur_fp
                    for e, face, ocean in members:
                        _place(gs, player, face, ocean)
                    b_gain = float(score(player) - before_b)
                    for e, face, ocean in reversed(members):
                        _unplace(gs, player, face, ocean)
                    for e, face, ocean in members:
                        gains_out[face] = b_gain / float(len(members))
                for e, face, ocean in members:
                    _place(gs, player, face, ocean)
                    placed.append((face, ocean))
                    selected.append((e, face))
                    remaining.remove(e)
                    accepted[e] = share
                cur_fp = score(player)
                if bkey is not None:
                    bkey[0] = _board_key(player)
                earned += float(cur_fp - b_before) * survival(horizon, t_new)
                t_used = t_new
                for _e, face, _o in members:
                    draw_for(face)
                continue
            # Before giving up on what is already available: the plan above only
            # re-scores its few best-looking cards each step, and a payoff card
            # (a Penguin, a Whale Shark) gains value as its set lands. Look at
            # every card once more on the board as planned so far.
            if remaining and float(params.get("final_sweep", 0.0)) > 0.0 and not swept:
                swept = True
                for e in remaining:
                    stale[e] = place_best(e)[0]
                if any(stale[e] > 0.0 for e in remaining):
                    continue
            # Nothing in hand pays for itself any more. Spend some of the turns
            # left drawing, and plan with what that world deals.
            spare = turns - t_used
            if upcoming and spare >= 1.5:
                add = max(1, int(spare * draw_frac + 0.5))
                add = min(add, len(upcoming) // 2)
                if add <= 0:
                    break
                new = upcoming[:2 * add]
                upcoming = upcoming[2 * add:]
                draw_turns += add
                avail.extend(new)
                for e in new:
                    g = place_best(e)[0]
                    # A drawn card that scores nothing here and belongs to no
                    # set is only ever fodder: it pays costs, it is not a play.
                    if g <= 0.0 and not _is_set_card(gs, ms, e):
                        continue
                    stale[e] = g
                    remaining.append(e)
                t_used = plan_turns(gs, ms, selected, avail, draw_turns, params)
                continue
            break
    finally:
        for face, ocean in reversed(placed):
            _unplace(gs, player, face, ocean)
    if selected_out is not None:
        selected_out.extend(selected)
    if keep_out is not None:
        tight = 1.0 if t_used > float(len(selected)) + draw_turns else 0.4
        for e in hand:
            if e in accepted:
                keep_out[e] = 1.0 + max(0.0, accepted[e]) + 0.5 * mu
            else:
                g = stale.get(e, 0.0)
                keep_out[e] = 0.25 * max(0.0, g if g != _NEG else 0.0) + 0.25 * mu * tight
    return (earned if horizon is not None else float(cur_fp - base_fp)) - mu * t_used


def measure_turn_value(gs: GameState, ms: MatchState, player: PlayerState, turns: float,
                       stream: List[int], params: Dict[str, float], score) -> float:
    """What one of this player's remaining turns is worth right now: the points
    a turn earns if the only cards it had were the ones still to be drawn,
    spread over every turn left.

    A fixed price is wrong at both ends of a game. Early, a turn spent on a +1
    Ocean is a turn not spent building toward a 30-point set; late, there is no
    set left to build and that +1 is the best thing the turn can do. Pricing the
    turn at what the future can still do with it gets both right."""
    if turns <= 1.0 or not stream:
        return float(params.get("turn_value_floor", 0.5))
    probe = dict(params)
    probe["turn_value"] = float(params.get("turn_value_floor", 0.5))
    saved = list(player.hand)
    player.hand[:] = []
    try:
        gained = projection(gs, ms, player, turns, stream, probe, score=score)
    finally:
        player.hand[:] = saved
    rate = (gained + probe["turn_value"] * turns) / max(1.0, turns)
    return max(float(params.get("turn_value_floor", 0.5)), min(float(params.get("turn_value", 3.0)), rate))


# ── One decision ────────────────────────────────────────────────────────────

class Ctx:
    __slots__ = ("gs", "ms", "player", "params", "rng", "keep", "threats", "stream", "nodes",
                 "others", "score", "memo", "rivals", "world_id", "bp_cache", "deadline")

    def __init__(self, gs, ms, player, params, rng):
        self.gs, self.ms, self.player, self.params, self.rng = gs, ms, player, params, rng
        self.keep: Dict[int, float] = {}
        self.threats: Dict[int, float] = {}
        self.stream: List[int] = []
        # Which world the stream belongs to. Memo keys use this, never id():
        # a world's list can be freed and its id handed to the next one.
        self.world_id = 0
        self.bp_cache: Dict[Tuple, Tuple[float, Optional[int], Optional[int]]] = {}
        self.deadline: Optional[float] = None
        self.nodes = 0
        # Positions already valued this move. Drawing A then B and B then A end
        # in the same place, and so do many chains.
        self.memo: Dict[Tuple, float] = {}
        self.rivals: Optional[List[Tuple[float, float]]] = None
        # Nothing this player does on its own turn touches another board, so
        # what the scorer needs to know about the others is fixed for the move.
        self.others = others_summary(gs, player)
        family = str(player.flags.get("_strategy_family", "") or "")
        loyalty = float(params.get("loyalty", 0.0)) if family in STRATEGY_FAMILIES else 0.0
        if loyalty > 0.0:
            self.score = lambda pl, _g=gs, _o=self.others, _f=family, _l=loyalty: fast_points(_g, pl, _o, _f, _l)
        else:
            self.score = lambda pl, _g=gs, _o=self.others: fast_points(_g, pl, _o)

    def rival_finish(self, turns: float) -> float:
        """Where the strongest opponent looks set to finish if the game lasts
        `turns` more turns: their score now plus the pace they have kept."""
        if self.rivals is None:
            played = max(1.0, float(self.gs.round_count) + 1.0)
            self.rivals = []
            for other in self.gs.players:
                if other is not self.player:
                    score = float(final_points(self.gs, other))
                    self.rivals.append((score, score / played))
        if not self.rivals:
            return 0.0
        return max(score + rate * turns for score, rate in self.rivals)

    def threat(self, entry_uid: int) -> float:
        """The most any opponent's board could score with this card, counted
        only past denial_threshold: every card helps somebody a little, and
        only the ones that complete something (a third goby, a fourth kelp)
        are worth spending anything to keep away."""
        got = self.threats.get(entry_uid)
        if got is None:
            got = 0.0
            for other in self.gs.players:
                if other is self.player:
                    continue
                cur = final_points(self.gs, other)
                gain = best_placement(self.gs, self.ms, other, entry_uid, cur)[0]
                if gain != _NEG:
                    got = max(got, gain)
            floor = float(self.params.get("denial_threshold", 0.0))
            got = max(0.0, got - floor) if floor > 0.0 else got
            self.threats[entry_uid] = got
        return got

    def pay(self, gs, ms, player, played_entry_uid, play_face_uid, cost, require_symbol):
        """Pay with the cards the plan needs least and the next players want least."""
        if cost <= 0:
            return []
        cands = [u for u in player.hand if u != played_entry_uid and u != ms.end_game_uid]
        if len(cands) < cost:
            return None
        beta = float(self.params.get("denial", 0.0))
        if beta > 0.0:
            ranked = sorted(cands, key=lambda u: (self.keep.get(u, 0.5) + beta * self.threat(u), u))
        else:
            ranked = sorted(cands, key=lambda u: (self.keep.get(u, 0.5), u))
        chosen: List[int] = []
        if require_symbol:
            sym = normalize_symbol(gs.card_db[play_face_uid].symbol)
            match = next((u for u in ranked if symbol_match_for_entry(ms, gs, u, sym)), None)
            if match is None:
                return None
            chosen.append(match)
        for u in ranked:
            if len(chosen) >= cost:
                break
            if u not in chosen:
                chosen.append(u)
        return chosen if len(chosen) == cost else None


def _budget_after(player: PlayerState, turn_state: TurnState, was_free_only: bool) -> int:
    """What run_match does to the action budget after an action, replayed on a
    simulated turn. Returns the actions left (0 = the turn is over)."""
    if was_free_only:
        player.flags["_free_action_only"] = False
    if turn_state.force_end_turn:
        return 0
    budget = 1
    if pending_replay_actions(player) > 0:
        added = consume_replay_actions(player)
        budget += added
        if added > 0:
            player.flags["_replay_turn_next"] = True
    if turn_state.free_followups > 0:
        budget += turn_state.free_followups
        if not player.flags.get("_draws_taken"):
            player.flags["_free_action_only"] = True
        turn_state.free_followups = 0
    if has_multi_play_window(player):
        budget += 1
    return budget - 1


IDLE_TURNS_BEFORE_PROGRESS = 2


def candidates(gs: GameState, ms: MatchState, player: PlayerState) -> List[Action]:
    out: List[Action] = []
    seen = set()
    legal = legal_actions(gs, ms, player, include_draw=True)
    if int(player.flags.get("_planner_idle", 0) or 0) >= IDLE_TURNS_BEFORE_PROGRESS:
        # Two turns in a row that changed nothing: no more trading with the
        # Pool until the game moves. Two bots with full hands can otherwise
        # swap the same cards back and forth forever, and the game never ends.
        progress = [a for a in legal if not (a.kind == "draw" and a.draw_from_pool == 1)]
        if progress:
            legal = progress
    for a in legal:
        if a.kind == "move_between_oceans":
            continue
        if a.kind == "draw" and a.draw_from_pool == 1:
            names = set()
            for uid in ms.pool:
                if uid == ms.end_game_uid:
                    continue
                sig = tuple(sorted(card_name_lc(gs.card_db[f]) + normalize_symbol(gs.card_db[f].symbol)
                                   for f in entry_faces(ms, uid) if f in gs.card_db))
                if sig in names:
                    continue
                names.add(sig)
                b = clone_action(a)
                b.pool_pick_uids = [uid]
                out.append(b)
            continue
        if a.kind == "play_to_ocean" and a.ocean_uid is not None:
            face = a.face_uid if a.face_uid is not None else a.card_uid
            key = (a.kind, a.card_uid, face, _ocean_sig(gs, player, a.ocean_uid), a.use_star)
        else:
            key = (a.kind, a.card_uid, a.face_uid, a.draw_from_pool, a.use_star)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _quick(ctx: Ctx, a: Action) -> float:
    """A cheap pre-rank: what the move scores on the spot, plus how much the
    plan wanted the card."""
    gs, ms, p = ctx.gs, ctx.ms, ctx.player
    mu = float(ctx.params.get("turn_value", 1.6))
    if a.kind == "draw":
        if a.draw_from_pool and a.pool_pick_uids:
            uid = a.pool_pick_uids[0]
            gain = best_placement(gs, ms, p, uid, ctx.score(p), score=ctx.score)[0]
            beta = float(ctx.params.get("denial", 0.0))
            return max(0.0, gain if gain != _NEG else 0.0) + (beta * ctx.threat(uid) if beta > 0.0 else 0.0)
        return mu
    if a.kind == "end_turn":
        return 0.0
    face = a.face_uid if a.face_uid is not None else a.card_uid
    if face not in gs.card_db:
        return 0.0
    cur = ctx.score(p)
    ocean = None if a.kind == "play_ocean" else a.ocean_uid
    if a.kind == "play_ocean" and face in p.ocean_slots:
        return 0.0
    if ocean is not None and ocean not in p.ocean_slots:
        return 0.0
    _place(gs, p, face, ocean)
    try:
        gain = ctx.score(p) - cur
    finally:
        _unplace(gs, p, face, ocean)
    return float(gain) + ctx.keep.get(a.card_uid, 0.0) + (1.0 if a.use_star else 0.0)


def end_value(ctx: Ctx) -> float:
    """What the position a simulated turn ends in is worth."""
    gs, ms, p, params = ctx.gs, ctx.ms, ctx.player, ctx.params
    ctx.nodes += 1
    if float(params.get("survival", 0.0)) > 0.0:
        lo, hi = turns_range_after_turn(gs, ms, p, params)
        turns = hi + 0.0001 * lo       # the key must tell ranges apart
    else:
        turns = turns_left_after_turn(gs, ms, p, params)
    key = (ctx.world_id, round(turns, 5), bool(ms.end_game_triggered), tuple(sorted(p.hand)),
           tuple(sorted(ms.pool)), tuple(p.board_oceans),
           tuple(tuple(s.up) + (-1,) + tuple(s.down) + (-2,) + tuple(s.left) + (-3,) + tuple(s.right)
                 for s in (p.ocean_slots.get(o) for o in p.board_oceans) if s is not None))
    got = ctx.memo.get(key)
    if got is not None:
        return got
    value = _end_value_uncached(ctx, turns)
    ctx.memo[key] = value
    return value


def _end_value_uncached(ctx: Ctx, turns: float) -> float:
    gs, ms, p, params = ctx.gs, ctx.ms, ctx.player, ctx.params
    over = len([u for u in p.hand if u != ms.end_game_uid]) - HAND_LIMIT
    dummy = Action(kind="end_turn")
    tx = capture_action_tx(gs, ms, dummy, TurnState()) if over > 0 else None
    try:
        if over > 0:
            discard_down(gs, ms, p, HAND_LIMIT, ctx=ctx)
        value = float(ctx.score(p))
        horizon = None
        if float(params.get("survival", 0.0)) > 0.0:
            horizon = turns_range_after_turn(gs, ms, p, params)
            turns = horizon[1]
        value += float(params.get("plan_discount", 1.0)) * projection(gs, ms, p, turns, ctx.stream, params,
                                                                     score=ctx.score, horizon=horizon,
                                                                     bp_cache=ctx.bp_cache)
        beta = float(params.get("denial", 0.0))
        if beta > 0 and ms.pool:
            value -= 0.5 * beta * sum(ctx.threat(u) for u in ms.pool if u != ms.end_game_uid)
        kappa = float(params.get("rival_weight", 0.0))
        if kappa > 0.0:
            value -= kappa * ctx.rival_finish(turns)
        return value
    finally:
        if tx is not None:
            restore_action_tx(gs, ms, dummy, TurnState(), tx)


def _continue(ctx: Ctx, turn_state: TurnState, budget: int, depth: int) -> float:
    if budget <= 0:
        return end_value(ctx)
    params = ctx.params
    if (depth >= int(params.get("max_chain_depth", 4)) or ctx.nodes >= int(params.get("node_budget", 500))
            or (ctx.deadline is not None and _monotonic() >= ctx.deadline)):
        return end_value(ctx) + 0.5 * float(params.get("turn_value", 1.6))
    gs, ms, p = ctx.gs, ctx.ms, ctx.player
    cands = candidates(gs, ms, p)
    if not cands:
        return end_value(ctx)
    if p.flags.get("_draws_taken"):
        width = int(params.get("second_draw_width", 4))
        pool_draws = [a for a in cands if a.kind == "draw" and a.draw_from_pool == 1]
        if len(pool_draws) > width:
            pool_draws.sort(key=lambda a: -_quick(ctx, a))
            keep = {id(a) for a in pool_draws[:width]}
            cands = [a for a in cands if not (a.kind == "draw" and a.draw_from_pool == 1) or id(a) in keep]
    beam = int(params.get("chain_beam", 5))
    if len(cands) > beam:
        cands = sorted(cands, key=lambda a: -_quick(ctx, a))[:beam]
    best = _NEG
    for a in cands:
        v = _try(ctx, a, turn_state, depth)
        if v is not None and v > best:
            best = v
    return best if best != _NEG else end_value(ctx)


def _try(ctx: Ctx, action: Action, turn_state: TurnState, depth: int,
         payments_out: Optional[List[int]] = None, single: bool = False) -> Optional[float]:
    gs, ms, p = ctx.gs, ctx.ms, ctx.player
    tx = capture_action_tx(gs, ms, action, turn_state)
    was_free_only = bool(p.flags.get("_free_action_only", False))
    try:
        if not _apply_action_uncommitted(gs, ms, p, action, turn_state, ctx.pay):
            return None
        if payments_out is not None:
            payments_out[:] = list(action.payment_uids)
        if single:
            return end_value(ctx)
        budget = _budget_after(p, turn_state, was_free_only)
        return _continue(ctx, turn_state, budget, depth + 1)
    except Exception:
        return None
    finally:
        restore_action_tx(gs, ms, action, turn_state, tx)


def discard_down(gs: GameState, ms: MatchState, player: PlayerState, limit: int = HAND_LIMIT,
                 ctx: Optional[Ctx] = None, params: Optional[Dict[str, float]] = None) -> None:
    """Trim the hand to the limit: keep what the plan needs, and do not hand the
    next player what they need."""
    params = params_for_table(params or (ctx.params if ctx is not None else PARAMS),
                              len(gs.players))
    if ctx is None:
        ctx = Ctx(gs, ms, player, params, random.Random(len(gs.deck) * 131 + len(player.hand)))
        ctx.stream = world_stream(gs, ms, world_deck(gs, ms, player, ctx.rng), params)
    beta = float(params.get("denial", 0.0))
    while len(player.hand) > limit:
        keep: Dict[int, float] = {}
        turns = turns_left_after_turn(gs, ms, player, params)
        projection(gs, ms, player, max(0.5, turns), ctx.stream, params, keep_out=keep, score=ctx.score)
        cands = [u for u in player.hand if u != ms.end_game_uid] or list(player.hand)
        ranked = sorted(cands, key=lambda u: (keep.get(u, 0.0) + (beta * ctx.threat(u) if beta > 0.0 else 0.0), u))
        # One plan prices the whole trim; re-planning after every single card
        # changes almost nothing and costs a projection each.
        for worst in ranked[:len(player.hand) - limit]:
            player.hand.remove(worst)
            add_to_pool(ms, worst)


def choose_action(gs: GameState, ms: MatchState, player: PlayerState,
                  params: Optional[Dict[str, float]] = None,
                  rng: Optional[random.Random] = None,
                  out_scored: Optional[List[Tuple[Action, float]]] = None) -> Optional[Action]:
    """The planner's move for `player`. See the module docstring."""
    params = params_for_table(params or PARAMS, len(gs.players))
    rng = rng or random.Random(random.getrandbits(64))
    note_turn(gs, ms, player)
    if ms.end_game_triggered and player.flags.get("_planner_eg_at_start"):
        # The last turn. Nothing after it can use a card still in hand, and the
        # longest chains (a Loggerhead turn, a Reef Trigger dump) are worth the
        # most right here, so it is the one move worth searching exhaustively.
        params = dict(params)
        params["max_chain_depth"] = int(params.get("max_chain_depth", 4)) + 4
        params["chain_beam"] = int(params.get("chain_beam", 5)) + 3
        params["node_budget"] = int(params.get("node_budget", 60)) * 4
        params["top_width"] = max(int(params.get("top_width", 12)), 20)
    player.flags["_planner"] = PLANNER_NAME
    note_turn(gs, ms, player)
    if float(params.get("loyalty", 0.0)) > 0.0:
        if not player.flags.get("_planner_family_chosen"):
            family, _values = choose_family(gs, ms, player, params, rng,
                                            worlds=int(params.get("family_worlds", 4)))
            player.flags["_strategy_family"] = family
            player.flags["_strategy_family_source"] = "planner"
            player.flags["_planner_family_chosen"] = True
        else:
            reconsider_family(gs, ms, player, params)
    cands = candidates(gs, ms, player)
    if not cands:
        return None
    ctx = Ctx(gs, ms, player, params, rng)
    decks = [world_deck(gs, ms, player, rng) for _ in range(max(1, int(params.get("worlds", 1))))]
    streams = [world_stream(gs, ms, d, params) for d in decks]
    ctx.stream = streams[0]
    if float(params.get("adaptive_turn_value", 0.0)) > 0.0:
        params = dict(params)
        params["turn_value"] = measure_turn_value(
            gs, ms, player, turns_left_after_turn(gs, ms, player, params) + 1.0, ctx.stream, params, ctx.score)
        ctx.params = params
    projection(gs, ms, player, turns_left_after_turn(gs, ms, player, params) + 1.0,
               ctx.stream, params, keep_out=ctx.keep, score=ctx.score)
    if len(cands) == 1 and cands[0].kind not in ("play_ocean", "play_to_ocean"):
        return cands[0]

    deck_samples = max(1, int(params.get("deck_samples", 1)))

    def variants(deck: List[int], action: Action) -> List[List[int]]:
        """The world's deck, plus (for a deck draw) the same world with other
        cards on top, so a draw is valued on what it might bring, not on one card."""
        if deck_samples <= 1 or action.kind != "draw" or action.draw_from_pool != 0:
            return [deck]
        out = [deck]
        movable = [i for i in range(min(len(deck), 6 + 2 * deck_samples)) if deck[i] != ms.end_game_uid]
        for j in range(1, deck_samples):
            if 2 * j + 1 >= len(movable):
                break
            d = list(deck)
            a0, a1, b0, b1 = movable[0], movable[1], movable[2 * j], movable[2 * j + 1]
            d[a0], d[b0] = d[b0], d[a0]
            d[a1], d[b1] = d[b1], d[a1]
            out.append(d)
        return out

    world_ids: List[int] = list(range(len(decks)))

    def run(action: Action, single: bool = False) -> Tuple[float, List[int]]:
        total, count, pays = 0.0, 0, []
        for deck, stream, wid in zip(decks, streams, world_ids):
            for vi, variant in enumerate(variants(deck, action)):
                saved = list(gs.deck)
                gs.deck[:] = variant
                ctx.stream = stream
                ctx.world_id = wid * 64 + vi
                ctx.nodes = 0
                try:
                    pay_out: List[int] = []
                    v = _try(ctx, action, TurnState(), 0, payments_out=pay_out, single=single)
                finally:
                    gs.deck[:] = saved
                if v is None:
                    continue
                total += v
                count += 1
                if not pays:
                    pays = pay_out
        return (total / count, pays) if count else (_NEG, [])

    budget = float(params.get("time_budget", 0.0))
    deadline = (_monotonic() + budget) if budget > 0.0 else None
    # A chain being searched when the clock runs out stops where it is too;
    # it gets a small grace so the move it is on can still be finished.
    ctx.deadline = (deadline + 0.25 * budget) if deadline is not None else None

    def out_of_time() -> bool:
        return deadline is not None and _monotonic() >= deadline

    draws = [a for a in cands if a.kind == "draw"]
    plays = [a for a in cands if a.kind != "draw"]
    top_width = int(params.get("top_width", 16))
    plays = sorted(plays, key=lambda a: -_quick(ctx, a))
    if len(plays) > top_width:
        plays = plays[:top_width]
    scored: List[Tuple[Action, float, List[int]]] = []
    # With a clock (live play only), the plain deck draw is valued first and the
    # plays best-first, so running out of time drops the least promising moves,
    # never the option of simply drawing.
    deck_first = [a for a in draws if a.draw_from_pool == 0] if deadline is not None else []
    for a in deck_first:
        v, pays = run(a)
        if v != _NEG:
            scored.append((a, v, pays))
    for a in plays:
        if scored and out_of_time():
            break
        v, pays = run(a)
        if v != _NEG:
            scored.append((a, v, pays))
    if deck_first:
        draws = [a for a in draws if a.draw_from_pool != 0]
    if draws and not (scored and out_of_time()):
        if not player.flags.get("_draws_taken") and len(draws) > 1:
            pool_draws = [a for a in draws if a.draw_from_pool == 1]
            pick_width = int(params.get("pool_pick_width", 4))
            if len(pool_draws) > pick_width:
                pool_draws = sorted(pool_draws, key=lambda a: -_quick(ctx, a))[:pick_width]
            draws = [a for a in draws if a.draw_from_pool == 0] + pool_draws
            singles = sorted(((run(a, single=True)[0], i, a) for i, a in enumerate(draws)),
                             key=lambda x: (x[0], -x[1]), reverse=True)
            width = int(params.get("first_draw_width", 3))
            shortlist = [a for _v, _i, a in singles[:width]]
            deck_draw = next((a for a in draws if a.draw_from_pool == 0), None)
            if deck_draw is not None and deck_draw not in shortlist:
                shortlist.append(deck_draw)
        else:
            shortlist = draws
        for a in shortlist:
            if scored and out_of_time():
                break
            if any(a is s[0] for s in scored):
                continue
            v, pays = run(a)
            if v != _NEG:
                scored.append((a, v, pays))
    if not scored:
        return cands[0]
    scored.sort(key=lambda x: x[1], reverse=True)
    confirm_worlds = int(params.get("confirm_worlds", 0))
    confirm_top = int(params.get("confirm_top", 4))
    if confirm_worlds > len(decks) and len(scored) > 1 and not out_of_time():
        # Every move was judged in the same few worlds, and one world is a noisy
        # judge: measured, three worlds a move beat one by five points and half
        # a finishing place. Worlds cost what they cost, so spend the extra ones
        # only where they can change the answer: re-judge the leading moves in
        # more reshuffles, all in the same new worlds, and choose among those.
        extra_decks = [world_deck(gs, ms, player, rng) for _ in range(confirm_worlds - len(decks))]
        extra_streams = [world_stream(gs, ms, d, params) for d in extra_decks]
        base_n = len(decks)
        finalists = scored[:max(2, confirm_top)]
        world_ids = list(range(len(decks), len(decks) + len(extra_decks)))
        decks, streams = extra_decks, extra_streams
        rejudged: List[Tuple[Action, float, List[int]]] = []
        for a, v0, pays0 in finalists:
            if out_of_time() and len(rejudged) >= 2:
                break
            v1, _p = run(a)
            if v1 == _NEG:
                continue
            n_extra = len(extra_decks)
            rejudged.append((a, (v0 * base_n + v1 * n_extra) / float(base_n + n_extra), pays0))
        if rejudged:
            rejudged.sort(key=lambda x: x[1], reverse=True)
            scored = rejudged + [s for s in scored if all(s[0] is not f[0] for f in finalists)]
    if out_scored is not None:
        out_scored.clear()
        out_scored.extend((a, v) for a, v, _p in scored)
    best, _v, pays = scored[0]
    temperature = float(params.get("temperature", 0.0))
    if temperature > 0.0 and len(scored) > 1:
        # A lower grade sees the same ranking and does not always follow it:
        # moves within a few points of the best are taken now and then, in
        # proportion to how close they are.
        top = scored[0][1]
        weights = [pow(2.718281828, max(-50.0, (v - top) / temperature)) for _a, v, _p in scored]
        r = rng.random() * sum(weights)
        for (a, v, p_), wgt in zip(scored, weights):
            r -= wgt
            if r <= 0.0:
                best, pays = a, p_
                break
    chosen = clone_action(best)
    if chosen.kind in ("play_ocean", "play_to_ocean") and pays:
        chosen.payment_uids = list(pays)
    return chosen


# ── The grade ladder ────────────────────────────────────────────────────────
# From Eugenie Clark (A) up, a bot is the planner. What separates the rungs is
# how much of it they get to use: how many moves it considers, how far it looks
# into a chain, how many cards to come it plans with, and how faithfully it
# follows its own ranking. Below A the weighted chooser and its handicaps carry
# the ladder, as before, because a beginner should not play like this.
#
# LITE is what any planner grade falls back to when the server is busy: the
# same judgement, looking at fewer moves.
LITE: Dict[str, float] = {
    "top_width": 5, "stream_max": 6, "max_chain_depth": 2, "chain_beam": 3,
    "first_draw_width": 1, "pool_pick_width": 2, "second_draw_width": 2,
    "plan_rescore": 1, "node_budget": 20, "worlds": 1, "confirm_worlds": 0,
    "family_worlds": 1,
}

# MINIMAL is for a site so busy that every move has to be cheap: the planner's
# judgement on the three most promising moves and no chains.
MINIMAL: Dict[str, float] = {
    **LITE, "top_width": 3, "stream_max": 4, "max_chain_depth": 1, "chain_beam": 2,
    "pool_pick_width": 1, "second_draw_width": 1, "node_budget": 8,
}

GRADE_PARAMS: Dict[str, Dict[str, float]] = {
    # About where the old S++ bot played: the planner looking at five moves and
    # four cards ahead, and loose about following its own ranking.
    "eugenie_clark":    {**LITE, "stream_max": 4, "temperature": 6.0},
    "rachel_carson":    {"top_width": 8, "stream_max": 8, "max_chain_depth": 3, "temperature": 2.5},
    "jacques_cousteau": {"temperature": 1.5},
    # Two worlds judge every move and the leading six are judged in six more,
    # over a wider look at moves, draws and pool picks. Measured on 120 paired
    # deals: level with settings that cost twice as much and with ones that
    # cost three times as much, and 6 points ahead of the lighter S++ it
    # replaced. Past this, more search stops showing up in the results.
    "charles_darwin":   {"worlds": 2, "confirm_worlds": 8, "confirm_top": 6, "top_width": 16,
                         "chain_beam": 6, "first_draw_width": 4, "pool_pick_width": 5},
    # Everything the planner has: the most worlds, the most moves confirmed.
    "giant_squid":      {"worlds": 4, "confirm_worlds": 16, "confirm_top": 10, "top_width": 24,
                         "chain_beam": 8, "first_draw_width": 6, "pool_pick_width": 7},
}


def params_for_grade(grade: str) -> Optional[Dict[str, float]]:
    """The planner settings a grade plays with, or None if the grade is not a
    planner grade."""
    key = fish.normalize_bot_grade(grade) if grade else ""
    over = GRADE_PARAMS.get(key)
    if over is None:
        return None
    params = dict(PARAMS)
    params.update(over)
    return params


def lite_params(params: Dict[str, float], floor: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """The same grade, looking at fewer moves: for a busy server. `floor` is the
    set of ceilings to apply (LITE by default, MINIMAL when it is busier)."""
    out = dict(params)
    for k, v in (floor or LITE).items():
        if k in ("worlds",):
            out[k] = 1
        elif k in out:
            out[k] = min(float(out[k]), float(v))
        else:
            out[k] = v
    return out


def tarpon_discards(gs: GameState, ms: MatchState, player: PlayerState,
                    params: Optional[Dict[str, float]] = None) -> List[int]:
    """Tarpon: discard any number of cards and draw that many. Cycle away the
    cards the plan has no use for, and only those: a card the plan wants is
    worth more than a random one off the deck."""
    params = params_for_table(params or PARAMS, len(gs.players))
    ctx = Ctx(gs, ms, player, params, random.Random(len(gs.deck) * 197 + len(player.hand)))
    ctx.stream = world_stream(gs, ms, world_deck(gs, ms, player, ctx.rng), params)
    keep: Dict[int, float] = {}
    turns = turns_left_after_turn(gs, ms, player, params)
    if turns < 1.0 or len(gs.deck) < 2:
        return []          # a card drawn now cannot be played in time
    projection(gs, ms, player, turns, ctx.stream, params, keep_out=keep, score=ctx.score)
    threshold = 0.5 * float(params.get("turn_value", 3.0))
    spare = [u for u in player.hand if u != ms.end_game_uid and keep.get(u, 0.0) < threshold]
    return spare[:len(gs.deck)]


# Fold in whatever training has proved, before anything reads PARAMS.
TUNED_PARAMS_APPLIED: List[str] = load_tuned_params()

fish.PLANNER_DISCARD_HOOKS[PLANNER_NAME] = lambda gs, ms, p, limit: discard_down(gs, ms, p, limit)
fish.PLANNER_TARPON_HOOKS[PLANNER_NAME] = lambda gs, ms, p: tarpon_discards(gs, ms, p)
