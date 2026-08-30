"""The "Last Turn" pill in the corner must report the turn, not guess at it.

Reported as "the thing in the top right that says what you play or drew is
wrong". It was wrong for one reason, and it was wrong for EVERY turn:

    GameRoom._describe_action was defined twice on the class. The second
    definition (the admin Current Controller's, an instance method returning a
    dict) shadowed the first (the classmethod that captions a turn), because a
    later `def` simply wins the class namespace. The captioning call site,
    GameRoom._describe_action(gs, ms, action), therefore bound `gs` to `self`
    and ran out of arguments: TypeError, every time, swallowed by a bare
    `except Exception: pass`.

So the server shipped `"actions": []` for every turn of every game ever played,
the client took its "older server" fallback branch, and that branch GUESSES:
its deck count was `2 - (cards that vanished from the pool)`, which announces
"Drew 2 from deck" over a turn that drew nothing and played a card.

The tests are grouped by what they defend:

  Collision   the shadowing itself, asserted against the class, so ANY future
              duplicate method name on GameRoom fails here rather than in a
              silent `except` six months later.
  Wording     what a caption says: the face that was PLAYED (a two-sided card
              has one side face-up on the board, naming both is a coin flip),
              the pool cards that were REALLY taken, and nothing at all about
              a deck draw, which is hidden information in a public summary.
  End to end  a real GameRoom, a real match on the real engine thread: every
              finished turn carries captions, and they name real cards.

Run:  python3 test_turn_notif_actions.py
"""
import ast
import os
import sys
import tempfile
import threading
import time

# Sandbox every path the server writes to BEFORE importing it, so a real match
# in a test can never dirty the working tree.
_TMP = tempfile.mkdtemp(prefix="cc-turn-notif-")
os.environ.setdefault("FISH_ROOM_STATE_DIR", os.path.join(_TMP, "state"))
os.environ.setdefault("FISH_GAMES_HISTORY_DIR", os.path.join(_TMP, "games_history"))
os.environ.setdefault("FISH_COMPETITIVE_GAMES_DIR", os.path.join(_TMP, "competitive_games"))
os.environ.setdefault("FISH_STATS_PATH", os.path.join(_TMP, "site_stats.json"))
# Rollout planning is what makes a bot turn take seconds. Captions do not
# care how well the bot plays, only that it plays, so switch it off.
os.environ.setdefault("FISH_DEEP_BOTS", "0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import multiplayer_server as mp                                    # noqa: E402

fish = mp.fish
mp.DATASET_PATH = os.path.join(_TMP, "human_game_dataset.jsonl")

CHECKS = [0]


def check(cond, label):
    CHECKS[0] += 1
    if not cond:
        raise AssertionError(label)


# ── a real deck, so captions are judged on the cards that actually ship ──────

def _deck():
    db = {}
    for name in ("cards_vertical.txt", "cards_lr.txt", "cards_oceans.txt"):
        with open(os.path.join(BASE_DIR, name), encoding="utf-8") as fh:
            db = fish.load_cards_from_lines(fh.read(), db, source=name, strict=True)
    return db


DB = _deck()
PRIMARY_TO_FACES, FACE_TO_PRIMARY = fish.build_non_ocean_pair_maps(DB)
MS = fish.MatchState(pair_primary_to_faces=PRIMARY_TO_FACES, face_to_primary=FACE_TO_PRIMARY)
GS = fish.GameState(card_db=DB, players=[], deck=[])

# One real two-sided card and two real oceans, read out of the deck files.
ENTRY, (FACE_A, FACE_B) = next(iter(PRIMARY_TO_FACES.items()))
NAME_A, NAME_B = DB[FACE_A].name, DB[FACE_B].name
OCEAN_UIDS = [uid for uid, c in sorted(DB.items()) if fish.is_ocean(c)]
# The deck ships several copies of each ocean, so pick two with DIFFERENT names,
# otherwise "the caption names both oceans" would pass on one word.
OCEAN_1 = OCEAN_UIDS[0]
OCEAN_2 = next(u for u in OCEAN_UIDS if DB[u].name != DB[OCEAN_1].name)


def caption(action):
    return mp.GameRoom._describe_turn_action(GS, MS, action)


# ── Collision ───────────────────────────────────────────────────────────────

def test_no_method_on_gameroom_is_defined_twice():
    """The whole bug in one assertion. A duplicate `def` on a class is not a
    syntax error and not a warning, the later one just silently wins."""
    with open(os.path.join(BASE_DIR, "multiplayer_server.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    dupes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.setdefault(item.name, []).append(item.lineno)
        for method, lines in seen.items():
            if len(lines) > 1:
                dupes.append(f"{node.name}.{method} defined at lines {lines}; "
                             f"only line {lines[-1]} is reachable")
    check(not dupes, "shadowed methods:\n  " + "\n  ".join(dupes))
    print("A PASS: no method on any server class is silently shadowed by a "
          "second definition")


def test_the_captioner_is_actually_callable_the_way_it_is_called():
    """The call site passes (gs, ms, action) on the CLASS. That must resolve to
    the captioner and return a string, not blow up into a bare except."""
    out = mp.GameRoom._describe_turn_action(
        GS, MS, fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_A, ocean_uid=OCEAN_1))
    check(isinstance(out, str) and out, f"captioner returned {out!r}")
    print("B PASS: GameRoom._describe_turn_action(gs, ms, action) resolves and "
          "returns a caption")


# ── Wording ─────────────────────────────────────────────────────────────────

def test_a_play_names_only_the_face_that_was_played():
    """Every non-ocean card is two-sided and one side goes down. Naming both is
    not a description, it is a guess that is wrong half the time."""
    a = caption(fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_A, ocean_uid=OCEAN_1))
    b = caption(fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_B, ocean_uid=OCEAN_1))
    check(NAME_A in a and NAME_B not in a, f"played {NAME_A}, caption said {a!r}")
    check(NAME_B in b and NAME_A not in b, f"played {NAME_B}, caption said {b!r}")
    check(a != b, "the two sides of one card cannot caption identically")
    print(f"C PASS: playing {NAME_A} says {NAME_A}, playing {NAME_B} says "
          f"{NAME_B}, never both")


