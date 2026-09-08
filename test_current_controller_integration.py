"""End-to-end: the table votes the Current Controller in, and the game survives it.

Run:  python3 test_current_controller_integration.py

test_current_controller.py proves the VOTE on a room built by hand, and
test_current_controller.js proves the lobby row and the reward prune. Neither
of them ever starts a game, so neither can see the half that actually worries
a player: what a modded table does to a match that is already running.

Three things are only true if they are RUN:

  1. The unanimous yes really arms the seat that asked, and arms nobody else.
     The vote and the mod gate are different functions with different notions
     of "who you are" (a ballot is per PERSON, the mod gate is per TOKEN), so
     agreeing about a table of three is a claim about two code paths, not one.

  2. `admin_mod` lets that seat in WITH NO KEY. This is the whole point of the
     Tsunami perk, and it is the one door where a mistake is a security bug
     rather than a cosmetic one: the room is the authority, never the browser.
     ADMIN_MOD_KEY is deliberately unset for this whole file, so a seat that
     gets in here got in on the table's permission alone.

  3. A real mutation lands on a real engine and the match still finishes.
     Activating the Controller is not a passive flag: it turns on hidden-state
     capture and re-snapshots the game every time a mod applies, on the match
     thread, while a human seat is parked in _wait_for_action. That is exactly
     the sort of thing that deadlocks a table rather than erroring, and a test
     that never launches a game cannot tell the difference.

The match in part B is played out by the kicked-seat stand-in, the same
mechanism test_kick_integration.py uses and for the same reason: the only human
at the table is the one holding the Controller, and a human seat blocks for
half an hour waiting for input nobody is going to send. Kicking it hands the
chair to a bot so the table can reach a natural finish, which is the thing
being measured: a MODDED game that still ends in a real result.
"""
import atexit
import os
import shutil
import tempfile
import time

