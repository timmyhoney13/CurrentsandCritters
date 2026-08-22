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
        return self.players[self.turn_index]


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
    cd = gs.card_db[card_uid]
    fn = ABILITIES_BY_UID.get(card_uid)
    if fn is None:
        fn = ABILITIES.get(cd.name.lower())
    if fn:
        fn(gs, card_uid, player, ctx)
    else:
        # default: no-op
        pass


def run_star_ability(gs: GameState, card_uid: int, player: PlayerState, ctx: Optional[dict] = None) -> None:
    cd = gs.card_db[card_uid]
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

def draw(gs: GameState, player: PlayerState, n: int = 1) -> None:
    for _ in range(n):
        if not gs.deck:
            gs.log.append("Deck is empty; cannot draw.")
            return
        # treat index 0 as the top of the deck for predictable drawing order
        player.hand.append(gs.deck.pop(0))


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
        card = CardDef(uid, name, species, cost, direction, symbol, text)
        return apply_card_data_overrides(card)
    except (ValueError, IndexError):
        return None


def apply_card_data_overrides(card: CardDef) -> CardDef:
    """Apply canonical rule corrections for known card-cost constraints."""
    name = card.name.strip().lower()
    normalized = re.sub(r"[^a-z]", "", name)
    target_cost = None
    if normalized in {
        "mandaringoby",
        "madraingoby",
        "mandraingoby",
        "mandaringobys",
        "madraingobys",
        "mandraingobys",
    }:
        target_cost = 1
    elif normalized in {"mantisshrimp", "mantisshrimps"}:
        target_cost = 0

    if target_cost is None or int(card.cost) == int(target_cost):
        return card
    return CardDef(
        uid=card.uid,
        name=card.name,
        species=card.species,
        cost=int(target_cost),
        direction=card.direction,
        symbol=card.symbol,
        text=card.text,
    )


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

    # Direct +X values.
    for m in re.finditer(r"\+(\d+)", t):
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

    # Draw-now text (not "draw one when ...").
    has_reactive_draw_one = (
        "draw one when" in t
        or "when you play" in t
        or "when a game fish is played" in t
        or "when a gamefish is played" in t
    )
    if "draw one" in t and not has_reactive_draw_one:
        n = choose_optional_draw_count(gs, player, 1)
        draw(gs, player, n)
        gs.log.append(f"{player.name} draws {n} from {card.name} main ability.")
    if "draw 2" in t or "draw two" in t:
        n = choose_optional_draw_count(gs, player, 2)
        draw(gs, player, n)
        gs.log.append(f"{player.name} draws {n} from {card.name} main ability.")

    # Tarpon-style hand cycling: "Discard and draw that many cards".
    if "discard and draw that many cards" in t:
        candidates = list(player.hand)
        if candidates:
            chosen: List[int] = []

            if is_human_turn and ms is not None:
                # Human turn: choose any number of cards (including 0).
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

            for uid in chosen:
                if uid in player.hand:
                    player.hand.remove(uid)
                    if ms is not None:
                        try:
                            add_to_pool(ms, uid)
                        except Exception:
                            player.discard.append(uid)
                    else:
                        player.discard.append(uid)
            draw(gs, player, len(chosen))
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
        if "uncharted animal" in raw or "n/a animal" in raw:
            count = sum(
                1
                for c in board
                if c.species.lower() in {"uncharted", "n/a"} and c.direction.strip().lower() != "n/a"
            )
            player.score += n * count
            continue
        if "mahi mahi" in raw:
            count = sum(1 for c in board if c.name.lower() == "mahi mahi")
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
    thresholds = []
    for m in re.finditer(r"(\d+)\s*=\s*(\d+)", t):
        thresholds.append((int(m.group(1)), int(m.group(2))))
    if thresholds:
        if "different species of baitfish" in t:
            value = len({c.name.lower() for c in board if c.species.lower() == "baitfish"})
        elif "matching symbol" in t:
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


def _execute_star_pattern(
    gs: GameState,
    player: PlayerState,
    text: str,
    card: CardDef,
    ctx: Optional[dict] = None,
) -> None:
    """Execute common star ability patterns."""
    t = text.lower()
    
    if "draw one" in t:
        n = choose_optional_draw_count(gs, player, 1)
        draw(gs, player, n)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    if "draw three" in t:
        n = choose_optional_draw_count(gs, player, 3)
        draw(gs, player, n)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    if "draw 2" in t or "draw two" in t:
        n = choose_optional_draw_count(gs, player, 2)
        draw(gs, player, n)
        gs.log.append(f"{player.name} draws {n} from {card.name} star ability.")
    if "play again" in t or "go again" in t:
        if should_take_optional_replay(gs, player, card):
            player.flags["play_again"] = True
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
) -> None:
    """Resolve persistent 'draw one when ...' listeners after a successful play."""
    species = played_card.species.strip().lower()
    direction = normalize_direction(played_card.direction)

    is_game_fish_play = species == "game fish"
    is_surface_play = action_kind == "play_to_ocean" and direction == "up"
    is_floor_play = action_kind == "play_to_ocean" and direction == "down"

    for owner in gs.players:
        if is_game_fish_play:
            n = int(owner.flags.get("trigger_draw_on_game_fish", 0))
            if n > 0:
                draw(gs, owner, n)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (game fish played)."
                )

        if owner is played_by and is_surface_play:
            n = int(owner.flags.get("trigger_draw_on_surface_play", 0))
            if n > 0:
                draw(gs, owner, n)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (played on ocean surface)."
                )

        if owner is played_by and is_floor_play:
            n = int(owner.flags.get("trigger_draw_on_floor_play", 0))
            if n > 0:
                draw(gs, owner, n)
                gs.log.append(
                    f"{owner.name} draws {n} from reactive trigger (played on ocean floor)."
                )


def sync_reactive_trigger_flags(gs: GameState, player: PlayerState) -> None:
    """Rebuild reactive listener counts from the player's current board state."""
    game_fish = 0
    surface = 0
    floor = 0
    for uid in all_board_cards(player):
        c = gs.card_db[uid]
        t = c.text.lower()
        if "draw one when a game fish is played" in t or "draw one when a gamefish is played" in t:
            game_fish += 1
        if "draw one when you play an animal on the ocean surface" in t:
            surface += 1
        if "draw one when you play a card on the ocean floor" in t:
            floor += 1

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


# -----------------------------
# Minimal demo runner (optional)
# -----------------------------
if __name__ == "__main__" and False:
    # Load all card files: vertical, left/right, and oceans
    card_db: Dict[int, CardDef] = {}
    
    # Load vertical (up/down) animals
    try:
        with open("/Users/timmyhoney1/fish game/cards_vertical.txt") as f:
            card_db = load_cards_from_lines(f.read(), card_db)
    except FileNotFoundError:
        print("Warning: cards_vertical.txt not found")
    
    # Load left/right animals
    try:
        with open("/Users/timmyhoney1/fish game/cards_lr.txt") as f:
            card_db = load_cards_from_lines(f.read(), card_db)
    except FileNotFoundError:
        print("Warning: cards_lr.txt not found")
    
    # Load ocean cards
    try:
        with open("/Users/timmyhoney1/fish game/cards_oceans.txt") as f:
            card_db = load_cards_from_lines(f.read(), card_db)
    except FileNotFoundError:
        print("Warning: cards_oceans.txt not found")
    
    # Register abilities for all cards
    register_all_card_abilities(card_db)
    
    p1 = PlayerState("Timmy")
    p2 = PlayerState("Connor")
    
    # Build a small test deck from available cards
    deck_list = sorted(list(card_db.keys()))[:15]
    
    gs = GameState(card_db=card_db, players=[p1, p2], deck=deck_list)
    start_game(gs, starting_hand=3, shuffle=False)

    print(f"Loaded {len(card_db)} cards total")
    print(f"Registered {len(ABILITIES)} main abilities, {len(STAR_ABILITIES)} star abilities")
    print()
    print("\n".join(gs.log))


# ===== Combined Main Simulator =====

"""Rules-faithful simulator + AI for The Fish Game (Ocean Shuffle style)."""

import argparse
import copy
import html
import json
import math
import os
import random
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple



@dataclass
class Action:
    kind: str  # draw, play_ocean, play_to_ocean
    card_uid: int = -1
    face_uid: Optional[int] = None
    ocean_uid: Optional[int] = None
    draw_from_pool: int = 0  # for draw action: 0/1/2
    pool_pick_uids: List[int] = field(default_factory=list)  # optional specific pool picks for human draw
    use_star: bool = False


@dataclass
class TurnState:
    star_activations: int = 0
    free_followups: int = 0
    played_face_uids: List[int] = field(default_factory=list)
    replay_pickup_used: bool = False


@dataclass
class MatchState:
    pool: List[int] = field(default_factory=list)  # face-up discard board
    discard_pile: List[int] = field(default_factory=list)  # face-down cleared pool
    end_game_uid: Optional[int] = None
    end_game_triggered: bool = False
    final_turns_remaining: int = 0
    pair_primary_to_faces: Dict[int, Tuple[int, int]] = field(default_factory=dict)
    face_to_primary: Dict[int, int] = field(default_factory=dict)
    guard_events: List[str] = field(default_factory=list)


BRAIN_PATH = "fish_ai_brain.json"
LIVE_LOG_PATH = "last_live_game_log.txt"
LIVE_STATE_PATH = "last_live_game_state.json"
FREE_PLAY_FLAGS = (
    "free_mammal",
    "free_baitfish",
    "free_game_fish",
    "free_cephalopods",
    "free_crustacean",
    "free_invertebrate",
    "free_coral",
)

PLAYSTYLE_SET = {"RANDOM", "AGGRESSIVE", "CONSERVATIVE", "OPPORTUNISTIC", "RISK_SEEKING"}

HUMAN_REALISM_CONFIG: Dict[str, Any] = {
    # Master switch for the human-like policy layer.
    "enabled": True,
    # Optional effects from the checklist.
    "optional_draw_effects": True,
    "optional_replay_effects": True,
    # "Every game independent" behavior from the checklist.
    "independent_games": True,
    "no_archetype_preweight": True,
    # Human-limited inference (no perfect hidden-info planning).
    "human_limited_inference": True,
    # Visible-card memory + decay for scarcity tiers.
    "memory_decay": 0.92,
    # Exploration for near-EV branches.
    "adaptive_exploration": True,
    # Extra lookahead rounds for stronger tactical foresight.
    "lookahead_rounds": 5,
    # Per simulated turn action budget during lookahead.
    "lookahead_actions_per_turn": 3,
    # Do not assume known deck order during lookahead (sample unknown draws instead).
    "lookahead_predict_draws": False,
    # In hidden-info mode, do not use exact opponent hand contents during lookahead.
    "lookahead_use_opponent_hidden_hands": False,
    # For balance testing: prefer exhaustive evaluation over speed shortcuts.
    "accuracy_over_speed": True,
    # Safety guard against infinite loops; high enough to avoid clipping legal long chains.
    "turn_chain_safety_cap": 500,
    # Encourage role diversity across AI seats in the same game.
    "diversity_roles": True,
    # Explore low-sample action signatures when not clearly losing EV.
    "rare_combo_exploration": True,
}


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


def game_progress_state(gs: GameState, ms: MatchState) -> Dict[str, Any]:
    """
    Return coarse game-phase awareness for AI decisions:
    - early_game
    - mid_game
    - late_game
    - nearing_game_end
    - limited_turns_remaining
    """
    deck_remaining = len(gs.deck)
    total_cards_est = (
        len(gs.deck)
        + len(ms.pool)
        + len(ms.discard_pile)
        + sum(
            len(p.hand)
            + len(p.discard)
            + len(p.board_oceans)
            + sum(len(slots.all_cards()) for slots in p.ocean_slots.values())
            for p in gs.players
        )
    )
    if total_cards_est <= 0:
        total_cards_est = max(1, len(gs.card_db))

    progress = 1.0 - (deck_remaining / float(max(1, total_cards_est)))
    pressure = endgame_pressure(deck_remaining)
    final_turns_left = int(ms.final_turns_remaining) if ms.end_game_triggered else -1

    limited_turns = bool(
        ms.end_game_triggered
        and final_turns_left >= 0
        and final_turns_left <= max(2, (len(gs.players) // 2) + 1)
    )

    if ms.end_game_triggered:
        if limited_turns:
            phase = "limited_turns_remaining"
        elif final_turns_left <= len(gs.players):
            phase = "nearing_game_end"
        else:
            phase = "late_game"
    else:
        if progress < 0.24 and gs.round_count < 3:
            phase = "early_game"
        elif progress < 0.60:
            phase = "mid_game"
        elif progress < 0.84:
            phase = "late_game"
        else:
            phase = "nearing_game_end"

    phase_urgency_map = {
        "early_game": 0.18,
        "mid_game": 0.42,
        "late_game": 0.68,
        "nearing_game_end": 0.86,
        "limited_turns_remaining": 1.0,
    }

    return {
        "phase": phase,
        "progress": float(max(0.0, min(1.0, progress))),
        "pressure": float(max(0.0, min(1.0, pressure))),
        "final_turns_left": final_turns_left,
        "limited_turns_remaining": limited_turns,
        "urgency": float(phase_urgency_map.get(phase, pressure)),
    }


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


def adaptive_exploration_rate(gs: GameState, ms: MatchState, player: PlayerState, base_epsilon: float) -> float:
    if not (human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("adaptive_exploration", False))):
        return base_epsilon
    style = player_playstyle(player)
    s = style_params(style)
    progress = game_progress_state(gs, ms)
    phase = str(progress.get("phase", "mid_game"))
    pressure = float(progress.get("pressure", endgame_pressure(len(gs.deck))))
    urgency = float(progress.get("urgency", pressure))
    # Explore more in early/mid game and with naturally exploratory playstyles.
    eps = max(base_epsilon, float(s.get("explore", 0.06)))
    if phase == "early_game":
        eps *= 1.22
    elif phase == "mid_game":
        eps *= 1.10
    elif phase == "late_game":
        eps *= 0.82
    elif phase == "nearing_game_end":
        eps *= 0.62
    elif phase == "limited_turns_remaining":
        eps *= 0.46
    if pressure >= 0.88 or urgency >= 0.90:
        eps *= 0.65
    elif pressure <= 0.45:
        eps *= 1.15
    if eps < 0.01:
        return 0.01
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
    progress_state = game_progress_state(gs, ms)
    phase = str(progress_state.get("phase", "mid_game"))
    phase_urgency = float(progress_state.get("urgency", pressure))
    limited_turns = bool(progress_state.get("limited_turns_remaining", False))
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
    board_pos = float(f.get("board_positioning", 0.0))
    combo_setup = float(f.get("combo_setup_value", 0.0))
    short_sacrifice = float(f.get("short_term_sacrifice_value", 0.0))
    trap_risk = float(f.get("strategic_trap_risk", 0.0))
    opp_pred_pressure = float(f.get("opponent_prediction_pressure", 0.0))
    adj = 0.0

    # Tempo bias from style + recent ability chaining.
    adj += min(2.0, max(-2.0, tempo)) * (0.09 * float(s.get("tempo", 0.0)))
    # Recognize strategic traps and board positioning quality.
    adj -= 0.30 * max(0.0, trap_risk)
    adj += 0.18 * max(0.0, board_pos)

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
        # Explicit game progress awareness.
        if phase == "early_game":
            if hand_n < 8:
                adj += 0.20
        elif phase == "mid_game":
            if hand_n < 7:
                adj += 0.10
        elif phase == "late_game":
            adj -= 0.35
            if action.draw_from_pool > 0 and pick_value >= 1.3:
                adj += 0.18
        elif phase == "nearing_game_end":
            adj -= 0.70
            if action.draw_from_pool > 0 and pick_value >= 1.7:
                adj += 0.22
        elif phase == "limited_turns_remaining":
            adj -= 1.05
            if gap < -7 and action.draw_from_pool > 0 and pick_value >= 2.0:
                adj += 0.26
            if hand_n <= 3:
                adj += 0.18
        # Human-like opponent adaptation: if predicted opponent pressure is high,
        # drawing from pool to deny key cards becomes more attractive.
        if action.draw_from_pool > 0:
            adj += 0.10 * max(0.0, opp_pred_pressure)
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
        # Explicit game progress awareness.
        if phase == "early_game":
            if action.kind == "play_ocean":
                adj += 0.24
            if immediate < 1.0 and float(f.get("long_term_impact", 0.0)) >= 0.45:
                adj += 0.15
            if combo_setup >= 1.1 and short_sacrifice > 0.0:
                adj += 0.22 * min(1.8, short_sacrifice)
        elif phase == "mid_game":
            if immediate >= 1.0:
                adj += 0.12
            if combo_setup >= 1.0 and short_sacrifice > 0.0:
                adj += 0.16 * min(1.5, short_sacrifice)
        elif phase == "late_game":
            adj += 0.12 * max(0.0, immediate)
            if immediate < 1.0:
                adj -= 0.25
        elif phase == "nearing_game_end":
            adj += 0.18 * max(0.0, immediate)
            if immediate < 1.3:
                adj -= 0.35
            if float(f.get("long_term_impact", 0.0)) > float(f.get("scoring_opportunity", 0.0)) + 0.40:
                adj -= 0.20
        elif phase == "limited_turns_remaining":
            adj += 0.24 * max(0.0, immediate)
            if immediate < 1.6:
                adj -= 0.50
            if float(f.get("scoring_opportunity", 0.0)) >= 1.6:
                adj += 0.22
        # Keep combo lines when they clearly outscale immediate points.
        if combo_setup >= immediate + 1.1 and phase in {"early_game", "mid_game"}:
            adj += 0.20
        # Under visible pressure, favor lines that reduce exposure to traps.
        if opp_pred_pressure >= 1.8 and trap_risk >= 0.8:
            adj -= 0.20

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
    if limited_turns:
        # Endgame forces decisive immediate lines.
        adj += 0.08 * max(0.0, immediate)
        if action.kind == "play_ocean" and open_slots > 1:
            adj -= 0.25
    else:
        # In earlier phases, urgency should not dominate.
        adj += 0.03 * phase_urgency * max(0.0, immediate)

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
    if player.flags.get("play_again", False) or player.flags.get("go_again", False):
        delta += 1.00
    if turn_state.free_followups > 0:
        delta += 1.00
    if (
        player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    ):
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
        score += 0.15 * float(card.cost)
        txt = card.text.lower()
        if "draw" in txt:
            score += 0.20
        if "play again" in txt or "go again" in txt:
            score += 0.30
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
    if turn_state.replay_pickup_used:
        return False
    candidates = replay_pickup_candidates(gs, ms, player, turn_state)
    if not candidates:
        return False

    chosen: Optional[Tuple[int, int]] = None
    if is_human_turn:
        print("Replay pickup required: pick up exactly one card you played this turn.")
        for i, (entry_uid, face_uid) in enumerate(candidates):
            print(f"  [{i}] {entry_short_label(ms, gs, entry_uid)} (face in play: {face_uid}:{gs.card_db[face_uid].name})")
        ans = input("Pickup index: ").strip()
        try:
            idx = int(ans)
            if 0 <= idx < len(candidates):
                chosen = candidates[idx]
        except ValueError:
            chosen = None
        if chosen is None:
            # Mandatory pickup: fallback to first legal option.
            chosen = candidates[0]
    else:
        chosen = ai_choose_replay_pickup(gs, ms, player, turn_state)

    if chosen is None:
        return False

    entry_uid, face_uid = chosen
    if not remove_face_from_board(player, face_uid):
        return False
    player.hand.append(entry_uid)
    sync_reactive_trigger_flags(gs, player)
    turn_state.replay_pickup_used = True
    gs.log.append(
        f"{player.name} picks up {entry_short_label(ms, gs, entry_uid)} before replay."
    )
    if verbose:
        print(f"{player.name} picks up {entry_short_label(ms, gs, entry_uid)} before replay.")
    return True


def choose_optional_draw_count(gs: GameState, player: PlayerState, requested: int) -> int:
    if requested <= 0:
        return 0
    # House rule: if a card says draw, you must draw.
    return requested


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
        "deny_bonus": (0.0, 2.2),
        "overbuild_ocean_penalty": (-6.0, -0.2),
        "strategy_bonus": (0.0, 3.5),
        "novelty_bonus": (0.0, 1.8),
        "branch_bonus": (0.0, 2.8),
        "sim_point_delta": (0.8, 4.0),
        "board_control": (0.0, 2.6),
        "resource_availability": (0.0, 2.2),
        "future_combo_potential": (0.0, 4.0),
        "opponent_threat": (-2.2, 0.0),
        "opponent_prediction_pressure": (-2.8, 0.0),
        "scoring_opportunity": (0.0, 3.8),
        "expected_value": (0.0, 4.2),
        "risk_level": (-2.8, 0.0),
        "long_term_impact": (0.0, 3.0),
        "board_positioning": (0.0, 3.0),
        "combo_setup_value": (0.0, 4.0),
        "short_term_sacrifice_value": (0.0, 2.8),
        "strategic_trap_risk": (-3.4, 0.0),
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
        "species_bonus": 0.5,
        "same_ocean_bonus": 0.6,
        "symbol_bonus": 0.9,
        "stack_bonus": 1.8,
        "plan_fit_bonus": 0.8,
        "deny_bonus": 0.4,
        "overbuild_ocean_penalty": -3.2,
        "strategy_bonus": 1.15,
        "novelty_bonus": 0.45,
        "branch_bonus": 0.75,
        "sim_point_delta": 0.8,
        "board_control": 0.45,
        "resource_availability": 0.35,
        "future_combo_potential": 0.55,
        "opponent_threat": -0.25,
        "opponent_prediction_pressure": -0.35,
        "scoring_opportunity": 0.75,
        "expected_value": 0.9,
        "risk_level": -0.35,
        "long_term_impact": 0.5,
        "board_positioning": 0.65,
        "combo_setup_value": 0.85,
        "short_term_sacrifice_value": 0.45,
        "strategic_trap_risk": -0.75,
    }


