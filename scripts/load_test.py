#!/usr/bin/env python3
"""Put N real players on a running game server at once and measure what they feel.

Run a server first (use a throwaway state dir, never the shipped one):

    FISH_ROOM_STATE_DIR=/tmp/cc-load/state FISH_GAMES_HISTORY_DIR=/tmp/cc-load/hist \
      python3 multiplayer_server.py --port 8871

then:

    python3 scripts/load_test.py --base http://127.0.0.1:8871 --players 100 \
      --humans-per-room 2 --bots-per-room 2 --duration 180

Every simulated player behaves like the browser client does in a game: it holds
the room's SSE stream open for the whole match, polls /state as the backstop
(2.5 s), and when it is its turn it "thinks" for a moment and submits a legal
move. Separately, a prober hits /api/health once a second: that route touches no
room at all, so its latency is a direct reading of how starved the process is.

What it reports: latency percentiles per route, error counts (5xx, timeouts,
refused connections), how many moves the humans got through, SSE drops, and the
server's peak memory when --server-pid is given.

Pacing matters. --ai-speed fast with --think-min 0.3 --think-max 1.2 is a
stress test: it plays several times faster than people do, so the server's CPU
reads 100% by design and only the latencies mean anything. The default bot
speed with --think-min 2 --think-max 8 is closer to a real evening.
"""

import argparse
import json
import random
import string
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

LAT = defaultdict(list)
ERR = defaultdict(int)
COUNT = defaultdict(int)
LOCK = threading.Lock()
STOP = threading.Event()
AI_SPEED = ""


def record(route, ms, ok=True):
    with LOCK:
        LAT[route].append(ms)
        if not ok:
            ERR[route] += 1


def bump(key, n=1):
    with LOCK:
        COUNT[key] += n


def call(base, method, path, body=None, route=None, timeout=15):
    route = route or path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            record(route, ms)
            return resp.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {}
        # 400s from /action are game-rule refusals (not your turn, etc.), not
        # server failures; only count 5xx as errors.
        record(route, ms, ok=e.code < 500)
        return e.code, payload
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        record(route, ms, ok=False)
        with LOCK:
            COUNT[f"exc:{route}:{type(e).__name__}"] += 1
        return 0, {}


def sse_reader(base, room_id, seat_token, on_state):
    """Hold the stream open like EventSource does, reconnecting on drops."""
    while not STOP.is_set():
        url = f"{base}/api/rooms/{room_id}/stream?seat_token={seat_token}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                bump("sse_connects")
                data_lines = []
                for raw in resp:
                    if STOP.is_set():
                        return
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("data: "):
                        data_lines.append(line[6:])
                    elif line == "" and data_lines:
                        try:
                            on_state(json.loads("".join(data_lines)))
                            bump("sse_events")
                        except Exception:
                            pass
                        data_lines = []
        except Exception:
            bump("sse_drops")
            time.sleep(1.0)


class Player:
    def __init__(self, base, room_id, seat_token, seat_index, name):
        self.base = base
        self.room_id = room_id
        self.token = seat_token
        self.seat = seat_index
        self.name = name
        self.latest = None
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)

    def on_state(self, payload):
        with self.cond:
            if self.latest is None or int(payload.get("version", 0)) >= int(self.latest.get("version", 0)):
                self.latest = payload
            self.cond.notify_all()

    def poll_loop(self):
        while not STOP.is_set():
            status, payload = call(self.base, "GET",
                                   f"/api/rooms/{self.room_id}/state?seat_token={self.token}",
                                   route="GET state")
            if status == 200 and payload:
                self.on_state(payload)
            STOP.wait(2.5)

    def pick(self, legal):
        actions = list(legal.get("actions") or [])
        if not actions:
            return None
        # Humans mostly play something when they can, otherwise draw.
        plays = [a for a in actions if a.get("kind") not in {"draw"}]
        draws = [a for a in actions if a.get("kind") == "draw"]
        pool = plays if plays and random.random() < 0.7 else (draws or plays)
        a = random.choice(pool)
        body = {
            "seat_token": self.token,
            "action_index": a.get("index"),
            "request_id": "".join(random.choices(string.ascii_letters, k=16)),
        }
        cost = int(a.get("cost_to_pay") or 0)
        cands = list(a.get("payment_candidates") or [])
        if cost > 0:
            body["payment_uids"] = cands[:cost]
        if a.get("kind") == "discard_batch_to_pool":
            hand = [c.get("uid") for c in (self._my_hand() or []) if isinstance(c, dict)]
            excess = int(legal.get("discard_excess") or 1)
            body["pool_pick_uids"] = [u for u in hand if isinstance(u, int)][:max(1, excess)]
        return body

    def _my_hand(self):
        try:
            for p in self.latest["state"]["players"]:
                if p.get("index") == self.seat:
                    return p.get("hand")
        except Exception:
            return None

    def play_loop(self, think_min, think_max):
        last_submitted_version = -1
        last_submit_at = 0.0
        while not STOP.is_set():
            with self.cond:
                self.cond.wait(timeout=1.0)
                payload = self.latest
            if not payload:
                continue
            phase = (payload.get("room") or {}).get("phase")
            if phase in {"ended", "finished", "error"}:
                bump(f"rooms_{phase}")
                return
            # Seats are shuffled when the game starts, so the index handed back
            # by create/join is stale: the viewer block is the truth.
            viewer_seat = (payload.get("viewer") or {}).get("seat_index")
            if isinstance(viewer_seat, int):
                self.seat = viewer_seat
            legal = payload.get("legal_actions")
            my_turn = payload.get("active_action_seat") == self.seat and legal and legal.get("actions")
            if not my_turn:
                continue
            version = int(payload.get("version", 0))
            # A refused move leaves the version where it was, so retry on a
            # clock as well as on a new version, or the table waits forever.
            if version == last_submitted_version and time.perf_counter() - last_submit_at < 3.0:
                continue
            STOP.wait(random.uniform(think_min, think_max))
            body = self.pick(payload["legal_actions"])
            if body is None:
                continue
            status, out = call(self.base, "POST", f"/api/rooms/{self.room_id}/action", body,
                               route="POST action")
            last_submitted_version = version
            last_submit_at = time.perf_counter()
            if status == 200 and out.get("ok"):
                bump("human_moves")
            else:
                bump(f"refused:{str(out.get('error'))[:40]}")