# A real match writes a training record into the shipped human-game dataset, a
# game-history file, a leaderboard entry and a room state file. A modded game is
# the last thing that should ever be fed back to the AI as an example of how
# people play, so every one of those paths is redirected before
# multiplayer_server is imported, because it resolves most of them at import.
_SANDBOX = tempfile.mkdtemp(prefix="cc-controller-int-")
atexit.register(shutil.rmtree, _SANDBOX, True)
os.environ["FISH_ROOM_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["FISH_GAMES_HISTORY_DIR"] = os.path.join(_SANDBOX, "games_history")
os.environ["FISH_COMPETITIVE_GAMES_DIR"] = os.path.join(_SANDBOX, "competitive_games")

# THE KEYLESS DOOR IS THE POINT. With a key in the environment every assertion
# below would still pass while proving nothing about the table's permission,
# because the route falls back to the key whenever the room says no.
os.environ.pop("ADMIN_MOD_KEY", None)

import multiplayer_server as mp

mp.DATASET_PATH = os.path.join(_SANDBOX, "human_game_dataset.jsonl")


def _wait_until(room, pred, timeout, poll=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with room.cond:
            if pred(room):
                return True
        time.sleep(poll)
    return False


def _may_mod(room, token):
    """Exactly the authorization the /api/rooms/<id>/admin_mod route runs, in
    the same order: resolve the token to a seat, then ask the ROOM. Copied
    shape rather than called through HTTP so the check is unmistakable, and
    because with ADMIN_MOD_KEY unset the route has no other way in."""
    with room.cond:
        seat = room._seat_from_token_locked(token)
        return bool(room._cc_may_mod_locked(seat))


def part_a_the_table_says_yes():
    """Three people at a casual table. One asks, the other two agree."""
    room = mp.GameRoom("CCVOTE", "Asker", total_players=4,
                       human_players=3, ai_players=1)
    host = room.host_seat()
    assert host is not None and host.token, "host seat not auto-claimed"
    assert room.claim_seat("Mate", None, None)["ok"]
    assert room.claim_seat("Pal", None, None)["ok"]

    asker = host
    others = [s for s in room.seats
              if s.kind == "human" and s.token and s.index != asker.index]
    assert len(others) == 2, f"expected two other humans, got {len(others)}"

    out = room.controller_request({"seat_token": asker.token})
    assert out.get("ok"), f"the ask was refused: {out}"
    assert not out.get("armed"), "one person's ask armed it with nobody agreeing"
    assert out.get("needed") == 2, f"expected 2 votes needed, got {out.get('needed')}"

    # The first yes is not enough. Unanimous means unanimous.
    out = room.controller_vote({"seat_token": others[0].token, "vote": True})
    assert out.get("ok"), f"the first vote was refused: {out}"
    assert not out.get("armed"), "it armed on ONE of two required yeses"
    assert not _may_mod(room, asker.token), "armed early: the asker could already mod"

    out = room.controller_vote({"seat_token": others[1].token, "vote": True})
    assert out.get("ok"), f"the second vote was refused: {out}"
    assert out.get("armed"), f"a unanimous yes did NOT arm it: {out}"
    print(f"3 humans, 1 bot: the ask needed {2} yeses, got both, and armed ✓")

    # Armed for the ASKER, and for nobody else at the table. The ballot is per
    # person and the mod gate is per token: this is where those two disagree.
    assert _may_mod(room, asker.token), "the table said yes and the asker still cannot mod"
    for s in others:
        assert not _may_mod(room, s.token), (
            f"seat {s.index} voted yes and was handed the Controller itself; "
            f"only the seat that ASKED may mod")
    assert not _may_mod(room, "not-a-real-token"), "a bogus token got in"

    # ARMED IS NOT MODDED: permission granted and never used is an ordinary game.
    assert room._admin_active is False, "merely arming already marked the game modded"
    payload = room._cc_payload_locked(asker)
    assert payload.get("armed") is True and payload.get("modded") is False, payload
    print("…and it armed the ASKER only, with the game still unmodded ✓")


def part_b_a_modded_game_still_finishes():
    """The armed seat really mods a running match, and the table plays on.

    One human and three bots, so the ask arms at once (bots are not in the
    denominator, which part A is what proves for a table with people in it).
    """
    room = mp.GameRoom("CCMOD", "Modder", total_players=4,
                       human_players=1, ai_players=3)
    host = room.host_seat()
    for seat in room.seats:
        if seat.kind == "ai":
            seat.difficulty = "easy"     # no rollouts: keeps the test quick
    room.ai_speed = "fast"

    out = room.controller_request({"seat_token": host.token})
    assert out.get("ok") and out.get("armed"), \
        f"alone with bots should arm at once: {out}"
    assert _may_mod(room, host.token)

    assert room.start_game(room.host_control_token, host.token, mp.CARD_DB)["ok"]
    human = next(s for s in room.seats if s.token == host.token)

    try:
        assert _wait_until(room, lambda r: r.active_action_seat is not None, timeout=60.0), \
            "the match never reached a first turn"
        # The vote survived the deal: a game starting must not clear the table's
        # permission, only a NEW game asks again.
        assert _may_mod(room, human.token), "starting the game dropped the arming"

        # This is the route's own next step once authorization passes.
        room.admin_activate()
        assert room._admin_active is True, "admin_activate did not mark the room"

        # A REAL mutation, on the live engine. It drains inside _wait_for_action
        # on the match thread, which is precisely why it is worth running: the
        # human seat is parked there right now.
        with room.cond:
            deck_uid = int(room._live_gs.deck[0])
            hand_before = len(room._seat_player(room._live_gs, human.index).hand)
        res = room.admin_enqueue_mod("hand_add", {"seat": human.index, "uid": deck_uid})
        assert res.get("ok"), f"the mod never applied: {res}"
        with room.cond:
            hand_after = room._seat_player(room._live_gs, human.index).hand
        assert len(hand_after) == hand_before + 1 and deck_uid in hand_after, (
            f"hand_add reported ok but the card is not in the hand "
            f"({hand_before} → {len(hand_after)})")
        print(f"a table-armed seat dealt itself card {deck_uid} with NO admin key ✓")

        # And the game now says so, on both surfaces a reward path can read.
        with room.cond:
            payload = room._cc_payload_locked(human)
        assert payload.get("modded") is True, f"the mod did not mark the game: {payload}"

        # Hand the chair to the stand-in so the table can reach a real finish.
        with room.cond:
            room._apply_kick_locked(room.seats[human.index])

        began = time.monotonic()
        finished = _wait_until(room, lambda r: r.phase != "running", timeout=420.0)
        elapsed = time.monotonic() - began
        turn = _wait_and_read(room, lambda r: int(r.last_turn_number))
        assert finished, (
            f"a MODDED match never finished: still running after {elapsed:.0f}s "
            f"on turn {turn}. Activating the Controller turns on hidden-state "
            f"capture and re-snapshots on the match thread; a deadlock there "
            f"parks the table instead of raising.")
        assert room.winner or room.final_scores, \
            "the modded match ended without producing a result"
        # Still flagged after the game ended: the record a save reads from.
        assert room._admin_active is True, "the modded flag was lost by the finish"
        print(f"the modded match played out to a real finish in {elapsed:.0f}s "
              f"({turn} turns), winner: {room.winner} ✓")
        print(f"final scores: {room.final_scores}")
    finally:
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def _wait_and_read(room, fn):
    with room.cond:
        return fn(room)


def part_c_one_no_still_ends_it():
    """The other half of "everyone accepts": one person declining really does
    close the request, on a room that has been through a live vote rather than
    one built for the assertion."""
    room = mp.GameRoom("CCNO", "Asker", total_players=3,
                       human_players=3, ai_players=0)
    host = room.host_seat()
    assert room.claim_seat("Mate", None, None)["ok"]
    assert room.claim_seat("Refuser", None, None)["ok"]
    others = [s for s in room.seats
              if s.kind == "human" and s.token and s.index != host.index]

    assert room.controller_request({"seat_token": host.token})["ok"]
    assert room.controller_vote({"seat_token": others[0].token, "vote": True})["ok"]
    out = room.controller_vote({"seat_token": others[1].token, "vote": False})
    assert out.get("denied"), f"a no did not deny the request: {out}"
    assert not _may_mod(room, host.token), "denied, and the asker could still mod"

    # A table cannot be worn down: asking again in the same game is refused.
    again = room.controller_request({"seat_token": host.token})
    assert not again.get("ok"), f"the asker got a second bite: {again}"
    print("one no closed the request for the game, and re-asking was refused ✓")


def part_d_a_rematch_asks_again():
    """The arming survives INTO its own game and no further.

    This is the other half of the fix in _launch_game_locked, and the half that
    is easy to lose while making the first half work: a launch out of the lobby
    is the game the table voted on, a launch out of "ended" is a new one. Get
    that backwards in the other direction and a single yes silently licenses
    every rematch the room ever plays.
    """
    room = mp.GameRoom("CCAGAIN", "Modder", total_players=4,
                       human_players=1, ai_players=3)
    host = room.host_seat()
    for seat in room.seats:
        if seat.kind == "ai":
            seat.difficulty = "easy"
    room.ai_speed = "fast"

    assert room.controller_request({"seat_token": host.token})["ok"]
    assert _may_mod(room, host.token), "alone with bots did not arm"

    # A finished game, without playing one: part B already plays a real match
    # through to a natural end, and what is under test here is the BRANCH, which
    # keys off the phase the launch starts from and nothing else. Driving a
    # whole second match to get here would only add three minutes and a
    # shutdown race (a room told to end mid-bot-turn does not stop on a dime).
    with room.cond:
        room.phase = "ended"
        room.ended_unix = mp.now_unix()
    assert room.game_thread is None, "this room was never supposed to launch one"

    try:
        out = room.restart_game(room.host_control_token, host.token, mp.CARD_DB)
        assert out.get("ok"), f"the restart was refused: {out}"
        after = next(s for s in room.seats if s.token == host.token)
        assert not _may_mod(room, after.token), (
            "a rematch inherited the last game's yes: one table decision would "
            "license every game the room plays from then on")
        print("a game launched out of 'ended' dropped the table's yes ✓")
    finally:
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def run():
    part_a_the_table_says_yes()
    part_c_one_no_still_ends_it()
    part_d_a_rematch_asks_again()
    part_b_a_modded_game_still_finishes()
    print("\nINTEGRATION: the table arms the Controller, the armed seat mods a "
          "live match with no key, and the game still finishes ✓")


if __name__ == "__main__":
    run()
