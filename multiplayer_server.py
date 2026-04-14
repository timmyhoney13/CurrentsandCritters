#!/usr/bin/env python3
"""Fish Game multiplayer room server (HTTP + SSE, server-authoritative)."""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import re
import secrets
import socket
import string
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Force non-interactive payment/discard behavior for human seats in run_match.
os.environ.setdefault("FISH_WEB_CONTROL", "1")

import fish_game_all_in_one as fish


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "index.html")
WEBSITE_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "website.html")
RULES_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "rules.html")
ABOUT_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "about.html")
DATASET_PATH = os.path.join(BASE_DIR, "multiplayer", "human_game_dataset.jsonl")
ROOM_STATE_DIR = str(
    os.environ.get("FISH_ROOM_STATE_DIR", os.path.join(BASE_DIR, "multiplayer", "state"))
).strip() or os.path.join(BASE_DIR, "multiplayer", "state")

BRAIN_LOCK = threading.Lock()
DATASET_LOCK = threading.Lock()
PUBLIC_BASE_URL = ""
LAN_IP_CACHE: Optional[str] = None
ACTIVE_SERVER: Optional[StableThreadingHTTPServer] = None
CREATE_KEY = ""
CORS_ALLOW_ORIGIN = os.environ.get("FISH_CORS_ALLOW_ORIGIN", "*").strip() or "*"
PUBLIC_LINKS_PATH = (
    str(os.environ.get("FISH_PUBLIC_LINKS_PATH", os.path.join(BASE_DIR, "multiplayer", "public_links.json"))).strip()
    or os.path.join(BASE_DIR, "multiplayer", "public_links.json")
)
MAX_JSON_BODY_BYTES = 128 * 1024
ROOM_CHECKPOINT_SCHEMA_VERSION = 1
ROOM_PERSIST_MIN_INTERVAL_SEC = 0.75
MAX_ACTION_HISTORY = 16000
ROOM_ID_LENGTH = 5
ROOM_ID_RE = re.compile(rf"[A-Z0-9]{{{ROOM_ID_LENGTH}}}")
ROOM_ID_ACCEPT_RE = re.compile(rf"(?:[A-Z0-9]{{{ROOM_ID_LENGTH}}}|[A-Z0-9]{{8}})")

CLIENT_DIR = os.path.join(BASE_DIR, "multiplayer", "client")
MANIFEST_PATH = os.path.join(CLIENT_DIR, "manifest.webmanifest")
SERVICE_WORKER_PATH = os.path.join(CLIENT_DIR, "sw.js")
ICON_PATH = os.path.join(CLIENT_DIR, "icon.svg")


def room_state_path(room_id: str) -> str:
    safe = re.sub(r"[^A-Z0-9]", "", str(room_id or "").upper())
    if not safe:
        safe = "UNKNOWN"
    return os.path.join(ROOM_STATE_DIR, f"{safe}.json")


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=True)
    os.replace(tmp_path, path)


def remove_room_state_file(room_id: str) -> None:
    try:
        os.remove(room_state_path(room_id))
    except FileNotFoundError:
        return
    except Exception:
        return


def list_room_state_files() -> List[str]:
    try:
        names = os.listdir(ROOM_STATE_DIR)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out: List[str] = []
    for name in names:
        if not str(name).endswith(".json"):
            continue
        out.append(os.path.join(ROOM_STATE_DIR, str(name)))
    out.sort()
    return out


def json_dumps(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def now_unix() -> int:
    return int(time.time())


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if out < lo:
        return lo
    if out > hi:
        return hi
    return out


def clamp_int_or_none(value: Any, lo: int, hi: int) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out < lo or out > hi:
        return None
    return out


def int_list(raw: Any, cap: int = 64) -> List[int]:
    out: List[int] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, int):
            out.append(int(item))
            if len(out) >= cap:
                break
    return out


def room_code(length: int = ROOM_ID_LENGTH) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def safe_name(name: Any, fallback: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return fallback
    cleaned = " ".join(raw.split())
    if len(cleaned) > 32:
        cleaned = cleaned[:32].rstrip()
    return cleaned or fallback


def _split_host_port(host_header: str) -> tuple[str, str]:
    raw = (host_header or "").strip()
    if not raw:
        return "127.0.0.1", "8777"
    if raw.startswith("[") and "]" in raw:
        close = raw.find("]")
        host = raw[1:close]
        rest = raw[close + 1 :]
        if rest.startswith(":"):
            return host, rest[1:] or "8777"
        return host, "8777"
    if ":" in raw:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            return host, port
    return raw, "8777"


def _host_header_has_explicit_port(host_header: str) -> bool:
    raw = (host_header or "").strip()
    if not raw:
        return False
    if raw.startswith("[") and "]" in raw:
        close = raw.find("]")
        rest = raw[close + 1 :]
        return rest.startswith(":") and rest[1:].isdigit()
    if raw.count(":") == 1:
        _, port = raw.rsplit(":", 1)
        return port.isdigit()
    return False


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return (
        h in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
        or h.startswith("127.")
        or h == ""
    )


def detect_lan_ipv4() -> Optional[str]:
    global LAN_IP_CACHE
    if LAN_IP_CACHE:
        return LAN_IP_CACHE

    # Fast local fallback from hostname resolution.
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                LAN_IP_CACHE = ip
                return ip
    except Exception:
        pass

    # Mac/Linux fallback: parse interface output.
    try:
        proc = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
        text = proc.stdout or ""
        current_iface = ""
        current_active = False
        for line in text.splitlines():
            if not line.startswith("\t") and ":" in line:
                current_iface = line.split(":", 1)[0].strip()
                current_active = False
                continue
            s = line.strip()
            if s == "status: active":
                current_active = True
                continue
            if s.startswith("inet "):
                parts = s.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    if ip.startswith("127."):
                        continue
                    if current_active and current_iface and current_iface != "lo0":
                        LAN_IP_CACHE = ip
                        return ip
    except Exception:
        pass
    return None


def share_base_url(host_header: str, proto_hint: str = "") -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")

    host, port = _split_host_port(host_header)
    # Current serveousercontent cert is expired; use HTTP so links still open.
    if host.endswith(".serveousercontent.com"):
        return f"http://{host}"
    if host.endswith(".lhr.life"):
        return f"https://{host}"

    hinted = (proto_hint or "").strip().lower()
    if hinted in {"http", "https"}:
        if _host_header_has_explicit_port(host_header):
            return f"{hinted}://{host}:{port}"
        return f"{hinted}://{host}"

    if _is_loopback_host(host):
        lan_ip = detect_lan_ipv4()
        if lan_ip:
            return f"http://{lan_ip}:{port}"
        return f"http://{host}:{port}"

    # Reverse-tunnel hosts often omit explicit ports and are HTTPS terminated.
    if not _host_header_has_explicit_port(host_header):
        return f"https://{host}"
    if port == "443":
        return f"https://{host}"
    if port == "80":
        return f"http://{host}"
    return f"http://{host}:{port}"


def normalize_public_url(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        parsed = urlparse(s)
    except Exception:
        return ""
    scheme = (parsed.scheme or "").lower().strip()
    if scheme not in {"http", "https"}:
        return ""
    host = (parsed.netloc or "").strip()
    if not host:
        return ""
    return f"{scheme}://{host}"


def load_public_links() -> List[str]:
    out: List[str] = []

    def _add(raw: Any) -> None:
        url = normalize_public_url(raw)
        if not url:
            return
        if url in out:
            return
        out.append(url)

    _add(PUBLIC_BASE_URL)
    try:
        with open(PUBLIC_LINKS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return out

    if isinstance(payload, dict):
        candidates = payload.get("public_urls")
        if not isinstance(candidates, list):
            candidates = payload.get("urls")
        if not isinstance(candidates, list):
            candidates = payload.get("links")
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []

    for raw in candidates:
        _add(raw)
        if len(out) >= 12:
            break
    return out


def _missing_card_payload(uid: int) -> Dict[str, Any]:
    return {
        "uid": int(uid),
        "name": "[missing card]",
        "species": "unknown",
        "cost": 0,
        "direction": "n/a",
        "symbol": "n/a",
        "text": "Card data missing from current card_db.",
    }


def _card_payload_safe(gs: fish.GameState, uid: int) -> Dict[str, Any]:
    card = gs.card_db.get(uid)
    if card is None:
        return _missing_card_payload(uid)
    return fish.card_to_dict(card)


def entry_to_dict(ms: fish.MatchState, gs: fish.GameState, entry_uid: int) -> Dict[str, Any]:
    try:
        entry_id = int(entry_uid)
    except Exception:
        entry_id = -1
    try:
        faces = fish.entry_faces(ms, entry_uid)
    except Exception:
        faces = [entry_uid] if isinstance(entry_uid, int) else []
    try:
        label = fish.entry_short_label(ms, gs, entry_uid)
    except Exception:
        label = f"{entry_id}:[invalid entry]" if entry_id >= 0 else "[invalid entry]"
    return {
        "entry_uid": entry_id,
        "label": label,
        "faces": [_card_payload_safe(gs, int(uid)) for uid in faces if isinstance(uid, int)],
    }


def board_to_dict(player: fish.PlayerState, gs: fish.GameState) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for ocean_uid in player.board_oceans:
        try:
            ocean_id = int(ocean_uid)
        except Exception:
            continue
        slots = player.ocean_slots.get(ocean_uid)
        if not isinstance(slots, fish.OceanSlots):
            slots = fish.OceanSlots()
        up = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "up", [])) if isinstance(uid, int)]
        down = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "down", [])) if isinstance(uid, int)]
        left = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "left", [])) if isinstance(uid, int)]
        right = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "right", [])) if isinstance(uid, int)]
        payload.append(
            {
                "ocean_uid": ocean_id,
                "ocean": _card_payload_safe(gs, ocean_id),
                "up": up,
                "down": down,
                "left": left,
                "right": right,
            }
        )
    return payload