def make_room(base, idx, humans, bots):
    total = humans + bots
    status, out = call(base, "POST", "/api/rooms", {
        "host_name": f"Load{idx}", "total_players": total,
        "human_players": humans, "ai_players": bots,
        "visibility": "private",
    }, route="POST rooms")
    if status != 200 or not out.get("ok"):
        print("create failed", status, out)
        return []
    room_id = out["room_id"]
    host_token = out["host_token"]
    players = [Player(base, room_id, out["seat_token"], out["seat_index"], f"Load{idx}-0")]
    for j in range(1, humans):
        status, joined = call(base, "POST", f"/api/rooms/{room_id}/join",
                              {"player_name": f"Load{idx}-{j}"}, route="POST join")
        if status != 200 or not joined.get("ok"):
            print("join failed", status, joined)
            continue
        players.append(Player(base, room_id, joined["seat_token"], joined["seat_index"], f"Load{idx}-{j}"))
    if AI_SPEED:
        call(base, "POST", f"/api/rooms/{room_id}/ai_speed",
             {"host_token": host_token, "seat_token": out["seat_token"], "speed": AI_SPEED},
             route="POST ai_speed")
    status, started = call(base, "POST", f"/api/rooms/{room_id}/start",
                           {"host_token": host_token, "seat_token": out["seat_token"]},
                           route="POST start")
    if status != 200 or not started.get("ok"):
        print("start failed", room_id, status, started)
    return players


def health_probe(base):
    while not STOP.is_set():
        call(base, "GET", "/api/health", route="GET health", timeout=30)
        STOP.wait(1.0)


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def rss_mb(pid):
    if not pid:
        return None
    try:
        import subprocess
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out) / 1024.0
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8871")
    ap.add_argument("--players", type=int, default=100)
    ap.add_argument("--humans-per-room", type=int, default=4)
    ap.add_argument("--bots-per-room", type=int, default=0)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--think-min", type=float, default=1.0)
    ap.add_argument("--think-max", type=float, default=4.0)
    ap.add_argument("--server-pid", type=int, default=0)
    ap.add_argument("--ai-speed", default="", choices=["", "slow", "normal", "fast"])
    args = ap.parse_args()
    global AI_SPEED
    AI_SPEED = args.ai_speed

    rooms = max(1, args.players // args.humans_per_room)
    print(f"{rooms} rooms × {args.humans_per_room} humans + {args.bots_per_room} bots "
          f"= {rooms * args.humans_per_room} players for {args.duration:.0f}s")

    threading.Thread(target=health_probe, args=(args.base,), daemon=True).start()

    players = []
    t_setup = time.perf_counter()
    for i in range(rooms):
        players.extend(make_room(args.base, i, args.humans_per_room, args.bots_per_room))
    print(f"setup {time.perf_counter() - t_setup:.1f}s, {len(players)} players seated")

    for p in players:
        threading.Thread(target=sse_reader, args=(args.base, p.room_id, p.token, p.on_state), daemon=True).start()
        threading.Thread(target=p.poll_loop, daemon=True).start()
        threading.Thread(target=p.play_loop, args=(args.think_min, args.think_max), daemon=True).start()

    t0 = time.time()
    peak_rss = 0.0
    while time.time() - t0 < args.duration:
        time.sleep(10)
        r = rss_mb(args.server_pid)
        if r:
            peak_rss = max(peak_rss, r)
        with LOCK:
            h = LAT["GET health"][-10:]
            print(f"  t={time.time() - t0:5.0f}s moves={COUNT['human_moves']} "
                  f"health(last10) p50={pct(h, 50):.0f}ms max={max(h) if h else 0:.0f}ms "
                  f"sse={COUNT['sse_connects']} drops={COUNT['sse_drops']}"
                  + (f" rss={r:.0f}MB" if r else ""))
    STOP.set()

    status, stats = call(args.base, "GET", "/api/health", route="final")
    print("\n=== results ===")
    for route in sorted(LAT):
        v = LAT[route]
        print(f"{route:14s} n={len(v):6d} p50={pct(v, 50):7.1f} p95={pct(v, 95):7.1f} "
              f"p99={pct(v, 99):7.1f} max={max(v):8.1f} ms  5xx/exc={ERR[route]}")
    for k in sorted(COUNT):
        print(f"{k}: {COUNT[k]}")
    if peak_rss:
        print(f"peak server RSS: {peak_rss:.0f} MB")
    print("health:", json.dumps(stats)[:400])


if __name__ == "__main__":
    main()