DEEP_RL_INPUT_KEYS: List[str] = [
    "bias",
    "is_ocean",
    "uses_star",
    "card_cost",
    "has_plus",
    "target_occupancy",
    "fills_empty_ocean",
    "draw_from_pool",
    "pool_pick_value",
    "immediate_delta",
    "synergy_bonus",
    "species_bonus",
    "same_ocean_bonus",
    "symbol_bonus",
    "stack_bonus",
    "plan_fit_bonus",
    "deny_bonus",
    "overbuild_ocean_penalty",
    "sim_point_delta",
    "board_control",
    "resource_availability",
    "future_combo_potential",
    "opponent_threat",
    "opponent_prediction_pressure",
    "scoring_opportunity",
    "expected_value",
    "risk_level",
    "long_term_impact",
    "board_positioning",
    "combo_setup_value",
    "short_term_sacrifice_value",
    "strategic_trap_risk",
    "phase_progress",
    "phase_pressure",
    "phase_urgency",
    "phase_early_game",
    "phase_mid_game",
    "phase_late_game",
    "phase_nearing_game_end",
    "phase_limited_turns_remaining",
]


def _rand_matrix(rng: random.Random, rows: int, cols: int, scale: float = 0.08) -> List[List[float]]:
    return [[rng.uniform(-scale, scale) for _ in range(cols)] for _ in range(rows)]


def _rand_vector(rng: random.Random, size: int, scale: float = 0.08) -> List[float]:
    return [rng.uniform(-scale, scale) for _ in range(size)]


def default_deep_rl_state(seed: int = 1337) -> Dict[str, Any]:
    """Small dense network (pure Python) used for deep-RL style value learning."""
    input_dim = len(DEEP_RL_INPUT_KEYS) + 5
    h1 = 32
    h2 = 16
    rng = random.Random(seed)
    return {
        "enabled": True,
        "input_dim": input_dim,
        "h1": h1,
        "h2": h2,
        "gamma": 0.93,
        "lr": 0.0025,
        "train_steps": 0,
        "replay_cap": 1500,
        "replay_batch": 16,
        "replay_train_steps": 4,
        "replay": [],
        "w1": _rand_matrix(rng, h1, input_dim),
        "b1": _rand_vector(rng, h1, scale=0.02),
        "w2": _rand_matrix(rng, h2, h1),
        "b2": _rand_vector(rng, h2, scale=0.02),
        "w3": _rand_vector(rng, h2, scale=0.06),
        "b3": 0.0,
    }


def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _relu_grad(x: float) -> float:
    return 1.0 if x > 0.0 else 0.0


def deep_rl_feature_vector(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    synergy_map: Optional[Dict[str, float]] = None,
    species_map: Optional[Dict[str, float]] = None,
    same_ocean_map: Optional[Dict[str, float]] = None,
) -> List[float]:
    feat = action_features(
        gs,
        ms,
        player,
        action,
        synergy_map=synergy_map,
        species_map=species_map,
        same_ocean_map=same_ocean_map,
        include_sim_delta=False,
    )
    vec = [float(feat.get(k, 0.0)) for k in DEEP_RL_INPUT_KEYS]
    vec.extend(
        [
            float(len(player.hand)) / 12.0,
            float(len(player.board_oceans)) / 8.0,
            float(open_slot_count(player)) / 32.0,
            float(endgame_pressure(len(gs.deck))),
            float(max(-40.0, min(40.0, score_gap_vs_table(gs, player)))) / 40.0,
        ]
    )
    return vec


def deep_rl_forward(net: Dict[str, Any], x: List[float]) -> Tuple[List[float], List[float], List[float], List[float], float]:
    w1 = net.get("w1", [])
    b1 = net.get("b1", [])
    w2 = net.get("w2", [])
    b2 = net.get("b2", [])
    w3 = net.get("w3", [])
    b3 = float(net.get("b3", 0.0))
    z1: List[float] = []
    a1: List[float] = []
    for i in range(len(w1)):
        s = float(b1[i]) if i < len(b1) else 0.0
        row = w1[i]
        lim = min(len(row), len(x))
        for j in range(lim):
            s += float(row[j]) * x[j]
        z1.append(s)
        a1.append(_relu(s))

    z2: List[float] = []
    a2: List[float] = []
    for i in range(len(w2)):
        s = float(b2[i]) if i < len(b2) else 0.0
        row = w2[i]
        lim = min(len(row), len(a1))
        for j in range(lim):
            s += float(row[j]) * a1[j]
        z2.append(s)
        a2.append(_relu(s))

    out = b3
    lim = min(len(w3), len(a2))
    for j in range(lim):
        out += float(w3[j]) * a2[j]
    return z1, a1, z2, a2, float(out)


def deep_rl_predict(net: Optional[Dict[str, Any]], x: List[float]) -> float:
    if not isinstance(net, dict) or not bool(net.get("enabled", False)):
        return 0.0
    try:
        _, _, _, _, out = deep_rl_forward(net, x)
        if math.isnan(out) or math.isinf(out):
            return 0.0
        if out > 50.0:
            return 50.0
        if out < -50.0:
            return -50.0
        return out
    except Exception:
        return 0.0


def deep_rl_train_step(net: Optional[Dict[str, Any]], x: List[float], target: float, lr: Optional[float] = None) -> None:
    if not isinstance(net, dict) or not bool(net.get("enabled", False)):
        return
    try:
        alpha = float(net.get("lr", 0.0025) if lr is None else lr)
        z1, a1, z2, a2, out = deep_rl_forward(net, x)
        # MSE loss: (out-target)^2
        d_out = 2.0 * (out - float(target))
        if d_out > 8.0:
            d_out = 8.0
        elif d_out < -8.0:
            d_out = -8.0

        w3 = net.get("w3", [])
        b3 = float(net.get("b3", 0.0))
        w2 = net.get("w2", [])
        b2 = net.get("b2", [])
        w1 = net.get("w1", [])
        b1 = net.get("b1", [])

        old_w3 = [float(v) for v in w3]
        # Output layer update
        lim = min(len(w3), len(a2))
        for j in range(lim):
            w3[j] = float(w3[j]) - alpha * d_out * a2[j]
        b3 = b3 - alpha * d_out

        # Backprop into layer 2
        d_z2 = [0.0 for _ in range(len(a2))]
        for j in range(len(d_z2)):
            d_a2 = d_out * (old_w3[j] if j < len(old_w3) else 0.0)
            d_z2[j] = d_a2 * _relu_grad(z2[j])

        old_w2 = [list(map(float, row)) for row in w2]
        for i in range(len(w2)):
            row = w2[i]
            lim2 = min(len(row), len(a1))
            for j in range(lim2):
                row[j] = float(row[j]) - alpha * d_z2[i] * a1[j]
            if i < len(b2):
                b2[i] = float(b2[i]) - alpha * d_z2[i]

        # Backprop into layer 1
        d_z1 = [0.0 for _ in range(len(a1))]
        for j in range(len(d_z1)):
            accum = 0.0
            for i in range(len(old_w2)):
                row = old_w2[i]
                if j < len(row):
                    accum += d_z2[i] * row[j]
            d_z1[j] = accum * _relu_grad(z1[j])

        for i in range(len(w1)):
            row = w1[i]
            lim1 = min(len(row), len(x))
            for j in range(lim1):
                row[j] = float(row[j]) - alpha * d_z1[i] * x[j]
            if i < len(b1):
                b1[i] = float(b1[i]) - alpha * d_z1[i]

        net["w1"] = w1
        net["b1"] = b1
        net["w2"] = w2
        net["b2"] = b2
        net["w3"] = w3
        net["b3"] = b3
        net["train_steps"] = int(net.get("train_steps", 0)) + 1
    except Exception:
        return


def deep_rl_add_replay(net: Optional[Dict[str, Any]], x: List[float], target: float) -> None:
    if not isinstance(net, dict) or not bool(net.get("enabled", False)):
        return
    try:
        cap = int(net.get("replay_cap", 1500))
        if cap < 32:
            cap = 32
        replay = net.get("replay")
        if not isinstance(replay, list):
            replay = []
            net["replay"] = replay
        replay.append({"x": [float(v) for v in x], "target": float(target)})
        overflow = len(replay) - cap
        if overflow > 0:
            del replay[:overflow]
    except Exception:
        return


def deep_rl_replay_train(net: Optional[Dict[str, Any]]) -> None:
    if not isinstance(net, dict) or not bool(net.get("enabled", False)):
        return
    replay = net.get("replay")
    if not isinstance(replay, list) or not replay:
        return
    try:
        batch = int(net.get("replay_batch", 16))
        steps = int(net.get("replay_train_steps", 4))
        if batch < 1:
            batch = 1
        if steps < 1:
            steps = 1
        if steps > 16:
            steps = 16
        sample_k = min(batch, len(replay))
        for _ in range(steps):
            for item in random.sample(replay, sample_k):
                if not isinstance(item, dict):
                    continue
                x = item.get("x")
                target = item.get("target")
                if not isinstance(x, list) or not isinstance(target, (int, float)):
                    continue
                deep_rl_train_step(net, [float(v) for v in x], float(target))
    except Exception:
        return


