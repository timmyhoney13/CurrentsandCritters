"""One read-through cache that nobody ever has to wait behind twice.

Everything slow this server hands a player is the same shape: a Firestore
query that costs seconds from Render and answers the same question for
everybody. The leaderboards, the clan standings, the live player counts and
the avatar-ownership tally were each cached, and every one of those caches
was "expire, then block": the first request after the TTL lapsed ran the
query and paid for it in full, while everyone else got a hit.

That reads as "the tabs take forever", because with a 20-60 second TTL and a
board per tab, the player opening a tab almost always IS that first request.
Measured live: 4.4s to open Leaderboard cold, 0.3s warm; 2.4s for /api/stats
cold, 0.14s warm.

A WarmCache never charges anybody for a refresh once it has an answer:

  * fresh (younger than `ttl`)      the cached value, no I/O at all
  * stale (younger than `hard_ttl`) the cached value RIGHT NOW, and ONE
                                    background thread refreshes it
  * nothing cached, or older than
    `hard_ttl`                      the caller waits, but concurrent callers
                                    share that single fetch instead of each
                                    starting their own

so only the very first request for a key ever waits, and `hard_ttl` is set
per cache to say how out-of-date an answer may be before showing it would be
worse than making somebody wait for it.

`fetch()` returns None to mean "I could not answer": the previous value is
kept and served, exactly like the hand-rolled caches this replaces, and a
key whose fetch is failing is not retried more than once every
`retry_sec` so a Firestore outage cannot turn every page load into a
multi-second timeout.

`sweep()` refreshes the keys somebody has actually asked for recently, so a
board in use is refreshed before anyone meets it stale, and a board nobody
looks at costs nothing to keep. One shared daemon thread sweeps every cache;
start it with `start_sweeper()`.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Every cache built, so one sweeper thread can keep all of them warm.
_CACHES: List["WarmCache"] = []
_CACHES_LOCK = threading.Lock()

SWEEP_INTERVAL_SEC = 15.0


class WarmCache:
    """A stale-while-revalidate cache with single-flight refreshes.

    `ttl`        how long a value is served with no refresh at all.
    `hard_ttl`   how old a value may be and still be served instantly while a
                 refresh runs behind it. Past this the caller waits, so set it
                 by how wrong a stale answer would be: hours for a
                 leaderboard, minutes for a live player count.
    `keep_warm_window`
                 a key nobody has asked for in this long stops being swept.
    `retry_sec`  how long to leave a failing key alone before blocking on it
                 again.
    """

    def __init__(self, name: str, ttl: float, hard_ttl: Optional[float] = None,
                 keep_warm_window: float = 300.0, retry_sec: float = 10.0) -> None:
        self.name = str(name)
        self.ttl = float(ttl)
        self.hard_ttl = float(hard_ttl if hard_ttl is not None else ttl * 15)
        self.keep_warm_window = float(keep_warm_window)
        self.retry_sec = float(retry_sec)
        self._lock = threading.Lock()                 # guards _entries
        self._entries: Dict[str, Dict[str, Any]] = {}
        with _CACHES_LOCK:
            _CACHES.append(self)

    # ── entries ──────────────────────────────────────────────────────────────
    # {"value", "at" (when value was stored), "used" (last asked for),
    #  "failed_at" (last fetch that answered nothing), "refreshing",
    #  "fetch", "lock"}
    def _entry(self, key: str) -> Dict[str, Any]:
        e = self._entries.get(key)
        if e is None:
            e = {"value": None, "at": 0.0, "used": 0.0, "failed_at": 0.0,
                 "refreshing": False, "fetch": None, "lock": threading.Lock()}
            self._entries[key] = e
        return e

    # ── the one call sites make ──────────────────────────────────────────────
    def get(self, key: str, fetch: Callable[[], Any], *, fresh: bool = False) -> Any:
        """The value for `key`, refreshing behind the caller wherever it can.

        `fresh=True` forces a real fetch and waits for it; it is for the paths
        that must observe their own writes (paying out a season), never for
        anything a player is watching load.
        """
        key = str(key)
        now = time.time()
        with self._lock:
            e = self._entry(key)
            e["used"] = now
            e["fetch"] = fetch                        # so sweep() can redo it
            value, at, failed_at = e["value"], e["at"], e["failed_at"]
        if fresh:
            return self._fetch_blocking(key, fetch, accept_age=-1.0)
        age = now - at
        if at > 0.0 and age < self.ttl:
            return value                              # fresh: no I/O
        if at > 0.0 and age < self.hard_ttl:
            self._refresh_behind(key, fetch)          # stale: serve it anyway
            return value
        # Nothing usable. Only a source that just FAILED is left alone: a
        # caller holding no value at all gets None rather than joining a queue
        # of multi-second timeouts. An invalidated key has no failure behind
        # it, so it is fetched here and now, which is the whole point of
        # invalidating it.
        if at <= 0.0 and failed_at > 0.0 and now - failed_at < self.retry_sec:
            return value
        return self._fetch_blocking(key, fetch, accept_age=self.ttl)

    def peek(self, key: str) -> Tuple[Any, float]:
        """(value, age in seconds) without fetching. Age is inf if unset."""
        with self._lock:
            e = self._entries.get(str(key))
            if not e or e["at"] <= 0.0:
                return None, float("inf")
            return e["value"], time.time() - e["at"]

    def stored_at(self, key: str) -> float:
        """When this key's value was last stored, 0.0 if it has none. For
        tests and diagnostics that need to see whether a write dropped the
        cache without waiting on a clock."""
        with self._lock:
            e = self._entries.get(str(key))
            return float(e["at"]) if e else 0.0

    def invalidate(self, key: Optional[str] = None) -> None:
        """Forget a key (or everything), so the next reader fetches for real.

        This drops the VALUE, not just its timestamp: it is called after a
        write that must be visible immediately, and serving the old answer
        while a refresh runs behind would be exactly the bug it prevents.
        """
        with self._lock:
            targets = (list(self._entries.values()) if key is None
                       else [e for e in (self._entries.get(str(key)),) if e])
            for e in targets:
                e["value"], e["at"], e["failed_at"] = None, 0.0, 0.0

    def warm(self, key: str, fetch: Callable[[], Any]) -> Any:
        """Fill a key now, on whatever thread calls this. For boot prewarm.

        Registers the fetch and counts as a use, so a key prewarmed at boot is
        one the sweeper knows how to keep warm; without that a prewarmed board
        went cold again the moment its TTL lapsed and the next player paid for
        it after all."""
        key = str(key)
        with self._lock:
            e = self._entry(key)
            e["fetch"] = fetch
            e["used"] = time.time()
        return self._fetch_blocking(key, fetch, accept_age=self.ttl)

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetch_blocking(self, key: str, fetch: Callable[[], Any],
                        accept_age: float) -> Any:
        """Fetch with the key's own lock held, so callers that arrive together
        share one query instead of each running their own.

        `accept_age` is how old a value another thread may have stored while we
        queued for that lock for us to take it instead of querying again. A
        caller that demanded fresh data passes a negative number, so nothing
        another thread did is ever good enough for it.
        """
        with self._lock:
            lock = self._entry(key)["lock"]
        with lock:
            if accept_age >= 0.0:
                with self._lock:
                    e = self._entry(key)
                    if e["at"] > 0.0 and time.time() - e["at"] <= accept_age:
                        return e["value"]
            ok, value = self._call(fetch)
            return self._store(key, ok, value)

    def _refresh_behind(self, key: str, fetch: Callable[[], Any]) -> None:
        """Start at most one background refresh for this key."""
        with self._lock:
            e = self._entry(key)
            if e["refreshing"]:
                return
            e["refreshing"] = True

        def run() -> None:
            try:
                self._fetch_blocking(key, fetch, accept_age=self.ttl)
            finally:
                with self._lock:
                    self._entry(key)["refreshing"] = False

        threading.Thread(target=run, name=f"warm-{self.name}", daemon=True).start()

    def _call(self, fetch: Callable[[], Any]) -> Tuple[bool, Any]:
        try:
            return True, fetch()
        except Exception as exc:  # noqa: BLE001 - a cache miss must never 500
            print(f"[warm-cache] {self.name}: fetch failed: {exc}")
            return False, None

    def _store(self, key: str, ok: bool, value: Any) -> Any:
        """Keep a real answer; keep the LAST GOOD one when the fetch failed.

        A fetch that returns None is a failure by convention, the same one the
        hand-rolled caches this replaces already used, so an outage degrades to
        "slightly out of date" instead of "the page is empty"."""
        now = time.time()
        with self._lock:
            e = self._entry(key)
            if ok and value is not None:
                e["value"] = value
                e["at"] = now
                e["failed_at"] = 0.0
            else:
                e["failed_at"] = now
            return e["value"]

    # ── keep-warm ────────────────────────────────────────────────────────────
    def sweep(self) -> None:
        """Refresh keys asked for recently that are near or past their TTL, so
        the next player finds them fresh. Runs on the sweeper's own thread, one
        key at a time: a burst of parallel Firestore queries is exactly the
        load this whole file exists to avoid."""
        now = time.time()
        with self._lock:
            due = [(k, e["fetch"]) for k, e in self._entries.items()
                   if e["fetch"] and not e["refreshing"]
                   and now - e["used"] <= self.keep_warm_window
                   and now - e["at"] >= self.ttl * 0.75]
        for key, fetch in due:
            try:
                self._fetch_blocking(key, fetch, accept_age=self.ttl * 0.75)
            except Exception as exc:  # noqa: BLE001
                print(f"[warm-cache] {self.name}: sweep of {key} failed: {exc}")


def sweep_all() -> None:
    with _CACHES_LOCK:
        caches = list(_CACHES)
    for c in caches:
        c.sweep()


_SWEEPER: Optional[threading.Thread] = None
_SWEEPER_LOCK = threading.Lock()


def start_sweeper(interval: float = SWEEP_INTERVAL_SEC) -> None:
    """Start the one daemon thread that keeps every cache warm. Idempotent."""
    global _SWEEPER
    with _SWEEPER_LOCK:
        if _SWEEPER is not None and _SWEEPER.is_alive():
            return

        def loop() -> None:
            while True:
                time.sleep(interval)
                try:
                    sweep_all()
                except Exception as exc:  # noqa: BLE001
                    print(f"[warm-cache] sweep failed: {exc}")

        _SWEEPER = threading.Thread(target=loop, name="warm-cache-sweeper",
                                    daemon=True)
        _SWEEPER.start()