def test_a_play_names_the_ocean_it_landed_on():
    out = caption(fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_A, ocean_uid=OCEAN_1))
    check(DB[OCEAN_1].name in out, f"no target ocean in {out!r}")
    print(f"D PASS: a play names its ocean ({out!r})")


def test_a_star_play_says_the_star_fired():
    plain = caption(fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_A, ocean_uid=OCEAN_1))
    starred = caption(fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_A,
                                  ocean_uid=OCEAN_1, use_star=True))
    check("★" in starred and "★" not in plain,
          f"star play {starred!r} vs plain {plain!r}")
    print("E PASS: a ★ play is captioned as one")


def test_a_pool_draw_names_the_card_that_was_taken():
    """A draw carries its picks in pool_pick_uids and NEVER sets card_uid (it
    stays at its -1 default). Reading card_uid captioned every single pool draw
    in the game's history as the literal words "a card"."""
    out = caption(fish.Action(kind="draw", draw_from_pool=1, pool_pick_uids=[ENTRY]))
    check(NAME_A in out and NAME_B in out,
          f"a pool draw must name the card taken, got {out!r}")
    check("a card" not in out, f"still the placeholder: {out!r}")
    check("pool" in out.lower(), f"a pool draw must say it came from the pool: {out!r}")
    print(f"F PASS: a pool draw names what was taken ({out!r})")


def test_a_pool_draw_names_both_sides():
    """Unlike a play, a draw takes the whole physical card, so both faces are
    what the player now holds, and both were face-up in the pool anyway."""
    out = caption(fish.Action(kind="draw", draw_from_pool=1, pool_pick_uids=[ENTRY]))
    check(f"{NAME_A} / {NAME_B}" in out, f"expected both faces in {out!r}")
    print("G PASS: a drawn card is named by both of its sides")