def ensure_deep_rl_state(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return default_deep_rl_state()
    state = default_deep_rl_state()
    # Preserve trained weights when shape-compatible.
    for k in (
        "enabled",
        "input_dim",
        "h1",
        "h2",
        "gamma",
        "lr",
        "train_steps",
        "replay_cap",
        "replay_batch",
        "replay_train_steps",
        "replay",
        "w1",
        "b1",
        "w2",
        "b2",
        "w3",
        "b3",
    ):
        if k in raw:
            state[k] = raw[k]
    # Minimal shape safety; reset if inconsistent.
    try:
        inp = int(state.get("input_dim", len(DEEP_RL_INPUT_KEYS) + 5))
        h1 = int(state.get("h1", 32))
        h2 = int(state.get("h2", 16))
        if (
            not isinstance(state.get("w1"), list)
            or len(state["w1"]) != h1
            or any((not isinstance(r, list) or len(r) != inp) for r in state["w1"])
            or not isinstance(state.get("w2"), list)
            or len(state["w2"]) != h2
            or any((not isinstance(r, list) or len(r) != h1) for r in state["w2"])
            or not isinstance(state.get("w3"), list)
            or len(state["w3"]) != h2
        ):
            return default_deep_rl_state()
    except Exception:
        return default_deep_rl_state()
    try:
        cap = int(state.get("replay_cap", 1500))
        if cap < 32:
            cap = 32
        if cap > 8000:
            cap = 8000
        state["replay_cap"] = cap
        batch = int(state.get("replay_batch", 16))
        if batch < 1:
            batch = 1
        if batch > 256:
            batch = 256
        state["replay_batch"] = batch
        replay_steps = int(state.get("replay_train_steps", 4))
        if replay_steps < 1:
            replay_steps = 1
        if replay_steps > 16:
            replay_steps = 16
        state["replay_train_steps"] = replay_steps
        clean_replay: List[Dict[str, Any]] = []
        raw_replay = state.get("replay")
        if isinstance(raw_replay, list):
            for item in raw_replay[-cap:]:
                if not isinstance(item, dict):
                    continue
                x = item.get("x")
                target = item.get("target")
                if not isinstance(x, list) or len(x) != inp or not isinstance(target, (int, float)):
                    continue
                try:
                    clean_replay.append({"x": [float(v) for v in x], "target": float(target)})
                except Exception:
                    continue
        state["replay"] = clean_replay
    except Exception:
        state["replay"] = []
    return state


def load_brain(path: str = BRAIN_PATH) -> Dict[str, object]:
    brain = {
        "weights": default_weights(),
        "deep_rl": default_deep_rl_state(),
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
        "balance_stats": default_balance_stats(),
        "error_stats": default_error_stats(),
        "evolution": {"runs": 0, "last_score_spread": 0.0},
        "games_played": 0,
        "move_updates": 0,
    }
    if not os.path.exists(path):
        brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
        return brain
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if isinstance(data.get("weights"), dict):
                merged = default_weights()
                merged.update({k: float(v) for k, v in data["weights"].items() if isinstance(v, (int, float))})
                brain["weights"] = stabilize_weights(merged)
            if "deep_rl" in data:
                brain["deep_rl"] = ensure_deep_rl_state(data.get("deep_rl"))
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
                for item in data["game_memory"][-400:]:
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
            if isinstance(data.get("balance_stats"), dict):
                loaded = copy.deepcopy(default_balance_stats())
                src = data["balance_stats"]
                if isinstance(src.get("games_analyzed"), (int, float)):
                    loaded["games_analyzed"] = int(src.get("games_analyzed", 0))
                if isinstance(src.get("loop_suspicions"), (int, float)):
                    loaded["loop_suspicions"] = int(src.get("loop_suspicions", 0))
                if isinstance(src.get("card_stats"), dict):
                    clean_cards: Dict[str, Dict[str, float]] = {}
                    for name, row in src["card_stats"].items():
                        if not isinstance(name, str) or not isinstance(row, dict):
                            continue
                        clean_cards[name] = {
                            "plays": int(row.get("plays", 0)),
                            "wins": int(row.get("wins", 0)),
                            "score_sum": float(row.get("score_sum", 0.0)),
                            "score_sq_sum": float(row.get("score_sq_sum", 0.0)),
                        }
                    loaded["card_stats"] = clean_cards
                if isinstance(src.get("strategy_stats"), dict):
                    clean_strat: Dict[str, Dict[str, float]] = {}
                    for label, row in src["strategy_stats"].items():
                        if not isinstance(label, str) or not isinstance(row, dict):
                            continue
                        clean_strat[label] = {
                            "games": int(row.get("games", 0)),
                            "wins": int(row.get("wins", 0)),
                            "score_sum": float(row.get("score_sum", 0.0)),
                            "max_score": float(row.get("max_score", -9999.0)),
                            "extreme_games": int(row.get("extreme_games", 0)),
                        }
                    loaded["strategy_stats"] = clean_strat
                if isinstance(src.get("global"), dict):
                    loaded["global"] = {
                        "samples": int(src["global"].get("samples", 0)),
                        "score_sum": float(src["global"].get("score_sum", 0.0)),
                        "score_sq_sum": float(src["global"].get("score_sq_sum", 0.0)),
                    }
                if isinstance(src.get("broken_combo_suspicions"), dict):
                    loaded["broken_combo_suspicions"] = {
                        str(k): int(v)
                        for k, v in src["broken_combo_suspicions"].items()
                        if isinstance(v, (int, float))
                    }
                if isinstance(src.get("recent_extreme_strategies"), list):
                    loaded["recent_extreme_strategies"] = [
                        x for x in src["recent_extreme_strategies"][-120:] if isinstance(x, dict)
                    ]
                brain["balance_stats"] = loaded
            if isinstance(data.get("error_stats"), dict):
                loaded_err = copy.deepcopy(default_error_stats())
                src_err = data["error_stats"]
                for k in [
                    "illegal_card_placements",
                    "duplicate_cards",
                    "missing_cards",
                    "negative_score_errors",
                    "invalid_actions",
                    "infinite_loop_guards",
                    "rule_violations",
                    "invalid_game_states",
                ]:
                    if isinstance(src_err.get(k), (int, float)):
                        loaded_err[k] = int(src_err.get(k, 0))
                if isinstance(src_err.get("recent"), list):
                    loaded_err["recent"] = [str(x) for x in src_err["recent"][-80:]]
                brain["error_stats"] = loaded_err
            if isinstance(data.get("evolution"), dict):
                brain["evolution"] = {
                    "runs": int(data["evolution"].get("runs", 0)),
                    "last_score_spread": float(data["evolution"].get("last_score_spread", 0.0)),
                }
            if isinstance(data.get("games_played"), int):
                brain["games_played"] = data["games_played"]
            if isinstance(data.get("move_updates"), int):
                brain["move_updates"] = data["move_updates"]
    except Exception:
        return brain
    brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
    # Keep STAR usage and STAR-setup pool drafting as strong policy defaults,
    # even when loading older learned weight files.
    brain["weights"]["uses_star"] = max(0.95, float(brain["weights"].get("uses_star", 0.0)))
    brain["weights"]["pool_pick_value"] = max(0.9, float(brain["weights"].get("pool_pick_value", 0.0)))
    brain["weights"] = stabilize_weights(dict(brain.get("weights", {})))
    brain["deep_rl"] = ensure_deep_rl_state(brain.get("deep_rl"))
    _ensure_balance_stats(brain)
    _ensure_error_stats(brain)
    return brain


def save_brain(brain: Dict[str, object], path: str = BRAIN_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=2, sort_keys=True)


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
        player.flags["play_again"] = True
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


def entry_is_ocean(ms: MatchState, gs: GameState, entry_uid: int) -> bool:
    faces = entry_faces(ms, entry_uid)
    return any(is_ocean(gs.card_db[uid]) for uid in faces)


def entry_label(ms: MatchState, gs: GameState, entry_uid: int) -> str:
    faces = entry_faces(ms, entry_uid)
    if len(faces) == 1:
        return card_label(gs.card_db[faces[0]])
    a = gs.card_db[faces[0]]
    b = gs.card_db[faces[1]]
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
        c = gs.card_db[faces[0]]
        return f"{entry_uid}:{c.name}"
    a = gs.card_db[faces[0]]
    b = gs.card_db[faces[1]]
    return f"{entry_uid}:{a.name}/{b.name}"


def symbol_match_for_entry(ms: MatchState, gs: GameState, entry_uid: int, target_symbol: str) -> bool:
    target = normalize_symbol(target_symbol)
    for uid in entry_faces(ms, entry_uid):
        if normalize_symbol(gs.card_db[uid].symbol) == target:
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
        lines.append(f"Ocean {ocean_uid}: {card_label(ocean)}")
        for dir_name in ["up", "down", "left", "right"]:
            cards = slots.slot(dir_name)
            if not cards:
                continue
            lines.append(f"  {dir_name}:")
            for uid in cards:
                lines.append("    - " + card_label(gs.card_db[uid]))
    return "\n".join(lines)


def player_board_face_uids(player: PlayerState) -> List[int]:
    out: List[int] = []
    out.extend(player.board_oceans)
    for slots in player.ocean_slots.values():
        out.extend(slots.all_cards())
    return out


def default_error_stats() -> Dict[str, Any]:
    return {
        "illegal_card_placements": 0,
        "duplicate_cards": 0,
        "missing_cards": 0,
        "negative_score_errors": 0,
        "invalid_actions": 0,
        "infinite_loop_guards": 0,
        "rule_violations": 0,
        "invalid_game_states": 0,
        "recent": [],
    }


def _ensure_error_stats(brain: Dict[str, object]) -> Dict[str, Any]:
    raw = brain.get("error_stats")
    if not isinstance(raw, dict):
        raw = default_error_stats()
        brain["error_stats"] = raw
    for k, v in default_error_stats().items():
        if k not in raw:
            raw[k] = copy.deepcopy(v)
    if not isinstance(raw.get("recent"), list):
        raw["recent"] = []
    return raw


def record_error_event(brain: Optional[Dict[str, object]], code: str, detail: str = "") -> None:
    if not isinstance(brain, dict):
        return
    stats = _ensure_error_stats(brain)
    key_map = {
        "illegal_card_placement": "illegal_card_placements",
        "duplicate_cards": "duplicate_cards",
        "missing_cards": "missing_cards",
        "negative_score_error": "negative_score_errors",
        "invalid_action": "invalid_actions",
        "infinite_loop": "infinite_loop_guards",
        "rule_violation": "rule_violations",
        "invalid_game_state": "invalid_game_states",
    }
    bucket = key_map.get(code, "invalid_game_states")
    stats[bucket] = int(stats.get(bucket, 0)) + 1
    if detail:
        recent = stats.get("recent")
        if not isinstance(recent, list):
            recent = []
            stats["recent"] = recent
        recent.append(f"{code}: {detail}")
        if len(recent) > 80:
            del recent[:-80]


def default_balance_stats() -> Dict[str, Any]:
    return {
        "games_analyzed": 0,
        "card_stats": {},
        "strategy_stats": {},
        "global": {"samples": 0, "score_sum": 0.0, "score_sq_sum": 0.0},
        "loop_suspicions": 0,
        "broken_combo_suspicions": {},
        "recent_extreme_strategies": [],
    }


def _ensure_balance_stats(brain: Dict[str, object]) -> Dict[str, Any]:
    raw = brain.get("balance_stats")
    if not isinstance(raw, dict):
        raw = default_balance_stats()
        brain["balance_stats"] = raw
    for k, v in default_balance_stats().items():
        if k not in raw:
            raw[k] = copy.deepcopy(v)
    if not isinstance(raw.get("card_stats"), dict):
        raw["card_stats"] = {}
    if not isinstance(raw.get("strategy_stats"), dict):
        raw["strategy_stats"] = {}
    if not isinstance(raw.get("global"), dict):
        raw["global"] = {"samples": 0, "score_sum": 0.0, "score_sq_sum": 0.0}
    if not isinstance(raw.get("broken_combo_suspicions"), dict):
        raw["broken_combo_suspicions"] = {}
    if not isinstance(raw.get("recent_extreme_strategies"), list):
        raw["recent_extreme_strategies"] = []
    return raw


def _entry_from_face(ms: MatchState, uid: int) -> int:
    return int(ms.face_to_primary.get(uid, uid))


def _collect_entry_locations(gs: GameState, ms: MatchState) -> Dict[int, List[str]]:
    locations: Dict[int, List[str]] = {}

    def add(entry_uid: int, where: str) -> None:
        entry = int(entry_uid)
        locations.setdefault(entry, []).append(where)

    for uid in gs.deck:
        add(uid, "deck")
    for uid in ms.pool:
        add(uid, "pool")
    for uid in ms.discard_pile:
        add(uid, "discard_pile")

    for p in gs.players:
        for uid in p.hand:
            add(uid, f"{p.name}.hand")
        for uid in p.discard:
            add(uid, f"{p.name}.discard")
        for ocean_uid in p.board_oceans:
            add(_entry_from_face(ms, ocean_uid), f"{p.name}.board_ocean")
        for slots in p.ocean_slots.values():
            for uid in slots.up:
                add(_entry_from_face(ms, uid), f"{p.name}.slot_up")
            for uid in slots.down:
                add(_entry_from_face(ms, uid), f"{p.name}.slot_down")
            for uid in slots.left:
                add(_entry_from_face(ms, uid), f"{p.name}.slot_left")
            for uid in slots.right:
                add(_entry_from_face(ms, uid), f"{p.name}.slot_right")
    return locations


def validate_match_state(
    gs: GameState,
    ms: MatchState,
    expected_entries: Optional[set[int]] = None,
) -> List[Tuple[str, str]]:
    errs: List[Tuple[str, str]] = []

    for p in gs.players:
        ocean_set = set(p.board_oceans)
        slot_keys = set(p.ocean_slots.keys())
        missing_slots = ocean_set - slot_keys
        orphan_slots = slot_keys - ocean_set
        if missing_slots:
            errs.append(
                ("invalid_game_state", f"{p.name} missing ocean slot containers for: {sorted(missing_slots)}")
            )
        if orphan_slots:
            errs.append(
                ("invalid_game_state", f"{p.name} has slot containers without oceans: {sorted(orphan_slots)}")
            )

        for ocean_uid in p.board_oceans:
            oc = gs.card_db.get(ocean_uid)
            if oc is None:
                errs.append(("invalid_game_state", f"{p.name} board references unknown ocean uid {ocean_uid}"))
                continue
            if not is_ocean(oc):
                errs.append(
                    ("illegal_card_placement", f"{p.name} board_oceans includes non-ocean card {ocean_uid}:{oc.name}")
                )
            slots = p.ocean_slots.get(ocean_uid)
            if not isinstance(slots, OceanSlots):
                continue

            for dir_name in ["up", "down", "left", "right"]:
                cards = list(slots.slot(dir_name))
                if len(cards) > 1:
                    stack_keys = {share_stack_key(gs.card_db[uid]) for uid in cards if uid in gs.card_db}
                    if None in stack_keys or len(stack_keys) != 1:
                        errs.append(
                            (
                                "rule_violation",
                                f"{p.name} illegal stack on ocean {ocean_uid} {dir_name}: {cards}",
                            )
                        )
                for face_uid in cards:
                    cd = gs.card_db.get(face_uid)
                    if cd is None:
                        errs.append(("invalid_game_state", f"{p.name} slot contains unknown face uid {face_uid}"))
                        continue
                    if normalize_direction(cd.direction) != dir_name:
                        errs.append(
                            (
                                "illegal_card_placement",
                                f"{p.name} {face_uid}:{cd.name} placed on {dir_name} but card direction is {cd.direction}",
                            )
                        )

    for p2 in gs.players:
        try:
            score_val = final_points(gs, p2)
        except Exception as exc:
            errs.append(("invalid_game_state", f"score computation failed for {p2.name}: {type(exc).__name__}"))
            continue
        if score_val < 0:
            errs.append(("negative_score_error", f"{p2.name} has negative score {score_val}"))

    locations = _collect_entry_locations(gs, ms)
    for entry_uid, where_list in locations.items():
        if len(where_list) > 1:
            errs.append(
                (
                    "duplicate_cards",
                    f"entry {entry_uid} appears in multiple zones: {', '.join(where_list[:6])}",
                )
            )

    present = set(locations.keys())
    if expected_entries is not None:
        missing = expected_entries - present
        extra = present - expected_entries
        if missing:
            preview = sorted(list(missing))[:10]
            errs.append(("missing_cards", f"missing entries: {preview} (total missing={len(missing)})"))
        if extra:
            preview = sorted(list(extra))[:10]
            errs.append(("invalid_game_state", f"unexpected entries present: {preview} (total extra={len(extra)})"))

    return errs


def update_balance_stats_from_match(gs: GameState, ms: MatchState, brain: Dict[str, object]) -> None:
    if not isinstance(brain, dict):
        return
    stats = _ensure_balance_stats(brain)
    card_stats = stats.get("card_stats")
    strategy_stats = stats.get("strategy_stats")
    global_stats = stats.get("global")
    if not isinstance(card_stats, dict) or not isinstance(strategy_stats, dict) or not isinstance(global_stats, dict):
        return

    finals = [float(final_points(gs, p)) for p in gs.players]
    if not finals:
        return
    top_score = max(finals)
    winners = {i for i, s in enumerate(finals) if s == top_score}

    samples = int(global_stats.get("samples", 0))
    score_sum = float(global_stats.get("score_sum", 0.0))
    score_sq_sum = float(global_stats.get("score_sq_sum", 0.0))

    for i, p in enumerate(gs.players):
        score = float(finals[i])
        samples += 1
        score_sum += score
        score_sq_sum += score * score

        fam = infer_player_strategy_family_label(gs, p)
        fam_key = fam.strip().lower() or "unknown"
        row = strategy_stats.get(fam_key)
        if not isinstance(row, dict):
            row = {"games": 0, "wins": 0, "score_sum": 0.0, "max_score": -9999.0, "extreme_games": 0}
            strategy_stats[fam_key] = row
        row["games"] = int(row.get("games", 0)) + 1
        if i in winners:
            row["wins"] = int(row.get("wins", 0)) + 1
        row["score_sum"] = float(row.get("score_sum", 0.0)) + score
        row["max_score"] = max(float(row.get("max_score", -9999.0)), score)

        board_names = [gs.card_db[uid].name.strip().lower() for uid in player_board_face_uids(p) if uid in gs.card_db]
        for name in board_names:
            cstat = card_stats.get(name)
            if not isinstance(cstat, dict):
                cstat = {"plays": 0, "wins": 0, "score_sum": 0.0, "score_sq_sum": 0.0}
                card_stats[name] = cstat
            cstat["plays"] = int(cstat.get("plays", 0)) + 1
            if i in winners:
                cstat["wins"] = int(cstat.get("wins", 0)) + 1
            cstat["score_sum"] = float(cstat.get("score_sum", 0.0)) + score
            cstat["score_sq_sum"] = float(cstat.get("score_sq_sum", 0.0)) + (score * score)

    global_stats["samples"] = samples
    global_stats["score_sum"] = score_sum
    global_stats["score_sq_sum"] = score_sq_sum
    stats["games_analyzed"] = int(stats.get("games_analyzed", 0)) + 1

    # Loop / broken-combo suspicion log from safety guards.
    guard_events = list(ms.guard_events)
    if guard_events:
        loop_hits = sum(1 for evt in guard_events if "turn-chain" in evt or "loop" in evt.lower())
        if loop_hits > 0:
            stats["loop_suspicions"] = int(stats.get("loop_suspicions", 0)) + loop_hits
        broken = stats.get("broken_combo_suspicions")
        if not isinstance(broken, dict):
            broken = {}
            stats["broken_combo_suspicions"] = broken
        for evt in guard_events:
            if "broken-combo suspect:" in evt:
                label = evt.split("broken-combo suspect:", 1)[-1].strip()
                broken[label] = int(broken.get(label, 0)) + 1

    # Extreme-score strategy detector (mean + 2.5*std once enough samples).
    if samples >= 20:
        mean = score_sum / max(1, samples)
        var = max(0.0, (score_sq_sum / max(1, samples)) - (mean * mean))
        std = math.sqrt(var)
        threshold = mean + (2.5 * std)
        recent_extreme = stats.get("recent_extreme_strategies")
        if not isinstance(recent_extreme, list):
            recent_extreme = []
            stats["recent_extreme_strategies"] = recent_extreme
        for i, p in enumerate(gs.players):
            if finals[i] < threshold:
                continue
            fam = infer_player_strategy_family_label(gs, p)
            fam_key = fam.strip().lower() or "unknown"
            row = strategy_stats.get(fam_key)
            if isinstance(row, dict):
                row["extreme_games"] = int(row.get("extreme_games", 0)) + 1
            recent_extreme.append(
                {
                    "player": p.name,
                    "strategy": fam_key,
                    "score": float(finals[i]),
                    "threshold": float(threshold),
                }
            )
        if len(recent_extreme) > 120:
            del recent_extreme[:-120]

    # Bound map sizes.
    if len(card_stats) > 1200:
        keep = sorted(card_stats.items(), key=lambda kv: int(kv[1].get("plays", 0)), reverse=True)[:1200]
        card_stats.clear()
        card_stats.update({k: v for k, v in keep})
    if len(strategy_stats) > 120:
        keep = sorted(strategy_stats.items(), key=lambda kv: int(kv[1].get("games", 0)), reverse=True)[:120]
        strategy_stats.clear()
        strategy_stats.update({k: v for k, v in keep})


def balance_report_lines(brain: Dict[str, object], top_n: int = 8) -> List[str]:
    stats = brain.get("balance_stats") if isinstance(brain, dict) else None
    if not isinstance(stats, dict):
        return []
    card_stats = stats.get("card_stats")
    global_stats = stats.get("global")
    strategy_stats = stats.get("strategy_stats")
    if not isinstance(card_stats, dict) or not isinstance(global_stats, dict) or not isinstance(strategy_stats, dict):
        return []

    samples = max(1, int(global_stats.get("samples", 0)))
    mean_score = float(global_stats.get("score_sum", 0.0)) / samples

    rows: List[Tuple[str, float, float, int]] = []
    all_wr: List[float] = []
    for name, row in card_stats.items():
        if not isinstance(row, dict):
            continue
        plays = max(0, int(row.get("plays", 0)))
        if plays <= 0:
            continue
        wins = max(0, int(row.get("wins", 0)))
        score_avg = float(row.get("score_sum", 0.0)) / plays
        wr = wins / plays
        rows.append((str(name), score_avg, wr, plays))
        all_wr.append(wr)
    if not rows:
        return []

    baseline_wr = sum(all_wr) / max(1, len(all_wr))
    min_plays = 8
    overpowered = [
        r for r in rows
        if r[3] >= min_plays and r[2] >= baseline_wr + 0.16 and r[1] >= mean_score + 4.0
    ]
    underpowered = [
        r for r in rows
        if r[3] >= min_plays and r[2] <= baseline_wr - 0.14 and r[1] <= mean_score - 3.5
    ]
    overpowered.sort(key=lambda x: (x[2], x[1], x[3]), reverse=True)
    underpowered.sort(key=lambda x: (x[2], x[1], -x[3]))
    lines: List[str] = []

    lines.append(f"Balance baseline: avg_card_win_rate={baseline_wr:.2%} | global_avg_score={mean_score:.2f}")
    if overpowered:
        lines.append("Potential overpowered cards:")
        for name, avg, wr, plays in overpowered[:top_n]:
            lines.append(f"  - {name}: win_rate={wr:.2%}, avg_score={avg:.2f}, plays={plays}")
    else:
        lines.append("Potential overpowered cards: none above confidence threshold.")

    if underpowered:
        lines.append("Potential underpowered cards:")
        for name, avg, wr, plays in underpowered[:top_n]:
            lines.append(f"  - {name}: win_rate={wr:.2%}, avg_score={avg:.2f}, plays={plays}")
    else:
        lines.append("Potential underpowered cards: none above confidence threshold.")

    rare_rows = sorted(rows, key=lambda x: x[3])[:top_n]
    lines.append("Lowest-usage cards (for diversity testing):")
    for name, avg, wr, plays in rare_rows:
        lines.append(f"  - {name}: plays={plays}, win_rate={wr:.2%}, avg_score={avg:.2f}")

    fam_rows: List[Tuple[str, int, int, float, int]] = []
    for fam, row in strategy_stats.items():
        if not isinstance(row, dict):
            continue
        games = max(0, int(row.get("games", 0)))
        if games <= 0:
            continue
        wins = max(0, int(row.get("wins", 0)))
        avg = float(row.get("score_sum", 0.0)) / games
        extreme = max(0, int(row.get("extreme_games", 0)))
        fam_rows.append((str(fam), games, wins, avg, extreme))
    fam_rows.sort(key=lambda x: (x[4], x[3], x[2]), reverse=True)
    if fam_rows:
        lines.append("Strategies with most extreme scores:")
        for fam, games, wins, avg, extreme in fam_rows[:top_n]:
            wr = wins / games
            lines.append(f"  - {fam}: games={games}, win_rate={wr:.2%}, avg_score={avg:.2f}, extremes={extreme}")

    loop_sus = int(stats.get("loop_suspicions", 0))
    lines.append(f"Loop suspicion count: {loop_sus}")
    broken = stats.get("broken_combo_suspicions")
    if isinstance(broken, dict) and broken:
        top_broken = sorted(broken.items(), key=lambda kv: int(kv[1]), reverse=True)[:top_n]
        lines.append("Broken-combo suspects:")
        for label, cnt in top_broken:
            lines.append(f"  - {label}: {int(cnt)}")
    return lines


def diversity_roles_for_game(num_players: int, seed: int) -> List[str]:
    roles = ["combo", "defender", "explorer", "value", "tempo", "opportunist"]
    if num_players <= 0:
        return []
    start = seed % len(roles)
    return [roles[(start + i) % len(roles)] for i in range(num_players)]


def diversity_role_adjustment(
    gs: GameState,
    player: PlayerState,
    action: Action,
    features: Dict[str, float],
    strategy_novelty: float,
    strategy_branch: float,
) -> float:
    if not (human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("diversity_roles", True))):
        return 0.0
    role = str(player.flags.get("_diversity_role", "")).strip().lower()
    if not role:
        return 0.0
    threat, _ = estimate_opponent_threat_level(gs, player)
    adj = 0.0
    if role == "explorer":
        adj += 0.45 * strategy_novelty + 0.20 * strategy_branch
        if action.kind == "draw" and action.draw_from_pool > 0 and float(features.get("pool_pick_value", 0.0)) >= 0.8:
            adj += 0.18
    elif role == "defender":
        adj += 0.55 * float(features.get("deny_bonus", 0.0))
        adj += 0.15 * max(0.0, threat)
    elif role == "combo":
        adj += 0.26 * (
            float(features.get("synergy_bonus", 0.0))
            + float(features.get("species_bonus", 0.0))
            + float(features.get("same_ocean_bonus", 0.0))
            + float(features.get("future_combo_potential", 0.0))
        )
    elif role == "value":
        adj += 0.28 * (
            float(features.get("immediate_delta", 0.0))
            + float(features.get("scoring_opportunity", 0.0))
            + float(features.get("expected_value", 0.0))
        )
    elif role == "tempo":
        adj += 0.20 * float(features.get("fills_empty_ocean", 0.0))
        adj += 0.12 * float(features.get("target_occupancy", 0.0))
        adj += 0.20 * float(features.get("uses_star", 0.0))
    elif role == "opportunist":
        pressure = endgame_pressure(len(gs.deck))
        adj += 0.16 * pressure * float(features.get("expected_value", 0.0))
    if adj > 2.0:
        return 2.0
    if adj < -2.0:
        return -2.0
    return adj


