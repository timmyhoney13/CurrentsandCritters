"""End-to-end: a kicked player's seat is OUT, and no bot takes it over.

Run:  python3 test_kick_integration.py

test_kick_and_skip_votes.py proves the VOTE. This proves the consequence, on a
real GameRoom running a real match on the real engine thread, because that is
where the whole feature could quietly fall over.

Two things have to be true at once, and they pull against each other:

  1. NOBODY plays the chair. Not a bot, not a fallback that shrugs and picks
     "the first legal action" (which would be a card play), not another player
     drawing for them. Being removed has to actually mean removed.

  2. The table still goes round. The engine binds each seat's policy once, at
     launch, and a human policy blocks in _wait_for_action for thirty minutes
     at a time, so a seat whose player is simply gone parks the whole match
     every time it comes round. "Remove the player who is ruining the game"
     cannot hand everyone left a game that stops dead once a lap.

The only way to have both is a seat that takes the shortest legal EXIT from
its own turn and never anything else: end_turn, the discard the hand limit
forces, or the draw the rules will not let a turn end without. That allow-list
is _kicked_seat_action, and part B watches every single action it returns
across a whole real match.
"""
import atexit
import os
import shutil
import tempfile
import time

# A real match WRITES: a training record into the shipped human-game dataset, a
# game-history file, a leaderboard entry and a room state file. This test plays
# a whole game out, and an all-bot game whose "human" was kicked is exactly the
# sort of thing that must never be fed back into the AI as an example of how
# people play. Redirect all of it before multiplayer_server is even imported,
# because it resolves most of these paths once, at import time.
_SANDBOX = tempfile.mkdtemp(prefix="cc-kick-test-")
atexit.register(shutil.rmtree, _SANDBOX, True)
os.environ["FISH_ROOM_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["FISH_GAMES_HISTORY_DIR"] = os.path.join(_SANDBOX, "games_history")
os.environ["FISH_COMPETITIVE_GAMES_DIR"] = os.path.join(_SANDBOX, "competitive_games")

import multiplayer_server as mp

# DATASET_PATH has no environment knob, so it is redirected on the module.
mp.DATASET_PATH = os.path.join(_SANDBOX, "human_game_dataset.jsonl")

# The complete list of things a removed player's seat may ever do. Anything
# else coming out of _kicked_seat_action is somebody playing their chair.
ALLOWED_KINDS = {"end_turn", "discard_to_pool", "draw"}


def _read(room, fn):
    with room.cond:
        return fn(room)


def _wait_until(room, pred, timeout, poll=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with room.cond:
            if pred(room):
                return True
        time.sleep(poll)
    return False


def part_a_no_bot_exists_to_take_the_chair():
    """A real vote, on a real running match, removes a real seated player, and
    there is no machinery left anywhere that could hand the seat to a bot."""
    # The strongest form of "no bot takes over" is that the room cannot build
    # one even if some future code path asked it to. These attributes were the
    # stand-in: a cached per-seat AI policy and the brain maps kept at launch
    # solely to build it. Their absence is the guarantee.
    for gone in ("_kicked_bot_policy", "_kicked_policies", "_ai_policy_args"):
        assert not hasattr(mp.GameRoom, gone), (
            f"GameRoom.{gone} is back: the bot stand-in for kicked seats has "
            f"been reintroduced")

    room = mp.GameRoom("KICKINT", "Tester", total_players=4,
                       human_players=2, ai_players=2)
    host = room.host_seat()
    assert host is not None and host.token, "host seat not auto-claimed"
    assert room.claim_seat("Victim", None, None)["ok"]
    for seat in room.seats:
        if seat.kind == "ai":
            seat.difficulty = "easy"
    room.ai_speed = "fast"
    assert room.start_game(room.host_control_token, host.token, mp.CARD_DB)["ok"]

    # Seats are shuffled at launch, so find both humans by their tokens.
    voter = next(s for s in room.seats if s.token == host.token)
    victim = next(s for s in room.seats
                  if s.kind == "human" and s.token and s.index != voter.index)
    victim_token = victim.token
    try:
        assert _wait_until(room, lambda r: r.active_action_seat is not None, timeout=30.0), \
            "the match never reached a first turn"
        out = room.player_kick_vote({"seat_token": voter.token,
                                     "target_seat_index": victim.index})
        assert out.get("kicked"), f"the kick did not pass mid-match: {out}"
        assert room.seats[victim.index].kicked, "the seat was not flagged kicked"
        assert room.seats[victim.index].token is None, "the kicked token was not cleared"
        assert not hasattr(room, "_ai_policy_args"), \
            "the room stashed brain maps for a kicked-seat bot"

        # Nothing may reach in and play the seat from the outside either. Both
        # of these are gated on state a kicked seat can never hold, so they are
        # already unreachable: assert it anyway, because "draw 2 cards for
        # them" is the last way anyone could still play a removed player.
        d = room.draw_for_inactive({"seat_token": voter.token,
                                    "target_seat_index": victim.index})
        assert not d.get("ok"), f"a kicked seat can still be drawn for: {d}"
        with room.cond:
            a = room._afk_cast_vote_locked(room.seats[voter.index],
                                           room.seats[victim.index])
        assert not a.get("ok"), f"a kicked seat can still be voted AFK: {a}"

        snap = {s["index"]: s for s in room.seat_snapshot_locked()}
        assert snap[victim.index]["kicked"] is True, "the seat snapshot lost the kick"
        assert snap[victim.index]["kind"] == "human", \
            "the kicked seat turned into an AI seat: turn order would shift"

        notes = [m["message"] for m in room.chat_messages if m.get("system")]
        assert any("removed from the game" in n for n in notes), \
            f"the room was never told about the removal: {notes[-3:]}"
        assert not any("bot" in n.lower() for n in notes if "removed" in n), \
            f"the room was told a bot is playing the seat: {notes[-3:]}"

        # The removed player's own client must be able to find out why.
        assert room.kicked_token_notice(victim_token), \
            "the removed player's client is given no reason for losing its seat"
        print(f"a real mid-match vote removed seat {victim.index}; no bot can be "
              f"built for it, nobody can draw for it, and the room was told ✓")
    finally:
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def part_b_the_seat_only_ever_passes_and_the_table_never_parks():
    """The consequence, measured the only way that really settles it: kick the
    ONLY human at the table, watch the match run to its natural end, and record
    every action the dead seat takes on the way.

    Kicking the last human is not something the vote rule allows (a kick needs
    somebody else to cast it), so the passed vote is applied directly here. The
    vote itself is what part A and test_kick_and_skip_votes.py cover.
    """
    seen = []
    original = mp.GameRoom._kicked_seat_action

    def recording(self, gs, ms, player):
        action = original(self, gs, ms, player)
        seen.append(getattr(action, "kind", None) if action is not None else None)
        return action

    mp.GameRoom._kicked_seat_action = recording
    room = mp.GameRoom("KICKSOLO", "Tester", total_players=4,
                       human_players=1, ai_players=3)
    host = room.host_seat()
    for seat in room.seats:
        if seat.kind == "ai":
            seat.difficulty = "easy"    # no rollouts: keeps the test quick
    room.ai_speed = "fast"
    assert room.start_game(room.host_control_token, host.token, mp.CARD_DB)["ok"]
    human = next(s for s in room.seats if s.token == host.token)

    try:
        assert _wait_until(room, lambda r: r.active_action_seat is not None, timeout=30.0), \
            "the match never reached a first turn"
        with room.cond:
            room._apply_kick_locked(room.seats[human.index])
        assert room.seats[human.index].kicked
        print(f"the only human (seat {human.index}) was removed mid-match; "
              f"nobody is left at the table")

        began = time.monotonic()
        finished = _wait_until(room, lambda r: r.phase != "running", timeout=420.0)
        elapsed = time.monotonic() - began
        turn = _read(room, lambda r: int(r.last_turn_number))
        assert finished, (
            f"the table PARKED on the empty chair: still running after "
            f"{elapsed:.0f}s, stuck on turn {turn}. A kicked seat still has to "
            f"pass its own turn, or removing a player breaks the game for "
            f"everyone who stayed."
        )
        assert room.winner or room.final_scores, \
            "the match ended without ever producing a result"
        print(f"the match played itself out to a real finish in {elapsed:.0f}s "
              f"({turn} turns), winner: {room.winner} ✓")

        # THE assertion. Every action the removed seat took, across a whole
        # match, was an exit from its own turn. Not one card was played for it.
        assert seen, "the dead seat never acted at all: the recording never fired"
        bad = sorted({k for k in seen if k is not None and k not in ALLOWED_KINDS})
        assert not bad, (
            f"a removed player's seat PLAYED: {bad}. Only {sorted(ALLOWED_KINDS)} "
            f"are ever allowed, everything else is somebody taking the chair over."
        )
        counts = {k: seen.count(k) for k in sorted(set(seen), key=str)}
        print(f"the dead seat acted {len(seen)} times, all of them exits: {counts} ✓")

        # And it never became the seat the table is WAITING on: that flag means
        # "a human is being waited for", and the whole point is that nobody is.
        assert room.active_action_seat != human.index, \
            "the table is still waiting on the removed player's seat"
    finally:
        mp.GameRoom._kicked_seat_action = original
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def run():
    part_a_no_bot_exists_to_take_the_chair()
    part_b_the_seat_only_ever_passes_and_the_table_never_parks()
    print("\nINTEGRATION: a kicked seat is out, no bot takes it, "
          "and the table never parks ✓")


if __name__ == "__main__":
    run()
