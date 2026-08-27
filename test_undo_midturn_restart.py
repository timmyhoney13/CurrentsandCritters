"""What the one Undo button means, proven against a real room and a real engine.

It means two different things and the difference matters:

  * you have already done something in the turn you are in  -> RESTART THIS TURN,
    putting back every card played and every card paid, and touching nothing that
    happened before this turn started;
  * you have not touched this turn yet                      -> TAKE BACK YOUR LAST
    COMPLETED TURN, which is the window that survives any number of bot turns.

The first case used to restore `undo_snapshot_gs`, which is the window for the
LAST COMPLETED turn. Between two of your turns a bot plays, and a bot turn leaves
that window standing, so a mid-turn press could rewind a whole round nobody asked
to take back, or, once the turn boundary had already re-pointed it, find no
snapshot at all and quietly re-offer legal actions having reverted nothing: "it
did not give me the cards I paid back, but it let me play again".
"""
import atexit
import os
import shutil
import tempfile
import time

import multiplayer_server as mp

# A real match writes a training record, a game-history file and a competitive
# game record. Point all of them at a temp dir so these throwaway rooms never
# land in the shipped dataset.
_SANDBOX = tempfile.mkdtemp(prefix="cc-undo-test-")
mp.DATASET_PATH = os.path.join(_SANDBOX, "human_game_dataset.jsonl")
mp.GAMES_HISTORY_DIR = os.path.join(_SANDBOX, "games_history")
mp.GAMES_LEADERBOARD_PATH = os.path.join(mp.GAMES_HISTORY_DIR, "leaderboard.json")
mp.COMPETITIVE_GAMES_DIR = os.path.join(_SANDBOX, "competitive_games")
atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))


def wait_until(pred, timeout=30.0, poll=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll)
    return False


def new_room(code):
    room = mp.GameRoom(code, "Tester", total_players=2, human_players=1, ai_players=1)
    room.ai_speed = "fast"
    host = room.host_seat()
    assert host is not None and host.token, "host seat not auto-claimed"
    started = room.start_game(room.host_control_token, host.token, mp.CARD_DB)
    assert started["ok"], started
    seat = host.index
    assert room.seats[seat].kind == "human", "the host seat is not the human seat"
    return room, host.token, seat


def snap(room, seat):
    """Hand of the human seat plus a summary of everything the bot owns. A restart
    of the current turn must move the first and leave the second alone."""
    with room.cond:
        gs = room._live_gs
        if gs is None:
            return None
        me = gs.players[seat]
        others = [
            (p.name, len(p.hand), len(p.board_oceans),
             sum(len(s.all_cards()) for s in p.ocean_slots.values()), p.score)
            for i, p in enumerate(gs.players) if i != seat
        ]
        return list(me.hand), others, len(gs.deck), list(room.latest_public_state or {})


def my_turn(room, seat):
    with room.cond:
        return (room.active_action_seat == seat
                and bool(room.legal_actions_by_seat.get(seat, {}).get("actions")))


def submit_first(room, token, seat, kinds):
    """Submit the first offered action whose kind is in `kinds`. Returns its kind."""
    with room.cond:
        actions = room.legal_actions_by_seat.get(seat, {}).get("actions", [])
    for i, a in enumerate(actions):
        if a.get("kind") in kinds:
            room.submit_action({"seat_token": token, "action_index": i,
                                "request_id": f"r-{time.monotonic()}"})
            return a.get("kind")
    return None


def undo_info(room):
    with room.cond:
        return {
            "valid": room.undo_valid,
            "eligible_seat": room.undo_eligible_seat,
            "requested": room.undo_requested,
            "can_restart_turn": bool(
                room.active_action_seat is not None
                and room._turn_acted_seat == room.active_action_seat
                and room._no_restart_seat != room.active_action_seat
                and room._undo_pending_gs is not None
                and room._undo_pending_seat == room.active_action_seat
            ),
        }


def legal_stamp(room, seat):
    with room.cond:
        payload = room.legal_actions_by_seat.get(seat)
        if not payload:
            return None
        return (payload.get("updated_unix"), len(payload.get("actions", [])),
                tuple(a.get("kind") for a in payload.get("actions", [])))


def turn_no(room):
    with room.cond:
        return room.last_turn_number