def test_a_deck_draw_never_leaks_the_card():
    """turn_summaries is public. A named deck draw would show the whole room a
    card only the drawer is allowed to know."""
    out = caption(fish.Action(kind="draw", draw_from_pool=0, card_uid=FACE_A))
    check(NAME_A not in out, f"deck draw leaked the card name: {out!r}")
    check("deck" in out.lower(), f"a deck draw must say so: {out!r}")
    print(f"H PASS: a deck draw stays unnamed ({out!r})")


def test_a_move_between_oceans_is_described():
    """The engine's kind is move_between_oceans. The captioner used to test for
    "move_card", which is not, and never was, a real action kind, so a move
    captioned itself as nothing at all."""
    kinds = set()
    with open(os.path.join(BASE_DIR, "fish_game_all_in_one.py"), encoding="utf-8") as fh:
        body = fh.read()
    check('"move_between_oceans"' in body, "engine no longer has move_between_oceans")
    check(body.count('kind == "move_card"') == 0,
          "move_card is not an engine action kind")
    out = caption(fish.Action(kind="move_between_oceans", card_uid=ENTRY, face_uid=FACE_A,
                              ocean_uid=OCEAN_2, source_ocean_uid=OCEAN_1))
    check(out, "a move captioned as nothing")
    check(NAME_A in out and NAME_B not in out, f"move must name the moved face: {out!r}")
    check(DB[OCEAN_1].name in out and DB[OCEAN_2].name in out,
          f"a move must name both oceans: {out!r}")
    del kinds
    print(f"I PASS: a move between oceans is captioned ({out!r})")


def test_ending_a_turn_is_not_something_that_happened_in_it():
    check(caption(fish.Action(kind="end_turn")) == "",
          "end_turn must not be captioned")
    print("J PASS: end_turn adds no line")


def test_a_long_discard_is_summarised_not_truncated_silently():
    uids = list(PRIMARY_TO_FACES)[:6]
    out = caption(fish.Action(kind="discard_batch_to_pool", pool_pick_uids=uids))
    check("more" in out, f"a 6-card discard must account for the rest: {out!r}")
    short = caption(fish.Action(kind="discard_batch_to_pool", pool_pick_uids=uids[:2]))
    check("more" not in short, f"a 2-card discard has no remainder: {short!r}")
    print("K PASS: a long discard says how many it did not list")


def test_no_caption_is_ever_the_placeholder_for_a_real_card():
    """Sweep every action kind against a real card: none may fall through to
    "a card", which is what an unresolvable uid renders as."""
    actions = [
        fish.Action(kind="draw", draw_from_pool=1, pool_pick_uids=[ENTRY]),
        fish.Action(kind="play_ocean", card_uid=OCEAN_1, face_uid=OCEAN_1),
        fish.Action(kind="play_to_ocean", card_uid=ENTRY, face_uid=FACE_B, ocean_uid=OCEAN_1),
        fish.Action(kind="discard_to_pool", card_uid=ENTRY),
        fish.Action(kind="discard_batch_to_pool", pool_pick_uids=[ENTRY]),
        fish.Action(kind="move_between_oceans", card_uid=ENTRY, face_uid=FACE_B,
                    ocean_uid=OCEAN_2, source_ocean_uid=OCEAN_1),
    ]
    bad = [f"{a.kind}: {caption(a)!r}" for a in actions if "a card" in caption(a)]
    check(not bad, "captions fell back to the placeholder:\n  " + "\n  ".join(bad))
    print("L PASS: every action kind resolves a real card name")


# ── End to end ──────────────────────────────────────────────────────────────

def _wait_until(pred, timeout, poll=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll)
    return False


