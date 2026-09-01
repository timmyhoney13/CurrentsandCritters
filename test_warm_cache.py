#!/usr/bin/env python3
"""What warm_cache.WarmCache promises, and the two bugs it already caught.

Run:  python3 test_warm_cache.py

Every slow read on the game server now goes through this one class, so the
promise it makes is load-bearing: nobody waits for a refresh once an answer
exists, an outage degrades to "slightly out of date" instead of a blank page,
and a write that invalidates a key really is visible to the very next reader.

The two bugs this file exists to keep fixed:
  * invalidate() left the "don't hammer a failing source" timer standing, so
    the next reader after a write got None instead of a fresh scan. Every clan
    test that checked "a new clan is on the board a second later" went red.
  * a failed refresh used to stamp the entry as freshly stored, which would
    have served a bad answer for a full TTL and then a good one.
"""
import sys
import threading
import time

import warm_cache

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {label}" + (f"  [{detail}]" if detail else ""))


class Source:
    """A fetch that counts its calls, can be made slow, and can be broken."""

    def __init__(self, value="v1"):
        self.value = value
        self.calls = 0
        self.delay = 0.0
        self.broken = False
        self.raises = False
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise RuntimeError("source exploded")
        return None if self.broken else self.value


print("serving:")
c = warm_cache.WarmCache("t-serve", ttl=0.05, hard_ttl=10.0)
src = Source("first")
check("the very first read fetches", c.get("k", src) == "first" and src.calls == 1)
check("a fresh read costs nothing", c.get("k", src) == "first" and src.calls == 1)

time.sleep(0.06)
src.value = "second"
got = c.get("k", src)
check("a stale read is answered INSTANTLY with what we had", got == "first")
for _ in range(100):                       # let the background refresh land
    if src.calls > 1 and c.get("k", src) == "second":
        break
    time.sleep(0.01)
check("...and the refresh really ran behind it", src.calls == 2)
check("...so the next reader gets the new value", c.get("k", src) == "second")

print("nobody waits twice:")
c = warm_cache.WarmCache("t-slow", ttl=0.05, hard_ttl=10.0)
src = Source("slow-1")
src.delay = 0.30
c.get("k", src)                            # pay for it once
time.sleep(0.06)
t0 = time.time()
c.get("k", src)                            # stale: must NOT wait 0.3s
elapsed = time.time() - t0
check("a stale read does not pay the source's latency", elapsed < 0.10, f"{elapsed:.3f}s")