def rare_combo_exploration_bonus(
    strategy_count_map: Optional[Dict[str, int]],
    signature: str,
    base_score: float,
) -> float:
    if not (human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("rare_combo_exploration", True))):
        return 0.0
    if not isinstance(strategy_count_map, dict):
        return 0.0
    c = max(0, int(strategy_count_map.get(signature, 0)))
    # "Test rare combinations (don't test stupid ones)":
    # only boost if the base score is not clearly bad.
    if base_score < -0.2:
        return 0.0
    if c == 0 and base_score >= 0.15:
        return 0.35
    if c <= 2 and base_score >= 0.0:
        return 0.18
    if c <= 5 and base_score >= 0.3:
        return 0.08
    return 0.0


def compute_action_meta_metrics(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
    features: Dict[str, float],
) -> Dict[str, float]:
    progress_state = game_progress_state(gs, ms)
    phase = str(progress_state.get("phase", "mid_game"))
    phase_progress = float(progress_state.get("progress", 0.5))
    phase_pressure = float(progress_state.get("pressure", endgame_pressure(len(gs.deck))))
    phase_urgency = float(progress_state.get("urgency", phase_pressure))
    limited_turns = bool(progress_state.get("limited_turns_remaining", False))

    open_slots = float(open_slot_count(player))
    occupied_slots = max(0.0, 32.0 - open_slots)
    board_control = (occupied_slots / 32.0) + (0.14 * len(player.board_oceans))
    if action.kind == "play_to_ocean":
        board_control += 0.18

    expected_hand = float(len(player.hand))
    if action.kind == "draw":
        expected_hand += 2.0
    elif action.kind in {"play_ocean", "play_to_ocean"}:
        expected_hand -= 1.0
    resource_availability = max(0.0, min(1.8, expected_hand / 10.0))

    future_combo = (
        float(features.get("synergy_bonus", 0.0))
        + float(features.get("species_bonus", 0.0))
        + float(features.get("same_ocean_bonus", 0.0))
        + float(features.get("plan_fit_bonus", 0.0))
    )
    opponent_threat, _ = estimate_opponent_threat_level(gs, player)
    excluded_pool_uids = set(action.pool_pick_uids) if (action.kind == "draw" and action.pool_pick_uids) else None
    opponent_pred = aggregate_opponent_predictions(gs, ms, player, excluded_pool_uids=excluded_pool_uids)
    opponent_prediction_pressure = max(
        float(opponent_pred.get("avg_pressure", 0.0)),
        0.75 * float(opponent_pred.get("peak_pressure", 0.0)),
    )
    scoring_opportunity = max(0.0, float(features.get("immediate_delta", 0.0))) + (0.4 * max(0.0, float(features.get("sim_point_delta", 0.0))))

    risk_level = 0.0
    if action.kind != "draw":
        play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        card = gs.card_db.get(play_face_uid)
        if card is not None:
            risk_level += max(0.0, float(card.cost) * 0.3)
            if action.use_star and card.cost > 0:
                risk_level += 0.5
            if action.kind == "play_ocean" and count_empty_oceans(player) > 0:
                risk_level += 0.35
    else:
        risk_level += 0.1

    long_term = 0.0
    if action.kind != "draw":
        play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        card = gs.card_db.get(play_face_uid)
        if card is not None:
            txt = card.text.lower()
            if "draw" in txt:
                long_term += 0.45
            if "per " in txt:
                long_term += 0.40
            if "play again" in txt or "go again" in txt:
                long_term += 0.35
            if "for free" in txt or "free " in txt:
                long_term += 0.30
            if is_ocean(card) and len(player.board_oceans) == 0:
                long_term += 0.35
    else:
        long_term += 0.25

    board_positioning = action_board_positioning_score(gs, player, action)
    combo_setup_value = max(0.0, (0.58 * future_combo) + (0.32 * long_term) + (0.24 * board_positioning) - (0.20 * risk_level))
    short_term_sacrifice_value = max(0.0, combo_setup_value - scoring_opportunity)
    strategic_trap_risk = action_strategic_trap_risk(gs, ms, player, action)

    # Game-progress aware weights: setup early, convert to points late.
    if phase == "early_game":
        w_score, w_combo, w_long = 0.42, 0.42, 0.36
        w_board, w_resource, w_risk, w_threat = 0.20, 0.22, 0.24, 0.10
        w_pos, w_combo_setup, w_sacrifice, w_trap, w_opp_pred = 0.34, 0.44, 0.32, 0.30, 0.14
    elif phase == "mid_game":
        w_score, w_combo, w_long = 0.55, 0.35, 0.28
        w_board, w_resource, w_risk, w_threat = 0.22, 0.18, 0.28, 0.14
        w_pos, w_combo_setup, w_sacrifice, w_trap, w_opp_pred = 0.30, 0.36, 0.24, 0.34, 0.18
    elif phase == "late_game":
        w_score, w_combo, w_long = 0.72, 0.28, 0.20
        w_board, w_resource, w_risk, w_threat = 0.26, 0.16, 0.30, 0.18
        w_pos, w_combo_setup, w_sacrifice, w_trap, w_opp_pred = 0.34, 0.24, 0.14, 0.42, 0.24
    elif phase == "nearing_game_end":
        w_score, w_combo, w_long = 0.86, 0.24, 0.14
        w_board, w_resource, w_risk, w_threat = 0.30, 0.14, 0.32, 0.22
        w_pos, w_combo_setup, w_sacrifice, w_trap, w_opp_pred = 0.38, 0.18, 0.08, 0.52, 0.30
    else:  # limited_turns_remaining
        w_score, w_combo, w_long = 0.98, 0.18, 0.08
        w_board, w_resource, w_risk, w_threat = 0.34, 0.12, 0.35, 0.25
        w_pos, w_combo_setup, w_sacrifice, w_trap, w_opp_pred = 0.44, 0.10, 0.04, 0.62, 0.34

    expected_value = (
        (w_score * scoring_opportunity)
        + (w_combo * future_combo)
        + (w_long * long_term)
        + (w_board * board_control)
        + (w_resource * resource_availability)
        + (w_pos * board_positioning)
        + (w_combo_setup * combo_setup_value)
        + (w_sacrifice * short_term_sacrifice_value)
        - (w_risk * risk_level)
        - (w_threat * opponent_threat)
        - (w_opp_pred * opponent_prediction_pressure)
        - (w_trap * strategic_trap_risk)
    )
    if action.kind == "draw" and (phase in {"nearing_game_end", "limited_turns_remaining"}):
        expected_value -= 0.40 + (0.45 * phase_urgency)
        if action.draw_from_pool > 0 and float(features.get("pool_pick_value", 0.0)) >= 1.8:
            expected_value += 0.28
    if limited_turns and action.kind != "draw":
        expected_value += 0.10 * max(0.0, float(features.get("immediate_delta", 0.0)))

    return {
        "board_control": float(board_control),
        "resource_availability": float(resource_availability),
        "future_combo_potential": float(future_combo),
        "opponent_threat": float(opponent_threat),
        "opponent_prediction_pressure": float(opponent_prediction_pressure),
        "scoring_opportunity": float(scoring_opportunity),
        "expected_value": float(expected_value),
        "risk_level": float(risk_level),
        "long_term_impact": float(long_term),
        "board_positioning": float(board_positioning),
        "combo_setup_value": float(combo_setup_value),
        "short_term_sacrifice_value": float(short_term_sacrifice_value),
        "strategic_trap_risk": float(strategic_trap_risk),
        "phase_progress": float(phase_progress),
        "phase_pressure": float(phase_pressure),
        "phase_urgency": float(phase_urgency),
        "phase_early_game": 1.0 if phase == "early_game" else 0.0,
        "phase_mid_game": 1.0 if phase == "mid_game" else 0.0,
        "phase_late_game": 1.0 if phase == "late_game" else 0.0,
        "phase_nearing_game_end": 1.0 if phase == "nearing_game_end" else 0.0,
        "phase_limited_turns_remaining": 1.0 if phase == "limited_turns_remaining" else 0.0,
    }


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
    if "matching symbol" in text:
        tags.add("engine:symbol")
    if "play a free" in text:
        tags.add("engine:freeplay")
    if "play again" in text or "go again" in text:
        tags.add("engine:tempo")
    if "draw" in text:
        tags.add("engine:draw")
    return tags


def board_strategy_profile(gs: GameState, player: PlayerState) -> Dict[str, int]:
    prof: Dict[str, int] = {}
    for uid in player_board_face_uids(player):
        c = gs.card_db[uid]
        for t in card_strategy_tags(c):
            prof[t] = prof.get(t, 0) + 1
    return prof


def entry_strategy_tags(ms: MatchState, gs: GameState, entry_uid: int) -> set[str]:
    tags: set[str] = set()
    for face_uid in entry_faces(ms, entry_uid):
        c = gs.card_db.get(face_uid)
        if c is None:
            continue
        tags.update(card_strategy_tags(c))
    return tags


def _observed_intent_public(player: PlayerState) -> Dict[str, float]:
    raw = player.flags.get("_observed_intent_public")
    if isinstance(raw, dict):
        out: Dict[str, float] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, (int, float)):
                out[k] = float(v)
        return out
    return {}


def _set_observed_intent_public(player: PlayerState, data: Dict[str, float]) -> None:
    clean = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float)) and float(v) > 0.0}
    player.flags["_observed_intent_public"] = clean


def _observed_playstyle_public(player: PlayerState) -> Dict[str, float]:
    raw = player.flags.get("_observed_playstyle_public")
    if isinstance(raw, dict):
        out: Dict[str, float] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, (int, float)):
                out[k] = float(v)
        return out
    return {}


def _set_observed_playstyle_public(player: PlayerState, data: Dict[str, float]) -> None:
    clean = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float)) and float(v) >= 0.0}
    player.flags["_observed_playstyle_public"] = clean


def record_observed_playstyle_from_action(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    action: Action,
) -> None:
    obs = _observed_playstyle_public(player)
    obs["actions_total"] = float(obs.get("actions_total", 0.0)) + 1.0
    if action.kind == "draw":
        obs["draw_actions"] = float(obs.get("draw_actions", 0.0)) + 1.0
        if action.draw_from_pool > 0:
            obs["pool_draw_actions"] = float(obs.get("pool_draw_actions", 0.0)) + 1.0
        if action.pool_pick_uids:
            obs["pool_pick_count"] = float(obs.get("pool_pick_count", 0.0)) + float(len(action.pool_pick_uids))
            # Approximate defensive drafting intent from visible pool value asymmetry.
            denied = 0
            for uid in action.pool_pick_uids:
                self_v = pool_entry_value_for_player(ms, gs, uid, player)
                opp_best = 0.0
                for op in gs.players:
                    if op is player:
                        continue
                    v = pool_entry_value_for_player(ms, gs, uid, op)
                    if v > opp_best:
                        opp_best = v
                if opp_best > self_v + 0.75:
                    denied += 1
            if denied > 0:
                obs["deny_pool_picks"] = float(obs.get("deny_pool_picks", 0.0)) + float(denied)
    else:
        obs["play_actions"] = float(obs.get("play_actions", 0.0)) + 1.0
        if action.kind == "play_ocean":
            obs["play_ocean_actions"] = float(obs.get("play_ocean_actions", 0.0)) + 1.0
        elif action.kind == "play_to_ocean":
            obs["play_attach_actions"] = float(obs.get("play_attach_actions", 0.0)) + 1.0
        if action.use_star:
            obs["star_uses"] = float(obs.get("star_uses", 0.0)) + 1.0
    if (
        player.flags.get("play_again", False)
        or player.flags.get("go_again", False)
        or player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    ):
        obs["chain_turn_signals"] = float(obs.get("chain_turn_signals", 0.0)) + 1.0
    _set_observed_playstyle_public(player, obs)


def infer_observed_playstyle_label(player: PlayerState) -> str:
    obs = _observed_playstyle_public(player)
    total = float(obs.get("actions_total", 0.0))
    if total < 4.0:
        return "UNKNOWN"
    draw_actions = float(obs.get("draw_actions", 0.0))
    play_actions = float(obs.get("play_actions", 0.0))
    pool_draw_actions = float(obs.get("pool_draw_actions", 0.0))
    attach_actions = float(obs.get("play_attach_actions", 0.0))
    ocean_actions = float(obs.get("play_ocean_actions", 0.0))
    star_uses = float(obs.get("star_uses", 0.0))
    chain = float(obs.get("chain_turn_signals", 0.0))
    deny_picks = float(obs.get("deny_pool_picks", 0.0))

    draw_rate = draw_actions / max(1.0, total)
    pool_draw_rate = pool_draw_actions / max(1.0, draw_actions)
    attach_rate = attach_actions / max(1.0, play_actions)
    ocean_rate = ocean_actions / max(1.0, play_actions)
    star_rate = star_uses / max(1.0, play_actions)
    chain_rate = chain / max(1.0, play_actions)
    deny_rate = deny_picks / max(1.0, pool_draw_actions)

    if pool_draw_rate >= 0.58 and deny_rate >= 0.30:
        return "DENIAL_DRAFTER"
    if draw_rate >= 0.56 and attach_rate < 0.45:
        return "CONSERVATIVE"
    if (attach_rate >= 0.60 and draw_rate <= 0.36) or chain_rate >= 0.24 or star_rate >= 0.35:
        return "AGGRESSIVE"
    if ocean_rate >= 0.45 and draw_rate >= 0.30:
        return "SETUP"
    if attach_rate >= 0.54 and draw_rate < 0.50:
        return "COMBO_PRESSURE"
    return "OPPORTUNISTIC"


def style_threat_multiplier(style_label: str) -> float:
    key = str(style_label or "").strip().upper()
    if key in {"AGGRESSIVE", "COMBO_PRESSURE"}:
        return 1.16
    if key == "DENIAL_DRAFTER":
        return 1.08
    if key == "SETUP":
        return 0.98
    if key == "CONSERVATIVE":
        return 0.90
    return 1.0


def predict_opponent_next_turn(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    opponent: PlayerState,
    excluded_pool_uids: Optional[set[int]] = None,
) -> Dict[str, Any]:
    # Human-like prediction: use visible board + pool only, no hidden hand/deck knowledge.
    excluded = excluded_pool_uids or set()
    family_label = infer_visible_strategy_family_label(gs, opponent).strip().lower()
    family_profile = strategy_family_profile_by_label(family_label)
    style_label = infer_observed_playstyle_label(opponent)
    style_mult = style_threat_multiplier(style_label)
    open_slots = open_slot_count(opponent)
    base_pressure = opponent_strategy_pressure(gs, opponent)

    pool_best = 0.0
    pool_family_best = 0.0
    pool_best_uid: Optional[int] = None
    for uid in ms.pool:
        if uid in excluded:
            continue
        v = pool_entry_value_for_player(ms, gs, uid, opponent)
        if v > pool_best:
            pool_best = v
            pool_best_uid = uid
        if isinstance(family_profile, dict):
            fam_v = entry_best_strategy_family_score(ms, gs, uid, family_profile)
            if fam_v > pool_family_best:
                pool_family_best = fam_v

    draw_intent = 0.35 + (0.10 * pool_best) + (0.06 * pool_family_best)
    play_intent = 0.38 + (0.12 * min(4, open_slots)) + (0.24 * base_pressure)
    if not opponent.board_oceans:
        play_intent += 0.32
    if style_label == "DENIAL_DRAFTER":
        draw_intent += 0.22
    elif style_label == "CONSERVATIVE":
        draw_intent += 0.12
    elif style_label in {"AGGRESSIVE", "COMBO_PRESSURE"}:
        play_intent += 0.16

    likely_move = "play_board" if play_intent >= draw_intent else "draw_pool"
    predicted_swing = (
        (0.55 * base_pressure)
        + (0.26 * pool_best)
        + (0.14 * pool_family_best)
        + (0.10 * min(1.0, open_slots / 4.0))
    ) * style_mult
    if likely_move == "draw_pool":
        predicted_swing += 0.12 * pool_best

    return {
        "opponent": opponent.name,
        "family": family_label or "unknown",
        "style": style_label,
        "likely_move": likely_move,
        "pool_best_uid": pool_best_uid,
        "pool_best_value": float(pool_best),
        "predicted_swing": float(predicted_swing),
    }


