"""Unit tests for the SERVER-SIDE trade core (pure functions — no Firestore).

Covers offer sanitising, per-side validation, and the atomic swap computation
(_trade_compute_apply): ownership, coins, duplicate protection, equipped-item
reset, and every mixed combination of avatars / backgrounds / coins.

Run:  python3 test_trade_logic.py
"""
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "mpsrv", os.path.join(os.path.dirname(__file__), "multiplayer_server.py"))


def _load():
    # Import just the module's top-level defs. It has heavy imports; if any are
    # missing we still want the pure helpers, so import lazily and tolerate a
    # partial failure by re-reading only the functions we need.
    mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = mod   # dataclasses need the module in sys.modules
    _spec.loader.exec_module(mod)
    return mod


M = _load()

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {name}")


def user(avatars=(), backgrounds=(), coins=0, avatar_url="", background_url=""):
    return {
        "unlocked_icons": list(avatars),
        "unlocked_backgrounds": list(backgrounds),
        "stats": {"critter_coins": coins},
        "avatar_url": avatar_url,
        "background_url": background_url,
    }


def trade(a, b, offer_a, offer_b, version=2, status="open"):
    return {
        "tradeId": f"{a}__{b}",
        "conv_id": f"{a}__{b}",
        "participants": sorted([a, b]),
        "names": {a: "A", b: "B"},
        "offers": {a: offer_a, b: offer_b},
        "confirmed": {a: True, b: True},
        "version": version,
        "status": status,
    }


def offer(coins=0, avatars=(), backgrounds=()):
    return {"coins": coins, "avatars": list(avatars), "backgrounds": list(backgrounds)}


print("clean_offer:")
o = M._trade_clean_offer({"coins": "1000", "avatars": ["/avatars/x.png", "/avatars/x.png", "bad", 3],
                          "backgrounds": ["/backgrounds/y.png"]})
check("coerces coins to int", o["coins"] == 1000)
check("dedupes + filters avatars", o["avatars"] == ["/avatars/x.png"])
check("filters backgrounds by prefix", o["backgrounds"] == ["/backgrounds/y.png"])
check("negative coins clamp to 0", M._trade_clean_offer({"coins": -50})["coins"] == 0)
check("caps items per side",
      len(M._trade_clean_offer({"avatars": [f"/avatars/{i}.png" for i in range(50)]})["avatars"])
      == M.TRADE_MAX_ITEMS_PER_SIDE)
check("strips query strings", M._trade_clean_offer({"avatars": ["/avatars/x.png?v=2"]})["avatars"] == ["/avatars/x.png"])

print("validate_side:")
giver = M._trade_assets(user(avatars=["/avatars/a.png"], coins=500))
recv = M._trade_assets(user())
check("owns + affordable → ok",
      M._trade_validate_side(offer(coins=500, avatars=["/avatars/a.png"]), giver, recv) == "")
check("not enough coins",
      M._trade_validate_side(offer(coins=501), giver, recv) == "not_enough_coins")
check("avatar not owned",
      M._trade_validate_side(offer(avatars=["/avatars/z.png"]), giver, recv) == "avatar_not_owned")
recv_owns = M._trade_assets(user(avatars=["/avatars/a.png"]))
check("recipient already owns avatar → duplicate",
      M._trade_validate_side(offer(avatars=["/avatars/a.png"]), giver, recv_owns) == "duplicate_avatar")

print("compute_apply — happy path (example from spec):")
# P1 gives 1000 coins + Sardine avatar; P2 gives Lobster avatar.
A, B = "uidA", "uidB"
docA = user(avatars=["/avatars/sardine.png"], coins=1500, avatar_url="/avatars/sardine.png")
docB = user(avatars=["/avatars/lobster.png"], coins=0)
t = trade(A, B, offer(coins=1000, avatars=["/avatars/sardine.png"]),
          offer(avatars=["/avatars/lobster.png"]))
# participants are sorted; map offers by uid regardless of order
t["offers"] = {A: offer(coins=1000, avatars=["/avatars/sardine.png"]),
               B: offer(avatars=["/avatars/lobster.png"])}
err, ch = M._trade_compute_apply(t, docA if t["participants"][0] == A else docB,
                                 docB if t["participants"][1] == B else docA)
check("no error", err == "")
check("A loses sardine, gains lobster",
      ch[A]["unlocked_icons"] == ["/avatars/lobster.png"])
