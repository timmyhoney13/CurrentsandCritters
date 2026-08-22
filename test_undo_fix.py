"""Verification for the undo fix.

Drives the REAL GameRoom.submit_undo and GameRoom._apply_pending_undo_restore
methods (bound to a lightweight stub) to prove:

  A. Undo during a BOT turn (active_action_seat is None) now ARMS the flag
     instead of failing with "no active player", this is the reported bug.
  B. The AI/human policy honoring the flag restores the pre-turn snapshot
     EXACTLY (cards put back, deck put back) and signals Action(kind='undo').
  C. The existing human-next routing (undo_confirm queue) still works.
  D. The existing mid-turn routing (undo_mid_turn queue) still works.
  E. Guard rails: wrong seat / no snapshot / not requested are handled.
"""
import threading
import types
import copy

import multiplayer_server as mp

fish = mp.fish
GameRoom = mp.GameRoom
Seat = mp.Seat


def make_stub(seats, *, active, eligible=0, valid=True, snapshot=True, phase="running"):
    stub = types.SimpleNamespace()
    stub.cond = threading.Condition()
    stub.phase = phase
    stub.seats = seats
    stub.undo_valid = valid
    stub.undo_eligible_seat = eligible
    stub.undo_requested = False
    stub.active_action_seat = active
    stub.legal_actions_by_seat = {0: "stale", 1: "stale"}
    stub.pending_actions = {}
    stub.status_note = ""
    stub._undo_pending_gs = "pending"
    stub._undo_pending_ms = "pending"
    stub._undo_pending_seat = 0
    # The turn-start snapshot: hand + deck BEFORE the player drew/played.
    if snapshot:
        stub.undo_snapshot_gs = types.SimpleNamespace(
            hand=["A", "B", "C", "D"], deck=[1, 2, 3, 4, 5], turn_index=0, played=[]
        )
        stub.undo_snapshot_ms = types.SimpleNamespace(pool=[9, 8, 7])
    else:
        stub.undo_snapshot_gs = None
        stub.undo_snapshot_ms = None
    # Bind the real methods.
    stub._seat_from_token_locked = GameRoom._seat_from_token_locked.__get__(stub)
    stub.submit_undo = GameRoom.submit_undo.__get__(stub)
    stub._apply_pending_undo_restore = GameRoom._apply_pending_undo_restore.__get__(stub)
    stub._wait_for_action = GameRoom._wait_for_action.__get__(stub)
    stub._drain_admin_mods_locked = lambda *a, **k: None
    stub._bump_locked = lambda *a, **k: None
    stub._record_event = lambda *a, **k: None
    return stub


def live_gs_ms():
    """Current (post-play) state: 2 cards in hand, 3 fewer in deck, what the
    player is looking at after drawing/playing this turn."""
    gs = types.SimpleNamespace(hand=["A", "B"], deck=[1, 2], turn_index=0, played=["C", "D"])
    ms = types.SimpleNamespace(pool=[9, 8, 7, 6, 5])
    return gs, ms


def test_A_bot_turn_arms_flag():
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "ai", "Bot")]
    stub = make_stub(seats, active=None)  # bot is playing -> active is None
    out = stub.submit_undo({"seat_token": "tok0"})
    assert out == {"ok": True}, out
    assert stub.undo_requested is True
    # Must NOT have been (mis)routed into any queue, the AI picks it up via flag.
    assert stub.pending_actions == {}, stub.pending_actions
    print("A PASS: undo during bot turn arms the flag (no 'no active player' error)")


def test_B_flag_restores_snapshot():
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "ai", "Bot")]
    stub = make_stub(seats, active=None)
    stub.submit_undo({"seat_token": "tok0"})  # arm
    gs, ms = live_gs_ms()
    action = stub._apply_pending_undo_restore(gs, ms)  # AI policy honors it
    assert action is not None and action.kind == "undo", action
    # Cards put BACK exactly as the turn started.
    assert gs.hand == ["A", "B", "C", "D"], gs.hand
    assert gs.deck == [1, 2, 3, 4, 5], gs.deck
    assert gs.played == [], gs.played
    assert ms.pool == [9, 8, 7], ms.pool
    # Flags fully cleared so the engine replays cleanly.
    assert stub.undo_requested is False
    assert stub.undo_valid is False
    assert stub.undo_snapshot_gs is None and stub.undo_snapshot_ms is None
    assert stub._undo_pending_gs is None and stub._undo_pending_seat is None
    assert stub.legal_actions_by_seat == {}
    assert stub.pending_actions == {}
    assert stub.active_action_seat is None
    print("B PASS: flag-driven undo restores hand+deck exactly and returns Action(undo)")