def choose_action_weighted_light(
    gs: fish.GameState,
    ms: fish.MatchState,
    player: fish.PlayerState,
    weights: Dict[str, float],
    synergy_map: Dict[str, float],
    species_map: Dict[str, float],
    same_ocean_map: Dict[str, float],
    strategy_value_map: Dict[str, float],
    strategy_count_map: Dict[str, int],
    strategy_transition_map: Dict[str, float],
    strategy_transition_count_map: Dict[str, int],
) -> Optional[fish.Action]:
    """
    Lightweight AI chooser for live multiplayer.
    Avoids deep simulated lookahead to keep turns responsive and avoid
    constant simulation-heavy evaluation.
    """
    acts = fish.candidate_actions_for_ai(gs, ms, player)
    acts = fish.filter_overbuild_ocean_actions(gs, ms, player, acts)
    non_dead = [a for a in acts if not fish.action_is_dead_engine_play(gs, ms, player, a)]
    if non_dead:
        acts = non_dead
    if not acts:
        return None

    scored: List[tuple[fish.Action, float]] = []
    for action in acts:
        feats = fish.action_features(
            gs,
            ms,
            player,
            action,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            include_sim_delta=False,  # key: no per-action simulation
        )
        score = fish.weighted_score(feats, weights)
        strategy_v, novelty_v, branch_v, _ = fish.strategy_signal(
            gs,
            ms,
            player,
            action,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
        )
        score += weights.get("strategy_bonus", 0.0) * strategy_v
        score += weights.get("novelty_bonus", 0.0) * novelty_v
        score += weights.get("branch_bonus", 0.0) * branch_v
        score += fish.action_engine_timing_bonus(gs, ms, player, action)
        score += fish.human_realism_action_adjustment(gs, ms, player, action, feats)
        scored.append((action, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    best = scored[0][0]

    # Keep board-first behavior if a draw only barely wins.
    if best.kind == "draw":
        play_opts = [(a, s) for a, s in scored if a.kind != "draw"]
        if play_opts:
            best_play, best_play_score = max(play_opts, key=lambda x: x[1])
            if best_play_score >= scored[0][1] - 0.6:
                return best_play
    return best


@dataclass
class Seat:
    index: int
    kind: str  # human | ai
    label: str
    claimed_name: Optional[str] = None
    token: Optional[str] = None
    is_host: bool = False

    def status(self) -> str:
        if self.kind == "ai":
            return "ai"
        return "taken" if self.token else "available"


class GameRoom:
    def __init__(self, room_id: str, host_name: str, total_players: int, human_players: int, ai_players: int) -> None:
        self.room_id = room_id
        self.total_players = total_players
        self.human_players = human_players
        self.ai_players = ai_players
        self.seed = (now_unix() ^ secrets.randbits(31)) & 0x7FFFFFFF
        self.created_unix = now_unix()
        self.started_unix: Optional[int] = None
        self.ended_unix: Optional[int] = None

        self.cond = threading.Condition()
        self.state_version = 1
        self.phase = "lobby"  # lobby | running | ended | error
        self.status_note = "Lobby open. Claim seats and start when ready."
        self.error_message: Optional[str] = None

        self.host_control_token = secrets.token_urlsafe(18)
        self.seats: List[Seat] = []
        ai_num = 1
        for i in range(total_players):
            if i < human_players:
                self.seats.append(
                    Seat(
                        index=i,
                        kind="human",
                        label=f"Player {i + 1}",
                        is_host=(i == 0),  # host is whoever claims the first human seat
                    )
                )
            else:
                self.seats.append(
                    Seat(
                        index=i,
                        kind="ai",
                        label=f"Player {i + 1}",
                        claimed_name=f"AI Player {ai_num}",
                        token=None,
                    )
                )
                ai_num += 1

        host_seat = self.host_seat()
        if host_seat is None:
            raise ValueError("room needs at least one human seat")
        # Auto-claim host seat at room creation so the creator is always Player 1.
        host_seat.claimed_name = safe_name(host_name, "Host")
        host_seat.token = secrets.token_urlsafe(18)
        self.status_note = (
            f"Lobby open. {safe_name(host_name, 'Host')} is Host (Player 1). "
            "Others can claim remaining human seats."
        )

        self.latest_public_state: Optional[Dict[str, Any]] = None
        self.latest_private_hands: Dict[int, List[Dict[str, Any]]] = {}
        self.last_turn_number: int = 0

        self.legal_actions_by_seat: Dict[int, Dict[str, Any]] = {}
        self.pending_actions: Dict[int, List[Dict[str, Any]]] = {}
        # Idempotency map for action submissions to prevent duplicate action
        # execution when clients retry after transient network issues.
        self.seen_action_requests: Dict[int, Dict[str, int]] = {}
        self.active_action_seat: Optional[int] = None

        self.log_events: List[str] = []
        self.turn_summaries: List[Dict[str, Any]] = []
        self._last_turn_scores: Dict[str, int] = {}

        self.training_events: List[str] = []
        self.training_snapshots: List[Dict[str, Any]] = []

        self.final_scores: List[Dict[str, Any]] = []
        self.winner: Optional[str] = None

        self.game_thread: Optional[threading.Thread] = None
        self.action_history: List[Dict[str, Any]] = []
        self.recovery_active = False
        self.recovery_target_count = 0
        self.recovery_cursor = 0
        self.recovery_error: Optional[str] = None
        self._skip_history_record_count = 0

        self._persist_dirty = True
        self._last_persist_monotonic = 0.0

    def _bump_locked(self, force_persist: bool = False) -> None:
        self.state_version += 1
        self.cond.notify_all()
        self._persist_dirty = True
        self._persist_if_due_locked(force=force_persist)

    def _int_keyed_dict(self, raw: Any, value_type: type) -> Dict[int, Any]:
        out: Dict[int, Any] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if value_type is dict and not isinstance(value, dict):
                continue
            if value_type is list and not isinstance(value, list):
                continue
            out[idx] = copy.deepcopy(value)
        return out

    def _normalize_action_history_item(self, raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind:
            return None
        rec: Dict[str, Any] = {"kind": kind}
        for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "seat_index", "turn_number"):
            if key in raw and isinstance(raw.get(key), int):
                rec[key] = int(raw.get(key))
        rec["use_star"] = bool(raw.get("use_star"))
        rec["pool_pick_uids"] = int_list(raw.get("pool_pick_uids"), cap=8)
        rec["payment_uids"] = int_list(raw.get("payment_uids"), cap=10)
        if isinstance(raw.get("player_name"), str):
            rec["player_name"] = str(raw.get("player_name"))
        if isinstance(raw.get("source"), str):
            rec["source"] = str(raw.get("source"))
        if isinstance(raw.get("request_id"), str):
            rec["request_id"] = str(raw.get("request_id"))[:96]
        if isinstance(raw.get("recorded_unix"), int):
            rec["recorded_unix"] = int(raw.get("recorded_unix"))
        return rec

    def _serialize_checkpoint_locked(self) -> Dict[str, Any]:
        return {
            "schema_version": ROOM_CHECKPOINT_SCHEMA_VERSION,
            "saved_unix": now_unix(),
            "room_id": self.room_id,
            "total_players": int(self.total_players),
            "human_players": int(self.human_players),
            "ai_players": int(self.ai_players),
            "seed": int(self.seed),
            "created_unix": int(self.created_unix),
            "started_unix": self.started_unix,
            "ended_unix": self.ended_unix,
            "state_version": int(self.state_version),
            "phase": self.phase,
            "status_note": self.status_note,
            "error_message": self.error_message,
            "host_control_token": self.host_control_token,
            "seats": [
                {
                    "index": int(seat.index),
                    "kind": str(seat.kind),
                    "label": str(seat.label),
                    "claimed_name": seat.claimed_name,
                    "token": seat.token,
                    "is_host": bool(seat.is_host),
                }
                for seat in self.seats
            ],
            "latest_public_state": copy.deepcopy(self.latest_public_state),
            "latest_private_hands": {str(k): copy.deepcopy(v) for k, v in self.latest_private_hands.items()},
            "last_turn_number": int(self.last_turn_number),
            "legal_actions_by_seat": {str(k): copy.deepcopy(v) for k, v in self.legal_actions_by_seat.items()},
            "seen_action_requests": {str(k): dict(v) for k, v in self.seen_action_requests.items()},
            "active_action_seat": self.active_action_seat,
            "log_events": list(self.log_events[-500:]),
            "turn_summaries": list(self.turn_summaries[-200:]),
            "final_scores": copy.deepcopy(self.final_scores),
            "winner": self.winner,
            "action_history": copy.deepcopy(self.action_history),
            "recovery": {
                "active": bool(self.recovery_active),
                "target_count": int(self.recovery_target_count),
                "cursor": int(self.recovery_cursor),
                "error": self.recovery_error,
            },
        }

    def _persist_if_due_locked(self, force: bool = False) -> None:
        if not self._persist_dirty:
            return
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_persist_monotonic) < ROOM_PERSIST_MIN_INTERVAL_SEC:
            return
        payload = self._serialize_checkpoint_locked()
        try:
            atomic_write_json(room_state_path(self.room_id), payload)
            self._persist_dirty = False
            self._last_persist_monotonic = now_mono
        except Exception:
            # Keep state dirty so later updates can retry.
            self._persist_dirty = True

    def persist_now(self) -> None:
        with self.cond:
            self._persist_if_due_locked(force=True)

    def _first_open_human_seat(self) -> Optional[Seat]:
        for seat in self.seats:
            if seat.kind == "human" and not seat.token:
                return seat
        return None

    def _seat_from_token_locked(self, token: Optional[str]) -> Optional[Seat]:
        if not token:
            return None
        for seat in self.seats:
            if seat.token and secrets.compare_digest(seat.token, token):
                return seat
        return None

    def _all_humans_claimed_locked(self) -> bool:
        for seat in self.seats:
            if seat.kind == "human" and not seat.token:
                return False
        return True

    def host_seat(self) -> Optional[Seat]:
        for seat in self.seats:
            if seat.is_host:
                return seat
        return None

    def _is_host_authorized_locked(self, host_token: str, seat_token: Optional[str]) -> bool:
        authorized = secrets.compare_digest(self.host_control_token, host_token or "")
        if not authorized and seat_token:
            seat = self._seat_from_token_locked(seat_token)
            if seat is not None and seat.kind == "human" and seat.is_host:
                authorized = True
        return authorized

    def is_host_authorized(self, host_token: str, seat_token: Optional[str]) -> bool:
        with self.cond:
            return self._is_host_authorized_locked(host_token, seat_token)

    def room_link(self, host_header: str, proto_hint: str = "") -> str:
        return f"{share_base_url(host_header, proto_hint)}/play/{self.room_id}"

    def seat_snapshot_locked(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for seat in self.seats:
            out.append(
                {
                    "index": seat.index,
                    "label": seat.label,
                    "kind": seat.kind,
                    "status": seat.status(),
                    "claimed_name": seat.claimed_name,
                    "is_host": bool(seat.is_host),
                }
            )
        return out

    @classmethod
    def from_checkpoint(cls, payload: Dict[str, Any]) -> Optional["GameRoom"]:
        if not isinstance(payload, dict):
            return None
        room_id = str(payload.get("room_id") or "").strip().upper()
        if not ROOM_ID_ACCEPT_RE.fullmatch(room_id):
            return None

        total_players = clamp_int(payload.get("total_players"), 4, 2, 8)
        human_players = clamp_int(payload.get("human_players"), 2, 1, total_players)
        ai_players = clamp_int(payload.get("ai_players"), total_players - human_players, 0, total_players - 1)
        if human_players + ai_players != total_players:
            ai_players = max(0, total_players - human_players)

        host_name = "Host"
        seats_raw = payload.get("seats")
        if isinstance(seats_raw, list):
            for seat_raw in seats_raw:
                if not isinstance(seat_raw, dict):
                    continue
                if seat_raw.get("is_host") and isinstance(seat_raw.get("claimed_name"), str):
                    host_name = safe_name(seat_raw.get("claimed_name"), "Host")
                    break

        room = cls(room_id, host_name, total_players, human_players, ai_players)
        with room.cond:
            room.seed = clamp_int(payload.get("seed"), room.seed, 0, 0x7FFFFFFF)
            room.created_unix = clamp_int(payload.get("created_unix"), room.created_unix, 0, 2**31 - 1)
            room.started_unix = clamp_int_or_none(payload.get("started_unix"), 0, 2**31 - 1)
            room.ended_unix = clamp_int_or_none(payload.get("ended_unix"), 0, 2**31 - 1)
            room.state_version = max(1, clamp_int(payload.get("state_version"), room.state_version, 1, 2**31 - 1))

            phase_raw = str(payload.get("phase") or "").strip().lower()
            room.phase = phase_raw if phase_raw in {"lobby", "running", "ended", "error"} else "lobby"

            if isinstance(payload.get("status_note"), str) and str(payload.get("status_note")).strip():
                room.status_note = str(payload.get("status_note")).strip()
            room.error_message = str(payload.get("error_message")).strip() if isinstance(payload.get("error_message"), str) else None

            host_token_raw = payload.get("host_control_token")
            if isinstance(host_token_raw, str) and host_token_raw.strip():
                room.host_control_token = host_token_raw.strip()

            parsed_seats: List[Seat] = []
            if isinstance(seats_raw, list) and len(seats_raw) == total_players:
                ai_num = 1
                for idx in range(total_players):
                    seat_raw = seats_raw[idx] if isinstance(seats_raw[idx], dict) else {}
                    seat_kind = str(seat_raw.get("kind") or "").strip().lower()
                    if seat_kind not in {"human", "ai"}:
                        seat_kind = "human" if idx < human_players else "ai"
                    default_label = f"Player {idx + 1}"
                    seat_label = safe_name(seat_raw.get("label"), default_label)
                    claimed_name: Optional[str] = None
                    if seat_kind == "ai":
                        raw_ai_name = seat_raw.get("claimed_name")
                        claimed_name = (
                            safe_name(raw_ai_name, f"AI Player {ai_num}")
                            if isinstance(raw_ai_name, str) and raw_ai_name.strip()
                            else f"AI Player {ai_num}"
                        )
                        ai_num += 1
                    else:
                        raw_human_name = seat_raw.get("claimed_name")
                        if isinstance(raw_human_name, str) and raw_human_name.strip():
                            claimed_name = safe_name(raw_human_name, seat_label)
                    token = seat_raw.get("token") if seat_kind == "human" and isinstance(seat_raw.get("token"), str) else None
                    parsed_seats.append(
                        Seat(
                            index=idx,
                            kind=seat_kind,
                            label=seat_label,
                            claimed_name=claimed_name,
                            token=token,
                            is_host=bool(seat_raw.get("is_host")) if seat_kind == "human" else False,
                        )
                    )
            if parsed_seats:
                room.seats = parsed_seats

            host_humans = [seat for seat in room.seats if seat.kind == "human" and seat.is_host]
            if not host_humans:
                for seat in room.seats:
                    if seat.kind == "human":
                        seat.is_host = True
                        break
            for seat in room.seats:
                if seat.kind != "human":
                    seat.is_host = False
                    seat.token = None

            latest_public = payload.get("latest_public_state")
            room.latest_public_state = copy.deepcopy(latest_public) if isinstance(latest_public, dict) else None
            room.latest_private_hands = room._int_keyed_dict(payload.get("latest_private_hands"), list)
            room.last_turn_number = clamp_int(payload.get("last_turn_number"), 0, 0, 1000000)
            room.legal_actions_by_seat = room._int_keyed_dict(payload.get("legal_actions_by_seat"), dict)
            room.pending_actions = {}

            seen_raw = payload.get("seen_action_requests")
            parsed_seen: Dict[int, Dict[str, int]] = {}
            if isinstance(seen_raw, dict):
                for seat_key, req_map in seen_raw.items():
                    try:
                        seat_idx = int(seat_key)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(req_map, dict):
                        continue
                    keep: Dict[str, int] = {}
                    for req_id, ts in req_map.items():
                        if not isinstance(req_id, str) or not req_id:
                            continue
                        if len(req_id) > 96:
                            continue
                        if isinstance(ts, int):
                            keep[req_id] = int(ts)
                        if len(keep) >= 512:
                            break
                    if keep:
                        parsed_seen[seat_idx] = keep
            room.seen_action_requests = parsed_seen

            room.active_action_seat = clamp_int_or_none(payload.get("active_action_seat"), 0, max(0, total_players - 1))
            room.log_events = [str(x) for x in list(payload.get("log_events", [])) if isinstance(x, str)][-500:]
            room.turn_summaries = [x for x in list(payload.get("turn_summaries", [])) if isinstance(x, dict)][-200:]
            room.final_scores = [x for x in list(payload.get("final_scores", [])) if isinstance(x, dict)]
            room.winner = str(payload.get("winner")).strip() if isinstance(payload.get("winner"), str) else None

            action_history_raw = payload.get("action_history")
            parsed_actions: List[Dict[str, Any]] = []
            if isinstance(action_history_raw, list):
                for item in action_history_raw:
                    normalized = room._normalize_action_history_item(item)
                    if normalized is not None:
                        parsed_actions.append(normalized)
                    if len(parsed_actions) >= MAX_ACTION_HISTORY:
                        break
            room.action_history = parsed_actions
            room.recovery_target_count = len(room.action_history)
            room.recovery_cursor = room.recovery_target_count if room.phase != "running" else 0
            room.recovery_active = bool(room.phase == "running" and room.recovery_target_count > 0)
            room._skip_history_record_count = 0

            recovery_raw = payload.get("recovery")
            if isinstance(recovery_raw, dict) and isinstance(recovery_raw.get("error"), str):
                err_msg = str(recovery_raw.get("error")).strip()
                if err_msg:
                    room.recovery_error = err_msg

            room.training_events = []
            room.training_snapshots = []

            room._persist_dirty = True
            room._last_persist_monotonic = 0.0
            room.game_thread = None

        return room

    def _action_to_history_record(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        source: str,
        turn_number: Optional[int] = None,
        request_id: str = "",
    ) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "recorded_unix": now_unix(),
            "seat_index": int(seat_index),
            "player_name": safe_name(player_name, f"Player {seat_index + 1}"),
            "source": str(source),
            "kind": str(action.kind),
            "card_uid": int(getattr(action, "card_uid", -1)),
            "face_uid": int(action.face_uid if action.face_uid is not None else getattr(action, "card_uid", -1)),
            "draw_from_pool": int(getattr(action, "draw_from_pool", 0)),
            "use_star": bool(getattr(action, "use_star", False)),
            "pool_pick_uids": int_list(getattr(action, "pool_pick_uids", []), cap=8),
            "payment_uids": int_list(getattr(action, "payment_uids", []), cap=10),
        }
        if getattr(action, "ocean_uid", None) is not None:
            rec["ocean_uid"] = int(action.ocean_uid)
        src_ocean = getattr(action, "source_ocean_uid", None)
        if src_ocean is not None:
            rec["source_ocean_uid"] = int(src_ocean)
        req = str(request_id or "").strip()
        if req:
            rec["request_id"] = req[:96]
        if isinstance(turn_number, int):
            rec["turn_number"] = int(turn_number)
        return rec

    def _record_action_history(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        source: str,
        turn_number: Optional[int] = None,
        request_id: str = "",
    ) -> None:
        rec = self._action_to_history_record(
            seat_index,
            player_name,
            action,
            source,
            turn_number=turn_number,
            request_id=request_id,
        )
        with self.cond:
            if self._skip_history_record_count > 0:
                self._skip_history_record_count -= 1
                return
            self.action_history.append(rec)
            if len(self.action_history) > MAX_ACTION_HISTORY:
                del self.action_history[: len(self.action_history) - MAX_ACTION_HISTORY]
            self.recovery_target_count = len(self.action_history)
            self.recovery_cursor = self.recovery_target_count
            self._persist_dirty = True
            self._persist_if_due_locked(force=True)

    def record_executed_action(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        turn_number: int,
    ) -> None:
        if seat_index < 0 or seat_index >= self.total_players:
            return
        self._record_action_history(
            seat_index=seat_index,
            player_name=player_name,
            action=action,
            source="executed",
            turn_number=turn_number,
        )

    def _action_cmd_from_history_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        cmd: Dict[str, Any] = {}
        for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
            value = record.get(key)
            if key == "kind":
                if isinstance(value, str) and value:
                    cmd[key] = value
            elif isinstance(value, int):
                cmd[key] = int(value)
        if isinstance(record.get("use_star"), bool):
            cmd["use_star"] = bool(record.get("use_star"))
        cmd["pool_pick_uids"] = int_list(record.get("pool_pick_uids"), cap=8)
        cmd["payment_uids"] = int_list(record.get("payment_uids"), cap=10)
        return cmd

    def _replay_action_if_available(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        seat_index: int,
        actions: List[fish.Action],
    ) -> Optional[fish.Action]:
        with self.cond:
            if self.phase != "running" or not self.recovery_active:
                return None
            target = min(self.recovery_target_count, len(self.action_history))
            if self.recovery_cursor >= target:
                self.recovery_active = False
                self.recovery_error = None
                self.status_note = "Recovery complete. Live play resumed."
                self._bump_locked(force_persist=True)
                return None

            record = self.action_history[self.recovery_cursor]
            expected_seat = record.get("seat_index")
            if isinstance(expected_seat, int) and expected_seat != seat_index:
                self.recovery_active = False
                self.recovery_error = (
                    f"replay seat mismatch at action {self.recovery_cursor + 1}: "
                    f"expected seat {expected_seat + 1}, got seat {seat_index + 1}"
                )
                self.status_note = "Recovery desynced. Live play resumed from nearest safe state."
                self._bump_locked(force_persist=True)
                return None
            cmd = self._action_cmd_from_history_record(record)

        chosen = self._resolve_submitted_action(gs, ms, player, actions, cmd)
        with self.cond:
            if not self.recovery_active:
                return None
            if chosen is None:
                self.recovery_active = False
                legal_kinds = [str(cand.kind) for cand in actions[:6]]
                self.recovery_error = (
                    f"replay action not legal at step {self.recovery_cursor + 1} "
                    f"(seat={record.get('seat_index')}, kind={record.get('kind')}, card={record.get('card_uid')}, "
                    f"legal={legal_kinds})"
                )
                self.status_note = "Recovery desynced. Live play resumed from nearest safe state."
                self._bump_locked(force_persist=True)
                return None

            self.recovery_cursor += 1
            self._skip_history_record_count += 1
            target = min(self.recovery_target_count, len(self.action_history))
            if self.recovery_cursor >= target:
                self.recovery_active = False
                self.recovery_error = None
                self.status_note = "Recovery complete. Live play resumed."
                self._bump_locked(force_persist=True)
            elif self.recovery_cursor % 20 == 0:
                self.status_note = f"Recovering game state ({self.recovery_cursor}/{target})..."
                self._bump_locked(force_persist=False)
        return chosen

    def claim_seat(
        self,
        player_name: str,
        seat_index: Optional[int],
        existing_token: Optional[str],
        allow_takeover: bool = False,
        allow_host_takeover: bool = False,
    ) -> Dict[str, Any]:
        with self.cond:
            existing_seat: Optional[Seat] = None
            if existing_token:
                existing_seat = self._seat_from_token_locked(existing_token)
                # Plain reconnect path only when no explicit seat target was requested.
                if existing_seat is not None and seat_index is None:
                    new_name = safe_name(player_name, existing_seat.label)
                    if new_name and existing_seat.claimed_name != new_name:
                        existing_seat.claimed_name = new_name
                        self.status_note = f"{existing_seat.claimed_name} reconnected to {existing_seat.label}."
                        self._bump_locked()
                    return {
                        "ok": True,
                        "seat_index": existing_seat.index,
                        "seat_token": existing_seat.token,
                        "reconnected": True,
                    }

            target: Optional[Seat] = None
            if seat_index is not None:
                if seat_index < 0 or seat_index >= len(self.seats):
                    return {"ok": False, "error": "invalid seat index"}
                target = self.seats[seat_index]
            else:
                if self.phase != "lobby":
                    return {"ok": False, "error": "seat index required after game start"}
                target = self._first_open_human_seat()

            if target is None:
                return {"ok": False, "error": "no open human seats"}
            if target.kind != "human":
                return {"ok": False, "error": "cannot claim AI seat"}
            if existing_seat is not None and target.index == existing_seat.index:
                # Explicit request for the same seat token holder: treat as reconnect/rename.
                new_name = safe_name(player_name, target.label)
                if new_name and target.claimed_name != new_name:
                    target.claimed_name = new_name
                    self.status_note = f"{target.claimed_name} updated name on {target.label}."
                    self._bump_locked()
                return {
                    "ok": True,
                    "seat_index": target.index,
                    "seat_token": target.token,
                    "reconnected": True,
                }
            if target.token is not None:
                host_ok = (not target.is_host) or allow_host_takeover
                # Idempotent host reclaim: if the same host identity is already on the host
                # seat, return the current token instead of rotating it on every reclaim call.
                if (
                    allow_takeover
                    and target.is_host
                    and allow_host_takeover
                    and target.claimed_name == safe_name(player_name, target.label)
                ):
                    return {
                        "ok": True,
                        "seat_index": target.index,
                        "seat_token": target.token,
                        "reconnected": True,
                    }
                if allow_takeover and host_ok:
                    previous_name = target.claimed_name or target.label
                    # Seat switch: if caller already owns another human seat, free it first.
                    if existing_seat is not None and existing_seat is not target and existing_seat.kind == "human":
                        existing_seat.claimed_name = None
                        existing_seat.token = None
                    target.claimed_name = safe_name(player_name, target.label)
                    target.token = secrets.token_urlsafe(18)
                    if target.is_host:
                        self.status_note = f"{target.claimed_name} reclaimed Host seat ({target.label})."
                    else:
                        self.status_note = f"{target.claimed_name} took over {target.label}."
                    self._bump_locked()
                    return {
                        "ok": True,
                        "seat_index": target.index,
                        "seat_token": target.token,
                        "reconnected": False,
                        "took_over": True,
                        "previous_name": previous_name,
                    }
                return {"ok": False, "error": "seat already taken"}
            if self.phase != "lobby":
                return {"ok": False, "error": "game already started"}

            # Seat switch: if caller already owns another human seat, free it first.
            if existing_seat is not None and existing_seat is not target and existing_seat.kind == "human":
                existing_seat.claimed_name = None
                existing_seat.token = None
            target.claimed_name = safe_name(player_name, target.label)
            target.token = secrets.token_urlsafe(18)
            self.status_note = f"{target.claimed_name} claimed {target.label}."
            self._bump_locked()
            return {"ok": True, "seat_index": target.index, "seat_token": target.token, "reconnected": False}

    def start_game(
        self,
        host_token: str,
        seat_token: Optional[str],
        card_db: Dict[int, fish.CardDef],
        allow_with_create_key: bool = False,
    ) -> Dict[str, Any]:
        with self.cond:
            if not (allow_with_create_key or self._is_host_authorized_locked(host_token, seat_token)):
                return {"ok": False, "error": "host authorization required"}
            if self.phase != "lobby":
                return {"ok": False, "error": "game already started"}
            if not self._all_humans_claimed_locked():
                return {"ok": False, "error": "all human seats must be claimed before start"}
            self._launch_game_locked(card_db, status_note="Game started. Waiting for first turn.")
            return {"ok": True}

    def restart_game(
        self,
        host_token: str,
        seat_token: Optional[str],
        card_db: Dict[int, fish.CardDef],
    ) -> Dict[str, Any]:
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            if self.phase == "running":
                return {"ok": False, "error": "game is still running"}
            if self.game_thread is not None and self.game_thread.is_alive():
                return {"ok": False, "error": "previous game is still finishing"}
            if not self._all_humans_claimed_locked():
                return {"ok": False, "error": "all human seats must be claimed before restart"}

            self._launch_game_locked(card_db, status_note="Game restarted. Waiting for first turn.")
            return {"ok": True}

    def resume_after_restore(self, card_db: Dict[int, fish.CardDef]) -> Dict[str, Any]:
        with self.cond:
            if self.phase != "running":
                return {"ok": False, "error": "room is not running"}
            if self.game_thread is not None and self.game_thread.is_alive():
                return {"ok": True, "already_running": True}

            self.error_message = None
            self.legal_actions_by_seat.clear()
            self.pending_actions.clear()
            self.active_action_seat = None
            if self.recovery_active:
                self.status_note = (
                    f"Recovering game state ({self.recovery_cursor}/{self.recovery_target_count})..."
                )
            else:
                self.recovery_error = None
                self.status_note = "Resuming live game."
            self._bump_locked(force_persist=True)

            self.game_thread = threading.Thread(target=self._run_game_thread, args=(card_db,), daemon=True)
            self.game_thread.start()
            return {"ok": True}

    def _launch_game_locked(self, card_db: Dict[int, fish.CardDef], status_note: str) -> None:
        # Ensure every game start/restart gets a fresh random shuffle seed.
        self.seed = (now_unix() ^ secrets.randbits(63)) & 0x7FFFFFFF
        self.phase = "running"
        self.started_unix = now_unix()
        self.ended_unix = None
        self.error_message = None
        self.status_note = status_note
        self.log_events = []
        self.turn_summaries = []
        self.training_events = []
        self.training_snapshots = []
        self.final_scores = []
        self.winner = None
        self.action_history = []
        self.recovery_active = False
        self.recovery_target_count = 0
        self.recovery_cursor = 0
        self.recovery_error = None
        self._skip_history_record_count = 0
        # Hard reset of published state so every game starts from a clean board view.
        self.latest_public_state = None
        self.latest_private_hands = {}
        self.last_turn_number = 0
        self._last_turn_scores = {}
        self.legal_actions_by_seat.clear()
        self.pending_actions.clear()
        self.seen_action_requests.clear()
        self.active_action_seat = None
        self._bump_locked(force_persist=True)

        self.game_thread = threading.Thread(target=self._run_game_thread, args=(card_db,), daemon=True)
        self.game_thread.start()

    def submit_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = payload.get("seat_token")
        with self.cond:
            seat = self._seat_from_token_locked(token if isinstance(token, str) else None)
            if seat is None:
                return {"ok": False, "error": "seat token invalid"}
            if self.phase != "running":
                return {"ok": False, "error": "game is not running"}
            if seat.kind != "human":
                return {"ok": False, "error": "AI seats cannot submit actions"}
            if self.active_action_seat != seat.index:
                return {"ok": False, "error": "not your turn"}

            req_id_raw = payload.get("request_id")
            req_id = req_id_raw.strip() if isinstance(req_id_raw, str) else ""
            seen: Optional[Dict[str, int]] = None
            req_now = 0
            if req_id:
                if len(req_id) > 96:
                    return {"ok": False, "error": "request_id too long"}
                seen = self.seen_action_requests.setdefault(seat.index, {})
                req_now = now_unix()
                stale_before = req_now - 900
                if seen:
                    for key, ts in list(seen.items()):
                        if ts < stale_before:
                            del seen[key]
                if req_id in seen:
                    # Duplicate retry of an already-accepted action request.
                    return {"ok": True, "duplicate": True}
                if len(seen) >= 256:
                    for key, _ in sorted(seen.items(), key=lambda kv: kv[1])[:80]:
                        del seen[key]

            idx = payload.get("action_index")
            has_semantic = any(
                key in payload
                for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "use_star")
            )
            if not isinstance(idx, int) and not has_semantic:
                return {"ok": False, "error": "action_index must be int or include action identity fields"}

            picks_raw = payload.get("pool_pick_uids", [])
            payment_raw = payload.get("payment_uids", [])
            picks = [x for x in picks_raw if isinstance(x, int)] if isinstance(picks_raw, list) else []
            payment = [x for x in payment_raw if isinstance(x, int)] if isinstance(payment_raw, list) else []

            cmd: Dict[str, Any] = {
                "action_index": idx if isinstance(idx, int) else None,
                "pool_pick_uids": picks,
                "payment_uids": payment,
                "submitted_unix": now_unix(),
            }
            kind = payload.get("kind")
            if isinstance(kind, str) and kind:
                cmd["kind"] = kind
            for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
                value = payload.get(key)
                if isinstance(value, int):
                    cmd[key] = value
            if isinstance(payload.get("use_star"), bool):
                cmd["use_star"] = bool(payload.get("use_star"))
            if req_id:
                cmd["request_id"] = req_id

            queue = self.pending_actions.setdefault(seat.index, [])
            if len(queue) >= 12:
                return {"ok": False, "error": "too many queued actions; wait for state update"}
            if req_id and seen is not None:
                seen[req_id] = req_now or now_unix()
            queue.append(cmd)
            self.status_note = f"Action queued from {seat.claimed_name or seat.label}."
            self._bump_locked()
            return {"ok": True}

    def _wait_for_action(self, seat_index: int) -> Optional[Dict[str, Any]]:
        with self.cond:
            while self.phase == "running":
                q = self.pending_actions.get(seat_index)
                if q:
                    return q.pop(0)
                self.cond.wait(timeout=0.25)
            return None

    def _serialize_legal_actions(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        actions: List[fish.Action],
    ) -> Dict[str, Any]:
        payload_actions: List[Dict[str, Any]] = []
        for idx, action in enumerate(actions):
            face_uid = action.face_uid if action.face_uid is not None else action.card_uid
            card = gs.card_db.get(face_uid)
            direction = fish.normalize_direction(card.direction) if card is not None else "n/a"
            cost_to_pay, requires_symbol, required_symbol = self._action_payment_requirements(gs, ms, player, action)
            payment_candidates = [int(uid) for uid in player.hand if uid != action.card_uid] if cost_to_pay > 0 else []
            payload_actions.append(
                {
                    "index": idx,
                    "kind": action.kind,
                    "card_uid": action.card_uid,
                    "face_uid": face_uid,
                    "face_name": card.name if card is not None else "",
                    "face_direction": direction,
                    "ocean_uid": action.ocean_uid,
                    "source_ocean_uid": getattr(action, "source_ocean_uid", None),
                    "draw_from_pool": action.draw_from_pool,
                    "use_star": bool(action.use_star),
                    "cost_to_pay": int(cost_to_pay),
                    "requires_symbol_match": bool(requires_symbol),
                    "required_symbol": required_symbol,
                    "payment_candidates": payment_candidates,
                    "description": fish.describe_action(gs, ms, action),
                }
            )
        return {
            "updated_unix": now_unix(),
            "player": player.name,
            "turn_index": int(gs.turn_index),
            "round_count": int(gs.round_count),
            "must_discard_to_ten": bool(actions) and all(a.kind == "discard_to_pool" for a in actions),
            "hand_limit": int(fish.HAND_LIMIT) if hasattr(fish, "HAND_LIMIT") else 10,
            "actions": payload_actions,
        }

    def _resolve_submitted_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        actions: List[fish.Action],
        cmd: Dict[str, Any],
    ) -> Optional[fish.Action]:
        idx_raw = cmd.get("action_index")
        has_identity = any(
            key in cmd for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "use_star")
        )
        chosen: Optional[fish.Action] = None

        if isinstance(idx_raw, int) and 0 <= idx_raw < len(actions):
            by_index = actions[idx_raw]
            if has_identity:
                if self._action_matches_identity(by_index, cmd):
                    chosen = copy.deepcopy(by_index)
                else:
                    # Stale index: resolve by identity so we do not execute
                    # the wrong card/action and accidentally burn hand cards.
                    chosen = self._resolve_action_by_identity(actions, cmd)
            else:
                chosen = copy.deepcopy(by_index)

        if chosen is None and has_identity:
            chosen = self._resolve_action_by_identity(actions, cmd)

        if chosen is None:
            return None

        if chosen.kind == "draw" and chosen.draw_from_pool > 0:
            need = chosen.draw_from_pool
            picks_raw = cmd.get("pool_pick_uids", [])
            picks = [x for x in picks_raw if isinstance(x, int)] if isinstance(picks_raw, list) else []
            if len(picks) == need and len(set(picks)) == need and all(uid in ms.pool for uid in picks):
                chosen.pool_pick_uids = picks
            else:
                chosen.pool_pick_uids = list(ms.pool[:need])

        pay_raw = cmd.get("payment_uids", [])
        provided_payments = [x for x in pay_raw if isinstance(x, int)] if isinstance(pay_raw, list) else []

        if chosen.kind in {"play_ocean", "play_to_ocean"}:
            play_face_uid = chosen.face_uid if chosen.face_uid is not None else chosen.card_uid
            cost_to_pay, requires_symbol, required_symbol = self._action_payment_requirements(gs, ms, player, chosen)

            def valid_payment_set(payments: List[int]) -> bool:
                if cost_to_pay <= 0:
                    return len(payments) == 0
                if len(payments) != cost_to_pay:
                    return False
                if len(set(payments)) != len(payments):
                    return False
                if any(uid == chosen.card_uid or uid not in player.hand for uid in payments):
                    return False
                if requires_symbol:
                    target_sym = fish.normalize_symbol(required_symbol)
                    if target_sym in {"", "n/a"}:
                        card = gs.card_db.get(play_face_uid)
                        if card is None:
                            return False
                        target_sym = fish.normalize_symbol(card.symbol)
                    if target_sym in {"", "n/a"}:
                        return False
                    if not any(fish.symbol_match_for_entry(ms, gs, uid, target_sym) for uid in payments):
                        return False
                return True

            def auto_payment_set() -> Optional[List[int]]:
                if cost_to_pay <= 0:
                    return []
                candidates = [int(uid) for uid in player.hand if int(uid) != int(chosen.card_uid)]
                if len(candidates) < cost_to_pay:
                    return None
                picks: List[int] = []
                remaining = list(candidates)
                if requires_symbol:
                    target_sym = fish.normalize_symbol(required_symbol)
                    if target_sym in {"", "n/a"}:
                        card = gs.card_db.get(play_face_uid)
                        if card is None:
                            return None
                        target_sym = fish.normalize_symbol(card.symbol)
                    if target_sym in {"", "n/a"}:
                        return None
                    match_uid = next((uid for uid in remaining if fish.symbol_match_for_entry(ms, gs, uid, target_sym)), None)
                    if match_uid is None:
                        return None
                    picks.append(match_uid)
                    remaining = [uid for uid in remaining if uid != match_uid]
                for uid in remaining:
                    if len(picks) >= cost_to_pay:
                        break
                    picks.append(uid)
                if not valid_payment_set(picks):
                    return None
                return picks

            if cost_to_pay <= 0:
                chosen.payment_uids = []
            elif valid_payment_set(provided_payments):
                chosen.payment_uids = list(provided_payments)
            else:
                auto_payments = auto_payment_set()
                if auto_payments is None:
                    return None
                chosen.payment_uids = auto_payments
        elif isinstance(pay_raw, list):
            chosen.payment_uids = provided_payments
        return chosen

    def _resolve_action_by_identity(self, actions: List[fish.Action], cmd: Dict[str, Any]) -> Optional[fish.Action]:
        for action in actions:
            if self._action_matches_identity(action, cmd):
                return copy.deepcopy(action)
        return None

    def _action_matches_identity(self, action: fish.Action, cmd: Dict[str, Any]) -> bool:
        kind = cmd.get("kind")
        if isinstance(kind, str) and kind and action.kind != kind:
            return False

        for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
            raw = cmd.get(key)
            if not isinstance(raw, int):
                continue
            if key == "face_uid":
                action_value = action.face_uid if action.face_uid is not None else action.card_uid
            elif key in {"ocean_uid", "source_ocean_uid"}:
                action_value = action.ocean_uid
                if key == "source_ocean_uid":
                    action_value = getattr(action, "source_ocean_uid", None)
            elif key == "draw_from_pool":
                action_value = int(action.draw_from_pool)
            else:
                action_value = getattr(action, key, None)
            # Backward compatibility for older clients that serialized null ocean_uid as 0.
            if key == "ocean_uid" and action_value is None and int(raw) == 0:
                continue
            if action_value is None:
                return False
            if int(action_value) != int(raw):
                return False

        use_star = cmd.get("use_star")
        if isinstance(use_star, bool) and bool(action.use_star) != bool(use_star):
            return False
        return True

    def _action_payment_requirements(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        action: fish.Action,
    ) -> tuple[int, bool, str]:
        if hasattr(fish, "action_payment_requirements"):
            try:
                return fish.action_payment_requirements(gs, ms, player, action)
            except Exception:
                pass

        if action.kind not in {"play_ocean", "play_to_ocean"}:
            return 0, False, ""

        face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        card = gs.card_db.get(face_uid)
        if card is None:
            return 0, False, ""

        free_play = False
        if hasattr(fish, "is_free_play_eligible"):
            try:
                free_play = bool(fish.is_free_play_eligible(player, card))
            except Exception:
                free_play = False

        cost_to_pay = 0 if free_play else max(0, int(getattr(card, "cost", 0)))
        if not bool(action.use_star):
            return cost_to_pay, False, ""

        has_star = False
        if hasattr(fish, "has_star_ability"):
            try:
                has_star = bool(fish.has_star_ability(card))
            except Exception:
                has_star = False
        if not has_star or cost_to_pay <= 0:
            return cost_to_pay, False, ""

        sym = fish.normalize_symbol(getattr(card, "symbol", ""))
        if sym in {"", "n/a"}:
            return cost_to_pay, False, ""
        return cost_to_pay, True, sym

    def _record_event(self, msg: str) -> None:
        with self.cond:
            self.log_events.append(msg)
            self.training_events.append(msg)
            if len(self.log_events) > 500:
                del self.log_events[:-500]
            if len(self.training_events) > 4000:
                del self.training_events[:-4000]
            self._bump_locked()

    def _record_snapshot(self, gs: fish.GameState, ms: fish.MatchState, turn_number: int, note: str) -> None:
        players_public: List[Dict[str, Any]] = []
        private_hands: Dict[int, List[Dict[str, Any]]] = {}
        scores_now: Dict[str, int] = {}

        for idx, p in enumerate(gs.players):
            try:
                full_breakdown = fish.full_score_breakdown(gs, p)
                score = int(full_breakdown.get("total", 0))
            except Exception:
                try:
                    score = int(fish.final_points(gs, p))
                except Exception:
                    score = int(getattr(p, "score", 0))
                full_breakdown = {
                    "total": score,
                    "rows": [],
                    "error": "score_breakdown_failed",
                }
            try:
                mandarin_payload = fish.mandarin_goby_score_breakdown(gs, p, precomputed_full=full_breakdown)
            except Exception:
                mandarin_payload = {
                    "count": 0,
                    "points": 0,
                    "table": {"1": 0, "2": 15, "3": 30, "4+": 80},
                    "total_score": score,
                    "error": "mandarin_breakdown_failed",
                }
            try:
                board_payload = board_to_dict(p, gs)
            except Exception:
                board_payload = []
            scores_now[p.name] = score
            players_public.append(
                {
                    "index": idx,
                    "name": p.name,
                    "score": score,
                    "score_breakdown": {
                        "full": full_breakdown,
                        "mandarin_goby": mandarin_payload,
                    },
                    "hand_count": len(p.hand),
                    "board_ocean_count": len(p.board_oceans),
                    "board": board_payload,
                }
            )
            hand_payload: List[Dict[str, Any]] = []
            for uid in list(p.hand):
                if not isinstance(uid, int):
                    continue
                hand_payload.append(entry_to_dict(ms, gs, uid))
            private_hands[idx] = hand_payload

        pool_payload = [entry_to_dict(ms, gs, uid) for uid in list(ms.pool) if isinstance(uid, int)]
        current_player = None
        if gs.players:
            try:
                current_idx = int(gs.turn_index) % len(gs.players)
            except Exception:
                current_idx = 0
            if 0 <= current_idx < len(gs.players):
                current_player = gs.players[current_idx].name

        training_players: List[Dict[str, Any]] = []
        for p in gs.players:
            try:
                score_now = int(fish.final_points(gs, p))
            except Exception:
                score_now = int(getattr(p, "score", 0))
            board_slots: Dict[str, Dict[str, List[int]]] = {}
            for ocean_uid in list(p.board_oceans):
                slots = p.ocean_slots.get(ocean_uid)
                if not isinstance(slots, fish.OceanSlots):
                    continue
                board_slots[str(ocean_uid)] = {
                    "up": [int(uid) for uid in list(getattr(slots, "up", [])) if isinstance(uid, int)],
                    "down": [int(uid) for uid in list(getattr(slots, "down", [])) if isinstance(uid, int)],
                    "left": [int(uid) for uid in list(getattr(slots, "left", [])) if isinstance(uid, int)],
                    "right": [int(uid) for uid in list(getattr(slots, "right", [])) if isinstance(uid, int)],
                }
            training_players.append(
                {
                    "name": p.name,
                    "score": score_now,
                    "hand_entry_uids": [int(uid) for uid in list(p.hand) if isinstance(uid, int)],
                    "board_oceans": [int(uid) for uid in list(p.board_oceans) if isinstance(uid, int)],
                    "board_slots": board_slots,
                }
            )

        public_state = {
            "seed": self.seed,
            "turn_number": int(turn_number),
            "turn_index": int(gs.turn_index),
            "round_count": int(gs.round_count),
            "current_player": current_player,
            "note": note,
            "deck_remaining": len(gs.deck),
            "pool_count": len(ms.pool),
            "discard_pile_count": len(ms.discard_pile),
            "pool": pool_payload,
            "players": players_public,
            "end_game": {
                "triggered": bool(ms.end_game_triggered),
                "final_turns_remaining": int(ms.final_turns_remaining),
                "end_game_uid": ms.end_game_uid,
            },
        }

        training_snapshot = {
            "turn_number": int(turn_number),
            "note": note,
            "deck_remaining": len(gs.deck),
            "pool_entry_uids": [int(uid) for uid in list(ms.pool) if isinstance(uid, int)],
            "discard_count": len(ms.discard_pile),
            "players": training_players,
        }

        summary: Optional[Dict[str, Any]] = None
        if note.startswith("turn_end:"):
            ended_player = note.split(":", 1)[1] if ":" in note else ""
            deltas = {
                name: int(score - self._last_turn_scores.get(name, score))
                for name, score in scores_now.items()
            }
            summary = {
                "turn_number": int(turn_number),
                "player": ended_player,
                "score_deltas": deltas,
                "pool_count": len(ms.pool),
                "ocean_counts": {p.name: len(p.board_oceans) for p in gs.players},
            }
            self._last_turn_scores = scores_now

        with self.cond:
            self.latest_public_state = public_state
            self.latest_private_hands = private_hands
            self.last_turn_number = int(turn_number)

            self.training_snapshots.append(training_snapshot)
            if len(self.training_snapshots) > 1200:
                del self.training_snapshots[:-1200]

            if summary is not None:
                self.turn_summaries.append(summary)
                if len(self.turn_summaries) > 200:
                    del self.turn_summaries[:-200]

            self._bump_locked()

    def _reset_tracking(self, gs: fish.GameState, ms: fish.MatchState) -> None:
        with self.cond:
            self.log_events = [f"Live game log (seed={self.seed})"]
            self.turn_summaries = []
            self.training_events = []
            self.training_snapshots = []
            if not self.recovery_active:
                self.latest_public_state = None
                self.latest_private_hands = {}
            self.legal_actions_by_seat.clear()
            self.pending_actions.clear()
            self.active_action_seat = None
            self._last_turn_scores = {}
            if self.recovery_active:
                self.status_note = (
                    f"Recovering game state ({self.recovery_cursor}/{self.recovery_target_count})..."
                )
            else:
                self.status_note = "Game running."
            self._bump_locked()

    def _human_policy(self, seat_index: int):
        def policy(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            while True:
                actions = fish.legal_actions(gs, ms, player, include_draw=True)
                replay_action = self._replay_action_if_available(gs, ms, player, seat_index, actions)
                if replay_action is not None:
                    return replay_action

                legal_payload = self._serialize_legal_actions(gs, ms, player, actions)
                with self.cond:
                    if self.phase != "running":
                        return None
                    self.legal_actions_by_seat[seat_index] = legal_payload
                    self.active_action_seat = seat_index
                    only_discards = bool(actions) and all(a.kind == "discard_to_pool" for a in actions)
                    if only_discards:
                        self.status_note = (
                            f"{player.name} must discard to pool until hand is 10 cards."
                        )
                    else:
                        self.status_note = f"Waiting for action from {player.name}."
                    self._bump_locked()

                if not actions:
                    with self.cond:
                        self.legal_actions_by_seat.pop(seat_index, None)
                        if self.active_action_seat == seat_index:
                            self.active_action_seat = None
                        self.status_note = f"{player.name}'s turn ended."
                        self._bump_locked()
                    return None

                cmd = self._wait_for_action(seat_index)
                if cmd is None:
                    return None

                chosen = self._resolve_submitted_action(gs, ms, player, actions, cmd)
                if chosen is None:
                    with self.cond:
                        self.status_note = "Invalid action submitted. Try again."
                        self._bump_locked()
                    continue
                return chosen

        return policy

    def _build_ai_policy(
        self,
        seat_index: int,
        weights: Dict[str, float],
        synergy_map: Dict[str, float],
        species_map: Dict[str, float],
        same_ocean_map: Dict[str, float],
        strategy_value_map: Dict[str, float],
        strategy_count_map: Dict[str, int],
        strategy_transition_map: Dict[str, float],
        strategy_transition_count_map: Dict[str, int],
    ):
        def policy(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            actions = fish.legal_actions(gs, ms, player, include_draw=True)
            replay_action = self._replay_action_if_available(gs, ms, player, seat_index, actions)
            if replay_action is not None:
                return replay_action

            chosen = choose_action_weighted_light(
                gs,
                ms,
                player,
                weights,
                synergy_map,
                species_map,
                same_ocean_map,
                strategy_value_map,
                strategy_count_map,
                strategy_transition_map,
                strategy_transition_count_map,
            )
            return chosen

        return policy

    def _safe_fallback_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
    ) -> Optional[fish.Action]:
        try:
            actions = fish.legal_actions(gs, ms, player, include_draw=True)
        except Exception:
            return None
        if not actions:
            return None
        # Prefer a plain deck draw fallback when available because it needs no
        # extra card selection and is robust under partial client desync.
        for action in actions:
            if action.kind == "draw" and int(getattr(action, "draw_from_pool", 0)) == 0:
                return action
        return actions[0]

    def _wrap_policy_with_fallback(self, seat_label: str, base_policy):
        def wrapped(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            try:
                return base_policy(gs, ms, player)
            except Exception as exc:
                self._record_event(
                    f"Policy error on {seat_label} ({player.name}): {exc}. "
                    "Using safe fallback action."
                )
                return self._safe_fallback_action(gs, ms, player)

        return wrapped

    def _build_training_record(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        standings: List[Dict[str, Any]],
        human_indices: set[int],
    ) -> Dict[str, Any]:
        top_score = float(standings[0]["score"]) if standings else 0.0
        low_score = float(standings[-1]["score"]) if standings else 0.0
        spread = top_score - low_score
        winner_name = standings[0]["name"] if standings else "N/A"
        winner_index = next((i for i, p in enumerate(gs.players) if p.name == winner_name), -1)
        winner_is_human = winner_index in human_indices

        score_bits = []
        if top_score >= 120:
            score_bits.append("high_top_score")
        if spread >= 30:
            score_bits.append("large_score_spread")
        if winner_is_human:
            score_bits.append("human_winner")

        valuable = bool(score_bits) and (winner_is_human or top_score >= 140)

        key_turns = [
            s
            for s in self.turn_summaries
            if any(int(v) > 0 for v in s.get("score_deltas", {}).values())
        ]

        winner_combo_pattern: Dict[str, int] = {}
        if 0 <= winner_index < len(gs.players):
            wp = gs.players[winner_index]
            for uid in fish.player_board_face_uids(wp):
                card = gs.card_db.get(uid)
                if card is None:
                    continue
                nm = card.name
                winner_combo_pattern[nm] = winner_combo_pattern.get(nm, 0) + 1

        record = {
            "recorded_unix": now_unix(),
            "room_id": self.room_id,
            "seed": int(self.seed),
            "config": {
                "total_players": self.total_players,
                "human_players": self.human_players,
                "ai_players": self.ai_players,
            },
            "standings": standings,
            "winner": winner_name,
            "top_score": top_score,
            "score_spread": spread,
            "valuable": valuable,
            "valuable_reasons": score_bits,
            "move_log": list(self.training_events),
            "turn_summaries": list(self.turn_summaries),
            "key_turns": key_turns,
            "snapshots": list(self.training_snapshots),
            "winner_combo_pattern": winner_combo_pattern,
            "deck_remaining": len(gs.deck),
            "pool_count": len(ms.pool),
            "discard_count": len(ms.discard_pile),
            "human_indices": sorted(int(i) for i in human_indices),
            "has_human_players": bool(human_indices),
        }
        return record

    def _append_training_record(self, record: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
        row = json.dumps(record, separators=(",", ":"))
        with DATASET_LOCK:
            with open(DATASET_PATH, "a", encoding="utf-8") as f:
                f.write(row + "\n")

    def _run_game_thread(self, card_db: Dict[int, fish.CardDef]) -> None:
        recorder = RoomLiveRecorder(self)
        try:
            with BRAIN_LOCK:
                try:
                    brain = fish.load_brain(fish.BRAIN_PATH)
                except Exception as exc:
                    brain = {"weights": fish.default_weights()}
                    self._record_event(f"Brain load warning: {exc}. Continuing with default live weights.")

            use_history = fish.use_historical_policy_bias()
            ai_weights = dict(fish.default_weights())
            if use_history:
                ai_weights.update(brain.get("weights", {}))
            ai_weights = fish.stabilize_weights(ai_weights)

            synergy_map = brain.get("synergy", {}) if use_history and isinstance(brain.get("synergy"), dict) else {}
            species_map = (
                brain.get("species_synergy", {}) if use_history and isinstance(brain.get("species_synergy"), dict) else {}
            )
            same_ocean_map = (
                brain.get("same_ocean_synergy", {})
                if use_history and isinstance(brain.get("same_ocean_synergy"), dict)
                else {}
            )
            strategy_value_map = (
                brain.get("strategy_value", {}) if use_history and isinstance(brain.get("strategy_value"), dict) else {}
            )
            strategy_count_map = (
                brain.get("strategy_count", {}) if use_history and isinstance(brain.get("strategy_count"), dict) else {}
            )
            strategy_transition_map = (
                brain.get("strategy_transition", {})
                if use_history and isinstance(brain.get("strategy_transition"), dict)
                else {}
            )
            strategy_transition_count_map = (
                brain.get("strategy_transition_count", {})
                if use_history and isinstance(brain.get("strategy_transition_count"), dict)
                else {}
            )

            player_names: List[str] = []
            policies = []
            human_indices: set[int] = set()
            for seat in self.seats:
                if seat.kind == "human":
                    human_indices.add(seat.index)
                    player_names.append(seat.claimed_name or seat.label)
                    human_policy = self._human_policy(seat.index)
                    policies.append(self._wrap_policy_with_fallback(seat.claimed_name or seat.label, human_policy))
                else:
                    player_names.append(seat.claimed_name or seat.label)
                    ai_policy = self._build_ai_policy(
                        seat.index,
                        ai_weights,
                        synergy_map,
                        species_map,
                        same_ocean_map,
                        strategy_value_map,
                        strategy_count_map,
                        strategy_transition_map,
                        strategy_transition_count_map,
                    )
                    policies.append(
                        self._wrap_policy_with_fallback(seat.claimed_name or seat.label, ai_policy)
                    )

            gs, ms = fish.run_match(
                card_db=card_db,
                player_names=player_names,
                action_policies=policies,
                seed=self.seed,
                max_turns=260,
                human_index=None,
                human_indices=human_indices,
                verbose=False,
                verbose_state=False,
                online_weights=None,
                online_state=None,
                online_state_path=None,
                live_recorder=recorder,
            )

            def _safe_score(player: fish.PlayerState) -> int:
                try:
                    return int(fish.final_points(gs, player))
                except Exception:
                    return int(getattr(player, "score", 0))

            standings = [
                {"name": p.name, "score": _safe_score(p)}
                for p in sorted(gs.players, key=lambda x: _safe_score(x), reverse=True)
            ]
            winner_name = standings[0]["name"] if standings else None

            human_game = bool(human_indices)
            training_record: Dict[str, Any]
            try:
                training_record = self._build_training_record(gs, ms, standings, human_indices)
                self._append_training_record(training_record)
            except Exception as exc:
                training_record = {"valuable": False}
                self._record_event(f"Training record warning: {exc}")

            if human_game:
                # Learn from real human gameplay in every live game.
                # For mixed human+AI tables, avoid full-match updates that may amplify AI-only patterns.
                valuable = bool(training_record.get("valuable"))
                human_names = {
                    gs.players[i].name
                    for i in human_indices
                    if 0 <= i < len(gs.players)
                }
                winner_is_human = bool(winner_name and winner_name in human_names)
                human_only_game = len(human_indices) == len(gs.players)
                demo_boost = 2.4 if valuable else 1.2
                if winner_is_human:
                    demo_boost += 0.25
                if human_only_game:
                    demo_boost += 0.2
                try:
                    with BRAIN_LOCK:
                        brain2 = fish.load_brain(fish.BRAIN_PATH)
                        if human_only_game:
                            # Human-only games can safely use full winner/loser match updates.
                            fish.update_brain_from_match(gs, brain2)
                        fish.reinforce_human_demo_from_board(gs, human_indices, brain2, boost=demo_boost)
                        fish.save_brain(brain2, fish.BRAIN_PATH)
                    learn_mode = "full_match+human_demo" if human_only_game else "human_demo_only"
                    self._record_event(
                        f"AI learned from {len(human_indices)} real human player(s) "
                        f"(mode={learn_mode}, boost={demo_boost:.2f})."
                    )
                except Exception as exc:
                    self._record_event(f"Human-learning warning: {exc}")

            with self.cond:
                self.phase = "ended"
                self.ended_unix = now_unix()
                self.status_note = "Game ended."
                self.final_scores = standings
                self.winner = winner_name
                self.active_action_seat = None
                self.legal_actions_by_seat.clear()
                self._bump_locked(force_persist=True)

        except Exception as exc:
            trace = traceback.format_exc(limit=20)
            with self.cond:
                self.phase = "error"
                self.error_message = f"{exc}"
                self.status_note = "Game error."
                self.log_events.append(trace)
                self.active_action_seat = None
                self.legal_actions_by_seat.clear()
                self._bump_locked(force_persist=True)

    def _viewer_payload_locked(self, seat: Optional[Seat]) -> Dict[str, Any]:
        if seat is None:
            return {
                "seat_index": None,
                "seat_kind": None,
                "display_name": None,
                "is_host": False,
                "can_act": False,
            }
        return {
            "seat_index": seat.index,
            "seat_kind": seat.kind,
            "display_name": seat.claimed_name or seat.label,
            "is_host": bool(seat.is_host),
            "can_act": bool(self.phase == "running" and self.active_action_seat == seat.index),
        }

    def state_view(
        self,
        seat_token: Optional[str],
        host_header: str,
        proto_hint: str = "",
        host_token: str = "",
    ) -> Dict[str, Any]:
        with self.cond:
            viewer_seat = self._seat_from_token_locked(seat_token)
            if viewer_seat is None and host_token:
                if secrets.compare_digest(self.host_control_token, host_token):
                    viewer_seat = self.host_seat()
            viewer_index = viewer_seat.index if viewer_seat is not None else None

            state_obj = copy.deepcopy(self.latest_public_state) if isinstance(self.latest_public_state, dict) else None
            if isinstance(state_obj, dict):
                players_list = state_obj.get("players")
                if not isinstance(players_list, list):
                    players_list = []
                    state_obj["players"] = players_list
                if viewer_index is not None:
                    for p in players_list:
                        if not isinstance(p, dict):
                            continue
                        idx = p.get("index")
                        if idx == viewer_index:
                            p["hand"] = copy.deepcopy(self.latest_private_hands.get(idx, []))
                        else:
                            p["hand"] = []
                else:
                    for p in players_list:
                        if isinstance(p, dict):
                            p["hand"] = []

            legal_payload: Optional[Dict[str, Any]] = None
            if viewer_index is not None:
                legal_payload = copy.deepcopy(self.legal_actions_by_seat.get(viewer_index))

            payload = {
                "ok": True,
                "version": self.state_version,
                "room": {
                    "room_id": self.room_id,
                    "phase": self.phase,
                    "created_unix": self.created_unix,
                    "started_unix": self.started_unix,
                    "ended_unix": self.ended_unix,
                    "total_players": self.total_players,
                    "human_players": self.human_players,
                    "ai_players": self.ai_players,
                    "share_url": self.room_link(host_header, proto_hint),
                },
                "status_note": self.status_note,
                "error": self.error_message,
                "public_links": load_public_links(),
                "seats": self.seat_snapshot_locked(),
                "viewer": self._viewer_payload_locked(viewer_seat),
                "can_start": bool(self.phase == "lobby" and self._all_humans_claimed_locked()),
                "state": state_obj,
                "legal_actions": legal_payload,
                "active_action_seat": self.active_action_seat,
                "log_tail": self.log_events[-120:],
                "turn_summaries": self.turn_summaries[-80:],
                "final_scores": self.final_scores,
                "winner": self.winner,
                "recovery": {
                    "active": bool(self.recovery_active),
                    "cursor": int(self.recovery_cursor),
                    "target_count": int(self.recovery_target_count),
                    "error": self.recovery_error,
                },
            }
            return payload

    def wait_for_update(self, last_version: int, timeout_sec: float) -> bool:
        with self.cond:
            if self.state_version != last_version:
                return True
            self.cond.wait(timeout=timeout_sec)
            return self.state_version != last_version


class RoomLiveRecorder:
    def __init__(self, room: GameRoom) -> None:
        self.room = room

    def event(self, msg: str) -> None:
        try:
            self.room._record_event(msg)
        except Exception:
            return

    def executed_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        turn_number: int,
    ) -> None:
        _ = gs, ms
        self.room.record_executed_action(
            seat_index=int(seat_index),
            player_name=player_name,
            action=action,
            turn_number=int(turn_number),
        )

    def snapshot(self, gs: fish.GameState, ms: fish.MatchState, turn_number: int, note: str) -> None:
        try:
            self.room._record_snapshot(gs, ms, turn_number=turn_number, note=note)
        except Exception as exc:
            self.event(f"Snapshot warning: {exc}")

    def reset(self, gs: fish.GameState, ms: fish.MatchState) -> None:
        try:
            self.room._reset_tracking(gs, ms)
        except Exception:
            return
        self.event(f"Players: {', '.join(p.name for p in gs.players)}")
        try:
            pool_label = fish.short_entry_list(ms, gs, ms.pool)
        except Exception:
            pool_label = "(unavailable)"
        self.event(f"Opening pool: {pool_label}")
        self.snapshot(gs, ms, turn_number=0, note="game_start")


class RoomManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rooms: Dict[str, GameRoom] = {}

    def _active_room_locked(self) -> Optional[GameRoom]:
        for room in self.rooms.values():
            if room.phase in {"lobby", "running"}:
                return room
        return None

    def create_room(
        self,
        host_name: str,
        total_players: int,
        human_players: int,
        ai_players: int,
        requested_room_id: Optional[str] = None,
        replace_active: bool = False,
    ) -> GameRoom:
        with self.lock:
            active = self._active_room_locked()
            if active is not None:
                if replace_active:
                    if active.phase != "lobby":
                        raise RuntimeError(f"active room is {active.phase}; cannot replace ({active.room_id})")
                    self.rooms.pop(active.room_id, None)
                    remove_room_state_file(active.room_id)
                else:
                    raise RuntimeError(f"active room already exists ({active.room_id})")
            if requested_room_id is not None:
                rid = str(requested_room_id).strip().upper()
                if not ROOM_ID_RE.fullmatch(rid):
                    raise ValueError(f"room_id must be exactly {ROOM_ID_LENGTH} uppercase letters/numbers")
                if rid in self.rooms:
                    existing = self.rooms.get(rid)
                    if existing is not None and existing.phase in {"ended", "error"}:
                        self.rooms.pop(rid, None)
                        remove_room_state_file(rid)
                    else:
                        raise RuntimeError(f"room id already exists ({rid})")
                room = GameRoom(rid, host_name, total_players, human_players, ai_players)
                self.rooms[rid] = room
                room.persist_now()
                return room
            for _ in range(100):
                rid = room_code(ROOM_ID_LENGTH)
                if rid not in self.rooms:
                    room = GameRoom(rid, host_name, total_players, human_players, ai_players)
                    self.rooms[rid] = room
                    room.persist_now()
                    return room
        raise RuntimeError("unable to allocate room code")

    def get(self, room_id: str) -> Optional[GameRoom]:
        with self.lock:
            return self.rooms.get(room_id)

    def active_room(self) -> Optional[GameRoom]:
        with self.lock:
            return self._active_room_locked()

    def _active_rank(self, room: GameRoom) -> tuple[int, int]:
        started = int(room.started_unix or 0)
        created = int(room.created_unix or 0)
        return started, created

    def load_persisted_rooms(self, card_db: Dict[int, fish.CardDef]) -> Dict[str, int]:
        loaded = 0
        resumed = 0
        skipped = 0
        failed = 0

        candidates: List[GameRoom] = []
        seen_ids: set[str] = set()
        for path in list_room_state_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                failed += 1
                continue
            room = GameRoom.from_checkpoint(payload if isinstance(payload, dict) else {})
            if room is None:
                failed += 1
                continue
            if room.room_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(room.room_id)
            candidates.append(room)

        keep_active_id: Optional[str] = None
        active_candidates = [room for room in candidates if room.phase in {"lobby", "running"}]
        if active_candidates:
            keep_active_id = max(active_candidates, key=self._active_rank).room_id

        resume_rooms: List[GameRoom] = []
        with self.lock:
            for room in candidates:
                if room.phase in {"lobby", "running"} and keep_active_id and room.room_id != keep_active_id:
                    room.phase = "ended"
                    room.ended_unix = room.ended_unix or now_unix()
                    room.status_note = "Archived room from previous server session."
                if room.room_id in self.rooms:
                    skipped += 1
                    continue
                self.rooms[room.room_id] = room
                loaded += 1
                if room.phase == "running":
                    resume_rooms.append(room)

        for room in resume_rooms:
            out = room.resume_after_restore(card_db)
            if out.get("ok"):
                resumed += 1
            else:
                failed += 1

        for room in candidates:
            try:
                room.persist_now()
            except Exception:
                continue

        return {"loaded": loaded, "resumed": resumed, "skipped": skipped, "failed": failed}


ROOMS = RoomManager()


class StableThreadingHTTPServer(ThreadingHTTPServer):
    # Avoid hanging shutdown on slow clients and allow quick restarts on the same port.
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class MultiplayerHandler(SimpleHTTPRequestHandler):
    server_version = "FishMultiplayer/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _apply_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", CORS_ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_client_asset(
        self,
        file_path: str,
        content_type: Optional[str] = None,
        cache_control: str = "public, max-age=300",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not os.path.exists(file_path):
            self._send_json({"ok": False, "error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "asset not readable"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        guessed_type, _ = mimetypes.guess_type(file_path)
        ctype = content_type or guessed_type or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(raw)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(str(key), str(value))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json_dumps(payload)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._apply_cors_headers()
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_index_html(self) -> None:
        try:
            with open(CLIENT_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "client index missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_cors_headers()
        # Keep clients in sync so all players render the same UI version.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_website_html(self) -> None:
        try:
            with open(WEBSITE_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "website page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_rules_html(self) -> None:
        try:
            with open(RULES_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "rules page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_about_html(self) -> None:
        try:
            with open(ABOUT_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "about page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _read_json_body(self) -> tuple[Dict[str, Any], Optional[str]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 0:
            return {}, "invalid Content-Length"
        if size > MAX_JSON_BODY_BYTES:
            try:
                # Drain a small prefix to keep the connection state sane.
                self.rfile.read(min(size, 4096))
            except Exception:
                pass
            return {}, f"request body too large (max {MAX_JSON_BODY_BYTES} bytes)"

        raw = self.rfile.read(size) if size > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}, "invalid JSON body"
        if not isinstance(body, dict):
            return {}, "JSON body must be an object"
        return body, None

    def _path_parts(self, path: str) -> List[str]:
        return [p for p in path.strip("/").split("/") if p]

    def _room_from_parts(self, parts: List[str]) -> Optional[GameRoom]:
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "rooms":
            return None
        return ROOMS.get(parts[2])

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = self._path_parts(parsed.path)

        if parsed.path == "/manifest.webmanifest":
            self._send_client_asset(MANIFEST_PATH, content_type="application/manifest+json; charset=utf-8")
            return

        if parsed.path == "/sw.js":
            self._send_client_asset(
                SERVICE_WORKER_PATH,
                content_type="application/javascript; charset=utf-8",
                cache_control="no-store",
                extra_headers={"Service-Worker-Allowed": "/"},
            )
            return

        if parsed.path == "/icon.svg":
            self._send_client_asset(ICON_PATH, content_type="image/svg+xml")
            return

        if parsed.path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "time": now_unix(),
                    "create_key_required": bool(CREATE_KEY),
                    "public_urls": load_public_links(),
                }
            )
            return

        if parsed.path == "/api/public-links":
            self._send_json(
                {
                    "ok": True,
                    "updated_unix": now_unix(),
                    "public_urls": load_public_links(),
                }
            )
            return

        if len(parts) == 2 and parts[0] == "play":
            if os.path.exists(CLIENT_INDEX_PATH):
                self._send_index_html()
                return
            self._send_json({"ok": False, "error": "client index missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] in {"website", "site"}:
            if os.path.exists(WEBSITE_INDEX_PATH):
                self._send_website_html()
                return
            self._send_json({"ok": False, "error": "website page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] == "rules":
            if os.path.exists(RULES_INDEX_PATH):
                self._send_rules_html()
                return
            self._send_json({"ok": False, "error": "rules page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] == "about":
            if os.path.exists(ABOUT_INDEX_PATH):
                self._send_about_html()
                return
            self._send_json({"ok": False, "error": "about page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if parsed.path == "/" and os.path.exists(CLIENT_INDEX_PATH):
            self._send_index_html()
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "state":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            qs = parse_qs(parsed.query)
            seat_token = qs.get("seat_token", [None])[0]
            host_token = qs.get("host_token", [None])[0]
            host_header = self.headers.get("Host", "127.0.0.1:8777")
            proto_hint = self.headers.get("X-Forwarded-Proto", "")
            self._send_json(
                room.state_view(
                    seat_token if isinstance(seat_token, str) else None,
                    host_header,
                    proto_hint,
                    host_token if isinstance(host_token, str) else "",
                )
            )
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "stream":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            qs = parse_qs(parsed.query)
            seat_token = qs.get("seat_token", [None])[0]
            host_token = qs.get("host_token", [None])[0]
            self._serve_sse(
                room,
                seat_token if isinstance(seat_token, str) else None,
                host_token if isinstance(host_token, str) else "",
            )
            return

        super().do_GET()

    def _serve_sse(self, room: GameRoom, seat_token: Optional[str], host_token: str = "") -> None:
        host_header = self.headers.get("Host", "127.0.0.1:8777")
        proto_hint = self.headers.get("X-Forwarded-Proto", "")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_version = -1
        try:
            while True:
                payload = room.state_view(seat_token, host_header, proto_hint, host_token)
                version = int(payload.get("version", 0))
                if version != last_version:
                    data = json.dumps(payload, separators=(",", ":"))
                    chunk = f"event: state\ndata: {data}\n\n".encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    last_version = version

                changed = room.wait_for_update(last_version, timeout_sec=5.0)
                if not changed:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = self._path_parts(parsed.path)
        body, body_error = self._read_json_body()
        if body_error:
            self._send_json({"ok": False, "error": body_error}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/server/stop":
            room_id = body.get("room_id") if isinstance(body.get("room_id"), str) else ""
            if not room_id:
                self._send_json({"ok": False, "error": "room_id required"}, status=HTTPStatus.BAD_REQUEST)
                return
            room = ROOMS.get(room_id)
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            if not room.is_host_authorized(host_token, seat_token):
                self._send_json({"ok": False, "error": "host authorization required"}, status=HTTPStatus.FORBIDDEN)
                return

            server = ACTIVE_SERVER
            if server is None:
                self._send_json({"ok": False, "error": "server not active"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json({"ok": True, "status": "stopping"})

            def _shutdown_later() -> None:
                time.sleep(0.12)
                try:
                    server.shutdown()
                except Exception:
                    pass

            threading.Thread(target=_shutdown_later, daemon=True).start()
            return

        if parsed.path == "/api/rooms":
            create_key = body.get("create_key") if isinstance(body.get("create_key"), str) else ""
            if not CREATE_KEY or not secrets.compare_digest(create_key, CREATE_KEY):
                self._send_json({"ok": False, "error": "host create key required"}, status=HTTPStatus.FORBIDDEN)
                return

            total_players = clamp_int(body.get("total_players"), 4, 2, 8)
            human_players = clamp_int(body.get("human_players"), 2, 1, 8)
            ai_players = clamp_int(body.get("ai_players"), total_players - human_players, 0, 8)
            if human_players + ai_players != total_players:
                self._send_json(
                    {"ok": False, "error": "human_players + ai_players must equal total_players"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            host_name = safe_name(body.get("host_name"), "Host")
            requested_room_id = body.get("room_id") if isinstance(body.get("room_id"), str) else None
            replace_active = bool(body.get("replace_active")) if isinstance(body.get("replace_active"), bool) else False
            try:
                room = ROOMS.create_room(
                    host_name,
                    total_players,
                    human_players,
                    ai_players,
                    requested_room_id=requested_room_id,
                    replace_active=replace_active,
                )
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except RuntimeError as exc:
                active = ROOMS.active_room()
                out: Dict[str, Any] = {"ok": False, "error": str(exc)}
                if active is not None:
                    host_header = self.headers.get("Host", "127.0.0.1:8777")
                    proto_hint = self.headers.get("X-Forwarded-Proto", "")
                    out["active_room_id"] = active.room_id
                    out["active_phase"] = active.phase
                    out["active_share_url"] = active.room_link(host_header, proto_hint)
                self._send_json(out, status=HTTPStatus.CONFLICT)
                return
            host_seat = room.host_seat()
            if host_seat is None:
                self._send_json({"ok": False, "error": "failed to create host seat"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            host_header = self.headers.get("Host", "127.0.0.1:8777")
            proto_hint = self.headers.get("X-Forwarded-Proto", "")
            self._send_json(
                {
                    "ok": True,
                    "room_id": room.room_id,
                    "play_path": f"/play/{room.room_id}",
                    "share_url": room.room_link(host_header, proto_hint),
                    "public_links": load_public_links(),
                    "host_token": room.host_control_token,
                    "seat_token": host_seat.token,
                    "seat_index": host_seat.index,
                }
            )
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "join":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            seat_index_raw = body.get("seat_index")
            seat_index = int(seat_index_raw) if isinstance(seat_index_raw, int) else None
            player_name = safe_name(body.get("player_name"), "Player")
            existing_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            allow_takeover = bool(body.get("takeover")) if isinstance(body.get("takeover"), bool) else False
            req_create_key = body.get("create_key") if isinstance(body.get("create_key"), str) else ""
            allow_host_takeover = bool(
                (CREATE_KEY and secrets.compare_digest(req_create_key, CREATE_KEY))
                or room.is_host_authorized(host_token, existing_token)
            )
            out = room.claim_seat(
                player_name,
                seat_index,
                existing_token,
                allow_takeover=allow_takeover,
                allow_host_takeover=allow_host_takeover,
            )

            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "start":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            req_create_key = body.get("create_key") if isinstance(body.get("create_key"), str) else ""
            allow_with_create_key = bool(CREATE_KEY and secrets.compare_digest(req_create_key, CREATE_KEY))
            out = room.start_game(host_token, seat_token, CARD_DB, allow_with_create_key=allow_with_create_key)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "restart":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            out = room.restart_game(host_token, seat_token, CARD_DB)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "action":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.submit_action(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)


def main() -> None:
    global PUBLIC_BASE_URL, ACTIVE_SERVER, CREATE_KEY, CORS_ALLOW_ORIGIN
    parser = argparse.ArgumentParser(description="Fish Game multiplayer server")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument(
        "--public-base-url",
        type=str,
        default="",
        help="Optional externally reachable base URL (example: http://192.168.1.50:8777)",
    )
    parser.add_argument(
        "--create-key",
        type=str,
        default="",
        help="Host-only lobby create key. If omitted, a random key is generated at startup.",
    )
    parser.add_argument(
        "--cors-allow-origin",
        type=str,
        default=os.environ.get("FISH_CORS_ALLOW_ORIGIN", "*"),
        help="CORS Access-Control-Allow-Origin value for API/SSE responses (default: *).",
    )
    args = parser.parse_args()
    PUBLIC_BASE_URL = str(args.public_base_url or "").strip()
    raw_key = str(args.create_key or os.environ.get("FISH_CREATE_KEY") or "").strip()
    CREATE_KEY = raw_key if raw_key else secrets.token_urlsafe(16)
    CORS_ALLOW_ORIGIN = str(args.cors_allow_origin or "*").strip() or "*"

    os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
    restore_stats = ROOMS.load_persisted_rooms(CARD_DB)

    ACTIVE_SERVER = StableThreadingHTTPServer((args.host, args.port), MultiplayerHandler)
    print(f"Serving Fish multiplayer on http://{args.host}:{args.port}")
    print(f"Host lobby create key: {CREATE_KEY}")
    print(f"Host setup URL: http://{args.host}:{args.port}/?create_key={CREATE_KEY}")
    print(
        "Recovered rooms: "
        f"{restore_stats.get('loaded', 0)} loaded, "
        f"{restore_stats.get('resumed', 0)} resumed, "
        f"{restore_stats.get('skipped', 0)} skipped, "
        f"{restore_stats.get('failed', 0)} failed."
    )
    try:
        ACTIVE_SERVER.serve_forever()
    finally:
        try:
            ACTIVE_SERVER.server_close()
        except Exception:
            pass
        ACTIVE_SERVER = None


CARD_DB = fish.load_card_db()


if __name__ == "__main__":
    main()