def aggregate_opponent_predictions(
    gs: GameState,
    ms: MatchState,
    player: PlayerState,
    excluded_pool_uids: Optional[set[int]] = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for op in gs.players:
        if op is player:
            continue
        rows.append(
            predict_opponent_next_turn(
                gs,
                ms,
                player,
                op,
                excluded_pool_uids=excluded_pool_uids,
            )
        )
    if not rows:
        return {
            "avg_pressure": 0.0,
            "peak_pressure": 0.0,
            "pool_hot_value": 0.0,
            "pool_hot_uid": None,
            "high_risk_count": 0,
            "rows": [],
        }
    avg_p = sum(float(r.get("predicted_swing", 0.0)) for r in rows) / len(rows)
    peak_row = max(rows, key=lambda r: float(r.get("predicted_swing", 0.0)))
    pool_hot_row = max(rows, key=lambda r: float(r.get("pool_best_value", 0.0)))
    high_risk = sum(1 for r in rows if float(r.get("predicted_swing", 0.0)) >= 2.0)
    return {
        "avg_pressure": float(avg_p),
        "peak_pressure": float(peak_row.get("predicted_swing", 0.0)),
        "pool_hot_value": float(pool_hot_row.get("pool_best_value", 0.0)),
        "pool_hot_uid": pool_hot_row.get("pool_best_uid"),
        "high_risk_count": int(high_risk),
        "rows": rows,
    }


def record_revealed_intent_from_face(player: PlayerState, card: CardDef, weight: float = 1.0) -> None:
    if weight <= 0.0:
        return
    obs = _observed_intent_public(player)
    for tag in card_strategy_tags(card):
        obs[tag] = float(obs.get(tag, 0.0)) + float(weight)

    lname = card.name.strip().lower()
    # Lightweight tactical inference hints.
    if lname == "mantis shrimp":
        obs["intent:mandarin_goby_hint"] = float(obs.get("intent:mandarin_goby_hint", 0.0)) + (1.8 * float(weight))
    elif lname == "mandarin goby":
        obs["intent:mandarin_goby_hint"] = float(obs.get("intent:mandarin_goby_hint", 0.0)) + (2.6 * float(weight))

    _set_observed_intent_public(player, obs)


def record_revealed_intent_from_entry(ms: MatchState, gs: GameState, player: PlayerState, entry_uid: int, weight: float = 0.55) -> None:
    faces = entry_faces(ms, entry_uid)
    if not faces:
        return
    per_face = float(weight) / float(len(faces))
    for face_uid in faces:
        c = gs.card_db.get(face_uid)
        if c is None:
            continue
        record_revealed_intent_from_face(player, c, weight=per_face)


def _cleanup_defense_hold(player: PlayerState) -> Dict[int, int]:
    raw = player.flags.get("_defense_hold_turns")
    hold: Dict[int, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, int) and isinstance(v, (int, float)) and int(v) > 0:
                hold[int(k)] = int(v)
    hand_set = set(player.hand)
    for uid in list(hold.keys()):
        if uid not in hand_set:
            del hold[uid]
    player.flags["_defense_hold_turns"] = hold
    return hold


def age_defense_hold_for_turn_start(player: PlayerState) -> None:
    hold = _cleanup_defense_hold(player)
    for uid in list(hold.keys()):
        hold[uid] = int(hold[uid]) - 1
        if hold[uid] <= 0:
            del hold[uid]
    player.flags["_defense_hold_turns"] = hold


def opponent_strategy_pressure(gs: GameState, opponent: PlayerState) -> float:
    board_cards = _board_cards(gs, opponent)
    mult = 0
    enab = 0
    for c in board_cards:
        t = c.text.lower()
        if " per " in t or re.search(r"\b\d+\s*=\s*\d+", t):
            mult += 1
        if any(k in t for k in ("draw one", "draw two", "draw 2", "draw three", "play again", "go again", "for free")):
            enab += 1
    open_slots = open_slot_count(opponent)
    obs = _observed_intent_public(opponent)
    label = infer_visible_strategy_family_label(gs, opponent).strip().lower()
    family_profile = strategy_family_profile_by_label(label)
    family_hint = 0.0
    if isinstance(family_profile, dict):
        fam_species = [str(x).strip().lower() for x in family_profile.get("species", [])]
        for sp in fam_species:
            family_hint += float(obs.get(f"species:{sp}", 0.0))
    family_hint += float(obs.get("intent:mandarin_goby_hint", 0.0))

    style_label = infer_observed_playstyle_label(opponent)
    pressure = 0.45 + (0.10 * mult) + (0.07 * enab)
    if open_slots >= 3:
        pressure += 0.20
    elif open_slots >= 1:
        pressure += 0.08
    pressure += min(0.65, 0.05 * family_hint)
    pressure *= style_threat_multiplier(style_label)
    if pressure < 0.30:
        return 0.30
    if pressure > 1.80:
        return 1.80
    return pressure


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

    # Small diversity reward: a human often keeps optional lines alive.
    bonus += 0.06 * sum(1 for t in tags if profile.get(t, 0) == 0)

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
                "common octopus",
                "giant squid",
                "bobtail squid",
                "cuttlefish",
            ],
            "support_names": ["reef trigger fish", "manta ray", "humuhumunukunukuapua'a", "humuhumu-nukunuku-apua'a"],
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
    # Learn family effectiveness faster (earlier confidence ramp).
    confidence = min(1.0, games / 12.0)
    bias = ((win_rate - 0.35) * 2.1) + min(1.5, avg_score / 36.0)
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


def infer_visible_strategy_family_label(gs: GameState, player: PlayerState) -> str:
    board_uids = player_board_face_uids(player)
    if board_uids:
        return infer_player_strategy_family_label(gs, player)
    obs = _observed_intent_public(player)
    if not obs:
        return "unknown"
    best_label = "unknown"
    best_score = 0.0
    species_to_engine = {
        "bird": "engine:bird",
        "crustacean": "engine:crustacean",
        "baitfish": "engine:baitfish",
        "game fish": "engine:gamefish",
        "cephalopod": "engine:cephalopod",
        "mammal": "engine:mammal",
        "coral": "engine:coral",
        "ocean": "engine:ocean",
    }
    for fam in strategy_family_profiles():
        label = str(fam.get("label", "unknown"))
        score = 0.0
        for sp in fam.get("species", []):
            sp_name = str(sp).strip().lower()
            if not sp_name:
                continue
            score += float(obs.get(f"species:{sp_name}", 0.0))
            et = species_to_engine.get(sp_name)
            if et:
                score += 0.9 * float(obs.get(et, 0.0))
        if label == "birds_crustaceans":
            birds = float(obs.get("species:bird", 0.0))
            crust = float(obs.get("species:crustacean", 0.0))
            score += min(birds, crust)
        if score > best_score:
            best_score = score
            best_label = label
    if best_score < 0.35:
        return "unknown"
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


