#!/usr/bin/env python3
"""Hand one player's clan a Clan Point bonus, and pay that player the matching XP.

    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/serviceAccountKey.json
    python3 scripts/grant_clan_points.py --player Clutchy --points 150
    python3 scripts/grant_clan_points.py --player Clutchy --points 150 --dry-run

Why this is not just a call to clan_server._apply_award
------------------------------------------------------
_apply_award is the engine for points a player EARNED, and it enforces two
rules that are correct for earned points and wrong for a hand-out:

  • WEEKLY_POINT_CAP (150/member/week) would eat the whole grant, or eat the
    player's real games for the rest of the week, a 150-point gift is exactly
    the cap, so every point they went on to earn this week would vanish.
  • it feeds gameplay_points, the weekly counters and the challenge sweep, so
    a gift would silently tick off "Rising Tide"/"Powerful Current" and pay
    out challenge rewards nobody played for.

So an admin bonus lands in its own bucket: season `bonus_points`, plus the
clan's season and lifetime totals, plus the contributor's row so the roster
shows who it was for. Nothing a challenge reads is touched.

The XP side
-----------
"XP worth N Clan Points" has one honest answer: every one of the 51 clan
challenges pays member_xp == clan_points * 5, with no exceptions. That ratio
is CLAN_POINT_XP below, and --xp overrides it when you want a different number.
XP is written the same way _admin_set_xp and clan_server._grant_challenge_xp
write it: total_xp plus all six derived level fields, because the header, the
profile and the XP leaderboard read the derived fields directly, and moving
total_xp alone leaves the account showing its old level everywhere.
"""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clan_server as cs                      # noqa: E402
import multiplayer_server as ms               # noqa: E402

# member_xp / clan_points across every challenge in CLAN_WEEKLY_CHALLENGES and
# CLAN_SEASON_CHALLENGES. Asserted below so a retune of the tables cannot leave
# this script quietly paying the old rate.
CLAN_POINT_XP = 5


def _ratio_still_holds() -> bool:
    pairs = [(ch.get("clan_points"), ch.get("member_xp"))
             for ch in (cs.CLAN_WEEKLY_CHALLENGES + cs.CLAN_SEASON_CHALLENGES)]
    return all(p and float(x) == float(p) * CLAN_POINT_XP for p, x in pairs)