def finish_turn(room, token, seat):
    """Play out EXACTLY the turn in progress and stop the moment it hands off.
    Prefers plain draws, then single-card discards for the end-of-turn trim (a
    batch discard needs explicit picks), then anything else the engine accepts."""
    started_on = turn_no(room)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if turn_no(room) != started_on:
            return True
        if not my_turn(room, seat):
            time.sleep(0.02)
            continue
        stamp = legal_stamp(room, seat)
        for wanted in ({"draw"}, {"end_turn"}, {"discard_to_pool"},
                       {"play_to_ocean", "play_ocean"}):
            if submit_first(room, token, seat, wanted) is not None:
                break
        # Wait for the engine to consume it (a new legal-action payload, or the
        # turn moving on) before choosing again, so one action is never queued
        # twice, and never wait forever if the submission was dropped.
        wait_until(lambda: legal_stamp(room, seat) != stamp
                   or turn_no(room) != started_on, timeout=4.0)
    diag(room, seat, "turn never ended")
    raise AssertionError("turn never ended")


def wait_for_my_next_turn(room, seat, after_turn, timeout=60.0):
    """Block until the human is on the clock in a turn LATER than after_turn."""
    ok = wait_until(lambda: my_turn(room, seat) and turn_no(room) > after_turn,
                    timeout=timeout)
    if not ok:
        diag(room, seat, f"never got a turn after turn {after_turn}")
    return ok


def diag(room, seat, label):
    with room.cond:
        kinds = [a.get("kind") for a in
                 room.legal_actions_by_seat.get(seat, {}).get("actions", [])][:6]
        print(f"  ! {label}: phase={room.phase} note={room.status_note!r} "
              f"active={room.active_action_seat} acted={room._turn_acted_seat} "
              f"valid={room.undo_valid} eligible={room.undo_eligible_seat} "
              f"requested={room.undo_requested}/{room.undo_requested_seat} "
              f"pending_seat={room._undo_pending_seat} "
              f"queued={ {k: [c.get('kind') for c in v] for k, v in room.pending_actions.items()} } "
              f"kinds={kinds}")
        for line in room.log_events[-8:]:
            print(f"      {line}")