print("single flight:")
c = warm_cache.WarmCache("t-flight", ttl=5.0, hard_ttl=10.0)
src = Source("once")
src.delay = 0.20
out = []
threads = [threading.Thread(target=lambda: out.append(c.get("k", src))) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("eight cold readers share ONE fetch", src.calls == 1, f"calls={src.calls}")
check("...and every one of them gets the answer", out == ["once"] * 8)

print("hard_ttl:")
c = warm_cache.WarmCache("t-hard", ttl=0.05, hard_ttl=0.10)
src = Source("old")
c.get("k", src)
time.sleep(0.15)
src.value = "new"
check("past hard_ttl the reader waits for the truth", c.get("k", src) == "new")

print("an outage degrades, it does not blank the page:")
c = warm_cache.WarmCache("t-fail", ttl=0.05, hard_ttl=10.0, retry_sec=0.0)
src = Source("good")
c.get("k", src)
src.broken = True
time.sleep(0.06)
check("a stale read still serves the last good answer", c.get("k", src) == "good")
time.sleep(0.10)
check("...and keeps serving it while the source is down", c.get("k", src) == "good")
src.raises = True
time.sleep(0.06)
check("a source that RAISES is caught, not propagated", c.get("k", src) == "good")
src.raises = False
src.broken = False
src.value = "recovered"
time.sleep(0.06)
c.get("k", src)
for _ in range(100):
    if c.get("k", src) == "recovered":
        break
    time.sleep(0.01)
check("...and recovery is picked up", c.get("k", src) == "recovered")

# A key that has NEVER answered must not put every caller into a queue of
# multi-second timeouts: it answers None and is left alone for retry_sec.
c = warm_cache.WarmCache("t-never", ttl=1.0, hard_ttl=2.0, retry_sec=5.0)
src = Source("unused")
src.broken = True
check("a key that never answered returns None", c.get("k", src) is None)
check("...and a failing source is not hammered", c.get("k", src) is None and src.calls == 1,
      f"calls={src.calls}")

print("invalidate:")
c = warm_cache.WarmCache("t-inv", ttl=60.0, hard_ttl=600.0, retry_sec=30.0)
src = Source("before")
c.get("k", src)
src.value = "after"
c.invalidate("k")
check("stored_at() reports the drop", c.stored_at("k") == 0.0)
# The bug: retry_sec used to be armed by the successful fetch above, so this
# read short-circuited to None instead of re-reading. A write that invalidates
# has no failure behind it and must be re-read here and now.
check("the very next read re-fetches, it is not served stale or None",
      c.get("k", src) == "after" and src.calls == 2, f"calls={src.calls}")
src2 = Source("x")
c.get("other", src2)
c.invalidate()
check("invalidate() with no key drops everything",
      c.stored_at("k") == 0.0 and c.stored_at("other") == 0.0)

print("a failed refresh never looks fresh:")
c = warm_cache.WarmCache("t-stamp", ttl=0.05, hard_ttl=10.0, retry_sec=0.0)
src = Source("real")
c.get("k", src)
at_good = c.stored_at("k")
src.broken = True
time.sleep(0.06)
c._fetch_blocking("k", src, accept_age=-1.0)      # a refresh that answers nothing
check("a failed refresh leaves the stored timestamp alone",
      c.stored_at("k") == at_good)
check("...so the entry is still stale and will be retried",
      c.get("k", src) == "real")

print("prewarm and sweep:")
c = warm_cache.WarmCache("t-warm", ttl=0.05, hard_ttl=10.0, keep_warm_window=10.0)
src = Source("w1")
check("warm() fills a key off the request path", c.warm("k", src) == "w1")
src.value = "w2"
time.sleep(0.06)
c.sweep()
check("sweep refreshes a key somebody asked for recently", c.get("k", src) == "w2")

cold = warm_cache.WarmCache("t-cold", ttl=0.05, hard_ttl=10.0, keep_warm_window=0.0)
src_cold = Source("c1")
cold.get("k", src_cold)
time.sleep(0.06)
before = src_cold.calls
cold.sweep()
check("a key nobody has asked for is NOT kept warm", src_cold.calls == before,
      f"{before} -> {src_cold.calls}")

print("fresh=True:")
c = warm_cache.WarmCache("t-fresh", ttl=60.0, hard_ttl=600.0)
src = Source("cached")
c.get("k", src)
src.value = "truth"
check("fresh=True re-reads even a perfectly fresh entry",
      c.get("k", src, fresh=True) == "truth" and src.calls == 2, f"calls={src.calls}")

print("peek:")
c = warm_cache.WarmCache("t-peek", ttl=60.0, hard_ttl=600.0)
val, age = c.peek("missing")
check("peek on an unknown key answers (None, inf)", val is None and age == float("inf"))
src = Source("p")
c.get("k", src)
val, age = c.peek("k")
check("peek does not fetch", val == "p" and age < 1.0 and src.calls == 1)

# ── The prewarm list has to match the boards the client asks for ────────────
# Prewarming is what stops one unlucky visitor per deploy from paying the cold
# ~4.7s read for a board. A board added to Player Home and forgotten here still
# works, so nothing else would ever notice: this is the only thing that does.
print("every board Player Home asks for is prewarmed:")
import importlib.util
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("mpsrv_for_warm_cache", "multiplayer_server.py")
APP = open(os.path.join(ROOT, "multiplayer/client/js/preview-app.js"),
           encoding="utf-8").read()

prewarm = set(M._LB_PREWARM)
check("the prewarm list is not empty", len(prewarm) > 0)
check("every prewarmed field is a whitelisted board",
      {f for f, _ in prewarm} <= M._LB_BOARD_FIELDS,
      str({f for f, _ in prewarm} - M._LB_BOARD_FIELDS))

# The literal calls: lbTopUsersWarm("stats.total_xp", 50)
literal = {(m.group(1), int(m.group(2)))
           for m in re.finditer(r'lbTopUsersWarm\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)', APP)}
check("the client's literal boards were found", len(literal) >= 7, str(len(literal)))
check("...and every one of them is prewarmed", literal <= prewarm,
      str(sorted(literal - prewarm)))

# The two calls that pass a computed field: the streak toggle and the
# per-table-size casual boards. Their fields come from the code beside them.
computed = {("stats.daily_streak", 75), ("stats.streak_longest", 75)}
computed |= {(f"stats.highest_score_{n}p", 25) for n in range(2, 9)}
check("the streak + per-size boards are prewarmed too", computed <= prewarm,
      str(sorted(computed - prewarm)))
check("nothing is prewarmed that no board asks for",
      prewarm <= (literal | computed), str(sorted(prewarm - (literal | computed))))

# Whatever the client asks for, the server must agree to sort by it.
check("every board the client asks for is one the server allows",
      {f for f, _ in (literal | computed)} <= M._LB_BOARD_FIELDS,
      str({f for f, _ in (literal | computed)} - M._LB_BOARD_FIELDS))

print("=" * 46)
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
