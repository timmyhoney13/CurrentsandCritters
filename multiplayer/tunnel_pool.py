#!/usr/bin/env python3
"""Keep multiple localhost.run tunnels alive and publish active public URLs."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import List

URL_RE = re.compile(r"\b([a-z0-9]+\.lhr\.life)\s+tunneled with tls termination", re.IGNORECASE)


def atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=True)
    os.replace(tmp, path)


def health_ok(url: str, timeout_sec: float) -> bool:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read(4096).decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return False
    except Exception:
        return False
    txt = str(raw or "").strip()
    if not txt:
        return False
    if "no tunnel here" in txt.lower():
        return False
    try:
        data = json.loads(txt)
    except Exception:
        return False
    return bool(isinstance(data, dict) and data.get("ok") is True)


class TunnelSlot:
    def __init__(self, index: int, ssh_cmd: List[str]) -> None:
        self.index = int(index)
        self.ssh_cmd = list(ssh_cmd)
        self.proc: subprocess.Popen[str] | None = None
        self.url = ""
        self.last_health_unix = 0.0
        self.last_health_ok = False
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

    def _set_url(self, url: str) -> None:
        with self._lock:
            self.url = str(url or "").strip()

    def get_url(self) -> str:
        with self._lock:
            return self.url

    def is_running(self) -> bool:
        p = self.proc
        return bool(p is not None and p.poll() is None)

    def _reader(self) -> None:
        p = self.proc
        if p is None or p.stdout is None:
            return
        buf = ""
        try:
            while True:
                chunk = p.stdout.read(256)
                if not chunk:
                    if p.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                buf += str(chunk)
                if len(buf) > 8192:
                    buf = buf[-8192:]
                m = URL_RE.search(buf)
                if m:
                    self._set_url(f"https://{m.group(1).lower()}")
        except Exception:
            return

    def stop(self) -> None:
        p = self.proc
        self.proc = None
        self._set_url("")
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            return

    def start(self) -> None:
        self.stop()
        self.last_health_unix = 0.0
        self.last_health_ok = False
        p = subprocess.Popen(
            self.ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.proc = p
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain a pool of localhost.run tunnels.")
    parser.add_argument(
        "--key-path",
        action="append",
        required=True,
        help="Path to SSH private key (repeat to provide multiple keys)",
    )
    parser.add_argument("--target-host", default="127.0.0.1", help="Local target host")
    parser.add_argument("--target-port", type=int, default=8777, help="Local target port")
    parser.add_argument("--count", type=int, default=2, help="Number of concurrent tunnels to keep alive")
    parser.add_argument(
        "--output",
        default=os.path.join("multiplayer", "public_links.json"),
        help="Where to write active URLs JSON",
    )
    parser.add_argument("--health-path", default="/api/health", help="Health endpoint path")
    parser.add_argument("--health-interval", type=float, default=6.0, help="Seconds between health checks per tunnel")
    parser.add_argument("--health-timeout", type=float, default=4.0, help="Health request timeout seconds")
    parser.add_argument("--ssh-bin", default="ssh", help="SSH binary")
    return parser.parse_args()


def unique_preserve_order(values: List[str]) -> List[str]:
    out: List[str] = []
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        if s in out:
            continue
        out.append(s)
    return out


def main() -> int:
    args = parse_args()
    key_paths = [str(k).strip() for k in (args.key_path or []) if str(k).strip()]
    if not key_paths:
        raise SystemExit("at least one --key-path is required")

    target = f"80:{args.target_host}:{args.target_port}"
    desired_count = max(1, int(args.count))
    if len(key_paths) == 1 and desired_count > 1:
        # localhost.run can flap when multiple sessions reuse one anonymous key.
        desired_count = 1

    slots: List[TunnelSlot] = []
    for i in range(desired_count):
        key_path = key_paths[i % len(key_paths)]
        ssh_cmd = [
            args.ssh_bin,
            "-v",
            "-i",
            key_path,
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ExitOnForwardFailure=yes",
            "-R",
            target,
            "localhost.run",
        ]
        slots.append(TunnelSlot(i, ssh_cmd))
    stop_event = threading.Event()

    def _stop(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    for slot in slots:
        slot.start()
        time.sleep(0.25)

    last_links: List[str] = []
    last_write_unix = 0.0
    output_path = os.path.abspath(args.output)

    try:
        while not stop_event.is_set():
            changed = False
            now = time.time()
            live_links: List[str] = []

            for slot in slots:
                if not slot.is_running():
                    slot.start()
                    changed = True
                    continue

                url = slot.get_url()
                if url:
                    needs_probe = (not slot.last_health_ok) or (
                        now - slot.last_health_unix >= max(2.0, float(args.health_interval))
                    )
                    if needs_probe:
                        slot.last_health_unix = now
                        probe = f"{url.rstrip('/')}{args.health_path}"
                        slot.last_health_ok = health_ok(probe, timeout_sec=float(args.health_timeout))
                        if not slot.last_health_ok:
                            slot.start()
                            changed = True
                            continue
                    if slot.last_health_ok:
                        live_links.append(url)

            links = unique_preserve_order(live_links)
            should_write = changed or links != last_links or (now - last_write_unix >= 5.0)
            if should_write:
                payload = {
                    "updated_unix": int(now),
                    "public_urls": links,
                }
                atomic_write_json(output_path, payload)
                last_links = links
                last_write_unix = now

            time.sleep(1.0)
    finally:
        for slot in slots:
            slot.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