def _drive_seat(room, token, seat, stop):
    """Take the human's turns with the simplest legal move on offer, so the
    match keeps moving and the bots get turns to caption."""
    while not stop.is_set() and room.phase == "running":
        with room.cond:
            active = room.active_action_seat
            lp = room.legal_actions_by_seat.get(seat)
        if active != seat or not lp or not lp.get("actions"):
            time.sleep(0.01)
            continue
        actions = lp["actions"]
        pick = next((i for i, a in enumerate(actions) if a.get("kind") == "end_turn"), None)
        if pick is None:
            pick = next((i for i, a in enumerate(actions) if a.get("kind") == "draw"), None)
        if pick is None:
            pick = 0
        room.submit_action({"seat_token": token, "action_index": pick,
                            "request_id": f"notif-{time.monotonic()}"})
        time.sleep(0.05)


def test_a_real_match_ships_captions_for_every_finished_turn():
    """The proof that matters: a real GameRoom, the real engine thread, real
    bots. Before the fix this produced turn_summaries whose "actions" were ALL
    empty lists, which is exactly what pushed the client onto its guessing
    fallback."""
    # A room must have a human seat, so the host takes one and is driven with
    # the plainest legal turn available while the two bots play real cards.
    room = mp.GameRoom("NOTIFTEST", "Tester", total_players=3, human_players=1, ai_players=2)
    room.ai_speed = "fast"
    host = room.host_seat()
    check(host is not None and host.token, "host seat not auto-claimed")
    started = room.start_game(room.host_control_token, host.token, mp.CARD_DB)
    check(started.get("ok"), f"could not start the match: {started}")
    stop = threading.Event()
    driver = threading.Thread(target=_drive_seat, args=(room, host.token, host.index, stop),
                              daemon=True)
    driver.start()
    try:
        got = _wait_until(lambda: len(room.turn_summaries) >= 4, timeout=90.0)
        summaries = list(room.turn_summaries)
        check(got, f"only {len(summaries)} turns finished in 90s")

        empty = [s for s in summaries if not s.get("actions")]
        check(not empty, f"{len(empty)} of {len(summaries)} finished turns shipped "
                         f"NO caption at all (this was 100% before the fix)")

        every_line = [line for s in summaries for line in s["actions"]]
        check(every_line, "no captions at all")
        check(all(isinstance(x, str) and x.strip() for x in every_line),
              "a caption is blank")
        check(not any("a card" in x for x in every_line),
              "a real turn captioned a card as the placeholder:\n  "
              + "\n  ".join(x for x in every_line if "a card" in x))

        names = {c.name for c in mp.CARD_DB.values()}
        named = [x for x in every_line if any(n in x for n in names)]
        check(named, "not one caption named a real card:\n  " + "\n  ".join(every_line[:10]))
        print(f"M PASS: {len(summaries)} finished turns, {len(every_line)} captions, "
              f"{len(named)} naming real cards. Sample:")
        for line in every_line[:6]:
            print(f"        {line}")
    finally:
        stop.set()
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()
        driver.join(timeout=5.0)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    order = [
        test_no_method_on_gameroom_is_defined_twice,
        test_the_captioner_is_actually_callable_the_way_it_is_called,
        test_a_play_names_only_the_face_that_was_played,
        test_a_play_names_the_ocean_it_landed_on,
        test_a_star_play_says_the_star_fired,
        test_a_pool_draw_names_the_card_that_was_taken,
        test_a_pool_draw_names_both_sides,
        test_a_deck_draw_never_leaks_the_card,
        test_a_move_between_oceans_is_described,
        test_ending_a_turn_is_not_something_that_happened_in_it,
        test_a_long_discard_is_summarised_not_truncated_silently,
        test_no_caption_is_ever_the_placeholder_for_a_real_card,
        test_a_real_match_ships_captions_for_every_finished_turn,
    ]
    check(len(order) == len(tests), "a test is not in the run order")
    failures = []
    for fn in order:
        try:
            fn()
        except AssertionError as exc:
            failures.append(f"{fn.__name__}: {exc}")
            print(f"FAIL {fn.__name__}: {exc}")
    print()
    if failures:
        print(f"{len(failures)} FAILED of {len(order)} ({CHECKS[0]} checks)")
        return 1
    print(f"ALL {len(order)} PASSED ({CHECKS[0]} checks) - the Last Turn pill "
          f"reports the turn instead of guessing at it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
