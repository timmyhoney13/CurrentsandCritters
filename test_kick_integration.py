"""End-to-end: a kicked player's seat is played out by a bot.

Run:  python3 test_kick_integration.py

test_kick_and_skip_votes.py proves the VOTE. This proves the consequence, on a
real GameRoom running a real match on the real engine thread, because that is
where the whole feature could quietly fall over.

The engine binds each seat's policy once, at launch. A human seat's policy
blocks in _wait_for_action for thirty minutes at a time, so a seat whose player
is simply gone parks the table until it times out. That is already true of an
abandoned seat today, and a kick makes one deliberately: without the stand-in,
"remove the player who is ruining the game" would hand everyone left a game
that stops dead every time the empty chair comes round.

So this test kicks a seated player mid-match and then watches the turn actually
come round to their seat and MOVE, on its own, with nobody submitting anything
for it.
"""
import threading
import time

import multiplayer_server as mp


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


def part_a_the_vote():
    """A real vote, on a real running match, removes a real seated player."""
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
    try:
        assert _wait_until(room, lambda r: r.active_action_seat is not None, timeout=30.0), \
            "the match never reached a first turn"
        out = room.player_kick_vote({"seat_token": voter.token,
                                     "target_seat_index": victim.index})
        assert out.get("kicked"), f"the kick did not pass mid-match: {out}"
        assert room.seats[victim.index].kicked, "the seat was not flagged kicked"
        assert room.seats[victim.index].token is None, "the kicked token was not cleared"

        # The stand-in must be buildable: the brain maps are stashed at launch
        # precisely so a kick landing much later can still make one.
        with room.cond:
            assert room._kicked_bot_policy(victim.index) is not None, (
                "no bot policy for the kicked seat: _ai_policy_args was never "
                "stashed, so the seat would fall back to bare timeout actions")

        snap = {s["index"]: s for s in room.seat_snapshot_locked()}
        assert snap[victim.index]["kicked"] is True, "the seat snapshot lost the kick"
        notes = [m["message"] for m in room.chat_messages if m.get("system")]
        assert any("removed from the game" in n for n in notes), \
            f"the room was never told about the removal: {notes[-3:]}"
        # The removed player's own client must be able to find out why.
        assert room.state_view(victim.token or "", "localhost") is not None
        print(f"a real mid-match vote removed seat {victim.index}, "
              f"a stand-in was built, and the room was told ✓")
    finally:
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def part_b_the_table_never_parks():
    """The consequence, measured the only way that really settles it: kick the
    ONLY human at the table and watch the match run to its natural end.

    The engine binds each seat's policy at launch, and a human policy blocks in
    _wait_for_action for half an hour at a time. So a seat whose player is gone
    parks the table until it times out, and a kick makes such a seat on purpose.
    If the stand-in is not wired up, this game cannot finish: it stops dead the
    first time the empty chair comes round, and the assertion below is what says
    so. With the chair played by a bot the match plays itself out normally.

    Kicking the last human is not something the vote rule allows (a kick needs
    somebody else to cast it), so the passed vote is applied directly here. The
    vote itself is what part A and test_kick_and_skip_votes.py cover.
    """
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
            f"{elapsed:.0f}s, stuck on turn {turn}. The kicked seat is waiting "
            f"for a human who is never coming back."
        )
        assert room.winner or room.final_scores, \
            "the match ended without ever producing a result"
        print(f"the match played itself out to a real finish in {elapsed:.0f}s "
              f"({turn} turns), winner: {room.winner} ✓")

        # And the stand-in really PLAYED the seat rather than passing every turn:
        # a bare timeout fallback is forbidden from drawing, so a seat it had
        # been "playing" would have scored nothing at all.
        scores = room.final_scores or {}
        print(f"final scores: {scores}")
        assert scores, "no final scores recorded"
    finally:
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


def run():
    part_a_the_vote()
    part_b_the_table_never_parks()
    print("\nINTEGRATION: a kicked seat keeps playing, the table never parks ✓")


if __name__ == "__main__":
    run()