check("A coins 1500-1000=500", ch[A]["critter_coins"] == 500)
check("B gains sardine", ch[B]["unlocked_icons"] == ["/avatars/sardine.png"])
check("B coins 0+1000=1000", ch[B]["critter_coins"] == 1000)
check("A equipped avatar reset (traded away sardine)", ch[A].get("avatar_url") == "/avatars/mullet.png")

print("compute_apply — commit-time ownership loss:")
docA2 = user(avatars=[], coins=0)   # A no longer owns what they offered
t2 = trade(A, B, None, None)
t2["offers"] = {A: offer(avatars=["/avatars/sardine.png"]), B: offer()}
err2, ch2 = M._trade_compute_apply(t2,
                                   docA2 if t2["participants"][0] == A else user(),
                                   user() if t2["participants"][1] == B else docA2)
check("aborts when giver lost the item", err2 == "avatar_not_owned" and ch2 is None)

print("compute_apply — insufficient coins at commit:")
t3 = trade(A, B, None, None)
t3["offers"] = {A: offer(coins=1000), B: offer()}
dA = user(coins=999)
err3, ch3 = M._trade_compute_apply(t3,
                                   dA if t3["participants"][0] == A else user(),
                                   user() if t3["participants"][1] == B else dA)
check("aborts when giver can't afford", err3 == "not_enough_coins" and ch3 is None)

print("compute_apply — background + coins both ways:")
dA4 = user(backgrounds=["/backgrounds/bg-kelp.png"], coins=2000, background_url="/backgrounds/bg-kelp.png")
dB4 = user(backgrounds=["/backgrounds/bg-deep.png"], coins=300)
t4 = trade(A, B, None, None)
t4["offers"] = {A: offer(coins=500, backgrounds=["/backgrounds/bg-kelp.png"]),
                B: offer(coins=200, backgrounds=["/backgrounds/bg-deep.png"])}
err4, ch4 = M._trade_compute_apply(t4,
                                   dA4 if t4["participants"][0] == A else dB4,
                                   dB4 if t4["participants"][1] == B else dA4)
check("no error (two-way bg+coins)", err4 == "")
check("A bg now deep", ch4[A]["unlocked_backgrounds"] == ["/backgrounds/bg-deep.png"])
check("A coins 2000-500+200=1700", ch4[A]["critter_coins"] == 1700)
check("B bg now kelp", ch4[B]["unlocked_backgrounds"] == ["/backgrounds/bg-kelp.png"])
check("B coins 300-200+500=600", ch4[B]["critter_coins"] == 600)
check("A equipped bg cleared", ch4[A].get("background_url") == "")

print("compute_apply — empty trade (both give nothing) is a valid no-op:")
t5 = trade(A, B, None, None)
t5["offers"] = {A: offer(), B: offer()}
err5, ch5 = M._trade_compute_apply(t5, user(coins=5), user(coins=9))
check("empty offers → ok", err5 == "")
check("coins unchanged", ch5[A]["critter_coins"] == 5 and ch5[B]["critter_coins"] == 9)


# ── Re-earn snapshots (traded_away) ────────────────────────────────────────
# Giving an item away must record the giver's progress AT THAT MOMENT, so the
# client can require the unlock requirement to be met all over again instead of
# handing the item straight back off banked lifetime progress.
def user_p(avatars=(), backgrounds=(), coins=0, stats=None, traded_away=None,
           avatar_url="", background_url=""):
    d = user(avatars, backgrounds, coins, avatar_url, background_url)
    d["stats"] = {"critter_coins": coins, **(stats or {})}
    if traded_away is not None:
        d["traded_away"] = traded_away
    return d


def entry_for(changes_for_uid, item):
    for e in changes_for_uid["traded_away"]:
        if e.get("item") == item:
            return e
    return None


print("progress_snapshot:")
snap = M._trade_progress_snapshot(user_p(stats={
    "lifetime_play_again": 91, "total_xp": M.LEVEL_XP_TOTALS[30], "rank_competitive": "Bronze Barracuda",
    "level_title": "Ocean Explorer", "normal_games_by_size": {"4": 3}, "in_beta": True}))
check("keeps numeric stats", snap["stats"]["lifetime_play_again"] == 91)
check("drops non-numeric stats", "level_title" not in snap["stats"]
      and "normal_games_by_size" not in snap["stats"])
check("drops booleans (bools are ints in python)", "in_beta" not in snap["stats"])
# Anchored to the curve's own level-31 boundary, not a literal, so retuning
# LEVEL_XP_TOTALS can never silently turn this into a different level.
check("derives level from total_xp",
      snap["level"] == 31 and snap["total_xp"] == M.LEVEL_XP_TOTALS[30])
