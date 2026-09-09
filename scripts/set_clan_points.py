#!/usr/bin/env python3
"""Set ONE clan's season Clan Points to an exact number, up or down.

Against the LIVE site (no credentials on this machine, the server has them):

    export ADMIN_RECOVERY_KEY=<the key from Render's env vars>
    python3 scripts/set_clan_points.py --clan "Goby Gang" --points 95 --remote --dry-run
    python3 scripts/set_clan_points.py --clan "Goby Gang" --points 95 --remote \
        --scale-contrib --lifetime --note "season correction"

Straight at Firestore, if this machine really does hold a service account:

    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/serviceAccountKey.json
    python3 scripts/set_clan_points.py --clan "Goby Gang" --points 95

--remote exists because the first run of this tool changed nothing: it needs
GOOGLE_APPLICATION_CREDENTIALS, no laptop here has a service account, and so
the number on the board never moved. The machine that CAN write is the server,
which keeps the credentials in an env var. --remote posts to
/api/admin/clan-set-points, which runs the very same _admin_set_points() in
clan_server.py that the local path below runs, and then reads the total back
through the functions the site itself reads through.

Why this is not scripts/grant_clan_points.py
--------------------------------------------
grant_clan_points.py only ADDS, and it pays the player XP for what they earned.
This is a correction: it moves the clan's season total TO a value, pays nobody,
and can move the number down. Nothing about it is a reward, so no XP is written
and no member's account is touched.

What the number actually is
---------------------------
Every place a clan's point total is shown to anybody reads ONE field,
`seasons/{sid}/points`, through _clan_card():

  • the public marketing homepage prize band (js/clan-prize.js, no login), which
    also prints the gap to second place;
  • the Clans tab: hero total, standings table, browse rows, clan home,
    rival compare, "N Clan Points - your contribution: X".

So setting that one field is what makes the new number register everywhere.
The standings are served from a 20s warm cache with a 1h hard TTL that serves
stale while it refreshes behind the reader; the server drops that cache as part
of the write, and --remote re-reads through it to prove the new value is what
the site now hands out.

What this deliberately does NOT touch
-------------------------------------
`challenge_points`, `challenges_completed`, `challenges_done`, `games`, the win
counters and each member's `contrib` row are the RECORD OF WHAT HAPPENED. A
correction to the headline total is not a claim that those games and challenges
were never played, so by default they stay, and the ledger keeps the delta.

Two of them are also actively dangerous to "tidy up":
  • clearing `challenges_done` un-completes those challenges, and the challenge
    sweep can then award them all over again, pushing points straight back up;
  • scaling `contrib` down moves members across SEASON_REWARD_MIN_POINTS (10)
    and MVP_MIN_POINTS (25), quietly taking season coins and the MVP badge off
    players who earned them.

--scale-contrib exists for when the correction really does mean "these
contributions were not real", and it prints exactly who crosses those two
thresholds before it writes anything.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clan_server as cs                      # noqa: E402

DEFAULT_HOST = "https://play.currentsandcritters.com"

# The one implementation of the correction lives in clan_server, so the server
# endpoint and this script cannot drift apart. Re-exported here because the
# tests (and older invocations) reach for them by these names.
scaled_contrib = cs.scaled_contrib


def resolve_clan(db, who: str):
    """(clan_id, clan_dict), or a printed reason and exit(1)."""
    clan_id, clan, err = cs.resolve_clan(db, who)
    if err.startswith("ambiguous:"):
        print(f"{who!r} matches several clans: {err.split(':', 1)[1]}. Pass the clan id.")
        sys.exit(1)
    if err:
        print(f"No clan found for {who!r}." if err == "no_clan" else f"Lookup failed: {err}")
        sys.exit(1)
    return clan_id, clan


def print_preview(prev: dict, scale: bool) -> None:
    """The same numbers whichever way the correction is being run."""
    print(f"clan        : {prev.get('clan')}")
    print(f"season      : {prev.get('season')}")
    print(f"clan points : {prev['before']} -> {prev['after']}   ({prev['delta']:+})")
    print(f"lifetime    : {prev['lifetime_before']}")
    # The breakdown columns the standings table shows NEXT TO the total. Named
    # here because a total below one of its own components reads as a bug to
    # anyone looking at the row.
    print("\nbreakdown left as history (not scaled):")
    for key, val in (prev.get("kept_as_history") or {}).items():
        flag = ("  <- larger than the new total"
                if key.endswith("points") and val > prev["after"] else "")
        print(f"  {key:<22}{val}{flag}")
    if scale:
        print(f"\ncontributions scaled onto {prev['after']}:")
    else:
        print(f"\ncontributions unchanged (they sum to {prev.get('contrib_sum')}):")
    for m in prev.get("members") or []:
        marks = []
        if m.get("loses_coins"):
            marks.append("LOSES season coins")
        if m.get("loses_mvp"):
            marks.append("LOSES MVP eligibility")
        arrow = f"{m['before']} -> {m['after']}" if scale else f"{m['before']}"
        print(f"  {str(m.get('name')):<20}{arrow:<18}"
              + ("  ⚠ " + ", ".join(marks) if marks else ""))
    if not scale and cs._num(prev.get("contrib_sum")) > prev["after"]:
        print(f"  ⚠ these sum to {prev.get('contrib_sum')}, above the new {prev['after']} "
              "total: a member's \"your contribution\" can read higher than the whole clan.")


def run_remote(args) -> None:
    key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
    if not key:
        print("ADMIN_RECOVERY_KEY is not set. Copy it from Render's env vars:")
        print("  export ADMIN_RECOVERY_KEY=...")
        sys.exit(1)
    host = (args.host or DEFAULT_HOST).rstrip("/")
    payload = {"admin_key": key, "clan": args.clan, "points": args.points,
               "scale_contrib": bool(args.scale_contrib),
               "lifetime": bool(args.lifetime), "note": args.note,
               "dry_run": bool(args.dry_run)}
    req = urllib.request.Request(host + "/api/admin/clan-set-points",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        print(f"{host} answered {exc.code}: {body}")
        if exc.code == 403:
            print("403 means the key did not match ADMIN_RECOVERY_KEY on the server.")
        if exc.code == 404:
            print("404 means this build is not deployed yet: push, wait for the "
                  "deploy, then run this again.")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not reach {host}: {exc}")
        sys.exit(1)

    prev = out.get("preview") or {}
    if prev:
        print_preview(prev, bool(args.scale_contrib))
    if not out.get("ok"):
        print("\nSet refused:", out.get("error"))
        sys.exit(1)
    if out.get("dry_run"):
        print("\nDRY RUN, nothing written.")
        return
    if out.get("noop"):
        print(f"\nAlready at {out.get('after')}, nothing to do.")
        return
    print(f"\n✓ season points {out.get('before')} -> {out.get('after')}")
    print(f"  ledger: {out.get('ledger')}")
    # Read back through the same functions every page reads through: this is
    # the difference between "the write returned ok" and "the site says 95".
    print(f"  clan card now reads      : {out.get('now_reads')}")
    print(f"  leaderboard now reads    : {out.get('leaderboard_reads')} (rank {out.get('rank')})")


def run_local(args) -> None:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.ApplicationDefault())
        db = firestore.client()
    except Exception as exc:  # noqa: BLE001
        print("Could not connect to Firebase:", exc)
        print("Set GOOGLE_APPLICATION_CREDENTIALS, or use --remote with "
              "ADMIN_RECOVERY_KEY to let the server (which has credentials) do it.")
        sys.exit(1)

    clan_id, clan = resolve_clan(db, args.clan)
    sid = cs._clan_sid()
    target = cs._num(args.points)
    prev = cs.set_points_preview(clan, sid, target, bool(args.scale_contrib))
    print_preview(prev, bool(args.scale_contrib))
    if prev["delta"] == 0:
        print("\nAlready at that value, nothing to do.")
        return
    if args.dry_run:
        print("\nDRY RUN, nothing written.")
        return

    out = cs._admin_set_points(db, clan_id, target,
                               scale_contrib=bool(args.scale_contrib),
                               lifetime=bool(args.lifetime), note=args.note, sid=sid)
    if not out.get("ok"):
        print("\nSet FAILED (nothing written):", out.get("error"))
        sys.exit(1)
    print(f"\n✓ {clan.get('name')!r} season points {out['before']} -> {out['after']}")
    print(f"  ledger: {out.get('ledger')}")
    # The running server holds the standings in a warm cache that serves stale
    # while it refreshes behind the reader, and dropping THIS process's cache
    # says nothing about that one, so a laptop write is confirmed over HTTP.
    print("\nNow confirm what the world sees (poll, do not trust one hit):")
    print("  curl -s https://play.currentsandcritters.com/api/clan/leaderboard \\")
    print("    | python3 -c \"import json,sys;print([(r['name'],r['points']) "
          "for r in json.load(sys.stdin)['rows']])\"")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clan", required=True, help="clan id or exact clan name")
    ap.add_argument("--points", type=float, required=True,
                    help="the season total to end up at")
    ap.add_argument("--scale-contrib", action="store_true",
                    help="scale every member's contribution by the same ratio "
                         "(changes who qualifies for season coins and MVP)")
    ap.add_argument("--lifetime", action="store_true",
                    help="apply the same delta to the clan's lifetime total")
    ap.add_argument("--note", default="", help="shown in the clan activity feed")
    ap.add_argument("--remote", action="store_true",
                    help="ask the live server to do it (needs ADMIN_RECOVERY_KEY)")
    ap.add_argument("--host", default=DEFAULT_HOST, help="server for --remote")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if cs._num(args.points) < 0:
        print("--points cannot be negative"); sys.exit(1)
    run_remote(args) if args.remote else run_local(args)


if __name__ == "__main__":
    main()
