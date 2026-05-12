#!/usr/bin/env python3
"""
Core rules engine for "The Fish Game" (Ocean Shuffle-style)

- Turn structure
- Oceans + directional slots (Up/Down/Left/Right)
- Playing cards and attaching to an Ocean
- Star ability rule (discard a card; if symbols match, you may use the discarded card's *star* ability)
- End game trigger + last round

NOTE:
This file is the RULES + ENGINE. It does NOT hardcode every card’s unique ability.
To add unique abilities, register functions in ABILITIES / STAR_ABILITIES by card name (see bottom).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Tuple
import random
import re


# -----------------------------
# Data model
# -----------------------------

DIRECTIONS = {"up", "down", "left", "right", "n/a"}
SYMBOLS = {"triangle", "square", "heart", "circle", "diamond", "n/a", ""}


@dataclass(frozen=True)
class CardDef:
    uid: int                     # unique per physical copy
    name: str                    # e.g., "Bottlenose Dolphin"
    species: str                 # e.g., "Mammal", "Ocean", "N/A"
    cost: int                    # energy / coins / whatever your cost system is
    direction: str               # Up/Down/Left/Right or "N/A" (Oceans)
    symbol: str                  # Triangle/Square/Heart/Circle/Diamond or "N/A"/"" if none
    text: str                    # ability text, may contain | and *star* parts


def normalize_symbol(sym: str) -> str:
    s = (sym or "").strip().lower()
    if s in {"n/a", "na", "none"}:
        return "n/a"
    return s


def normalize_direction(d: str) -> str:
    s = (d or "").strip().lower()
    if s in {"n/a", "na", "none"}:
        return "n/a"
    return s


@dataclass
class OceanSlots:
    up: List[int] = field(default_factory=list)
    down: List[int] = field(default_factory=list)
    left: List[int] = field(default_factory=list)
    right: List[int] = field(default_factory=list)

    def slot(self, direction: str) -> List[int]:
        d = normalize_direction(direction)
        if d == "up":
            return self.up
        if d == "down":
            return self.down
        if d == "left":
            return self.left
        if d == "right":
            return self.right
        raise ValueError(f"Invalid direction for slot attachment: {direction!r}")

    def all_cards(self) -> List[int]:
        return self.up + self.down + self.left + self.right


@dataclass
class PlayerState:
    name: str
    hand: List[int] = field(default_factory=list)          # card uids
    discard: List[int] = field(default_factory=list)       # card uids
    board_oceans: List[int] = field(default_factory=list)  # ocean card uids
    ocean_slots: Dict[int, OceanSlots] = field(default_factory=dict)  # ocean_uid -> slots
    score: int = 0
    energy: int = 0  # optional resource if you use costs
    flags: Dict[str, object] = field(default_factory=dict)


@dataclass
class GameState:
    card_db: Dict[int, CardDef]
    players: List[PlayerState]
    deck: List[int]
    turn_index: int = 0
    round_count: int = 0

    # end game
    end_game_triggered: bool = False
    end_game_trigger_turn_player: Optional[int] = None
    turns_remaining_after_trigger: int = 0  # set when triggered

    # logging
    log: List[str] = field(default_factory=list)

    def current_player(self) -> PlayerState:
        if not self.players:
            raise RuntimeError("no players in game state")
        idx = self.turn_index if isinstance(self.turn_index, int) else 0
        if idx < 0 or idx >= len(self.players):
            idx = idx % len(self.players)
            self.turn_index = idx
        return self.players[idx]


# -----------------------------
# Parsing: main vs star text
# -----------------------------

STAR_RE = re.compile(r"\*(.*?)\*", flags=re.DOTALL)

def split_main_and_star(text: str) -> Tuple[str, str]:
    """
    Your rule: '|' separates normal ability text from star ability text,
    and star ability is wrapped in * *.
    Examples:
      "+1 per coral | *play a free mammal*"
      "*draw one* | '+1 per card ...'"  (still handled; star is whatever is inside * *)
    Returns: (main_text, star_text) WITHOUT asterisks for star.
    """
    t = (text or "").strip()
    # Find first *...* as star ability (per your rule)
    m = STAR_RE.search(t)
    star = m.group(1).strip() if m else ""
    # main is everything EXCEPT the *...* region, cleaned a bit
    if m:
        main = (t[:m.start()] + t[m.end():]).replace("||", "|").strip()
    else:
        main = t
    return main.strip(), star.strip()


# -----------------------------
# Ability registry (extend this)
# -----------------------------
#
# Each ability function can read the card text and/or implement custom logic.
# Keep it simple: by default, main abilities and star abilities do NOTHING unless registered.

AbilityFn = Callable[[GameState, int, PlayerState, Optional[dict]], None]

ABILITIES: Dict[str, AbilityFn] = {}
STAR_ABILITIES: Dict[str, AbilityFn] = {}
# New canonical registries keyed by physical face uid.
ABILITIES_BY_UID: Dict[int, AbilityFn] = {}
STAR_ABILITIES_BY_UID: Dict[int, AbilityFn] = {}


def register_ability(card_name: str):
    def deco(fn: AbilityFn):
        ABILITIES[card_name.lower()] = fn
        return fn
    return deco


def register_star_ability(card_name: str):
    def deco(fn: AbilityFn):
        STAR_ABILITIES[card_name.lower()] = fn
        return fn
    return deco


def run_main_ability(gs: GameState, card_uid: int, player: PlayerState, ctx: Optional[dict] = None) -> None:
    cd = gs.card_db.get(card_uid)
    if cd is None:
        gs.log.append(f"Skipping main ability for missing card uid={card_uid}.")
        return
    fn = ABILITIES_BY_UID.get(card_uid)
    if fn is None:
        fn = ABILITIES.get(cd.name.lower())
    if fn:
        fn(gs, card_uid, player, ctx)
    else:
        # default: no-op
        pass


def run_star_ability(gs: GameState, card_uid: int, player: PlayerState, ctx: Optional[dict] = None) -> None:
    cd = gs.card_db.get(card_uid)
    if cd is None:
        gs.log.append(f"Skipping STAR ability for missing card uid={card_uid}.")
        return
    fn = STAR_ABILITIES_BY_UID.get(card_uid)
    if fn is None:
        fn = STAR_ABILITIES.get(cd.name.lower())
    if fn:
        fn(gs, card_uid, player, ctx)
    else:
        # default: no-op
        pass


# -----------------------------
# Core rules
# -----------------------------

def draw(gs: GameState, player: PlayerState, n: int = 1, ms=None) -> None:
    drew = 0
    while drew < n:
        if not gs.deck:
            gs.log.append("Deck is empty; cannot draw.")
            return
        uid = gs.deck.pop(0)
        if ms is not None and ms.end_game_uid is not None and uid == ms.end_game_uid:
            trigger_end_game(ms, gs)
            ms.discard_pile.append(uid)
            gs.log.append(f"END GAME card drawn by {player.name} via card ability — end game triggered.")
            continue  # draw a replacement card
        player.hand.append(uid)
        drew += 1


def start_game(gs: GameState, starting_hand: int = 5, shuffle: bool = True) -> None:
    if shuffle:
        random.shuffle(gs.deck)
    for p in gs.players:
        draw(gs, p, starting_hand)
    gs.log.append(f"Game started. Each player drew {starting_hand}.")


def next_turn(gs: GameState) -> None:
    # advance turn index, increment round when wraps
    gs.turn_index = (gs.turn_index + 1) % len(gs.players)
    if gs.turn_index == 0:
        gs.round_count += 1


def trigger_end_game(gs_or_ms, maybe_gs: Optional[GameState] = None) -> None:
    """
    Unified END GAME trigger for both legacy and current engine paths.

    - Legacy path: trigger_end_game(gs)
    - Current path: trigger_end_game(ms, gs)
    """
    # Current simulator path: MatchState + GameState
    if maybe_gs is not None:
        ms = gs_or_ms
        gs = maybe_gs
        if ms.end_game_triggered:
            return
        ms.end_game_triggered = True
        # Drawer/flipper still gets this turn, then each player gets one final turn total.
        ms.final_turns_remaining = len(gs.players)
        gs.log.append("END GAME revealed: each player (including revealer) gets one final turn.")
        return

    # Legacy engine path: GameState only
    gs = gs_or_ms
    if gs.end_game_triggered:
        return
    gs.end_game_triggered = True
    gs.end_game_trigger_turn_player = gs.turn_index
    # Your rule: "everyone gets one more turn then the game is over"
    # If triggered on Player i's turn, then remaining turns = len(players) - 1
    gs.turns_remaining_after_trigger = len(gs.players) - 1
    gs.log.append("END GAME triggered: each other player gets one more turn.")


def game_is_over(gs: GameState) -> bool:
    return gs.end_game_triggered and gs.turns_remaining_after_trigger <= 0


# -----------------------------
# Playing cards
# -----------------------------

def can_play_card(gs: GameState, player: PlayerState, card_uid: int) -> bool:
    return card_uid in player.hand


def play_ocean(gs: GameState, player: PlayerState, card_uid: int) -> None:
    cd = gs.card_db[card_uid]
    if not can_play_card(gs, player, card_uid):
        raise ValueError("Card not in hand.")
    if cd.species.lower() != "ocean" and cd.direction.lower() != "n/a":
        # If you mark oceans as species "Ocean", great. If not, oceans usually have direction "N/A".
        pass

    player.hand.remove(card_uid)
    player.board_oceans.append(card_uid)
    player.ocean_slots[card_uid] = OceanSlots()
    gs.log.append(f"{player.name} played Ocean: {cd.name} (uid={card_uid}).")

    # run main ability for oceans too, if you want
    run_main_ability(gs, card_uid, player, ctx={"played_as": "ocean"})


def play_to_ocean(gs: GameState, player: PlayerState, card_uid: int, ocean_uid: int) -> None:
    """
    Attach a non-ocean card to an existing ocean on the player's board
    using the card's direction (Up/Down/Left/Right).
    """
    if not can_play_card(gs, player, card_uid):
        raise ValueError("Card not in hand.")
    if ocean_uid not in player.ocean_slots:
        raise ValueError("Ocean not on player's board.")

    cd = gs.card_db[card_uid]
    direction = normalize_direction(cd.direction)
    if direction == "n/a":
        raise ValueError("This card has no direction; cannot attach to ocean.")
    slot_cards = player.ocean_slots[ocean_uid].slot(direction)
    # One card per side unless card explicitly allows stacking.
    share_ok = ("lobster" in cd.name.lower()) or ("yellowfin tuna" in cd.name.lower())
    if slot_cards and not share_ok:
        raise ValueError("That ocean side is already occupied.")

    player.hand.remove(card_uid)
    slot_cards.append(card_uid)

    gs.log.append(
        f"{player.name} played {cd.name} (uid={card_uid}) to ocean uid={ocean_uid} on {direction}."
    )

    # normal ability triggers when played
    run_main_ability(gs, card_uid, player, ctx={"ocean_uid": ocean_uid, "played_to": direction})


# -----------------------------
# Star ability rule (your exact mechanic)
# -----------------------------

def symbols_match(a: str, b: str) -> bool:
    sa = normalize_symbol(a)
    sb = normalize_symbol(b)
    if sa in {"", "n/a"} or sb in {"", "n/a"}:
        return False
    return sa == sb


def discard_for_star(gs: GameState, player: PlayerState, discard_uid: int, target_uid: int) -> bool:
    """
    Your rule:
    - You discard a card (goes to discard pile).
    - If the card you WANT to play (target_uid) has a matching symbol
      with the card you are discarding (discard_uid),
      you get the discarded card's *star* ability.
    Returns True if star ability executed.
    """
    if discard_uid not in player.hand:
        raise ValueError("Discard card must be in hand.")
    if target_uid not in player.hand:
        raise ValueError("Target card must be in hand (the one you're about to play).")

    dcd = gs.card_db[discard_uid]
    tcd = gs.card_db[target_uid]

    player.hand.remove(discard_uid)
    player.discard.append(discard_uid)

    gs.log.append(
        f"{player.name} discarded {dcd.name} (uid={discard_uid}) attempting star vs {tcd.name} (uid={target_uid})."
    )

    if symbols_match(dcd.symbol, tcd.symbol):
        # Execute the star ability of the discarded card.
        run_star_ability(gs, discard_uid, player, ctx={"target_uid": target_uid})
        gs.log.append(f"STAR triggered: {dcd.name} star ability executed (matched {dcd.symbol}).")
        return True

    gs.log.append("STAR failed: symbols did not match.")
    return False


# -----------------------------
# Turn structure helpers
# -----------------------------

def begin_turn(gs: GameState) -> None:
    p = gs.current_player()
    gs.log.append(f"--- {p.name}'s turn begins ---")
    # Optional: draw at start of turn if your rules do that:
    # draw(gs, p, 1)

def end_turn(gs: GameState) -> None:
    # if end-game is active, count down turns after trigger
    if gs.end_game_triggered:
        # Only count down for players AFTER the trigger player finishes.
        # If current turn belongs to someone other than the trigger player, decrement.
        if gs.turn_index != gs.end_game_trigger_turn_player:
            gs.turns_remaining_after_trigger -= 1
            gs.log.append(f"End-game countdown: {gs.turns_remaining_after_trigger} turns remaining.")

    p = gs.current_player()
    gs.log.append(f"--- {p.name}'s turn ends ---")

    next_turn(gs)


# -----------------------------
# Scoring (simple baseline; customize)
# -----------------------------

def all_board_cards(player: PlayerState) -> List[int]:
    uids: List[int] = []
    for ocean_uid, slots in player.ocean_slots.items():
        uids.append(ocean_uid)
        uids.extend(slots.all_cards())
    return uids


def compute_score(gs: GameState, player: PlayerState) -> int:
    """
    Baseline scoring placeholder.
    You will likely replace this with your real scoring rules.
    """
    # Example: score is sum of all "+X" found in text (very rough) + existing stored score
    total = 0
    for uid in all_board_cards(player):
        cd = gs.card_db[uid]
        # very rough: find +number patterns
        for m in re.finditer(r"\+(\d+)", cd.text):
            total += int(m.group(1))
    return total


def finalize_scores(gs: GameState) -> None:
    for p in gs.players:
        p.score = compute_score(gs, p)
        gs.log.append(f"{p.name} final score = {p.score}")


# -----------------------------
# Special: END GAME card helper
# -----------------------------

def play_end_game_card(gs: GameState, player: PlayerState, card_uid: int) -> None:
    if card_uid not in player.hand:
        raise ValueError("END GAME card must be in hand.")
    cd = gs.card_db[card_uid]
    player.hand.remove(card_uid)
    player.discard.append(card_uid)
    gs.log.append(f"{player.name} played END GAME card: {cd.name} (uid={card_uid}).")
    trigger_end_game(gs)


# -----------------------------
# Card parsing & auto-ability generation
# -----------------------------

def parse_card_line(line: str) -> Optional[CardDef]:
    """Parse a tab-separated card line: UID\tName\tReward\tSpecies\tCost\tDirection\tSymbol"""
    def clean_cell(s: str) -> str:
        # Excel helper apostrophes are formatting noise; strip leading ones.
        return s.strip().lstrip("'").strip()

    parts = line.strip().split('\t')
    if len(parts) < 7:
        return None
    try:
        uid = int(clean_cell(parts[0]))
        name = clean_cell(parts[1])
        text = clean_cell(parts[2])
        species = clean_cell(parts[3])
        cost = int(clean_cell(parts[4]))
        direction = clean_cell(parts[5])
        symbol = clean_cell(parts[6])
        return CardDef(uid, name, species, cost, direction, symbol, text)
    except (ValueError, IndexError):
        return None


def auto_register_abilities_from_text() -> None:
    """Generate and register abilities based on card text patterns."""
    for uid, cd in list(gs.card_db.items()) if 'gs' in globals() else []:
        _register_card_ability(cd)


def _register_card_ability(cd: CardDef) -> None:
    """Auto-detect and register an ability for a card based on its text."""
    name_lower = cd.name.lower()
    text = cd.text.lower()
    
    # Skip if already registered
    if name_lower in ABILITIES or name_lower in STAR_ABILITIES:
        return
    
    # Parse main vs star
    main, star = split_main_and_star(cd.text)
    
    # Register main ability if it has one
    if main and main not in {"", "n/a"}:
        @register_ability(cd.name)
        def main_ability(gs, card_uid, player, ctx=None, card=cd, m=main):
            # Generic pattern handler for common main abilities
            _execute_main_pattern(gs, player, m, card, ctx)
    
    # Register star ability if it has one
    if star and star not in {"", "n/a"}:
        @register_star_ability(cd.name)
        def star_ability(gs, card_uid, player, ctx=None, card=cd, s=star):
            # Generic pattern handler for common star abilities
            _execute_star_pattern(gs, player, s, card, ctx)


def _execute_main_pattern(
    gs: GameState,
    player: PlayerState,
    text: str,
    card: CardDef,
    ctx: Optional[dict] = None,
) -> None:
    """Execute common main ability patterns."""
    t = text.lower()
    board = [gs.card_db[uid] for uid in all_board_cards(player)]
    ms = (ctx or {}).get("ms")
    is_human_turn = bool((ctx or {}).get("is_human_turn", False))
    turn_state = (ctx or {}).get("turn_state")

    # Direct +X values. Skip "+N" that are part of "+N per <type>" patterns to
    # avoid double-counting when the per-type loop below also runs.
    for m in re.finditer(r"\+(\d+)(?!\s+per\b)", t):
        player.score += int(m.group(1))

    # Register reactive draw listeners ("when ... is played") as persistent board effects.
    if "draw one when a game fish is played" in t or "draw one when a gamefish is played" in t:
        player.flags["trigger_draw_on_game_fish"] = int(player.flags.get("trigger_draw_on_game_fish", 0)) + 1
        gs.log.append(f"{player.name} enables: draw 1 when a Game Fish is played ({card.name}).")
    if "draw one when you play an animal on the ocean surface" in t:
        player.flags["trigger_draw_on_surface_play"] = int(player.flags.get("trigger_draw_on_surface_play", 0)) + 1
        gs.log.append(f"{player.name} enables: draw 1 when they play on ocean surface ({card.name}).")
    if "draw one when you play a card on the ocean floor" in t:
        player.flags["trigger_draw_on_floor_play"] = int(player.flags.get("trigger_draw_on_floor_play", 0)) + 1
        gs.log.append(f"{player.name} enables: draw 1 when they play on ocean floor ({card.name}).")

    # Big Eye Tuna: "draw one for each yellowfin tuna on your board"
    if "draw one for each yellowfin tuna" in t:
        yf_count = sum(1 for c in board if c.name.lower() == "yellowfin tuna")
        if yf_count > 0:
            draw(gs, player, yf_count, ms)
            gs.log.append(f"{player.name} draws {yf_count} from {card.name} (1 per Yellowfin Tuna).")

    # Draw-now text (not "draw one when ..." or "draw one for each ...").
    has_reactive_draw_one = (
        "draw one when" in t
        or "when you play" in t
        or "when a game fish is played" in t
        or "when a gamefish is played" in t
        or "draw one for each" in t
    )
    star_active = bool((ctx or {}).get("star_active", False))
    # "draw one or" at end of text means draw-one is the non-star alternative; skip when star fires.
    draw_one_is_or_alt = bool(re.search(r"draw one or\s*$", t))
    if "draw one" in t and not has_reactive_draw_one and not (star_active and draw_one_is_or_alt):
        n = choose_optional_draw_count(gs, player, 1)
        draw(gs, player, n, ms)
        gs.log.append(f"{player.name} draws {n} from {card.name} main ability.")
    if "draw 2" in t or "draw two" in t:
        n = choose_optional_draw_count(gs, player, 2)
        draw(gs, player, n, ms)
        gs.log.append(f"{player.name} draws {n} from {card.name} main ability.")

    # Tarpon-style hand cycling: "Discard and draw that many cards".
    if "discard and draw that many cards" in t:
        candidates = list(player.hand)
        if candidates:
            chosen: List[int] = []

            is_web_human = bool(player.flags.get("_web_human", False))
            if is_web_human:
                # Web human: set a flag so run_match's interactive loop handles the discard.
                # The draw-back happens there too — do nothing else here.
                player.flags["_tarpon_discard_active"] = True
                gs.log.append(f"{player.name} Tarpon effect: choose cards to discard via web UI.")
            elif is_human_turn and ms is not None:
                # Terminal human turn: choose any number of cards (including 0).
                while True:
                    print(f"Tarpon effect: discard any number of cards (0..{len(candidates)}) and draw that many.")
                    print("Your hand:")
                    for uid in candidates:
                        print("  " + entry_label(ms, gs, uid))
                    raw = input("Discard uid(s), space-separated (blank=0): ").strip()
                    if not raw:
                        chosen = []
                        break
                    parts = [p for p in raw.split() if p]
                    try:
                        picks = [int(p) for p in parts]
                    except ValueError:
                        print("Use numeric uids.")
                        continue
                    if len(set(picks)) != len(picks):
                        print("Duplicate uid entered.")
                        continue
                    if any(uid not in candidates for uid in picks):
                        print("One or more selected uids are not in your hand.")
                        continue
                    chosen = picks
                    break
            else:
                # AI: discard many when cycling, keep only the most useful few.
                # Higher score = better discard candidate.
                def discard_priority(uid: int) -> float:
                    c = gs.card_db[uid]
                    score = float(c.cost)
                    if is_ocean(c):
                        score += 0.4
                    txt = c.text.lower()
                    if "draw" in txt:
                        score -= 0.3
                    if "play again" in txt or "go again" in txt or "for free" in txt:
                        score -= 0.35
                    return score

                ranked = sorted(candidates, key=discard_priority, reverse=True)
                if len(candidates) >= 8:
                    discard_n = len(candidates) - 2
                elif len(candidates) >= 5:
                    discard_n = len(candidates) - 3
                else:
                    discard_n = max(1, len(candidates) - 2)
                chosen = ranked[:max(0, discard_n)]

            if not is_web_human:
                for uid in chosen:
                    if uid in player.hand:
                        player.hand.remove(uid)
                        if isinstance(turn_state, TurnState):
                            turn_state.discarded_entry_uids.add(uid)
                        if ms is not None:
                            try:
                                add_to_pool(ms, uid)
                            except Exception:
                                player.discard.append(uid)
                        else:
                            player.discard.append(uid)
                draw(gs, player, len(chosen), ms)
                gs.log.append(
                    f"{player.name} discards {len(chosen)} and draws {len(chosen)} from {card.name} main ability."
                )

    # Generic "+N per <type>" patterns.
    type_map = {
        "bird": "bird",
        "crustacean": "crustacean",
        "coral": "coral",
        "invertebrate": "invertebrate",
        "baitfish": "baitfish",
        "cephalopod": "cephalopod",
        "mammal": "mammal",
        "uncharted": "uncharted",
        "n/a": "uncharted",
        "crosscurrent": "crosscurrent",
    }
    for m in re.finditer(r"\+(\d+)\s+per\s+([a-z ]+)", t):
        n = int(m.group(1))
        raw = m.group(2).strip()

        if "matching symbol" in raw:
            count = sum(1 for c in board if normalize_symbol(c.symbol) == normalize_symbol(card.symbol) and c.uid != card.uid)
            player.score += n * count
            continue
        if "symbol-less" in raw:
            count = sum(1 for c in board if normalize_symbol(c.symbol) in {"", "n/a"})
            player.score += n * count
            continue
        if "uncharted animal" in raw or "n/a animal" in raw or "crosscurrent animal" in raw:
            count = sum(
                1
                for c in board
                if c.species.lower() in {"uncharted", "n/a", "crosscurrent"} and c.direction.strip().lower() != "n/a"
            )
            player.score += n * count
            continue
        if "mahi mahi" in raw:
            count = sum(1 for c in board if c.name.lower() == "mahi mahi")
            player.score += n * count
            continue
        if "yellowfin tuna" in raw:
            count = sum(1 for c in board if c.name.lower() == "yellowfin tuna")
            player.score += n * count
            continue
        if "mandarin goby" in raw:
            count = sum(1 for c in board if c.name.lower() == "mandarin goby")
            player.score += n * count
            continue
        if "card attached" in raw:
            attached = sum(len(slots.all_cards()) for slots in player.ocean_slots.values())
            player.score += n * attached
            continue

        species_key = raw.replace("each ", "").replace("game fish", "game fish").strip()
        species_key = species_key[:-1] if species_key.endswith("s") else species_key
        mapped = type_map.get(species_key)
        if mapped:
            count = sum(1 for c in board if c.species.lower() == mapped)
            player.score += n * count

    # Count tables like "1 = 5 | 2 = 25".
    # Baitfish diversity charts are end-game only (final_points is authoritative) — skip here.
    thresholds = []
    for m in re.finditer(r"(\d+)\s*=\s*(\d+)", t):
        thresholds.append((int(m.group(1)), int(m.group(2))))
    if thresholds and "different species of baitfish" not in t:
        if "matching symbol" in t:
            value = sum(1 for c in board if normalize_symbol(c.symbol) == normalize_symbol(card.symbol))
        elif "at least three cephalopods" in t:
            value = sum(1 for c in board if c.species.lower() == "cephalopod")
        else:
            value = sum(1 for c in board if c.name.lower() == card.name.lower())
        best = 0
        for k, v in thresholds:
            if value >= k:
                best = max(best, v)
        player.score += best

    if "+1 per every two ocean" in t:
        player.score += len(player.board_oceans) // 2
    if "+2 or +3 if you have the most" in t:
        player.score += 2

    # Turn-window engines.
    # Loggerhead Sea Turtle: chain paid plays this turn.
    if "play any number of cards by paying the costs" in t or "play any number of cards by paying the cost" in t:
        player.flags["multi_play_paid_turn"] = True
        gs.log.append(f"{player.name} can keep playing cards this turn by paying costs ({card.name}).")

    # Hermit Crab style: chain free baitfish plays this turn.
    if (
        "play any # of baitfish for free" in t
        or "play any number of baitfish for free" in t
        or "play any number of baitfish this turn" in t
    ):
        player.flags["free_baitfish_chain"] = True
        gs.log.append(f"{player.name} can keep playing free Baitfish this turn ({card.name}).")
    # Updated Hermit Crab text: up to two free Yellowfin Tuna this turn.
    if "two free yellowfin tuna" in t:
        player.flags["free_yellowfin_tuna"] = 2
        gs.log.append(f"{player.name} can play up to 2 free Yellowfin Tuna this turn ({card.name}).")


def _flag_count(v: object) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return max(0, int(v))
    return 0


def pending_replay_actions(player: PlayerState) -> int:
    return _flag_count(player.flags.get("play_again", 0)) + _flag_count(player.flags.get("go_again", 0))


def consume_replay_actions(player: PlayerState) -> int:
    n = pending_replay_actions(player)
    player.flags["play_again"] = 0
    player.flags["go_again"] = 0
    return n


def grant_replay_actions(player: PlayerState, n: int = 1) -> None:
    if n <= 0:
        return
    player.flags["play_again"] = _flag_count(player.flags.get("play_again", 0)) + int(n)


def _execute_star_pattern(
    gs: GameState,
    player: PlayerState,
    text: str,
    card: CardDef,
    ctx: Optional[dict] = None,
) -> None:
    """Execute common star ability patterns."""
    t = text.lower()
    ms = (ctx or {}).get("ms")

    if "draw three" in t:
        n = choose_optional_draw_count(gs, player, 3)
        draw(gs, player, n, ms)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    elif "draw 2" in t or "draw two" in t:
        n = choose_optional_draw_count(gs, player, 2)
        draw(gs, player, n, ms)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    elif "draw one" in t:
        n = choose_optional_draw_count(gs, player, 1)
        draw(gs, player, n, ms)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    if "play again" in t or "go again" in t:
        if should_take_optional_replay(gs, player, card):
            grant_replay_actions(player, 1)
            gs.log.append(f"{player.name} gets to play again from {card.name} star ability.")
        else:
            gs.log.append(f"{player.name} skips optional replay from {card.name} star ability.")
    if "play a free mammal" in t:
        player.flags["free_mammal"] = True
        gs.log.append(f"{player.name} can play a free Mammal from {card.name} star ability.")
    if "play a free baitfish" in t:
        player.flags["free_baitfish"] = True
        gs.log.append(f"{player.name} can play a free Baitfish from {card.name} star ability.")
    if "play a free game fish" in t:
        player.flags["free_game_fish"] = True
        gs.log.append(f"{player.name} can play a free Game Fish from {card.name} star ability.")
    if "play any number of cephalopods for free" in t:
        player.flags["free_cephalopods"] = True
        gs.log.append(f"{player.name} can play Cephalopods for free from {card.name} star ability.")
    elif "play a free cephalopod" in t:
        # Single-use free cephalopod (e.g. Grooved Brain Coral) — consumed after one play.
        player.flags["free_cephalopod_once"] = True
        gs.log.append(f"{player.name} can play one free Cephalopod from {card.name} star ability.")
    if "free crustacean" in t:
        player.flags["free_crustacean"] = True
        gs.log.append(f"{player.name} can play a free Crustacean from {card.name} star ability.")
    if "free invertebrate" in t:
        player.flags["free_invertebrate"] = True
        gs.log.append(f"{player.name} can play a free Invertebrate from {card.name} star ability.")
    if "play a free coral" in t:
        player.flags["free_coral"] = True
        gs.log.append(f"{player.name} can play a free Coral from {card.name} star ability.")


def resolve_reactive_draw_triggers(
    gs: GameState,
    played_by: PlayerState,
    played_card: CardDef,
    action_kind: str,
    ms=None,
) -> None:
    """Resolve persistent 'draw one when ...' listeners after a successful play."""
    species = played_card.species.strip().lower()
    direction = normalize_direction(played_card.direction)

    is_game_fish_play = species == "game fish"
    is_cephalopod_play = species == "cephalopod"
    is_surface_play = action_kind == "play_to_ocean" and direction == "up"
    is_floor_play = action_kind == "play_to_ocean" and direction == "down"

    for owner in gs.players:
        if owner is played_by and is_cephalopod_play:
            n = int(owner.flags.get("trigger_draw_on_cephalopod", 0))
            if n > 0:
                draw(gs, owner, n, ms)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (cephalopod played)."
                )

        if owner is played_by and is_game_fish_play:
            # Skip reactive draw if the game fish itself already draws from its main ability.
            _main_text, _ = split_main_and_star(played_card.text)
            _card_draws = any(k in _main_text.lower() for k in ("draw one", "draw two", "draw 2", "draw three"))
            if not _card_draws:
                n = int(owner.flags.get("trigger_draw_on_game_fish", 0))
                if n > 0:
                    draw(gs, owner, n, ms)
                    gs.log.append(
                        f"{owner.name} draws {n} from reactive trigger (game fish played)."
                    )

        if owner is played_by and is_surface_play:
            n = int(owner.flags.get("trigger_draw_on_surface_play", 0))
            # Prevent self-trigger: if the card just placed IS a surface-draw listener,
            # subtract 1 so it doesn't fire for its own placement.
            played_text = played_card.text.lower()
            if "draw one when you play an animal on the ocean surface" in played_text:
                n = max(0, n - 1)
            if n > 0:
                draw(gs, owner, n, ms)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (played on ocean surface)."
                )

        if owner is played_by and is_floor_play:
            n = int(owner.flags.get("trigger_draw_on_floor_play", 0))
            # Prevent self-trigger: if the card just played IS itself a floor-draw listener,
            # subtract 1 so it doesn't fire for its own placement.
            played_text = played_card.text.lower()
            if "draw one when you play a card on the ocean floor" in played_text:
                n = max(0, n - 1)
            if n > 0:
                draw(gs, owner, n, ms)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (played on ocean floor)."
                )


def sync_reactive_trigger_flags(gs: GameState, player: PlayerState) -> None:
    """Rebuild reactive listener counts from the player's current board state."""
    game_fish = 0
    surface = 0
    floor = 0
    cephalopod = 0
    for uid in all_board_cards(player):
        c = gs.card_db[uid]
        t = c.text.lower()
        if ("draw one when a game fish is played" in t or "draw one when a gamefish is played" in t
                or "draw one when you play a game fish" in t):
            game_fish += 1
        if "draw one when you play an animal on the ocean surface" in t:
            surface += 1
        if "draw one when you play a card on the ocean floor" in t:
            floor += 1
        if "draw one when you play a cephalopod" in t:
            cephalopod += 1

    if game_fish > 0:
        player.flags["trigger_draw_on_game_fish"] = game_fish
    else:
        player.flags.pop("trigger_draw_on_game_fish", None)

    if surface > 0:
        player.flags["trigger_draw_on_surface_play"] = surface
    else:
        player.flags.pop("trigger_draw_on_surface_play", None)

    if floor > 0:
        player.flags["trigger_draw_on_floor_play"] = floor
    else:
        player.flags.pop("trigger_draw_on_floor_play", None)

    if cephalopod > 0:
        player.flags["trigger_draw_on_cephalopod"] = cephalopod
    else:
        player.flags.pop("trigger_draw_on_cephalopod", None)


def trigger_board_symbol_star_draws(
    gs: GameState,
    ms: Optional[Any],
    player: PlayerState,
    discarded_entry_uids: List[int],
) -> None:
    """
    After any cards are discarded to the pool (payment or end-of-turn batch),
    fire the 'draw' star ability of each board card whose symbol matches a
    discarded card's symbol.  Each matching board card fires at most once per call.
    """
    discarded_syms: set = set()
    for uid in discarded_entry_uids:
        for face_uid in entry_faces(ms, uid):
            cd = gs.card_db.get(face_uid)
            if cd is not None:
                sym = normalize_symbol(cd.symbol)
                if sym not in {"", "n/a"}:
                    discarded_syms.add(sym)
    if not discarded_syms:
        return

    triggered: set = set()
    for face_uid in all_board_cards(player):
        if face_uid in triggered:
            continue
        cd = gs.card_db.get(face_uid)
        if cd is None:
            continue
        sym = normalize_symbol(cd.symbol)
        if sym in {"", "n/a"} or sym not in discarded_syms:
            continue
        _, star_text = split_main_and_star(cd.text)
        if not star_text or "draw" not in star_text.lower():
            continue
        triggered.add(face_uid)
        draw(gs, player, 1, ms)
        gs.log.append(
            f"{player.name}: {cd.name} ({sym}) drew 1 — matching symbol discarded."
        )


# -----------------------------
# Global GameState (for bulk operations)
# -----------------------------

gs: Optional[GameState] = None  # Set by initialize_game() or load_cards()


def load_cards_from_lines(
    lines: str,
    card_db: Dict[int, CardDef],
    source: str = "<memory>",
    strict: bool = False,
) -> Dict[int, CardDef]:
    """Parse tab-separated card lines and add to card_db.

    When strict=True:
    - malformed lines raise errors with source + line number
    - duplicate UIDs raise errors with source + line number
    """
    errors: List[str] = []
    for lineno, raw in enumerate(lines.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        card = parse_card_line(raw)
        if card is None:
            msg = f"{source}:{lineno}: malformed row (expected 7 tab-separated fields)"
            if strict:
                errors.append(msg)
            else:
                continue
            continue
        if card.uid in card_db:
            msg = f"{source}:{lineno}: duplicate uid {card.uid} for card '{card.name}'"
            if strict:
                errors.append(msg)
                continue
        card_db[card.uid] = card

    if errors and strict:
        preview = "\n".join(errors[:40])
        more = f"\n... and {len(errors) - 40} more" if len(errors) > 40 else ""
        raise ValueError(f"Card data validation failed:\n{preview}{more}")

    return card_db


def register_all_card_abilities(card_db: Dict[int, CardDef]) -> None:
    """Auto-register abilities for all cards in the database."""
    ABILITIES_BY_UID.clear()
    STAR_ABILITIES_BY_UID.clear()
    for uid, cd in card_db.items():
        _register_card_ability_impl(cd)


def _register_card_ability_impl(cd: CardDef) -> None:
    """Auto-detect and register an ability for a card based on its text."""
    name_lower = cd.name.lower()

    # Parse main vs star
    main, star = split_main_and_star(cd.text)
    
    # Register main ability if it has one
    if main and main not in {"", "n/a"}:
        def main_ability_fn(gs, card_uid, player, ctx=None, text=main, card=cd):
            _execute_main_pattern(gs, player, text, card, ctx)
        ABILITIES_BY_UID[cd.uid] = main_ability_fn
        # Keep name-based fallback for compatibility with older paths.
        ABILITIES.setdefault(name_lower, main_ability_fn)
    
    # Register star ability if it has one
    if star and star not in {"", "n/a"}:
        def star_ability_fn(gs, card_uid, player, ctx=None, text=star, card=cd):
            _execute_star_pattern(gs, player, text, card, ctx)
        STAR_ABILITIES_BY_UID[cd.uid] = star_ability_fn
        STAR_ABILITIES.setdefault(name_lower, star_ability_fn)


# ===== Combined Main Simulator =====

"""Rules-faithful simulator + AI for The Fish Game (Ocean Shuffle style)."""

import argparse
import copy
import html
import json
import os
import secrets
import sys
import time
from typing import Any



@dataclass
class Action:
    kind: str  # draw, play_ocean, play_to_ocean, move_between_oceans, end_turn
    card_uid: int = -1
    face_uid: Optional[int] = None
    ocean_uid: Optional[int] = None
    source_ocean_uid: Optional[int] = None
    draw_from_pool: int = 0  # for draw action: 0/1/2
    pool_pick_uids: List[int] = field(default_factory=list)  # optional specific pool picks for human draw
    use_star: bool = False
    payment_uids: List[int] = field(default_factory=list)  # optional explicit payment cards from client


@dataclass
class TurnState:
    star_activations: int = 0
    free_followups: int = 0
    played_face_uids: List[int] = field(default_factory=list)
    discarded_entry_uids: set[int] = field(default_factory=set)
    replay_pickup_used: bool = False
    force_end_turn: bool = False
    draws_this_turn: int = 0  # tracks cards drawn this turn for discard-and-redraw


@dataclass
class MatchState:
    pool: List[int] = field(default_factory=list)  # face-up discard board
    discard_pile: List[int] = field(default_factory=list)  # face-down cleared pool
    end_game_uid: Optional[int] = None
    end_game_triggered: bool = False
    final_turns_remaining: int = 0
    pair_primary_to_faces: Dict[int, Tuple[int, int]] = field(default_factory=dict)
    face_to_primary: Dict[int, int] = field(default_factory=dict)


BRAIN_PATH = "fish_ai_brain.json"
LIVE_LOG_PATH = "last_live_game_log.txt"
HAND_LIMIT = 10
LIVE_STATE_PATH = "last_live_game_state.json"
BRAIN_GAME_MEMORY_CAP = 20000
STRATEGIC_SHORTLIST_SIZE = 7
STRATEGIC_CONFIRM_WEIGHT = 0.55
STRATEGIC_FIRST_TURN_MAX_ACTIONS = 4
STRATEGIC_OPP_TURN_MAX_ACTIONS = 3
STRATEGIC_RETURN_TURN_MAX_ACTIONS = 3
STRATEGIC_EXTRA_LOOKAHEAD_MAX_ACTIONS = 2
FREE_PLAY_FLAGS = (
    "free_mammal",
    "free_baitfish",
    "free_game_fish",
    "free_cephalopods",
    "free_cephalopod_once",
    "free_crustacean",
    "free_invertebrate",
    "free_coral",
)

PLAYSTYLE_SET = {"RANDOM", "AGGRESSIVE", "CONSERVATIVE", "OPPORTUNISTIC", "RISK_SEEKING"}


class BrainFileCorruptionError(RuntimeError):
    """Raised when the persisted AI brain file is unreadable/corrupt."""


def brain_fix_prompt(path: str, reason: str) -> str:
    abs_path = os.path.abspath(path)
    return (
        f"AI brain file is corrupted or unreadable: {abs_path}\n"
        f"Reason: {reason}\n"
        "Game stopped to prevent bad learning state.\n"
        "Fix it before running again:\n"
        f"  1) Restore a backup over {abs_path}\n"
        f"  2) or delete {abs_path} to regenerate a fresh brain\n"
    )

HUMAN_REALISM_CONFIG: Dict[str, Any] = {
    # Master switch for the human-like policy layer.
    "enabled": True,
    # Optional effects from the checklist.
    # Rules-fidelity default: card draw/replay effects should resolve exactly as printed.
    "optional_draw_effects": False,
    "optional_replay_effects": False,
    # "Every game independent" behavior from the checklist.
    "independent_games": False,
    "no_archetype_preweight": False,
    # Human-limited inference (no perfect hidden-info planning).
    "human_limited_inference": False,
    # Visible-card memory + decay for scarcity tiers.
    "memory_decay": 0.92,
    # Exploration for near-EV branches.
    "adaptive_exploration": True,
}


class TerminalProgressBar:
    def __init__(
        self,
        total: int,
        label: str = "Progress",
        width: int = 30,
        enabled: bool = True,
    ) -> None:
        self.total = int(total)
        self.label = label
        self.width = max(10, int(width))
        self.current = 0
        self._last_percent = -1
        self._tty = sys.stdout.isatty()
        self._last_bucket = -1
        self.enabled = bool(enabled) and self.total > 0
        if self.total <= 0:
            self.total = 1
        if self.enabled:
            self._render(force=True)

    def _render(self, force: bool = False) -> None:
        if not self.enabled:
            return
        pct = int((self.current * 100) / self.total)
        if self._tty:
            if not force and self.current < self.total and pct == self._last_percent:
                return
            self._last_percent = pct
        else:
            bucket = pct // 5
            if not force and self.current < self.total and bucket == self._last_bucket:
                return
            self._last_bucket = bucket
        filled = int((self.current * self.width) / self.total)
        bar = "#" * filled + "-" * (self.width - filled)
        end = "\n" if (not self._tty or self.current >= self.total) else "\r"
        print(f"{self.label} [{bar}] {self.current}/{self.total} ({pct:3d}%)", end=end, flush=True)

    def advance(self, step: int = 1) -> None:
        if step <= 0:
            return
        self.current = min(self.total, self.current + int(step))
        self._render()

    def finish(self) -> None:
        if self.current >= self.total:
            return
        self.current = self.total
        self._render(force=True)

    def print_message(self, message: str) -> None:
        if self.enabled and self._tty:
            print()
        print(message)


def _safe_final_points(gs: GameState, player: PlayerState) -> float:
    try:
        return float(final_points(gs, player))
    except Exception:
        return float(player.score)


def player_playstyle(player: PlayerState) -> str:
    raw = str(player.flags.get("_playstyle", "OPPORTUNISTIC")).upper()
    return raw if raw in PLAYSTYLE_SET else "OPPORTUNISTIC"


def score_gap_vs_table(gs: GameState, player: PlayerState) -> float:
    my = _safe_final_points(gs, player)
    others = [_safe_final_points(gs, p) for p in gs.players if p is not player]
    if not others:
        return 0.0
    return my - (sum(others) / len(others))


def endgame_pressure(deck_remaining: int) -> float:
    if deck_remaining > 15:
        return 0.0
    if deck_remaining > 10:
        return 0.45
    if deck_remaining > 5:
        return 0.70
    if deck_remaining > 3:
        return 0.88
    return 0.96


def endgame_pressure_label(deck_remaining: int) -> str:
    if deck_remaining > 15:
        return "LOW"
    if deck_remaining > 10:
        return "MEDIUM"
    if deck_remaining > 5:
        return "HIGH"
    if deck_remaining > 3:
        return "NEAR_CERTAIN"
    return "ALMOST_GUARANTEED"


def open_slot_count(player: PlayerState) -> int:
    open_slots = 0
    for ocean_uid in player.board_oceans:
        slots = player.ocean_slots[ocean_uid]
        for d in ("up", "down", "left", "right"):
            if len(slots.slot(d)) == 0:
                open_slots += 1
    return open_slots


def classify_card_role(card: CardDef) -> str:
    name = card.name.strip().lower()
    text = card.text.lower()
    species = card.species.strip().lower()
    if is_ocean(card):
        if any(k in text for k in ("draw one", "draw two", "draw 2", "play again", "card attached")):
            return "ENGINE"
        return "FLEX"
    if name in {"yellowfin tuna", "lobster"}:
        return "STACK TARGET"
    if "play any number" in text or "for free" in text or "play again" in text or "go again" in text:
        return "ENGINE"
    if "per " in text or re.search(r"\b\d+\s*=\s*\d+", text):
        return "SCALER"
    if "draw one" in text or "draw two" in text or "draw 2" in text or "draw three" in text:
        return "ENABLER"
    if card.cost <= 1:
        return "PAYMENT"
    if species in {"bird", "baitfish", "game fish", "cephalopod", "mammal", "coral", "crustacean"}:
        return "FLEX"
    return "FLEX"


def hand_role_balance(gs: GameState, ms: MatchState, player: PlayerState) -> Dict[str, int]:
    counts = {"ENGINE": 0, "SCALER": 0, "ENABLER": 0, "STACK TARGET": 0, "PAYMENT": 0, "FLEX": 0}
    for entry_uid in player.hand:
        roles = [classify_card_role(gs.card_db[face_uid]) for face_uid in entry_faces(ms, entry_uid)]
        # Use the strongest role observed on this two-sided card.
        pick = "FLEX"
        for role in ("ENGINE", "SCALER", "ENABLER", "STACK TARGET", "PAYMENT", "FLEX"):
            if role in roles:
                pick = role
                break
        counts[pick] += 1
    return counts


def _board_cards(gs: GameState, player: PlayerState) -> List[CardDef]:
    cards: List[CardDef] = []
    cards.extend(gs.card_db[uid] for uid in player.board_oceans)
    for ocean_uid in player.board_oceans:
        cards.extend(gs.card_db[uid] for uid in player.ocean_slots[ocean_uid].all_cards())
    return cards


def estimate_opponent_threat_level(gs: GameState, player: PlayerState) -> Tuple[float, str]:
    top = 0.0
    for op in gs.players:
        if op is player:
            continue
        bcards = _board_cards(gs, op)
        multiplier_cards = 0
        enabler_cards = 0
        for c in bcards:
            t = c.text.lower()
            if " per " in t or re.search(r"\b\d+\s*=\s*\d+", t):
                multiplier_cards += 1
            if any(k in t for k in ("draw one", "draw two", "draw 2", "draw three", "play again", "go again", "for free")):
                enabler_cards += 1
        o_slots = open_slot_count(op)
        threat = (0.20 * multiplier_cards) + (0.12 * enabler_cards)
        if o_slots >= 3:
            threat += 0.35
        elif o_slots >= 1:
            threat += 0.15
        top = max(top, threat)

    if top >= 1.15:
        return top, "HIGH"
    if top >= 0.55:
        return top, "MODERATE"
    return top, "LOW"


def _visible_card_names_for_player(gs: GameState, ms: MatchState, player: PlayerState) -> List[str]:
    names: List[str] = []
    # Own hand is known.
    for entry_uid in player.hand:
        for face_uid in entry_faces(ms, entry_uid):
            names.append(gs.card_db[face_uid].name.strip().lower())
    # Public board state.
    for p in gs.players:
        for c in _board_cards(gs, p):
            names.append(c.name.strip().lower())
    # Pool is public.
    for entry_uid in ms.pool:
        for face_uid in entry_faces(ms, entry_uid):
            names.append(gs.card_db[face_uid].name.strip().lower())
    return names


def update_visible_memory_for_player(gs: GameState, ms: MatchState, player: PlayerState) -> None:
    mem = player.flags.get("_visible_memory")
    if not isinstance(mem, dict):
        mem = {}
    decay = float(HUMAN_REALISM_CONFIG.get("memory_decay", 0.92))
    for k in list(mem.keys()):
        try:
            mem[k] = float(mem[k]) * decay
        except Exception:
            mem[k] = 0.0
        if float(mem[k]) < 0.05:
            del mem[k]
    for nm in _visible_card_names_for_player(gs, ms, player):
        mem[nm] = float(mem.get(nm, 0.0)) + 1.0
    player.flags["_visible_memory"] = mem


def scarcity_tier_for_name(player: PlayerState, name: str) -> str:
    mem = player.flags.get("_visible_memory")
    if not isinstance(mem, dict):
        return "RARE"
    v = float(mem.get(name.strip().lower(), 0.0))
    # Human-tiered scarcity (with uncertainty): never hard-eliminate.
    if v >= 7.0:
        return "COMMON"
    if v >= 3.0:
        return "LIMITED"
    if v >= 1.0:
        return "RARE"
    return "CRITICAL"


def style_params(style: str) -> Dict[str, float]:
    s = style.upper()
    if s == "RANDOM":
        return {"risk": 0.0, "tempo": 0.0, "explore": 0.25, "commit": 0.0}
    if s == "AGGRESSIVE":
        return {"risk": 0.45, "tempo": 0.55, "explore": 0.10, "commit": 0.35}
    if s == "CONSERVATIVE":
        return {"risk": -0.45, "tempo": 0.15, "explore": 0.04, "commit": -0.25}
    if s == "RISK_SEEKING":
        return {"risk": 0.70, "tempo": 0.35, "explore": 0.14, "commit": 0.20}
    # OPPORTUNISTIC
    return {"risk": 0.10, "tempo": 0.30, "explore": 0.08, "commit": 0.10}


def human_realism_enabled() -> bool:
    return bool(HUMAN_REALISM_CONFIG.get("enabled", False))


def use_historical_policy_bias() -> bool:
    # "Every game independent" means runtime decisions should not depend on
    # prior-game learned synergy/archetype maps unless realism mode is disabled.
    if human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("independent_games", False)):
        return False
    return True


def adaptive_exploration_rate(gs: GameState, player: PlayerState, base_epsilon: float) -> float:
    if not (human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("adaptive_exploration", False))):
        return base_epsilon
    style = player_playstyle(player)
    s = style_params(style)
    pressure = endgame_pressure(len(gs.deck))
    # Explore more in the mid game and with naturally exploratory playstyles.
    eps = max(base_epsilon, float(s.get("explore", 0.06)))
    # Once an engine is established, reduce randomness and commit to it.
    non_ocean_count = sum(1 for uid in player_board_face_uids(player) if not is_ocean(gs.card_db[uid]))
    if non_ocean_count >= 6:
        eps *= 0.35
    elif non_ocean_count >= 3:
        eps *= 0.55
    if pressure >= 0.88:
        eps *= 0.55
    elif pressure <= 0.45:
        eps *= 1.15
    if eps < 0.005:
        return 0.005
    if eps > 0.22:
        return 0.22
    return eps


def human_realism_action_adjustment(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    features: Optional[Dict[str, float]] = None,
) -> float:
    if not human_realism_enabled():
        return 0.0

    style = player_playstyle(player)
    s = style_params(style)
    deck_remaining = len(gs.deck)
    pressure = endgame_pressure(deck_remaining)
    gap = score_gap_vs_table(gs, player)
    open_slots = open_slot_count(player)
    role_counts = hand_role_balance(gs, ms, player)
    tempo = float(player.flags.get("_tempo_score", 0.0))
    threat_score, threat_label = estimate_opponent_threat_level(gs, player)
    need_symbols, need_species = star_prep_needs(gs, ms, player)
    hand_n = len(player.hand)

    f = features or {}
    immediate = float(f.get("immediate_delta", 0.0))
    deny_bonus = float(f.get("deny_bonus", 0.0))
    pick_value = float(f.get("pool_pick_value", 0.0))
    future_value = float(f.get("future_value", 0.0))
    adj = 0.0

    # Tempo bias from style + recent ability chaining.
    adj += min(2.0, max(-2.0, tempo)) * (0.09 * float(s.get("tempo", 0.0)))

    if action.kind == "draw":
        # Avoid over-drawing late unless trailing hard.
        if pressure >= 0.70 and gap > -8:
            adj -= 0.55
        if hand_n >= 9:
            adj -= 0.35
        # Value drafting from pool with purpose.
        if action.draw_from_pool > 0:
            adj += 0.15 * pick_value
            if deny_bonus > 0.0 and pick_value < 0.8:
                # Defensive denial is allowed, but must not dominate engine growth.
                adj -= 0.35
        # Preserve draw lines when hand is thin.
        if hand_n <= 5:
            adj += 0.5
        # Star setup drafting (symbol/species enablers).
        if action.pool_pick_uids:
            for uid in action.pool_pick_uids:
                for face_uid in entry_faces(ms, uid):
                    c = gs.card_db[face_uid]
                    sym = normalize_symbol(c.symbol)
                    if sym in need_symbols:
                        adj += 0.2
                    sp = c.species.strip().lower()
                    if sp in need_species:
                        adj += 0.2
        if future_value > 0.0:
            # Human planning: early/mid game draws that set up future turns are good.
            adj += (1.0 - 0.55 * pressure) * min(0.9, 0.22 * future_value)
        return max(-2.5, min(2.5, adj))

    play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(play_face_uid)
    if card is None:
        return 0.0
    lname = card.name.strip().lower()

    # Human-limited scarcity urgency.
    tier = scarcity_tier_for_name(player, lname)
    if tier == "CRITICAL":
        adj += 0.35
    elif tier == "RARE":
        adj += 0.20
    if threat_label == "HIGH" and tier in {"CRITICAL", "RARE"}:
        adj += 0.20

    # Concealment: keep Goby hidden until burst windows.
    if lname == "mandarin goby":
        gobies_on_board = sum(
            1 for uid in player_board_face_uids(player) if gs.card_db[uid].name.strip().lower() == "mandarin goby"
        )
        if pressure < 0.70 and gobies_on_board < 2:
            adj -= 0.85
        else:
            adj += 0.30

    if action.kind == "play_ocean":
        # Prevent over-spreading lanes without density.
        if player.board_oceans and open_slots >= 5:
            adj -= 0.90
        if role_counts.get("ENGINE", 0) <= 0 and role_counts.get("SCALER", 0) <= 0:
            adj -= 0.45
        if role_counts.get("PAYMENT", 0) <= 0 and card.cost > 0:
            adj -= 0.20
        if pressure >= 0.70:
            adj -= 0.30
        # Ocean floor only makes sense when no lanes exist.
        if not player.board_oceans:
            adj += 0.65
    else:
        # Board saturation: late game favors immediate points.
        if open_slots <= 2:
            adj += 0.15 * max(0.0, immediate)
            if immediate < 1.0:
                adj -= 0.25
        # Diminishing return guard.
        if immediate < 0.5 and pressure >= 0.70:
            adj -= 0.35
        elif immediate >= 4.0:
            adj += 0.30
        if future_value > 0.0:
            # Human setup bias: prioritize scalable board construction before late game.
            adj += (1.0 - pressure) * min(1.2, 0.25 * future_value)

    # Risk profile by standings.
    if gap > 10:
        # Leading: reduce variance.
        adj -= 0.35 * max(0.0, float(s.get("risk", 0.0)))
    elif gap < -10:
        # Trailing: allow more variance/aggression.
        adj += 0.45 * max(0.0, float(s.get("risk", 0.0)))

    # Commitment timing and mirror pressure.
    if threat_score > 1.1 and pressure >= 0.45:
        adj += 0.18 * float(s.get("commit", 0.0))

    if adj > 3.0:
        return 3.0
    if adj < -3.0:
        return -3.0
    return adj


def assign_runtime_playstyle(player: PlayerState, rng: random.Random, is_human: bool = False) -> None:
    existing = str(player.flags.get("_playstyle", "")).upper()
    if existing in PLAYSTYLE_SET:
        return
    if is_human:
        player.flags["_playstyle"] = "OPPORTUNISTIC"
        return
    roll = rng.random()
    if roll < 0.16:
        player.flags["_playstyle"] = "AGGRESSIVE"
    elif roll < 0.32:
        player.flags["_playstyle"] = "CONSERVATIVE"
    elif roll < 0.48:
        player.flags["_playstyle"] = "RISK_SEEKING"
    elif roll < 0.60:
        player.flags["_playstyle"] = "RANDOM"
    else:
        player.flags["_playstyle"] = "OPPORTUNISTIC"


def has_multi_play_window(player: PlayerState) -> bool:
    return bool(
        player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or player.flags.get("free_cephalopods", False)
        or player.flags.get("free_cephalopod_once", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    )


def update_tempo_after_action(player: PlayerState, action: Action, turn_state: TurnState) -> None:
    tempo = float(player.flags.get("_tempo_score", 0.0)) * 0.90
    delta = 0.0
    if action.kind == "draw":
        if action.draw_from_pool > 0:
            delta += 0.20
        if action.pool_pick_uids:
            delta += 0.25
    else:
        delta += 0.15
    if action.use_star:
        delta += 0.40
    if pending_replay_actions(player) > 0:
        delta += 1.00
    if turn_state.free_followups > 0:
        delta += 1.00
    if has_multi_play_window(player):
        delta += 0.50
    tempo += delta
    if tempo > 8.0:
        tempo = 8.0
    elif tempo < -8.0:
        tempo = -8.0
    player.flags["_tempo_score"] = tempo


def face_is_on_board(player: PlayerState, face_uid: int) -> bool:
    if face_uid in player.board_oceans:
        return True
    for ocean_uid in player.board_oceans:
        slots = player.ocean_slots[ocean_uid]
        if face_uid in slots.all_cards():
            return True
    return False


def remove_face_from_board(player: PlayerState, face_uid: int) -> bool:
    # Ocean pickup is only legal if that ocean has no attached cards.
    if face_uid in player.board_oceans:
        slots = player.ocean_slots.get(face_uid)
        if slots is not None and slots.all_cards():
            return False
        try:
            player.board_oceans.remove(face_uid)
        except ValueError:
            return False
        if face_uid in player.ocean_slots:
            del player.ocean_slots[face_uid]
        return True

    for ocean_uid in list(player.board_oceans):
        slots = player.ocean_slots[ocean_uid]
        for d in ("up", "down", "left", "right"):
            lst = slots.slot(d)
            if face_uid in lst:
                lst.remove(face_uid)
                return True
    return False


def replay_pickup_candidates(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    turn_state: TurnState,
) -> List[Tuple[int, int]]:
    # Returns [(entry_uid, face_uid)] candidates in newest-first order.
    out: List[Tuple[int, int]] = []
    seen_entries: set[int] = set()
    for face_uid in reversed(turn_state.played_face_uids):
        entry_uid = ms.face_to_primary.get(face_uid, face_uid)
        if entry_uid in seen_entries:
            continue
        if entry_uid in turn_state.discarded_entry_uids:
            continue
        if entry_uid in player.hand:
            continue
        if not face_is_on_board(player, face_uid):
            continue
        if face_uid in player.board_oceans:
            slots = player.ocean_slots.get(face_uid)
            if slots is not None and slots.all_cards():
                continue
        seen_entries.add(entry_uid)
        out.append((entry_uid, face_uid))
    return out


def ai_choose_replay_pickup(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    turn_state: TurnState,
) -> Optional[Tuple[int, int]]:
    candidates = replay_pickup_candidates(gs, ms, player, turn_state)
    if not candidates:
        return None

    best: Optional[Tuple[int, int]] = None
    best_score = float("-inf")
    for entry_uid, face_uid in candidates:
        card = gs.card_db[face_uid]
        score = pool_entry_value_for_player(ms, gs, entry_uid, player)
        score += 0.60 * entry_keep_priority_for_strategy(ms, gs, player, entry_uid)
        score += 0.15 * float(card.cost)
        if card.name.strip().lower() in PAYMENT_HEAVY_HITTER_NAMES:
            score += 0.90
        txt = card.text.lower()
        if "draw" in txt:
            score += 0.20
        if "play again" in txt or "go again" in txt:
            score += 0.30
        if "for free" in txt:
            score += 0.25
        if has_star_ability(card):
            score += 0.20
        if score > best_score:
            best_score = score
            best = (entry_uid, face_uid)
    return best


def maybe_apply_replay_pickup(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    turn_state: TurnState,
    is_human_turn: bool,
    verbose: bool = False,
) -> bool:
    # Replay effects (Play Again / Go Again) should not pick cards back up.
    # Keep this function as a no-op for compatibility with existing call sites.
    if turn_state.replay_pickup_used:
        return False
    turn_state.replay_pickup_used = True
    return False


def choose_optional_draw_count(gs: GameState, player: PlayerState, requested: int) -> int:
    if requested <= 0:
        return 0
    # Optional draw: allow drawing fewer cards when hand pressure is high.
    if not bool(HUMAN_REALISM_CONFIG.get("optional_draw_effects", False)):
        return requested

    hand_n = len(player.hand)
    pressure = endgame_pressure(len(gs.deck))
    gap = score_gap_vs_table(gs, player)

    if hand_n >= 10:
        return 0
    if hand_n >= 9:
        return min(1, requested)

    draw_n = requested
    # Late-game leaders should avoid over-drawing into forced discards.
    if pressure >= 0.70 and gap > 0 and hand_n >= 7:
        draw_n = min(draw_n, 1)
    # If behind or hand is thin, take full draw value.
    if gap < -8 or hand_n <= 5:
        draw_n = requested
    return max(0, min(requested, draw_n))


def should_take_optional_replay(gs: GameState, player: PlayerState, card: CardDef) -> bool:
    if not bool(HUMAN_REALISM_CONFIG.get("optional_replay_effects", False)):
        return True
    # If there is no practical hand follow-up, skip optional replay.
    if not player.hand:
        return False
    gap = score_gap_vs_table(gs, player)
    if gap > 14 and len(player.hand) >= 9:
        return False
    return True


def stabilize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Keep learned policy weights in sane ranges so AI keeps playing the board."""
    bounds = {
        "bias": (-0.5, 0.5),
        # Allow neutral/negative ocean preference so AI can prioritize creatures.
        "is_ocean": (-1.2, 1.2),
        "uses_star": (0.0, 1.5),
        "card_cost": (-1.5, -0.02),
        "has_plus": (0.0, 1.5),
        "target_occupancy": (-0.6, 1.8),
        "fills_empty_ocean": (0.1, 2.2),
        "draw_from_pool": (-0.4, 0.6),
        "pool_pick_value": (0.0, 3.0),
        "immediate_delta": (0.4, 3.0),
        "synergy_bonus": (0.0, 2.5),
        "species_bonus": (0.0, 2.5),
        "same_ocean_bonus": (0.0, 2.8),
        "symbol_bonus": (0.0, 3.0),
        "stack_bonus": (0.0, 4.0),
        "plan_fit_bonus": (0.0, 3.0),
        "future_value": (0.0, 4.0),
        "deny_bonus": (0.0, 2.2),
        "overbuild_ocean_penalty": (-6.0, -0.2),
        "strategy_bonus": (0.0, 3.5),
        "novelty_bonus": (0.0, 1.8),
        "branch_bonus": (0.0, 2.8),
        "sim_point_delta": (0.8, 4.0),
    }
    for k, (lo, hi) in bounds.items():
        v = float(weights.get(k, default_weights().get(k, 0.0)))
        if v < lo:
            v = lo
        elif v > hi:
            v = hi
        weights[k] = v
    return weights


def card_to_dict(card: CardDef) -> Dict[str, Any]:
    return {
        "uid": card.uid,
        "name": card.name,
        "species": card.species,
        "cost": card.cost,
        "direction": card.direction,
        "symbol": card.symbol,
        "text": card.text,
    }


def short_entry_list(ms: "MatchState", gs: GameState, uids: List[int]) -> str:
    if not uids:
        return "(empty)"
    return ", ".join(entry_short_label(ms, gs, uid) for uid in uids)


@dataclass
class LiveRecorder:
    log_path: str
    state_path: str
    seed: int

    def event(self, msg: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def _entry_to_dict(self, ms: "MatchState", gs: GameState, entry_uid: int) -> Dict[str, Any]:
        faces = entry_faces(ms, entry_uid)
        return {
            "entry_uid": entry_uid,
            "label": entry_short_label(ms, gs, entry_uid),
            "faces": [card_to_dict(gs.card_db[uid]) for uid in faces],
        }

    def snapshot(self, gs: GameState, ms: "MatchState", turn_number: int, note: str) -> None:
        players_payload: List[Dict[str, Any]] = []
        for p in gs.players:
            board_payload: List[Dict[str, Any]] = []
            for ocean_uid in p.board_oceans:
                slots = p.ocean_slots[ocean_uid]
                board_payload.append(
                    {
                        "ocean_uid": ocean_uid,
                        "ocean": card_to_dict(gs.card_db[ocean_uid]),
                        "up": [card_to_dict(gs.card_db[uid]) for uid in slots.up],
                        "down": [card_to_dict(gs.card_db[uid]) for uid in slots.down],
                        "left": [card_to_dict(gs.card_db[uid]) for uid in slots.left],
                        "right": [card_to_dict(gs.card_db[uid]) for uid in slots.right],
                    }
                )
            players_payload.append(
                {
                    "name": p.name,
                    "score": final_points(gs, p),
                    "hand_count": len(p.hand),
                    "hand": [self._entry_to_dict(ms, gs, uid) for uid in p.hand],
                    "board_ocean_count": len(p.board_oceans),
                    "board": board_payload,
                    "flags": {k: v for k, v in p.flags.items() if bool(v)},
                }
            )

        payload = {
            "seed": self.seed,
            "turn_number": turn_number,
            "turn_index": gs.turn_index,
            "round_count": gs.round_count,
            "current_player": gs.players[gs.turn_index].name if gs.players else None,
            "note": note,
            "deck_remaining": len(gs.deck),
            "pool_count": len(ms.pool),
            "pool": [self._entry_to_dict(ms, gs, uid) for uid in ms.pool],
            "discard_pile_count": len(ms.discard_pile),
            "end_game": {
                "triggered": ms.end_game_triggered,
                "final_turns_remaining": ms.final_turns_remaining,
                "end_game_uid": ms.end_game_uid,
            },
            "players": players_payload,
            "log_tail": gs.log[-50:],
        }

        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.state_path)

    def reset(self, gs: GameState, ms: "MatchState") -> None:
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(f"Live game log (seed={self.seed})\n")
        self.event(f"Players: {', '.join(p.name for p in gs.players)}")
        self.event(f"Opening pool: {short_entry_list(ms, gs, ms.pool)}")
        self.snapshot(gs, ms, turn_number=0, note="game_start")


def is_free_play_eligible(player: PlayerState, card: CardDef) -> bool:
    species = card.species.strip().lower()
    if card.name.strip().lower() == "yellowfin tuna" and int(player.flags.get("free_yellowfin_tuna", 0)) > 0:
        return True
    if player.flags.get("free_mammal", False) and species == "mammal":
        return True
    if player.flags.get("free_baitfish_chain", False) and species == "baitfish":
        return True
    if player.flags.get("free_baitfish", False) and species == "baitfish":
        return True
    if player.flags.get("free_game_fish", False) and species == "game fish":
        return True
    if player.flags.get("free_cephalopods", False) and species == "cephalopod":
        return True
    if player.flags.get("free_cephalopod_once", False) and species == "cephalopod":
        return True
    if player.flags.get("free_crustacean", False) and species == "crustacean":
        return True
    if player.flags.get("free_invertebrate", False) and species == "invertebrate":
        return True
    if player.flags.get("free_coral", False) and species == "coral":
        return True
    return False


def load_card_db() -> Dict[int, CardDef]:
    card_db: Dict[int, CardDef] = {}
    for path in ["cards_vertical.txt", "cards_lr.txt", "cards_oceans.txt"]:
        with open(path, "r", encoding="utf-8") as f:
            card_db = load_cards_from_lines(f.read(), card_db, source=path, strict=True)
    register_all_card_abilities(card_db)
    try:
        save_animal_synergy_grid(card_db, ANIMAL_SYNERGY_GRID_PATH)
    except Exception:
        # Grid export is best-effort and should never block simulation.
        pass
    return card_db


def default_weights() -> Dict[str, float]:
    return {
        "bias": 0.0,
        "is_ocean": -0.25,
        "uses_star": 0.95,
        "card_cost": -0.2,
        "has_plus": 0.3,
        "target_occupancy": 0.5,
        "fills_empty_ocean": 0.9,
        "draw_from_pool": 0.1,
        "pool_pick_value": 0.9,
        "immediate_delta": 1.0,
        "synergy_bonus": 0.5,
        "species_bonus": 0.9,
        "same_ocean_bonus": 0.6,
        "symbol_bonus": 0.9,
        "stack_bonus": 1.8,
        "plan_fit_bonus": 0.8,
        "future_value": 1.35,
        "deny_bonus": 0.4,
        "overbuild_ocean_penalty": -3.2,
        "strategy_bonus": 1.15,
        "novelty_bonus": 0.45,
        "branch_bonus": 0.75,
        "sim_point_delta": 0.8,
    }


def load_brain(path: str = BRAIN_PATH) -> Dict[str, object]:
    brain = {
        "weights": default_weights(),
        "synergy": {},
        "species_synergy": {},
        "same_ocean_synergy": {},
        "strategy_value": {},
        "strategy_count": {},
        "strategy_transition": {},
        "strategy_transition_count": {},
        "strategy_family_stats": {},
        "game_memory": [],
        "archetype_stats": {},
        "evolution": {"runs": 0, "last_score_spread": 0.0},
        "games_played": 0,
        "move_updates": 0,
    }
    if not os.path.exists(path):
        brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
        ensure_priority_anchor_brain_rules(brain)
        return brain
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"JSON parse error at line {e.lineno}, column {e.colno}")) from e
    except OSError as e:
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"I/O error: {e}")) from e
    except Exception as e:
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"Unexpected read error: {e}")) from e

    if not isinstance(data, dict):
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"Expected a JSON object, got {type(data).__name__}"))

    try:
        if isinstance(data, dict):
            if isinstance(data.get("weights"), dict):
                merged = default_weights()
                merged.update({k: float(v) for k, v in data["weights"].items() if isinstance(v, (int, float))})
                brain["weights"] = stabilize_weights(merged)
            if isinstance(data.get("synergy"), dict):
                brain["synergy"] = {str(k): float(v) for k, v in data["synergy"].items() if isinstance(v, (int, float))}
            if isinstance(data.get("species_synergy"), dict):
                brain["species_synergy"] = {
                    str(k): float(v) for k, v in data["species_synergy"].items() if isinstance(v, (int, float))
                }
            if isinstance(data.get("same_ocean_synergy"), dict):
                brain["same_ocean_synergy"] = {
                    str(k): float(v) for k, v in data["same_ocean_synergy"].items() if isinstance(v, (int, float))
                }
            if isinstance(data.get("strategy_value"), dict):
                brain["strategy_value"] = {
                    str(k): float(v) for k, v in data["strategy_value"].items() if isinstance(v, (int, float))
                }
            if isinstance(data.get("strategy_count"), dict):
                brain["strategy_count"] = {
                    str(k): int(v)
                    for k, v in data["strategy_count"].items()
                    if isinstance(v, (int, float)) and int(v) >= 0
                }
            if isinstance(data.get("strategy_transition"), dict):
                brain["strategy_transition"] = {
                    str(k): float(v) for k, v in data["strategy_transition"].items() if isinstance(v, (int, float))
                }
            if isinstance(data.get("strategy_transition_count"), dict):
                brain["strategy_transition_count"] = {
                    str(k): int(v)
                    for k, v in data["strategy_transition_count"].items()
                    if isinstance(v, (int, float)) and int(v) >= 0
                }
            if isinstance(data.get("strategy_family_stats"), dict):
                fam_stats: Dict[str, Dict[str, float]] = {}
                for k, v in data["strategy_family_stats"].items():
                    if isinstance(k, str) and isinstance(v, dict):
                        fam_stats[k] = {
                            "games": float(v.get("games", 0.0)),
                            "wins": float(v.get("wins", 0.0)),
                            "score_sum": float(v.get("score_sum", 0.0)),
                        }
                brain["strategy_family_stats"] = fam_stats
            if isinstance(data.get("game_memory"), list):
                mem: List[Dict[str, Any]] = []
                for item in data["game_memory"][-BRAIN_GAME_MEMORY_CAP:]:
                    if isinstance(item, dict):
                        mem.append(item)
                brain["game_memory"] = mem
            if isinstance(data.get("archetype_stats"), dict):
                stats: Dict[str, Dict[str, float]] = {}
                for k, v in data["archetype_stats"].items():
                    if isinstance(k, str) and isinstance(v, dict):
                        stats[k] = {
                            "games": float(v.get("games", 0.0)),
                            "wins": float(v.get("wins", 0.0)),
                            "score_sum": float(v.get("score_sum", 0.0)),
                        }
                brain["archetype_stats"] = stats
            if isinstance(data.get("evolution"), dict):
                brain["evolution"] = {
                    "runs": int(data["evolution"].get("runs", 0)),
                    "last_score_spread": float(data["evolution"].get("last_score_spread", 0.0)),
                }
            if isinstance(data.get("games_played"), int):
                brain["games_played"] = data["games_played"]
            if isinstance(data.get("move_updates"), int):
                brain["move_updates"] = data["move_updates"]
    except Exception as e:
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"Validation error: {e}")) from e
    brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
    # Keep STAR usage and STAR-setup pool drafting as strong policy defaults,
    # even when loading older learned weight files.
    brain["weights"]["uses_star"] = max(0.95, float(brain["weights"].get("uses_star", 0.0)))
    brain["weights"]["pool_pick_value"] = max(0.9, float(brain["weights"].get("pool_pick_value", 0.0)))
    brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
    ensure_priority_anchor_brain_rules(brain)
    return brain


def save_brain(brain: Dict[str, object], path: str = BRAIN_PATH) -> None:
    abs_path = os.path.abspath(path)
    directory = os.path.dirname(abs_path) or "."
    base = os.path.basename(abs_path)
    tmp_path = os.path.join(directory, f".{base}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(brain, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, abs_path)
    except Exception as e:
        raise BrainFileCorruptionError(brain_fix_prompt(path, f"Write error: {e}")) from e
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def is_ocean(card: CardDef) -> bool:
    return card.species.strip().lower() == "ocean" or card.direction.strip().lower() == "n/a"


def has_star_text(card: CardDef) -> bool:
    _, star = split_main_and_star(card.text)
    return bool(star.strip())


def has_star_ability(card: CardDef) -> bool:
    """STAR detection with fallback for data rows missing *...* markers."""
    if has_star_text(card):
        return True
    t = card.text.lower()
    # Fallback for cards like Arctic Oceans: "+2 | Play again"
    return ("play again" in t) or ("go again" in t)


def run_inferred_star_fallback(gs: GameState, player: PlayerState, card: CardDef) -> None:
    """Apply minimal star effects when card data omitted explicit *...* star text."""
    t = card.text.lower()
    if "play again" in t or "go again" in t:
        grant_replay_actions(player, 1)
        gs.log.append(f"{player.name} gets to play again from {card.name} inferred star ability.")


def build_non_ocean_pair_maps(card_db: Dict[int, CardDef]) -> Tuple[Dict[int, Tuple[int, int]], Dict[int, int]]:
    pair_primary_to_faces: Dict[int, Tuple[int, int]] = {}
    face_to_primary: Dict[int, int] = {}

    uids = sorted(card_db.keys())
    for uid in uids:
        if uid in face_to_primary:
            continue
        # Two-sided non-ocean cards are numbered in odd/even uid pairs.
        # Pairing by parity is robust against direction typos in source data.
        if uid % 2 == 0:
            continue
        other = uid + 1
        if other not in card_db:
            continue
        a = card_db[uid]
        b = card_db[other]
        if is_ocean(a) or is_ocean(b):
            continue
        pair_primary_to_faces[uid] = (uid, other)
        face_to_primary[uid] = uid
        face_to_primary[other] = uid

    return pair_primary_to_faces, face_to_primary


def entry_faces(ms: MatchState, entry_uid: int) -> List[int]:
    if entry_uid in ms.pair_primary_to_faces:
        return [ms.pair_primary_to_faces[entry_uid][0], ms.pair_primary_to_faces[entry_uid][1]]
    return [entry_uid]


def canonical_entry_uid(ms: MatchState, uid: int) -> int:
    return int(ms.face_to_primary.get(uid, uid))


def entry_is_ocean(ms: MatchState, gs: GameState, entry_uid: int) -> bool:
    faces = entry_faces(ms, entry_uid)
    for uid in faces:
        card = gs.card_db.get(uid)
        if card is not None and is_ocean(card):
            return True
    return False


def entry_label(ms: MatchState, gs: GameState, entry_uid: int) -> str:
    faces = entry_faces(ms, entry_uid)
    if len(faces) == 1:
        c = gs.card_db.get(faces[0])
        if c is None:
            return f"{entry_uid}: [missing card {faces[0]}]"
        return card_label(c)
    a = gs.card_db.get(faces[0])
    b = gs.card_db.get(faces[1])
    if a is None or b is None:
        return f"{entry_uid}: TWO-SIDED CARD [missing face data]"
    pair_num = ""
    if 1 <= faces[0] <= 96:
        pair_num = f" (Card #{(faces[0] + 1) // 2})"
    elif 101 <= faces[0] <= 190:
        pair_num = f" (Card #{((faces[0] - 101) // 2) + 1})"
    return (
        f"{entry_uid}: TWO-SIDED CARD{pair_num}\n"
        f"    A) {card_label(a)}\n"
        f"    B) {card_label(b)}"
    )


def entry_short_label(ms: MatchState, gs: GameState, entry_uid: int) -> str:
    faces = entry_faces(ms, entry_uid)
    if len(faces) == 1:
        c = gs.card_db.get(faces[0])
        return f"{entry_uid}:{c.name}" if c is not None else f"{entry_uid}:[missing]"
    a = gs.card_db.get(faces[0])
    b = gs.card_db.get(faces[1])
    if a is None or b is None:
        return f"{entry_uid}:[missing pair]"
    return f"{entry_uid}:{a.name}/{b.name}"


def symbol_match_for_entry(ms: MatchState, gs: GameState, entry_uid: int, target_symbol: str) -> bool:
    target = normalize_symbol(target_symbol)
    for uid in entry_faces(ms, entry_uid):
        card = gs.card_db.get(uid)
        if card is None:
            continue
        if normalize_symbol(card.symbol) == target:
            return True
    return False


def card_label(card: CardDef) -> str:
    return (
        f"{card.uid}:{card.name} [{card.species}] "
        f"cost={card.cost} dir={card.direction} sym={card.symbol} | {card.text}"
    )


def board_summary(gs: GameState, player: PlayerState) -> str:
    if not player.board_oceans:
        return "No oceans in play."

    lines: List[str] = []
    for ocean_uid in player.board_oceans:
        ocean = gs.card_db[ocean_uid]
        slots = player.ocean_slots[ocean_uid]
        lines.append(f"Ocean {ocean_uid} ({ocean.name})")
        for dir_name in ["up", "down", "left", "right"]:
            cards = slots.slot(dir_name)
            if not cards:
                continue
            lines.append(
                "  " + dir_name + ": " + ", ".join(f"{uid}:{gs.card_db[uid].name}" for uid in cards)
            )
    return "\n".join(lines)


def player_board_face_uids(player: PlayerState) -> List[int]:
    out: List[int] = []
    out.extend(player.board_oceans)
    for slots in player.ocean_slots.values():
        out.extend(slots.all_cards())
    return out


def synergy_key(a_name: str, b_name: str) -> str:
    x = a_name.strip().lower()
    y = b_name.strip().lower()
    if x <= y:
        return f"{x}|{y}"
    return f"{y}|{x}"


def species_synergy_key(a_species: str, b_species: str) -> str:
    x = a_species.strip().lower()
    y = b_species.strip().lower()
    if x <= y:
        return f"{x}|{y}"
    return f"{y}|{x}"


ANCHOR_PRIORITY_VERSION = 1
ANIMAL_SYNERGY_GRID_PATH = "animal_synergy_grid.json"

# Main anchor combos from user guidance.
PRIORITY_CARD_SYNERGY: Dict[str, set[str]] = {
    "whale shark": {"mullet", "bunker", "sardine", "flying fish", "bonito", "hermit crab", "sea cucumber", "sea urchin", "roosterfish"},
    "hermit crab": {"mullet", "bunker", "sardine", "flying fish", "bonito", "whale shark", "roosterfish"},
    "reef trigger fish": {"common octopus", "bobtail squid", "cuttlefish", "giant squid"},
    "california seagull": {"lobster", "spiny lobster", "mantis shrimp", "king crab", "hermit crab"},
    "great white shark": {"spinner dolphin", "bottlenose dolphin", "narwhal"},
    "sea cucumber": {"mullet", "bunker", "sardine", "flying fish", "bonito", "whale shark", "roosterfish"},
    "common sea star": {"mandarin goby", "spiny lobster", "lobster", "mantis shrimp", "king crab", "hermit crab"},
    "sea star": {"mandarin goby", "spiny lobster", "lobster", "mantis shrimp", "king crab", "hermit crab"},
    "sea urchin": {"emperor penguin", "horned puffin", "california seagull", "peruvian pelican", "great albatross", "osprey", "mullet", "bunker", "sardine", "flying fish", "bonito"},
    "loggerhead sea turtle": {"mullet", "bunker", "sardine", "flying fish", "bonito", "yellowfin tuna", "mahi mahi", "lobster"},
    "roosterfish": {"mullet", "bunker", "sardine", "flying fish", "bonito", "whale shark", "hermit crab"},
    "blue marlin": {"mahi mahi"},
    "cleaner wrasse": {"mahi mahi", "yellowfin tuna"},
}

PRIORITY_SAME_OCEAN_SYNERGY: Dict[str, set[str]] = {
    "whale shark": {"mullet", "bunker", "sardine", "flying fish", "bonito", "roosterfish"},
    "hermit crab": {"mullet", "bunker", "sardine", "flying fish", "bonito", "roosterfish"},
    "reef trigger fish": {"common octopus", "bobtail squid", "cuttlefish", "giant squid"},
    "california seagull": {"lobster", "spiny lobster", "mantis shrimp", "king crab", "hermit crab"},
    "great white shark": {"spinner dolphin", "bottlenose dolphin", "narwhal"},
    "roosterfish": {"mullet", "bunker", "sardine", "flying fish", "bonito", "whale shark"},
    "blue marlin": {"mahi mahi"},
    "cleaner wrasse": {"mahi mahi"},
}

PRIORITY_SPECIES_SYNERGY: List[Tuple[str, str]] = [
    ("baitfish", "crosscurrent"),
    ("baitfish", "n/a"),
    ("baitfish", "game fish"),
    ("baitfish", "invertebrate"),
    ("cephalopod", "crosscurrent"),
    ("cephalopod", "n/a"),
    ("cephalopod", "cephalopod"),
    ("bird", "crustacean"),
    ("mammal", "crosscurrent"),
    ("mammal", "n/a"),
    ("crustacean", "invertebrate"),
]

PRIORITY_CARD_TO_STRATEGIES: Dict[str, set[str]] = {
    "whale shark": {"Baitfish Engine"},
    "hermit crab": {"Baitfish Engine", "Crustaceans"},
    "reef trigger fish": {"Cephalopods"},
    "california seagull": {"Crustaceans", "Birds"},
    "great white shark": {"Mammals"},
    "sea cucumber": {"Baitfish Engine"},
    "common sea star": {"Goby Spiny Combo", "Bottom Engine"},
    "sea star": {"Goby Spiny Combo", "Bottom Engine"},
    "sea urchin": {"Birds", "Baitfish Engine"},
    "loggerhead sea turtle": {"Cheap Burst"},
    "roosterfish": {"Baitfish Engine", "Cheap Flex"},
}


def ensure_priority_anchor_brain_rules(brain: Dict[str, object]) -> None:
    """Keep user-defined main combo anchors highly weighted in learned memory."""
    synergy_map = brain.get("synergy")
    species_map = brain.get("species_synergy")
    same_ocean_map = brain.get("same_ocean_synergy")
    weights = brain.get("weights")
    if (
        not isinstance(synergy_map, dict)
        or not isinstance(species_map, dict)
        or not isinstance(same_ocean_map, dict)
        or not isinstance(weights, dict)
    ):
        return

    for a, others in PRIORITY_CARD_SYNERGY.items():
        for b in others:
            k = synergy_key(a, b)
            synergy_map[k] = max(2.95, float(synergy_map.get(k, 0.0)))

    for a, others in PRIORITY_SAME_OCEAN_SYNERGY.items():
        for b in others:
            k = synergy_key(a, b)
            same_ocean_map[k] = max(3.85, float(same_ocean_map.get(k, 0.0)))

    for a_species, b_species in PRIORITY_SPECIES_SYNERGY:
        k = species_synergy_key(a_species, b_species)
        species_map[k] = max(2.85, float(species_map.get(k, 0.0)))

    weights["synergy_bonus"] = max(1.45, float(weights.get("synergy_bonus", 0.0)))
    weights["species_bonus"] = max(2.2, float(weights.get("species_bonus", 0.0)))
    weights["same_ocean_bonus"] = max(1.35, float(weights.get("same_ocean_bonus", 0.0)))
    weights["plan_fit_bonus"] = max(1.40, float(weights.get("plan_fit_bonus", 0.0)))
    weights["future_value"] = max(1.95, float(weights.get("future_value", 0.0)))
    weights["deny_bonus"] = max(0.85, float(weights.get("deny_bonus", 0.0)))
    weights["strategy_bonus"] = max(1.60, float(weights.get("strategy_bonus", 0.0)))
    # Keep strategy exploration, but don't let novelty overpower consistency.
    weights["novelty_bonus"] = min(0.45, max(0.20, float(weights.get("novelty_bonus", 0.0))))
    brain["weights"] = stabilize_weights(dict(weights))

    brain["priority_anchor_rules_version"] = ANCHOR_PRIORITY_VERSION
    brain["priority_anchor_card_synergy_count"] = int(sum(len(v) for v in PRIORITY_CARD_SYNERGY.values()))
    brain["priority_anchor_species_synergy_count"] = int(len(PRIORITY_SPECIES_SYNERGY))
    brain["animal_synergy_grid_path"] = ANIMAL_SYNERGY_GRID_PATH


def action_synergy_bonus(
    gs: GameState,
    player: PlayerState,
    action: Action,
    synergy_map: Optional[Dict[str, float]] = None,
) -> float:
    if not synergy_map:
        return 0.0
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return 0.0
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    if face_uid not in gs.card_db:
        return 0.0
    name = gs.card_db[face_uid].name
    board_names = [gs.card_db[uid].name for uid in player_board_face_uids(player)]
    if not board_names:
        return 0.0
    bonus = 0.0
    for other in board_names:
        bonus += synergy_map.get(synergy_key(name, other), 0.0)
    return bonus / max(1, len(board_names))


def action_species_bonus(
    gs: GameState,
    player: PlayerState,
    action: Action,
    species_map: Optional[Dict[str, float]] = None,
) -> float:
    if not species_map:
        return 0.0
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return 0.0

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    if face_uid not in gs.card_db:
        return 0.0
    card = gs.card_db[face_uid]
    my_species = card.species.strip().lower()
    if not my_species or my_species == "n/a":
        return 0.0

    board_species = [gs.card_db[uid].species.strip().lower() for uid in player_board_face_uids(player)]
    board_species = [s for s in board_species if s and s != "n/a"]
    if not board_species:
        return 0.0

    bonus = 0.0
    for other in board_species:
        bonus += species_map.get(species_synergy_key(my_species, other), 0.0)
    return bonus / max(1, len(board_species))


def action_same_ocean_bonus(
    gs: GameState,
    player: PlayerState,
    action: Action,
    same_ocean_map: Optional[Dict[str, float]] = None,
) -> float:
    if not same_ocean_map:
        return 0.0
    if action.kind != "play_to_ocean" or action.ocean_uid is None:
        return 0.0

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    if face_uid not in gs.card_db:
        return 0.0
    card = gs.card_db[face_uid]
    slots = player.ocean_slots.get(action.ocean_uid)
    if slots is None:
        return 0.0
    local = slots.all_cards()
    if not local:
        return 0.0

    bonus = 0.0
    for uid in local:
        bonus += same_ocean_map.get(synergy_key(card.name, gs.card_db[uid].name), 0.0)
    return bonus / max(1, len(local))


def action_symbol_bonus(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return 0.0

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0

    sym = normalize_symbol(card.symbol)
    if sym in {"", "n/a"}:
        return 0.0

    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    non_ocean_cards = [c for c in board_cards if not is_ocean(c)]
    same_sym_count = sum(1 for c in non_ocean_cards if normalize_symbol(c.symbol) == sym)

    text = card.text.lower()
    symbol_engine_count = sum(1 for c in non_ocean_cards if "matching symbol" in c.text.lower())
    bonus = min(2.5, 0.5 * same_sym_count)

    # Cards that explicitly scale with symbol matching should strongly prefer symbol stacks.
    if "matching symbol" in text:
        bonus += min(3.0, 0.8 * same_sym_count)

    # If board already has symbol-matters cards (Osprey/Sea Sponge/Spinner Dolphin style),
    # prefer feeding that engine with same-symbol cards.
    if symbol_engine_count > 0:
        bonus += min(2.5, 0.6 * symbol_engine_count + 0.35 * same_sym_count)

    # STAR cards are more reliable when a matching payment symbol is available in hand.
    if has_star_ability(card) and card.cost > 0:
        candidates = [uid for uid in player.hand if uid != action.card_uid]
        if any(symbol_match_for_entry(ms, gs, uid, sym) for uid in candidates):
            bonus += 0.9
        else:
            bonus -= 0.7

    if bonus > 6.0:
        return 6.0
    if bonus < -2.5:
        return -2.5
    return bonus


def card_strategy_tags(card: CardDef) -> set[str]:
    tags: set[str] = set()
    name = card.name.strip().lower()
    species = card.species.strip().lower()
    text = card.text.strip().lower()

    if species and species not in {"n/a"}:
        tags.add(f"species:{species}")
    if species == "baitfish":
        tags.add("engine:baitfish")
    if species == "cephalopod":
        tags.add("engine:cephalopod")
    if species == "crustacean":
        tags.add("engine:crustacean")
    if species == "coral":
        tags.add("engine:coral")
    if species == "bird":
        tags.add("engine:bird")
    if species == "mammal":
        tags.add("engine:mammal")
    if species == "game fish":
        tags.add("engine:gamefish")
    if species == "ocean":
        tags.add("engine:ocean")

    if "yellowfin tuna" in name or "big eye tuna" in name:
        tags.add("engine:yellowfin")
    if "mahi mahi" in name or "blue marlin" in name or "tarpon" in name:
        tags.add("engine:pelagic")
    if "mangrove" in name or "tide pool" in name or "kelp forest" in name:
        tags.add("engine:ocean-race")
    if name in {"reef trigger fish", "reef triggerfish"}:
        tags.add("engine:cephalopod")
    if name == "great white shark":
        tags.add("engine:mammal")
    if name == "sea cucumber":
        tags.add("engine:baitfish")
    if name in {"sea star", "common sea star"}:
        tags.add("engine:goby-spiny")
    if name == "sea urchin":
        tags.add("engine:bird")
        tags.add("engine:baitfish")
    if name == "loggerhead sea turtle":
        tags.add("engine:cheap-burst")
    if name == "roosterfish":
        # Roosterfish is a cheap flexible card, but strongest with baitfish lines.
        tags.add("engine:baitfish")
        tags.add("engine:cheap-flex")
    if name in {"mandarin goby", "spiny lobster"}:
        tags.add("engine:goby-spiny")
        tags.add("engine:crosscurrent")
    if name == "blue tang":
        tags.add("engine:crosscurrent")
    if "matching symbol" in text:
        tags.add("engine:symbol")
    if "play a free" in text:
        tags.add("engine:freeplay")
    if "play again" in text or "go again" in text:
        tags.add("engine:tempo")
    if "draw" in text:
        tags.add("engine:draw")
    return tags


ENGINE_TAG_LABELS = {
    "engine:baitfish": "Baitfish Engine",
    "engine:cephalopod": "Cephalopods",
    "engine:crustacean": "Crustaceans",
    "engine:coral": "Coral",
    "engine:bird": "Birds",
    "engine:mammal": "Mammals",
    "engine:gamefish": "Game Fish",
    "engine:yellowfin": "Yellowfin",
    "engine:goby-spiny": "Goby Spiny Combo",
    "engine:na": "N/A",
    "engine:cheap-burst": "Cheap Burst",
    "engine:cheap-flex": "Cheap Flex",
    "engine:tempo": "Tempo",
    "engine:draw": "Draw",
    "engine:freeplay": "Free Play",
    "engine:symbol": "Symbol Match",
    "engine:ocean": "Oceans",
    "engine:ocean-race": "Ocean Race",
}


def build_animal_synergy_grid(card_db: Dict[int, CardDef]) -> Dict[str, object]:
    """Create a readable animal->synergy grid from priorities + inferred tags."""
    unique_by_name: Dict[str, CardDef] = {}
    for uid in sorted(card_db.keys()):
        c = card_db[uid]
        name = c.name.strip().lower()
        if not name or name in unique_by_name:
            continue
        sp = c.species.strip().lower()
        if sp in {"ocean", "end game"}:
            continue
        unique_by_name[name] = c

    reverse_anchor: Dict[str, set[str]] = {}
    for src, dsts in PRIORITY_CARD_SYNERGY.items():
        for dst in dsts:
            reverse_anchor.setdefault(dst, set()).add(src)

    rows: List[Dict[str, object]] = []
    for name in sorted(unique_by_name.keys()):
        c = unique_by_name[name]
        tags = card_strategy_tags(c)
        strategy_labels: set[str] = set()
        for t in tags:
            if t.startswith("engine:"):
                strategy_labels.add(ENGINE_TAG_LABELS.get(t, t.replace("engine:", "").replace("-", " ").title()))
        strategy_labels.update(PRIORITY_CARD_TO_STRATEGIES.get(name, set()))

        goes_with_cards = set(PRIORITY_CARD_SYNERGY.get(name, set())) | set(reverse_anchor.get(name, set()))
        rows.append(
            {
                "card": c.name,
                "species": c.species,
                "ability": c.text,
                "goes_with_strategies": sorted(strategy_labels),
                "priority_goes_with_cards": sorted(goes_with_cards),
            }
        )

    return {
        "generated_unix": int(time.time()),
        "anchor_priority_version": ANCHOR_PRIORITY_VERSION,
        "rows": rows,
    }


def save_animal_synergy_grid(card_db: Dict[int, CardDef], path: str = ANIMAL_SYNERGY_GRID_PATH) -> None:
    payload = build_animal_synergy_grid(card_db)
    abs_path = os.path.abspath(path)
    directory = os.path.dirname(abs_path) or "."
    base = os.path.basename(abs_path)
    tmp_path = os.path.join(directory, f".{base}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, abs_path)


def board_strategy_profile(gs: GameState, player: PlayerState) -> Dict[str, int]:
    prof: Dict[str, int] = {}
    for uid in player_board_face_uids(player):
        c = gs.card_db[uid]
        for t in card_strategy_tags(c):
            prof[t] = prof.get(t, 0) + 1
    return prof


def action_plan_fit_bonus(gs: GameState, player: PlayerState, action: Action) -> float:
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return 0.0
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0

    profile = board_strategy_profile(gs, player)
    tags = card_strategy_tags(card)
    if not profile:
        return 1.2 if is_ocean(card) else 0.25

    bonus = 0.0
    for t in tags:
        bonus += min(profile.get(t, 0), 5) * 0.22

    off_plan_tags = sum(1 for t in tags if profile.get(t, 0) == 0)
    total_profile_weight = sum(profile.values())
    pivot_hint = 0.0
    text_low = card.text.lower()
    if "draw" in text_low:
        pivot_hint += 0.25
    if "play again" in text_low or "go again" in text_low:
        pivot_hint += 0.30
    if "for free" in text_low or "play any number" in text_low:
        pivot_hint += 0.30
    if "per " in text_low:
        pivot_hint += 0.20
    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
        local_species_match = sum(
            1 for c in local_cards if c.species.strip().lower() == card.species.strip().lower() and c.species.strip().lower() != "ocean"
        )
        if local_species_match > 0:
            pivot_hint += min(0.45, 0.20 * local_species_match)

    if total_profile_weight >= 6:
        # Established engines should avoid isolated off-plan cards,
        # but allow strategic pivots that build future value.
        off_plan_penalty = min(1.4, 0.18 * off_plan_tags)
        off_plan_penalty = max(0.15, off_plan_penalty - min(0.85, pivot_hint))
        bonus -= off_plan_penalty
    else:
        # Early game can keep optional branching and setup pivots.
        bonus += 0.03 * off_plan_tags
        bonus += 0.15 * min(1.0, pivot_hint)

    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    board_names = [c.name.lower() for c in board_cards]
    board_species = [c.species.strip().lower() for c in board_cards]
    non_ocean_cards = [c for c in board_cards if not is_ocean(c)]
    symbol_less_animals = sum(
        1
        for c in non_ocean_cards
        if normalize_symbol(c.symbol) in {"", "n/a"} and c.species.strip().lower() != "ocean"
    )
    coral_count = sum(1 for s in board_species if s == "coral")
    invertebrate_count = sum(1 for s in board_species if s == "invertebrate")

    if "yellowfin tuna" in card.name.lower():
        bonus += 0.32 * board_names.count("big eye tuna")
    if "big eye tuna" in card.name.lower():
        bonus += 0.28 * board_names.count("yellowfin tuna")
    if "blue marlin" in card.name.lower():
        bonus += 0.50 * board_names.count("mahi mahi")
    if "mahi mahi" in card.name.lower():
        bonus += 0.40 * board_names.count("blue marlin")
    if "barracuda" in card.name.lower():
        bonus += 0.30 * board_names.count("goliath grouper")
    if "roosterfish" in card.name.lower():
        baitfish_count = sum(1 for s in board_species if s == "baitfish")
        gamefish_count = sum(1 for s in board_species if s == "game fish")
        # Works in many plans because it's cheap, but gets much better with baitfish.
        bonus += 0.35 + min(1.8, 0.45 * baitfish_count + 0.10 * gamefish_count)
    if "blue tang" in card.name.lower():
        bonus += min(2.2, 0.60 * symbol_less_animals)
    if card.species.strip().lower() == "coral":
        bonus += min(1.8, 0.18 * invertebrate_count + 0.08 * coral_count)
    if card.species.strip().lower() == "invertebrate":
        bonus += min(1.8, 0.16 * coral_count + 0.10 * invertebrate_count)
    if "clownfish" in card.name.lower() and action.kind == "play_to_ocean" and action.ocean_uid is not None:
        ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()
        bonus += clownfish_ocean_value(ocean_name)

    if action.kind == "play_ocean":
        bonus += 0.55 if len(player.board_oceans) < 4 else 0.12

    if bonus > 4.0:
        return 4.0
    return bonus


LONG_TERM_ENGINE_TAGS = {
    "engine:baitfish",
    "engine:bird",
    "engine:cephalopod",
    "engine:coral",
    "engine:crustacean",
    "engine:gamefish",
    "engine:goby-spiny",
    "engine:mammal",
    "engine:na",
    "engine:yellowfin",
}

STRATEGY_FAMILY_TO_ENGINE_TAG = {
    "birds": "engine:bird",
    "birds_crustaceans": "engine:crustacean",
    "game_fish": "engine:gamefish",
    "cephalopods": "engine:cephalopod",
}


def action_future_value_bonus(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    pressure = endgame_pressure(len(gs.deck))
    stage_mult = 1.70 - 0.70 * pressure  # early game strongly favors future-building lines.

    board_profile = board_strategy_profile(gs, player)
    hand_tag_counts: Dict[str, int] = {}
    hand_name_counts: Dict[str, int] = {}
    hand_species_counts: Dict[str, int] = {}
    for entry_uid in player.hand:
        if action.card_uid != -1 and entry_uid == action.card_uid:
            continue
        for face_uid in entry_faces(ms, entry_uid):
            c = gs.card_db[face_uid]
            hand_name_counts[c.name.strip().lower()] = hand_name_counts.get(c.name.strip().lower(), 0) + 1
            sp = c.species.strip().lower()
            hand_species_counts[sp] = hand_species_counts.get(sp, 0) + 1
            for t in card_strategy_tags(c):
                hand_tag_counts[t] = hand_tag_counts.get(t, 0) + 1

    family_label = str(player.flags.get("_strategy_family", "")).strip().lower()
    best_engine = ""
    best_strength = 0.0
    for t in LONG_TERM_ENGINE_TAGS:
        strength = 1.7 * float(board_profile.get(t, 0)) + 0.85 * float(hand_tag_counts.get(t, 0))
        if strength > best_strength:
            best_strength = strength
            best_engine = t
    fam_engine = STRATEGY_FAMILY_TO_ENGINE_TAG.get(family_label, "")
    if fam_engine:
        fam_strength = 1.35 + 1.3 * float(board_profile.get(fam_engine, 0)) + 0.7 * float(hand_tag_counts.get(fam_engine, 0))
        if fam_strength > best_strength:
            best_strength = fam_strength
            best_engine = fam_engine

    if action.kind == "draw":
        bonus = 0.0
        if hand_has_engine_timing_card(ms, gs, player) and len(player.hand) < 9:
            bonus += 1.2
        if action.pool_pick_uids:
            for uid in action.pool_pick_uids:
                for face_uid in entry_faces(ms, uid):
                    c = gs.card_db[face_uid]
                    tags = card_strategy_tags(c)
                    if best_engine and best_engine in tags:
                        bonus += 0.8
                    if "per " in c.text.lower():
                        bonus += 0.35
                    if "for free" in c.text.lower() or "play any number" in c.text.lower():
                        bonus += 0.35
        if len(player.hand) >= 10 and pressure >= 0.70:
            bonus -= 0.8
        bonus *= stage_mult
        if bonus > 4.5:
            return 4.5
        if bonus < -2.0:
            return -2.0
        return bonus

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0

    cname = card.name.strip().lower()
    cspecies = card.species.strip().lower()
    tags = card_strategy_tags(card)
    engine_tags = {t for t in tags if t in LONG_TERM_ENGINE_TAGS}
    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    board_names = [c.name.strip().lower() for c in board_cards]
    board_species = [c.species.strip().lower() for c in board_cards]

    bonus = 0.0
    if action.kind == "play_ocean":
        attachable = 0
        match_attachable = 0
        for entry_uid in player.hand:
            if action.card_uid != -1 and entry_uid == action.card_uid:
                continue
            for fu in entry_faces(ms, entry_uid):
                c2 = gs.card_db[fu]
                if is_ocean(c2):
                    continue
                attachable += 1
                if best_engine and best_engine in card_strategy_tags(c2):
                    match_attachable += 1
                break
        bonus += min(2.6, 0.14 * attachable + 0.30 * match_attachable)
        if count_empty_oceans(player) > 0 and pressure >= 0.72:
            bonus -= 1.0
        bonus *= stage_mult
        if bonus > 4.5:
            return 4.5
        if bonus < -2.0:
            return -2.0
        return bonus

    # play_to_ocean
    if best_engine and best_engine in engine_tags:
        bonus += 1.6 + min(2.0, 0.35 * best_strength)

    t = card.text.lower()
    # Hand+board bias: scale bonus by how many matching cards the player holds/has placed.
    # Higher coefficients = AI commits harder to whatever strategy is in its hand.
    if "per baitfish" in t:
        bonus += min(3.8, 0.42 * (sum(1 for s in board_species if s == "baitfish") + hand_species_counts.get("baitfish", 0)))
    if "per cephalopod" in t:
        bonus += min(3.8, 0.44 * (sum(1 for s in board_species if s == "cephalopod") + hand_species_counts.get("cephalopod", 0)))
    if "per crustacean" in t:
        bonus += min(3.4, 0.36 * (sum(1 for s in board_species if s == "crustacean") + hand_species_counts.get("crustacean", 0)))
    if "per bird" in t:
        bonus += min(3.4, 0.36 * (sum(1 for s in board_species if s == "bird") + hand_species_counts.get("bird", 0)))
    if "per coral" in t:
        bonus += min(3.2, 0.34 * (sum(1 for s in board_species if s == "coral") + hand_species_counts.get("coral", 0)))
    if "per invertebrate" in t:
        bonus += min(3.2, 0.34 * (sum(1 for s in board_species if s == "invertebrate") + hand_species_counts.get("invertebrate", 0)))
    if "per mammal" in t:
        bonus += min(3.4, 0.36 * (sum(1 for s in board_species if s == "mammal") + hand_species_counts.get("mammal", 0)))
    if "per n/a animal" in t or "per uncharted animal" in t or "per crosscurrent animal" in t:
        n_uncharted = (
            sum(1 for s in board_species if s in {"uncharted", "n/a", "crosscurrent"})
            + hand_species_counts.get("uncharted", 0)
            + hand_species_counts.get("n/a", 0)
            + hand_species_counts.get("crosscurrent", 0)
        )
        bonus += min(2.8, 0.30 * n_uncharted)
    if "per yellowfin tuna" in t:
        bonus += min(3.4, 0.55 * (board_names.count("yellowfin tuna") + hand_name_counts.get("yellowfin tuna", 0)))
    # Ocean All-Blue: bonus for playing oceans when hand is ocean-heavy.
    hand_ocean_count = sum(1 for entry_uid in player.hand if any(
        is_ocean(gs.card_db[fu]) for fu in entry_faces(ms, entry_uid) if fu in gs.card_db
    ))
    if is_ocean(card) and hand_ocean_count >= 2:
        bonus += min(2.0, 0.50 * hand_ocean_count)

    bait_support = sum(1 for s in board_species if s == "baitfish") + hand_species_counts.get("baitfish", 0)
    ceph_support = sum(1 for s in board_species if s == "cephalopod") + hand_species_counts.get("cephalopod", 0)
    goby_support = board_names.count("mandarin goby") + hand_name_counts.get("mandarin goby", 0)
    gamefish_support = sum(1 for s in board_species if s == "game fish") + hand_species_counts.get("game fish", 0)
    if cname in {"hermit crab", "whale shark", "roosterfish"}:
        bonus += min(3.4, 0.70 * bait_support)
    if cname in {"reef trigger fish", "reef triggerfish", "manta ray"}:
        bonus += min(3.4, 0.75 * ceph_support)
    if cname == "spiny lobster":
        bonus += min(3.4, 0.90 * goby_support)
    if cname == "california seagull":
        crust_support = sum(1 for s in board_species if s == "crustacean") + hand_species_counts.get("crustacean", 0)
        bonus += min(3.0, 0.60 * crust_support)
    if cname == "sea cucumber":
        bonus += min(2.2, 0.48 * gamefish_support)
    if cname == "loggerhead sea turtle":
        cheap_hand = 0
        for entry_uid in player.hand:
            if action.card_uid != -1 and entry_uid == action.card_uid:
                continue
            opts = entry_faces(ms, entry_uid)
            if opts and min(gs.card_db[fu].cost for fu in opts) <= 1:
                cheap_hand += 1
        if len(player.hand) >= 7 and cheap_hand >= 4:
            bonus += 2.4

    # Light penalty for off-engine plays when a strong plan already exists.
    # Strategic pivot credit: if a move is off-plan but clearly improves a
    # future engine or combo, reduce the penalty.
    pivot_credit = 0.0
    if "draw" in t:
        pivot_credit += 0.35
    if "play again" in t or "go again" in t:
        pivot_credit += 0.45
    if "for free" in t or "play any number" in t:
        pivot_credit += 0.45
    if "per " in t:
        pivot_credit += 0.30
    supporting_off_tags = [et for et in engine_tags if hand_tag_counts.get(et, 0) >= 2]
    if supporting_off_tags:
        pivot_credit += min(1.10, 0.40 * len(supporting_off_tags))
    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
        local_species_match = sum(
            1 for c in local_cards if c.species.strip().lower() == cspecies and c.species.strip().lower() != "ocean"
        )
        if local_species_match > 0:
            pivot_credit += min(0.80, 0.25 * local_species_match)
        if any("matching symbol" in c.text.lower() for c in local_cards):
            sym = normalize_symbol(card.symbol)
            if sym not in {"", "n/a"}:
                pivot_credit += 0.25

    non_ocean_count = sum(1 for c in board_cards if not is_ocean(c))
    if non_ocean_count >= 2 and best_strength >= 3.5 and best_engine:
        if best_engine not in engine_tags and not is_ocean(card):
            if engine_tags:
                off_engine_penalty = min(4.0, 0.60 * best_strength)
                off_engine_penalty = max(0.35, off_engine_penalty - min(1.90, pivot_credit))
                bonus -= off_engine_penalty

    bonus *= stage_mult
    if bonus > 6.5:
        return 6.5
    if bonus < -3.0:
        return -3.0
    return bonus


def action_stack_bonus(gs: GameState, player: PlayerState, action: Action) -> float:
    if action.kind != "play_to_ocean" or action.ocean_uid is None:
        return 0.0
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0
    if not can_share_slot(card):
        return 0.0

    direction = card.direction.strip().lower()
    if direction not in {"up", "down", "left", "right"}:
        return 0.0

    target_slot = player.ocean_slots[action.ocean_uid].slot(direction)
    bonus = 0.0

    # Strong preference: stack onto an already-occupied matching slot.
    if target_slot:
        bonus += 4.5
        if any(gs.card_db[uid].name.lower() == card.name.lower() for uid in target_slot):
            bonus += 2.0
    else:
        # Slight penalty when not stacking despite stackable card.
        bonus -= 0.8

    same_name_board = sum(
        1 for uid in player_board_face_uids(player) if gs.card_db[uid].name.lower() == card.name.lower()
    )
    if same_name_board > 0:
        bonus += 0.9

    if bonus > 6.5:
        return 6.5
    return bonus


def count_empty_oceans(player: PlayerState) -> int:
    return sum(1 for ocean_uid in player.board_oceans if len(player.ocean_slots[ocean_uid].all_cards()) == 0)


def ocean_action_is_high_value(gs: GameState, player: PlayerState, action: Action) -> bool:
    if action.kind != "play_ocean":
        return True
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return False

    if len(player.board_oceans) < 2:
        return True

    t = card.text.lower()
    if "play again" in t or "go again" in t:
        return True
    if "draw" in t:
        return True
    if "if you have all 8 oceans" in t and len(player.board_oceans) >= 7:
        return True
    return False


def filter_overbuild_ocean_actions(
    gs: GameState, ms: MatchState, player: PlayerState, actions: List[Action]
) -> List[Action]:
    if not actions or not player.board_oceans:
        return actions

    empty_oceans = count_empty_oceans(player)

    non_ocean_actions = [a for a in actions if a.kind != "play_ocean"]
    attach_actions = [a for a in actions if a.kind == "play_to_ocean"]
    has_draw_action = any(a.kind == "draw" for a in actions)
    has_attach_action = any(a.kind == "play_to_ocean" for a in actions)

    # Human-like: when you already have an empty ocean and can still attach cards,
    # prioritize filling your board before adding more oceans.
    if empty_oceans > 0 and has_attach_action and non_ocean_actions:
        return non_ocean_actions

    # Even without an immediate attach, avoid overbuilding empty oceans when
    # drawing is available; draw to find attachable cards first.
    if empty_oceans > 0 and has_draw_action and non_ocean_actions:
        return non_ocean_actions

    # After the board is established, prefer attaching creatures over adding
    # yet another ocean unless that ocean has immediate/high-value text.
    if len(player.board_oceans) >= 3 and attach_actions:
        high_value_oceans = [
            a for a in actions if a.kind == "play_ocean" and ocean_action_is_high_value(gs, player, a)
        ]
        return attach_actions + high_value_oceans

    filtered: List[Action] = []
    for a in actions:
        if a.kind != "play_ocean":
            filtered.append(a)
            continue
        if ocean_action_is_high_value(gs, player, a):
            filtered.append(a)

    return filtered if filtered else actions


def action_signature(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> str:
    if action.kind == "draw":
        mode = f"p{action.draw_from_pool}"
        if action.pool_pick_uids:
            mode += ":pick"
        return f"draw|{mode}"

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return f"{action.kind}|unknown"

    kind = "ocean" if action.kind == "play_ocean" else "attach"
    species = (card.species or "n/a").strip().lower()
    symbol = normalize_symbol(card.symbol) or "n/a"
    tags = sorted(card_strategy_tags(card))
    tag_part = ",".join(tags[:3]) if tags else "none"
    dir_part = card.direction.strip().lower()
    star_part = "star" if action.use_star else "plain"
    return f"{kind}|{species}|{symbol}|{dir_part}|{star_part}|{tag_part}"


def strategy_signal(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
) -> Tuple[float, float, float, str]:
    sig = action_signature(gs, ms, player, action)
    strategy_value = 0.0
    novelty = 0.0
    branch = 0.0

    if isinstance(strategy_value_map, dict):
        strategy_value = float(strategy_value_map.get(sig, 0.0))
    if isinstance(strategy_count_map, dict):
        c = max(0, int(strategy_count_map.get(sig, 0)))
        novelty = (1.0 / ((1 + c) ** 0.5)) + min(0.5, 0.35 / (1 + c))
    if isinstance(strategy_transition_map, dict):
        prev = str(player.flags.get("_last_sig", "") or "")
        if prev:
            tkey = f"{prev}=>{sig}"
            branch = float(strategy_transition_map.get(tkey, 0.0))
            if isinstance(strategy_transition_count_map, dict):
                tc = max(0, int(strategy_transition_count_map.get(tkey, 0)))
                branch += min(0.8, 0.6 / (1 + tc))

    return strategy_value, novelty, branch, sig


def update_strategy_memory_from_match(
    finals: List[float],
    move_signatures: Dict[int, List[str]],
    strategy_value_map: Dict[str, float],
    strategy_count_map: Dict[str, int],
    strategy_transition_map: Dict[str, float],
    strategy_transition_count_map: Dict[str, int],
) -> None:
    if not finals:
        return

    for i, sigs in move_signatures.items():
        if not sigs:
            continue
        my = finals[i]
        others = [s for j, s in enumerate(finals) if j != i]
        avg_other = (sum(others) / len(others)) if others else 0.0
        target = (my - avg_other) / 10.0
        if target > 4.0:
            target = 4.0
        elif target < -4.0:
            target = -4.0

        n = len(sigs)
        for step, sig in enumerate(sigs):
            discount = 0.995 ** (n - step - 1)
            y = target * discount
            c = max(0, int(strategy_count_map.get(sig, 0))) + 1
            strategy_count_map[sig] = c
            old = float(strategy_value_map.get(sig, 0.0))
            # Decaying-rate moving average so old learning remains but can adapt.
            rate = max(0.04, min(0.35, 1.0 / (c ** 0.5)))
            strategy_value_map[sig] = old + (y - old) * rate

        for j in range(len(sigs) - 1):
            key = f"{sigs[j]}=>{sigs[j + 1]}"
            old_t = float(strategy_transition_map.get(key, 0.0))
            strategy_transition_map[key] = old_t + (target - old_t) * 0.08
            strategy_transition_count_map[key] = max(0, int(strategy_transition_count_map.get(key, 0))) + 1

    for k in list(strategy_value_map.keys()):
        v = float(strategy_value_map[k])
        if abs(v) < 1e-6:
            del strategy_value_map[k]
            continue
        if v > 4.0:
            strategy_value_map[k] = 4.0
        elif v < -4.0:
            strategy_value_map[k] = -4.0

    if len(strategy_value_map) > 5000:
        top = sorted(strategy_value_map.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5000]
        keep = {k for k, _ in top}
        strategy_value_map.clear()
        strategy_value_map.update(top)
        strategy_count_map_keys = list(strategy_count_map.keys())
        for k in strategy_count_map_keys:
            if k not in keep:
                del strategy_count_map[k]

    if len(strategy_count_map) > 5000:
        top_counts = sorted(strategy_count_map.items(), key=lambda kv: kv[1], reverse=True)[:5000]
        strategy_count_map.clear()
        strategy_count_map.update({k: int(v) for k, v in top_counts})

    for k in list(strategy_transition_map.keys()):
        v = float(strategy_transition_map[k])
        if abs(v) < 1e-6:
            del strategy_transition_map[k]
            continue
        if v > 4.0:
            strategy_transition_map[k] = 4.0
        elif v < -4.0:
            strategy_transition_map[k] = -4.0

    if len(strategy_transition_map) > 8000:
        top_t = sorted(strategy_transition_map.items(), key=lambda kv: abs(kv[1]), reverse=True)[:8000]
        keep = {k for k, _ in top_t}
        strategy_transition_map.clear()
        strategy_transition_map.update(top_t)
        for k in list(strategy_transition_count_map.keys()):
            if k not in keep:
                del strategy_transition_count_map[k]

    if len(strategy_transition_count_map) > 8000:
        top_tc = sorted(strategy_transition_count_map.items(), key=lambda kv: kv[1], reverse=True)[:8000]
        strategy_transition_count_map.clear()
        strategy_transition_count_map.update({k: int(v) for k, v in top_tc})


def reinforce_human_teaching_signatures(
    move_signatures: Dict[int, List[str]],
    human_indices: set[int],
    strategy_value_map: Dict[str, float],
    strategy_count_map: Dict[str, int],
    strategy_transition_map: Dict[str, float],
    strategy_transition_count_map: Dict[str, int],
    boost: float,
) -> None:
    if not human_indices:
        return
    if boost <= 1.0:
        return

    move_rate = min(0.55, 0.12 + 0.04 * boost)
    trans_rate = min(0.45, 0.08 + 0.03 * boost)

    for i in human_indices:
        sigs = move_signatures.get(i, [])
        if not sigs:
            continue
        for sig in sigs:
            c = max(0, int(strategy_count_map.get(sig, 0))) + 1
            strategy_count_map[sig] = c
            old = float(strategy_value_map.get(sig, 0.0))
            strategy_value_map[sig] = old + (4.0 - old) * move_rate

        for j in range(len(sigs) - 1):
            key = f"{sigs[j]}=>{sigs[j + 1]}"
            old_t = float(strategy_transition_map.get(key, 0.0))
            strategy_transition_map[key] = old_t + (4.0 - old_t) * trans_rate
            strategy_transition_count_map[key] = max(0, int(strategy_transition_count_map.get(key, 0))) + 1

    for k in list(strategy_value_map.keys()):
        v = float(strategy_value_map[k])
        if v > 4.0:
            strategy_value_map[k] = 4.0
        elif v < -4.0:
            strategy_value_map[k] = -4.0

    for k in list(strategy_transition_map.keys()):
        v = float(strategy_transition_map[k])
        if v > 4.0:
            strategy_transition_map[k] = 4.0
        elif v < -4.0:
            strategy_transition_map[k] = -4.0


def reinforce_human_demo_from_board(gs: GameState, human_indices: set[int], brain: Dict[str, object], boost: float) -> None:
    if not human_indices or boost <= 1.0:
        return

    synergy_map = brain.get("synergy")
    species_map = brain.get("species_synergy")
    same_ocean_map = brain.get("same_ocean_synergy")
    weights = brain.get("weights")
    if not isinstance(synergy_map, dict) or not isinstance(species_map, dict) or not isinstance(same_ocean_map, dict):
        return
    if not isinstance(weights, dict):
        return

    pair_gain = min(0.35, 0.08 * boost)
    species_gain = min(0.22, 0.05 * boost)
    local_gain = min(0.42, 0.10 * boost)

    for i in sorted(human_indices):
        if i < 0 or i >= len(gs.players):
            continue
        p = gs.players[i]
        board_uids = player_board_face_uids(p)
        names = [gs.card_db[uid].name for uid in board_uids]
        species = [gs.card_db[uid].species for uid in board_uids]

        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                k = synergy_key(names[a], names[b])
                synergy_map[k] = float(synergy_map.get(k, 0.0) + pair_gain)
                sk = synergy_key(species[a], species[b])
                species_map[sk] = float(species_map.get(sk, 0.0) + species_gain)

        for ocean_uid in p.board_oceans:
            local = [gs.card_db[uid].name for uid in p.ocean_slots[ocean_uid].all_cards()]
            for a in range(len(local)):
                for b in range(a + 1, len(local)):
                    lk = synergy_key(local[a], local[b])
                    same_ocean_map[lk] = float(same_ocean_map.get(lk, 0.0) + local_gain)

    weights["synergy_bonus"] = float(weights.get("synergy_bonus", 0.0) + min(0.08, 0.02 * boost))
    weights["species_bonus"] = float(weights.get("species_bonus", 0.0) + min(0.07, 0.015 * boost))
    weights["same_ocean_bonus"] = float(weights.get("same_ocean_bonus", 0.0) + min(0.09, 0.02 * boost))
    weights["plan_fit_bonus"] = float(weights.get("plan_fit_bonus", 0.0) + min(0.08, 0.015 * boost))
    stabilize_weights(weights)


def strategy_family_profiles() -> List[Dict[str, Any]]:
    """High-level strategy families to learn across games."""
    return [
        {
            "label": "birds",
            "species": ["bird"],
            "names": [
                "emperor penguin",
                "horned puffin",
                "california seagull",
                "peruvian pelican",
                "great albatross",
                "osprey",
                "magnificent frigatebird",
                "razorbill auk",
            ],
            "support_names": ["sea urchin", "sea star"],
            "text_keywords": ["per bird"],
        },
        {
            "label": "birds_crustaceans",
            "species": ["bird", "crustacean"],
            "names": [
                "california seagull",
                "lobster",
                "spiny lobster",
                "mantis shrimp",
                "king crab",
                "hermit crab",
            ],
            "support_names": ["sea urchin", "cleaner wrasse"],
            "text_keywords": ["per crustacean"],
        },
        {
            "label": "game_fish",
            "species": ["game fish"],
            "names": [
                "yellowfin tuna",
                "big eye tuna",
                "mahi mahi",
                "blue marlin",
                "tarpon",
                "sailfish",
                "roosterfish",
                "goliath grouper",
                "king salmon",
                "barracuda",
            ],
            "support_names": ["sea cucumber", "artificial reef", "clownfish", "whale shark"],
            "text_keywords": ["game fish", "yellowfin tuna", "big eye tuna", "sharing an ocean with baitfish"],
        },
        {
            "label": "cephalopods",
            "species": ["cephalopod"],
            "names": [
                "reef trigger fish",
                "common octopus",
                "giant squid",
                "bobtail squid",
                "cuttlefish",
            ],
            "support_names": [
                "reef trigger fish",
                "reef triggerfish",
                "manta ray",
                "humuhumunukunukuapua'a",
                "humuhumu-nukunuku-apua'a",
            ],
            "text_keywords": ["cephalopod", "at least three cephalopods", "free cephalopods"],
        },
    ]


def strategy_family_profile_by_label(label: str) -> Optional[Dict[str, Any]]:
    key = str(label or "").strip().lower()
    for p in strategy_family_profiles():
        if str(p.get("label", "")).strip().lower() == key:
            return p
    return None


def strategy_family_card_score(card: CardDef, family_profile: Optional[Dict[str, Any]]) -> float:
    if not isinstance(family_profile, dict):
        return 0.0
    species_set = {str(x).strip().lower() for x in family_profile.get("species", [])}
    names_set = {str(x).strip().lower() for x in family_profile.get("names", [])}
    support_set = {str(x).strip().lower() for x in family_profile.get("support_names", [])}
    keywords = [str(x).strip().lower() for x in family_profile.get("text_keywords", [])]

    name = card.name.strip().lower()
    species = card.species.strip().lower()
    text = card.text.lower()
    score = 0.0
    if species in species_set:
        score += 2.0
    if name in names_set:
        score += 2.8
    if name in support_set:
        score += 1.2
    for kw in keywords:
        if kw and (kw in text or kw in name):
            score += 0.9
    return score


def entry_best_strategy_family_score(
    ms: MatchState,
    gs: GameState,
    entry_uid: int,
    family_profile: Optional[Dict[str, Any]],
) -> float:
    best = 0.0
    for face_uid in entry_faces(ms, entry_uid):
        s = strategy_family_card_score(gs.card_db[face_uid], family_profile)
        if s > best:
            best = s
    return best


def hand_strategy_family_fit_score(
    gs: GameState,
    ms: MatchState,
    hand_uids: List[int],
    family_profile: Optional[Dict[str, Any]],
) -> float:
    if not hand_uids or not isinstance(family_profile, dict):
        return -999.0
    total = 0.0
    hits = 0
    for entry_uid in hand_uids:
        best = entry_best_strategy_family_score(ms, gs, entry_uid, family_profile)
        total += best
        if best >= 1.0:
            hits += 1
    total += 0.18 * hits
    return total


def strategy_family_stats_bias(family_stats: Optional[Dict[str, Any]], label: str) -> float:
    if not isinstance(family_stats, dict):
        return 0.0
    rec = family_stats.get(label)
    if not isinstance(rec, dict):
        return 0.0
    games = max(0.0, float(rec.get("games", 0.0)))
    if games <= 0.0:
        return 0.0
    wins = max(0.0, float(rec.get("wins", 0.0)))
    score_sum = float(rec.get("score_sum", 0.0))
    win_rate = wins / games
    avg_score = score_sum / games
    confidence = min(1.0, games / 25.0)
    bias = ((win_rate - 0.35) * 1.8) + min(1.2, avg_score / 45.0)
    return bias * (0.15 + 0.85 * confidence)


def assign_strategy_families_from_opening_hands(
    gs: GameState,
    ms: MatchState,
    brain: Optional[Dict[str, object]],
    human_indices: set[int],
    rng: random.Random,
) -> List[Tuple[str, str, float]]:
    families = strategy_family_profiles()
    assigned: List[Tuple[str, str, float]] = []
    family_stats = None
    if isinstance(brain, dict):
        maybe_stats = brain.get("strategy_family_stats")
        if isinstance(maybe_stats, dict):
            family_stats = maybe_stats

    for i, p in enumerate(gs.players):
        if i in human_indices:
            continue
        best_label = ""
        best_fit = float("-inf")
        best_total = float("-inf")
        for fam in families:
            label = str(fam.get("label", ""))
            fit = hand_strategy_family_fit_score(gs, ms, p.hand, fam)
            hist = strategy_family_stats_bias(family_stats, label)
            total = fit + hist + rng.uniform(-0.15, 0.15)
            if total > best_total:
                best_total = total
                best_fit = fit
                best_label = label
        if best_label:
            p.flags["_strategy_family"] = best_label
            p.flags["_strategy_family_fit"] = float(best_fit)
            p.flags["_strategy_family_source"] = "opening_hand+learned"
            assigned.append((p.name, best_label, float(best_fit)))
    return assigned


def infer_player_strategy_family_label(gs: GameState, player: PlayerState) -> str:
    board_uids = player_board_face_uids(player)
    if not board_uids:
        return str(player.flags.get("_strategy_family", "unknown") or "unknown")
    best_label = "unknown"
    best_score = float("-inf")
    for fam in strategy_family_profiles():
        label = str(fam.get("label", "unknown"))
        score = 0.0
        birds = 0
        crust = 0
        for uid in board_uids:
            c = gs.card_db[uid]
            score += strategy_family_card_score(c, fam)
            sp = c.species.strip().lower()
            if sp == "bird":
                birds += 1
            if sp == "crustacean":
                crust += 1
        if label == "birds_crustaceans" and birds > 0 and crust > 0:
            score += 3.0
        if score > best_score:
            best_score = score
            best_label = label
    return best_label


def update_strategy_family_stats(brain: Dict[str, object], gs: GameState, players: List[PlayerState], scores: List[float]) -> None:
    stats = brain.get("strategy_family_stats")
    if not isinstance(stats, dict):
        stats = {}
        brain["strategy_family_stats"] = stats
    if not players or not scores or len(players) != len(scores):
        return
    top = max(float(s) for s in scores) if scores else 0.0
    for p, s_raw in zip(players, scores):
        s = float(s_raw)
        label = infer_player_strategy_family_label(gs, p)
        rec = stats.get(label)
        if not isinstance(rec, dict):
            rec = {"games": 0.0, "wins": 0.0, "score_sum": 0.0}
            stats[label] = rec
        rec["games"] = float(rec.get("games", 0.0)) + 1.0
        rec["score_sum"] = float(rec.get("score_sum", 0.0)) + s
        if s >= top:
            rec["wins"] = float(rec.get("wins", 0.0)) + 1.0


def default_archetype_profiles() -> List[Dict[str, Any]]:
    return [
        # ── Beginner ─────────────────────────────────────────────────────────
        {
            # "The All Blue" — maximize ocean cards for easy board presence.
            "label": "Ocean All Blue",
            "species": ["ocean", "coral"],
            "names": [
                "mangrove", "great albatross", "tide pool", "artificial reef",
                "coral reef", "arctic ocean", "deep ocean", "kelp forest", "pier",
            ],
            "name_contains": ["ocean", "reef", "tide", "mangrove", "kelp", "pier"],
            "text_keywords": ["place an ocean", "ocean card", "per ocean"],
            "support_names": ["coral reef", "artificial reef", "kelp forest", "deep ocean"],
        },
        {
            # Yellowfin Tuna — stack fish in one ocean to maximize space and scoring.
            "label": "Yellowfin Tuna Stack",
            "species": ["game fish"],
            "names": [
                "yellowfin tuna", "bigeye tuna", "big eye tuna", "sea cucumber",
                "artificial reef", "clownfish",
            ],
            "name_contains": ["yellowfin", "bigeye", "big eye"],
            "text_keywords": ["yellowfin tuna", "big eye tuna", "free game fish", "cleaner wrasse"],
            "support_names": ["sea cucumber", "artificial reef", "clownfish"],
        },
        {
            # Mammals — simple stack of dolphins/sharks/narwhals for steady scoring.
            "label": "Mammals",
            "species": ["mammal"],
            "names": [
                "great white shark", "spinner dolphin", "bottlenose dolphin", "narwhal",
                "blue tang",
            ],
            "name_contains": ["dolphin", "shark", "narwhal", "whale"],
            "text_keywords": ["per mammal", "free mammal"],
            "support_names": [
                "elk horn coral", "elkhorn coral", "staghorn coral", "deep sea coral",
                "red tree coral", "blue tang",
            ],
        },
        # ── Intermediate ─────────────────────────────────────────────────────
        {
            # Baitfish Barrage — flood board with baitfish, scale with predators.
            "label": "Baitfish Barrage",
            "species": ["baitfish"],
            "names": [
                "whale shark", "hermit crab", "roosterfish",
                "mullet", "bunker", "sardine", "flying fish", "bonito", "amberjack",
            ],
            "name_contains": [],
            "text_keywords": ["baitfish", "per baitfish", "sharing an ocean with baitfish", "whale shark"],
            "support_names": ["sea cucumber", "sea urchin", "hermit crab", "loggerhead sea turtle", "roosterfish"],
        },
        # ── Advanced ─────────────────────────────────────────────────────────
        {
            # Bird/Lobster (B-Lob) — birds + invertebrate synergies.
            "label": "Bird Lobster",
            "species": ["bird", "crustacean"],
            "names": [
                "emperor penguin", "razorbill auk", "california seagull", "horned puffin",
                "peruvian pelican", "lobster", "mantis shrimp", "artificial reef",
                "sea star", "common sea star", "clownfish",
            ],
            "name_contains": ["penguin", "puffin", "seagull", "pelican", "auk", "lobster"],
            "text_keywords": ["per bird", "california seagull", "crustacean"],
            "support_names": ["mantis shrimp", "lobster", "spiny lobster", "sea urchin", "sea star"],
        },
        {
            # Bird/Coral (B-Coral) — high-scoring birds + coral passive generation.
            "label": "Bird Coral",
            "species": ["bird", "coral"],
            "names": [
                "emperor penguin", "horned puffin", "peruvian pelican", "razorbill auk",
                "magnificent frigatebird", "staghorn coral", "deep sea coral",
                "grooved brain coral", "elkhorn coral", "elk horn coral", "coral reef",
            ],
            "name_contains": ["penguin", "puffin", "pelican", "auk", "frigatebird", "coral"],
            "text_keywords": ["per bird", "magnificent frigatebird", "coral"],
            "support_names": ["staghorn coral", "deep sea coral", "grooved brain coral", "elkhorn coral"],
        },
        {
            # Coral/Cephalopods (CC) — coral base + cephalopod explosive turns.
            "label": "Coral Cephalopods",
            "species": ["cephalopod", "coral"],
            "names": [
                "grooved brain coral", "staghorn coral", "deep sea coral", "elkhorn coral",
                "elk horn coral", "reef triggerfish", "reef trigger fish", "manta ray",
                "giant squid", "bobtail squid", "common octopus", "cuttlefish",
            ],
            "name_contains": ["octopus", "squid", "cuttlefish", "coral"],
            "text_keywords": ["cephalopod", "per cephalopod", "at least three cephalopods", "reef trigger fish"],
            "support_names": ["manta ray", "blue tang", "grooved brain coral"],
        },
        # ── Expert ───────────────────────────────────────────────────────────
        {
            # Goby "Shooting the Moon" — high-risk Goby + Spiny Lobster combo.
            "label": "Goby Moon Shot",
            "species": ["crosscurrent", "n/a", "crustacean"],
            "names": [
                "mandarin goby", "spiny lobster", "blue tang",
                "sea star", "common sea star",
            ],
            "name_contains": ["goby", "spiny"],
            "text_keywords": ["mandarin goby", "spiny lobster", "crosscurrent animal"],
            "support_names": ["california seagull", "artificial reef", "clownfish", "sea star", "blue tang"],
        },
        # ── Support / Hybrid ─────────────────────────────────────────────────
        {
            # Generic birds catch-all for any bird-heavy opening hand.
            "label": "Birds",
            "species": ["bird"],
            "names": [
                "emperor penguin", "horned puffin", "california seagull", "peruvian pelican",
                "great albatross", "osprey", "magnificent frigatebird", "razorbill auk",
            ],
            "name_contains": ["penguin", "puffin", "seagull", "pelican", "albatross", "osprey", "frigatebird", "auk"],
            "text_keywords": ["per bird"],
            "support_names": ["mantis shrimp", "lobster", "spiny lobster", "sea urchin"],
        },
        {
            # Cephalopods-only for hands heavy on squids/octopus without much coral.
            "label": "Cephalopods",
            "species": ["cephalopod"],
            "names": [
                "reef trigger fish", "reef triggerfish", "bobtail squid", "cuttlefish",
                "common octopus", "giant squid", "manta ray",
            ],
            "name_contains": ["octopus", "squid", "cuttlefish"],
            "text_keywords": ["cephalopod", "free cephalopods", "draw one when you play a cephalopod"],
            "support_names": ["manta ray", "blue tang"],
        },
        {
            # King Salmon / Coral Fill — fill oceans for bonus scoring.
            "label": "King Salmon Coral Fill",
            "species": ["coral", "game fish"],
            "names": [
                "king salmon", "red tree coral", "clownfish", "artificial reef",
                "coral reef", "staghorn coral", "elk horn coral", "elkhorn coral", "deep sea coral",
            ],
            "name_contains": [],
            "text_keywords": ["fully occupied ocean", "sharing an ocean with a king salmon", "card attached"],
            "support_names": ["sea star", "common sea star", "sea urchin", "sea anemone", "bottlenose dolphin"],
        },
    ]


def resolve_archetype_profile(player: PlayerState, archetype_profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(archetype_profile, dict):
        return archetype_profile
    stored = player.flags.get("_ai_profile")
    return stored if isinstance(stored, dict) else None


def select_archetype_profiles(num_players: int, seed: int) -> Optional[List[Dict[str, Any]]]:
    profiles = default_archetype_profiles()
    if num_players <= 0 or not profiles:
        return None

    # For 4-player games, keep the first 3 core engines stable and rotate slot 4
    # through remaining archetypes.
    if num_players == 4 and len(profiles) >= 4:
        core = ["Birds", "Baitfish Engine", "Yellowfin BigEye Cleaner"]
        by_label = {str(p.get("label", "")): p for p in profiles}
        selected: List[Dict[str, Any]] = []
        for label in core:
            p = by_label.get(label)
            if isinstance(p, dict):
                selected.append(dict(p))
        if len(selected) < 3:
            while len(selected) < 3 and len(selected) < len(profiles):
                selected.append(dict(profiles[len(selected)]))
        remaining = [p for p in profiles if str(p.get("label", "")) not in core]
        if remaining:
            selected.append(dict(remaining[seed % len(remaining)]))
        while len(selected) < num_players:
            selected.append(dict(profiles[len(selected) % len(profiles)]))
        return selected[:num_players]

    if num_players >= len(profiles):
        return [dict(profiles[i % len(profiles)]) for i in range(num_players)]

    start = seed % len(profiles)
    return [dict(profiles[(start + i) % len(profiles)]) for i in range(num_players)]


def hand_archetype_fit_score(gs: GameState, ms: MatchState, hand_uids: List[int], profile: Dict[str, Any]) -> float:
    if not hand_uids:
        return -999.0
    score = 0.0
    hits = 0
    for entry_uid in hand_uids:
        best = 0.0
        for face_uid in entry_faces(ms, entry_uid):
            v = card_archetype_score(gs.card_db[face_uid], profile)
            if v > best:
                best = v
        score += best
        if best >= 1.0:
            hits += 1
    score += 0.15 * hits
    return score


def assign_archetypes_from_opening_hands(
    gs: GameState,
    ms: MatchState,
    human_index: Optional[int] = None,
) -> List[Tuple[str, str, float]]:
    profiles = default_archetype_profiles()
    assigned: List[Tuple[str, str, float]] = []
    if not profiles:
        return assigned

    for i, p in enumerate(gs.players):
        if human_index is not None and i == human_index:
            continue
        best_profile = None
        best_score = float("-inf")
        for prof in profiles:
            s = hand_archetype_fit_score(gs, ms, p.hand, prof)
            if s > best_score:
                best_score = s
                best_profile = prof
        chosen = dict(best_profile) if isinstance(best_profile, dict) else dict(profiles[i % len(profiles)])
        p.flags["_ai_profile"] = chosen
        p.flags["_ai_profile_source"] = "opening_hand"
        assigned.append((p.name, str(chosen.get("label", "Unknown")), float(best_score)))
    return assigned


def card_archetype_score(card: CardDef, profile: Optional[Dict[str, Any]]) -> float:
    if not isinstance(profile, dict):
        return 0.0

    species_set = {str(x).strip().lower() for x in profile.get("species", [])}
    names_set = {str(x).strip().lower() for x in profile.get("names", [])}
    support_names = {str(x).strip().lower() for x in profile.get("support_names", [])}
    name_contains = [str(x).strip().lower() for x in profile.get("name_contains", [])]
    text_keywords = [str(x).strip().lower() for x in profile.get("text_keywords", [])]

    name = card.name.strip().lower()
    species = card.species.strip().lower()
    text = card.text.lower()

    score = 0.0
    if species and species in species_set:
        score += 2.2
    if name in names_set:
        score += 3.0
    if name in support_names:
        score += 1.5
    for token in name_contains:
        if token and token in name:
            score += 1.8
    for kw in text_keywords:
        if kw and (kw in text or kw in name):
            score += 1.2

    # Cross-strategy utility: cards can still be good outside a primary archetype.
    if "draw" in text:
        score += 0.35
    if "play again" in text or "go again" in text:
        score += 0.45
    if "play a free" in text or "for free" in text:
        score += 0.4
    if card.cost <= 1:
        score += 0.25

    return score


def entry_best_archetype_score(ms: MatchState, gs: GameState, entry_uid: int, profile: Optional[Dict[str, Any]]) -> float:
    best = 0.0
    for face_uid in entry_faces(ms, entry_uid):
        s = card_archetype_score(gs.card_db[face_uid], profile)
        if s > best:
            best = s
    return best


def action_archetype_bonus(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    archetype_profile: Optional[Dict[str, Any]],
) -> float:
    profile = resolve_archetype_profile(player, archetype_profile)
    family_label = str(player.flags.get("_strategy_family", "")).strip().lower()
    family_profile = strategy_family_profile_by_label(family_label)
    if not isinstance(profile, dict) and not isinstance(family_profile, dict):
        return 0.0

    if action.kind == "draw":
        bonus = 0.0
        if action.pool_pick_uids:
            for uid in action.pool_pick_uids:
                if isinstance(profile, dict):
                    bonus += 0.9 * entry_best_archetype_score(ms, gs, uid, profile)
                if isinstance(family_profile, dict):
                    bonus += 0.65 * entry_best_strategy_family_score(ms, gs, uid, family_profile)
        elif action.draw_from_pool > 0 and ms.pool:
            # small upside for pool draw when no explicit pick assigned
            bonus += 0.15
        return bonus

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0
    cname = card.name.strip().lower()
    cspecies = card.species.strip().lower()
    score = card_archetype_score(card, profile) if isinstance(profile, dict) else 0.0
    label = str(profile.get("label", "")).strip().lower() if isinstance(profile, dict) else ""
    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    board_names = [c.name.strip().lower() for c in board_cards]
    board_species = [c.species.strip().lower() for c in board_cards]
    card_tags = card_strategy_tags(card)

    hand_name_counts: Dict[str, int] = {}
    hand_species_counts: Dict[str, int] = {}
    hand_tag_counts: Dict[str, int] = {}
    for entry_uid in player.hand:
        if action.card_uid != -1 and entry_uid == action.card_uid:
            continue
        for face_uid2 in entry_faces(ms, entry_uid):
            c2 = gs.card_db[face_uid2]
            n2 = c2.name.strip().lower()
            s2 = c2.species.strip().lower()
            hand_name_counts[n2] = hand_name_counts.get(n2, 0) + 1
            hand_species_counts[s2] = hand_species_counts.get(s2, 0) + 1
            for t2 in card_strategy_tags(c2):
                hand_tag_counts[t2] = hand_tag_counts.get(t2, 0) + 1

    if action.kind == "play_ocean":
        if label == "yellowfin bigeye cleaner" and card.name.strip().lower() == "artificial reef":
            engine_count = (
                board_names.count("yellowfin tuna")
                + board_names.count("big eye tuna")
                + board_names.count("cleaner wrasse")
                + board_names.count("sea cucumber")
            )
            if not player.board_oceans:
                return 0.35 + min(1.0, 0.25 * engine_count)
            if count_empty_oceans(player) > 0:
                return 0.2 + min(1.5, 0.3 * engine_count)
            return 0.55 + min(2.0, 0.35 * engine_count)
        if not player.board_oceans:
            return 0.25
        if count_empty_oceans(player) > 0:
            return -1.0
        return -0.35

    # play_to_ocean
    bonus = score if score > 0 else (0.08 if isinstance(family_profile, dict) else 0.0)

    # Loggerhead timing: hold until a true burst turn (full hand + many cheap follow-ups).
    logger_count = board_names.count("loggerhead sea turtle")
    cheap_hand_cards = 0
    expensive_hand_cards = 0
    hand_after_size = max(0, len(player.hand) - (1 if action.card_uid in player.hand else 0))
    for entry_uid in player.hand:
        if action.card_uid != -1 and entry_uid == action.card_uid:
            continue
        face_options = entry_faces(ms, entry_uid)
        if not face_options:
            continue
        min_cost = min(gs.card_db[fu].cost for fu in face_options)
        if min_cost <= 1:
            cheap_hand_cards += 1
        elif min_cost >= 3:
            expensive_hand_cards += 1
    cheap_density = (cheap_hand_cards / max(1, hand_after_size))
    turtle_burst_ready = hand_after_size >= 7 and cheap_hand_cards >= 4 and cheap_density >= 0.5
    turtle_almost_ready = hand_after_size >= 6 and cheap_hand_cards >= 3 and cheap_density >= 0.45
    if cname == "loggerhead sea turtle":
        if turtle_burst_ready:
            bonus += 2.2 + min(2.8, 0.55 * cheap_hand_cards)
        elif turtle_almost_ready:
            bonus += 0.25 + min(0.6, 0.2 * cheap_hand_cards)
            bonus -= 1.1
        else:
            # User strategy: do NOT spend turtle early; wait for a loaded hand.
            bonus -= 4.8
        if hand_after_size <= 5:
            bonus -= 1.8
        if cheap_density < 0.45:
            bonus -= 1.4
        bonus -= min(1.2, 0.25 * expensive_hand_cards)
    if logger_count > 0 and card.cost <= 1:
        bonus += min(2.3, 0.65 * logger_count)
    if cname == "roosterfish":
        bait_count_global = sum(1 for s in board_species if s == "baitfish")
        gamefish_count_global = sum(1 for s in board_species if s == "game fish")
        # Cheap, flexible card for most strategies with extra baitfish upside.
        bonus += 0.45 + min(1.5, 0.35 * bait_count_global + 0.12 * gamefish_count_global)

    # Strict engine readiness checks: avoid dead setup cards with no support.
    board_baitfish = sum(1 for s in board_species if s == "baitfish")
    board_cephalopod = sum(1 for s in board_species if s == "cephalopod")
    board_game_fish = sum(1 for s in board_species if s == "game fish")
    baitfish_ready = board_baitfish + hand_species_counts.get("baitfish", 0)
    cephalopod_ready = board_cephalopod + hand_species_counts.get("cephalopod", 0)
    game_fish_ready = board_game_fish + hand_species_counts.get("game fish", 0)
    goby_ready = board_names.count("mandarin goby") + hand_name_counts.get("mandarin goby", 0)
    reef_trigger_ready = (
        board_names.count("reef trigger fish")
        + board_names.count("reef triggerfish")
        + hand_name_counts.get("reef trigger fish", 0)
        + hand_name_counts.get("reef triggerfish", 0)
    )

    if cname == "hermit crab":
        if baitfish_ready <= 0:
            bonus -= 4.4
        elif baitfish_ready == 1:
            bonus -= 0.8
        else:
            bonus += min(2.4, 0.85 * baitfish_ready)

    if cname == "reef trigger fish":
        if cephalopod_ready <= 0:
            bonus -= 4.6
        elif cephalopod_ready == 1:
            bonus -= 1.2
        else:
            bonus += min(2.8, 0.80 * cephalopod_ready)

    if cname == "spiny lobster":
        if goby_ready <= 0:
            bonus -= 3.8
        elif goby_ready == 1:
            bonus -= 0.7
        else:
            bonus += min(2.2, 0.85 * goby_ready)

    if cname == "manta ray":
        ceph_engine_ready = cephalopod_ready + reef_trigger_ready
        if ceph_engine_ready <= 0:
            bonus -= 3.4
        elif ceph_engine_ready == 1:
            bonus -= 0.5
        else:
            bonus += min(1.8, 0.5 * ceph_engine_ready)

    if cname == "sea cucumber":
        if game_fish_ready <= 0:
            bonus -= 1.8
        else:
            bonus += min(1.4, 0.4 * game_fish_ready)

    # Stronger strategy commitment: once a lane is established, avoid random off-plan plays.
    core_engine_tags = {
        "engine:baitfish",
        "engine:bird",
        "engine:cephalopod",
        "engine:coral",
        "engine:crustacean",
        "engine:gamefish",
        "engine:goby-spiny",
        "engine:mammal",
        "engine:na",
        "engine:yellowfin",
    }
    family_to_engine = {
        "birds": "engine:bird",
        "birds_crustaceans": "engine:crustacean",
        "game_fish": "engine:gamefish",
        "cephalopods": "engine:cephalopod",
    }
    board_profile = board_strategy_profile(gs, player)
    best_engine = ""
    best_strength = 0.0
    for t in core_engine_tags:
        strength = 1.75 * float(board_profile.get(t, 0)) + 0.85 * float(hand_tag_counts.get(t, 0))
        if strength > best_strength:
            best_strength = strength
            best_engine = t
    fam_engine = family_to_engine.get(family_label, "")
    if fam_engine:
        fam_strength = 1.25 + 1.35 * float(board_profile.get(fam_engine, 0)) + 0.65 * float(hand_tag_counts.get(fam_engine, 0))
        if fam_strength > best_strength:
            best_strength = fam_strength
            best_engine = fam_engine

    non_ocean_count = sum(1 for c in board_cards if not is_ocean(c))
    committed = best_strength >= 4.0 and non_ocean_count >= 2
    card_engine_tags = {t for t in card_tags if t in core_engine_tags}
    neutral_tags = {
        "engine:draw",
        "engine:tempo",
        "engine:freeplay",
        "engine:symbol",
        "engine:cheap-burst",
        "engine:cheap-flex",
        "engine:ocean",
        "engine:ocean-race",
    }
    if committed:
        if best_engine in card_engine_tags:
            bonus += min(2.8, 0.45 * best_strength)
        elif not is_ocean(card):
            # Strategic pivot credit: off-lane plays that still improve future
            # combo potential should not be punished as harshly.
            innovation_credit = 0.0
            text_low = card.text.lower()
            if "draw" in text_low:
                innovation_credit += 0.25
            if "play again" in text_low or "go again" in text_low:
                innovation_credit += 0.35
            if "for free" in text_low or "play any number" in text_low:
                innovation_credit += 0.35
            if "per " in text_low:
                innovation_credit += 0.20
            if card_engine_tags:
                off_support = sum(hand_tag_counts.get(t, 0) for t in card_engine_tags)
                innovation_credit += min(1.20, 0.18 * off_support)
            if action.ocean_uid is not None:
                local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
                if any(
                    c.species.strip().lower() == cspecies and c.species.strip().lower() != "ocean"
                    for c in local_cards
                ):
                    innovation_credit += 0.35

            if card_engine_tags:
                off_penalty = min(3.6, 0.5 * best_strength)
                off_penalty = max(0.4, off_penalty - min(1.8, innovation_credit))
                bonus -= off_penalty
            elif any(t in card_tags for t in neutral_tags):
                off_penalty = min(1.4, 0.2 * best_strength)
                off_penalty = max(0.15, off_penalty - min(1.2, innovation_credit))
                bonus -= off_penalty

    if isinstance(family_profile, dict):
        fam_score = strategy_family_card_score(card, family_profile)
        if fam_score > 0:
            bonus += 0.75 * fam_score
    if action.ocean_uid is not None and score > 0:
        local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
        local_match = sum(1 for c in local_cards if card_archetype_score(c, profile) > 0)
        if local_match > 0:
            bonus += min(2.0, 0.6 * local_match)
    if action.ocean_uid is not None and isinstance(family_profile, dict):
        local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
        local_family_match = sum(1 for c in local_cards if strategy_family_card_score(c, family_profile) > 0)
        if local_family_match > 0:
            bonus += min(1.8, 0.35 * local_family_match)

    # Profile-specific contextual combo nudges.
    if label == "birds":
        crust_count = sum(1 for s in board_species if s == "crustacean")
        seagull_count = board_names.count("california seagull")
        bird_count = sum(1 for s in board_species if s == "bird")
        sea_urchin_count = board_names.count("sea urchin")
        razorbill_count = board_names.count("razorbill auk")

        if cname == "california seagull":
            bonus += min(3.0, 0.9 * crust_count)
        if cspecies == "crustacean":
            bonus += min(3.0, 1.1 * seagull_count)
        if cname == "sea urchin":
            bonus += min(2.5, 0.6 * bird_count)
        if cspecies == "bird":
            bonus += min(2.0, 1.0 * sea_urchin_count)
        if cname == "razorbill auk":
            # Razorbill scales hard at 2 copies (1=5, 2=25), so prefer completing the pair.
            bonus += 3.2 if razorbill_count >= 1 else 0.8

    elif label == "baitfish engine":
        bait_count = sum(1 for s in board_species if s == "baitfish")
        game_fish_count = sum(1 for s in board_species if s == "game fish")
        sea_urchin_count = board_names.count("sea urchin")
        sea_cucumber_count = board_names.count("sea cucumber")
        if cname in {"amberjack", "whale shark"}:
            bonus += min(3.5, 0.8 * bait_count)
        if cname == "whale shark":
            # User baitfish model: Whale Shark should also value game-fish-heavy boards.
            bonus += min(2.5, 0.45 * game_fish_count)
        if cspecies == "baitfish":
            helpers = board_names.count("amberjack") + board_names.count("whale shark")
            bonus += min(2.5, 0.7 * helpers)
            bonus += min(2.0, 0.8 * sea_urchin_count)
        if cname == "sea urchin":
            bonus += min(2.5, 0.6 * bait_count)
        if cname == "sea cucumber":
            bonus += min(2.5, 0.65 * bait_count)
            bonus += min(1.4, 0.35 * game_fish_count)
        if cname == "roosterfish":
            bonus += min(3.0, 0.75 * bait_count)
        if cspecies == "baitfish" and sea_cucumber_count > 0:
            bonus += min(1.8, 0.45 * sea_cucumber_count)

    elif label == "yellowfin bigeye cleaner":
        yellow_count = board_names.count("yellowfin tuna")
        bigeye_count = board_names.count("big eye tuna")
        cleaner_count = board_names.count("cleaner wrasse")
        sea_cucumber_count = board_names.count("sea cucumber")
        clownfish_count = board_names.count("clownfish")
        artificial_reef_count = board_names.count("artificial reef")
        hand_game_fish = 0
        for entry_uid in player.hand:
            if action.card_uid != -1 and entry_uid == action.card_uid:
                continue
            for face_uid2 in entry_faces(ms, entry_uid):
                if gs.card_db[face_uid2].species.strip().lower() == "game fish":
                    hand_game_fish += 1
                    break
        if cname == "yellowfin tuna":
            bonus += min(4.0, 1.0 * bigeye_count)
            bonus += min(1.4, 0.35 * cleaner_count)
        if cname == "big eye tuna":
            bonus += min(4.0, 1.0 * yellow_count)
            bonus += min(1.0, 0.25 * cleaner_count)
        if cname == "cleaner wrasse":
            bonus += min(3.0, 0.65 * (yellow_count + bigeye_count))
        if cname == "clownfish":
            # Clownfish is strong in this engine when it feeds non-Deep-Ocean build-outs.
            core_engine = yellow_count + bigeye_count + cleaner_count + sea_cucumber_count
            bonus += min(3.2, 0.55 * core_engine)
            bonus += min(2.8, 1.0 * artificial_reef_count)
            if action.ocean_uid is not None:
                ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()
                bonus += clownfish_ocean_value(ocean_name)
                if ocean_name == "artificial reef":
                    bonus += 2.0
                    slots = player.ocean_slots[action.ocean_uid]
                    occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
                    bonus += min(1.5, 0.4 * occupied_dirs)
        if cname == "sailfish":
            # Sailfish helps chain into free game-fish followups in this engine.
            bonus += min(2.8, 0.55 * (yellow_count + bigeye_count))
            bonus += min(1.5, 0.3 * hand_game_fish)
        if cname == "sea cucumber":
            bonus += min(2.8, 0.55 * (yellow_count + bigeye_count + cleaner_count))
            bonus += min(1.2, 0.3 * sea_cucumber_count)
        if cname in {"yellowfin tuna", "big eye tuna", "cleaner wrasse", "sea cucumber", "clownfish"}:
            bonus += min(2.2, 0.6 * artificial_reef_count)
            if action.ocean_uid is not None:
                ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()
                if ocean_name == "artificial reef":
                    bonus += 1.8
        if cname in {"yellowfin tuna", "big eye tuna"} and clownfish_count > 0:
            bonus += min(2.4, 0.8 * clownfish_count)
            if action.ocean_uid is not None:
                local_names = [
                    gs.card_db[uid].name.strip().lower()
                    for uid in player.ocean_slots[action.ocean_uid].all_cards()
                ]
                if "clownfish" in local_names:
                    bonus += 1.4

    elif label == "mammals sharks":
        mammal_count = sum(1 for s in board_species if s == "mammal")
        shark_count = sum(1 for n in board_names if "shark" in n)
        coral_count = sum(1 for s in board_species if s == "coral")
        bottlenose_count = board_names.count("bottlenose dolphin")
        red_tree_count = board_names.count("red tree coral")
        king_salmon_count = board_names.count("king salmon")
        full_oceans = 0
        for ocean_uid in player.board_oceans:
            slots = player.ocean_slots[ocean_uid]
            occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
            if occupied_dirs >= 4:
                full_oceans += 1
        if cspecies == "mammal":
            bonus += min(2.5, 0.55 * mammal_count)
        if "shark" in cname:
            bonus += min(2.2, 0.6 * mammal_count)
        if "dolphin" in cname:
            bonus += min(2.2, 0.5 * shark_count)
        if cname == "bottlenose dolphin":
            bonus += min(3.0, 0.8 * coral_count)
        if cspecies == "coral":
            bonus += min(3.0, 0.9 * bottlenose_count)
        if cname == "red tree coral":
            bonus += min(2.6, 0.9 * king_salmon_count)
        if cname == "king salmon":
            bonus += min(2.6, 0.9 * red_tree_count)
            bonus += min(2.0, 0.6 * full_oceans)
        if action.ocean_uid is not None and cname in {"red tree coral", "king salmon"}:
            local_names = [gs.card_db[uid].name.strip().lower() for uid in player.ocean_slots[action.ocean_uid].all_cards()]
            if cname == "red tree coral" and "king salmon" in local_names:
                bonus += 2.0
            if cname == "king salmon" and "red tree coral" in local_names:
                bonus += 2.0
            if cname == "king salmon":
                slots = player.ocean_slots[action.ocean_uid]
                occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
                if occupied_dirs >= 3:
                    bonus += 1.5

    elif label == "cephalopods":
        ceph_count = sum(1 for s in board_species if s == "cephalopod")
        manta_count = board_names.count("manta ray")
        humuhumu_count = sum(1 for n in board_names if "humuhumu" in n)
        if cspecies == "cephalopod":
            bonus += min(3.2, 0.75 * ceph_count)
            bonus += min(2.0, 0.7 * humuhumu_count)
        if cname == "manta ray":
            bonus += min(3.5, 0.9 * ceph_count)
        if "humuhumu" in cname:
            bonus += min(3.0, 0.8 * ceph_count)
        if cname in {"common octopus", "commen octopus", "gaint squid", "giant squid", "bobtail squid", "cuttlefish"}:
            bonus += min(2.5, 0.65 * ceph_count)
        if cspecies == "cephalopod" and manta_count > 0:
            bonus += min(2.0, 0.6 * manta_count)

    elif label == "goby spiny combo":
        goby_count = board_names.count("mandarin goby")
        spiny_count = board_names.count("spiny lobster")
        sea_star_count = board_names.count("sea star")
        crust_count = sum(1 for s in board_species if s == "crustacean")
        symbol_less_non_ocean = sum(
            1
            for c in board_cards
            if normalize_symbol(c.symbol) in {"", "n/a"} and c.species.strip().lower() != "ocean"
        )
        if cname == "sea star":
            bonus += min(3.2, 0.9 * spiny_count + 0.35 * crust_count)
            bonus += min(1.6, 0.35 * symbol_less_non_ocean)
        if cname == "spiny lobster":
            bonus += min(3.0, 0.9 * goby_count)
            bonus += min(1.5, 0.45 * sea_star_count)
        if cname == "mandarin goby":
            bonus += min(3.0, 0.9 * spiny_count)
            bonus += min(1.0, 0.35 * sea_star_count)
        if cname == "california seagull":
            bonus += min(2.8, 0.8 * crust_count)
        if cspecies == "crustacean":
            bonus += min(2.2, 0.55 * board_names.count("california seagull"))

    elif label == "king salmon coral fill":
        king_salmon_count = board_names.count("king salmon")
        red_tree_count = board_names.count("red tree coral")
        coral_count = sum(1 for s in board_species if s == "coral")
        invertebrate_count = sum(1 for s in board_species if s == "invertebrate")
        full_oceans = 0
        near_full_oceans = 0
        for ocean_uid in player.board_oceans:
            slots = player.ocean_slots[ocean_uid]
            occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
            if occupied_dirs >= 4:
                full_oceans += 1
            elif occupied_dirs >= 3:
                near_full_oceans += 1

        if cname == "king salmon":
            bonus += min(3.5, 1.0 * red_tree_count)
            bonus += min(3.0, 0.65 * full_oceans + 0.5 * near_full_oceans)
        if cname == "red tree coral":
            bonus += min(3.5, 1.0 * king_salmon_count)
        if cspecies == "coral":
            bonus += min(2.2, 0.35 * (king_salmon_count + red_tree_count))
        if cspecies == "invertebrate":
            bonus += min(1.5, 0.2 * coral_count)
        if cname in {"sea star", "sea urchin", "sea anemone"}:
            bonus += min(2.0, 0.5 * coral_count + 0.25 * invertebrate_count)
        if cname == "clownfish":
            bonus += min(2.4, 0.5 * (king_salmon_count + red_tree_count + coral_count))

        if action.ocean_uid is not None:
            local_names = [gs.card_db[uid].name.strip().lower() for uid in player.ocean_slots[action.ocean_uid].all_cards()]
            slots = player.ocean_slots[action.ocean_uid]
            occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
            # Reward finishing/packing oceans for King Salmon.
            if action.kind == "play_to_ocean":
                bonus += min(1.8, 0.35 * occupied_dirs)
                if occupied_dirs >= 3:
                    bonus += 1.2
            if cname == "red tree coral" and "king salmon" in local_names:
                bonus += 2.2
            if cname == "king salmon" and "red tree coral" in local_names:
                bonus += 2.2
            if cname == "clownfish":
                ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()
                bonus += 0.8 * clownfish_ocean_value(ocean_name)
    if bonus > 6.0:
        return 6.0
    if bonus < -6.0:
        return -6.0
    return bonus


def pool_entry_value_for_player(ms: MatchState, gs: GameState, entry_uid: int, player: PlayerState) -> float:
    faces = entry_faces(ms, entry_uid)
    profile = board_strategy_profile(gs, player)
    need_symbols, need_species = star_prep_needs(gs, ms, player)
    best = -1e9
    for uid in faces:
        c = gs.card_db[uid]
        v = 0.0
        if is_ocean(c):
            v += 2.2 if len(player.board_oceans) < 3 else 1.1
        v += 0.12 * sum(int(m.group(1)) for m in re.finditer(r"\+(\d+)", c.text))
        v += 0.08 * max(0, 3 - c.cost)
        for t in card_strategy_tags(c):
            v += 0.10 * min(profile.get(t, 0), 4)
        sym = normalize_symbol(c.symbol)
        if sym in need_symbols:
            v += 2.4
        if c.species.strip().lower() in need_species:
            v += 2.6
            v += 0.10 * max(0, 4 - c.cost)
        if v > best:
            best = v
    return best if best > -1e8 else 0.0


def action_deny_bonus(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    if action.kind != "draw" or action.draw_from_pool <= 0 or not action.pool_pick_uids:
        return 0.0
    opponents = [p for p in gs.players if p is not player]
    if not opponents:
        return 0.0

    deny = 0.0
    for uid in action.pool_pick_uids:
        opp_best = max(pool_entry_value_for_player(ms, gs, uid, op) for op in opponents)
        deny += opp_best
    deny = deny / max(1, len(action.pool_pick_uids))
    # Pool denial matters more as the pool approaches clear-at-10, but keep it capped
    # so denial does not overwhelm proactive scoring plans.
    pool_pressure = max(0.0, (len(ms.pool) - 6) / 4.0)
    deny *= 1.0 + min(0.30, 0.18 * pool_pressure)
    if len(ms.pool) >= 9:
        deny += 0.25
    if deny > 3.0:
        return 3.0
    return deny


ENGINE_TIMING_CARDS = {"loggerhead sea turtle", "hermit crab"}


def entry_has_engine_timing_card(ms: MatchState, gs: GameState, entry_uid: int) -> bool:
    for face_uid in entry_faces(ms, entry_uid):
        if gs.card_db[face_uid].name.strip().lower() in ENGINE_TIMING_CARDS:
            return True
    return False


def hand_has_engine_timing_card(ms: MatchState, gs: GameState, player: PlayerState) -> bool:
    return any(entry_has_engine_timing_card(ms, gs, uid) for uid in player.hand)


def action_engine_timing_bonus(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    # Once chain window is active, don't interfere with move choice.
    if has_multi_play_window(player):
        return 0.0

    hand_n = len(player.hand)
    have_engine_in_hand = hand_has_engine_timing_card(ms, gs, player)

    if action.kind == "draw":
        # If an engine is waiting in hand and hand is small, draw up first.
        if have_engine_in_hand and hand_n < 8:
            return min(3.0, 1.6 + 0.45 * (8 - hand_n))
        # With very full hand, drawing is less useful for engine timing.
        if have_engine_in_hand and hand_n >= 10:
            return -0.6
        return 0.0

    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return 0.0

    name = card.name.strip().lower()
    if name in ENGINE_TIMING_CARDS:
        if name == "hermit crab":
            # Hermit Crab only has true value when it can immediately release baitfish.
            if not has_playable_followup_species(gs, ms, player, "baitfish", exclude_entry_uid=action.card_uid):
                return -4.8
        # Strong preference: fire these engines at 8-10 cards.
        if 8 <= hand_n <= 10:
            return 3.0 + 0.2 * (hand_n - 8)
        if hand_n == 7:
            return 0.2
        if hand_n < 7:
            return -3.2 + 0.45 * hand_n
        # Slightly reduced value above 10 due to discard pressure.
        return 1.0

    # Mildly discourage non-engine commits while waiting to draw up and fire engine.
    if have_engine_in_hand and hand_n < 8:
        if action.kind == "play_ocean" and not player.board_oceans:
            return 0.0
        return -0.8
    return 0.0


def action_is_dead_engine_play(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> bool:
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return False
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return False

    name = card.name.strip().lower()
    hand_name_counts: Dict[str, int] = {}
    hand_species_counts: Dict[str, int] = {}
    for entry_uid in player.hand:
        if action.card_uid != -1 and entry_uid == action.card_uid:
            continue
        for face_uid2 in entry_faces(ms, entry_uid):
            c2 = gs.card_db[face_uid2]
            n2 = c2.name.strip().lower()
            s2 = c2.species.strip().lower()
            hand_name_counts[n2] = hand_name_counts.get(n2, 0) + 1
            hand_species_counts[s2] = hand_species_counts.get(s2, 0) + 1

    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    board_names = [c.name.strip().lower() for c in board_cards]
    board_species = [c.species.strip().lower() for c in board_cards]

    if name == "hermit crab":
        baitfish_ready = hand_species_counts.get("baitfish", 0) + sum(1 for s in board_species if s == "baitfish")
        return baitfish_ready <= 0
    if name == "reef trigger fish":
        cephalopod_ready = hand_species_counts.get("cephalopod", 0) + sum(1 for s in board_species if s == "cephalopod")
        return cephalopod_ready <= 0
    if name == "spiny lobster":
        goby_ready = hand_name_counts.get("mandarin goby", 0) + board_names.count("mandarin goby")
        return goby_ready <= 0
    if name == "manta ray":
        cephalopod_ready = hand_species_counts.get("cephalopod", 0) + sum(1 for s in board_species if s == "cephalopod")
        reef_trigger_ready = (
            hand_name_counts.get("reef trigger fish", 0)
            + hand_name_counts.get("reef triggerfish", 0)
            + board_names.count("reef trigger fish")
            + board_names.count("reef triggerfish")
        )
        return (cephalopod_ready + reef_trigger_ready) <= 0
    return False


def expand_draw_actions_for_ai(gs: GameState, ms: MatchState, player: PlayerState, actions: List[Action]) -> List[Action]:
    out: List[Action] = []
    for a in actions:
        if a.kind != "draw" or a.draw_from_pool <= 0:
            out.append(a)
            continue

        # keep default behavior as a candidate
        out.append(a)
        scored = [(uid, pool_entry_value_for_player(ms, gs, uid, player)) for uid in ms.pool]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = [uid for uid, _ in scored[: min(4, len(scored))]]
        if not top:
            continue

        if a.draw_from_pool == 1:
            for uid in top:
                b = copy.deepcopy(a)
                b.pool_pick_uids = [uid]
                out.append(b)
        elif a.draw_from_pool == 2 and len(top) >= 2:
            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    b = copy.deepcopy(a)
                    b.pool_pick_uids = [top[i], top[j]]
                    out.append(b)
    return out


def candidate_actions_for_ai(gs: GameState, ms: MatchState, player: PlayerState) -> List[Action]:
    acts = [a for a in legal_actions(gs, ms, player, include_draw=True) if a.kind != "end_turn"]
    if not acts:
        return []
    star_keys = {
        (a.kind, a.card_uid, a.face_uid if a.face_uid is not None else a.card_uid, a.ocean_uid)
        for a in acts
        if a.use_star
    }
    filtered: List[Action] = []
    for a in acts:
        key = (a.kind, a.card_uid, a.face_uid if a.face_uid is not None else a.card_uid, a.ocean_uid)
        if (not a.use_star) and key in star_keys:
            continue
        filtered.append(a)
    return expand_draw_actions_for_ai(gs, ms, player, filtered)


def simulated_point_delta(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return 0.0
    try:
        player_index = next(i for i, p in enumerate(gs.players) if p is player)
    except StopIteration:
        return 0.0

    gs2 = copy.deepcopy(gs)
    ms2 = copy.deepcopy(ms)
    p2 = gs2.players[player_index]
    before = final_points(gs2, p2)
    action_copy = copy.deepcopy(action)
    ok = apply_action(gs2, ms2, p2, action_copy, TurnState(), choose_payment_ai, verbose=False)
    if not ok:
        return -4.0
    after = final_points(gs2, p2)
    delta = float(after - before)
    if delta > 15.0:
        return 15.0
    if delta < -15.0:
        return -15.0
    return delta


def update_brain_from_match(gs: GameState, brain: Dict[str, object]) -> None:
    synergy_map = brain.get("synergy")
    species_map = brain.get("species_synergy")
    same_ocean_map = brain.get("same_ocean_synergy")
    weights = brain.get("weights")
    if (
        not isinstance(synergy_map, dict)
        or not isinstance(species_map, dict)
        or not isinstance(same_ocean_map, dict)
        or not isinstance(weights, dict)
    ):
        return

    scores = {p.name: final_points(gs, p) for p in gs.players}
    ranked = sorted(gs.players, key=lambda p: scores[p.name], reverse=True)
    if not ranked:
        return

    top_score = scores[ranked[0].name]
    winners = [p for p in ranked if scores[p.name] == top_score]
    losers = [p for p in ranked if scores[p.name] < top_score]

    # Learn card combinations from winners and dampen losing combinations.
    # Rates are 2-3× higher than self-play to learn fast from real human games.
    for p in winners:
        names = [gs.card_db[uid].name for uid in player_board_face_uids(p)]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                k = synergy_key(names[i], names[j])
                synergy_map[k] = float(synergy_map.get(k, 0.0) + 0.14)

    for p in losers:
        names = [gs.card_db[uid].name for uid in player_board_face_uids(p)]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                k = synergy_key(names[i], names[j])
                synergy_map[k] = float(synergy_map.get(k, 0.0) - 0.04)

    # Learn species combinations from winners and dampen loser species mixes.
    for p in winners:
        species = [gs.card_db[uid].species for uid in player_board_face_uids(p)]
        species = [s for s in species if s.strip() and s.strip().lower() != "n/a"]
        for i in range(len(species)):
            for j in range(i + 1, len(species)):
                k = species_synergy_key(species[i], species[j])
                species_map[k] = float(species_map.get(k, 0.0) + 0.10)

    for p in losers:
        species = [gs.card_db[uid].species for uid in player_board_face_uids(p)]
        species = [s for s in species if s.strip() and s.strip().lower() != "n/a"]
        for i in range(len(species)):
            for j in range(i + 1, len(species)):
                k = species_synergy_key(species[i], species[j])
                species_map[k] = float(species_map.get(k, 0.0) - 0.03)

    # Learn same-ocean card combinations more strongly (highest-signal feature).
    for p in winners:
        for ocean_uid in p.board_oceans:
            local = [gs.card_db[uid].name for uid in p.ocean_slots[ocean_uid].all_cards()]
            for i in range(len(local)):
                for j in range(i + 1, len(local)):
                    k = synergy_key(local[i], local[j])
                    same_ocean_map[k] = float(same_ocean_map.get(k, 0.0) + 0.20)

    for p in losers:
        for ocean_uid in p.board_oceans:
            local = [gs.card_db[uid].name for uid in p.ocean_slots[ocean_uid].all_cards()]
            for i in range(len(local)):
                for j in range(i + 1, len(local)):
                    k = synergy_key(local[i], local[j])
                    same_ocean_map[k] = float(same_ocean_map.get(k, 0.0) - 0.05)

    # Keep map bounded in size and magnitude.
    for k in list(synergy_map.keys()):
        v = float(synergy_map[k])
        if abs(v) < 1e-6:
            del synergy_map[k]
            continue
        if v > 3.0:
            synergy_map[k] = 3.0
        elif v < -3.0:
            synergy_map[k] = -3.0

    if len(synergy_map) > 6000:
        top = sorted(synergy_map.items(), key=lambda kv: abs(kv[1]), reverse=True)[:6000]
        synergy_map.clear()
        synergy_map.update(top)

    for k in list(species_map.keys()):
        v = float(species_map[k])
        if abs(v) < 1e-6:
            del species_map[k]
            continue
        if v > 3.0:
            species_map[k] = 3.0
        elif v < -3.0:
            species_map[k] = -3.0

    if len(species_map) > 800:
        top_species = sorted(species_map.items(), key=lambda kv: abs(kv[1]), reverse=True)[:800]
        species_map.clear()
        species_map.update(top_species)

    for k in list(same_ocean_map.keys()):
        v = float(same_ocean_map[k])
        if abs(v) < 1e-6:
            del same_ocean_map[k]
            continue
        if v > 4.0:
            same_ocean_map[k] = 4.0
        elif v < -4.0:
            same_ocean_map[k] = -4.0

    if len(same_ocean_map) > 1600:
        top_local = sorted(same_ocean_map.items(), key=lambda kv: abs(kv[1]), reverse=True)[:1600]
        same_ocean_map.clear()
        same_ocean_map.update(top_local)

    # Small global weight nudges based on winner behavior.
    winner = winners[0]
    winner_cards = [gs.card_db[uid] for uid in player_board_face_uids(winner)]
    if winner_cards:
        ocean_ratio = sum(1 for c in winner_cards if is_ocean(c)) / len(winner_cards)
        avg_cost = sum(c.cost for c in winner_cards) / len(winner_cards)
        plus_ratio = sum(1 for c in winner_cards if "+" in c.text) / len(winner_cards)
        empty_ratio = count_empty_oceans(winner) / max(1, len(winner.board_oceans))
        # Target a lower ocean share so winners are mostly creatures, not empty oceans.
        weights["is_ocean"] = float(weights.get("is_ocean", 0.0) * 0.99 + (ocean_ratio - 0.32) * 0.02)
        weights["card_cost"] = float(weights.get("card_cost", 0.0) * 0.99 - avg_cost * 0.005)
        weights["has_plus"] = float(weights.get("has_plus", 0.0) * 0.99 + plus_ratio * 0.02)
        weights["synergy_bonus"] = float(weights.get("synergy_bonus", 0.0) * 0.995 + 0.005)
        weights["species_bonus"] = float(weights.get("species_bonus", 0.0) * 0.995 + 0.005)
        weights["same_ocean_bonus"] = float(weights.get("same_ocean_bonus", 0.0) * 0.995 + 0.005)
        weights["stack_bonus"] = float(weights.get("stack_bonus", 0.0) * 0.995 + 0.004)
        weights["plan_fit_bonus"] = float(weights.get("plan_fit_bonus", 0.0) * 0.995 + 0.005)
        weights["deny_bonus"] = float(weights.get("deny_bonus", 0.0) * 0.995 + 0.003)
        weights["overbuild_ocean_penalty"] = float(
            weights.get("overbuild_ocean_penalty", 0.0) * 0.995 - (0.25 - empty_ratio) * 0.02
        )
        unique_names = len({c.name for c in winner_cards}) / len(winner_cards)
        weights["strategy_bonus"] = float(weights.get("strategy_bonus", 0.0) * 0.995 + 0.004)
        weights["novelty_bonus"] = float(weights.get("novelty_bonus", 0.0) * 0.995 + unique_names * 0.002)
        weights["branch_bonus"] = float(weights.get("branch_bonus", 0.0) * 0.995 + 0.003)
        weights["sim_point_delta"] = float(weights.get("sim_point_delta", 0.0) * 0.995 + 0.005)

    stabilize_weights(weights)
    ensure_priority_anchor_brain_rules(brain)
    brain["games_played"] = int(brain.get("games_played", 0)) + 1


PAYMENT_HEAVY_HITTER_NAMES = {
    "whale shark",
    "emperor penguin",
    "reef trigger fish",
    "reef triggerfish",
    "staghorn coral",
    "california seagull",
    "sea anemone",
    "great white shark",
    "great shark",
    "blue tang",
    "king salmon",
    "yellowfin tuna",
    "big eye tuna",
    "great albatross",
    "roosterfish",
    "mandarin goby",
    "spiny lobster",
    "manta ray",
    "loggerhead sea turtle",
}

PAYMENT_ENGINE_KEEPER_NAMES = {
    "cleaner wrasse",
    "tarpon",
    "hermit crab",
    "sail fish",
    "sailfish",
    "bottlenose dolphin",
    "peruvian pelican",
}


def entry_keep_priority_for_strategy(ms: MatchState, gs: GameState, player: PlayerState, entry_uid: int) -> float:
    board_profile = board_strategy_profile(gs, player)
    has_memory = isinstance(player.flags.get("_visible_memory"), dict)
    best = 0.0

    for face_uid in entry_faces(ms, entry_uid):
        c = gs.card_db[face_uid]
        name = c.name.strip().lower()
        text = c.text.lower()
        keep = 0.0

        if name in PAYMENT_HEAVY_HITTER_NAMES:
            keep += 2.4
        if name in PAYMENT_ENGINE_KEEPER_NAMES:
            keep += 1.6
        if "play again" in text or "go again" in text:
            keep += 1.4
        if "for free" in text:
            keep += 1.0
        if "draw" in text:
            keep += 0.7
        if has_star_ability(c):
            keep += 0.4

        for t in card_strategy_tags(c):
            keep += 0.22 * min(board_profile.get(t, 0), 5)

        if has_memory:
            tier = scarcity_tier_for_name(player, name)
            if tier == "CRITICAL":
                keep += 1.2
            elif tier == "RARE":
                keep += 0.7
            elif tier == "LIMITED":
                keep += 0.3

        if keep > best:
            best = keep

    return best


def sort_hand_for_payment(
    gs: GameState,
    ms: MatchState,
    hand_uids: List[int],
    player: Optional[PlayerState] = None,
) -> List[int]:
    def face_score(face_uid: int) -> float:
        c = gs.card_db[face_uid]
        plus = sum(int(m.group(1)) for m in re.finditer(r"\+(\d+)", c.text))
        return c.cost * 2 + plus + (2 if is_ocean(c) else 0)

    def pay_score(entry_uid: int) -> float:
        base = min(face_score(f) for f in entry_faces(ms, entry_uid))
        if player is None:
            return base
        # Higher keep priority means less expendable for payment/discard.
        keep = entry_keep_priority_for_strategy(ms, gs, player, entry_uid)
        return base + (2.8 * keep)

    return sorted(hand_uids, key=pay_score)


def add_to_pool(ms: MatchState, uid: int) -> None:
    ms.pool.append(uid)
    if len(ms.pool) == 10:
        ms.discard_pile.extend(ms.pool)
        ms.pool.clear()


def share_stack_key(card: CardDef) -> Optional[str]:
    name = card.name.strip().lower()
    if name == "lobster":
        return "lobster"
    if name == "yellowfin tuna":
        return "yellowfin tuna"
    return None


def can_share_slot(card: CardDef) -> bool:
    return share_stack_key(card) is not None


def can_attach_to_ocean(gs: GameState, player: PlayerState, card_uid: int, ocean_uid: int) -> bool:
    if ocean_uid not in player.ocean_slots:
        return False
    card = gs.card_db.get(card_uid)
    if card is None:
        return False
    direction = card.direction.strip().lower()
    if direction not in {"up", "down", "left", "right"}:
        return False
    slot_cards = player.ocean_slots[ocean_uid].slot(direction)
    if not slot_cards:
        return True
    new_key = share_stack_key(card)
    if new_key is None:
        return False
    # Stacking is only legal on the same stackable card family.
    for uid in slot_cards:
        existing = gs.card_db.get(uid)
        if existing is None:
            return False
        if share_stack_key(existing) != new_key:
            return False
    return True


def locate_face_on_board(player: PlayerState, face_uid: int) -> Optional[Tuple[int, str, int]]:
    for ocean_uid in player.board_oceans:
        slots = player.ocean_slots.get(ocean_uid)
        if not slots:
            continue
        for direction in ("up", "down", "left", "right"):
            lane = slots.slot(direction)
            for idx, uid in enumerate(lane):
                if uid == face_uid:
                    return ocean_uid, direction, idx
    return None


def can_move_face_to_ocean(
    gs: GameState,
    player: PlayerState,
    face_uid: int,
    target_ocean_uid: int,
    source_ocean_uid: Optional[int] = None,
) -> bool:
    card = gs.card_db.get(face_uid)
    if card is None or is_ocean(card):
        return False
    loc = locate_face_on_board(player, face_uid)
    if loc is None:
        return False
    found_source_ocean_uid, found_direction, _ = loc
    if source_ocean_uid is not None and int(found_source_ocean_uid) != int(source_ocean_uid):
        return False
    if int(target_ocean_uid) == int(found_source_ocean_uid):
        return False
    # Direction must remain the same after moving.
    if normalize_direction(card.direction) != found_direction:
        return False
    return can_attach_to_ocean(gs, player, face_uid, target_ocean_uid)


def _sanitize_int_uid_list(items: Any, valid_uids: set[int], dedupe: bool = False) -> Tuple[List[int], int]:
    cleaned: List[int] = []
    dropped = 0
    seen: set[int] = set()
    if not isinstance(items, list):
        return cleaned, dropped
    for raw in items:
        if not isinstance(raw, int):
            dropped += 1
            continue
        uid = int(raw)
        if uid not in valid_uids:
            dropped += 1
            continue
        if dedupe and uid in seen:
            dropped += 1
            continue
        seen.add(uid)
        cleaned.append(uid)
    return cleaned, dropped


def sanitize_runtime_state(
    gs: GameState,
    ms: MatchState,
    action_policies: Optional[List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]]] = None,
    max_notes: int = 24,
) -> List[str]:
    """
    Repair obvious runtime state corruption in-place.
    This is intentionally conservative: it only removes invalid references and
    normalizes container shapes so the engine can continue safely.
    """
    notes: List[str] = []
    valid_uids = set(gs.card_db.keys())
    if not valid_uids:
        notes.append("card_db is empty; cannot sanitize runtime state")
        return notes[:max(0, int(max_notes))]

    if not isinstance(gs.players, list):
        gs.players = []
        notes.append("players container was invalid and reset")
    if not gs.players:
        notes.append("no players available")
        return notes[:max(0, int(max_notes))]

    if not isinstance(gs.turn_index, int):
        old = gs.turn_index
        gs.turn_index = 0
        notes.append(f"turn_index type invalid ({old!r}); reset to 0")
    if gs.turn_index < 0 or gs.turn_index >= len(gs.players):
        old = gs.turn_index
        gs.turn_index = 0
        notes.append(f"turn_index out of range ({old}); reset to 0")

    if action_policies is not None and len(action_policies) < len(gs.players):
        notes.append(
            f"policy count ({len(action_policies)}) below player count ({len(gs.players)}); modulo fallback will be used"
        )

    pool_clean, pool_drop = _sanitize_int_uid_list(ms.pool, valid_uids, dedupe=False)
    if pool_drop:
        ms.pool = pool_clean
        notes.append(f"removed {pool_drop} invalid pool entries")
    elif not isinstance(ms.pool, list):
        ms.pool = []
        notes.append("pool container was invalid and reset")

    discard_clean, discard_drop = _sanitize_int_uid_list(ms.discard_pile, valid_uids, dedupe=False)
    if discard_drop:
        ms.discard_pile = discard_clean
        notes.append(f"removed {discard_drop} invalid discard entries")
    elif not isinstance(ms.discard_pile, list):
        ms.discard_pile = []
        notes.append("discard_pile container was invalid and reset")

    if ms.end_game_uid is not None and ms.end_game_uid not in valid_uids:
        notes.append(f"cleared invalid end_game_uid {ms.end_game_uid}")
        ms.end_game_uid = None

    # Keep pair maps consistent so entry_faces() cannot emit invalid face uids.
    if not isinstance(ms.pair_primary_to_faces, dict):
        ms.pair_primary_to_faces = {}
        notes.append("pair_primary_to_faces container reset")
    if not isinstance(ms.face_to_primary, dict):
        ms.face_to_primary = {}
        notes.append("face_to_primary container reset")
    cleaned_pairs: Dict[int, Tuple[int, int]] = {}
    cleaned_face_to_primary: Dict[int, int] = {}
    bad_pair_entries = 0
    for raw_primary, raw_pair in list(ms.pair_primary_to_faces.items()):
        if not isinstance(raw_primary, int):
            bad_pair_entries += 1
            continue
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
            bad_pair_entries += 1
            continue
        a, b = raw_pair[0], raw_pair[1]
        if not isinstance(a, int) or not isinstance(b, int):
            bad_pair_entries += 1
            continue
        if raw_primary not in valid_uids or a not in valid_uids or b not in valid_uids:
            bad_pair_entries += 1
            continue
        cleaned_pairs[int(raw_primary)] = (int(a), int(b))
        cleaned_face_to_primary[int(a)] = int(raw_primary)
        cleaned_face_to_primary[int(b)] = int(raw_primary)
    if bad_pair_entries:
        notes.append(f"removed {bad_pair_entries} invalid pair-map entries")
    ms.pair_primary_to_faces = cleaned_pairs
    ms.face_to_primary = cleaned_face_to_primary

    for idx, player in enumerate(gs.players):
        if not isinstance(player.flags, dict):
            player.flags = {}
            notes.append(f"player {idx + 1} flags were invalid and reset")

        hand_clean, hand_drop = _sanitize_int_uid_list(player.hand, valid_uids, dedupe=True)
        if hand_drop:
            player.hand = hand_clean
            notes.append(f"player {idx + 1}: removed {hand_drop} invalid hand entries")
        elif not isinstance(player.hand, list):
            player.hand = []
            notes.append(f"player {idx + 1}: hand container reset")

        if not isinstance(player.board_oceans, list):
            player.board_oceans = []
            notes.append(f"player {idx + 1}: board_oceans container reset")

        board_clean: List[int] = []
        seen_oceans: set[int] = set()
        dropped_oceans = 0
        for raw in player.board_oceans:
            if not isinstance(raw, int):
                dropped_oceans += 1
                continue
            ocean_uid = int(raw)
            ocean_card = gs.card_db.get(ocean_uid)
            if ocean_card is None or (not is_ocean(ocean_card)):
                dropped_oceans += 1
                continue
            if ocean_uid in seen_oceans:
                dropped_oceans += 1
                continue
            seen_oceans.add(ocean_uid)
            board_clean.append(ocean_uid)
        if dropped_oceans:
            notes.append(f"player {idx + 1}: removed {dropped_oceans} invalid board oceans")
        player.board_oceans = board_clean

        if not isinstance(player.ocean_slots, dict):
            player.ocean_slots = {}
            notes.append(f"player {idx + 1}: ocean_slots container reset")

        slot_keys = list(player.ocean_slots.keys())
        for key in slot_keys:
            if key not in seen_oceans:
                del player.ocean_slots[key]
                notes.append(f"player {idx + 1}: removed dangling slot map for ocean {key}")

        for ocean_uid in player.board_oceans:
            slots_obj = player.ocean_slots.get(ocean_uid)
            if not isinstance(slots_obj, OceanSlots):
                slots_obj = OceanSlots()
                player.ocean_slots[ocean_uid] = slots_obj
                notes.append(f"player {idx + 1}: rebuilt slots for ocean {ocean_uid}")

            for direction in ("up", "down", "left", "right"):
                lane_raw = getattr(slots_obj, direction, None)
                if not isinstance(lane_raw, list):
                    setattr(slots_obj, direction, [])
                    notes.append(f"player {idx + 1}: reset non-list lane {direction} on ocean {ocean_uid}")
                    continue

                lane_clean: List[int] = []
                lane_seen: set[int] = set()
                lane_drop = 0
                for raw in lane_raw:
                    if not isinstance(raw, int):
                        lane_drop += 1
                        continue
                    face_uid = int(raw)
                    card = gs.card_db.get(face_uid)
                    if card is None or is_ocean(card):
                        lane_drop += 1
                        continue
                    if face_uid in lane_seen:
                        lane_drop += 1
                        continue
                    lane_seen.add(face_uid)
                    lane_clean.append(face_uid)
                if lane_drop:
                    notes.append(
                        f"player {idx + 1}: removed {lane_drop} invalid cards from {direction} lane on ocean {ocean_uid}"
                    )
                if lane_clean != lane_raw:
                    setattr(slots_obj, direction, lane_clean)

    # Global ownership dedupe: one card entry cannot exist in multiple zones.
    claimed_entries: set[int] = set()

    def _dedupe_zone(items: List[int], label: str) -> Tuple[List[int], int]:
        out: List[int] = []
        dropped = 0
        for raw in items:
            if not isinstance(raw, int):
                dropped += 1
                continue
            uid = int(raw)
            entry_uid = canonical_entry_uid(ms, uid)
            if entry_uid not in valid_uids:
                dropped += 1
                continue
            if entry_uid in claimed_entries:
                dropped += 1
                continue
            claimed_entries.add(entry_uid)
            out.append(uid)
        if dropped:
            notes.append(f"{label}: removed {dropped} duplicate/invalid card reference(s)")
        return out, dropped

    # Priority order: board commitments first, then hand, then shared zones.
    for idx, player in enumerate(gs.players):
        old_board = list(player.board_oceans)
        deduped_board, _ = _dedupe_zone(old_board, f"player {idx + 1} board_oceans")
        if deduped_board != old_board:
            player.board_oceans = deduped_board
            keep = set(deduped_board)
            for key in list(player.ocean_slots.keys()):
                if key not in keep:
                    del player.ocean_slots[key]

        for ocean_uid in list(player.board_oceans):
            slots_obj = player.ocean_slots.get(ocean_uid)
            if not isinstance(slots_obj, OceanSlots):
                continue
            for direction in ("up", "down", "left", "right"):
                lane_raw = list(slots_obj.slot(direction))
                lane_clean, _ = _dedupe_zone(
                    lane_raw,
                    f"player {idx + 1} ocean {ocean_uid} {direction} lane",
                )
                if lane_clean != lane_raw:
                    setattr(slots_obj, direction, lane_clean)

        hand_raw = list(player.hand)
        hand_clean, _ = _dedupe_zone(hand_raw, f"player {idx + 1} hand")
        if hand_clean != hand_raw:
            player.hand = hand_clean

    pool_raw = list(ms.pool)
    pool_clean, _ = _dedupe_zone(pool_raw, "pool")
    if pool_clean != pool_raw:
        ms.pool = pool_clean

    deck_raw = list(gs.deck)
    deck_clean, _ = _dedupe_zone(deck_raw, "deck")
    if deck_clean != deck_raw:
        gs.deck = deck_clean

    discard_raw = list(ms.discard_pile)
    discard_clean, _ = _dedupe_zone(discard_raw, "discard_pile")
    if discard_clean != discard_raw:
        ms.discard_pile = discard_clean

    limit = max(0, int(max_notes))
    return notes[:limit] if limit > 0 else []


def free_flag_for_card(card: CardDef) -> Optional[str]:
    species = card.species.strip().lower()
    if species == "mammal":
        return "free_mammal"
    if species == "baitfish":
        return "free_baitfish"
    if species == "game fish":
        return "free_game_fish"
    if species == "cephalopod":
        return "free_cephalopods"
    if species == "crustacean":
        return "free_crustacean"
    if species == "invertebrate":
        return "free_invertebrate"
    if species == "coral":
        return "free_coral"
    return None


def consume_free_flag_if_applicable(player: PlayerState, card: CardDef) -> bool:
    if card.name.strip().lower() == "yellowfin tuna" and int(player.flags.get("free_yellowfin_tuna", 0)) > 0:
        player.flags["free_yellowfin_tuna"] = int(player.flags.get("free_yellowfin_tuna", 0)) - 1
        return True

    if player.flags.get("free_baitfish_chain", False) and card.species.strip().lower() == "baitfish":
        return True

    key = free_flag_for_card(card)
    if not key:
        return False

    # Single-use free cephalopod (Grooved Brain Coral star) — consumed after one play.
    if player.flags.get("free_cephalopod_once", False) and card.species.strip().lower() == "cephalopod":
        player.flags["free_cephalopod_once"] = False
        return True

    if key == "free_cephalopods":
        # Unlimited free cephalopods (Reef Trigger Fish star) — never consumed.
        return bool(player.flags.get(key, False))

    if player.flags.get(key, False):
        player.flags[key] = False
        return True

    return False


def clownfish_ocean_value(ocean_name: str) -> float:
    """Relative value of cloning the attached ocean text with Clownfish."""
    name = (ocean_name or "").strip().lower()
    if name == "artificial reef":
        return 3.2
    if name == "coral reef":
        return 2.4
    if name == "mangrove":
        return 1.8
    if name == "kelp forest":
        return 1.3
    if name == "tide pool":
        return 0.7
    if name == "pier":
        return 0.2
    if name == "arctic oceans":
        return -0.9
    if name == "deep ocean":
        return -1.6
    return 0.6


def has_playable_followup_species(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    species_key: str,
    exclude_entry_uid: Optional[int] = None,
) -> bool:
    target = species_key.strip().lower()
    if not player.board_oceans:
        return False

    for entry_uid in player.hand:
        if exclude_entry_uid is not None and entry_uid == exclude_entry_uid:
            continue
        for face_uid in entry_faces(ms, entry_uid):
            c = gs.card_db[face_uid]
            if is_ocean(c):
                continue
            if c.species.strip().lower() != target:
                continue
            if any(can_attach_to_ocean(gs, player, face_uid, ocean_uid) for ocean_uid in player.board_oceans):
                return True
    return False


def star_has_immediate_value(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    card: CardDef,
    played_entry_uid: Optional[int] = None,
) -> bool:
    _, star = split_main_and_star(card.text)
    t = star.lower().strip()
    if not t:
        t = card.text.lower()

    # STARs that always provide value immediately.
    if any(k in t for k in ("draw one", "draw two", "draw 2", "draw three", "play again", "go again")):
        return True

    if "play a free baitfish" in t:
        return has_playable_followup_species(gs, ms, player, "baitfish", exclude_entry_uid=played_entry_uid)
    if "play a free mammal" in t:
        return has_playable_followup_species(gs, ms, player, "mammal", exclude_entry_uid=played_entry_uid)
    if "play a free game fish" in t:
        return has_playable_followup_species(gs, ms, player, "game fish", exclude_entry_uid=played_entry_uid)
    if "play any number of cephalopods for free" in t:
        return has_playable_followup_species(gs, ms, player, "cephalopod", exclude_entry_uid=played_entry_uid)
    if "free crustacean" in t:
        return has_playable_followup_species(gs, ms, player, "crustacean", exclude_entry_uid=played_entry_uid)
    if "free invertebrate" in t:
        return has_playable_followup_species(gs, ms, player, "invertebrate", exclude_entry_uid=played_entry_uid)
    if "play a free coral" in t:
        return has_playable_followup_species(gs, ms, player, "coral", exclude_entry_uid=played_entry_uid)

    return True


def star_followup_species_targets(card: CardDef) -> List[str]:
    _, star = split_main_and_star(card.text)
    t = star.lower().strip()
    if not t:
        t = card.text.lower()

    targets: List[str] = []
    if "play a free baitfish" in t:
        targets.append("baitfish")
    if "play a free mammal" in t:
        targets.append("mammal")
    if "play a free game fish" in t:
        targets.append("game fish")
    if "play any number of cephalopods for free" in t:
        targets.append("cephalopod")
    if "free crustacean" in t:
        targets.append("crustacean")
    if "free invertebrate" in t:
        targets.append("invertebrate")
    if "play a free coral" in t:
        targets.append("coral")
    return targets


def star_prep_needs(gs: GameState, ms: MatchState, player: PlayerState) -> Tuple[set, set]:
    need_symbols: set = set()
    need_species: set = set()

    for entry_uid in player.hand:
        for face_uid in entry_faces(ms, entry_uid):
            card = gs.card_db[face_uid]
            if is_ocean(card):
                continue
            if not has_star_ability(card):
                continue

            # Symbol need for paying STAR.
            if card.cost > 0:
                sym = normalize_symbol(card.symbol)
                if sym not in {"", "n/a"}:
                    has_match = any(
                        uid2 != entry_uid and symbol_match_for_entry(ms, gs, uid2, sym)
                        for uid2 in player.hand
                    )
                    if not has_match:
                        need_symbols.add(sym)

            # Follow-up species need for STAR free-play effects.
            for species in star_followup_species_targets(card):
                if not has_playable_followup_species(gs, ms, player, species, exclude_entry_uid=entry_uid):
                    need_species.add(species)

    return need_symbols, need_species


def has_star_option_for_action(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> bool:
    if action.kind not in {"play_ocean", "play_to_ocean"}:
        return False
    if action.use_star:
        return False
    face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face_uid)
    if card is None:
        return False
    if not can_potentially_use_star(gs, ms, player, action.card_uid, face_uid):
        return False
    if not can_afford_play(gs, ms, player, action.card_uid, face_uid, use_star=True):
        return False
    return star_has_immediate_value(gs, ms, player, card, played_entry_uid=action.card_uid)


def can_potentially_use_star(gs: GameState, ms: MatchState, player: PlayerState, played_entry_uid: int, play_face_uid: int) -> bool:
    card = gs.card_db[play_face_uid]
    if not has_star_ability(card):
        return False
    if card.cost <= 0:
        return False

    card_sym = normalize_symbol(card.symbol)
    if not card_sym or card_sym in {"n/a", ""}:
        return False

    for uid in player.hand:
        if uid == played_entry_uid:
            continue
        if symbol_match_for_entry(ms, gs, uid, card_sym):
            return star_has_immediate_value(gs, ms, player, card, played_entry_uid=played_entry_uid)
    return False


def can_afford_play(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    played_entry_uid: int,
    play_face_uid: int,
    use_star: bool,
) -> bool:
    card = gs.card_db[play_face_uid]
    free_play = is_free_play_eligible(player, card)
    cost_to_pay = 0 if free_play else max(0, card.cost)

    candidates = [uid for uid in player.hand if uid != played_entry_uid]
    if len(candidates) < cost_to_pay:
        return False

    if not use_star:
        return True

    if not has_star_ability(card):
        return False
    if cost_to_pay <= 0:
        return False

    sym = normalize_symbol(card.symbol)
    if sym in {"", "n/a"}:
        return False
    return any(symbol_match_for_entry(ms, gs, uid, sym) for uid in candidates)


def build_deck_with_late_end_game(
    card_db: Dict[int, CardDef],
    pair_primary_to_faces: Dict[int, Tuple[int, int]],
    face_to_primary: Dict[int, int],
    rng: random.Random,
) -> Tuple[List[int], Optional[int]]:
    end_uids = [uid for uid, c in card_db.items() if c.name.strip().lower() == "end game"]
    end_uid = end_uids[0] if end_uids else None

    deck_entries: List[int] = []
    for uid in sorted(card_db.keys()):
        if uid in face_to_primary and face_to_primary[uid] != uid:
            continue
        deck_entries.append(uid)

    if end_uid is None:
        deck = list(deck_entries)
        rng.shuffle(deck)
        return deck, None

    end_entry = face_to_primary.get(end_uid, end_uid)
    non_end = [uid for uid in deck_entries if uid != end_entry]
    rng.shuffle(non_end)

    # Place END GAME randomly within the bottom 10 positions.
    bottom_non_end_count = min(9, len(non_end))
    top = non_end[:-bottom_non_end_count] if bottom_non_end_count else non_end
    bottom_group = non_end[-bottom_non_end_count:] if bottom_non_end_count else []
    rng.shuffle(bottom_group)
    insert_pos = rng.randint(0, len(bottom_group))
    bottom_group.insert(insert_pos, end_entry)

    deck = top + bottom_group
    return deck, end_uid


def draw_from_deck(gs: GameState, ms: MatchState, player: PlayerState, n: int) -> List[int]:
    drew: List[int] = []
    while len(drew) < n:
        if not gs.deck:
            break
        uid = gs.deck.pop(0)
        if ms.end_game_uid is not None and uid == ms.end_game_uid:
            trigger_end_game(ms, gs)
            ms.discard_pile.append(uid)
            # Draw replacement card for this same draw count.
            continue
        player.hand.append(uid)
        drew.append(uid)
    return drew


def draw_from_pool(ms: MatchState, player: PlayerState, n: int, gs: Optional["GameState"] = None) -> List[int]:
    drew: List[int] = []
    while len(drew) < n:
        if not ms.pool:
            break
        uid = ms.pool.pop()  # top of pool = most recent
        if gs is not None and ms.end_game_uid is not None and uid == ms.end_game_uid:
            trigger_end_game(ms, gs)
            ms.discard_pile.append(uid)
            continue  # draw a replacement card
        player.hand.append(uid)
        drew.append(uid)
    return drew


def draw_selected_from_pool(ms: MatchState, player: PlayerState, pick_uids: List[int], gs: Optional["GameState"] = None) -> List[int]:
    if len(set(pick_uids)) != len(pick_uids):
        return []
    if any(uid not in ms.pool for uid in pick_uids):
        return []
    drew: List[int] = []
    for uid in pick_uids:
        ms.pool.remove(uid)
        if gs is not None and ms.end_game_uid is not None and uid == ms.end_game_uid:
            trigger_end_game(ms, gs)
            ms.discard_pile.append(uid)
            continue
        player.hand.append(uid)
        drew.append(uid)
    return drew


def perform_mulligans(gs: GameState, ms: MatchState) -> None:
    for p in gs.players:
        if any(entry_is_ocean(ms, gs, uid) for uid in p.hand):
            continue

        old = list(p.hand)
        p.hand.clear()
        for uid in old:
            ms.discard_pile.append(uid)
        draw_from_deck(gs, ms, p, 8)


def legal_actions(gs: GameState, ms: MatchState, player: PlayerState, include_draw: bool = True) -> List[Action]:
    # Tarpon discard phase: player chooses any cards to discard (0 or more), then ends.
    if player.flags.get("_tarpon_discard_active"):
        actions = [Action(kind="discard_to_pool", card_uid=uid) for uid in list(player.hand)]
        actions.append(Action(kind="end_turn"))  # "Done discarding"
        return actions

    # End-of-turn discard phase: only discard actions are legal until hand is within the limit.
    if player.flags.get("_discard_mode") and len(player.hand) > HAND_LIMIT:
        # Batch action for web human multi-select UI.  pool_pick_uids is intentionally
        # empty here — the client populates it with the player's actual selection before
        # submitting.  Individual discard_to_pool actions are kept as a fallback for AI
        # and for single-card interactive removal.
        return [
            Action(kind="discard_batch_to_pool", pool_pick_uids=[]),
            *[Action(kind="discard_to_pool", card_uid=uid) for uid in list(player.hand)],
        ]

    actions: List[Action] = []
    free_only = bool(player.flags.get("_free_action_only", False))
    multi_paid = bool(player.flags.get("multi_play_paid_turn", False))
    multi_baitfish = bool(player.flags.get("free_baitfish_chain", False))
    multi_cephalopods = bool(player.flags.get("free_cephalopods", False)) or bool(player.flags.get("free_cephalopod_once", False))
    multi_yellowfin = int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    has_manual_end_window = free_only or multi_paid or multi_baitfish or multi_cephalopods or multi_yellowfin
    if has_manual_end_window:
        include_draw = False

    if include_draw:
        if len(gs.deck) >= 2:
            actions.append(Action(kind="draw", draw_from_pool=0))
        if len(gs.deck) >= 1 and len(ms.pool) >= 1:
            actions.append(Action(kind="draw", draw_from_pool=1))
        if len(ms.pool) >= 2:
            actions.append(Action(kind="draw", draw_from_pool=2))
        # During the final round, the player may end their turn without drawing.
        if ms.end_game_triggered:
            actions.append(Action(kind="end_turn"))

    for entry_uid in list(player.hand):
        faces = entry_faces(ms, entry_uid)
        first_face = faces[0] if faces else None
        first_face_card = gs.card_db.get(first_face) if isinstance(first_face, int) else None
        if len(faces) == 1 and first_face_card is not None and is_ocean(first_face_card):
            face_uid = faces[0]
            if free_only:
                # Restricted follow-up windows from STAR free-play abilities
                # cannot be used to play oceans.
                continue
            if can_afford_play(gs, ms, player, entry_uid, face_uid, use_star=False):
                actions.append(Action(kind="play_ocean", card_uid=entry_uid, face_uid=face_uid, use_star=False))
            if can_potentially_use_star(gs, ms, player, entry_uid, face_uid) and can_afford_play(
                gs, ms, player, entry_uid, face_uid, use_star=True
            ):
                actions.append(Action(kind="play_ocean", card_uid=entry_uid, face_uid=face_uid, use_star=True))
            continue

        if not player.board_oceans:
            continue

        for face_uid in faces:
            card = gs.card_db.get(face_uid)
            if card is None:
                continue
            if is_ocean(card):
                continue
            if multi_baitfish and card.species.strip().lower() != "baitfish":
                continue
            if multi_cephalopods and card.species.strip().lower() != "cephalopod":
                continue
            if free_only and not is_free_play_eligible(player, card):
                continue
            for ocean_uid in player.board_oceans:
                if not can_attach_to_ocean(gs, player, face_uid, ocean_uid):
                    continue
                if can_afford_play(gs, ms, player, entry_uid, face_uid, use_star=False):
                    actions.append(
                        Action(
                            kind="play_to_ocean",
                            card_uid=entry_uid,
                            face_uid=face_uid,
                            ocean_uid=ocean_uid,
                            use_star=False,
                        )
                    )
                if can_potentially_use_star(gs, ms, player, entry_uid, face_uid) and can_afford_play(
                    gs, ms, player, entry_uid, face_uid, use_star=True
                ):
                    actions.append(
                        Action(
                            kind="play_to_ocean",
                            card_uid=entry_uid,
                            face_uid=face_uid,
                            ocean_uid=ocean_uid,
                            use_star=True,
                        )
                    )

    # Optional once-per-turn utility action for human seats:
    # move one already-played non-ocean card to another ocean and end turn.
    if (
        bool(player.flags.get("_allow_relocate_action", False))
        and len(player.board_oceans) >= 2
        and not free_only
        and not multi_paid
        and not multi_baitfish
        and not multi_cephalopods
        and not multi_yellowfin
    ):
        for source_ocean_uid in player.board_oceans:
            slots = player.ocean_slots.get(source_ocean_uid)
            if not slots:
                continue
            for direction in ("up", "down", "left", "right"):
                lane = slots.slot(direction)
                for moved_face_uid in list(lane):
                    moved_card = gs.card_db.get(moved_face_uid)
                    if moved_card is None or is_ocean(moved_card):
                        continue
                    for target_ocean_uid in player.board_oceans:
                        if target_ocean_uid == source_ocean_uid:
                            continue
                        if can_move_face_to_ocean(
                            gs,
                            player,
                            moved_face_uid,
                            target_ocean_uid,
                            source_ocean_uid=source_ocean_uid,
                        ):
                            actions.append(
                                Action(
                                    kind="move_between_oceans",
                                    card_uid=moved_face_uid,
                                    face_uid=moved_face_uid,
                                    ocean_uid=target_ocean_uid,
                                    source_ocean_uid=source_ocean_uid,
                                )
                            )

    if has_manual_end_window:
        # Optional stop action so players can end a chain window early.
        actions.append(Action(kind="end_turn"))

    return actions


def describe_action(gs: GameState, ms: MatchState, action: Action) -> str:
    if action.kind == "end_turn":
        return "end turn"

    if action.kind == "draw":
        pick_txt = f" (pool picks: {','.join(str(x) for x in action.pool_pick_uids)})" if action.pool_pick_uids else ""
        if action.draw_from_pool == 0:
            return "draw 2 from deck"
        if action.draw_from_pool == 1:
            return f"draw 1 from pool + 1 from deck{pick_txt}"
        return f"draw 2 from pool{pick_txt}"

    if action.kind == "move_between_oceans":
        moved_uid = action.face_uid if action.face_uid is not None else action.card_uid
        moved = gs.card_db.get(moved_uid)
        moved_name = moved.name if moved is not None else str(moved_uid)
        src = action.source_ocean_uid if action.source_ocean_uid is not None else "?"
        dst = action.ocean_uid if action.ocean_uid is not None else "?"
        return f"move {moved_uid}:{moved_name} from ocean {src} to ocean {dst} (end turn)"

    face = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(face)
    if card is None:
        return f"{action.kind} unknown-card({face})"

    if action.kind == "play_ocean":
        text = f"play ocean {card.uid}:{card.name}"
    else:
        text = f"play {card.uid}:{card.name} to ocean {action.ocean_uid}"
    if action.use_star:
        text += " [STAR]"
    return text


def choose_payment_ai(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    played_entry_uid: int,
    play_face_uid: int,
    cost: int,
    require_symbol: bool,
) -> Optional[List[int]]:
    if cost <= 0:
        return []

    candidates = [uid for uid in player.hand if uid != played_entry_uid]
    if len(candidates) < cost:
        return None

    chosen: List[int] = []
    candidates_sorted = sort_hand_for_payment(gs, ms, candidates, player=player)

    if require_symbol:
        target_sym = normalize_symbol(gs.card_db[play_face_uid].symbol)
        match_uid = next((uid for uid in candidates_sorted if symbol_match_for_entry(ms, gs, uid, target_sym)), None)
        if match_uid is None:
            return None
        chosen.append(match_uid)
        candidates_sorted = [uid for uid in candidates_sorted if uid != match_uid]

    for uid in candidates_sorted:
        if len(chosen) >= cost:
            break
        chosen.append(uid)

    if len(chosen) != cost:
        return None
    return chosen


def choose_payment_human(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    played_entry_uid: int,
    play_face_uid: int,
    cost: int,
    require_symbol: bool,
) -> Optional[List[int]]:
    if cost <= 0:
        return []

    candidates = [uid for uid in player.hand if uid != played_entry_uid]
    if len(candidates) < cost:
        print("Not enough cards in hand to pay this cost.")
        return None

    target_sym = normalize_symbol(gs.card_db[play_face_uid].symbol)
    while True:
        print(f"Pay cost {cost}: choose {cost} uid(s) from your hand, excluding played card.")
        if require_symbol:
            print(f"At least one payment card must match symbol: {target_sym}")
        print("Candidates:")
        for uid in candidates:
            print("  " + entry_label(ms, gs, uid))

        raw = input("Payment uids (space-separated): ").strip()
        parts = [p for p in raw.split() if p]
        if len(parts) != cost:
            print("Wrong count.")
            continue

        try:
            picked = [int(p) for p in parts]
        except ValueError:
            print("Use numeric uids.")
            continue

        if len(set(picked)) != len(picked):
            print("Duplicate uid entered.")
            continue

        if any(uid not in candidates for uid in picked):
            print("One or more uids are invalid for payment.")
            continue

        if require_symbol:
            if not any(symbol_match_for_entry(ms, gs, uid, target_sym) for uid in picked):
                print("No matching symbol in payment cards.")
                continue

        return picked


def discard_down_to_ten_ai(gs: GameState, ms: MatchState, player: PlayerState) -> None:
    while len(player.hand) > 10:
        ordered = sort_hand_for_payment(gs, ms, list(player.hand), player=player)
        uid = ordered[0]
        player.hand.remove(uid)
        add_to_pool(ms, uid)


def discard_down_to_ten_human(gs: GameState, ms: MatchState, player: PlayerState) -> None:
    while len(player.hand) > 10:
        print(f"You have {len(player.hand)} cards; discard down to 10.")
        for uid in player.hand:
            print("  " + entry_label(ms, gs, uid))
        raw = input("Enter uid to discard: ").strip()
        if not raw.isdigit():
            print("Invalid uid.")
            continue
        uid = int(raw)
        if uid not in player.hand:
            print("Uid not in hand.")
            continue
        player.hand.remove(uid)
        add_to_pool(ms, uid)


def clear_turn_only_flags(player: PlayerState) -> None:
    for k in [
        "free_mammal",
        "free_baitfish",
        "free_baitfish_chain",
        "free_yellowfin_tuna",
        "free_game_fish",
        "free_cephalopods",
        "free_cephalopod_once",
        "free_crustacean",
        "free_invertebrate",
        "free_coral",
        "multi_play_paid_turn",
        "play_again",
        "go_again",
        "_free_action_only",
        "_discard_mode",
        "_tarpon_discard_active",
    ]:
        if k in player.flags:
            if k in {"free_yellowfin_tuna", "play_again", "go_again"}:
                player.flags[k] = 0
            else:
                player.flags[k] = False


def apply_action(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    turn_state: TurnState,
    payment_picker: Callable[[GameState, MatchState, PlayerState, int, int, int, bool], Optional[List[int]]],
    verbose: bool = False,
    fail_reason: Optional[List[str]] = None,
) -> bool:
    def fail(msg: str) -> bool:
        if fail_reason is not None:
            fail_reason.append(msg)
        return False

    is_human_turn = payment_picker is choose_payment_human

    if action.kind == "draw":
        pool_take = action.draw_from_pool
        deck_take = 2 - pool_take
        if pool_take < 0 or pool_take > 2:
            return fail(f"invalid draw split: pool_take={pool_take}")
        if len(ms.pool) < pool_take:
            return fail(f"not enough cards in pool: need {pool_take}, have {len(ms.pool)}")
        if len(gs.deck) < deck_take:
            return fail(f"not enough cards in deck: need {deck_take}, have {len(gs.deck)}")

        pool_cards: List[int] = []
        if pool_take > 0 and action.pool_pick_uids:
            if len(action.pool_pick_uids) != pool_take:
                return fail(f"wrong pool pick count: expected {pool_take}, got {len(action.pool_pick_uids)}")
            pool_cards = draw_selected_from_pool(ms, player, action.pool_pick_uids, gs)
            if len(pool_cards) != pool_take:
                return fail("invalid selected pool cards")
        else:
            pool_cards = draw_from_pool(ms, player, pool_take, gs)
        if pool_take > 0:
            action.pool_pick_uids = [int(uid) for uid in pool_cards]
        else:
            action.pool_pick_uids = []
        deck_cards = draw_from_deck(gs, ms, player, deck_take)
        if verbose:
            parts = []
            if pool_cards:
                parts.append("pool: " + ", ".join(entry_short_label(ms, gs, uid) for uid in pool_cards))
            if deck_cards:
                parts.append("deck: " + ", ".join(entry_short_label(ms, gs, uid) for uid in deck_cards))
            detail = " | ".join(parts) if parts else "no cards drawn"
            print(f"{player.name} draws 2 -> {detail}")
        turn_state.draws_this_turn += len(pool_cards) + len(deck_cards)
        return True

    if action.kind == "discard_to_pool":
        if action.card_uid not in player.hand:
            return fail(f"uid {action.card_uid} not in hand for discard")
        if player.flags.get("_discard_mode") and len(player.hand) - 1 < HAND_LIMIT:
            return fail(f"cannot discard below {HAND_LIMIT} cards during discard phase")
        player.hand.remove(action.card_uid)
        add_to_pool(ms, action.card_uid)
        if verbose:
            print(f"{player.name} discards {entry_short_label(ms, gs, action.card_uid)} to pool.")
        return True

    if action.kind == "discard_batch_to_pool":
        # Human selects multiple cards to discard at once (end-of-turn hand-limit phase).
        # pool_pick_uids holds the card UIDs chosen to move to the pool.
        chosen_uids = [uid for uid in action.pool_pick_uids if uid in player.hand]
        if not chosen_uids:
            return fail("discard_batch_to_pool: no valid cards selected")
        remaining = len(player.hand) - len(chosen_uids)
        if remaining > HAND_LIMIT:
            return fail(
                f"discard_batch_to_pool: not enough discarded — {remaining} would remain, limit is {HAND_LIMIT}"
            )
        if player.flags.get("_discard_mode") and remaining < HAND_LIMIT:
            return fail(
                f"discard_batch_to_pool: cannot discard below {HAND_LIMIT} cards during discard phase"
            )
        for uid in chosen_uids:
            player.hand.remove(uid)
            if ms is not None:
                add_to_pool(ms, uid)
            else:
                player.discard.append(uid)
        if verbose:
            labels = ", ".join(entry_short_label(ms, gs, uid) for uid in chosen_uids)
            print(f"{player.name} batch-discards {len(chosen_uids)} card(s) to pool: {labels}")
        trigger_board_symbol_star_draws(gs, ms, player, chosen_uids)
        return True

    if action.kind == "move_between_oceans":
        moved_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        moved_card = gs.card_db.get(moved_face_uid)
        if moved_card is None:
            return fail(f"unknown moved face uid {moved_face_uid}")
        if is_ocean(moved_card):
            return fail(f"cannot move ocean card {moved_face_uid}:{moved_card.name}")
        target_ocean_uid = action.ocean_uid
        if target_ocean_uid is None:
            return fail("move action missing target ocean")
        if target_ocean_uid not in player.ocean_slots:
            return fail(f"target ocean {target_ocean_uid} is not on your board")
        source_loc = locate_face_on_board(player, moved_face_uid)
        if source_loc is None:
            return fail(f"card {moved_face_uid}:{moved_card.name} is not on your board")
        source_ocean_uid, source_direction, source_index = source_loc
        if action.source_ocean_uid is not None and int(source_ocean_uid) != int(action.source_ocean_uid):
            return fail(
                f"source ocean mismatch for {moved_face_uid}:{moved_card.name} "
                f"(expected {action.source_ocean_uid}, found {source_ocean_uid})"
            )
        if int(source_ocean_uid) == int(target_ocean_uid):
            return fail("move target must be a different ocean")
        if not can_move_face_to_ocean(
            gs,
            player,
            moved_face_uid,
            target_ocean_uid,
            source_ocean_uid=source_ocean_uid,
        ):
            return fail(
                f"cannot move {moved_face_uid}:{moved_card.name} "
                f"from ocean {source_ocean_uid} to ocean {target_ocean_uid}"
            )
        src_slots = player.ocean_slots.get(source_ocean_uid)
        if not isinstance(src_slots, OceanSlots):
            return fail("source ocean slots missing before move resolve")
        src_lane = src_slots.slot(source_direction)
        if source_index < 0 or source_index >= len(src_lane) or src_lane[source_index] != moved_face_uid:
            return fail("source lane changed before move could resolve")
        src_lane.pop(source_index)
        dst_slots = player.ocean_slots.get(target_ocean_uid)
        if not isinstance(dst_slots, OceanSlots):
            return fail("target ocean slots missing before move resolve")
        dst_lane = dst_slots.slot(source_direction)
        dst_lane.append(moved_face_uid)
        turn_state.force_end_turn = True
        if verbose:
            print(
                f"{player.name} moves {moved_face_uid}:{moved_card.name} "
                f"from ocean {source_ocean_uid} to ocean {target_ocean_uid} on {source_direction} (turn ends)."
            )
        return True

    if action.kind == "end_turn":
        turn_state.force_end_turn = True
        return True

    if action.card_uid not in player.hand:
        return fail(f"card uid {action.card_uid} not in hand")

    play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    if play_face_uid not in entry_faces(ms, action.card_uid):
        return fail(f"face uid {play_face_uid} is not valid for card entry {action.card_uid}")
    card = gs.card_db.get(play_face_uid)
    if card is None:
        return fail(f"missing card data for uid {play_face_uid}")
    if action.kind == "play_to_ocean":
        if action.ocean_uid is None or not can_attach_to_ocean(gs, player, play_face_uid, action.ocean_uid):
            return fail(f"cannot attach {card.uid}:{card.name} to ocean {action.ocean_uid}")

    if action.kind == "play_ocean" and not is_ocean(card):
        return fail(f"attempted play_ocean with non-ocean card {card.uid}:{card.name}")

    if action.kind == "play_to_ocean" and is_ocean(card):
        return fail(f"attempted play_to_ocean with ocean card {card.uid}:{card.name}")

    # Optional free-play flags from star effects.
    free_play = consume_free_flag_if_applicable(player, card)
    cost_to_pay = 0 if free_play else max(0, card.cost)

    require_symbol = False
    auto_star = False
    if action.use_star:
        if not has_star_ability(card):
            return fail(f"card {card.uid}:{card.name} has no STAR ability")
        if cost_to_pay <= 0:
            return fail(f"card {card.uid}:{card.name} has no payable cost for STAR activation")
        sym = normalize_symbol(card.symbol)
        if sym in {"", "n/a"}:
            return fail(f"card {card.uid}:{card.name} has no valid symbol for STAR activation")
        require_symbol = True

    explicit_payments = [uid for uid in list(getattr(action, "payment_uids", [])) if isinstance(uid, int)]
    if explicit_payments:
        if len(explicit_payments) != cost_to_pay:
            return fail(f"wrong payment count: need {cost_to_pay}, got {len(explicit_payments)}")
        if len(set(explicit_payments)) != len(explicit_payments):
            return fail("duplicate payment uid")
        if any(uid == action.card_uid or uid not in player.hand for uid in explicit_payments):
            return fail("invalid payment uid")
        if require_symbol:
            sym = normalize_symbol(card.symbol)
            if not any(symbol_match_for_entry(ms, gs, uid, sym) for uid in explicit_payments):
                return fail("no matching symbol in payment cards")
        payments = explicit_payments
    else:
        payments = payment_picker(gs, ms, player, action.card_uid, play_face_uid, cost_to_pay, require_symbol)
        if payments is None:
            return fail("payment selection failed")
    action.payment_uids = [int(uid) for uid in payments]

    # User rules override: if a starred card is played and payment includes
    # a matching symbol, STAR auto-activates even when non-STAR action is picked.
    if (
        (not action.use_star)
        and has_star_ability(card)
        and cost_to_pay > 0
    ):
        sym = normalize_symbol(card.symbol)
        if (
            sym not in {"", "n/a"}
            and any(symbol_match_for_entry(ms, gs, uid, sym) for uid in payments)
            and star_has_immediate_value(gs, ms, player, card, played_entry_uid=action.card_uid)
        ):
            auto_star = True

    # Pay cost into pool.
    for uid in payments:
        if uid not in player.hand or uid == action.card_uid:
            return fail(f"invalid payment uid {uid}")
    for uid in payments:
        player.hand.remove(uid)
        add_to_pool(ms, uid)
        turn_state.discarded_entry_uids.add(uid)
    if verbose and payments:
        paid = ", ".join(entry_short_label(ms, gs, uid) for uid in payments)
        print(f"{player.name} pays cost by discarding: {paid}")

    # Play card.
    try:
        player.hand.remove(action.card_uid)
        if action.kind == "play_ocean":
            player.board_oceans.append(play_face_uid)
            player.ocean_slots[play_face_uid] = OceanSlots()
            if verbose:
                print(f"{player.name} plays ocean {card.uid}:{card.name}")
            # Ocean play flips one card from draw pile to pool.
            if gs.deck:
                flipped = gs.deck.pop(0)
                if ms.end_game_uid is not None and flipped == ms.end_game_uid:
                    trigger_end_game(ms, gs)
                    ms.discard_pile.append(flipped)
                    if verbose:
                        print("Ocean flip revealed END GAME (not added to pool). Final round starts.")
                else:
                    add_to_pool(ms, flipped)
                    if verbose:
                        print(f"Ocean flip to pool: {entry_short_label(ms, gs, flipped)}")
            before_hand = len(player.hand)
            run_main_ability(
                gs,
                play_face_uid,
                player,
                ctx={
                    "played_as": "ocean",
                    "ms": ms,
                    "is_human_turn": is_human_turn,
                    "turn_state": turn_state,
                    "star_active": action.use_star or auto_star,
                },
            )
            sync_reactive_trigger_flags(gs, player)
            resolve_reactive_draw_triggers(gs, player, card, action.kind, ms)
            if verbose:
                drew = len(player.hand) - before_hand
                if drew > 0:
                    print(f"{player.name} draws {drew} from {card.uid}:{card.name} ability.")
        elif action.kind == "play_to_ocean":
            direction = card.direction.strip().lower()
            if direction not in {"up", "down", "left", "right"}:
                return fail(f"invalid direction '{card.direction}' for card {card.uid}:{card.name}")
            slots_obj = player.ocean_slots.get(action.ocean_uid)
            if not isinstance(slots_obj, OceanSlots):
                return fail(f"target ocean slots missing for ocean {action.ocean_uid}")
            slots_obj.slot(direction).append(play_face_uid)
            before_hand = len(player.hand)
            run_main_ability(
                gs,
                play_face_uid,
                player,
                ctx={
                    "ocean_uid": action.ocean_uid,
                    "played_to": direction,
                    "ms": ms,
                    "is_human_turn": is_human_turn,
                    "turn_state": turn_state,
                    "star_active": action.use_star or auto_star,
                },
            )
            sync_reactive_trigger_flags(gs, player)
            resolve_reactive_draw_triggers(gs, player, card, action.kind, ms)
            if verbose:
                print(f"{player.name} plays {card.uid}:{card.name} to ocean {action.ocean_uid}")
                drew = len(player.hand) - before_hand
                if drew > 0:
                    print(f"{player.name} draws {drew} from {card.uid}:{card.name} ability.")
        else:
            return fail(f"unknown action kind {action.kind}")
    except Exception:
        return fail("exception while resolving action")

    # Keep reactive trigger flags aligned with actual board state.
    sync_reactive_trigger_flags(gs, player)

    # Track cards played this turn for optional replay pickup.
    turn_state.played_face_uids.append(play_face_uid)

    if action.use_star or auto_star:
        pre_free_flags = {k: bool(player.flags.get(k, False)) for k in FREE_PLAY_FLAGS}
        # For two-sided cards, STAR must execute from the face actually played.
        if has_star_text(card):
            run_star_ability(gs, play_face_uid, player, ctx={"played_with_star": True})
        else:
            run_inferred_star_fallback(gs, player, card)
        turn_state.star_activations += 1
        gained_free = any((not pre_free_flags[k]) and bool(player.flags.get(k, False)) for k in FREE_PLAY_FLAGS)
        if gained_free:
            # STAR free-play abilities need an immediate same-turn action window.
            turn_state.free_followups += 1
        if verbose:
            star_note = " (auto)" if auto_star and not action.use_star else ""
            print(f"{player.name} triggers STAR on {card.uid}:{card.name}{star_note}")

    return True


def action_features(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    include_sim_delta: bool = True,
) -> Dict[str, float]:
    def _safe_zero_features() -> Dict[str, float]:
        return {
            "bias": 1.0,
            "is_ocean": 0.0,
            "uses_star": 0.0,
            "card_cost": 0.0,
            "has_plus": 0.0,
            "target_occupancy": 0.0,
            "fills_empty_ocean": 0.0,
            "draw_from_pool": float(getattr(action, "draw_from_pool", 0)),
            "pool_pick_value": 0.0,
            "immediate_delta": 0.0,
            "synergy_bonus": 0.0,
            "species_bonus": 0.0,
            "same_ocean_bonus": 0.0,
            "symbol_bonus": 0.0,
            "stack_bonus": 0.0,
            "plan_fit_bonus": 0.0,
            "future_value": 0.0,
            "deny_bonus": 0.0,
            "overbuild_ocean_penalty": 0.0,
            "sim_point_delta": 0.0,
        }

    if action.kind == "draw":
        pick_value = 0.0
        if action.pool_pick_uids:
            vals = [pool_entry_value_for_player(ms, gs, uid, player) for uid in action.pool_pick_uids]
            if vals:
                pick_value = float(sum(vals) / len(vals))
        elif action.draw_from_pool > 0 and ms.pool:
            scored = sorted(
                (pool_entry_value_for_player(ms, gs, uid, player) for uid in ms.pool),
                reverse=True,
            )
            top = scored[: action.draw_from_pool]
            if top:
                pick_value = float(sum(top) / len(top))
        return {
            "bias": 1.0,
            "is_ocean": 0.0,
            "uses_star": 0.0,
            "card_cost": 0.0,
            "has_plus": 0.0,
            "target_occupancy": 0.0,
            "fills_empty_ocean": 0.0,
            "draw_from_pool": float(action.draw_from_pool),
            "pool_pick_value": pick_value,
            "immediate_delta": 0.5,
            "synergy_bonus": 0.0,
            "species_bonus": 0.0,
            "same_ocean_bonus": 0.0,
            "symbol_bonus": 0.0,
            "stack_bonus": 0.0,
            "plan_fit_bonus": 0.0,
            "future_value": action_future_value_bonus(gs, ms, player, action),
            "deny_bonus": action_deny_bonus(gs, ms, player, action),
            "overbuild_ocean_penalty": 0.0,
            "sim_point_delta": 0.0,
        }

    play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db.get(play_face_uid)
    if card is None:
        return _safe_zero_features()
    feat = {
        "bias": 1.0,
        "is_ocean": 1.0 if action.kind == "play_ocean" else 0.0,
        "uses_star": 1.0 if action.use_star else 0.0,
        "card_cost": float(card.cost),
        "has_plus": 1.0 if "+" in card.text else 0.0,
        "draw_from_pool": 0.0,
        "pool_pick_value": 0.0,
        "synergy_bonus": action_synergy_bonus(gs, player, action, synergy_map),
        "species_bonus": action_species_bonus(gs, player, action, species_map),
        "same_ocean_bonus": action_same_ocean_bonus(gs, player, action, same_ocean_map),
        "symbol_bonus": action_symbol_bonus(gs, ms, player, action),
        "stack_bonus": action_stack_bonus(gs, player, action),
        "plan_fit_bonus": action_plan_fit_bonus(gs, player, action),
        "future_value": action_future_value_bonus(gs, ms, player, action),
        "deny_bonus": 0.0,
        "overbuild_ocean_penalty": 0.0,
    }

    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        slots = player.ocean_slots.get(action.ocean_uid)
        count = len(slots.all_cards()) if isinstance(slots, OceanSlots) else 0
        feat["target_occupancy"] = float(count)
        feat["fills_empty_ocean"] = 1.0 if count == 0 else 0.0
    else:
        feat["target_occupancy"] = 0.0
        feat["fills_empty_ocean"] = 0.0

    t = card.text.lower()
    if card.name.lower() == "clownfish" and action.kind == "play_to_ocean" and action.ocean_uid is not None:
        ocean_card = gs.card_db.get(action.ocean_uid)
        if ocean_card is not None:
            t = f"{t} | {ocean_card.text.lower()}"
    base_plus = 0
    for chunk in [x.strip() for x in t.split("|")]:
        if not chunk.startswith("+"):
            continue
        if any(w in chunk for w in [" if ", " per ", " or ", " most ", " only ", " at least ", " every "]):
            continue
        m = re.match(r"\+(\d+)\b", chunk)
        if m:
            base_plus += int(m.group(1))

    board = [gs.card_db[uid] for uid in player.board_oceans if uid in gs.card_db]
    for slots in player.ocean_slots.values():
        board.extend(gs.card_db[uid] for uid in slots.all_cards() if uid in gs.card_db)
    ocean_count = len(player.board_oceans)
    other_ocean_counts = [len(p.board_oceans) for p in gs.players if p is not player]

    hand_name_counts: Dict[str, int] = {}
    hand_species_counts: Dict[str, int] = {}
    hand_floor_total = 0
    for entry_uid in player.hand:
        if action.card_uid != -1 and entry_uid == action.card_uid:
            continue
        for face_uid2 in entry_faces(ms, entry_uid):
            c2 = gs.card_db.get(face_uid2)
            if c2 is None:
                continue
            n2 = c2.name.strip().lower()
            s2 = c2.species.strip().lower()
            hand_name_counts[n2] = hand_name_counts.get(n2, 0) + 1
            hand_species_counts[s2] = hand_species_counts.get(s2, 0) + 1
            if c2.direction.strip().lower() == "down" and s2 != "ocean":
                hand_floor_total += 1

    if "+3 per bird" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "bird")
    if "+1 per crustacean" in t:
        base_plus += sum(1 for c in board if c.species.lower() == "crustacean")
    if "+3 per crustacean" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "crustacean")
    if "+1 per coral" in t and "attached to a coral reef" not in t:
        base_plus += sum(1 for c in board if c.species.lower() == "coral")
    if "+2 per coral" in t and "attached to a coral reef" not in t:
        base_plus += 2 * sum(1 for c in board if c.species.lower() == "coral")
    if "+5 per coral" in t:
        base_plus += 5 * sum(1 for c in board if c.species.lower() == "coral")
    if "+3 per coral" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "coral")
    if "+2 per matching symbol" in t:
        base_plus += 2 * sum(1 for c in board if normalize_symbol(c.symbol) == normalize_symbol(card.symbol))
    if "+5 per invertebrate" in t:
        base_plus += 5 * sum(1 for c in board if c.species.lower() == "invertebrate")
    if "+1 per invertebrate" in t:
        base_plus += sum(1 for c in board if c.species.lower() == "invertebrate")
    if "+3 per invertebrate" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "invertebrate")
    if "+3 per baitfish" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "baitfish")
    if "+4 per baitfish" in t:
        base_plus += 4 * sum(1 for c in board if c.species.lower() == "baitfish")
    if "+4 per cephalopod" in t:
        base_plus += 4 * sum(1 for c in board if c.species.lower() == "cephalopod")
    if "+2 per n/a animal" in t or "+2 per uncharted animal" in t or "+2 per crosscurrent animal" in t:
        base_plus += 2 * sum(
            1
            for c in board
            if c.species.lower() in {"uncharted", "n/a", "crosscurrent"} and c.direction.strip().lower() != "n/a"
        )
    if "+3 per n/a animal" in t or "+3 per uncharted animal" in t or "+3 per crosscurrent animal" in t:
        base_plus += 3 * sum(
            1
            for c in board
            if c.species.lower() in {"uncharted", "n/a", "crosscurrent"} and c.direction.strip().lower() != "n/a"
        )
    if "+9 per each mahi mahi you control" in t:
        base_plus += 9 * sum(1 for c in board if c.name.lower() == "mahi mahi")

    # Engine-readiness checks from card text: avoid dead setup plays.
    if (
        "play any # of baitfish for free" in t
        or "play any number of baitfish for free" in t
        or "play any number of baitfish this turn" in t
    ):
        if has_playable_followup_species(gs, ms, player, "baitfish", exclude_entry_uid=action.card_uid):
            base_plus += 6
        else:
            base_plus -= 10

    if "play any number of cephalopods for free" in t:
        if has_playable_followup_species(gs, ms, player, "cephalopod", exclude_entry_uid=action.card_uid):
            base_plus += 6
        else:
            base_plus -= 10

    if "+6 per mandarin goby" in t:
        goby_total = sum(1 for c in board if c.name.lower() == "mandarin goby") + hand_name_counts.get("mandarin goby", 0)
        if goby_total <= 0:
            # Very low-value line without goby support; strongly avoid this dead play.
            base_plus -= 20
        elif goby_total == 1:
            base_plus -= 4
        else:
            base_plus += min(5, 2 * goby_total)

    if "draw one when a game fish is played" in t:
        game_fish_total = sum(1 for c in board if c.species.lower() == "game fish") + hand_species_counts.get("game fish", 0)
        if game_fish_total <= 0:
            base_plus -= 3
        else:
            base_plus += min(2, game_fish_total)

    if "draw one when you play a card on the ocean floor" in t:
        floor_total = sum(1 for c in board if c.direction.lower() == "down" and c.species.lower() != "ocean")
        floor_total += hand_floor_total
        if floor_total <= 1:
            base_plus -= 2

    # Human-like conditional handling: penalize dead plays, reward satisfied conditions.
    same_ocean_cards: List[CardDef] = []
    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        slots = player.ocean_slots.get(action.ocean_uid)
        if isinstance(slots, OceanSlots):
            same_ocean_cards = [gs.card_db[uid] for uid in slots.all_cards() if uid in gs.card_db]

    if "if this is the only creature on this ocean" in t:
        if action.kind == "play_to_ocean" and len(same_ocean_cards) == 0:
            base_plus += 10
        else:
            base_plus -= 8

    if "if sharing an ocean with a goliath grouper" in t:
        if action.kind == "play_to_ocean" and any(c.name.lower() == "goliath grouper" for c in same_ocean_cards):
            base_plus += 8
        else:
            base_plus -= 6

    if "if sharing an ocean with a king salmon" in t:
        if action.kind == "play_to_ocean" and any(c.name.lower() == "king salmon" for c in same_ocean_cards):
            base_plus += 4
        else:
            base_plus -= 3

    if "if sharing the ocean with a cephalopod" in t:
        if action.kind == "play_to_ocean" and any(c.species.lower() == "cephalopod" for c in same_ocean_cards):
            base_plus += 6
        else:
            base_plus -= 6

    if "if sharing an ocean with baitfish" in t:
        if action.kind == "play_to_ocean" and any(c.species.lower() == "baitfish" for c in same_ocean_cards):
            base_plus += 5
        else:
            base_plus -= 5

    if "if sharing an ocean with a mahi mahi" in t:
        if action.kind == "play_to_ocean" and any(c.name.lower() == "mahi mahi" for c in same_ocean_cards):
            base_plus += 9
        else:
            base_plus -= 6

    if "+2 per each unique symbol attached to this ocean" in t and action.kind == "play_to_ocean":
        syms = {normalize_symbol(c.symbol) for c in same_ocean_cards if normalize_symbol(c.symbol) not in {"", "n/a"}}
        cs = normalize_symbol(card.symbol)
        if cs not in {"", "n/a"}:
            syms.add(cs)
        base_plus += 2 * len(syms)

    if "+2 per each unique species attached to this ocean" in t and action.kind == "play_to_ocean":
        spp = {c.species.lower() for c in same_ocean_cards if c.species.strip()}
        if card.species.strip():
            spp.add(card.species.lower())
        base_plus += 2 * len(spp)

    if "if you have at least three cephalopods" in t:
        ceph_count = sum(1 for c in board if c.species.lower() == "cephalopod")
        if card.species.lower() == "cephalopod":
            ceph_count += 1
        if ceph_count >= 3:
            base_plus += 4
        else:
            base_plus -= 3

    if "if you have the most oceans" in t:
        projected_oceans = ocean_count + (1 if action.kind == "play_ocean" else 0)
        if projected_oceans > (max(other_ocean_counts) if other_ocean_counts else 0):
            base_plus += 8
        else:
            base_plus -= 4

    if "+8 if you have all 8 oceans" in t:
        projected_oceans = ocean_count + (1 if action.kind == "play_ocean" else 0)
        if projected_oceans >= 8:
            base_plus += 8
        else:
            base_plus -= 2

    if has_star_option_for_action(gs, ms, player, action):
        base_plus -= 6

    # STAR free-play actions should be scored as weak when they cannot actually chain.
    if action.use_star and not star_has_immediate_value(gs, ms, player, card, played_entry_uid=action.card_uid):
        base_plus -= 6

    if card.name.lower() == "clownfish" and action.kind == "play_to_ocean" and action.ocean_uid is not None:
        ocean_card = gs.card_db.get(action.ocean_uid)
        ocean_name = ocean_card.name.strip().lower() if ocean_card is not None else ""
        current_v = clownfish_ocean_value(ocean_name)
        best_v = current_v
        for candidate_ocean_uid in player.board_oceans:
            if can_attach_to_ocean(gs, player, play_face_uid, candidate_ocean_uid):
                candidate = gs.card_db.get(candidate_ocean_uid)
                if candidate is None:
                    continue
                candidate_name = candidate.name.strip().lower()
                best_v = max(best_v, clownfish_ocean_value(candidate_name))
        base_plus += 2.0 * current_v
        if best_v > current_v:
            base_plus -= 2.5 * (best_v - current_v)

    if "kelp forest" in t and ("at least 4" in t or "4 or more" in t):
        kelp_count = sum(1 for c in board if c.name.lower() == "kelp forest")
        if card.name.lower() == "kelp forest":
            kelp_count += 1
        if kelp_count >= 4:
            if "per kelp forest" in t:
                base_plus += 5 * kelp_count
            else:
                base_plus += 5

    if action.kind == "play_ocean" and player.board_oceans:
        empty_oceans = count_empty_oceans(player)
        has_attach_option = any(
            a.kind == "play_to_ocean" for a in legal_actions(gs, ms, player, include_draw=False)
        )
        if has_attach_option:
            # Penalize overbuilding oceans when the player can already attach creatures.
            if empty_oceans > 0:
                feat["overbuild_ocean_penalty"] = float(1 + empty_oceans)
            elif len(player.board_oceans) >= 3:
                feat["overbuild_ocean_penalty"] = 1.5

    feat["immediate_delta"] = float(base_plus)
    feat["sim_point_delta"] = simulated_point_delta(gs, ms, player, action) if include_sim_delta else 0.0
    return feat


def weighted_score(features: Dict[str, float], w: Dict[str, float]) -> float:
    return sum(features[k] * w.get(k, 0.0) for k in features)


def choose_action_greedy_quick(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    weights: Dict[str, float],
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
    archetype_profile: Optional[Dict[str, Any]] = None,
) -> Optional[Action]:
    acts = candidate_actions_for_ai(gs, ms, player)
    acts = filter_overbuild_ocean_actions(gs, ms, player, acts)
    non_dead = [a for a in acts if not action_is_dead_engine_play(gs, ms, player, a)]
    if non_dead:
        acts = non_dead
    if not acts:
        return None
    limited_hidden = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("human_limited_inference", False))
    best_a: Optional[Action] = None
    best_score = float("-inf")
    for a in acts:
        feats = action_features(
            gs,
            ms,
            player,
            a,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            include_sim_delta=False,
        )
        score = weighted_score(feats, weights)
        strategy_v, novelty_v, branch_v, _ = strategy_signal(
            gs,
            ms,
            player,
            a,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
        )
        score += weights.get("strategy_bonus", 0.0) * strategy_v
        score += weights.get("novelty_bonus", 0.0) * novelty_v
        score += weights.get("branch_bonus", 0.0) * branch_v
        score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
        score += action_engine_timing_bonus(gs, ms, player, a)
        score += human_realism_action_adjustment(gs, ms, player, a, feats)
        # Add tiny point-delta signal for tie breaks.
        if not limited_hidden:
            score += 0.08 * simulated_point_delta(gs, ms, player, a)
        if score > best_score:
            best_score = score
            best_a = a
    return best_a


def continue_turn_followups_greedy(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    turn_state: TurnState,
    weights: Dict[str, float],
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
    archetype_profile: Optional[Dict[str, Any]] = None,
    max_actions: int = 3,
) -> None:
    action_budget = 0
    replay_count = pending_replay_actions(player)
    if replay_count > 0:
        maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
        action_budget += consume_replay_actions(player)
    if turn_state.free_followups > 0:
        action_budget += turn_state.free_followups
        player.flags["_free_action_only"] = True
        turn_state.free_followups = 0

    steps = 0
    hard_cap = max(60, max_actions)
    while action_budget > 0 and steps < hard_cap and (
        steps < max_actions
        or has_multi_play_window(player)
    ):
        a = choose_action_greedy_quick(
            gs,
            ms,
            player,
            weights,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
            archetype_profile=archetype_profile,
        )
        if a is None:
            break
        ok = apply_action(gs, ms, player, a, turn_state, choose_payment_ai, verbose=False)
        if not ok:
            break
        steps += 1
        if turn_state.force_end_turn:
            action_budget = 0
            break
        replay_count = pending_replay_actions(player)
        if replay_count > 0:
            maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
            action_budget += consume_replay_actions(player)
        if turn_state.free_followups > 0:
            action_budget += turn_state.free_followups
            player.flags["_free_action_only"] = True
            turn_state.free_followups = 0
        if has_multi_play_window(player):
            action_budget += 1
        action_budget -= 1

    if "_free_action_only" in player.flags:
        player.flags["_free_action_only"] = False


def simulate_one_turn_greedy(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    weights: Dict[str, float],
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
    archetype_profile: Optional[Dict[str, Any]] = None,
    max_actions: int = 3,
) -> None:
    turn_state = TurnState()
    action_budget = 1
    steps = 0
    hard_cap = max(60, max_actions)
    while action_budget > 0 and steps < hard_cap and (
        steps < max_actions
        or has_multi_play_window(player)
    ):
        a = choose_action_greedy_quick(
            gs,
            ms,
            player,
            weights,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
            archetype_profile=archetype_profile,
        )
        if a is None:
            break
        ok = apply_action(gs, ms, player, a, turn_state, choose_payment_ai, verbose=False)
        if not ok:
            break
        steps += 1
        if turn_state.force_end_turn:
            action_budget = 0
            break
        replay_count = pending_replay_actions(player)
        if replay_count > 0:
            maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
            action_budget += consume_replay_actions(player)
        if turn_state.free_followups > 0:
            action_budget += turn_state.free_followups
            player.flags["_free_action_only"] = True
            turn_state.free_followups = 0
        if has_multi_play_window(player):
            action_budget += 1
        action_budget -= 1

    discard_down_to_ten_ai(gs, ms, player)
    clear_turn_only_flags(player)


def relative_advantage(gs: GameState, player_index: int) -> float:
    my = final_points(gs, gs.players[player_index])
    others = [final_points(gs, p) for i, p in enumerate(gs.players) if i != player_index]
    if not others:
        return float(my)
    return float(my - (sum(others) / len(others)))


def double_check_action_score(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    weights: Dict[str, float],
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
    archetype_profile: Optional[Dict[str, Any]] = None,
) -> float:
    """Second-pass move check: simulate move, likely opponent response, and our next turn."""
    limited_hidden = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("human_limited_inference", False))
    if limited_hidden:
        # Human-limited inference mode: avoid hidden-information rollouts.
        feats = action_features(
            gs,
            ms,
            player,
            action,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            include_sim_delta=False,
        )
        score = weighted_score(feats, weights)
        score += human_realism_action_adjustment(gs, ms, player, action, feats)
        pressure = endgame_pressure(len(gs.deck))
        if action.kind == "draw":
            score -= 0.20 * pressure
            score += (1.0 - 0.55 * pressure) * 0.20 * max(0.0, float(feats.get("future_value", 0.0)))
        else:
            score += 0.08 * float(feats.get("immediate_delta", 0.0))
            score += (1.0 - pressure) * 0.30 * max(0.0, float(feats.get("future_value", 0.0)))
        return score

    try:
        player_index = next(i for i, p in enumerate(gs.players) if p is player)
    except StopIteration:
        return -1e9

    gs2 = copy.deepcopy(gs)
    ms2 = copy.deepcopy(ms)
    p2 = gs2.players[player_index]
    before_score = final_points(gs2, p2)
    before_adv = relative_advantage(gs2, player_index)
    ts = TurnState()
    ok = apply_action(gs2, ms2, p2, copy.deepcopy(action), ts, choose_payment_ai, verbose=False)
    if not ok:
        return -1e9

    continue_turn_followups_greedy(
        gs2,
        ms2,
        p2,
        ts,
        weights,
        synergy_map=synergy_map,
        species_map=species_map,
        same_ocean_map=same_ocean_map,
        strategy_value_map=strategy_value_map,
        strategy_count_map=strategy_count_map,
        strategy_transition_map=strategy_transition_map,
        strategy_transition_count_map=strategy_transition_count_map,
        archetype_profile=archetype_profile,
        max_actions=STRATEGIC_FIRST_TURN_MAX_ACTIONS,
    )

    after_score = final_points(gs2, p2)
    point_gain = float(after_score - before_score)

    # Opponent-aware layer: simulate one likely response from each opponent.
    opponent_threat = 0.0
    n = len(gs2.players)
    for step in range(1, n):
        oi = (player_index + step) % n
        op = gs2.players[oi]
        before_op = final_points(gs2, op)
        before_my = final_points(gs2, p2)
        simulate_one_turn_greedy(
            gs2,
            ms2,
            op,
            weights,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
            archetype_profile=None,
            max_actions=STRATEGIC_OPP_TURN_MAX_ACTIONS,
        )
        op_gain = final_points(gs2, op) - before_op
        my_loss = before_my - final_points(gs2, p2)
        opponent_threat += max(0.0, (0.7 * op_gain) + (0.3 * my_loss))

    # Deeper lookahead: simulate our next turn after opponents.
    before_next_my = final_points(gs2, p2)
    simulate_one_turn_greedy(
        gs2,
        ms2,
        p2,
        weights,
        synergy_map=synergy_map,
        species_map=species_map,
        same_ocean_map=same_ocean_map,
        strategy_value_map=strategy_value_map,
        strategy_count_map=strategy_count_map,
        strategy_transition_map=strategy_transition_map,
        strategy_transition_count_map=strategy_transition_count_map,
        archetype_profile=archetype_profile,
        max_actions=STRATEGIC_RETURN_TURN_MAX_ACTIONS,
    )
    next_gain = max(0.0, final_points(gs2, p2) - before_next_my)
    # Additional lookahead to value compounding engine growth.
    before_next2_my = final_points(gs2, p2)
    simulate_one_turn_greedy(
        gs2,
        ms2,
        p2,
        weights,
        synergy_map=synergy_map,
        species_map=species_map,
        same_ocean_map=same_ocean_map,
        strategy_value_map=strategy_value_map,
        strategy_count_map=strategy_count_map,
        strategy_transition_map=strategy_transition_map,
        strategy_transition_count_map=strategy_transition_count_map,
        archetype_profile=archetype_profile,
        max_actions=STRATEGIC_EXTRA_LOOKAHEAD_MAX_ACTIONS,
    )
    next2_gain = max(0.0, final_points(gs2, p2) - before_next2_my)
    adv_gain = relative_advantage(gs2, player_index) - before_adv

    # Human-like tie-break: if this creates/keeps a healthy board, prefer it over passive lines.
    board_development = 0.0
    if action.kind == "play_ocean":
        board_development += 0.9 if len(player.board_oceans) < 4 else 0.2
    elif action.kind == "play_to_ocean":
        board_development += 0.5

    pressure = endgame_pressure(len(gs.deck))
    local_feats = action_features(
        gs,
        ms,
        player,
        action,
        synergy_map=synergy_map,
        species_map=species_map,
        same_ocean_map=same_ocean_map,
        include_sim_delta=False,
    )
    future_bias = max(0.0, float(local_feats.get("future_value", 0.0)))

    if pressure <= 0.45:
        point_w = 1.55
        next_w = 1.60
        board_w = 1.30
        threat_w = 0.50
        score_w = 0.045
    elif pressure <= 0.75:
        point_w = 2.05
        next_w = 1.05
        board_w = 1.00
        threat_w = 0.55
        score_w = 0.055
    else:
        point_w = 2.60
        next_w = 0.65
        board_w = 0.70
        threat_w = 0.62
        score_w = 0.065

    return (
        point_gain * point_w
        + next_gain * next_w
        + next2_gain * (0.55 - 0.25 * pressure)
        + adv_gain * 0.65
        + board_development * board_w
        + future_bias * (1.2 - 0.7 * pressure)
        + after_score * score_w
        - opponent_threat * threat_w
    )


def online_update_weights(
    weights: Dict[str, float],
    features: Dict[str, float],
    reward: float,
    lr: float = 0.03,
) -> None:
    """One-step online update so AI learns from each executed move."""
    learnable_keys = {
        "uses_star",
        "card_cost",
        "has_plus",
        "target_occupancy",
        "fills_empty_ocean",
        "draw_from_pool",
        "pool_pick_value",
        "synergy_bonus",
        "species_bonus",
        "same_ocean_bonus",
        "stack_bonus",
        "plan_fit_bonus",
        "future_value",
        "deny_bonus",
        "overbuild_ocean_penalty",
    }
    pred = weighted_score(features, weights)
    err = reward - pred
    for k, v in features.items():
        if k not in learnable_keys:
            continue
        weights[k] = float(weights.get(k, 0.0) + lr * err * v)
        if weights[k] > 6.0:
            weights[k] = 6.0
        elif weights[k] < -6.0:
            weights[k] = -6.0
    stabilize_weights(weights)


def choose_action_random(gs: GameState, ms: MatchState, player: PlayerState) -> Optional[Action]:
    acts = candidate_actions_for_ai(gs, ms, player)
    if not acts:
        return None
    return random.choice(acts)


def choose_action_weighted(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    weights: Dict[str, float],
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
    strategy_value_map: Optional[Dict[str, float]] = None,
    strategy_count_map: Optional[Dict[str, int]] = None,
    strategy_transition_map: Optional[Dict[str, float]] = None,
    strategy_transition_count_map: Optional[Dict[str, int]] = None,
    archetype_profile: Optional[Dict[str, Any]] = None,
    epsilon: float = 0.05,
) -> Optional[Action]:
    acts = candidate_actions_for_ai(gs, ms, player)
    acts = filter_overbuild_ocean_actions(gs, ms, player, acts)
    non_dead = [a for a in acts if not action_is_dead_engine_play(gs, ms, player, a)]
    if non_dead:
        acts = non_dead
    if not acts:
        return None

    limited_hidden = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("human_limited_inference", False))
    eps = adaptive_exploration_rate(gs, player, epsilon)
    include_sim_delta = not limited_hidden

    if random.random() < eps:
        # Explore inside high-quality strategic lines instead of random isolated plays.
        explore_scored: List[Tuple[Action, float]] = []
        for a in acts:
            feats = action_features(
                gs,
                ms,
                player,
                a,
                synergy_map=synergy_map,
                species_map=species_map,
                same_ocean_map=same_ocean_map,
                include_sim_delta=False,
            )
            score = weighted_score(feats, weights)
            strategy_v, novelty_v, branch_v, _ = strategy_signal(
                gs,
                ms,
                player,
                a,
                strategy_value_map=strategy_value_map,
                strategy_count_map=strategy_count_map,
                strategy_transition_map=strategy_transition_map,
                strategy_transition_count_map=strategy_transition_count_map,
            )
            score += weights.get("strategy_bonus", 0.0) * strategy_v
            score += 0.35 * weights.get("novelty_bonus", 0.0) * novelty_v
            score += weights.get("branch_bonus", 0.0) * branch_v
            score += 1.35 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
            score += action_engine_timing_bonus(gs, ms, player, a)
            score += human_realism_action_adjustment(gs, ms, player, a, feats)
            score += 0.55 * max(0.0, float(feats.get("future_value", 0.0)))
            explore_scored.append((a, score))
        explore_scored.sort(key=lambda x: x[1], reverse=True)
        band_n = max(2, min(6, len(explore_scored) // 2 + 1))
        return random.choice([a for a, _ in explore_scored[:band_n]])

    # Human-like opening: if you have no ocean, prioritize playing an ocean when possible.
    if not player.board_oceans:
        ocean_acts = [a for a in acts if a.kind == "play_ocean"]
        if ocean_acts:
            best_ocean: Optional[Action] = None
            best_ocean_score = float("-inf")
            for a in ocean_acts:
                feats = action_features(
                    gs,
                    ms,
                    player,
                    a,
                    synergy_map=synergy_map,
                    species_map=species_map,
                    same_ocean_map=same_ocean_map,
                    include_sim_delta=include_sim_delta,
                )
                score = weighted_score(feats, weights)
                strategy_v, novelty_v, branch_v, _ = strategy_signal(
                    gs,
                    ms,
                    player,
                    a,
                strategy_value_map=strategy_value_map,
                strategy_count_map=strategy_count_map,
                strategy_transition_map=strategy_transition_map,
                strategy_transition_count_map=strategy_transition_count_map,
            )
                score += weights.get("strategy_bonus", 0.0) * strategy_v
                score += weights.get("novelty_bonus", 0.0) * novelty_v
                score += weights.get("branch_bonus", 0.0) * branch_v
                score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
                score += action_engine_timing_bonus(gs, ms, player, a)
                score += human_realism_action_adjustment(gs, ms, player, a, feats)
                if score > best_ocean_score:
                    best_ocean_score = score
                    best_ocean = a
            if best_ocean is not None:
                return best_ocean

    scored: List[Tuple[Action, float]] = []
    for a in acts:
        feats = action_features(
            gs,
            ms,
            player,
            a,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            include_sim_delta=include_sim_delta,
        )
        score = weighted_score(feats, weights)
        strategy_v, novelty_v, branch_v, _ = strategy_signal(
            gs,
            ms,
            player,
            a,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
        )
        score += weights.get("strategy_bonus", 0.0) * strategy_v
        score += weights.get("novelty_bonus", 0.0) * novelty_v
        score += weights.get("branch_bonus", 0.0) * branch_v
        score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
        score += action_engine_timing_bonus(gs, ms, player, a)
        score += human_realism_action_adjustment(gs, ms, player, a, feats)
        scored.append((a, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    shortlist = scored[: min(STRATEGIC_SHORTLIST_SIZE, len(scored))]

    best: Optional[Action] = None
    best_score = float("-inf")
    for a, base_score in shortlist:
        if limited_hidden:
            confirm = base_score
        else:
            confirm = double_check_action_score(
                gs,
                ms,
                player,
                a,
                weights,
                synergy_map=synergy_map,
                species_map=species_map,
                same_ocean_map=same_ocean_map,
                strategy_value_map=strategy_value_map,
                strategy_count_map=strategy_count_map,
                strategy_transition_map=strategy_transition_map,
                strategy_transition_count_map=strategy_transition_count_map,
                archetype_profile=archetype_profile,
            )
        total = base_score * (1.0 - STRATEGIC_CONFIRM_WEIGHT) + confirm * STRATEGIC_CONFIRM_WEIGHT
        if total > best_score:
            best_score = total
            best = a

    # If draw barely beats play, still prefer playing the board.
    if best is not None and best.kind == "draw":
        play_options = [(a, s) for a, s in scored if a.kind != "draw"]
        if play_options:
            best_play = max(play_options, key=lambda x: x[1])
            # Be more willing to play onto the board, especially when we still
            # have empty oceans to fill.
            threshold = 0.9 if count_empty_oceans(player) > 0 else 0.3
            if human_realism_enabled():
                # Late game should bias toward immediate board points over draws.
                threshold += 0.4 * endgame_pressure(len(gs.deck))
            if best_play[1] >= best_score - threshold:
                return best_play[0]

    return best


def choose_action_human(gs: GameState, ms: MatchState, player: PlayerState) -> Optional[Action]:
    actions = legal_actions(gs, ms, player, include_draw=True)
    if not actions:
        return None

    print(f"\n{player.name} hand:")
    for uid in player.hand:
        print("  " + entry_label(ms, gs, uid))

    print(f"\n{player.name} board:")
    print(board_summary(gs, player))

    print("\nPool:")
    if ms.pool:
        print("  " + ", ".join(entry_short_label(ms, gs, uid) for uid in ms.pool))
    else:
        print("  (empty)")

    print(f"\nDeck cards remaining: {len(gs.deck)}")

    print("\nLegal actions:")
    for i, a in enumerate(actions):
        if a.kind == "draw":
            if a.draw_from_pool == 0:
                desc = "draw 2 from deck"
            elif a.draw_from_pool == 1:
                desc = "draw 1 from pool + 1 from deck"
            else:
                desc = "draw 2 from pool"
        elif a.kind == "play_ocean":
            face = a.face_uid if a.face_uid is not None else a.card_uid
            card = gs.card_db[face]
            desc = f"play ocean {card.uid}:{card.name}"
            if a.use_star:
                desc += " (use STAR if symbol paid)"
        else:
            face = a.face_uid if a.face_uid is not None else a.card_uid
            card = gs.card_db[face]
            desc = f"play {card.uid}:{card.name} to ocean {a.ocean_uid}"
            if a.use_star:
                desc += " (use STAR if symbol paid)"
        print(f"  [{i}] {desc}")

    while True:
        raw = input("Choose action index: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < len(actions):
                chosen = actions[idx]
                if chosen.kind == "draw" and chosen.draw_from_pool > 0:
                    need = chosen.draw_from_pool
                    while True:
                        print(f"Choose {need} pool card uid(s) to draw.")
                        print("Pool choices:")
                        for uid in ms.pool:
                            print("  " + entry_label(ms, gs, uid))
                        raw_pool = input("Pool pick uid(s), space-separated: ").strip()
                        parts = [p for p in raw_pool.split() if p]
                        if len(parts) != need:
                            print("Wrong count.")
                            continue
                        try:
                            picks = [int(p) for p in parts]
                        except ValueError:
                            print("Use numeric uids.")
                            continue
                        if len(set(picks)) != len(picks):
                            print("Duplicate uid entered.")
                            continue
                        if any(uid not in ms.pool for uid in picks):
                            print("One or more selected uids are not currently in the pool.")
                            continue
                        chosen.pool_pick_uids = picks
                        break
                return chosen
        print("Invalid choice.")


def final_points(gs: GameState, player: PlayerState) -> int:
    board: List[Tuple[int, CardDef, int]] = []
    for ocean_uid in player.board_oceans:
        ocean = gs.card_db.get(ocean_uid)
        if ocean is None:
            continue
        board.append((ocean_uid, ocean, ocean_uid))
        slots = player.ocean_slots.get(ocean_uid)
        if not isinstance(slots, OceanSlots):
            continue
        for uid in slots.all_cards():
            face = gs.card_db.get(uid)
            if face is None:
                continue
            board.append((uid, face, ocean_uid))

    if not board:
        return 0

    all_cards = [c for _, c, _ in board]
    non_ocean_cards = [c for c in all_cards if c.direction.strip().lower() != "n/a"]

    ocean_count = len(player.board_oceans)
    other_ocean_counts = [len(p.board_oceans) for p in gs.players if p is not player]
    has_most_oceans = ocean_count >= max(other_ocean_counts) if other_ocean_counts else True

    def has_most_piers() -> bool:
        my = sum(1 for uid in player.board_oceans if ((gs.card_db.get(uid).name.lower() == "pier") if gs.card_db.get(uid) else False))
        others = []
        for p in gs.players:
            if p is player:
                continue
            n = sum(1 for uid in p.board_oceans if ((gs.card_db.get(uid).name.lower() == "pier") if gs.card_db.get(uid) else False))
            others.append(n)
        return my >= max(others) if others else True

    def count_animals(owner: PlayerState) -> int:
        total_animals = 0
        for ocean_uid in owner.board_oceans:
            slots = owner.ocean_slots.get(ocean_uid)
            if not slots:
                continue
            for uid in slots.all_cards():
                c = gs.card_db.get(uid)
                if c is None:
                    continue
                if c.direction.strip().lower() != "n/a":
                    total_animals += 1
        return total_animals

    def has_most_animals() -> bool:
        my = count_animals(player)
        others = []
        for p in gs.players:
            if p is player:
                continue
            others.append(count_animals(p))
        return my >= max(others) if others else True

    def fully_occupied_ocean_count() -> int:
        full = 0
        for ocean_uid in player.board_oceans:
            slots = player.ocean_slots.get(ocean_uid)
            if not slots:
                continue
            if slots.up and slots.down and slots.left and slots.right:
                full += 1
        return full

    def coral_attached_to_coral_reef_count() -> int:
        total_coral = 0
        for ocean_uid in player.board_oceans:
            ocean = gs.card_db.get(ocean_uid)
            if ocean is None:
                continue
            if ocean.name.lower() != "coral reef":
                continue
            slots = player.ocean_slots.get(ocean_uid)
            if not slots:
                continue
            for uid in slots.all_cards():
                c = gs.card_db.get(uid)
                if c is None:
                    continue
                if c.species.lower() == "coral":
                    total_coral += 1
        return total_coral

    def species_count(spec: str) -> int:
        s = spec.lower()
        return sum(1 for c in non_ocean_cards if c.species.lower() == s)

    def name_count(name: str) -> int:
        n = name.lower()
        return sum(1 for c in all_cards if c.name.lower() == n)

    def cards_on_same_ocean(target_ocean_uid: int) -> List[CardDef]:
        return [c for _, c, ocean_uid in board if ocean_uid == target_ocean_uid and c.direction.strip().lower() != "n/a"]

    def value_from_threshold_table(text: str, base_value: int) -> int:
        pairs = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\+?\s*=\s*(\d+)", text)]
        if not pairs:
            return 0
        table = {need: pts for need, pts in pairs}
        if base_value in table:
            return table[base_value]
        eligible = [need for need in table.keys() if need <= base_value]
        if not eligible:
            return 0
        best_need = max(eligible)
        return table[best_need]

    coral_reef_count_total = name_count("coral reef") + sum(
        1 for uid, c, o_uid in board
        if c.name.lower() == "clownfish"
        and gs.card_db.get(o_uid) is not None
        and gs.card_db.get(o_uid).name.lower() == "coral reef"
    )
    coral_reef_table_applied = False
    count_table_applied: set = set()  # tracks card names whose threshold table has been scored once

    total = 0
    for uid, card, ocean_uid in board:
        t = card.text.lower()
        # Updated Clownfish: continuous copy of attached ocean's ability text.
        if card.name.lower() == "clownfish":
            ocean_card = gs.card_db.get(ocean_uid)
            if ocean_card is not None:
                t = f"{t} | {ocean_card.text.lower()}"
        pts = 0

        # Common conditional patterns.
        if "if sharing an ocean with a goliath grouper" in t:
            same = cards_on_same_ocean(ocean_uid)
            if any(c.name.lower() == "goliath grouper" for c in same):
                pts += 8
        if "if sharing an ocean with a king salmon" in t:
            same = cards_on_same_ocean(ocean_uid)
            if any(c.name.lower() == "king salmon" for c in same):
                pts += 4
        if "if sharing the ocean with a cephalopod" in t:
            same = cards_on_same_ocean(ocean_uid)
            if any(c.species.lower() == "cephalopod" for c in same):
                m = re.search(r"\+(\d+)\s*if sharing the ocean with a cephalopod", t)
                pts += int(m.group(1)) if m else 5
        if "if sharing an ocean with baitfish" in t:
            same = cards_on_same_ocean(ocean_uid)
            if any(c.species.lower() in {"baitfish", "bait fish"} for c in same):
                m = re.search(r"\+(\d+)\s*if sharing an ocean with baitfish", t)
                pts += int(m.group(1)) if m else 4
        if "if sharing an ocean with a mahi mahi" in t:
            same = cards_on_same_ocean(ocean_uid)
            if any(c.name.lower() == "mahi mahi" for c in same):
                pts += 9
        if "if this is the only creature on this ocean" in t:
            same = cards_on_same_ocean(ocean_uid)
            if len(same) == 1:
                pts += 10
        if "if you have at least three cephalopods" in t:
            if species_count("cephalopod") >= 3:
                m = re.search(r"\+(\d+)\s*if you have at least three cephalopods", t)
                pts += int(m.group(1)) if m else 4
        if "if you have at least four cephalopods" in t:
            if species_count("cephalopod") >= 4:
                m = re.search(r"\+(\d+)\s*if you have at least four cephalopods", t)
                pts += int(m.group(1)) if m else 6
        if "if you have the most oceans" in t:
            if has_most_oceans:
                m = re.search(r"\+(\d+)\s*if you have the most oceans", t)
                pts += int(m.group(1)) if m else 6
        if "if you have the most animals" in t:
            if has_most_animals():
                pts += 4
        if "if you have all 8 oceans" in t:
            if ocean_count >= 8:
                m = re.search(r"\+(\d+)\s*if you have all 8 oceans", t)
                pts += int(m.group(1)) if m else 8
        pier_m = re.search(r"\+2 or \+(\d+) if you have the most piers", t)
        if pier_m:
            pts += int(pier_m.group(1)) if has_most_piers() else 2
        if "+1 per every two oceans you control" in t:
            pts += ocean_count // 2
        if "+5 per fully occupied ocean" in t:
            pts += 5 * fully_occupied_ocean_count()
        if "+2 per card attached" in t:
            attached = len(cards_on_same_ocean(ocean_uid))
            pts += 2 * attached
        if "kelp forest" in t and (">= 4" in t or "\u2265 4" in t or "at least 4" in t or "4 or more" in t):
            kelp_total = name_count("kelp forest")
            if kelp_total >= 4:
                if "per kelp forest" in t:
                    pts += 5 * kelp_total
                else:
                    pts += 5

        # Generic "+N per X" patterns.
        if "+2 per bird" in t:
            pts += 2 * species_count("bird")
        if "+3 per bird" in t:
            pts += 3 * species_count("bird")
        if "+1 per crustacean" in t:
            pts += species_count("crustacean")
        if "+2 per crustacean" in t:
            pts += 2 * species_count("crustacean")
        if "+3 per crustacean" in t:
            pts += 3 * species_count("crustacean")
        if "+2 per coral that is attached to a coral reef" in t:
            pts += 2 * coral_attached_to_coral_reef_count()
        if "+1 per coral" in t and "attached to a coral reef" not in t:
            pts += species_count("coral")
        if "+2 per coral" in t and "attached to a coral reef" not in t:
            pts += 2 * species_count("coral")
        if "+5 per coral" in t:
            pts += 5 * species_count("coral")
        if "+5 per invertebrate" in t:
            pts += 5 * species_count("invertebrate")
        if "+1 per invertebrate" in t:
            pts += species_count("invertebrate")
        if "+3 per baitfish" in t:
            pts += 3 * species_count("baitfish")
        if "+4 per baitfish" in t:
            pts += 4 * species_count("baitfish")
        if "+1 per game fish" in t:
            pts += species_count("game fish")
        if "+4 per cephalopod" in t:
            pts += 4 * species_count("cephalopod")
        if "+2 per mammal" in t:
            pts += 2 * species_count("mammal")
        if "+3 per mammal" in t:
            pts += 3 * species_count("mammal")
        if "+3 per coral" in t:
            pts += 3 * species_count("coral")
        if "+6 per mandarin goby" in t:
            pts += 6 * name_count("mandarin goby")
        if "+1 per uncharted animal" in t or "+1 per crosscurrent animal" in t:
            pts += species_count("n/a") + species_count("uncharted") + species_count("crosscurrent")
        if "+3 per invertebrate" in t:
            pts += 3 * species_count("invertebrate")
        if "+2 per n/a animal" in t or "+2 per uncharted animal" in t or "+2 per crosscurrent animal" in t:
            pts += 2 * (species_count("n/a") + species_count("uncharted") + species_count("crosscurrent"))
        if "+3 per n/a animal" in t or "+3 per uncharted animal" in t or "+3 per crosscurrent animal" in t:
            pts += 3 * (species_count("n/a") + species_count("uncharted") + species_count("crosscurrent"))
        if "+2 per mahi mahi" in t:
            pts += 2 * name_count("mahi mahi")
        if "+2 per matching symbol" in t:
            sym = normalize_symbol(card.symbol)
            if sym not in {"", "n/a"}:
                pts += 2 * sum(1 for c in non_ocean_cards if normalize_symbol(c.symbol) == sym and c.uid != card.uid)
        if "+10 per each mahi mahi you control" in t:
            pts += 10 * name_count("mahi mahi")
        if "+9 per each mahi mahi you control" in t:
            pts += 9 * name_count("mahi mahi")
        if "+3 per yellowfin tuna" in t:
            pts += 3 * name_count("yellowfin tuna")
        if "+2 per each unique symbol attached to this ocean" in t:
            same = cards_on_same_ocean(ocean_uid)
            syms = {normalize_symbol(c.symbol) for c in same if normalize_symbol(c.symbol) not in {"", "n/a"}}
            pts += 2 * len(syms)
        if "+2 per each unique species attached to this ocean" in t:
            same = cards_on_same_ocean(ocean_uid)
            spp = {c.species.lower() for c in same if c.species.strip()}
            pts += 2 * len(spp)

        # Threshold table cards.
        if "different species of baitfish" in t:
            # Chart card: one total score for the whole set, not per-card.
            if "baitfish_species_chart" not in count_table_applied:
                count_table_applied.add("baitfish_species_chart")
                kinds = {c.name.lower() for c in non_ocean_cards if c.species.lower() == "baitfish"}
                pts += value_from_threshold_table(t, len(kinds))
        elif card.name.lower() == "coral reef":
            # Coral Reef table is a global count score, not per-Coral-Reef multiplier.
            if not coral_reef_table_applied:
                pts += value_from_threshold_table(t, coral_reef_count_total)
                coral_reef_table_applied = True
        elif re.search(r"\d+\s*=\s*\d+", t):
            # Table score is a global bracket (e.g. 2 Mantis Shrimps = 15 total, not 30).
            # Clownfish on a Coral Reef is already counted in coral_reef_count_total — skip.
            if (card.name.lower() == "clownfish"
                    and gs.card_db.get(ocean_uid) is not None
                    and gs.card_db.get(ocean_uid).name.lower() == "coral reef"):
                pass
            else:
                card_name_key = card.name.lower()
                if card_name_key not in count_table_applied:
                    count_table_applied.add(card_name_key)
                    pts += value_from_threshold_table(t, name_count(card.name))

        # Flat +N pieces (exclude conditional/per-table text).
        for chunk in [x.strip() for x in t.split("|")]:
            if not chunk.startswith("+"):
                continue
            if any(w in chunk for w in [" if ", " per ", " or ", " most ", " only ", " at least ", " every "]):
                continue
            m = re.match(r"\+(\d+)\b", chunk)
            if m:
                pts += int(m.group(1))

        total += pts

    return total


def full_score_breakdown(gs: GameState, player: PlayerState) -> Dict[str, Any]:
    """
    Per-card score breakdown mirroring the final_points() calculation exactly.
    Returns card_rows (matching what the web client reads) with per-component
    explanations of why each card earned its points.
    final_points() remains the authoritative total; this function only explains it.
    """
    try:
        return _full_score_breakdown_impl(gs, player)
    except Exception:
        try:
            total = int(final_points(gs, player))
        except Exception:
            total = int(getattr(player, "score", 0))
        return {"total": total, "card_rows": [], "error": "breakdown_failed"}


def _full_score_breakdown_impl(gs: GameState, player: PlayerState) -> Dict[str, Any]:
    """Internal implementation — mirrors final_points() and captures per-card components."""
    # Build same board list as final_points: (uid, CardDef, ocean_uid)
    board: List[Tuple[int, CardDef, int]] = []
    for ocean_uid in player.board_oceans:
        ocean = gs.card_db.get(ocean_uid)
        if ocean is None:
            continue
        board.append((ocean_uid, ocean, ocean_uid))
        slots = player.ocean_slots.get(ocean_uid)
        if not isinstance(slots, OceanSlots):
            continue
        for uid in slots.all_cards():
            face = gs.card_db.get(uid)
            if face is None:
                continue
            board.append((uid, face, ocean_uid))

    if not board:
        return {"total": 0, "card_rows": []}

    all_cards = [c for _, c, _ in board]
    non_ocean_cards = [c for c in all_cards if c.direction.strip().lower() != "n/a"]

    ocean_count = len(player.board_oceans)
    other_ocean_counts = [len(p.board_oceans) for p in gs.players if p is not player]
    has_most_oceans = ocean_count >= max(other_ocean_counts) if other_ocean_counts else True

    def _has_most_piers() -> bool:
        def pier_count(p: PlayerState) -> int:
            return sum(1 for uid in p.board_oceans
                       if (gs.card_db.get(uid) is not None and
                           gs.card_db[uid].name.lower() == "pier"))
        my = pier_count(player)
        others = [pier_count(p) for p in gs.players if p is not player]
        return my >= max(others) if others else True

    def _count_animals(owner: PlayerState) -> int:
        n = 0
        for o_uid in owner.board_oceans:
            sl = owner.ocean_slots.get(o_uid)
            if not sl:
                continue
            for uid in sl.all_cards():
                c = gs.card_db.get(uid)
                if c is not None and c.direction.strip().lower() != "n/a":
                    n += 1
        return n

    def _has_most_animals() -> bool:
        my = _count_animals(player)
        others = [_count_animals(p) for p in gs.players if p is not player]
        return my >= max(others) if others else True

    def _fully_occupied_ocean_count() -> int:
        full = 0
        for o_uid in player.board_oceans:
            sl = player.ocean_slots.get(o_uid)
            if sl and sl.up and sl.down and sl.left and sl.right:
                full += 1
        return full

    def _coral_on_reef_count() -> int:
        n = 0
        for o_uid in player.board_oceans:
            ocean = gs.card_db.get(o_uid)
            if ocean is None or ocean.name.lower() != "coral reef":
                continue
            sl = player.ocean_slots.get(o_uid)
            if sl:
                for uid in sl.all_cards():
                    c = gs.card_db.get(uid)
                    if c is not None and c.species.lower() == "coral":
                        n += 1
        return n

    def _species_count(spec: str) -> int:
        s = spec.lower()
        return sum(1 for c in non_ocean_cards if c.species.lower() == s)

    def _name_count(name: str) -> int:
        n = name.lower()
        return sum(1 for c in all_cards if c.name.lower() == n)

    def _same_ocean_cards(target_ocean_uid: int) -> List[CardDef]:
        return [c for _, c, o_uid in board
                if o_uid == target_ocean_uid and c.direction.strip().lower() != "n/a"]

    def _threshold(text: str, value: int) -> int:
        pairs = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\+?\s*=\s*(\d+)", text)]
        if not pairs:
            return 0
        table = {need: pts for need, pts in pairs}
        eligible = [need for need in table if need <= value]
        return table[max(eligible)] if eligible else 0

    coral_reef_total = _name_count("coral reef") + sum(
        1 for uid, c, o_uid in board
        if c.name.lower() == "clownfish"
        and gs.card_db.get(o_uid) is not None
        and gs.card_db.get(o_uid).name.lower() == "coral reef"
    )
    coral_reef_table_applied = False
    breakdown_count_table_applied: set = set()

    card_rows: List[Dict[str, Any]] = []

    for uid, card, ocean_uid in board:
        t = card.text.lower()
        # Clownfish copies its ocean's text
        if card.name.lower() == "clownfish":
            ocean_card = gs.card_db.get(ocean_uid)
            if ocean_card is not None:
                t = f"{t} | {ocean_card.text.lower()}"

        card_pts = 0
        components: List[Dict[str, object]] = []

        def add(n: int, reason: str) -> None:
            nonlocal card_pts
            if n != 0:
                card_pts += n
                components.append({"points": int(n), "reason": str(reason)})

        # ── Conditional bonus patterns ──────────────────────────────────────
        if "if sharing an ocean with a goliath grouper" in t:
            same = _same_ocean_cards(ocean_uid)
            if any(c.name.lower() == "goliath grouper" for c in same):
                add(8, "sharing ocean with Goliath Grouper")

        if "if sharing an ocean with a king salmon" in t:
            same = _same_ocean_cards(ocean_uid)
            if any(c.name.lower() == "king salmon" for c in same):
                add(4, "sharing ocean with King Salmon")

        if "if sharing the ocean with a cephalopod" in t:
            same = _same_ocean_cards(ocean_uid)
            if any(c.species.lower() == "cephalopod" for c in same):
                m2 = re.search(r"\+(\d+)\s*if sharing the ocean with a cephalopod", t)
                add(int(m2.group(1)) if m2 else 5, "sharing ocean with a Cephalopod")

        if "if sharing an ocean with baitfish" in t:
            same = _same_ocean_cards(ocean_uid)
            if any(c.species.lower() in {"baitfish", "bait fish"} for c in same):
                m2 = re.search(r"\+(\d+)\s*if sharing an ocean with baitfish", t)
                add(int(m2.group(1)) if m2 else 4, "sharing ocean with Baitfish")

        if "if sharing an ocean with a mahi mahi" in t:
            same = _same_ocean_cards(ocean_uid)
            if any(c.name.lower() == "mahi mahi" for c in same):
                add(9, "sharing ocean with Mahi Mahi")

        if "if this is the only creature on this ocean" in t:
            same = _same_ocean_cards(ocean_uid)
            if len(same) == 1:
                add(10, "only creature on this ocean")

        if "if you have at least three cephalopods" in t:
            cnt = _species_count("cephalopod")
            if cnt >= 3:
                m2 = re.search(r"\+(\d+)\s*if you have at least three cephalopods", t)
                add(int(m2.group(1)) if m2 else 4, f"at least 3 Cephalopods ({cnt} total)")

        if "if you have at least four cephalopods" in t:
            cnt = _species_count("cephalopod")
            if cnt >= 4:
                m2 = re.search(r"\+(\d+)\s*if you have at least four cephalopods", t)
                add(int(m2.group(1)) if m2 else 6, f"at least 4 Cephalopods ({cnt} total)")

        if "if you have the most oceans" in t and has_most_oceans:
            m2 = re.search(r"\+(\d+)\s*if you have the most oceans", t)
            add(int(m2.group(1)) if m2 else 6, f"most oceans ({ocean_count})")

        if "if you have the most animals" in t and _has_most_animals():
            add(4, "most animals")

        if "if you have all 8 oceans" in t and ocean_count >= 8:
            m2 = re.search(r"\+(\d+)\s*if you have all 8 oceans", t)
            add(int(m2.group(1)) if m2 else 8, "all 8 oceans")

        pier_m2 = re.search(r"\+2 or \+(\d+) if you have the most piers", t)
        if pier_m2:
            bonus_pts = int(pier_m2.group(1))
            add(bonus_pts if _has_most_piers() else 2,
                f"most piers (+{bonus_pts})" if _has_most_piers() else "base pier score (+2)")

        if "+1 per every two oceans you control" in t:
            n = ocean_count // 2
            if n:
                add(n, f"+1 per every 2 oceans ({ocean_count} oceans → {n} pts)")

        if "+5 per fully occupied ocean" in t:
            foc = _fully_occupied_ocean_count()
            if foc:
                add(5 * foc, f"+5 per fully occupied ocean × {foc}")

        if "+2 per card attached" in t:
            same = _same_ocean_cards(ocean_uid)
            if same:
                add(2 * len(same), f"+2 per card on same ocean × {len(same)}")

        if "kelp forest" in t and (">= 4" in t or "\u2265 4" in t or "at least 4" in t or "4 or more" in t):
            kt = _name_count("kelp forest")
            if kt >= 4:
                if "per kelp forest" in t:
                    add(5 * kt, f"+5 per Kelp Forest × {kt}")
                else:
                    add(5, f"4+ Kelp Forests bonus")

        # ── Per-species / per-name patterns ─────────────────────────────────
        def _per(pattern: str, n_pts: int, label: str) -> None:
            if pattern in t:
                cnt = _species_count(label.lower()) if label.lower() not in {"mahi mahi", "yellowfin tuna", "mandarin goby"} else _name_count(label.lower())
                if cnt:
                    add(n_pts * cnt, f"+{n_pts} per {label} × {cnt}")

        if "+2 per bird" in t:
            cnt = _species_count("bird"); cnt and add(2 * cnt, f"+2 per Bird × {cnt}")
        if "+3 per bird" in t:
            cnt = _species_count("bird"); cnt and add(3 * cnt, f"+3 per Bird × {cnt}")
        if "+1 per crustacean" in t:
            cnt = _species_count("crustacean"); cnt and add(cnt, f"+1 per Crustacean × {cnt}")
        if "+2 per crustacean" in t:
            cnt = _species_count("crustacean"); cnt and add(2 * cnt, f"+2 per Crustacean × {cnt}")
        if "+3 per crustacean" in t:
            cnt = _species_count("crustacean"); cnt and add(3 * cnt, f"+3 per Crustacean × {cnt}")
        if "+2 per coral that is attached to a coral reef" in t:
            cnt = _coral_on_reef_count(); cnt and add(2 * cnt, f"+2 per Coral on Coral Reef × {cnt}")
        if "+1 per coral" in t and "attached to a coral reef" not in t:
            cnt = _species_count("coral"); cnt and add(cnt, f"+1 per Coral × {cnt}")
        if "+2 per coral" in t and "attached to a coral reef" not in t:
            cnt = _species_count("coral"); cnt and add(2 * cnt, f"+2 per Coral × {cnt}")
        if "+5 per coral" in t:
            cnt = _species_count("coral"); cnt and add(5 * cnt, f"+5 per Coral × {cnt}")
        if "+5 per invertebrate" in t:
            cnt = _species_count("invertebrate"); cnt and add(5 * cnt, f"+5 per Invertebrate × {cnt}")
        if "+1 per invertebrate" in t:
            cnt = _species_count("invertebrate"); cnt and add(cnt, f"+1 per Invertebrate × {cnt}")
        if "+3 per invertebrate" in t:
            cnt = _species_count("invertebrate"); cnt and add(3 * cnt, f"+3 per Invertebrate × {cnt}")
        if "+3 per baitfish" in t:
            cnt = _species_count("baitfish"); cnt and add(3 * cnt, f"+3 per Baitfish × {cnt}")
        if "+4 per baitfish" in t:
            cnt = _species_count("baitfish"); cnt and add(4 * cnt, f"+4 per Baitfish × {cnt}")
        if "+1 per game fish" in t:
            cnt = _species_count("game fish"); cnt and add(cnt, f"+1 per Game Fish × {cnt}")
        if "+4 per cephalopod" in t:
            cnt = _species_count("cephalopod"); cnt and add(4 * cnt, f"+4 per Cephalopod × {cnt}")
        if "+2 per mammal" in t:
            cnt = _species_count("mammal"); cnt and add(2 * cnt, f"+2 per Mammal × {cnt}")
        if "+3 per mammal" in t:
            cnt = _species_count("mammal"); cnt and add(3 * cnt, f"+3 per Mammal × {cnt}")
        if "+3 per coral" in t:
            cnt = _species_count("coral"); cnt and add(3 * cnt, f"+3 per Coral × {cnt}")
        if "+6 per mandarin goby" in t:
            cnt = _name_count("mandarin goby"); cnt and add(6 * cnt, f"+6 per Mandarin Goby × {cnt}")
        if "+1 per uncharted animal" in t or "+1 per crosscurrent animal" in t:
            cnt = _species_count("n/a") + _species_count("uncharted") + _species_count("crosscurrent")
            cnt and add(cnt, f"+1 per Crosscurrent Animal × {cnt}")
        if "+2 per n/a animal" in t or "+2 per uncharted animal" in t or "+2 per crosscurrent animal" in t:
            cnt = _species_count("n/a") + _species_count("uncharted") + _species_count("crosscurrent")
            cnt and add(2 * cnt, f"+2 per Crosscurrent Animal × {cnt}")
        if "+3 per n/a animal" in t or "+3 per uncharted animal" in t or "+3 per crosscurrent animal" in t:
            cnt = _species_count("n/a") + _species_count("uncharted") + _species_count("crosscurrent")
            cnt and add(3 * cnt, f"+3 per Crosscurrent Animal × {cnt}")
        if "+2 per mahi mahi" in t:
            cnt = _name_count("mahi mahi")
            cnt and add(2 * cnt, f"+2 per Mahi Mahi × {cnt}")
        if "+2 per matching symbol" in t:
            sym = normalize_symbol(card.symbol)
            if sym not in {"", "n/a"}:
                cnt = sum(1 for c in non_ocean_cards
                          if normalize_symbol(c.symbol) == sym and c.uid != card.uid)
                cnt and add(2 * cnt, f"+2 per matching symbol ({sym}) × {cnt}")
        if "+10 per each mahi mahi you control" in t:
            cnt = _name_count("mahi mahi"); cnt and add(10 * cnt, f"+10 per Mahi Mahi × {cnt}")
        if "+9 per each mahi mahi you control" in t:
            cnt = _name_count("mahi mahi"); cnt and add(9 * cnt, f"+9 per Mahi Mahi × {cnt}")
        if "+3 per yellowfin tuna" in t:
            cnt = _name_count("yellowfin tuna"); cnt and add(3 * cnt, f"+3 per Yellowfin Tuna × {cnt}")
        if "+2 per each unique symbol attached to this ocean" in t:
            same = _same_ocean_cards(ocean_uid)
            syms = {normalize_symbol(c.symbol) for c in same if normalize_symbol(c.symbol) not in {"", "n/a"}}
            syms and add(2 * len(syms), f"+2 per unique symbol on ocean × {len(syms)}")
        if "+2 per each unique species attached to this ocean" in t:
            same = _same_ocean_cards(ocean_uid)
            spp = {c.species.lower() for c in same if c.species.strip()}
            spp and add(2 * len(spp), f"+2 per unique species on ocean × {len(spp)}")

        # ── Threshold table cards ─────────────────────────────────────────────
        if "different species of baitfish" in t:
            # Chart card: one total score for the whole set, not per-card.
            if "baitfish_species_chart" not in breakdown_count_table_applied:
                breakdown_count_table_applied.add("baitfish_species_chart")
                kinds = {c.name.lower() for c in non_ocean_cards if c.species.lower() == "baitfish"}
                n = _threshold(t, len(kinds))
                n and add(n, f"{len(kinds)} different Baitfish species → {n} pts")
        elif card.name.lower() == "coral reef":
            if not coral_reef_table_applied:
                n = _threshold(t, coral_reef_total)
                n and add(n, f"{coral_reef_total} Coral Reef(s) → {n} pts")
                coral_reef_table_applied = True
        elif re.search(r"\d+\s*=\s*\d+", t):
            # Clownfish on a Coral Reef is already counted in coral_reef_total — skip.
            if (card.name.lower() == "clownfish"
                    and gs.card_db.get(ocean_uid) is not None
                    and gs.card_db.get(ocean_uid).name.lower() == "coral reef"):
                pass
            else:
                bd_key = card.name.lower()
                if bd_key not in breakdown_count_table_applied:
                    breakdown_count_table_applied.add(bd_key)
                    cnt = _name_count(card.name)
                    n = _threshold(t, cnt)
                    n and add(n, f"{cnt} × {card.name} → {n} pts (table)")

        # ── Flat +N values (one-time bonuses, no conditions) ─────────────────
        for chunk in [x.strip() for x in t.split("|")]:
            if not chunk.startswith("+"):
                continue
            if any(w in chunk for w in [" if ", " per ", " or ", " most ", " only ", " at least ", " every "]):
                continue
            m2 = re.match(r"\+(\d+)\b", chunk)
            if m2:
                add(int(m2.group(1)), f"flat +{m2.group(1)}")

        is_ocean_card = card.direction.strip().lower() == "n/a"
        card_rows.append({
            "card_uid": int(uid),
            "card_name": card.name,
            "is_ocean": is_ocean_card,
            "ocean_uid": int(ocean_uid),
            "total": card_pts,
            "components": components,
        })

    # Always use final_points() as the authoritative total so the displayed
    # number is always correct even if the breakdown has edge cases.
    try:
        authoritative_total = int(final_points(gs, player))
    except Exception:
        authoritative_total = sum(row["total"] for row in card_rows)

    return {
        "total": authoritative_total,
        "card_rows": card_rows,
    }


def mandarin_goby_score_breakdown(
    gs: GameState,
    player: PlayerState,
    precomputed_full: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Dedicated Mandarin Goby table breakdown used by multiplayer UI.
    Table: 1=0, 2=15, 3=30, 4+=80.
    """
    board_cards: List[CardDef] = []
    for ocean_uid in player.board_oceans:
        slots = player.ocean_slots.get(ocean_uid)
        if not slots:
            continue
        for uid in slots.all_cards():
            c = gs.card_db.get(uid)
            if c is not None:
                board_cards.append(c)

    goby_count = sum(1 for c in board_cards if c.name.strip().lower() == "mandarin goby")
    if goby_count <= 1:
        points = 0
    elif goby_count == 2:
        points = 15
    elif goby_count == 3:
        points = 30
    else:
        points = 80

    return {
        "count": int(goby_count),
        "points": int(points),
        "table": {"1": 0, "2": 15, "3": 30, "4+": 80},
        "total_score": int((precomputed_full or {}).get("total", final_points(gs, player))),
    }


def board_cards_label(gs: GameState, player: PlayerState) -> str:
    cards: List[str] = []
    for uid in player.board_oceans:
        c = gs.card_db.get(uid)
        if c is None:
            cards.append(f"{uid}:[missing](Ocean)")
            continue
        cards.append(f"{uid}:{c.name}(Ocean)")
    for slots in player.ocean_slots.values():
        for uid in slots.all_cards():
            c = gs.card_db.get(uid)
            if c is None:
                cards.append(f"{uid}:[missing]")
                continue
            cards.append(f"{uid}:{c.name}")
    return ", ".join(cards) if cards else "(no cards)"


def render_final_board_html(gs: GameState, ms: MatchState, out_path: str, title: str = "Fish Game Final Board") -> None:
    standings = sorted(gs.players, key=lambda p: final_points(gs, p), reverse=True)

    def esc(s: str) -> str:
        return html.escape(s, quote=True)

    player_blocks: List[str] = []
    for p in standings:
        score = final_points(gs, p)
        oceans_html: List[str] = []
        for ocean_uid in p.board_oceans:
            ocean = gs.card_db[ocean_uid]
            slots = p.ocean_slots[ocean_uid]
            def slot_cards(direction: str) -> str:
                uids = slots.slot(direction)
                if not uids:
                    return "<div class='empty'>-</div>"
                parts = []
                for uid in uids:
                    c = gs.card_db[uid]
                    parts.append(f"<div class='card'>{esc(c.name)} <span class='meta'>#{uid} {esc(c.direction)}</span></div>")
                return "".join(parts)

            oceans_html.append(
                f"""
                <div class="ocean">
                  <div class="ocean-head">{esc(ocean.name)} <span class="meta">#{ocean_uid}</span></div>
                  <div class="grid">
                    <div class="cell"><div class="label">Up</div>{slot_cards("up")}</div>
                    <div class="cell"><div class="label">Left</div>{slot_cards("left")}</div>
                    <div class="cell center"><div class="label">Ocean</div><div class="card ocean-card">{esc(ocean.name)}</div></div>
                    <div class="cell"><div class="label">Right</div>{slot_cards("right")}</div>
                    <div class="cell"><div class="label">Down</div>{slot_cards("down")}</div>
                  </div>
                </div>
                """
            )

        if not oceans_html:
            oceans_html.append("<div class='empty-board'>No oceans played.</div>")

        player_blocks.append(
            f"""
            <section class="player">
              <h2>{esc(p.name)} <span class="score">Score: {score}</span></h2>
              {''.join(oceans_html)}
            </section>
            """
        )

    pool_cards = ", ".join(f"{esc(entry_short_label(ms, gs, uid))}" for uid in ms.pool) if ms.pool else "(empty)"
    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{esc(title)}</title>
  <style>
    body {{ font-family: 'Avenir Next', Arial, sans-serif; background: #f4fbff; color: #123; margin: 0; padding: 20px; }}
    h1 {{ margin: 0 0 8px 0; }}
    .meta {{ color: #567; font-size: 12px; }}
    .summary {{ background: #e7f4ff; padding: 10px 12px; border-radius: 10px; margin-bottom: 16px; }}
    .player {{ background: #fff; border: 1px solid #d9e8f4; border-radius: 12px; padding: 12px; margin-bottom: 14px; }}
    .score {{ font-size: 14px; color: #245; margin-left: 8px; }}
    .ocean {{ border: 1px dashed #c8ddee; border-radius: 10px; padding: 10px; margin: 10px 0; }}
    .ocean-head {{ font-weight: 700; margin-bottom: 8px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
    .cell {{ background: #f8fcff; border-radius: 8px; padding: 8px; min-height: 54px; }}
    .cell.center {{ background: #edf7ff; }}
    .label {{ font-size: 11px; text-transform: uppercase; color: #678; margin-bottom: 6px; }}
    .card {{ background: #ffffff; border: 1px solid #d6e6f3; border-radius: 6px; padding: 4px 6px; margin-bottom: 4px; font-size: 13px; }}
    .ocean-card {{ border-color: #7fb4d8; background: #dff2ff; font-weight: 600; }}
    .empty {{ color: #99a; font-size: 13px; }}
    .empty-board {{ color: #667; padding: 8px; }}
  </style>
</head>
<body>
  <h1>{esc(title)}</h1>
  <div class="summary">
    Deck remaining: {len(gs.deck)} |
    Pool cards: {len(ms.pool)} |
    Discard pile: {len(ms.discard_pile)}<br/>
    Pool: {pool_cards}
  </div>
  {''.join(player_blocks)}
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)


def run_match(
    card_db: Dict[int, CardDef],
    player_names: List[str],
    action_policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]],
    seed: int,
    max_turns: int,
    human_index: Optional[int] = None,
    human_indices: Optional[set[int]] = None,
    verbose: bool = False,
    verbose_state: bool = False,
    online_weights: Optional[Dict[str, float]] = None,
    online_learning_indices: Optional[set[int]] = None,
    online_lr: float = 0.03,
    online_state: Optional[Dict[str, object]] = None,
    online_state_path: Optional[str] = None,
    live_recorder: Optional[LiveRecorder] = None,
    player_archetype_profiles: Optional[List[Dict[str, Any]]] = None,
    hand_based_archetypes: bool = False,
    human_learning_boost: float = 1.0,
) -> Tuple[GameState, MatchState]:
    rng = random.Random(seed)
    web_control_mode = str(os.environ.get("FISH_WEB_CONTROL", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    pair_primary_to_faces, face_to_primary = build_non_ocean_pair_maps(card_db)
    deck, end_uid = build_deck_with_late_end_game(card_db, pair_primary_to_faces, face_to_primary, rng)

    players = [PlayerState(name) for name in player_names]
    gs = GameState(card_db=card_db, players=players, deck=deck)
    ms = MatchState(
        end_game_uid=end_uid,
        pair_primary_to_faces=pair_primary_to_faces,
        face_to_primary=face_to_primary,
    )

    human_idx_set: set[int] = set()
    if human_indices:
        for i in human_indices:
            if 0 <= i < len(players):
                human_idx_set.add(i)
    if human_index is not None and 0 <= human_index < len(players):
        human_idx_set.add(human_index)

    for i, p in enumerate(players):
        assign_runtime_playstyle(p, rng, is_human=(i in human_idx_set))
        p.flags["_allow_relocate_action"] = bool(i in human_idx_set)
        if web_control_mode and (i in human_idx_set):
            p.flags["_web_human"] = True

    start_game(gs, starting_hand=8, shuffle=False)
    perform_mulligans(gs, ms)
    _ = sanitize_runtime_state(gs, ms, action_policies=action_policies, max_notes=0)

    assigned_archetypes: List[Tuple[str, str, float]] = []
    if player_archetype_profiles:
        for i, p in enumerate(gs.players):
            if i < len(player_archetype_profiles) and isinstance(player_archetype_profiles[i], dict):
                p.flags["_ai_profile"] = dict(player_archetype_profiles[i])
                assigned_archetypes.append((p.name, str(player_archetype_profiles[i].get("label", "Profile")), 0.0))
    elif hand_based_archetypes:
        assigned_archetypes = assign_archetypes_from_opening_hands(gs, ms, human_index=human_index)

    assigned_families = assign_strategy_families_from_opening_hands(
        gs,
        ms,
        online_state if isinstance(online_state, dict) else None,
        human_idx_set,
        rng,
    )

    if live_recorder is not None:
        live_recorder.reset(gs, ms)
        for p in gs.players:
            live_recorder.event(f"Opening hand {p.name}: {short_entry_list(ms, gs, p.hand)}")
        live_recorder.snapshot(gs, ms, turn_number=0, note="after_opening_hands")

    if verbose_state:
        print("\n=== Opening Hands ===")
        for p in gs.players:
            entries = ", ".join(entry_short_label(ms, gs, uid) for uid in p.hand)
            print(f"{p.name}: {entries}")
        if assigned_archetypes:
            print("Opening-hand archetype picks:")
            for name, label, fit in assigned_archetypes:
                if hand_based_archetypes:
                    print(f"  {name}: {label} (fit {fit:.2f})")
                else:
                    print(f"  {name}: {label}")
        if assigned_families:
            print("Opening-hand strategy families:")
            for name, label, fit in assigned_families:
                print(f"  {name}: {label} (fit {fit:.2f})")

    turns = 0
    stalled_turns = 0
    sanitize_log_budget = 40
    move_histories: Dict[int, List[Dict[str, float]]] = {i: [] for i in range(len(gs.players))}
    move_signatures: Dict[int, List[str]] = {i: [] for i in range(len(gs.players))}

    # Crash-safe score helper used in logging throughout the loop and after it ends.
    def _safe_fp(pl: PlayerState) -> int:
        try:
            return int(final_points(gs, pl))
        except Exception:
            return int(getattr(pl, "score", 0))

    while True:
        if max_turns > 0 and turns >= max_turns:
            break
        if not gs.players:
            gs.log.append("Match ended: no players available.")
            if live_recorder is not None:
                live_recorder.event("Match ended: no players available.")
            break
        if not action_policies:
            gs.log.append("Match ended: no action policies available.")
            if live_recorder is not None:
                live_recorder.event("Match ended: no action policies available.")
            break
        sanitize_notes = sanitize_runtime_state(gs, ms, action_policies=action_policies, max_notes=8)
        if sanitize_notes:
            for note in sanitize_notes[:2]:
                gs.log.append(f"State sanitizer: {note}")
            if live_recorder is not None and sanitize_log_budget > 0:
                for note in sanitize_notes[:3]:
                    if sanitize_log_budget <= 0:
                        break
                    live_recorder.event(f"State sanitizer: {note}")
                    sanitize_log_budget -= 1
                extra = len(sanitize_notes) - 3
                if extra > 0 and sanitize_log_budget > 0:
                    live_recorder.event(f"State sanitizer: +{extra} additional fixes")
                    sanitize_log_budget -= 1
        p = gs.current_player()
        turn_state = TurnState()
        made_action_this_turn = False
        if live_recorder is not None:
            live_recorder.event(f"=== Turn {turns + 1}: {p.name} ===")
            live_recorder.event(f"Pool: {short_entry_list(ms, gs, ms.pool)}")
            live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"turn_start:{p.name}")
        if verbose:
            print(f"\n=== Turn {turns + 1}: {p.name} ===")
        if human_realism_enabled():
            for mem_p in gs.players:
                update_visible_memory_for_player(gs, ms, mem_p)
        if verbose_state:
            print("Hands:")
            for hp in gs.players:
                entries = ", ".join(entry_short_label(ms, gs, uid) for uid in hp.hand)
                print(f"  {hp.name}: {entries}")
            if ms.pool:
                print("Pool: " + ", ".join(entry_short_label(ms, gs, uid) for uid in ms.pool))
            else:
                print("Pool: (empty)")

        # Per-turn check: if deck is low and any player holds the end-game card, trigger end game now.
        if ms.end_game_uid is not None and not ms.end_game_triggered and len(gs.deck) < 10:
            for _check_p in gs.players:
                if ms.end_game_uid in _check_p.hand:
                    _check_p.hand.remove(ms.end_game_uid)
                    ms.discard_pile.append(ms.end_game_uid)
                    trigger_end_game(ms, gs)
                    gs.log.append(
                        f"End game triggered: {_check_p.name} held end-game card in hand "
                        f"with {len(gs.deck)} cards remaining in deck."
                    )
                    if live_recorder is not None:
                        live_recorder.event(
                            f"End game triggered: {_check_p.name} held end-game card in hand "
                            f"with {len(gs.deck)} cards remaining in deck."
                        )
                    break

        # One action per turn by default; abilities may grant extra actions.
        undo_occurred = False
        action_budget = 1
        while action_budget > 0:
            was_free_only = bool(p.flags.get("_free_action_only", False))
            policy_index = int(gs.turn_index)
            if policy_index < 0 or policy_index >= len(action_policies):
                policy_index = policy_index % len(action_policies)
            policy = action_policies[policy_index]
            chosen = policy(gs, ms, p)
            if chosen is not None and getattr(chosen, 'kind', None) == 'undo':
                undo_occurred = True
                break
            is_human_turn = policy_index in human_idx_set
            interactive_human_turn = is_human_turn and (not web_control_mode)
            if chosen is None:
                # fallback to legal draw if possible
                legal = legal_actions(gs, ms, p, include_draw=True)
                if not legal:
                    break
                chosen = next((a for a in legal if a.kind == "draw"), None)
                if chosen is None:
                    break

            if verbose and (not is_human_turn):
                print(f"{p.name} attempts: {describe_action(gs, ms, chosen)}")
            if live_recorder is not None:
                live_recorder.event(f"{p.name} attempts: {describe_action(gs, ms, chosen)}")

            picker = choose_payment_human if interactive_human_turn else choose_payment_ai
            do_online = online_weights is not None and (
                online_learning_indices is None or gs.turn_index in online_learning_indices
            )
            effective_online_lr = online_lr * (human_learning_boost if is_human_turn else 1.0)
            online_species_map = (
                online_state.get("species_synergy", {}) if isinstance(online_state, dict) else {}
            )
            online_synergy_map = (
                online_state.get("synergy", {}) if isinstance(online_state, dict) else {}
            )
            online_same_ocean_map = (
                online_state.get("same_ocean_synergy", {}) if isinstance(online_state, dict) else {}
            )
            try:
                had_play_option = any(a.kind != "draw" for a in legal_actions(gs, ms, p, include_draw=True))
            except Exception:
                had_play_option = False
            if do_online:
                try:
                    before_score = final_points(gs, p)
                except Exception:
                    before_score = int(getattr(p, "score", 0))
                try:
                    chosen_feats = action_features(
                        gs,
                        ms,
                        p,
                        chosen,
                        synergy_map=online_synergy_map,
                        species_map=online_species_map,
                        same_ocean_map=online_same_ocean_map,
                    )
                except Exception:
                    chosen_feats = None
            else:
                before_score = 0
                chosen_feats = None
            executed_action = chosen
            executed_feats = chosen_feats
            fail_messages: List[str] = []
            ok = apply_action(gs, ms, p, chosen, turn_state, picker, verbose=verbose, fail_reason=fail_messages)
            if not ok:
                if verbose:
                    reason = fail_messages[-1] if fail_messages else "unknown reason"
                    print(f"{p.name} attempt failed: {reason}")
                if live_recorder is not None:
                    reason = fail_messages[-1] if fail_messages else "unknown reason"
                    live_recorder.event(f"{p.name} attempt failed: {reason}")
                # Fallback: force a legal draw action if available.
                fallback = None
                legal_now = legal_actions(gs, ms, p, include_draw=True)
                for cand in legal_now:
                    if cand.kind == "draw":
                        fallback = cand
                        break
                if fallback is not None and chosen.kind != "draw":
                    if verbose:
                        print(f"{p.name} action failed; forcing draw fallback.")
                        print(f"{p.name} fallback: {describe_action(gs, ms, fallback)}")
                    if live_recorder is not None:
                        live_recorder.event(f"{p.name} action failed; forcing draw fallback.")
                        live_recorder.event(f"{p.name} fallback: {describe_action(gs, ms, fallback)}")
                    if do_online:
                        executed_action = fallback
                        try:
                            executed_feats = action_features(
                                gs,
                                ms,
                                p,
                                fallback,
                                synergy_map=online_synergy_map,
                                species_map=online_species_map,
                                same_ocean_map=online_same_ocean_map,
                            )
                        except Exception:
                            executed_feats = None
                    fb_fail_messages: List[str] = []
                    ok = apply_action(gs, ms, p, fallback, turn_state, picker, verbose=verbose, fail_reason=fb_fail_messages)
                if not ok:
                    if verbose:
                        fb_reason = fb_fail_messages[-1] if 'fb_fail_messages' in locals() and fb_fail_messages else "unknown reason"
                        print(f"{p.name} action failed: {fb_reason}")
                    if live_recorder is not None:
                        fb_reason = fb_fail_messages[-1] if 'fb_fail_messages' in locals() and fb_fail_messages else "unknown reason"
                        live_recorder.event(f"{p.name} action failed: {fb_reason}")
                    break
            _ = sanitize_runtime_state(gs, ms, action_policies=action_policies, max_notes=0)

            if do_online and executed_feats is not None:
                try:
                    after_score = final_points(gs, p)
                except Exception:
                    after_score = int(getattr(p, "score", 0))
                reward = float(after_score - before_score)
                pressure = endgame_pressure(len(gs.deck))
                future_v = float(executed_feats.get("future_value", 0.0))
                setup_v = (
                    float(executed_feats.get("plan_fit_bonus", 0.0))
                    + float(executed_feats.get("synergy_bonus", 0.0))
                    + float(executed_feats.get("species_bonus", 0.0))
                    + float(executed_feats.get("same_ocean_bonus", 0.0))
                    + float(executed_feats.get("stack_bonus", 0.0))
                )
                if executed_action.kind == "draw":
                    reward -= 0.08
                    if had_play_option:
                        reward -= 0.62 if pressure >= 0.68 else 0.24
                    reward += (1.0 - 0.50 * pressure) * 0.16 * max(0.0, future_v)
                    reward += (1.0 - pressure) * 0.04 * max(0.0, setup_v)
                else:
                    reward += (1.0 - pressure) * (0.30 * max(0.0, future_v) + 0.08 * max(0.0, setup_v))
                    reward += (0.30 + 0.70 * pressure) * 0.05 * max(0.0, float(executed_feats.get("immediate_delta", 0.0)))
                if reward > 8.0:
                    reward = 8.0
                elif reward < -8.0:
                    reward = -8.0
                online_update_weights(online_weights, executed_feats, reward, lr=effective_online_lr)
                move_histories.setdefault(int(gs.turn_index), []).append(dict(executed_feats))
                if online_state is not None:
                    online_state["move_updates"] = int(online_state.get("move_updates", 0)) + 1
                    if online_state_path:
                        save_brain(online_state, online_state_path)

            executed_sig = action_signature(gs, ms, p, executed_action)
            p.flags["_last_sig"] = executed_sig
            if do_online:
                move_signatures.setdefault(int(gs.turn_index), []).append(executed_sig)

            if live_recorder is not None and hasattr(live_recorder, "executed_action"):
                try:
                    live_recorder.executed_action(
                        gs,
                        ms,
                        int(gs.turn_index),
                        p.name,
                        copy.deepcopy(executed_action),
                        int(turns + 1),
                    )
                except Exception:
                    pass

            made_action_this_turn = True
            update_tempo_after_action(p, executed_action, turn_state)
            if live_recorder is not None:
                live_recorder.event(f"{p.name} executed: {describe_action(gs, ms, executed_action)}")
                live_recorder.event(f"Pool now: {short_entry_list(ms, gs, ms.pool)}")
                live_recorder.event(f"Deck remaining: {len(gs.deck)}")
                live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"post_action:{p.name}")
            if was_free_only:
                p.flags["_free_action_only"] = False
            if turn_state.force_end_turn:
                if verbose:
                    print(f"{p.name}'s turn ends after moving a card.")
                if live_recorder is not None:
                    live_recorder.event(f"{p.name}'s turn ends after moving a card.")
                action_budget = 0
                break
            replay_count = pending_replay_actions(p)
            if replay_count > 0:
                picked = maybe_apply_replay_pickup(
                    gs,
                    ms,
                    p,
                    turn_state,
                    is_human_turn=interactive_human_turn,
                    verbose=verbose,
                )
                if picked and live_recorder is not None:
                    live_recorder.event(f"{p.name} uses replay pickup (1 card).")
                replay_added = consume_replay_actions(p)
                action_budget += replay_added
                if replay_added > 0:
                    p.flags["_replay_turn_next"] = True
                if verbose:
                    if replay_added == 1:
                        print(f"{p.name} gets an extra action.")
                    else:
                        print(f"{p.name} gets {replay_added} extra actions.")
            if turn_state.free_followups > 0:
                action_budget += turn_state.free_followups
                p.flags["_free_action_only"] = True
                if verbose:
                    print(f"{p.name} gets {turn_state.free_followups} restricted follow-up action(s) for free-play ability.")
                turn_state.free_followups = 0
            if has_multi_play_window(p):
                action_budget += 1
            action_budget -= 1

        if undo_occurred:
            continue

        # Tarpon interactive discard loop (web human only).
        # _tarpon_discard_active is set by _execute_main_pattern when a web human plays Tarpon.
        # We loop here letting the player pick cards to discard, then draw back that many.
        if p.flags.get("_tarpon_discard_active") and (gs.turn_index in human_idx_set) and web_control_mode:
            tarpon_discarded = 0
            t_policy = action_policies[gs.turn_index % len(action_policies)]
            while p.flags.get("_tarpon_discard_active"):
                t_action = t_policy(gs, ms, p)
                if t_action is None:
                    p.flags["_tarpon_discard_active"] = False
                    break
                if t_action.kind == "end_turn":
                    p.flags["_tarpon_discard_active"] = False
                    break
                if t_action.kind == "discard_to_pool":
                    t_ok = apply_action(gs, ms, p, t_action, turn_state, choose_payment_ai, verbose=verbose)
                    if t_ok:
                        tarpon_discarded += 1
                        if live_recorder is not None:
                            try:
                                live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"tarpon_discard:{p.name}")
                            except Exception:
                                pass
                else:
                    p.flags["_tarpon_discard_active"] = False
                    break
            p.flags["_tarpon_discard_active"] = False
            if tarpon_discarded > 0:
                drew_tarpon = draw_from_deck(gs, ms, p, min(tarpon_discarded, len(gs.deck)))
                gs.log.append(f"{p.name} draws {len(drew_tarpon)} from Tarpon discard-and-draw.")
                if live_recorder is not None:
                    try:
                        live_recorder.event(f"{p.name} draws back {len(drew_tarpon)} card(s) from Tarpon effect.")
                        live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"tarpon_draw:{p.name}")
                    except Exception:
                        pass

        # End-turn hand limit.
        if (gs.turn_index in human_idx_set) and web_control_mode and len(p.hand) > HAND_LIMIT:
            # Web human over the limit: set _discard_mode so legal_actions returns only discard
            # actions, then wait for the player to choose which cards to remove.
            p.flags["_discard_mode"] = True
            d_policy = action_policies[gs.turn_index % len(action_policies)]
            while len(p.hand) > HAND_LIMIT:
                chosen_discard = d_policy(gs, ms, p)
                if chosen_discard is None:
                    # Policy returned None — either game phase ended or an error
                    # occurred.  For web humans, do NOT auto-discard: leave the
                    # hand as-is and let the next poll re-enter the discard phase.
                    # For AI or terminal humans, fall back to the AI discard helper.
                    if gs.turn_index in human_idx_set and web_control_mode:
                        break
                    discard_down_to_ten_ai(gs, ms, p)
                    break
                if chosen_discard.kind == "discard_batch_to_pool":
                    # Human submitted a batch — process all at once then exit loop.
                    discard_ok = apply_action(gs, ms, p, chosen_discard, turn_state, choose_payment_ai, verbose=verbose)
                    if not discard_ok:
                        # Batch failed (e.g. empty picks got through) — try again next loop.
                        continue
                    break
                if chosen_discard.kind != "discard_to_pool":
                    # Unexpected action kind during discard phase — ignore and try again.
                    continue
                discard_ok = apply_action(gs, ms, p, chosen_discard, turn_state, choose_payment_ai, verbose=verbose)
                if not discard_ok:
                    continue
            p.flags["_discard_mode"] = False
        elif (gs.turn_index in human_idx_set) and (not web_control_mode):
            discard_down_to_ten_human(gs, ms, p)
        else:
            discard_down_to_ten_ai(gs, ms, p)

        clear_turn_only_flags(p)

        if live_recorder is not None:
            try:
                live_recorder.event(
                    "Scores -> " + " | ".join(f"{pl.name}: {_safe_fp(pl)}" for pl in gs.players)
                )
            except Exception:
                pass
            try:
                live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"turn_end:{p.name}")
            except Exception:
                pass
        if verbose:
            try:
                scores = " | ".join(f"{pl.name}: {_safe_fp(pl)}" for pl in gs.players)
                print(f"Scores -> {scores}")
                print("Score audit:")
                for pl in gs.players:
                    print(f"  {pl.name}: {_safe_fp(pl)} | Cards: {board_cards_label(gs, pl)}")
                if verbose_state and ms.pool:
                    print("Pool now: " + ", ".join(entry_short_label(ms, gs, uid) for uid in ms.pool))
                elif verbose_state:
                    print("Pool now: (empty)")
                print(f"Pool size: {len(ms.pool)} | Deck: {len(gs.deck)}")
            except Exception:
                pass

        if ms.end_game_triggered:
            ms.final_turns_remaining -= 1
            if ms.final_turns_remaining <= 0:
                break

        if made_action_this_turn:
            stalled_turns = 0
        else:
            p.flags["_tempo_score"] = float(p.flags.get("_tempo_score", 0.0)) - 0.45
            stalled_turns += 1
            # Safety: if the table is completely stalled for many turns, stop.
            if stalled_turns >= len(gs.players) * 6:
                gs.log.append("Game ended due to complete stall (no legal progress).")
                if live_recorder is not None:
                    live_recorder.event("Game ended due to complete stall (no legal progress).")
                break

        gs.turn_index = (gs.turn_index + 1) % len(gs.players)
        if gs.turn_index == 0:
            gs.round_count += 1

        turns += 1

    if live_recorder is not None:
        try:
            live_recorder.event("=== Final ===")
            for pl in gs.players:
                try:
                    live_recorder.event(f"{pl.name}: {_safe_fp(pl)}")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            live_recorder.snapshot(gs, ms, turn_number=turns, note="game_end")
        except Exception:
            pass

    # End-of-game self-play learning: reinforce moves that led to higher final outcomes.
    if online_weights is not None and any(move_histories.values()):
        finals = [_safe_fp(p) for p in gs.players]
        for i, feats_list in move_histories.items():
            if not feats_list:
                continue
            if i < 0 or i >= len(finals):
                continue
            my = finals[i]
            others = [s for j, s in enumerate(finals) if j != i]
            avg_other = (sum(others) / len(others)) if others else 0.0
            target = (my - avg_other) / 10.0
            if target > 4.0:
                target = 4.0
            elif target < -4.0:
                target = -4.0
            n = len(feats_list)
            for step, feats in enumerate(feats_list):
                discount = 0.9985 ** (n - step - 1)
                future_v = max(0.0, float(feats.get("future_value", 0.0)))
                setup_v = max(
                    0.0,
                    float(feats.get("plan_fit_bonus", 0.0))
                    + float(feats.get("synergy_bonus", 0.0))
                    + float(feats.get("species_bonus", 0.0))
                    + float(feats.get("same_ocean_bonus", 0.0))
                    + float(feats.get("stack_bonus", 0.0)),
                )
                setup_boost = 1.0 + min(0.35, 0.045 * future_v + 0.015 * setup_v)
                lr_scale = human_learning_boost if i in human_idx_set else 1.0
                online_update_weights(online_weights, feats, target * discount * setup_boost, lr=online_lr * 0.25 * lr_scale)
                if online_state is not None:
                    online_state["move_updates"] = int(online_state.get("move_updates", 0)) + 1

    if online_state is not None and online_state_path:
        if online_weights is not None:
            stabilize_weights(online_weights)

        strategy_value_map = online_state.get("strategy_value")
        strategy_count_map = online_state.get("strategy_count")
        strategy_transition_map = online_state.get("strategy_transition")
        strategy_transition_count_map = online_state.get("strategy_transition_count")
        if not isinstance(strategy_value_map, dict):
            strategy_value_map = {}
            online_state["strategy_value"] = strategy_value_map
        if not isinstance(strategy_count_map, dict):
            strategy_count_map = {}
            online_state["strategy_count"] = strategy_count_map
        if not isinstance(strategy_transition_map, dict):
            strategy_transition_map = {}
            online_state["strategy_transition"] = strategy_transition_map
        if not isinstance(strategy_transition_count_map, dict):
            strategy_transition_count_map = {}
            online_state["strategy_transition_count"] = strategy_transition_count_map

        finals = [float(_safe_fp(p)) for p in gs.players]
        update_strategy_memory_from_match(
            finals,
            move_signatures,
            strategy_value_map,
            strategy_count_map,
            strategy_transition_map,
            strategy_transition_count_map,
        )
        update_strategy_family_stats(online_state, gs, gs.players, finals)
        reinforce_human_teaching_signatures(
            move_signatures=move_signatures,
            human_indices=human_idx_set,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
            boost=human_learning_boost,
        )
        save_brain(online_state, online_state_path)

    return gs, ms


def run_game(
    card_db: Dict[int, CardDef],
    policy_a: Callable[[GameState, MatchState, PlayerState], Optional[Action]],
    policy_b: Callable[[GameState, MatchState, PlayerState], Optional[Action]],
    seed: int,
    max_turns: int = 180,
) -> Tuple[int, int, int]:
    gs, _ = run_match(
        card_db=card_db,
        player_names=["AI_A", "AI_B"],
        action_policies=[policy_a, policy_b],
        seed=seed,
        max_turns=max_turns,
        human_index=None,
        verbose=False,
    )

    p1 = gs.players[0]
    p2 = gs.players[1]
    a = final_points(gs, p1)
    b = final_points(gs, p2)

    if a > b:
        return 0, a, b
    if b > a:
        return 1, a, b
    return -1, a, b


def evaluate_weights(
    card_db: Dict[int, CardDef],
    w: Dict[str, float],
    games: int,
    seed: int,
    progress: Optional[TerminalProgressBar] = None,
) -> Tuple[float, float]:
    wins = 0.0
    margin_sum = 0.0

    for i in range(games):
        s = seed + i
        winner1, a1, b1 = run_game(
            card_db,
            policy_a=lambda gs, ms, p, _w=w: choose_action_weighted(gs, ms, p, _w),
            policy_b=choose_action_random,
            seed=s,
        )
        winner2, a2, b2 = run_game(
            card_db,
            policy_a=choose_action_random,
            policy_b=lambda gs, ms, p, _w=w: choose_action_weighted(gs, ms, p, _w),
            seed=s + 100000,
        )

        if winner1 == 0:
            wins += 1.0
        elif winner1 == -1:
            wins += 0.5

        if winner2 == 1:
            wins += 1.0
        elif winner2 == -1:
            wins += 0.5

        margin_sum += (a1 - b1)
        margin_sum += (b2 - a2)
        if progress is not None:
            progress.advance()

    denom = games * 2
    return wins / denom, margin_sum / denom


def mutate_weights(w: Dict[str, float], scale: float = 0.6) -> Dict[str, float]:
    out = dict(w)
    for k in out:
        out[k] += random.uniform(-scale, scale)
    return out


def mutate_weights_rng(w: Dict[str, float], rng: random.Random, scale: float = 0.22) -> Dict[str, float]:
    out = dict(w)
    for k in list(out.keys()):
        out[k] += rng.uniform(-scale, scale)
    return stabilize_weights(out)


def evolve_weights_from_population(
    base_weights: Dict[str, float],
    population_weights: List[Dict[str, float]],
    scores: List[float],
    lr: float = 0.08,
) -> None:
    if not population_weights or not scores or len(population_weights) != len(scores):
        return

    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    top = ranked[: max(1, min(2, len(ranked)))]
    bot = ranked[-max(1, min(2, len(ranked))):]
    score_spread = max(1.0, float(max(scores) - min(scores)))

    for k in list(base_weights.keys()):
        top_target = sum(population_weights[i].get(k, base_weights[k]) for i in top) / max(1, len(top))
        bot_target = sum(population_weights[i].get(k, base_weights[k]) for i in bot) / max(1, len(bot))
        base = float(base_weights.get(k, 0.0))
        pull = (top_target - base) * (lr * 0.9)
        push = (base - bot_target) * (lr * 0.35)
        base_weights[k] = base + ((pull + push) / score_spread)

    stabilize_weights(base_weights)


def update_archetype_stats(brain: Dict[str, object], players: List[PlayerState], scores: List[float]) -> None:
    stats = brain.get("archetype_stats")
    if not isinstance(stats, dict):
        stats = {}
        brain["archetype_stats"] = stats
    if not players or not scores or len(players) != len(scores):
        return

    top = max(scores)
    for p, s in zip(players, scores):
        prof = p.flags.get("_ai_profile")
        label = str(prof.get("label", "Unknown")) if isinstance(prof, dict) else "Unknown"
        rec = stats.get(label)
        if not isinstance(rec, dict):
            rec = {"games": 0.0, "wins": 0.0, "score_sum": 0.0}
            stats[label] = rec
        rec["games"] = float(rec.get("games", 0.0)) + 1.0
        rec["score_sum"] = float(rec.get("score_sum", 0.0)) + float(s)
        if float(s) >= float(top):
            rec["wins"] = float(rec.get("wins", 0.0)) + 1.0


def append_game_memory(
    brain: Dict[str, object],
    seed: int,
    players: List[PlayerState],
    scores: List[float],
    deck_remaining: int,
    pool_count: int,
    discard_count: int,
) -> None:
    mem = brain.get("game_memory")
    if not isinstance(mem, list):
        mem = []
        brain["game_memory"] = mem

    standings = sorted([(p.name, float(s)) for p, s in zip(players, scores)], key=lambda x: x[1], reverse=True)
    winner = standings[0][0] if standings else "N/A"
    archetypes = []
    for p in players:
        prof = p.flags.get("_ai_profile")
        label = str(prof.get("label", "Unknown")) if isinstance(prof, dict) else "Unknown"
        archetypes.append({"player": p.name, "label": label})

    mem.append(
        {
            "seed": int(seed),
            "winner": winner,
            "standings": standings,
            "archetypes": archetypes,
            "deck_remaining": int(deck_remaining),
            "pool_count": int(pool_count),
            "discard_count": int(discard_count),
        }
    )
    if len(mem) > BRAIN_GAME_MEMORY_CAP:
        del mem[:-BRAIN_GAME_MEMORY_CAP]


def learn_strategy(
    card_db: Dict[int, CardDef],
    generations: int,
    games_per_eval: int,
    seed: int,
) -> Dict[str, float]:
    random.seed(seed)

    best = default_weights()
    total_eval_games = (1 + (max(0, generations) * 8)) * max(0, games_per_eval)
    progress = TerminalProgressBar(total_eval_games, label="Simulation")

    best_wr, best_margin = evaluate_weights(card_db, best, games_per_eval, seed, progress=progress)

    for g in range(1, generations + 1):
        candidates = [best] + [mutate_weights(best, scale=max(0.1, 0.6 * (0.97 ** g))) for _ in range(7)]

        round_best = best
        round_wr = best_wr
        round_margin = best_margin

        for idx, c in enumerate(candidates):
            wr, margin = evaluate_weights(
                card_db,
                c,
                games_per_eval,
                seed + (g * 1000) + idx * 100,
                progress=progress,
            )
            if (wr, margin) > (round_wr, round_margin):
                round_best, round_wr, round_margin = c, wr, margin

        best, best_wr, best_margin = round_best, round_wr, round_margin
        progress.print_message(f"gen {g:02d}: win_rate_vs_random={best_wr:.3f}, avg_margin={best_margin:.2f}")

    progress.finish()

    return best


def run_human_vs_ai(
    card_db: Dict[int, CardDef],
    seed: int,
    max_turns: int,
    num_players: int = 2,
    export_final_board: Optional[str] = None,
    live_log_path: str = LIVE_LOG_PATH,
    live_state_path: str = LIVE_STATE_PATH,
) -> None:
    if num_players < 2:
        raise ValueError("num_players must be at least 2")

    brain = load_brain(BRAIN_PATH)
    use_history = use_historical_policy_bias()
    ai_weights = dict(default_weights())
    if use_history:
        ai_weights.update(brain.get("weights", {}))
    ai_weights = stabilize_weights(ai_weights)
    brain["weights"] = ai_weights
    synergy_map = brain.get("synergy", {}) if use_history else {}
    species_map = brain.get("species_synergy", {}) if use_history else {}
    same_ocean_map = brain.get("same_ocean_synergy", {}) if use_history else {}
    strategy_value_map = brain.get("strategy_value", {}) if use_history else {}
    strategy_count_map = brain.get("strategy_count", {}) if use_history else {}
    strategy_transition_map = brain.get("strategy_transition", {}) if use_history else {}
    strategy_transition_count_map = brain.get("strategy_transition_count", {}) if use_history else {}

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = [choose_action_human]
    for _ in range(1, num_players):
        policies.append(
            lambda gs, ms, p, _w=ai_weights, _s=synergy_map, _sp=species_map, _so=same_ocean_map, _sv=strategy_value_map, _sc=strategy_count_map, _st=strategy_transition_map, _stc=strategy_transition_count_map: choose_action_weighted(
                gs,
                ms,
                p,
                _w,
                synergy_map=_s,
                species_map=_sp,
                same_ocean_map=_so,
                strategy_value_map=_sv,
                strategy_count_map=_sc,
                strategy_transition_map=_st,
                strategy_transition_count_map=_stc,
                epsilon=0.0,
            )
        )

    live_recorder = LiveRecorder(log_path=live_log_path, state_path=live_state_path, seed=seed)

    gs, ms = run_match(
        card_db=card_db,
        player_names=["You"] + [f"AI_{i}" for i in range(1, num_players)],
        action_policies=policies,
        seed=seed,
        max_turns=max_turns,
        human_index=0,
        verbose=True,
        verbose_state=False,
        online_weights=ai_weights,
        online_learning_indices=set(range(num_players)),
        online_lr=0.03,
        online_state=brain,
        online_state_path=BRAIN_PATH,
        live_recorder=live_recorder,
    )

    print("\n=== Final ===")
    standings = [(p.name, final_points(gs, p)) for p in gs.players]
    standings.sort(key=lambda x: x[1], reverse=True)
    for name, score in standings:
        print(f"{name}: {score}")
    print(f"Winner: {standings[0][0]}")
    print(f"Deck remaining: {len(gs.deck)} | Pool cards: {len(ms.pool)} | Discard pile: {len(ms.discard_pile)}")

    if use_history:
        update_brain_from_match(gs, brain)
    save_brain(brain, BRAIN_PATH)
    print(
        f"AI brain updated. games_played={brain.get('games_played', 0)} "
        f"| move_updates={brain.get('move_updates', 0)} "
        f"| synergy_pairs={len(brain.get('synergy', {}))} "
        f"| species_pairs={len(brain.get('species_synergy', {}))} "
        f"| same_ocean_pairs={len(brain.get('same_ocean_synergy', {}))} "
        f"| strategy_patterns={len(brain.get('strategy_value', {}))} "
        f"| strategy_branches={len(brain.get('strategy_transition', {}))} "
        f"| strategy_families={len(brain.get('strategy_family_stats', {}))}"
    )
    if export_final_board:
        render_final_board_html(gs, ms, export_final_board, title="Fish Game Final Board (Human vs AI)")
        print(f"Final board visual saved to: {export_final_board}")
    print(f"Live game log saved to: {live_log_path}")
    print(f"Live game state saved to: {live_state_path}")


def run_all_human_teaching_game(
    card_db: Dict[int, CardDef],
    seed: int,
    max_turns: int,
    num_players: int = 4,
    export_final_board: Optional[str] = None,
    human_teach_boost: float = 3.5,
    live_log_path: str = LIVE_LOG_PATH,
    live_state_path: str = LIVE_STATE_PATH,
) -> None:
    if num_players < 2:
        raise ValueError("num_players must be at least 2")

    brain = load_brain(BRAIN_PATH)
    use_history = use_historical_policy_bias()
    ai_weights = dict(default_weights())
    if use_history:
        ai_weights.update(brain.get("weights", {}))
    ai_weights = stabilize_weights(ai_weights)
    brain["weights"] = ai_weights

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = [
        choose_action_human for _ in range(num_players)
    ]
    human_set = set(range(num_players))
    names = [f"P{i+1}" for i in range(num_players)]
    live_recorder = LiveRecorder(log_path=live_log_path, state_path=live_state_path, seed=seed)

    gs, ms = run_match(
        card_db=card_db,
        player_names=names,
        action_policies=policies,
        seed=seed,
        max_turns=max_turns,
        human_index=None,
        human_indices=human_set,
        verbose=True,
        verbose_state=False,
        online_weights=ai_weights,
        online_learning_indices=human_set,
        online_lr=0.035,
        online_state=brain,
        online_state_path=BRAIN_PATH,
        live_recorder=live_recorder,
        hand_based_archetypes=False,
        human_learning_boost=max(1.0, human_teach_boost),
    )

    print("\n=== Final ===")
    standings = [(p.name, final_points(gs, p)) for p in gs.players]
    standings.sort(key=lambda x: x[1], reverse=True)
    for name, score in standings:
        print(f"{name}: {score}")
    print(f"Winner: {standings[0][0]}")
    print(f"Deck remaining: {len(gs.deck)} | Pool cards: {len(ms.pool)} | Discard pile: {len(ms.discard_pile)}")

    if use_history:
        update_brain_from_match(gs, brain)
        reinforce_human_demo_from_board(gs, human_set, brain, boost=max(1.0, human_teach_boost))
    save_brain(brain, BRAIN_PATH)
    print(
        f"Teaching applied. boost={max(1.0, human_teach_boost):.2f} "
        f"| games_played={brain.get('games_played', 0)} "
        f"| move_updates={brain.get('move_updates', 0)} "
        f"| synergy_pairs={len(brain.get('synergy', {}))} "
        f"| species_pairs={len(brain.get('species_synergy', {}))} "
        f"| same_ocean_pairs={len(brain.get('same_ocean_synergy', {}))} "
        f"| strategy_patterns={len(brain.get('strategy_value', {}))} "
        f"| strategy_branches={len(brain.get('strategy_transition', {}))}"
    )
    if export_final_board:
        render_final_board_html(gs, ms, export_final_board, title="Fish Game Final Board (All-Human Teaching)")
        print(f"Final board visual saved to: {export_final_board}")
    print(f"Live game log saved to: {live_log_path}")
    print(f"Live game state saved to: {live_state_path}")


def run_ai_only_game(
    card_db: Dict[int, CardDef],
    seed: int,
    max_turns: int,
    num_players: int = 4,
    export_final_board: Optional[str] = None,
    hand_based_archetypes: bool = True,
) -> None:
    if num_players < 2:
        raise ValueError("num_players must be at least 2")

    brain = load_brain(BRAIN_PATH)
    use_history = use_historical_policy_bias()
    no_archetype_bias = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("no_archetype_preweight", False))
    ai_weights = dict(default_weights())
    if use_history:
        ai_weights.update(brain.get("weights", {}))
    ai_weights = stabilize_weights(ai_weights)
    brain["weights"] = ai_weights
    synergy_map = brain.get("synergy", {}) if use_history else {}
    species_map = brain.get("species_synergy", {}) if use_history else {}
    same_ocean_map = brain.get("same_ocean_synergy", {}) if use_history else {}
    strategy_value_map = brain.get("strategy_value", {}) if use_history else {}
    strategy_count_map = brain.get("strategy_count", {}) if use_history else {}
    strategy_transition_map = brain.get("strategy_transition", {}) if use_history else {}
    strategy_transition_count_map = brain.get("strategy_transition_count", {}) if use_history else {}
    if no_archetype_bias:
        hand_based_archetypes = False
    archetype_profiles: Optional[List[Dict[str, Any]]] = (
        None if (hand_based_archetypes or no_archetype_bias) else select_archetype_profiles(num_players, seed)
    )
    games_played = int(brain.get("games_played", 0))
    evo_rng = random.Random(seed ^ (games_played << 1))
    evo_scale = max(0.05, 0.28 * (0.992 ** max(0, games_played)))
    epsilon_base = 0.02 + min(0.08, (10.0 / (10.0 + max(0, games_played))) * 0.08)

    policy_weights: List[Dict[str, float]] = []
    for i in range(num_players):
        if i == 0:
            policy_weights.append(dict(ai_weights))
        else:
            policy_weights.append(mutate_weights_rng(ai_weights, evo_rng, scale=evo_scale))

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = []
    for i in range(num_players):
        prof = archetype_profiles[i] if archetype_profiles and i < len(archetype_profiles) else None
        p_w = policy_weights[i] if i < len(policy_weights) else ai_weights
        p_eps = max(0.01, min(0.14, epsilon_base + evo_rng.uniform(-0.01, 0.02)))
        policies.append(
            lambda gs, ms, p, _w=p_w, _eps=p_eps, _s=synergy_map, _sp=species_map, _so=same_ocean_map, _sv=strategy_value_map, _sc=strategy_count_map, _st=strategy_transition_map, _stc=strategy_transition_count_map, _prof=prof: choose_action_weighted(
                gs,
                ms,
                p,
                _w,
                synergy_map=_s,
                species_map=_sp,
                same_ocean_map=_so,
                strategy_value_map=_sv,
                strategy_count_map=_sc,
                strategy_transition_map=_st,
                strategy_transition_count_map=_stc,
                archetype_profile=_prof,
                epsilon=_eps,
            )
        )

    if archetype_profiles:
        print("AI archetypes:")
        for i, prof in enumerate(archetype_profiles):
            print(f"  AI_{i+1}: {prof.get('label', 'Profile')}")
    elif hand_based_archetypes:
        print("AI archetypes: opening-hand adaptive selection enabled")
    elif no_archetype_bias:
        print("AI archetypes: disabled (human realism no pre-weight mode)")

    gs, ms = run_match(
        card_db=card_db,
        player_names=[f"AI_{i+1}" for i in range(num_players)],
        action_policies=policies,
        seed=seed,
        max_turns=max_turns,
        human_index=None,
        verbose=True,
        verbose_state=True,
        online_weights=ai_weights,
        online_learning_indices=set(range(num_players)),
        online_lr=0.03,
        online_state=brain,
        online_state_path=BRAIN_PATH,
        player_archetype_profiles=archetype_profiles,
        hand_based_archetypes=hand_based_archetypes,
    )

    print("\n=== Final ===")
    standings = [(p.name, final_points(gs, p)) for p in gs.players]
    standings.sort(key=lambda x: x[1], reverse=True)
    for name, score in standings:
        print(f"{name}: {score}")
    print(f"Winner: {standings[0][0]}")
    print(f"Deck remaining: {len(gs.deck)} | Pool cards: {len(ms.pool)} | Discard pile: {len(ms.discard_pile)}")
    print("\n=== Final Boards ===")
    for p in gs.players:
        print(f"\n{p.name}:")
        print(board_summary(gs, p))

    finals = [float(final_points(gs, p)) for p in gs.players]
    if use_history:
        evolve_weights_from_population(ai_weights, policy_weights, finals, lr=0.10)
        update_brain_from_match(gs, brain)
        update_archetype_stats(brain, gs.players, finals)
    append_game_memory(
        brain,
        seed=seed,
        players=gs.players,
        scores=finals,
        deck_remaining=len(gs.deck),
        pool_count=len(ms.pool),
        discard_count=len(ms.discard_pile),
    )
    evo = brain.get("evolution")
    if not isinstance(evo, dict):
        evo = {"runs": 0, "last_score_spread": 0.0}
        brain["evolution"] = evo
    if use_history:
        evo["runs"] = int(evo.get("runs", 0)) + 1
        evo["last_score_spread"] = float(max(finals) - min(finals)) if finals else 0.0
    brain["weights"] = stabilize_weights(ai_weights)
    save_brain(brain, BRAIN_PATH)
    print(
        f"AI brain updated. games_played={brain.get('games_played', 0)} "
        f"| move_updates={brain.get('move_updates', 0)} "
        f"| synergy_pairs={len(brain.get('synergy', {}))} "
        f"| species_pairs={len(brain.get('species_synergy', {}))} "
        f"| same_ocean_pairs={len(brain.get('same_ocean_synergy', {}))} "
        f"| strategy_patterns={len(brain.get('strategy_value', {}))} "
        f"| strategy_branches={len(brain.get('strategy_transition', {}))} "
        f"| branch_counts={len(brain.get('strategy_transition_count', {}))} "
        f"| strategy_families={len(brain.get('strategy_family_stats', {}))} "
        f"| game_memory={len(brain.get('game_memory', []))} "
        f"| evo_runs={brain.get('evolution', {}).get('runs', 0)}"
    )
    if export_final_board:
        render_final_board_html(gs, ms, export_final_board, title="Fish Game Final Board (AI Only)")
        print(f"Final board visual saved to: {export_final_board}")


def _to_float_score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _entry_top_and_spread(entry: Dict[str, object]) -> Tuple[float, float]:
    standings = entry.get("standings")
    if not isinstance(standings, list):
        return 0.0, 0.0
    scores: List[float] = []
    for row in standings:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            scores.append(_to_float_score(row[1]))
    if not scores:
        return 0.0, 0.0
    top = max(scores)
    return top, top - min(scores)


def _entry_int(entry: Dict[str, object], key: str) -> int:
    try:
        return int(entry.get(key, 0))
    except (TypeError, ValueError):
        return 0


def is_inert_game_entry(
    entry: Dict[str, object],
    max_top_score: float,
    max_score_spread: float,
    max_discard_count: int,
    min_deck_remaining: int,
) -> bool:
    top, spread = _entry_top_and_spread(entry)
    discard_count = _entry_int(entry, "discard_count")
    deck_remaining = _entry_int(entry, "deck_remaining")
    return (
        top <= max_top_score
        and spread <= max_score_spread
        and discard_count <= max_discard_count
        and deck_remaining >= min_deck_remaining
    )


def decay_numeric_map_values(
    m: object,
    decay: float,
    epsilon: float = 1e-6,
) -> int:
    if not isinstance(m, dict):
        return 0
    touched = 0
    for k in list(m.keys()):
        v = m.get(k)
        if not isinstance(v, (int, float)):
            continue
        new_v = float(v) * decay
        touched += 1
        if abs(new_v) < epsilon:
            del m[k]
        else:
            m[k] = new_v
    return touched


def decay_count_map_values(
    m: object,
    decay: float,
) -> int:
    if not isinstance(m, dict):
        return 0
    touched = 0
    for k in list(m.keys()):
        v = m.get(k)
        if not isinstance(v, (int, float)):
            continue
        new_v = int(round(float(v) * decay))
        touched += 1
        if new_v <= 0:
            del m[k]
        else:
            m[k] = new_v
    return touched


def unlearn_inert_games(
    brain: Dict[str, object],
    max_top_score: float = 55.0,
    max_score_spread: float = 20.0,
    max_discard_count: int = 35,
    min_deck_remaining: int = 12,
) -> Dict[str, object]:
    mem = brain.get("game_memory")
    if not isinstance(mem, list):
        mem = []
        brain["game_memory"] = mem

    kept: List[Dict[str, object]] = []
    removed = 0
    for entry in mem:
        if isinstance(entry, dict) and is_inert_game_entry(
            entry,
            max_top_score=max_top_score,
            max_score_spread=max_score_spread,
            max_discard_count=max_discard_count,
            min_deck_remaining=min_deck_remaining,
        ):
            removed += 1
            continue
        if isinstance(entry, dict):
            kept.append(entry)

    if removed == 0:
        return {
            "removed_games": 0,
            "kept_games": len(kept),
            "decay": 1.0,
            "games_played_before": int(brain.get("games_played", 0)),
            "games_played_after": int(brain.get("games_played", 0)),
        }

    games_before = int(brain.get("games_played", 0))
    ratio = float(removed) / max(1.0, float(games_before))
    value_decay = max(0.65, 1.0 - min(0.35, ratio * 6.0))
    count_decay = max(0.55, 1.0 - min(0.45, ratio * 8.0))

    brain["game_memory"] = kept[-BRAIN_GAME_MEMORY_CAP:]
    brain["games_played"] = max(0, games_before - removed)
    brain["move_updates"] = max(0, int(round(int(brain.get("move_updates", 0)) * count_decay)))

    touched = {
        "synergy": decay_numeric_map_values(brain.get("synergy"), value_decay),
        "species_synergy": decay_numeric_map_values(brain.get("species_synergy"), value_decay),
        "same_ocean_synergy": decay_numeric_map_values(brain.get("same_ocean_synergy"), value_decay),
        "strategy_value": decay_numeric_map_values(brain.get("strategy_value"), value_decay),
        "strategy_transition": decay_numeric_map_values(brain.get("strategy_transition"), value_decay),
        "strategy_count": decay_count_map_values(brain.get("strategy_count"), count_decay),
        "strategy_transition_count": decay_count_map_values(brain.get("strategy_transition_count"), count_decay),
    }

    for stats_key in ["strategy_family_stats", "archetype_stats"]:
        stats = brain.get(stats_key)
        if not isinstance(stats, dict):
            continue
        for rec in stats.values():
            if not isinstance(rec, dict):
                continue
            rec["games"] = float(rec.get("games", 0.0)) * count_decay
            rec["wins"] = float(rec.get("wins", 0.0)) * count_decay
            rec["score_sum"] = float(rec.get("score_sum", 0.0)) * value_decay

    evo = brain.get("evolution")
    if isinstance(evo, dict):
        evo["runs"] = max(0, int(round(int(evo.get("runs", 0)) * count_decay)))

    brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
    ensure_priority_anchor_brain_rules(brain)
    brain["last_inert_unlearn"] = {
        "removed_games": removed,
        "kept_games": len(brain.get("game_memory", [])),
        "games_played_before": games_before,
        "games_played_after": int(brain.get("games_played", 0)),
        "value_decay": value_decay,
        "count_decay": count_decay,
        "touched": touched,
        "criteria": {
            "max_top_score": max_top_score,
            "max_score_spread": max_score_spread,
            "max_discard_count": max_discard_count,
            "min_deck_remaining": min_deck_remaining,
        },
    }
    return dict(brain["last_inert_unlearn"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Fish Game simulation + strategy learning")
    parser.add_argument("--generations", type=int, default=10)
    parser.add_argument("--games-per-eval", type=int, default=40)
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default: random each run)")
    parser.add_argument("--benchmark-games", type=int, default=200)
    parser.add_argument("--human-vs-ai", action="store_true")
    parser.add_argument("--all-human", action="store_true", help="Control all players manually (teaching mode).")
    parser.add_argument("--ai-only", action="store_true")
    parser.add_argument("--max-turns", type=int, default=0, help="0 means unlimited turns (until END GAME final round ends)")
    parser.add_argument("--num-players", type=int, default=2)
    parser.add_argument("--export-final-board", type=str, default="", help="Write end-game visual board HTML to this path")
    parser.add_argument("--num-games", type=int, default=1, help="Number of AI-only games to run (default: 1)")
    parser.add_argument("--open-board", action="store_true", help="Open each saved board HTML in the browser after the game")
    parser.add_argument(
        "--unlearn-inert-games",
        action="store_true",
        help="Remove low-signal games from memory and decay their influence from the brain.",
    )
    parser.add_argument(
        "--inert-max-top-score",
        type=float,
        default=55.0,
        help="Unlearn criteria: top score must be <= this value.",
    )
    parser.add_argument(
        "--inert-max-score-spread",
        type=float,
        default=20.0,
        help="Unlearn criteria: winner minus last place must be <= this value.",
    )
    parser.add_argument(
        "--inert-max-discard-count",
        type=int,
        default=35,
        help="Unlearn criteria: discard pile count must be <= this value.",
    )
    parser.add_argument(
        "--inert-min-deck-remaining",
        type=int,
        default=12,
        help="Unlearn criteria: deck remaining must be >= this value.",
    )
    parser.add_argument(
        "--human-teach-boost",
        type=float,
        default=3.5,
        help="Learning multiplier for human-controlled teaching turns.",
    )
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else secrets.randbits(32)

    card_db = load_card_db()
    print(f"Loaded {len(card_db)} cards")
    print(f"Seed: {seed}")

    if args.unlearn_inert_games:
        brain = load_brain(BRAIN_PATH)
        report = unlearn_inert_games(
            brain,
            max_top_score=args.inert_max_top_score,
            max_score_spread=args.inert_max_score_spread,
            max_discard_count=args.inert_max_discard_count,
            min_deck_remaining=args.inert_min_deck_remaining,
        )
        save_brain(brain, BRAIN_PATH)
        print(
            "Inert-game unlearn complete. "
            f"removed_games={report.get('removed_games', 0)} "
            f"| games_played={report.get('games_played_before', 0)}->{report.get('games_played_after', 0)} "
            f"| value_decay={float(report.get('value_decay', 1.0)):.6f} "
            f"| count_decay={float(report.get('count_decay', 1.0)):.6f}"
        )
        return

    if args.human_vs_ai:
        run_human_vs_ai(
            card_db=card_db,
            seed=seed,
            max_turns=args.max_turns,
            num_players=args.num_players,
            export_final_board=args.export_final_board or None,
        )
        return

    if args.all_human:
        run_all_human_teaching_game(
            card_db=card_db,
            seed=seed,
            max_turns=args.max_turns,
            num_players=args.num_players,
            export_final_board=args.export_final_board or None,
            human_teach_boost=args.human_teach_boost,
        )
        return

    if args.ai_only:
        boards_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_boards")
        os.makedirs(boards_dir, exist_ok=True)
        num_games = max(1, args.num_games)
        for game_idx in range(1, num_games + 1):
            game_seed = seed + game_idx - 1
            if num_games > 1:
                print(f"\n=== Game {game_idx} of {num_games} ===")
            ts = time.strftime("%Y%m%d_%H%M%S")
            auto_path = os.path.join(boards_dir, f"game_{ts}_{game_idx}.html")
            export_path = args.export_final_board if args.export_final_board and num_games == 1 else auto_path
            run_ai_only_game(
                card_db=card_db,
                seed=game_seed,
                max_turns=args.max_turns,
                num_players=args.num_players,
                export_final_board=export_path,
            )
            print(f"Board saved → {export_path}")
            if args.open_board:
                import webbrowser
                webbrowser.open(f"file://{export_path}")
        return

    best = learn_strategy(
        card_db=card_db,
        generations=args.generations,
        games_per_eval=args.games_per_eval,
        seed=seed,
    )

    benchmark_progress = TerminalProgressBar(args.benchmark_games, label="Benchmark")
    wr, margin = evaluate_weights(card_db, best, args.benchmark_games, seed + 90000, progress=benchmark_progress)
    benchmark_progress.finish()
    print("\nBest learned strategy weights:")
    for k in sorted(best.keys()):
        print(f"  {k}: {best[k]:.3f}")
    print(f"\nBenchmark vs random over {args.benchmark_games} games:")
    print(f"  win_rate={wr:.3f}")
    print(f"  avg_margin={margin:.2f}")


if __name__ == "__main__":
    try:
        main()
    except BrainFileCorruptionError as e:
        print("\n=== Brain File Error ===")
        print(str(e))
        raise SystemExit(2)
