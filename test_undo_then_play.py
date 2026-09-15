"""After Undo you can PLAY, wherever the button was pressed. Real room, real engine.

The report: "I clicked Undo, it gave me my cards back, but it didn't let me play."

Every earlier undo test stops at "the hand came back". Each case here takes the
step a player takes next and plays a card, through the same submit_action the
client uses, then checks the card is on the board.

The case that failed was Undo on the end-of-turn discard prompt, and in the same
way on Tarpon's discard-and-draw. Both prompts are asked for by engine loops of
their own, and neither honoured the rewind: the server put the cards back, the
loop went on asking for discards from the old hand and then ended the turn, so
the player never got to play. It also rewound to the start of the player's
PREVIOUS turn, bots' turns included, when all they had done this turn was draw.
"""
import atexit
import copy
import os
import shutil
import tempfile
import time

_SANDBOX = tempfile.mkdtemp(prefix="cc-undo-play-test-")
atexit.register(shutil.rmtree, _SANDBOX, True)
os.environ["FISH_ROOM_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["FISH_GAMES_HISTORY_DIR"] = os.path.join(_SANDBOX, "games_history")
os.environ["FISH_COMPETITIVE_GAMES_DIR"] = os.path.join(_SANDBOX, "competitive_games")

import multiplayer_server as mp  # noqa: E402

fish = mp.fish
mp.DATASET_PATH = os.path.join(_SANDBOX, "human_game_dataset.jsonl")
mp.GAMES_HISTORY_DIR = os.path.join(_SANDBOX, "games_history")
mp.GAMES_LEADERBOARD_PATH = os.path.join(mp.GAMES_HISTORY_DIR, "leaderboard.json")
mp.COMPETITIVE_GAMES_DIR = os.path.join(_SANDBOX, "competitive_games")

DISCARD_KINDS = {"discard_to_pool", "discard_batch_to_pool"}


def wait_until(pred, timeout=30.0, poll=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll)
    return False


def payload(room, seat):
    with room.cond:
        return room.legal_actions_by_seat.get(seat)


def legal(room, seat):
    return list((payload(room, seat) or {}).get("actions", []))


def my_turn(room, seat):
    with room.cond:
        return room.active_action_seat == seat and bool(
            (room.legal_actions_by_seat.get(seat) or {}).get("actions"))


def hand(room, seat):
    with room.cond:
        return sorted(room._live_gs.players[seat].hand)


def shown_hand(room, seat):
    """The hand the client is sent, which comes from the last snapshot."""
    with room.cond:
        return sorted(e.get("entry_uid") for e in (room.latest_private_hands or {}).get(seat, []))


def board_count(room, seat):
    with room.cond:
        p = room._live_gs.players[seat]
        return len(p.board_oceans) + sum(len(s.all_cards()) for s in p.ocean_slots.values())


def others(room, seat):
    with room.cond:
        return [(p.name, len(p.hand), len(p.board_oceans),
                 sum(len(s.all_cards()) for s in p.ocean_slots.values()), p.score)
                for i, p in enumerate(room._live_gs.players) if i != seat]


def turn_no(room):
    with room.cond:
        return room.last_turn_number


def diag(room, seat, label):
    with room.cond:
        kinds = [a.get("kind") for a in (room.legal_actions_by_seat.get(seat) or {}).get("actions", [])][:6]
        print(f"  ! {label}: phase={room.phase} note={room.status_note!r} "
              f"active={room.active_action_seat} acted={room._turn_acted_seat} "
              f"valid={room.undo_valid} eligible={room.undo_eligible_seat} "
              f"requested={room.undo_requested}/{room.undo_requested_seat} "
              f"pending_seat={room._undo_pending_seat} kinds={kinds}")
        for line in room.log_events[-8:]:
            print(f"      {line}")


def new_room(code, humans=1, bots=1, speed="fast"):
    room = mp.GameRoom(code, "Tester", total_players=humans + bots,
                       human_players=humans, ai_players=bots)
    room.ai_speed = speed
    host = room.host_seat()
    assert host is not None and host.token, "host seat not auto-claimed"
    for i in range(humans - 1):
        assert room.claim_seat(f"Friend{i}", None, None)["ok"]
    for s in room.seats:
        if s.kind == "ai":
            s.difficulty = "easy"
    started = room.start_game(room.host_control_token, host.token, mp.CARD_DB)
    assert started["ok"], started
    # Seats are reshuffled at launch: find every human by token, never by index.
    seats = {s.token: s.index for s in room.seats if s.kind == "human" and s.token}
    return room, host.token, seats[host.token], seats


def submit(room, token, seat, action):
    """Send an action the way the client does: index plus identity, and for a
    play, exactly cost_to_pay cards of payment."""
    before = payload(room, seat)
    body = {"seat_token": token, "action_index": action["index"], "kind": action["kind"],
            "use_star": bool(action.get("use_star")), "request_id": f"t-{time.monotonic_ns()}"}
    for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
        if isinstance(action.get(key), int):
            body[key] = action[key]
    cost = int(action.get("cost_to_pay") or 0)
    if cost:
        body["payment_uids"] = [int(u) for u in (action.get("payment_candidates") or [])[:cost]]
    out = room.submit_action(body)
    assert out.get("ok"), out
    # Wait for the engine to take it: a fresh legal payload, or the turn handed on.
    wait_until(lambda: payload(room, seat) is not before or not my_turn(room, seat), timeout=10)


def deck_draw(room, seat):
    return next((a for a in legal(room, seat)
                 if a.get("kind") == "draw" and int(a.get("draw_from_pool") or 0) == 0), None)


def finish_turn_by_drawing(room, token, seat):
    started = turn_no(room)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if turn_no(room) != started:
            return
        if not my_turn(room, seat):
            time.sleep(0.02)
            continue
        acts = legal(room, seat)
        pick = deck_draw(room, seat) or next(
            (a for a in acts if a.get("kind") in {"end_turn", "discard_to_pool"}), None)
        assert pick is not None, f"nothing to finish the turn with: {[a.get('kind') for a in acts]}"
        submit(room, token, seat, pick)
    diag(room, seat, "turn never ended")
    raise AssertionError("turn never ended")


def affordable_play(room, seat, name=None):
    for a in legal(room, seat):
        if a.get("kind") not in {"play_ocean", "play_to_ocean"} or a.get("use_star"):
            continue
        if name is not None and a.get("face_name") != name:
            continue
        if len(a.get("payment_candidates") or []) >= int(a.get("cost_to_pay") or 0):
            return a
    return None


def play_a_card(room, token, seat, label, name=None):
    """The step the report says was impossible: after Undo, play a card."""
    if not wait_until(lambda: my_turn(room, seat), timeout=20):
        diag(room, seat, label)
        raise AssertionError(f"{label}: it never became the player's turn after Undo")
    with room.cond:
        can_act = room.active_action_seat == seat and room.phase == "running"
    assert can_act, f"{label}: the client would be told it is not their turn"
    kinds = {a.get("kind") for a in legal(room, seat)}
    assert not kinds <= DISCARD_KINDS, (
        f"{label}: after Undo the player is still being asked only to discard: {sorted(kinds)}")
    play = affordable_play(room, seat, name)
    if play is None:
        diag(room, seat, label)
        raise AssertionError(f"{label}: no play offered after Undo: {sorted(kinds)}")
    on_board = board_count(room, seat)
    submit(room, token, seat, play)
    if not wait_until(lambda: board_count(room, seat) > on_board, timeout=10):
        diag(room, seat, label)
        raise AssertionError(f"{label}: {play.get('face_name')} was submitted after Undo but never landed")
    return play


class _Stop(Exception):
    pass


def _scripted_match(human):
    """run_match with a scripted web human at seat 0 and a bot that only draws.
    The human script raises _Stop once it has seen what it came to see."""
    def bot(gs, ms, p):
        acts = fish.legal_actions(gs, ms, p, include_draw=True)
        return next((a for a in acts if a.kind == "draw" and int(a.draw_from_pool) == 0),
                    acts[0] if acts else None)
    try:
        fish.run_match(mp.CARD_DB, ["Human", "Bot"], [human, bot], seed=11,
                       max_turns=12, human_indices={0})
    except _Stop:
        return
    raise AssertionError("the scripted match finished without reaching the check")


def _rewind(gs, ms, saved):
    """What the room's policy does on Undo: put gs/ms back in place."""
    snap_gs, snap_ms = saved
    gs.__dict__.clear()
    gs.__dict__.update(mp.copy_game_state(snap_gs).__dict__)
    ms.__dict__.clear()
    ms.__dict__.update(copy.deepcopy(snap_ms).__dict__)
    return fish.Action(kind="undo")


def test_engine_discard_loop_starts_the_turn_over_on_undo():
    """The engine half of the bug, with no room and no threads: a policy that
    answers the end-of-turn trim with Action(undo) must get the turn back from
    the top, with the rewound hand and every normal move on offer."""
    seen = {"undone": False}

    def human(gs, ms, p):
        acts = fish.legal_actions(gs, ms, p, include_draw=True)
        kinds = {a.kind for a in acts}
        if acts and kinds <= DISCARD_KINDS:
            if seen["undone"]:
                seen["after"] = f"asked to discard again from the abandoned {len(p.hand)}-card hand"
                raise _Stop()
            seen["undone"] = True
            return _rewind(gs, ms, seen["saved"])
        if not p.flags.get("_draws_taken"):
            if seen["undone"]:
                seen["after"] = (sorted(p.hand), kinds, len(gs.players[1].hand))
                raise _Stop()
            seen["saved"] = (mp.copy_game_state(gs), copy.deepcopy(ms))
            seen["hand"], seen["bot_hand"] = sorted(p.hand), len(gs.players[1].hand)
        return next(a for a in acts if a.kind == "draw" and int(a.draw_from_pool) == 0)

    _scripted_match(human)
    after = seen.get("after")
    assert isinstance(after, tuple), f"Undo on the discard prompt was ignored: {after}"
    hand_after, kinds_after, bot_hand_after = after
    assert hand_after == seen["hand"], "the restarted turn does not hold the rewound hand"
    assert bot_hand_after == seen["bot_hand"], "the turn was passed on to the bot instead of restarted"
    assert "draw" in kinds_after and not kinds_after <= DISCARD_KINDS, sorted(kinds_after)
    print("G PASS: the engine starts the turn over when Undo comes from the discard loop")


def test_engine_tarpon_loop_starts_the_turn_over_on_undo():
    """Same for Tarpon's discard-and-draw, after one card has already been
    discarded into it: that card must not be drawn back out of the rewound deck."""
    seen = {"undone": False, "discarded": False}

    def human(gs, ms, p):
        acts = fish.legal_actions(gs, ms, p, include_draw=True)
        if p.flags.get("_tarpon_discard_active"):
            if seen["undone"]:
                seen["after"] = "asked for Tarpon discards again from the abandoned hand"
                raise _Stop()
            if not seen["discarded"]:
                seen["discarded"] = True
                return next(a for a in acts if a.kind == "discard_to_pool")
            seen["undone"] = True
            return _rewind(gs, ms, seen["saved"])
        if acts and {a.kind for a in acts} <= DISCARD_KINDS:
            return next(a for a in acts if a.kind == "discard_to_pool")
        if not p.flags.get("_draws_taken"):
            if seen["undone"]:
                seen["after"] = (sorted(p.hand), len(gs.deck), len(gs.players[1].hand))
                raise _Stop()
            seen["saved"] = (mp.copy_game_state(gs), copy.deepcopy(ms))
            seen["hand"], seen["deck"] = sorted(p.hand), len(gs.deck)
            seen["bot_hand"] = len(gs.players[1].hand)
        elif not seen["discarded"]:
            # The turn's last action opens a Tarpon phase, the way playing a
            # Tarpon does, so the engine's Tarpon loop is what asks next.
            p.flags["_tarpon_discard_active"] = True
        return next(a for a in acts if a.kind == "draw" and int(a.draw_from_pool) == 0)

    _scripted_match(human)
    after = seen.get("after")
    assert seen["discarded"] and isinstance(after, tuple), f"Undo on the Tarpon prompt was ignored: {after}"
    hand_after, deck_after, bot_hand_after = after
    assert bot_hand_after == seen["bot_hand"], "the turn was passed on to the bot instead of restarted"
    assert deck_after == seen["deck"], (
        f"the card discarded into the undone Tarpon was drawn back out of the rewound deck: "
        f"{seen['deck']} -> {deck_after}")
    assert hand_after == seen["hand"], "the restarted turn does not hold the rewound hand"
    print("H PASS: the engine starts the turn over when Undo comes from the Tarpon loop")


def test_undo_during_a_bot_turn_then_play():
    room, token, seat, _ = new_room("UPLAY1", speed="slow")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "no first turn"
        start = hand(room, seat)
        finish_turn_by_drawing(room, token, seat)
        assert wait_until(lambda: room.undo_valid and room.undo_eligible_seat == seat
                          and room.active_action_seat != seat, timeout=20), "undo never armed"
        assert room.submit_undo({"seat_token": token}) == {"ok": True}
        assert wait_until(lambda: hand(room, seat) == start and my_turn(room, seat), timeout=15), \
            "the turn was not replayed"
        play_a_card(room, token, seat, "undo during a bot turn")
        print("A PASS: Undo during a bot's turn, then a card played on the replayed turn")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_undo_before_acting_then_play():
    room, token, seat, _ = new_room("UPLAY2")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "no first turn"
        start = hand(room, seat)
        first = turn_no(room)
        finish_turn_by_drawing(room, token, seat)
        assert wait_until(lambda: my_turn(room, seat) and turn_no(room) > first, timeout=60)
        assert room.submit_undo({"seat_token": token}) == {"ok": True}
        assert wait_until(lambda: hand(room, seat) == start and my_turn(room, seat), timeout=15), \
            "the last completed turn was not replayed"
        play_a_card(room, token, seat, "undo at the start of the next turn")
        print("B PASS: Undo before touching the next turn, then a card played")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_restart_after_a_draw_then_play():
    room, token, seat, _ = new_room("UPLAY3")
    try:
        assert wait_until(lambda: my_turn(room, seat)), "no first turn"
        start = hand(room, seat)
        submit(room, token, seat, deck_draw(room, seat))
        assert wait_until(lambda: len(hand(room, seat)) == len(start) + 1, timeout=10)
        assert room.submit_undo({"seat_token": token}) == {"ok": True}
        assert wait_until(lambda: hand(room, seat) == start and my_turn(room, seat), timeout=15), \
            "the restart did not put the drawn card back"
        this_turn = turn_no(room)
        with room.cond:
            name = room._live_gs.players[seat].name
        play_a_card(room, token, seat, "restart after a draw")

        # The "Last Turn" pill the table sees must report the turn that happened,
        # not the draw that was taken back before it.
        def summary():
            with room.cond:
                return next((s for s in room.turn_summaries
                             if s.get("player") == name and s.get("turn_number") == this_turn), None)
        if my_turn(room, seat) and turn_no(room) == this_turn:
            finish_turn_by_drawing(room, token, seat)
        assert wait_until(lambda: summary() is not None, timeout=20), "the turn never produced a summary"
        actions = summary().get("actions") or []
        assert actions and actions[0].startswith("Played"), (
            f"the Last Turn caption still lists what was undone: {actions}")
        print("C PASS: restart after a draw, then a card played, and the caption shows only the real turn")
    finally:
        room.terminate_game(room.host_control_token, token)


def test_undo_on_the_discard_prompt_restarts_this_turn_then_play():
    room, token, seat, _ = new_room("UPLAY4")
    try:
        for _ in range(8):
            assert wait_until(lambda: my_turn(room, seat), timeout=60), "no turn"
            turn_start, bots_then, t = hand(room, seat), others(room, seat), turn_no(room)
            submit(room, token, seat, deck_draw(room, seat))
            submit(room, token, seat, deck_draw(room, seat))
            if wait_until(lambda: my_turn(room, seat) and set(
                    a.get("kind") for a in legal(room, seat)) <= DISCARD_KINDS, timeout=3):
                break
            assert wait_until(lambda: turn_no(room) != t, timeout=10), "turn did not hand off"
        else:
            raise AssertionError("never ended a turn over the hand limit")
        assert len(hand(room, seat)) == len(turn_start) + 2
        info = room.state_view(token, "localhost")["undo"]
        assert info["can_restart_turn"], (
            f"on the discard prompt Undo must offer to restart this turn: {info}")

        assert room.submit_undo({"seat_token": token}) == {"ok": True}
        if not wait_until(lambda: hand(room, seat) == turn_start and my_turn(room, seat), timeout=15):
            diag(room, seat, "discard prompt")
            raise AssertionError(
                f"Undo on the discard prompt did not restart this turn: hand {len(hand(room, seat))}, "
                f"want this turn's start ({len(turn_start)})")
        assert others(room, seat) == bots_then, (
            "Undo on the discard prompt rewound the bot's turn too, a whole round the player "
            f"never asked to take back: {bots_then} -> {others(room, seat)}")
        assert wait_until(lambda: shown_hand(room, seat) == turn_start, timeout=5), \
            "the client is still being shown the hand from before the Undo"
        play_a_card(room, token, seat, "undo on the discard prompt")
        print("D PASS: Undo on the discard prompt restarts THIS turn, and a card can be played")
    finally:
        room.terminate_game(room.host_control_token, token)


def _give_web_humans_a_tarpon(original):
    tarpon_faces = {uid for uid, c in mp.CARD_DB.items() if c.name == "Tarpon"}

    def rig(gs, ms, idx, end_uid=None, variant=None):
        original(gs, ms, idx, end_uid, variant=variant)
        for p in gs.players:
            if not p.flags.get("_web_human"):
                continue
            entry = next((u for u in gs.deck if u != end_uid
                          and tarpon_faces & set(fish.entry_faces(ms, u))), None)
            victim = next((u for u in p.hand if not fish.entry_is_ocean(ms, gs, u)), None)
            if entry is None or victim is None:
                continue
            gs.deck[gs.deck.index(entry)] = victim
            p.hand[p.hand.index(victim)] = entry
    return rig


def test_undo_on_the_tarpon_prompt_restarts_this_turn_then_play():
    original = fish.rig_tutorial_opening_hand
    fish.rig_tutorial_opening_hand = _give_web_humans_a_tarpon(original)
    try:
        for attempt in range(6):
            room, token, seat, _ = new_room(f"UPLAY5{attempt}")
            try:
                assert wait_until(lambda: my_turn(room, seat)), "no first turn"
                ocean = next((a for a in legal(room, seat) if a.get("kind") == "play_ocean"
                              and len(a.get("payment_candidates") or []) >= int(a.get("cost_to_pay") or 0)), None)
                if ocean is None:
                    continue
                first = turn_no(room)
                submit(room, token, seat, ocean)
                if my_turn(room, seat) and turn_no(room) == first:
                    finish_turn_by_drawing(room, token, seat)
                assert wait_until(lambda: my_turn(room, seat) and turn_no(room) > first, timeout=60)
                tarpon = affordable_play(room, seat, "Tarpon")
                if tarpon is None:
                    continue
                turn_start, board_then = hand(room, seat), board_count(room, seat)
                with room.cond:
                    deck_then = len(room._live_gs.deck)
                submit(room, token, seat, tarpon)
                if not wait_until(lambda: my_turn(room, seat)
                                  and (payload(room, seat) or {}).get("tarpon_discard_active"), timeout=10):
                    diag(room, seat, "no Tarpon prompt")
                    raise AssertionError("playing Tarpon did not open its discard-and-draw prompt")
                # Discard one card inside the prompt first: the old loop counted it
                # and would have drawn it back from the rewound deck.
                one = next(a for a in legal(room, seat) if a.get("kind") == "discard_to_pool")
                submit(room, token, seat, one)
                assert wait_until(lambda: (payload(room, seat) or {}).get("tarpon_discard_active"), timeout=10)

                assert room.submit_undo({"seat_token": token}) == {"ok": True}
                if not wait_until(lambda: hand(room, seat) == turn_start and my_turn(room, seat)
                                  and not (payload(room, seat) or {}).get("tarpon_discard_active"), timeout=15):
                    diag(room, seat, "Tarpon prompt")
                    raise AssertionError("Undo on the Tarpon prompt did not restart this turn")
                assert board_count(room, seat) == board_then, "the Tarpon stayed on the board after Undo"
                with room.cond:
                    deck_now = len(room._live_gs.deck)
                assert deck_now == deck_then, (
                    f"the card discarded into the undone Tarpon was drawn back out of the rewound "
                    f"deck: {deck_then} -> {deck_now}")
                play_a_card(room, token, seat, "undo on the Tarpon prompt", name="Tarpon")
                print("E PASS: Undo on the Tarpon prompt restarts THIS turn, and Tarpon can be played again")
                return
            finally:
                room.terminate_game(room.host_control_token, token)
        raise AssertionError("could not set up a playable Tarpon in 6 deals")
    finally:
        fish.rig_tutorial_opening_hand = original


def test_undo_while_the_other_human_waits_then_play():
    room, host_token, _, seats = new_room("UPLAY6", humans=2, bots=0)
    try:
        assert wait_until(lambda: room.active_action_seat is not None, timeout=30)
        with room.cond:
            me = room.active_action_seat
        token = next(t for t, s in seats.items() if s == me)
        friend = next(s for s in seats.values() if s != me)
        assert wait_until(lambda: my_turn(room, me))
        start = hand(room, me)
        finish_turn_by_drawing(room, token, me)
        assert wait_until(lambda: my_turn(room, friend), timeout=20)
        assert room.submit_undo({"seat_token": token}) == {"ok": True}
        assert wait_until(lambda: hand(room, me) == start and my_turn(room, me), timeout=15), \
            "the turn was not replayed"
        play_a_card(room, token, me, "undo while the other human waits")
        print("F PASS: Undo while the next human is waiting, then a card played")
    finally:
        room.terminate_game(room.host_control_token, host_token)


if __name__ == "__main__":
    test_engine_discard_loop_starts_the_turn_over_on_undo()
    test_engine_tarpon_loop_starts_the_turn_over_on_undo()
    test_undo_during_a_bot_turn_then_play()
    test_undo_before_acting_then_play()
    test_restart_after_a_draw_then_play()
    test_undo_on_the_discard_prompt_restarts_this_turn_then_play()
    test_undo_on_the_tarpon_prompt_restarts_this_turn_then_play()
    test_undo_while_the_other_human_waits_then_play()
    print("\nALL UNDO-THEN-PLAY TESTS PASSED ✓")
    os._exit(0)