def resolve_player(db, who: str):
    """(snapshot, doc) for ONE account by uid, nickname, username or email.
    Never guesses between two matching names: pass the uid for those."""
    who = str(who or "").strip()
    if not who:
        print("--player is required"); sys.exit(1)
    users = db.collection("users")
    snap = users.document(who).get()
    if snap.exists:
        return snap, (snap.to_dict() or {})
    for field, value in (("nickname_lower", who.lower()), ("nickname", who),
                         ("usernameLower", who.lower()), ("username", who),
                         ("email", who), ("authEmail", who)):
        matches = list(users.where(field, "==", value).limit(2).stream())
        if len(matches) > 1:
            print(f"{who!r} matches more than one account on {field}: pass the uid instead.")
            sys.exit(1)
        if matches:
            return matches[0], (matches[0].to_dict() or {})
    print(f"No account found for {who!r}.")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", required=True, help="uid, username or email")
    ap.add_argument("--points", type=float, required=True, help="Clan Points to add")
    ap.add_argument("--xp", type=int, default=None,
                    help=f"XP for the player (default: points x {CLAN_POINT_XP})")
    ap.add_argument("--note", default="", help="shown in the clan activity feed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    points = cs._num(args.points)
    if points <= 0:
        print("--points must be positive"); sys.exit(1)
    if args.xp is None and not _ratio_still_holds():
        print("The challenge tables no longer pay member_xp == clan_points * "
              f"{CLAN_POINT_XP}. Re-read them and pass --xp explicitly.")
        sys.exit(1)
    xp_add = int(args.xp if args.xp is not None else round(float(points) * CLAN_POINT_XP))
    if xp_add < 0:
        print("--xp cannot be negative"); sys.exit(1)

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.ApplicationDefault())
        db = firestore.client()
    except Exception as exc:  # noqa: BLE001
        print("Could not connect to Firebase:", exc)
        print("Set GOOGLE_APPLICATION_CREDENTIALS and run again.")
        sys.exit(1)

    snap, udoc = resolve_player(db, args.player)
    uid = snap.id
    name = str(udoc.get("nickname") or udoc.get("username") or "Player")
    clan_id = str(udoc.get("clan_id") or "")
    if not clan_id:
        print(f"{name} ({uid}) is not in a clan.")
        sys.exit(1)

    clan_ref = cs._clans(db).document(clan_id)
    clan_snap = clan_ref.get()
    if not clan_snap.exists:
        print(f"{name} points at clan {clan_id}, which does not exist.")
        sys.exit(1)
    if uid not in ((clan_snap.to_dict() or {}).get("members") or {}):
        print(f"{name} ({uid}) is not on clan {clan_id}'s member list.")
        sys.exit(1)

    sid = ms.get_season_id()
    dedup_id = f"admin_bonus_{cs._now()}_{secrets.token_hex(4)}"
    stats = udoc.get("stats") if isinstance(udoc.get("stats"), dict) else {}
    try:
        had_xp = int(stats.get("total_xp") or 0)
    except (TypeError, ValueError):
        had_xp = 0
    new_xp = had_xp + xp_add
    lvl, xp_cur, xp_goal = ms._level_progress_for_total_xp(new_xp)

    # ── Read-only preview of both sides before anything is written ──────────
    clan = clan_snap.to_dict() or {}
    slot_before = (clan.get("seasons") or {}).get(sid) or {}
    print(f"player     : {name}  ({uid})")
    print(f"clan       : {clan.get('name')}  ({clan_id})")
    print(f"season     : {sid}")
    print(f"clan points: {cs._num(slot_before.get('points'))} -> "
          f"{cs._num(cs._num(slot_before.get('points')) + points)}  (+{points})")
    print(f"lifetime   : {cs._num((clan.get('lifetime') or {}).get('points'))} -> "
          f"{cs._num(cs._num((clan.get('lifetime') or {}).get('points')) + points)}")
    print(f"player XP  : {had_xp} -> {new_xp}  (+{xp_add}), level "
          f"{stats.get('level', stats.get('player_level'))} -> {lvl}")
    if args.dry_run:
        print("\nDRY RUN, nothing written.")
        return

    # ── Clan side: one transaction, whole-doc set, exactly like _apply_award ──
    transactional = cs._txn_helpers()
    ledger_ref = clan_ref.collection("ledger").document(dedup_id)

    @transactional
    def _run(t):
        if ledger_ref.get(transaction=t).exists:
            return {"ok": False, "error": "already_claimed"}
        s = clan_ref.get(transaction=t)
        if not s.exists:
            return {"ok": False, "error": "no_clan"}
        c = s.to_dict() or {}
        if uid not in (c.get("members") or {}):
            return {"ok": False, "error": "not_member"}

        slot = cs._season_slot(c, sid)
        contrib = cs._contrib_slot(slot, uid, name)
        # Season + lifetime totals move (this IS clan points), and the grant is
        # parked in its own counter so it is always separable from earned play.
        slot["points"] = cs._num(cs._num(slot.get("points")) + points)
        slot["bonus_points"] = cs._num(cs._num(slot.get("bonus_points")) + points)
        slot["last_gain_ts"] = cs._now()
        contrib["points"] = cs._num(cs._num(contrib.get("points")) + points)
        contrib["bonus_points"] = cs._num(cs._num(contrib.get("bonus_points")) + points)
        life = c.setdefault("lifetime", {})
        life["points"] = cs._num(cs._num(life.get("points")) + points)
        # Deliberately NOT touched: weekly[...] (the cap + weekly challenges),
        # daily[...] (the shared goal), gameplay_points/comp_points/casual_points
        # (the "earned by playing" challenges). A gift proves none of those.
        cs._activity_push(c, "bonus",
                          f"🎁 +{points} Clan Points for {name}"
                          + (f": {args.note}" if args.note else ""))
        t.set(ledger_ref, {
            "ts": cs._now(), "uid": uid, "name": name, "kind": "admin_bonus",
            "points": points, "requested": points, "week": cs._week_key(),
            "date": cs._date_key(), "season": sid,
            "meta": {"tool": "scripts/grant_clan_points.py", "note": args.note,
                     "member_xp": xp_add},
        })
        t.set(clan_ref, c)
        return {"ok": True, "season_points": slot["points"]}

    try:
        out = _run(db.transaction())
    except Exception as exc:  # noqa: BLE001
        print("Clan point grant FAILED (nothing written):", exc)
        sys.exit(1)
    if not out.get("ok"):
        print("Clan point grant refused:", out.get("error"))
        sys.exit(1)
    print(f"\n✓ clan {clan.get('name')!r} season points now {out['season_points']}")

    # ── Player side: total_xp plus every field derived from it ──────────────
    db.collection("users").document(uid).set({"stats": {
        "total_xp": new_xp, "level": lvl, "player_level": lvl,
        "xp_current": xp_cur, "level_xp_current": xp_cur,
        "xp_goal": xp_goal, "level_xp_goal": xp_goal,
    }}, merge=True)
    print(f"✓ {name} now {new_xp} XP, level {lvl} ({xp_cur}/{xp_goal})")
    print(f"  ledger: clans/{clan_id}/ledger/{dedup_id}")


if __name__ == "__main__":
    main()