check("records rank", snap["rank"] == "Bronze Barracuda")
check("missing stats → empty snapshot", M._trade_progress_snapshot({})["stats"] == {})

print("compute_apply — trading an avatar away snapshots progress:")
PUFFIN, LOB = "/avatars/horned-puffin.png", "/avatars/lobster.png"
dA6 = user_p(avatars=[PUFFIN], stats={"lifetime_play_again": 91, "total_xp": 1000})
dB6 = user_p(avatars=[LOB], stats={"lifetime_play_again": 4})
t6 = trade(A, B, None, None)
t6["offers"] = {A: offer(avatars=[PUFFIN]), B: offer(avatars=[LOB])}
err6, ch6 = M._trade_compute_apply(t6,
                                   dA6 if t6["participants"][0] == A else dB6,
                                   dB6 if t6["participants"][1] == B else dA6)
check("no error", err6 == "")
e6 = entry_for(ch6[A], PUFFIN)
check("giver gets a snapshot for the item given", e6 is not None)
check("snapshot holds the giver's count at trade time",
      e6 and e6["stats"]["lifetime_play_again"] == 91)
check("receiver records nothing for what they received",
      entry_for(ch6[B], PUFFIN) is None)
check("receiver DOES record what they gave", entry_for(ch6[B], LOB) is not None)

print("compute_apply — receiving an item back clears its debt:")
dA7 = user_p(avatars=[], traded_away=[{"item": PUFFIN, "stats": {"lifetime_play_again": 91}},
                                      {"item": LOB, "stats": {"lifetime_play_again": 12}}])
dB7 = user_p(avatars=[PUFFIN])
t7 = trade(A, B, None, None)
t7["offers"] = {A: offer(), B: offer(avatars=[PUFFIN])}
err7, ch7 = M._trade_compute_apply(t7,
                                   dA7 if t7["participants"][0] == A else dB7,
                                   dB7 if t7["participants"][1] == B else dA7)
check("no error", err7 == "")
check("entry dropped for the item received back", entry_for(ch7[A], PUFFIN) is None)
check("unrelated entries preserved", entry_for(ch7[A], LOB) is not None)

print("compute_apply — re-giving the same item re-snapshots (no stale baseline):")
dA8 = user_p(avatars=[PUFFIN], stats={"lifetime_play_again": 200},
             traded_away=[{"item": PUFFIN, "stats": {"lifetime_play_again": 91}}])
t8 = trade(A, B, None, None)
t8["offers"] = {A: offer(avatars=[PUFFIN]), B: offer()}
err8, ch8 = M._trade_compute_apply(t8,
                                   dA8 if t8["participants"][0] == A else user_p(),
                                   user_p() if t8["participants"][1] == B else dA8)
check("no error", err8 == "")
check("exactly one entry for the item",
      sum(1 for e in ch8[A]["traded_away"] if e.get("item") == PUFFIN) == 1)
check("baseline is the CURRENT count, not the old one",
      entry_for(ch8[A], PUFFIN)["stats"]["lifetime_play_again"] == 200)

print("compute_apply — backgrounds are snapshotted too:")
KELP = "/backgrounds/bg-kelp.png"
dA9 = user_p(backgrounds=[KELP], stats={"total_xp": 500})
t9 = trade(A, B, None, None)
t9["offers"] = {A: offer(backgrounds=[KELP]), B: offer()}
err9, ch9 = M._trade_compute_apply(t9,
                                   dA9 if t9["participants"][0] == A else user_p(),
                                   user_p() if t9["participants"][1] == B else dA9)
check("no error", err9 == "")
check("background recorded", entry_for(ch9[A], KELP) is not None)
check("a trade that gives nothing writes no entries", ch9[B]["traded_away"] == [])

print("compute_apply — junk in an existing traded_away list is dropped safely:")
dA10 = user_p(avatars=[PUFFIN], traded_away=["nope", None, {"no_item": 1}, 7])
t10 = trade(A, B, None, None)
t10["offers"] = {A: offer(avatars=[PUFFIN]), B: offer()}
err10, ch10 = M._trade_compute_apply(t10,
                                     dA10 if t10["participants"][0] == A else user_p(),
                                     user_p() if t10["participants"][1] == B else dA10)
check("no error", err10 == "")
check("only the valid new entry survives",
      len(ch10[A]["traded_away"]) == 1 and ch10[A]["traded_away"][0]["item"] == PUFFIN)

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