def pretty_card_label(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return "Unknown"
    pieces = []
    small = {"and", "or", "of", "the", "a", "an", "if", "with", "per"}
    for i, w in enumerate(s.split()):
        wl = w.lower()
        if wl in {"n/a", "na"}:
            pieces.append("N/A")
        elif i > 0 and wl in small:
            pieces.append(wl)
        elif wl == "mahi":
            pieces.append("Mahi")
        else:
            pieces.append(wl.capitalize())
    out = " ".join(pieces)
    out = out.replace("Wrasse", "Wrasse")
    out = out.replace("Goby", "Goby")
    return out


def pretty_strategy_family_label(label: str) -> str:
    key = str(label or "").strip().lower()
    mapping = {
        "birds": "Birds",
        "birds_crustaceans": "Birds + Crustaceans",
        "game_fish": "Game Fish",
        "cephalopods": "Cephalopods",
        "unknown": "Unknown",
    }
    if key in mapping:
        return mapping[key]
    return pretty_card_label(key.replace("_", " "))


def split_pair_key(key: str) -> Tuple[str, str]:
    parts = str(key or "").split("|", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return str(key or "").strip(), ""


def describe_action_signature(sig: str) -> str:
    s = str(sig or "").strip()
    if not s:
        return "unknown action"
    if s.startswith("draw|"):
        mode = s.split("|", 1)[1]
        if mode.startswith("p2"):
            return "draw from pool"
        if mode.startswith("p1"):
            return "draw mixed (pool + deck)"
        return "draw from deck"
    parts = s.split("|")
    if len(parts) < 6:
        return s
    kind, species, symbol, direction, star, tags = parts[:6]
    kind_txt = "attach" if kind == "attach" else ("play ocean" if kind == "ocean" else kind)
    sp_txt = "N/A" if species == "n/a" else species
    sym_txt = symbol.upper() if symbol != "n/a" else "n/a"
    star_txt = " with STAR" if star == "star" else ""
    tag_txt = "" if tags in {"", "none"} else f" [{tags}]"
    return f"{kind_txt} {sp_txt} ({sym_txt}, {direction}){star_txt}{tag_txt}"


def describe_transition_key(key: str) -> str:
    txt = str(key or "")
    if "=>" not in txt:
        return txt
    a, b = txt.split("=>", 1)
    return f"{describe_action_signature(a)} -> {describe_action_signature(b)}"


def discovered_strategy_insights(brain: Dict[str, object], top_n: int = 10) -> List[str]:
    lines: List[str] = []
    if not isinstance(brain, dict):
        return lines

    family_stats = brain.get("strategy_family_stats")
    if isinstance(family_stats, dict):
        fam_rows: List[Tuple[float, str, float, float, float]] = []
        for label, rec in family_stats.items():
            if not isinstance(rec, dict):
                continue
            games = max(0.0, float(rec.get("games", 0.0)))
            if games < 2.0:
                continue
            wins = max(0.0, float(rec.get("wins", 0.0)))
            score_sum = float(rec.get("score_sum", 0.0))
            win_rate = wins / games if games > 0 else 0.0
            avg_score = score_sum / games if games > 0 else 0.0
            confidence = min(1.0, games / 15.0)
            power = ((win_rate - 0.25) * 2.2 + (avg_score / 30.0)) * (0.2 + 0.8 * confidence)
            fam_rows.append((power, str(label), games, win_rate, avg_score))
        fam_rows.sort(key=lambda x: x[0], reverse=True)
        for _, label, games, win_rate, avg in fam_rows[:3]:
            lines.append(
                f"Family {pretty_strategy_family_label(label)}: win {win_rate*100:.0f}% over {games:.0f} games, avg {avg:.1f} points."
            )

    same_ocean = brain.get("same_ocean_synergy")
    if isinstance(same_ocean, dict):
        pair_rows = [(str(k), float(v)) for k, v in same_ocean.items() if isinstance(v, (int, float)) and float(v) > 0.25]
        pair_rows.sort(key=lambda kv: kv[1], reverse=True)
        for key, v in pair_rows[:3]:
            a, b = split_pair_key(key)
            if a and b:
                lines.append(f"Same-ocean combo: {pretty_card_label(a)} + {pretty_card_label(b)} (strength {v:.2f}).")

    synergy = brain.get("synergy")
    if isinstance(synergy, dict):
        mix_rows = [(str(k), float(v)) for k, v in synergy.items() if isinstance(v, (int, float)) and float(v) > 0.20]
        mix_rows.sort(key=lambda kv: kv[1], reverse=True)
        for key, v in mix_rows[:2]:
            a, b = split_pair_key(key)
            if a and b:
                lines.append(f"Cross-board combo: {pretty_card_label(a)} + {pretty_card_label(b)} (strength {v:.2f}).")

    transitions = brain.get("strategy_transition")
    transition_counts = brain.get("strategy_transition_count")
    if isinstance(transitions, dict) and isinstance(transition_counts, dict):
        trans_rows: List[Tuple[float, str, float, int]] = []
        for k, v in transitions.items():
            if not isinstance(v, (int, float)):
                continue
            vv = float(v)
            cnt = int(transition_counts.get(k, 0))
            if vv <= 0.12 or cnt < 3:
                continue
            strength = vv * (1.0 + min(2.0, cnt / 6.0))
            trans_rows.append((strength, str(k), vv, cnt))
        trans_rows.sort(key=lambda x: x[0], reverse=True)
        for _, key, vv, cnt in trans_rows[:2]:
            lines.append(f"Sequence edge: {describe_transition_key(key)} (strength {vv:.2f}, seen {cnt}x).")

    if len(lines) > top_n:
        lines = lines[:top_n]
    return lines


def default_archetype_profiles() -> List[Dict[str, Any]]:
    return [
        {
            "label": "Birds",
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
            "name_contains": ["penguin", "puffin", "seagull", "pelican", "albatross", "osprey", "frigatebird", "auk"],
            "text_keywords": ["per bird"],
            # Bird variant support package:
            # California Seagull + crustaceans, and Sea Urchin with bird-heavy boards.
            "support_names": ["mantis shrimp", "lobster", "spiny lobster", "sea urchin"],
        },
        {
            "label": "Baitfish Engine",
            "species": ["baitfish"],
            "names": [
                "mullet",
                "bunker",
                "sardine",
                "flying fish",
                "bonito",
                "amberjack",
                "whale shark",
            ],
            "name_contains": [],
            "text_keywords": ["baitfish", "whale shark", "amberjack", "per baitfish", "sharing an ocean with baitfish"],
            "support_names": ["sea cucumber", "sea urchin"],
        },
        {
            "label": "Yellowfin BigEye Cleaner",
            "species": [],
            "names": ["yellowfin tuna", "big eye tuna", "cleaner wrasse", "sailfish", "clownfish"],
            "name_contains": [],
            "text_keywords": ["yellowfin tuna", "big eye tuna", "cleaner wrasse", "free game fish"],
            "support_names": ["sea cucumber", "artificial reef", "clownfish"],
        },
        {
            "label": "Mammals Sharks",
            "species": ["mammal"],
            "names": [
                "great white shark",
                "spinner dolphin",
                "bottlenose dolphin",
                "narwhal",
            ],
            "name_contains": ["dolphin", "shark"],
            "text_keywords": ["per mammal", "free mammal"],
            "support_names": [
                "elk horn coral",
                "staghorn coral",
                "stagehorn coral",
                "deep sea coral",
                "red tree coral",
                "king salmon",
            ],
        },
        {
            "label": "Cephalopods",
            "species": ["cephalopod"],
            "names": [
                "bobtail squid",
                "cuttlefish",
                "common octopus",
                "commen octopus",
                "gaint squid",
                "giant squid",
            ],
            "name_contains": ["octopus", "squid", "cuttlefish", "humuhumu"],
            "text_keywords": ["cephalopod", "at least three cephalopods", "free cephalopods"],
            "support_names": ["manta ray", "humuhumunukunukuapua'a", "humuhumu-nukunuku-apua'a"],
        },
        {
            "label": "King Salmon Coral Fill",
            "species": ["coral", "game fish"],
            "names": [
                "king salmon",
                "red tree coral",
                "clownfish",
                "artificial reef",
                "coral reef",
                "staghorn coral",
                "elk horn coral",
                "deep sea coral",
            ],
            "name_contains": [],
            "text_keywords": ["fully occupied ocean", "sharing an ocean with a king salmon", "card attached"],
            "support_names": ["sea star", "sea urchin", "sea anemone", "bottlenose dolphin"],
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
    # between remaining archetypes (currently Mammals vs Cephalopods).
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
    score = card_archetype_score(card, profile) if isinstance(profile, dict) else 0.0
    label = str(profile.get("label", "")).strip().lower() if isinstance(profile, dict) else ""
    board_cards = [gs.card_db[uid] for uid in player_board_face_uids(player)]
    board_names = [c.name.strip().lower() for c in board_cards]
    board_species = [c.species.strip().lower() for c in board_cards]

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
    bonus = score if score > 0 else (-0.2 if isinstance(family_profile, dict) else -0.35)
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
    cname = card.name.strip().lower()
    cspecies = card.species.strip().lower()
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
            bonus += min(2.0, 0.4 * game_fish_count)

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
    if bonus < -2.5:
        return -2.5
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

    hold = _cleanup_defense_hold(player)
    gap = score_gap_vs_table(gs, player)
    # Defense is useful, but over-defending should taper off.
    defense_budget = max(0.35, 1.0 - 0.22 * float(len(hold)))
    if gap < -8:
        defense_budget *= 0.72
    elif gap > 8:
        defense_budget *= 1.08

    deny = 0.0
    for uid in action.pool_pick_uids:
        self_value = pool_entry_value_for_player(ms, gs, uid, player)
        opp_scores = []
        for op in opponents:
            opp_base = pool_entry_value_for_player(ms, gs, uid, op)
            opp_pressure = opponent_strategy_pressure(gs, op)
            fam = infer_visible_strategy_family_label(gs, op).strip().lower()
            fam_prof = strategy_family_profile_by_label(fam)
            fam_fit = 0.0
            if isinstance(fam_prof, dict):
                fam_fit = entry_best_strategy_family_score(ms, gs, uid, fam_prof)
            pred = predict_opponent_next_turn(gs, ms, player, op)
            style_mult = style_threat_multiplier(str(pred.get("style", "UNKNOWN")))
            move_mult = 1.12 if str(pred.get("likely_move", "")).strip().lower() == "draw_pool" else 0.96
            opp_scores.append(
                ((opp_base * (0.65 + 0.35 * opp_pressure)) + (0.20 * fam_fit)) * style_mult * move_mult
            )
        opp_best = max(opp_scores) if opp_scores else 0.0
        if opp_best <= self_value + 0.65:
            continue
        deny += (opp_best - self_value)
    deny = deny / max(1, len(action.pool_pick_uids))
    deny *= defense_budget
    if deny > 2.4:
        return 2.4
    if deny < 0.0:
        return 0.0
    return float(deny)


def mark_defense_holds_from_pool_draw(gs: GameState, ms: MatchState, player: PlayerState, drawn_pool_entries: List[int]) -> None:
    if not drawn_pool_entries:
        return
    opponents = [p for p in gs.players if p is not player]
    if not opponents:
        return
    hold = _cleanup_defense_hold(player)
    for uid in drawn_pool_entries:
        self_value = pool_entry_value_for_player(ms, gs, uid, player)
        opp_best = 0.0
        for op in opponents:
            opp_base = pool_entry_value_for_player(ms, gs, uid, op)
            opp_pressure = opponent_strategy_pressure(gs, op)
            fam = infer_visible_strategy_family_label(gs, op).strip().lower()
            fam_prof = strategy_family_profile_by_label(fam)
            fam_fit = 0.0
            if isinstance(fam_prof, dict):
                fam_fit = entry_best_strategy_family_score(ms, gs, uid, fam_prof)
            pred = predict_opponent_next_turn(gs, ms, player, op)
            style_mult = style_threat_multiplier(str(pred.get("style", "UNKNOWN")))
            move_mult = 1.10 if str(pred.get("likely_move", "")).strip().lower() == "draw_pool" else 0.97
            score = ((opp_base * (0.65 + 0.35 * opp_pressure)) + (0.20 * fam_fit)) * style_mult * move_mult
            if score > opp_best:
                opp_best = score
        if opp_best > self_value + 1.15:
            hold[uid] = max(int(hold.get(uid, 0)), 3)
    player.flags["_defense_hold_turns"] = hold


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
    if (
        player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    ):
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


def action_board_positioning_score(gs: GameState, player: PlayerState, action: Action) -> float:
    if action.kind == "draw":
        return 0.0
    score = 0.0
    if action.kind == "play_ocean":
        if not player.board_oceans:
            score += 0.9
        empty_oceans = count_empty_oceans(player)
        if empty_oceans <= 0:
            score += 0.35
        elif empty_oceans >= 2:
            score -= 0.45
        if len(player.board_oceans) >= 5:
            score -= 0.20
        return max(-1.5, min(2.0, score))

    if action.kind != "play_to_ocean" or action.ocean_uid is None:
        return 0.0
    slots = player.ocean_slots[action.ocean_uid]
    occupied_dirs = sum(1 for d in ("up", "down", "left", "right") if len(slots.slot(d)) > 0)
    local_cards = [gs.card_db[uid] for uid in slots.all_cards()]
    local_names = {c.name.strip().lower() for c in local_cards}
    ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()

    score += 0.24 * occupied_dirs
    if occupied_dirs == 0:
        score += 0.30
    if occupied_dirs == 2:
        score += 0.22
    if occupied_dirs >= 3:
        score += 0.85
    if "king salmon" in local_names:
        score += 0.60 if occupied_dirs >= 2 else 0.15
    if "artificial reef" in ocean_name:
        score += 0.35
    if "coral reef" in ocean_name and occupied_dirs >= 2:
        score += 0.18
    return max(-1.5, min(2.5, score))


def action_strategic_trap_risk(gs: GameState, ms: MatchState, player: PlayerState, action: Action) -> float:
    risk = 0.0
    excluded_pool: set[int] = set(action.pool_pick_uids) if action.pool_pick_uids else set()
    pred = aggregate_opponent_predictions(gs, ms, player, excluded_pool_uids=excluded_pool)
    peak_pressure = float(pred.get("peak_pressure", 0.0))
    pool_hot_value = float(pred.get("pool_hot_value", 0.0))
    pool_hot_uid = pred.get("pool_hot_uid")

    if action.kind == "draw":
        # Strategic trap: ignoring a hot pool card can hand tempo to opponents.
        if action.draw_from_pool == 0 and pool_hot_value >= 2.0:
            risk += 0.50 + (0.22 * pool_hot_value)
        if (
            action.draw_from_pool > 0
            and pool_hot_uid is not None
            and action.pool_pick_uids
            and pool_hot_uid not in action.pool_pick_uids
            and pool_hot_value >= 2.4
        ):
            risk += 0.35
    elif action.kind == "play_ocean":
        if player.board_oceans and count_empty_oceans(player) > 1 and peak_pressure >= 1.10:
            risk += 0.55
    elif action.kind == "play_to_ocean" and action.ocean_uid is not None:
        local_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]
        local_names = {c.name.strip().lower() for c in local_cards}
        if "deep sea coral" in local_names:
            # Often a trap: breaking the "only creature on ocean" condition.
            risk += 0.95
        if "mandarin goby" in local_names and any(c.name.strip().lower() == "mantis shrimp" for c in local_cards):
            # Usually not a trap; explicitly reduce risk for this known combo line.
            risk -= 0.30

    if action.kind != "draw":
        play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        card = gs.card_db.get(play_face_uid)
        if card is not None:
            if card.cost >= 3 and len(player.hand) <= 5:
                risk += 0.40
            if action.use_star and not star_has_immediate_value(gs, ms, player, card, played_entry_uid=action.card_uid):
                risk += 0.85

    if peak_pressure >= 1.45 and action.kind == "draw" and action.draw_from_pool == 0:
        risk += 0.20
    if risk < 0.0:
        return 0.0
    if risk > 3.5:
        return 3.5
    return risk


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
    acts = legal_actions(gs, ms, player, include_draw=True)
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
    for p in winners:
        names = [gs.card_db[uid].name for uid in player_board_face_uids(p)]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                k = synergy_key(names[i], names[j])
                synergy_map[k] = float(synergy_map.get(k, 0.0) + 0.05)

    for p in losers:
        names = [gs.card_db[uid].name for uid in player_board_face_uids(p)]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                k = synergy_key(names[i], names[j])
                synergy_map[k] = float(synergy_map.get(k, 0.0) - 0.01)

    # Learn species combinations from winners and dampen loser species mixes.
    for p in winners:
        species = [gs.card_db[uid].species for uid in player_board_face_uids(p)]
        species = [s for s in species if s.strip() and s.strip().lower() != "n/a"]
        for i in range(len(species)):
            for j in range(i + 1, len(species)):
                k = species_synergy_key(species[i], species[j])
                species_map[k] = float(species_map.get(k, 0.0) + 0.04)

    for p in losers:
        species = [gs.card_db[uid].species for uid in player_board_face_uids(p)]
        species = [s for s in species if s.strip() and s.strip().lower() != "n/a"]
        for i in range(len(species)):
            for j in range(i + 1, len(species)):
                k = species_synergy_key(species[i], species[j])
                species_map[k] = float(species_map.get(k, 0.0) - 0.01)

    # Learn same-ocean card combinations more strongly.
    for p in winners:
        for ocean_uid in p.board_oceans:
            local = [gs.card_db[uid].name for uid in p.ocean_slots[ocean_uid].all_cards()]
            for i in range(len(local)):
                for j in range(i + 1, len(local)):
                    k = synergy_key(local[i], local[j])
                    same_ocean_map[k] = float(same_ocean_map.get(k, 0.0) + 0.08)

    for p in losers:
        for ocean_uid in p.board_oceans:
            local = [gs.card_db[uid].name for uid in p.ocean_slots[ocean_uid].all_cards()]
            for i in range(len(local)):
                for j in range(i + 1, len(local)):
                    k = synergy_key(local[i], local[j])
                    same_ocean_map[k] = float(same_ocean_map.get(k, 0.0) - 0.02)

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
    brain["games_played"] = int(brain.get("games_played", 0)) + 1


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

    defense_hold: Dict[int, int] = {}
    if isinstance(player, PlayerState):
        defense_hold = _cleanup_defense_hold(player)
    pool_n = len(ms.pool)

    def pay_score(entry_uid: int) -> float:
        base = min(face_score(f) for f in entry_faces(ms, entry_uid))
        # Keep denial cards briefly, then cash them in when pool-clear pressure is high.
        if entry_uid in defense_hold:
            ttl = int(defense_hold.get(entry_uid, 1))
            if pool_n >= 8:
                base -= 3.0
            else:
                base += 1.1 + (0.25 * min(4, ttl))
        return base

    return sorted(hand_uids, key=pay_score)


def add_to_pool(ms: MatchState, uid: int) -> None:
    ms.pool.append(uid)
    if len(ms.pool) >= 10:
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
    card = gs.card_db[card_uid]
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
    return all(share_stack_key(gs.card_db[uid]) == new_key for uid in slot_cards)


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

    if key == "free_cephalopods":
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

    bottom_non_end_count = min(9, len(non_end))
    top = non_end[:-bottom_non_end_count] if bottom_non_end_count else non_end
    bottom_group = non_end[-bottom_non_end_count:] if bottom_non_end_count else []
    bottom_group.append(end_entry)
    rng.shuffle(bottom_group)

    deck = top + bottom_group
    return deck, end_uid


def draw_from_deck(gs: GameState, ms: MatchState, player: PlayerState, n: int) -> List[int]:
    drew: List[int] = []
    no_predict = bool(player.flags.get("_sim_no_deck_prediction", False))
    while len(drew) < n:
        if not gs.deck:
            break
        if no_predict:
            idx = random.randrange(len(gs.deck))
            uid = gs.deck.pop(idx)
        else:
            uid = gs.deck.pop(0)
        if ms.end_game_uid is not None and uid == ms.end_game_uid:
            trigger_end_game(ms, gs)
            ms.discard_pile.append(uid)
            # Draw replacement card for this same draw count.
            continue
        player.hand.append(uid)
        drew.append(uid)
    return drew


def draw_from_pool(ms: MatchState, player: PlayerState, n: int) -> List[int]:
    drew: List[int] = []
    for _ in range(n):
        if not ms.pool:
            break
        uid = ms.pool.pop()  # top of pool = most recent
        player.hand.append(uid)
        drew.append(uid)
    return drew


def draw_selected_from_pool(ms: MatchState, player: PlayerState, pick_uids: List[int]) -> List[int]:
    if len(set(pick_uids)) != len(pick_uids):
        return []
    if any(uid not in ms.pool for uid in pick_uids):
        return []
    drew: List[int] = []
    for uid in pick_uids:
        ms.pool.remove(uid)
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
    actions: List[Action] = []
    free_only = bool(player.flags.get("_free_action_only", False))
    multi_paid = bool(player.flags.get("multi_play_paid_turn", False))
    multi_baitfish = bool(player.flags.get("free_baitfish_chain", False))
    multi_yellowfin = int(player.flags.get("free_yellowfin_tuna", 0)) > 0
    if free_only or multi_paid or multi_baitfish or multi_yellowfin:
        include_draw = False

    if include_draw:
        if len(gs.deck) >= 2:
            actions.append(Action(kind="draw", draw_from_pool=0))
        if len(gs.deck) >= 1 and len(ms.pool) >= 1:
            actions.append(Action(kind="draw", draw_from_pool=1))
        if len(ms.pool) >= 2:
            actions.append(Action(kind="draw", draw_from_pool=2))

    for entry_uid in list(player.hand):
        faces = entry_faces(ms, entry_uid)
        if len(faces) == 1 and is_ocean(gs.card_db[faces[0]]):
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
            card = gs.card_db[face_uid]
            if is_ocean(card):
                continue
            if multi_baitfish and card.species.strip().lower() != "baitfish":
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

    return actions


def describe_action(gs: GameState, ms: MatchState, action: Action) -> str:
    if action.kind == "draw":
        pick_txt = f" (pool picks: {','.join(str(x) for x in action.pool_pick_uids)})" if action.pool_pick_uids else ""
        if action.draw_from_pool == 0:
            return "draw 2 from deck"
        if action.draw_from_pool == 1:
            return f"draw 1 from pool + 1 from deck{pick_txt}"
        return f"draw 2 from pool{pick_txt}"

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
        "free_crustacean",
        "free_invertebrate",
        "free_coral",
        "multi_play_paid_turn",
        "play_again",
        "go_again",
        "_free_action_only",
    ]:
        if k in player.flags:
            if k == "free_yellowfin_tuna":
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
            pool_cards = draw_selected_from_pool(ms, player, action.pool_pick_uids)
            if len(pool_cards) != pool_take:
                return fail("invalid selected pool cards")
        else:
            pool_cards = draw_from_pool(ms, player, pool_take)
        deck_cards = draw_from_deck(gs, ms, player, deck_take)
        if pool_cards:
            for entry_uid in pool_cards:
                record_revealed_intent_from_entry(ms, gs, player, entry_uid, weight=0.65)
            mark_defense_holds_from_pool_draw(gs, ms, player, pool_cards)
        if verbose:
            parts = []
            if pool_cards:
                parts.append("pool: " + ", ".join(entry_short_label(ms, gs, uid) for uid in pool_cards))
            if deck_cards:
                parts.append("deck: " + ", ".join(entry_short_label(ms, gs, uid) for uid in deck_cards))
            detail = " | ".join(parts) if parts else "no cards drawn"
            print(f"{player.name} draws 2 -> {detail}")
        return True

    if action.card_uid not in player.hand:
        return fail(f"card uid {action.card_uid} not in hand")

    play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    if play_face_uid not in entry_faces(ms, action.card_uid):
        return fail(f"face uid {play_face_uid} is not valid for card entry {action.card_uid}")
    card = gs.card_db[play_face_uid]
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

    payments = payment_picker(gs, ms, player, action.card_uid, play_face_uid, cost_to_pay, require_symbol)
    if payments is None:
        return fail("payment selection failed")

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
                ctx={"played_as": "ocean", "ms": ms, "is_human_turn": is_human_turn},
            )
            sync_reactive_trigger_flags(gs, player)
            resolve_reactive_draw_triggers(gs, player, card, action.kind)
            if verbose:
                drew = len(player.hand) - before_hand
                if drew > 0:
                    print(f"{player.name} draws {drew} from {card.uid}:{card.name} ability.")
        elif action.kind == "play_to_ocean":
            direction = card.direction.strip().lower()
            if direction not in {"up", "down", "left", "right"}:
                return fail(f"invalid direction '{card.direction}' for card {card.uid}:{card.name}")
            player.ocean_slots[action.ocean_uid].slot(direction).append(play_face_uid)
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
                },
            )
            sync_reactive_trigger_flags(gs, player)
            resolve_reactive_draw_triggers(gs, player, card, action.kind)
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
    record_revealed_intent_from_face(player, card, weight=1.0)

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
        feat = {
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
            "deny_bonus": action_deny_bonus(gs, ms, player, action),
            "overbuild_ocean_penalty": 0.0,
            "sim_point_delta": 0.0,
            "board_control": float((32.0 - float(open_slot_count(player))) / 32.0),
            "resource_availability": float(max(0.0, min(1.8, (len(player.hand) + 2.0) / 10.0))),
            "future_combo_potential": 0.15,
            "opponent_threat": float(estimate_opponent_threat_level(gs, player)[0]),
            "scoring_opportunity": 0.25,
            "expected_value": float(0.25 + (0.30 * pick_value)),
            "risk_level": 0.1,
            "long_term_impact": 0.25,
        }
        feat.update(compute_action_meta_metrics(gs, ms, player, action, feat))
        return feat

    play_face_uid = action.face_uid if action.face_uid is not None else action.card_uid
    card = gs.card_db[play_face_uid]
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
        "deny_bonus": 0.0,
        "overbuild_ocean_penalty": 0.0,
    }

    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        slots = player.ocean_slots[action.ocean_uid]
        count = len(slots.all_cards())
        feat["target_occupancy"] = float(count)
        feat["fills_empty_ocean"] = 1.0 if count == 0 else 0.0
    else:
        feat["target_occupancy"] = 0.0
        feat["fills_empty_ocean"] = 0.0

    t = card.text.lower()
    if card.name.lower() == "clownfish" and action.kind == "play_to_ocean" and action.ocean_uid is not None:
        t = f"{t} | {gs.card_db[action.ocean_uid].text.lower()}"
    base_plus = 0
    for chunk in [x.strip() for x in t.split("|")]:
        if not chunk.startswith("+"):
            continue
        if any(w in chunk for w in [" if ", " per ", " or ", " most ", " only ", " at least ", " every "]):
            continue
        m = re.match(r"\+(\d+)\b", chunk)
        if m:
            base_plus += int(m.group(1))

    board = [gs.card_db[uid] for uid in player.board_oceans]
    for slots in player.ocean_slots.values():
        board.extend(gs.card_db[uid] for uid in slots.all_cards())
    ocean_count = len(player.board_oceans)
    other_ocean_counts = [len(p.board_oceans) for p in gs.players if p is not player]

    if "+3 per bird" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "bird")
    if "+1 per crustacean" in t:
        base_plus += sum(1 for c in board if c.species.lower() == "crustacean")
    if "+3 per crustacean" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "crustacean")
    if "+5 per coral" in t:
        base_plus += 5 * sum(1 for c in board if c.species.lower() == "coral")
    if "+3 per coral" in t:
        base_plus += 3 * sum(1 for c in board if c.species.lower() == "coral")
    if "+1 per coral" in t:
        base_plus += sum(1 for c in board if c.species.lower() == "coral")
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
    if "+2 per n/a animal" in t or "+2 per uncharted animal" in t:
        base_plus += 2 * sum(
            1
            for c in board
            if c.species.lower() in {"uncharted", "n/a"} and c.direction.strip().lower() != "n/a"
        )
    if "+3 per n/a animal" in t or "+3 per uncharted animal" in t:
        base_plus += 3 * sum(
            1
            for c in board
            if c.species.lower() in {"uncharted", "n/a"} and c.direction.strip().lower() != "n/a"
        )
    if "+9 per each mahi mahi you control" in t:
        base_plus += 9 * sum(1 for c in board if c.name.lower() == "mahi mahi")

    # Human-like conditional handling: penalize dead plays, reward satisfied conditions.
    same_ocean_cards: List[CardDef] = []
    if action.kind == "play_to_ocean" and action.ocean_uid is not None:
        same_ocean_cards = [gs.card_db[uid] for uid in player.ocean_slots[action.ocean_uid].all_cards()]

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
            base_plus += 5
        else:
            base_plus -= 3

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
        ocean_name = gs.card_db[action.ocean_uid].name.strip().lower()
        current_v = clownfish_ocean_value(ocean_name)
        best_v = current_v
        for candidate_ocean_uid in player.board_oceans:
            if can_attach_to_ocean(gs, player, play_face_uid, candidate_ocean_uid):
                candidate_name = gs.card_db[candidate_ocean_uid].name.strip().lower()
                best_v = max(best_v, clownfish_ocean_value(candidate_name))
        base_plus += 2.0 * current_v
        if best_v > current_v:
            base_plus -= 2.5 * (best_v - current_v)

    if "4 kelp forest" in t or "4 kelp forests" in t:
        kelp_count = sum(1 for c in board if c.name.lower() == "kelp forest")
        if card.name.lower() == "kelp forest":
            kelp_count += 1
        if kelp_count >= 4:
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
    feat.update(compute_action_meta_metrics(gs, ms, player, action, feat))
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
) -> Optional[Action]:
    acts = candidate_actions_for_ai(gs, ms, player)
    acts = filter_overbuild_ocean_actions(gs, ms, player, acts)
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
        strategy_v, novelty_v, branch_v, sig = strategy_signal(
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
        score += 0.22 * deep_rl_predict(
            deep_rl_state,
            deep_rl_feature_vector(
                gs,
                ms,
                player,
                a,
                synergy_map=synergy_map,
                species_map=species_map,
                same_ocean_map=same_ocean_map,
            ),
        )
        score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
        score += action_engine_timing_bonus(gs, ms, player, a)
        score += human_realism_action_adjustment(gs, ms, player, a, feats)
        score += diversity_role_adjustment(gs, player, a, feats, novelty_v, branch_v)
        score += rare_combo_exploration_bonus(strategy_count_map, sig, score)
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
    max_actions: int = 3,
) -> None:
    action_budget = 0
    replay_granted = bool(player.flags.get("play_again", False) or player.flags.get("go_again", False))
    if replay_granted:
        maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
    if player.flags.pop("play_again", False) or player.flags.pop("go_again", False):
        action_budget += 1
    if turn_state.free_followups > 0:
        action_budget += turn_state.free_followups
        player.flags["_free_action_only"] = True
        turn_state.free_followups = 0

    steps = 0
    hard_cap = int(HUMAN_REALISM_CONFIG.get("turn_chain_safety_cap", 500))
    hard_cap = max(max_actions + 10, hard_cap)
    while action_budget > 0 and steps < hard_cap and (
        steps < max_actions
        or player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
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
            deep_rl_state=deep_rl_state,
        )
        if a is None:
            break
        ok = apply_action(gs, ms, player, a, turn_state, choose_payment_ai, verbose=False)
        if not ok:
            break
        steps += 1
        replay_granted = bool(player.flags.get("play_again", False) or player.flags.get("go_again", False))
        if replay_granted:
            maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
        if player.flags.pop("play_again", False) or player.flags.pop("go_again", False):
            action_budget += 1
        if turn_state.free_followups > 0:
            action_budget += turn_state.free_followups
            player.flags["_free_action_only"] = True
            turn_state.free_followups = 0
        if (
            player.flags.get("multi_play_paid_turn", False)
            or player.flags.get("free_baitfish_chain", False)
            or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
        ):
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
    hide_unknown_hand: bool = False,
    max_actions: int = 3,
) -> None:
    turn_state = TurnState()
    if hide_unknown_hand and bool(player.flags.get("_sim_hidden_hand_mode", False)):
        if not bool(player.flags.get("_sim_hidden_hand_initialized", False)):
            player.hand.clear()
            player.flags["_sim_hidden_hand_initialized"] = True
    action_budget = 1
    steps = 0
    hard_cap = int(HUMAN_REALISM_CONFIG.get("turn_chain_safety_cap", 500))
    hard_cap = max(max_actions + 10, hard_cap)
    while action_budget > 0 and steps < hard_cap and (
        steps < max_actions
        or player.flags.get("multi_play_paid_turn", False)
        or player.flags.get("free_baitfish_chain", False)
        or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
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
            deep_rl_state=deep_rl_state,
        )
        if a is None:
            break
        ok = apply_action(gs, ms, player, a, turn_state, choose_payment_ai, verbose=False)
        if not ok:
            break
        steps += 1
        replay_granted = bool(player.flags.get("play_again", False) or player.flags.get("go_again", False))
        if replay_granted:
            maybe_apply_replay_pickup(gs, ms, player, turn_state, is_human_turn=False, verbose=False)
        if player.flags.pop("play_again", False) or player.flags.pop("go_again", False):
            action_budget += 1
        if turn_state.free_followups > 0:
            action_budget += turn_state.free_followups
            player.flags["_free_action_only"] = True
            turn_state.free_followups = 0
        if (
            player.flags.get("multi_play_paid_turn", False)
            or player.flags.get("free_baitfish_chain", False)
            or int(player.flags.get("free_yellowfin_tuna", 0)) > 0
        ):
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
) -> float:
    """Second-pass move check: simulate move, likely opponent response, and our next turn."""
    limited_hidden = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("human_limited_inference", False))

    try:
        player_index = next(i for i, p in enumerate(gs.players) if p is player)
    except StopIteration:
        return -1e9

    gs2 = copy.deepcopy(gs)
    ms2 = copy.deepcopy(ms)
    p2 = gs2.players[player_index]
    predict_draws = bool(HUMAN_REALISM_CONFIG.get("lookahead_predict_draws", False))
    use_hidden_hands = bool(HUMAN_REALISM_CONFIG.get("lookahead_use_opponent_hidden_hands", False))
    if not predict_draws:
        for sim_p in gs2.players:
            sim_p.flags["_sim_no_deck_prediction"] = True
    if limited_hidden and (not use_hidden_hands):
        for i, sim_p in enumerate(gs2.players):
            if i != player_index:
                sim_p.flags["_sim_hidden_hand_mode"] = True
                sim_p.flags["_sim_hidden_hand_initialized"] = False

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
        deep_rl_state=deep_rl_state,
        max_actions=3,
    )

    after_score = final_points(gs2, p2)
    point_gain = float(after_score - before_score)

    # Opponent-aware multi-round lookahead.
    opponent_threat = 0.0
    cumulative_next_gain = 0.0
    n = len(gs2.players)
    lookahead_rounds = int(HUMAN_REALISM_CONFIG.get("lookahead_rounds", 2))
    if lookahead_rounds < 1:
        lookahead_rounds = 1
    if lookahead_rounds > 5:
        lookahead_rounds = 5
    lookahead_actions = int(HUMAN_REALISM_CONFIG.get("lookahead_actions_per_turn", 3))
    if lookahead_actions < 1:
        lookahead_actions = 1
    if lookahead_actions > 4:
        lookahead_actions = 4

    for r in range(lookahead_rounds):
        round_decay = 0.75 ** r
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
                deep_rl_state=deep_rl_state,
                hide_unknown_hand=(limited_hidden and (not use_hidden_hands)),
                max_actions=lookahead_actions,
            )
            op_gain = final_points(gs2, op) - before_op
            my_loss = before_my - final_points(gs2, p2)
            pressure = opponent_strategy_pressure(gs2, op)
            opponent_threat += round_decay * max(0.0, ((0.7 * op_gain) + (0.3 * my_loss)) * pressure)

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
            deep_rl_state=deep_rl_state,
            hide_unknown_hand=False,
            max_actions=lookahead_actions,
        )
        cumulative_next_gain += round_decay * max(0.0, final_points(gs2, p2) - before_next_my)

    next_gain = cumulative_next_gain
    adv_gain = relative_advantage(gs2, player_index) - before_adv

    # Human-like tie-break: if this creates/keeps a healthy board, prefer it over passive lines.
    board_development = 0.0
    if action.kind == "play_ocean":
        board_development += 0.9 if len(player.board_oceans) < 4 else 0.2
    elif action.kind == "play_to_ocean":
        board_development += 0.5

    return (
        point_gain * 2.35
        + next_gain * 0.85
        + adv_gain * 0.65
        + board_development
        + after_score * 0.06
        - opponent_threat * 0.55
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
        "deny_bonus",
        "overbuild_ocean_penalty",
        "board_control",
        "resource_availability",
        "future_combo_potential",
        "opponent_threat",
        "opponent_prediction_pressure",
        "scoring_opportunity",
        "expected_value",
        "risk_level",
        "long_term_impact",
        "board_positioning",
        "combo_setup_value",
        "short_term_sacrifice_value",
        "strategic_trap_risk",
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
    epsilon: float = 0.05,
) -> Optional[Action]:
    acts = candidate_actions_for_ai(gs, ms, player)
    acts = filter_overbuild_ocean_actions(gs, ms, player, acts)
    if not acts:
        return None

    limited_hidden = human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("human_limited_inference", False))
    eps = adaptive_exploration_rate(gs, ms, player, epsilon)
    # Expert bias: keep some exploration, but tighten it so strong lines win more often.
    eps = max(0.006, eps * 0.72)
    include_sim_delta = not limited_hidden

    if random.random() < eps:
        # Explore under-tried lines, but keep an EV floor to avoid "stupid" tests.
        explore_rows: List[Tuple[Action, float, float]] = []
        best_base = float("-inf")
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
            base = weighted_score(feats, weights)
            strategy_v, novelty_v, branch_v, sig = strategy_signal(
                gs,
                ms,
                player,
                a,
                strategy_value_map=strategy_value_map,
                strategy_count_map=strategy_count_map,
                strategy_transition_map=strategy_transition_map,
                strategy_transition_count_map=strategy_transition_count_map,
            )
            explore = (
                (weights.get("novelty_bonus", 0.0) * novelty_v)
                + (weights.get("branch_bonus", 0.0) * branch_v)
                + 0.22
                * deep_rl_predict(
                    deep_rl_state,
                    deep_rl_feature_vector(
                        gs,
                        ms,
                        player,
                        a,
                        synergy_map=synergy_map,
                        species_map=species_map,
                        same_ocean_map=same_ocean_map,
                    ),
                )
                + action_archetype_bonus(gs, ms, player, a, archetype_profile) * 0.35
                + action_engine_timing_bonus(gs, ms, player, a)
                + human_realism_action_adjustment(gs, ms, player, a, feats) * 0.45
                + diversity_role_adjustment(gs, player, a, feats, novelty_v, branch_v)
                + rare_combo_exploration_bonus(strategy_count_map, sig, base)
            )
            if base < -0.35:
                explore -= 1.1
            explore_rows.append((a, explore, base))
            if base > best_base:
                best_base = base
        if not explore_rows:
            return None
        explore_rows.sort(key=lambda x: x[1], reverse=True)
        viable = [row for row in explore_rows if row[2] >= best_base - 1.1]
        return viable[0][0] if viable else explore_rows[0][0]

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
                strategy_v, novelty_v, branch_v, sig = strategy_signal(
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
                score += 0.30 * deep_rl_predict(
                    deep_rl_state,
                    deep_rl_feature_vector(
                        gs,
                        ms,
                        player,
                        a,
                        synergy_map=synergy_map,
                        species_map=species_map,
                        same_ocean_map=same_ocean_map,
                    ),
                )
                score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
                score += action_engine_timing_bonus(gs, ms, player, a)
                score += human_realism_action_adjustment(gs, ms, player, a, feats)
                score += diversity_role_adjustment(gs, player, a, feats, novelty_v, branch_v)
                score += rare_combo_exploration_bonus(strategy_count_map, sig, score)
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
        strategy_v, novelty_v, branch_v, sig = strategy_signal(
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
        score += 0.30 * deep_rl_predict(
            deep_rl_state,
            deep_rl_feature_vector(
                gs,
                ms,
                player,
                a,
                synergy_map=synergy_map,
                species_map=species_map,
                same_ocean_map=same_ocean_map,
            ),
        )
        score += 1.1 * action_archetype_bonus(gs, ms, player, a, archetype_profile)
        score += action_engine_timing_bonus(gs, ms, player, a)
        score += human_realism_action_adjustment(gs, ms, player, a, feats)
        score += diversity_role_adjustment(gs, player, a, feats, novelty_v, branch_v)
        score += rare_combo_exploration_bonus(strategy_count_map, sig, score)
        scored.append((a, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    if human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("accuracy_over_speed", False)):
        shortlist = scored
    else:
        shortlist = scored[: min(8, len(scored))]

    best: Optional[Action] = None
    best_score = float("-inf")
    for a, base_score in shortlist:
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
            deep_rl_state=deep_rl_state,
        )
        # In expert mode, trust confirmation rollouts more than raw heuristic.
        total = base_score * 0.45 + confirm * 0.55
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
            progress_state = game_progress_state(gs, ms)
            phase = str(progress_state.get("phase", "mid_game"))
            threshold = 0.9 if count_empty_oceans(player) > 0 else 0.3
            if phase == "early_game":
                threshold = 0.45 if count_empty_oceans(player) > 0 else 0.14
            elif phase == "mid_game":
                threshold = 0.78 if count_empty_oceans(player) > 0 else 0.26
            elif phase == "late_game":
                threshold = 1.15 if count_empty_oceans(player) > 0 else 0.44
            elif phase == "nearing_game_end":
                threshold = 1.45 if count_empty_oceans(player) > 0 else 0.62
            elif phase == "limited_turns_remaining":
                threshold = 1.85 if count_empty_oceans(player) > 0 else 0.85
            if human_realism_enabled():
                threshold += 0.20 * float(progress_state.get("pressure", endgame_pressure(len(gs.deck))))
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
        for uid in ms.pool:
            detail = entry_label(ms, gs, uid)
            detail = "\n    ".join(detail.splitlines())
            print("  " + detail)
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
    # >= so tied counts still grant "most oceans" bonus (ties count)
    has_most_oceans = ocean_count >= max(other_ocean_counts) if other_ocean_counts else True

    def has_most_piers() -> bool:
        my = sum(1 for uid in player.board_oceans if ((gs.card_db.get(uid).name.lower() == "pier") if gs.card_db.get(uid) else False))
        others = []
        for p in gs.players:
            if p is player:
                continue
            n = sum(1 for uid in p.board_oceans if ((gs.card_db.get(uid).name.lower() == "pier") if gs.card_db.get(uid) else False))
            others.append(n)
        # >= so ties count
        return my >= max(others) if others else True

    def count_animals(owner: PlayerState) -> int:
        total_animals = 0
        for o_uid in owner.board_oceans:
            sl = owner.ocean_slots.get(o_uid)
            if not sl:
                continue
            for uid in sl.all_cards():
                c = gs.card_db.get(uid)
                if c is None:
                    continue
                if c.direction.strip().lower() != "n/a":
                    total_animals += 1
        return total_animals

    def has_most_animals() -> bool:
        my = count_animals(player)
        others = [count_animals(p) for p in gs.players if p is not player]
        return my >= max(others) if others else True

    def fully_occupied_ocean_count() -> int:
        full = 0
        for o_uid in player.board_oceans:
            sl = player.ocean_slots.get(o_uid)
            if not sl:
                continue
            if sl.up and sl.down and sl.left and sl.right:
                full += 1
        return full

    def coral_attached_to_coral_reef_count() -> int:
        total_coral = 0
        for o_uid in player.board_oceans:
            ocean = gs.card_db.get(o_uid)
            if ocean is None or ocean.name.lower() != "coral reef":
                continue
            sl = player.ocean_slots.get(o_uid)
            if not sl:
                continue
            for uid in sl.all_cards():
                c = gs.card_db.get(uid)
                if c is not None and c.species.lower() == "coral":
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
        pairs = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*=\s*(\d+)", text)]
        if not pairs:
            return 0
        best = 0
        for need, pts in pairs:
            if base_value >= need:
                best = max(best, pts)
        return best

    coral_reef_count_total = name_count("coral reef")
    coral_reef_table_applied = False

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
            if any(c.species.lower() == "baitfish" for c in same):
                pts += 5
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
                pts += 4
        if "if you have at least four cephalopods" in t:
            if species_count("cephalopod") >= 4:
                pts += 6
        if "if you have the most oceans" in t:
            if has_most_oceans:
                pts += 8
        if "if you have the most animals" in t:
            if has_most_animals():
                pts += 4
        if "if you have all 8 oceans" in t:
            if ocean_count >= 8:
                m = re.search(r"\+(\d+)\s*if you have all 8 oceans", t)
                pts += int(m.group(1)) if m else 8
        if "+2 or +3 if you have the most piers" in t:
            pts += 3 if has_most_piers() else 2
        if "+1 per every two oceans you control" in t:
            pts += ocean_count // 2
        if "+5 per fully occupied ocean" in t:
            pts += 5 * fully_occupied_ocean_count()
        if "+2 per card attached" in t:
            attached = len(cards_on_same_ocean(ocean_uid))
            pts += 2 * attached
        if "kelp forest" in t and ("at least 4" in t or "4 or more" in t):
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
        if "+2 per coral" in t and "attached to a coral reef" not in t:
            pts += 2 * species_count("coral")
        if "+5 per coral" in t:
            pts += 5 * species_count("coral")
        if "+1 per coral" in t:
            pts += species_count("coral")
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
        if "+3 per invertebrate" in t:
            pts += 3 * species_count("invertebrate")
        if "+1 per uncharted animal" in t:
            pts += species_count("n/a") + species_count("uncharted")
        if "+2 per n/a animal" in t or "+2 per uncharted animal" in t:
            pts += 2 * (species_count("n/a") + species_count("uncharted"))
        if "+3 per n/a animal" in t or "+3 per uncharted animal" in t:
            pts += 3 * (species_count("n/a") + species_count("uncharted"))
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
            kinds = {c.name.lower() for c in non_ocean_cards if c.species.lower() == "baitfish"}
            pts += value_from_threshold_table(t, len(kinds))
        elif card.name.lower() == "coral reef":
            # Coral Reef table is a global count score, only apply once, not once per card.
            if not coral_reef_table_applied:
                pts += value_from_threshold_table(t, coral_reef_count_total)
                coral_reef_table_applied = True
        elif re.search(r"\d+\s*=\s*\d+", t):
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


def board_cards_label(gs: GameState, player: PlayerState) -> str:
    cards: List[str] = []
    for uid in player.board_oceans:
        c = gs.card_db[uid]
        cards.append(f"{uid}:{c.name}(Ocean)")
    for slots in player.ocean_slots.values():
        for uid in slots.all_cards():
            c = gs.card_db[uid]
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
    deep_rl_state: Optional[Dict[str, Any]] = None,
    live_recorder: Optional[LiveRecorder] = None,
    player_archetype_profiles: Optional[List[Dict[str, Any]]] = None,
    player_diversity_roles: Optional[List[str]] = None,
    hand_based_archetypes: bool = False,
    human_learning_boost: float = 1.0,
    strict_balance: bool = False,
) -> Tuple[GameState, MatchState]:
    rng = random.Random(seed)
    pair_primary_to_faces, face_to_primary = build_non_ocean_pair_maps(card_db)
    deck, end_uid = build_deck_with_late_end_game(card_db, pair_primary_to_faces, face_to_primary, rng)

    players = [PlayerState(name) for name in player_names]
    gs = GameState(card_db=card_db, players=players, deck=deck)
    ms = MatchState(
        end_game_uid=end_uid,
        pair_primary_to_faces=pair_primary_to_faces,
        face_to_primary=face_to_primary,
    )

    def mark_guard(msg: str) -> None:
        ms.guard_events.append(msg)
        if strict_balance or verbose:
            print(f"[GUARD] {msg}")
        if live_recorder is not None:
            live_recorder.event(f"[GUARD] {msg}")
        low = msg.lower()
        if "turn-chain" in low or "loop" in low:
            record_error_event(online_state, "infinite_loop", msg)
        elif "rule" in low:
            record_error_event(online_state, "rule_violation", msg)
        elif "invalid" in low:
            record_error_event(online_state, "invalid_game_state", msg)

    expected_entries: set[int] = set(deck)
    if len(expected_entries) != len(deck):
        mark_guard("duplicate entries detected in starting deck")
        record_error_event(online_state, "duplicate_cards", "starting deck contains duplicates")

    def validate_or_raise(where: str) -> None:
        problems = validate_match_state(gs, ms, expected_entries=expected_entries)
        if not problems:
            return
        preview = problems[:20]
        for code, detail in preview:
            mark_guard(f"{where} | {detail}")
            record_error_event(online_state, code, f"{where} | {detail}")
        if len(problems) > len(preview):
            extra = len(problems) - len(preview)
            mark_guard(f"{where} | ... plus {extra} additional state errors")
        raise RuntimeError(f"invalid game state at {where}: {problems[0][1]}")

    human_idx_set: set[int] = set()
    if human_indices:
        for i in human_indices:
            if 0 <= i < len(players):
                human_idx_set.add(i)
    if human_index is not None and 0 <= human_index < len(players):
        human_idx_set.add(human_index)

    for i, p in enumerate(players):
        assign_runtime_playstyle(p, rng, is_human=(i in human_idx_set))
        if player_diversity_roles and i < len(player_diversity_roles):
            p.flags["_diversity_role"] = str(player_diversity_roles[i]).strip().lower()

    start_game(gs, starting_hand=8, shuffle=False)
    perform_mulligans(gs, ms)
    validate_or_raise("after_opening_setup")

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
    move_histories: Dict[int, List[Dict[str, float]]] = {i: [] for i in range(len(gs.players))}
    move_signatures: Dict[int, List[str]] = {i: [] for i in range(len(gs.players))}
    # Tracks non-human actions between human turns so the human gets a clear recap.
    non_human_actions_since_human: Dict[int, List[str]] = {
        i: [] for i in range(len(gs.players)) if i not in human_idx_set
    }
    while True:
        if max_turns > 0 and turns >= max_turns:
            mark_guard(f"max-turn cap reached ({max_turns}) before natural game end")
            break
        p = gs.current_player()
        age_defense_hold_for_turn_start(p)
        turn_state = TurnState()
        made_action_this_turn = False
        if live_recorder is not None:
            live_recorder.event(f"=== Turn {turns + 1}: {p.name} ===")
            live_recorder.event(f"Pool: {short_entry_list(ms, gs, ms.pool)}")
            live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"turn_start:{p.name}")
        if verbose:
            print(f"\n=== Turn {turns + 1}: {p.name} ===")
        if verbose and gs.turn_index in human_idx_set and non_human_actions_since_human:
            print("\nSince your last turn, AI actions were:")
            any_actions = False
            for i, opp in enumerate(gs.players):
                if i in human_idx_set:
                    continue
                acts = non_human_actions_since_human.get(i, [])
                if acts:
                    any_actions = True
                    print(f"  {opp.name}:")
                    for step, text in enumerate(acts, start=1):
                        print(f"    {step}. {text}")
                else:
                    print(f"  {opp.name}: (no actions)")
            if not any_actions:
                print("  (no AI actions yet)")
            for k in list(non_human_actions_since_human.keys()):
                non_human_actions_since_human[k] = []
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

        # One action per turn by default; abilities may grant extra actions.
        action_budget = 1
        turn_chain_cap = int(HUMAN_REALISM_CONFIG.get("turn_chain_safety_cap", 500))
        if turn_chain_cap < 10:
            turn_chain_cap = 10
        actions_taken_this_turn = 0
        turn_face_play_counts: Dict[int, int] = {}
        while action_budget > 0:
            if actions_taken_this_turn >= turn_chain_cap:
                mark_guard(f"{p.name} hit turn-chain safety cap ({turn_chain_cap})")
                break
            actions_taken_this_turn += 1
            was_free_only = bool(p.flags.get("_free_action_only", False))
            policy = action_policies[gs.turn_index]
            chosen = policy(gs, ms, p)
            is_human_turn = gs.turn_index in human_idx_set
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

            picker = choose_payment_human if is_human_turn else choose_payment_ai
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
            had_play_option = any(a.kind != "draw" for a in legal_actions(gs, ms, p, include_draw=True))
            before_score = final_points(gs, p) if do_online else 0
            chosen_feats = (
                action_features(
                    gs,
                    ms,
                    p,
                    chosen,
                    synergy_map=online_synergy_map,
                    species_map=online_species_map,
                    same_ocean_map=online_same_ocean_map,
                )
                if do_online
                else None
            )
            chosen_deep_vec = (
                deep_rl_feature_vector(
                    gs,
                    ms,
                    p,
                    chosen,
                    synergy_map=online_synergy_map,
                    species_map=online_species_map,
                    same_ocean_map=online_same_ocean_map,
                )
                if (do_online and isinstance(deep_rl_state, dict) and bool(deep_rl_state.get("enabled", False)))
                else None
            )
            executed_action = chosen
            executed_feats = chosen_feats
            fail_messages: List[str] = []
            ok = apply_action(gs, ms, p, chosen, turn_state, picker, verbose=verbose, fail_reason=fail_messages)
            if not ok:
                reason = fail_messages[-1] if fail_messages else "unknown reason"
                msg = f"{p.name} action failed: {reason}"
                if verbose:
                    print(msg)
                if live_recorder is not None:
                    live_recorder.event(msg)
                record_error_event(online_state, "invalid_action", msg)
                raise RuntimeError(msg)

            if do_online and executed_feats is not None:
                after_score = final_points(gs, p)
                reward = float(after_score - before_score)
                if executed_action.kind == "draw":
                    reward -= 0.1
                    if had_play_option:
                        reward -= 0.6
                online_update_weights(online_weights, executed_feats, reward, lr=effective_online_lr)
                move_histories[gs.turn_index].append(dict(executed_feats))
                if online_state is not None:
                    online_state["move_updates"] = int(online_state.get("move_updates", 0)) + 1
                if chosen_deep_vec is not None:
                    gamma = float(deep_rl_state.get("gamma", 0.93))
                    next_actions = candidate_actions_for_ai(gs, ms, p)
                    if next_actions:
                        if human_realism_enabled() and bool(HUMAN_REALISM_CONFIG.get("accuracy_over_speed", False)):
                            top_next = list(next_actions)
                        else:
                            # Speed path for large batches.
                            prelim: List[Tuple[Action, float]] = []
                            for na in next_actions:
                                nf = action_features(
                                    gs,
                                    ms,
                                    p,
                                    na,
                                    synergy_map=online_synergy_map,
                                    species_map=online_species_map,
                                    same_ocean_map=online_same_ocean_map,
                                    include_sim_delta=False,
                                )
                                prelim.append((na, weighted_score(nf, online_weights or default_weights())))
                            prelim.sort(key=lambda x: x[1], reverse=True)
                            top_next = [a for a, _ in prelim[: min(6, len(prelim))]]
                        next_q = max(
                            (
                                deep_rl_predict(
                                    deep_rl_state,
                                    deep_rl_feature_vector(
                                        gs,
                                        ms,
                                        p,
                                        na,
                                        synergy_map=online_synergy_map,
                                        species_map=online_species_map,
                                        same_ocean_map=online_same_ocean_map,
                                    ),
                                )
                                for na in top_next
                            ),
                            default=0.0,
                        )
                    else:
                        next_q = 0.0
                    td_target = reward + gamma * next_q
                    deep_rl_train_step(deep_rl_state, chosen_deep_vec, td_target)
                    deep_rl_add_replay(deep_rl_state, chosen_deep_vec, td_target)
                    deep_rl_replay_train(deep_rl_state)

            executed_sig = action_signature(gs, ms, p, executed_action)
            p.flags["_last_sig"] = executed_sig
            if do_online:
                move_signatures[gs.turn_index].append(executed_sig)

            if executed_action.kind in {"play_ocean", "play_to_ocean"}:
                played_face = executed_action.face_uid if executed_action.face_uid is not None else executed_action.card_uid
                turn_face_play_counts[played_face] = int(turn_face_play_counts.get(played_face, 0)) + 1
                if turn_face_play_counts[played_face] == 8:
                    cd = gs.card_db.get(played_face)
                    card_name = cd.name if cd is not None else str(played_face)
                    mark_guard(f"broken-combo suspect: {p.name} repeated {card_name} x8 in one turn")

            made_action_this_turn = True
            record_observed_playstyle_from_action(gs, ms, p, executed_action)
            update_tempo_after_action(p, executed_action, turn_state)
            if (not is_human_turn) and (gs.turn_index in non_human_actions_since_human):
                non_human_actions_since_human[gs.turn_index].append(describe_action(gs, ms, executed_action))
            if live_recorder is not None:
                live_recorder.event(f"{p.name} executed: {describe_action(gs, ms, executed_action)}")
                live_recorder.event(f"Pool now: {short_entry_list(ms, gs, ms.pool)}")
                live_recorder.event(f"Deck remaining: {len(gs.deck)}")
                live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"post_action:{p.name}")
            if was_free_only:
                p.flags["_free_action_only"] = False
            replay_granted = bool(p.flags.get("play_again", False) or p.flags.get("go_again", False))
            if replay_granted:
                picked = maybe_apply_replay_pickup(
                    gs,
                    ms,
                    p,
                    turn_state,
                    is_human_turn=is_human_turn,
                    verbose=verbose,
                )
                if picked and live_recorder is not None:
                    live_recorder.event(f"{p.name} uses replay pickup (1 card).")
            if p.flags.pop("play_again", False) or p.flags.pop("go_again", False):
                action_budget += 1
                if verbose:
                    print(f"{p.name} gets an extra action.")
            if turn_state.free_followups > 0:
                action_budget += turn_state.free_followups
                p.flags["_free_action_only"] = True
                if verbose:
                    print(f"{p.name} gets {turn_state.free_followups} restricted follow-up action(s) for free-play ability.")
                turn_state.free_followups = 0
            if (
                p.flags.get("multi_play_paid_turn", False)
                or p.flags.get("free_baitfish_chain", False)
                or int(p.flags.get("free_yellowfin_tuna", 0)) > 0
            ):
                action_budget += 1
            action_budget -= 1
            validate_or_raise(f"after_action:{p.name}")

        if actions_taken_this_turn >= 120:
            mark_guard(f"loop-risk: {p.name} took {actions_taken_this_turn} actions in a single turn")

        # End-turn hand limit.
        if gs.turn_index in human_idx_set:
            discard_down_to_ten_human(gs, ms, p)
        else:
            discard_down_to_ten_ai(gs, ms, p)

        clear_turn_only_flags(p)
        validate_or_raise(f"after_turn:{p.name}")
        if live_recorder is not None:
            live_recorder.event(
                "Scores -> " + " | ".join(f"{pl.name}: {final_points(gs, pl)}" for pl in gs.players)
            )
            live_recorder.snapshot(gs, ms, turn_number=turns + 1, note=f"turn_end:{p.name}")
        if verbose:
            scores = " | ".join(f"{pl.name}: {final_points(gs, pl)}" for pl in gs.players)
            print(f"Scores -> {scores}")
            print("Score audit:")
            for pl in gs.players:
                print(f"  {pl.name}: {final_points(gs, pl)} | Cards: {board_cards_label(gs, pl)}")
            if verbose_state and ms.pool:
                print("Pool now: " + ", ".join(entry_short_label(ms, gs, uid) for uid in ms.pool))
            elif verbose_state:
                print("Pool now: (empty)")
            print(f"Pool size: {len(ms.pool)} | Deck: {len(gs.deck)}")

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
                mark_guard("stalled-turn safety guard triggered (no legal progress)")
                break

        gs.turn_index = (gs.turn_index + 1) % len(gs.players)
        if gs.turn_index == 0:
            gs.round_count += 1

        turns += 1

    if live_recorder is not None:
        live_recorder.event("=== Final ===")
        for pl in gs.players:
            live_recorder.event(f"{pl.name}: {final_points(gs, pl)}")
        live_recorder.snapshot(gs, ms, turn_number=turns, note="game_end")

    # End-of-game self-play learning: reinforce moves that led to higher final outcomes.
    if online_weights is not None and any(move_histories.values()):
        finals = [final_points(gs, p) for p in gs.players]
        for i, feats_list in move_histories.items():
            if not feats_list:
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
                discount = 0.995 ** (n - step - 1)
                lr_scale = human_learning_boost if i in human_idx_set else 1.0
                online_update_weights(online_weights, feats, target * discount, lr=online_lr * 0.25 * lr_scale)
                if online_state is not None:
                    online_state["move_updates"] = int(online_state.get("move_updates", 0)) + 1

    if online_state is not None:
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

        finals = [float(final_points(gs, p)) for p in gs.players]
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
        update_balance_stats_from_match(gs, ms, online_state)

    if strict_balance:
        if ms.guard_events:
            print(f"\nStrict balance warnings ({len(ms.guard_events)}):")
            for i, evt in enumerate(ms.guard_events, start=1):
                print(f"  {i}. {evt}")
        else:
            print("\nStrict balance check: no safety guards triggered.")

    return gs, ms


