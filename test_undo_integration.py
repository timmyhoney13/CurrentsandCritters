"""End-to-end integration test for the Undo button.

Unlike test_undo_fix.py (which exercises the undo methods against a stub), this
spins up a REAL GameRoom, starts a REAL match (1 human + 1 bot) on the actual
engine thread, drives the human's turn through the real submit_action API, then
presses Undo during the BOT's turn via the real submit_undo — exactly the path a
player hits in the live game. It proves the full wiring:

    human plays a turn  →  bot starts thinking  →  human presses Undo  →
    the bot's think-pause is interrupted, the pre-turn snapshot is restored,
    and the human's hand/board revert and their turn replays.

This is the scenario the fix targets ("I pressed Undo during the bots' turns and
nothing happened"). The bot is forced to "slow" thinking so that, without the
interruptible-think fix, the undo could only land seconds later (after the bot's
move); with the fix it lands in well under a second.
"""
import threading
import time

import multiplayer_server as mp

fish = mp.fish


def _read(room, fn):
    with room.cond:
        return fn(room)


def _wait_until(room, pred, timeout, poll=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with room.cond:
            if pred(room):
                return True
        time.sleep(poll)
    return False


def _human_hand_len(room, seat):
    # Which SEAT the human gets is randomised at start_game — the host is not
    # always seat 0. seat→game-player index is identity for a non-competitive
    # room, so the human's engine-side player is players[seat].
    gs = room._live_gs
    if gs is None or not getattr(gs, "players", None):
        return None
    return len(gs.players[seat].hand)


def drive_human_turn(room, token, seat, timeout=25.0):
    """Complete the human's current turn using only legal actions: draw from the
    deck when offered, end the turn as soon as that's allowed. Returns when the
    turn has been handed off (active_action_seat moves away from the human)."""
    deadline = time.monotonic() + timeout
    last_submit = 0.0
    while time.monotonic() < deadline:
        if room.phase != "running":
            return False
        with room.cond:
            active = room.active_action_seat
            lp = room.legal_actions_by_seat.get(seat)
        if active != seat or not lp:
            time.sleep(0.01)
            continue
        actions = lp.get("actions", [])
        if not actions:
            time.sleep(0.01)
            continue
        # Prefer to end the turn; otherwise draw from the deck; else first action.
        choice = next((i for i, a in enumerate(actions) if a.get("kind") == "end_turn"), None)
        if choice is None:
            choice = next(
                (i for i, a in enumerate(actions)
                 if a.get("kind") == "draw" and int(a.get("draw_from_pool", 0)) == 0),
                None,
            )
        if choice is None:
            choice = next((i for i, a in enumerate(actions) if a.get("kind") == "draw"), None)
        if choice is None:
            # Only discards/plays left — submit the first; harmless for arming undo.
            choice = 0
        room.submit_action({"seat_token": token, "action_index": choice,
                            "request_id": f"drive-{time.monotonic()}"})
        last_submit = time.monotonic()
        # Let the engine consume the action and either re-offer or hand off.
        handed_off = _wait_until(
            room,
            lambda r: r.active_action_seat != seat or r.legal_actions_by_seat.get(seat) is not lp,
            timeout=3.0,
        )
        with room.cond:
            if room.active_action_seat != seat:
                return True
        if not handed_off:
            time.sleep(0.02)
    return _read(room, lambda r: r.active_action_seat != seat)


def run():
    room = mp.GameRoom("UNDOINT", "Tester", total_players=2, human_players=1, ai_players=1)
    # The creator auto-claims the host seat at construction — but WHICH seat that
    # is gets randomised when the match starts, so it is seat 1 about half the
    # time. Everything below keys off host_seat.index; hardcoding 0 made this
    # test wait out its timeout on the BOT's seat whenever the draw went the
    # other way (it failed ~3 runs in 4, while reporting a game bug that was
    # only ever the test's own assumption).
    host_seat = room.host_seat()
    assert host_seat is not None and host_seat.token, "host seat not auto-claimed"
    token = host_seat.token

    # Force the bot to "think" slowly so the undo must interrupt a real pause.
    room.ai_speed = "slow"

    started = room.start_game(room.host_control_token, token, mp.CARD_DB)
    assert started["ok"], started
    human = host_seat.index
    assert room.seats[human].kind == "human", "the host seat is not the human seat"
    print(f"game started (1 human on seat {human} + 1 slow bot)")

    try:
        # Wait for the human's first turn and capture the turn-start hand size.
        assert _wait_until(room, lambda r: r.active_action_seat == human
                           and r.legal_actions_by_seat.get(human), timeout=15.0), \
            "human's first turn never became active"
        turn_start_hand = _read(room, lambda r: _human_hand_len(r, human))
        assert isinstance(turn_start_hand, int), "could not read human hand"
        print(f"human turn-start hand size = {turn_start_hand}")

        # Play out the human's whole turn.
        assert drive_human_turn(room, token, human), "could not complete the human's turn"
        after_turn_hand = _read(room, lambda r: _human_hand_len(r, human))
        print(f"human completed turn; hand size now = {after_turn_hand}")

        # Wait until the bot's turn has begun AND the undo is armed for the human.
        assert _wait_until(room, lambda r: r.undo_valid and r.undo_eligible_seat == human
                           and r.active_action_seat != human, timeout=15.0), \
            "undo never became valid for the human during the bot's turn"
        print("bot is now taking its (slow) turn; undo is armed for the human")

        # Press Undo during the bot's turn — the reported-broken scenario.
        fired_at = time.monotonic()
        out = room.submit_undo({"seat_token": token})
        assert out.get("ok"), out
        print("pressed Undo during the bot's turn -> submit_undo ok")

        # The durable, unambiguous proof the undo landed: the human's hand reverts
        # to its turn-start size AND it is the human's turn again (the turn replays).
        # ("Undo granted" in status_note is too transient to poll — the replayed
        # human turn overwrites it within milliseconds.)
        #
        # Timing also proves the interrupt: "slow" think is 3.0-5.5s, so without the
        # interruptible-think fix the bot would still be sleeping now and the revert
        # could not appear inside 2.5s. The fix lands it in well under a second.
        assert after_turn_hand != turn_start_hand, (
            "test precondition failed: the human's hand did not change during their "
            f"turn (both {turn_start_hand}), so a revert can't be observed"
        )
        reverted = _wait_until(
            room,
            lambda r: _human_hand_len(r, human) == turn_start_hand and r.active_action_seat == human,
            timeout=2.5,
        )
        elapsed = time.monotonic() - fired_at
        now_hand = _read(room, lambda r: _human_hand_len(r, human))
        assert reverted, (
            f"undo NOT honored within 2.5s: hand={now_hand} (want turn-start {turn_start_hand}), "
            f"active_seat={_read(room, lambda r: r.active_action_seat)} "
            f"— the bot's think-pause was not interrupted / cards not put back"
        )
        print(f"undo honored + hand reverted {after_turn_hand}->{turn_start_hand} "
              f"in {elapsed:.3f}s (bot 'slow' think is 3.0-5.5s) ✓")

        # The undo flag must be consumed (not left armed/stuck).
        assert _wait_until(room, lambda r: not r.undo_requested, timeout=2.0), \
            "undo_requested left stuck True after the restore"
        print("undo_requested cleared after restore ✓")

        print("\nINTEGRATION: undo-during-bot-turn WORKS END TO END ✓")
    finally:
        # Tear the match thread down so the test process can exit cleanly.
        with room.cond:
            room.phase = "ended"
            room.cond.notify_all()


if __name__ == "__main__":
    run()