def test_restart_rewinds_this_turn_only():
    room, token, seat = new_room("MIDTURN1")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "human never got a first turn"
        first_turn = turn_no(room)
        finish_turn(room, token, seat)

        # Wait for the bot to play and the human's SECOND turn to come round.
        assert wait_for_my_next_turn(room, seat, first_turn), "human never got a second turn"

        start = snap(room, seat)
        info = undo_info(room)
        assert info["valid"] and info["eligible_seat"] == seat, info
        assert info["can_restart_turn"] is False, (
            "nothing has been done in this turn yet, so Undo cannot mean 'restart it'"
        )

        # Act once in this turn.
        kind = submit_first(room, token, seat, {"draw"})
        assert kind == "draw", f"expected a draw to be on offer, got {kind}"
        assert wait_until(lambda: snap(room, seat)[0] != start[0], timeout=10), "the draw never landed"
        assert wait_until(lambda: undo_info(room)["can_restart_turn"], timeout=10), (
            "after acting, Undo must offer to restart this turn"
        )
        mid = snap(room, seat)
        assert len(mid[0]) == len(start[0]) + 1, (len(start[0]), len(mid[0]))

        out = room.submit_undo({"seat_token": token})
        assert out == {"ok": True}, out

        assert wait_until(lambda: snap(room, seat)[0] == start[0], timeout=15), (
            f"the restart did not put the hand back: want {start[0]}, got {snap(room, seat)[0]}"
        )
        after = snap(room, seat)
        assert after[1] == start[1], (
            f"the restart rewound the BOT's turn as well, which is a whole round the "
            f"player never asked to take back: {start[1]} -> {after[1]}"
        )
        assert after[2] == start[2], f"deck size not restored: {start[2]} -> {after[2]}"
        with room.cond:
            assert room.undo_requested is False, "the request must be cleared once honored"
            assert room._turn_acted_seat is None, "the restarted turn has nothing done in it"
            assert room._undo_pending_seat == seat and room._undo_pending_gs is not None, (
                "the restore point for this turn must survive so the player can restart again"
            )
        print("A PASS: a mid-turn restart puts this turn back and leaves the bot's turn alone")

        # And it must be repeatable: draw, restart, draw, restart.
        assert wait_until(lambda: my_turn(room, seat), timeout=20), "turn never re-offered"
        assert submit_first(room, token, seat, {"draw"}) == "draw"
        assert wait_until(lambda: len(snap(room, seat)[0]) == len(start[0]) + 1, timeout=10)
        assert wait_until(lambda: undo_info(room)["can_restart_turn"], timeout=10)
        out = room.submit_undo({"seat_token": token})
        assert out == {"ok": True}, out
        assert wait_until(lambda: snap(room, seat)[0] == start[0], timeout=15), (
            "the second restart in the same turn did nothing, the restore point was thrown away"
        )
        print("B PASS: the turn can be restarted more than once, the restore point survives")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_untouched_turn_still_takes_back_the_last_one():
    room, token, seat = new_room("MIDTURN2")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "human never got a first turn"
        first_start = snap(room, seat)
        first_turn = turn_no(room)
        finish_turn(room, token, seat)
        assert wait_for_my_next_turn(room, seat, first_turn), "human never got a second turn"

        info = undo_info(room)
        assert info["can_restart_turn"] is False, info
        out = room.submit_undo({"seat_token": token})
        assert out == {"ok": True}, out
        # This is the ordinary "undo my last turn": the engine replays the human's
        # FIRST turn, so the hand goes back to how that turn started.
        if not wait_until(lambda: snap(room, seat)[0] == first_start[0], timeout=25):
            diag(room, seat, "last completed turn not replayed")
            raise AssertionError(
                f"the last completed turn was not replayed: want {first_start[0]}, "
                f"got {snap(room, seat)[0]}"
            )
        print("C PASS: pressing Undo before touching your turn still takes back your last one")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_a_dead_undo_is_refused_not_faked():
    room, token, seat = new_room("MIDTURN3")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "human never got a turn"
        assert submit_first(room, token, seat, {"draw"}) == "draw"
        assert wait_until(lambda: undo_info(room)["can_restart_turn"], timeout=10)
        before = snap(room, seat)
        # Lose the restore point the way a turn boundary would.
        with room.cond:
            room._undo_pending_gs = None
        out = room.submit_undo({"seat_token": token})
        assert out["ok"] is False and "restore point" in out["error"], out
        with room.cond:
            assert not room.pending_actions.get(seat), (
                "a refused undo must not queue a restart, that is what turned a dead "
                "undo into a fresh prompt with the cards still spent"
            )
        time.sleep(0.4)
        assert snap(room, seat)[0] == before[0], "a refused undo changed the hand"
        print("D PASS: with no restore point the undo is refused outright, nothing is faked")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_stale_restart_never_survives_the_turn():
    room, token, seat = new_room("MIDTURN4")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "human never got a turn"
        first_turn = turn_no(room)
        finish_turn(room, token, seat)
        assert wait_until(lambda: turn_no(room) != first_turn, timeout=20), "turn never handed off"
        # A press that lands as the turn ends leaves this command queued behind a
        # turn that is already over. Left there it fired at the start of some later
        # turn and rewound a turn nobody asked to take back.
        with room.cond:
            room.pending_actions.setdefault(seat, []).insert(0, {"kind": "undo_mid_turn"})
        assert wait_for_my_next_turn(room, seat, first_turn), "human never got a second turn"
        with room.cond:
            queued = [c.get("kind") for q in room.pending_actions.values() for c in q]
        assert "undo_mid_turn" not in queued, (
            f"a restart command outlived its turn and was queued to fire later: {queued}"
        )
        hand_now = snap(room, seat)[0]
        time.sleep(0.5)
        assert snap(room, seat)[0] == hand_now, "the stale restart fired anyway"
        print("E PASS: a queued turn restart never outlives the turn it was pressed in")
    finally:
        room.terminate_game(room.host_control_token, token)


if __name__ == "__main__":
    test_restart_rewinds_this_turn_only()
    test_untouched_turn_still_takes_back_the_last_one()
    test_a_dead_undo_is_refused_not_faked()
    test_stale_restart_never_survives_the_turn()
    print("\nALL MID-TURN UNDO TESTS PASSED ✓")