def run_game(
    card_db: Dict[int, CardDef],
    policy_a: Callable[[GameState, MatchState, PlayerState], Optional[Action]],
    policy_b: Callable[[GameState, MatchState, PlayerState], Optional[Action]],
    seed: int,
    max_turns: int = 0,
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


def evaluate_weights(card_db: Dict[int, CardDef], w: Dict[str, float], games: int, seed: int) -> Tuple[float, float]:
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
    if len(mem) > 400:
        del mem[:-400]


def learn_strategy(card_db: Dict[int, CardDef], generations: int, games_per_eval: int, seed: int) -> Dict[str, float]:
    random.seed(seed)

    best = default_weights()

    best_wr, best_margin = evaluate_weights(card_db, best, games_per_eval, seed)

    for g in range(1, generations + 1):
        candidates = [best] + [mutate_weights(best, scale=max(0.1, 0.6 * (0.97 ** g))) for _ in range(7)]

        round_best = best
        round_wr = best_wr
        round_margin = best_margin

        for idx, c in enumerate(candidates):
            wr, margin = evaluate_weights(card_db, c, games_per_eval, seed + (g * 1000) + idx * 100)
            if (wr, margin) > (round_wr, round_margin):
                round_best, round_wr, round_margin = c, wr, margin

        best, best_wr, best_margin = round_best, round_wr, round_margin
        print(f"gen {g:02d}: win_rate_vs_random={best_wr:.3f}, avg_margin={best_margin:.2f}")

    return best


def run_human_vs_ai(
    card_db: Dict[int, CardDef],
    seed: int,
    max_turns: int,
    num_players: int = 2,
    export_final_board: Optional[str] = None,
    strict_balance: bool = False,
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
    deep_rl_state = ensure_deep_rl_state(brain.get("deep_rl"))
    brain["deep_rl"] = deep_rl_state
    diversity_roles = diversity_roles_for_game(num_players, seed)
    if diversity_roles:
        diversity_roles[0] = "human"

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = [choose_action_human]
    for _ in range(1, num_players):
        policies.append(
            lambda gs, ms, p, _w=ai_weights, _s=synergy_map, _sp=species_map, _so=same_ocean_map, _sv=strategy_value_map, _sc=strategy_count_map, _st=strategy_transition_map, _stc=strategy_transition_count_map, _dr=deep_rl_state: choose_action_weighted(
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
                deep_rl_state=_dr,
                epsilon=0.0,
            )
        )

    live_recorder = LiveRecorder(log_path=live_log_path, state_path=live_state_path, seed=seed)

    try:
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
            deep_rl_state=deep_rl_state,
            live_recorder=live_recorder,
            player_diversity_roles=diversity_roles,
            strict_balance=strict_balance,
        )
    except Exception as exc:
        print(f"\nSimulation stopped due to error: {type(exc).__name__}: {exc}")
        print("Brain file was not saved for this failed game.")
        return

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
        f"| strategy_families={len(brain.get('strategy_family_stats', {}))} "
        f"| deep_rl_steps={brain.get('deep_rl', {}).get('train_steps', 0)}"
    )
    insights = discovered_strategy_insights(brain, top_n=10)
    if insights:
        print("\n=== Learned Strategy Insights ===")
        for i, line in enumerate(insights, start=1):
            print(f"{i}. {line}")
    bal_lines = balance_report_lines(brain, top_n=6)
    if bal_lines:
        print("\n=== Balance Report ===")
        for line in bal_lines:
            print(line)
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
    strict_balance: bool = False,
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
    deep_rl_state = ensure_deep_rl_state(brain.get("deep_rl"))
    brain["deep_rl"] = deep_rl_state

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = [
        choose_action_human for _ in range(num_players)
    ]
    human_set = set(range(num_players))
    names = [f"P{i+1}" for i in range(num_players)]
    live_recorder = LiveRecorder(log_path=live_log_path, state_path=live_state_path, seed=seed)
    diversity_roles = diversity_roles_for_game(num_players, seed)

    try:
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
            deep_rl_state=deep_rl_state,
            live_recorder=live_recorder,
            player_diversity_roles=diversity_roles,
            hand_based_archetypes=False,
            human_learning_boost=max(1.0, human_teach_boost),
            strict_balance=strict_balance,
        )
    except Exception as exc:
        print(f"\nSimulation stopped due to error: {type(exc).__name__}: {exc}")
        print("Brain file was not saved for this failed game.")
        return

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
        f"| strategy_branches={len(brain.get('strategy_transition', {}))} "
        f"| deep_rl_steps={brain.get('deep_rl', {}).get('train_steps', 0)}"
    )
    bal_lines = balance_report_lines(brain, top_n=6)
    if bal_lines:
        print("\n=== Balance Report ===")
        for line in bal_lines:
            print(line)
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
    strict_balance: bool = False,
    quiet_ai_only: bool = False,
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
    deep_rl_state = ensure_deep_rl_state(brain.get("deep_rl"))
    brain["deep_rl"] = deep_rl_state
    if no_archetype_bias:
        hand_based_archetypes = False
    archetype_profiles: Optional[List[Dict[str, Any]]] = (
        None if (hand_based_archetypes or no_archetype_bias) else select_archetype_profiles(num_players, seed)
    )
    games_played = int(brain.get("games_played", 0))
    evo_rng = random.Random(seed ^ (games_played << 1))
    # Keep mutation/exploration moderate so AI stays competitive while still adapting.
    evo_scale = max(0.04, 0.18 * (0.994 ** max(0, games_played)))
    epsilon_base = 0.01 + min(0.03, (6.0 / (6.0 + max(0, games_played))) * 0.03)

    policy_weights: List[Dict[str, float]] = []
    for i in range(num_players):
        if i == 0:
            policy_weights.append(dict(ai_weights))
        else:
            policy_weights.append(mutate_weights_rng(ai_weights, evo_rng, scale=evo_scale))

    diversity_roles = diversity_roles_for_game(num_players, seed ^ (games_played + 17))

    policies: List[Callable[[GameState, MatchState, PlayerState], Optional[Action]]] = []
    for i in range(num_players):
        prof = archetype_profiles[i] if archetype_profiles and i < len(archetype_profiles) else None
        p_w = policy_weights[i] if i < len(policy_weights) else ai_weights
        p_eps = max(0.01, min(0.14, epsilon_base + evo_rng.uniform(-0.01, 0.02)))
        policies.append(
            lambda gs, ms, p, _w=p_w, _eps=p_eps, _s=synergy_map, _sp=species_map, _so=same_ocean_map, _sv=strategy_value_map, _sc=strategy_count_map, _st=strategy_transition_map, _stc=strategy_transition_count_map, _prof=prof, _dr=deep_rl_state: choose_action_weighted(
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
                deep_rl_state=_dr,
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
    if diversity_roles:
        print("AI diversity roles:")
        for i, role in enumerate(diversity_roles):
            print(f"  AI_{i+1}: {role}")

    try:
        gs, ms = run_match(
            card_db=card_db,
            player_names=[f"AI_{i+1}" for i in range(num_players)],
            action_policies=policies,
            seed=seed,
            max_turns=max_turns,
            human_index=None,
            verbose=not quiet_ai_only,
            verbose_state=not quiet_ai_only,
            online_weights=ai_weights,
            online_learning_indices=set(range(num_players)),
            online_lr=0.04,
            online_state=brain,
            online_state_path=BRAIN_PATH,
            deep_rl_state=deep_rl_state,
            player_archetype_profiles=archetype_profiles,
            player_diversity_roles=diversity_roles,
            hand_based_archetypes=hand_based_archetypes,
            strict_balance=strict_balance,
        )
    except Exception as exc:
        print(f"\nSimulation stopped due to error: {type(exc).__name__}: {exc}")
        print("Brain file was not saved for this failed game.")
        return

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
        f"| deep_rl_steps={brain.get('deep_rl', {}).get('train_steps', 0)} "
        f"| game_memory={len(brain.get('game_memory', []))} "
        f"| evo_runs={brain.get('evolution', {}).get('runs', 0)}"
    )
    insights = discovered_strategy_insights(brain, top_n=12)
    if insights:
        print("\n=== Learned Strategy Insights ===")
        for i, line in enumerate(insights, start=1):
            print(f"{i}. {line}")
    if export_final_board:
        render_final_board_html(gs, ms, export_final_board, title="Fish Game Final Board (AI Only)")
        print(f"Final board visual saved to: {export_final_board}")

    bal_lines = balance_report_lines(brain, top_n=10)
    if bal_lines:
        print("\n=== Balance Report ===")
        for line in bal_lines:
            print(line)

    err_stats = brain.get("error_stats", {}) if isinstance(brain, dict) else {}
    if isinstance(err_stats, dict):
        print(
            "\n=== Error Report ===\n"
            f"illegal_placements={int(err_stats.get('illegal_card_placements', 0))} | "
            f"duplicates={int(err_stats.get('duplicate_cards', 0))} | "
            f"missing_cards={int(err_stats.get('missing_cards', 0))} | "
            f"negative_scores={int(err_stats.get('negative_score_errors', 0))} | "
            f"invalid_actions={int(err_stats.get('invalid_actions', 0))} | "
            f"infinite_loop_guards={int(err_stats.get('infinite_loop_guards', 0))} | "
            f"rule_violations={int(err_stats.get('rule_violations', 0))} | "
            f"invalid_states={int(err_stats.get('invalid_game_states', 0))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fish Game simulation + strategy learning")
    parser.add_argument("--generations", type=int, default=10)
    parser.add_argument("--games-per-eval", type=int, default=40)
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default: random each run)")
    parser.add_argument("--benchmark-games", type=int, default=200)
    parser.add_argument("--human-vs-ai", action="store_true")
    parser.add_argument("--all-human", action="store_true", help="Control all players manually (teaching mode).")
    parser.add_argument("--ai-only", action="store_true")
    parser.add_argument(
        "--quiet-ai-only",
        action="store_true",
        help="For --ai-only runs, suppress turn-by-turn logs while keeping final results.",
    )
    parser.add_argument("--max-turns", type=int, default=0, help="0 means unlimited turns (until END GAME final round ends)")
    parser.add_argument("--num-players", type=int, default=2)
    parser.add_argument(
        "--strict-balance",
        action="store_true",
        help="Report any safety guard trigger (max-turn cap, chain cap, stall cap) during the game.",
    )
    parser.add_argument("--export-final-board", type=str, default="", help="Write end-game visual board HTML to this path")
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

    try:
        if args.human_vs_ai:
            run_human_vs_ai(
                card_db=card_db,
                seed=seed,
                max_turns=args.max_turns,
                num_players=args.num_players,
                export_final_board=args.export_final_board or None,
                strict_balance=args.strict_balance,
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
                strict_balance=args.strict_balance,
            )
            return

        if args.ai_only:
            run_ai_only_game(
                card_db=card_db,
                seed=seed,
                max_turns=args.max_turns,
                num_players=args.num_players,
                export_final_board=args.export_final_board or None,
                strict_balance=args.strict_balance,
                quiet_ai_only=args.quiet_ai_only,
            )
            return

        best = learn_strategy(
            card_db=card_db,
            generations=args.generations,
            games_per_eval=args.games_per_eval,
            seed=seed,
        )

        wr, margin = evaluate_weights(card_db, best, args.benchmark_games, seed + 90000)
        print("\nBest learned strategy weights:")
        for k in sorted(best.keys()):
            print(f"  {k}: {best[k]:.3f}")
        print(f"\nBenchmark vs random over {args.benchmark_games} games:")
        print(f"  win_rate={wr:.3f}")
        print(f"  avg_margin={margin:.2f}")
    except Exception as exc:
        print(f"\nSimulation stopped due to error: {type(exc).__name__}: {exc}")
        print("No learning state was saved from this failed run.")


if __name__ == "__main__":
    main()