def test_C_human_next_uses_queue():
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "human", "P2", token="tok1")]
    stub = make_stub(seats, active=1)  # next human waiting
    out = stub.submit_undo({"seat_token": "tok0"})
    assert out == {"ok": True}, out
    assert stub.pending_actions.get(1) == [{"kind": "undo_confirm"}], stub.pending_actions
    print("C PASS: human-next undo still routes via undo_confirm queue")


def test_D_mid_turn_uses_queue():
    seats = [Seat(0, "human", "P1", token="tok0")]
    stub = make_stub(seats, active=0)  # player's own turn
    out = stub.submit_undo({"seat_token": "tok0"})
    assert out == {"ok": True}, out
    assert stub.pending_actions.get(0) == [{"kind": "undo_mid_turn"}], stub.pending_actions
    print("D PASS: mid-turn undo still routes via undo_mid_turn queue")


def test_E_guards():
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "human", "P2", token="tok1")]
    # Wrong seat (seat 1 not eligible).
    stub = make_stub(seats, active=None, eligible=0)
    out = stub.submit_undo({"seat_token": "tok1"})
    assert out["ok"] is False and "not your undo" in out["error"], out
    # No snapshot.
    stub = make_stub(seats, active=None, snapshot=False)
    out = stub.submit_undo({"seat_token": "tok0"})
    assert out["ok"] is False and "snapshot" in out["error"], out
    # undo not valid.
    stub = make_stub(seats, active=None, valid=False)
    out = stub.submit_undo({"seat_token": "tok0"})
    assert out["ok"] is False and "undo not available" in out["error"], out
    # Helper is a no-op when nothing is requested.
    stub = make_stub(seats, active=None)
    gs, ms = live_gs_ms()
    before = copy.deepcopy(gs.__dict__)
    assert stub._apply_pending_undo_restore(gs, ms) is None
    assert gs.__dict__ == before
    print("E PASS: wrong-seat / no-snapshot / invalid / not-requested all guarded")


def test_F_blocked_human_wakes_on_armed_undo():
    """The handoff-race fix: a human parked in _wait_for_action must wake with the
    __undo_armed__ sentinel when a flag-driven undo is armed for another seat, so
    its policy re-loops and applies the restore instead of hanging until timeout."""
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "human", "P2", token="tok1")]
    stub = make_stub(seats, active=None, eligible=0)
    stub.undo_requested = True  # armed via the flag path while seat 1 was waiting
    cmd = stub._wait_for_action(1, timeout_sec=2.0)
    assert cmd == {"kind": "__undo_armed__"}, cmd
    print("F PASS: blocked human wakes with __undo_armed__ when a flag undo is armed")


def test_G_queue_beats_sentinel():
    """An explicit undo_confirm/undo_mid_turn command in the seat's queue must still
    win over the flag sentinel, so the dedicated routing paths are never bypassed."""
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "human", "P2", token="tok1")]
    stub = make_stub(seats, active=1, eligible=0)
    stub.undo_requested = True
    stub.pending_actions = {1: [{"kind": "undo_confirm"}]}
    cmd = stub._wait_for_action(1, timeout_sec=2.0)
    assert cmd == {"kind": "undo_confirm"}, cmd
    print("G PASS: an explicit queued command still beats the undo sentinel")


def test_H_wait_times_out_when_nothing_armed():
    """No regression: with no undo armed and an empty queue, the wait still blocks
    and returns None on timeout (so normal play is completely unaffected)."""
    seats = [Seat(0, "human", "P1", token="tok0")]
    stub = make_stub(seats, active=0)
    stub.undo_requested = False
    cmd = stub._wait_for_action(0, timeout_sec=0.3)
    assert cmd is None, cmd
    print("H PASS: normal wait still times out to None when no undo is armed")


def test_I_no_sentinel_without_snapshot():
    """undo_requested set but no snapshot (an impossible-but-defensive state) must
    NOT yield the sentinel, we never wake a player for an undo we cannot apply."""
    seats = [Seat(0, "human", "P1", token="tok0"), Seat(1, "human", "P2", token="tok1")]
    stub = make_stub(seats, active=None, eligible=0, snapshot=False)
    stub.undo_requested = True
    stub.undo_valid = True
    cmd = stub._wait_for_action(1, timeout_sec=0.3)
    assert cmd is None, cmd
    print("I PASS: no sentinel (and no wake) when the snapshot is missing")


if __name__ == "__main__":
    test_A_bot_turn_arms_flag()
    test_B_flag_restores_snapshot()
    test_C_human_next_uses_queue()
    test_D_mid_turn_uses_queue()
    test_E_guards()
    test_F_blocked_human_wakes_on_armed_undo()
    test_G_queue_beats_sentinel()
    test_H_wait_times_out_when_nothing_armed()
    test_I_no_sentinel_without_snapshot()
    print("\nALL UNDO TESTS PASSED ✓")
