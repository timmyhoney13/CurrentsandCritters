"""Squarespace Commerce → Critter Coins / Supporter Tiers.

The Store's buy buttons now go to products on the Squarespace shop
(www.beardedsealstudios.com/shop). Squarespace has no webhook a site owner can
point at us without an approved OAuth app, so this module POLLS the Squarespace
Orders API instead:

    every SQUARESPACE_POLL_SECONDS (default 60)
      → GET /1.0/commerce/orders for everything modified since the last poll
      → each PAID line item that is a Critter Coins pack or a Supporter Tier
        becomes ONE Stripe-shaped "checkout session"
      → handed to the SAME fulfilment the Stripe webhook uses
        (multiplayer_server._process_stripe_checkout)

Reusing that one function is the point: coins, tier perks, the supporter wall,
the unclaimed-reward record, the emailed redemption code and the guest
auto-merge all behave exactly as they did for Stripe. Nothing here grants
anything by itself.

WHO GETS IT
  Squarespace checkout gives us the buyer's email (and, if the product has a
  form asking for it, a typed game username). The buyer is matched:
    1. email → a game account whose Firebase sign-in email is VERIFIED and the
       same address (an unverified address could be someone else's, so it
       never matches), then
    2. a typed "username" form answer → that account, then
    3. otherwise a guest record keyed by email: the buyer is mailed a
       redemption code and can also claim at /claim-rewards.

EXACTLY ONCE
  Polling sees the same order again and again (every overlapping window, and
  again when the order is fulfilled or edited). Each unit of each line item has
  a STABLE id, sqsp_<order>_<line item>_<n>, used as both the session id and
  the event id; the fulfilment transaction refuses an event id it has already
  recorded, so a repeat is a no-op.

SETUP (Render → Environment)
  SQUARESPACE_API_KEY   Developer API key from the MAIN site
                        (Settings → Developer Tools → Developer API Keys) with
                        the Orders permission (read is enough). Without it the
                        poller stays off and says so at boot.
  SQUARESPACE_POLL_SECONDS        optional, default 60, minimum 20.
  SQUARESPACE_LOOKBACK_DAYS       optional, default 30: how far back the very
                                  first poll looks, so orders taken before the
                                  key was set are still delivered.
  SQUARESPACE_ALLOW_TEST_ORDERS   optional, "1" to fulfil test-mode orders.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

API_URL = "https://api.squarespace.com/1.0/commerce/orders"
USER_AGENT = "CurrentsAndCritters-Store/1.0"

# The products on the Squarespace shop, by SKU. SKU first because it is the
# one field a rename on the shop does not touch.
#   SKU → (kind, value, price in cents)
SKU_REWARDS: Dict[str, Tuple[str, Any, int]] = {
    "CC-COINS-1000":        ("coins", 1000,  100),
    "CC-COINS-5250":        ("coins", 5250,  500),
    "CC-COINS-11500":       ("coins", 11500, 1000),
    "CC-COINS-25000":       ("coins", 25000, 2000),
    "CC-TIER-WAVE-WARRIOR": ("tier", "wave-warrior", 1500),
    "CC-TIER-OCEAN-ALLY":   ("tier", "ocean-ally",   3500),
    "CC-TIER-TIDE-TURNER":  ("tier", "tide-turner",  5000),
    "CC-TIER-TSUNAMI":      ("tier", "tsunami",      10000),
}

# Fallback for an order line with no known SKU (a Squarespace Pay Link, or a
# product someone re-created by hand): read the product NAME.
_COINS_NAME_RE = re.compile(r"([\d][\d,]*)\s*critter\s*coins", re.I)
_TIER_NAMES = (
    ("wave warrior", "wave-warrior"),
    ("ocean ally",   "ocean-ally"),
    ("tide turner",  "tide-turner"),
    ("tsunami",      "tsunami"),
)

# One line can carry quantity N; each unit is its own grant. Past this many it
# is a typo or abuse, and a human should look.
MAX_UNITS_PER_LINE = 50

OVERLAP = timedelta(minutes=10)     # re-read this much of the last window

USERNAME_FORM_HINTS = ("username", "user name", "game name", "player name")

# ── injected by multiplayer_server.main() ────────────────────────────────────
_deps: Dict[str, Callable[..., Any]] = {}

_state_lock = threading.Lock()
_status: Dict[str, Any] = {
    "running": False,
    "lastPollAt": None,
    "lastPolledUntil": None,
    "lastResult": {},
    "lastError": "",
}


def init(*, get_firestore: Callable[[], Any],
         fulfil: Callable[[dict], str],
         find_uid_by_email: Callable[[str], Optional[str]],
         coin_packs_by_cents: Dict[int, int],
         tiers_by_cents: Dict[int, str],
         username_label: str) -> None:
    """Wire in the server's Firestore accessor and its ONE fulfilment path."""
    _deps.update(get_firestore=get_firestore, fulfil=fulfil,
                 find_uid_by_email=find_uid_by_email)
    _deps["coin_packs"] = dict(coin_packs_by_cents)
    _deps["tiers"] = dict(tiers_by_cents)
    _deps["username_label"] = username_label


def api_key() -> str:
    return os.environ.get("SQUARESPACE_API_KEY", "").strip()


def enabled() -> bool:
    return bool(api_key())


def status() -> Dict[str, Any]:
    with _state_lock:
        out = dict(_status)
    out["configured"] = enabled()
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  ORDER → REWARDS
# ═════════════════════════════════════════════════════════════════════════════
def _cents(money: Any) -> int:
    """{"currency": "USD", "value": "20.00"} → 2000. Anything odd → 0."""
    if not isinstance(money, dict):
        return 0
    try:
        return int(round(float(str(money.get("value") or "0")) * 100))
    except (TypeError, ValueError):
        return 0


def reward_for_line_item(item: dict) -> Optional[Tuple[str, Any, int]]:
    """(kind, value, list price in cents) for a coin pack / tier line, else None."""
    sku = str(item.get("sku") or "").strip().upper()
    if sku in SKU_REWARDS:
        return SKU_REWARDS[sku]
    name = str(item.get("productName") or "").strip()
    low = name.lower()
    m = _COINS_NAME_RE.search(name)
    if m:
        try:
            coins = int(m.group(1).replace(",", ""))
        except ValueError:
            coins = 0
        packs = _deps.get("coin_packs") or {}
        for cents, amount in packs.items():
            if amount == coins:
                return ("coins", coins, int(cents))
        return None                      # "7 Critter Coins": not a pack we sell
    for needle, tier in _TIER_NAMES:
        if needle in low:
            for cents, t in (_deps.get("tiers") or {}).items():
                if t == tier:
                    return ("tier", tier, int(cents))
    return None


def _typed_username(order: dict) -> str:
    """A product-form answer that looks like the buyer's game username."""
    form = order.get("formSubmission")
    rows: List[Any] = []
    if isinstance(form, list):
        rows = form
    elif isinstance(form, dict):
        rows = [{"label": k, "value": v} for k, v in form.items()]
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("name") or "").strip().lower()
        if any(h in label for h in USERNAME_FORM_HINTS):
            val = str(row.get("value") or "").strip()
            if val:
                return val[:64]
    return ""


def _order_is_paid(order: dict) -> bool:
    if str(order.get("fulfillmentStatus") or "").upper() == "CANCELED":
        return False
    state = str(order.get("paymentState") or "").upper()
    if state:
        return state == "PAID"
    # Older payloads with no paymentState: paid unless it was refunded.
    return _cents(order.get("refundedTotal")) == 0 and _cents(order.get("grandTotal")) > 0


def events_for_order(order: dict, *, allow_test: bool = False,
                     find_uid_by_email: Optional[Callable[[str], Optional[str]]] = None
                     ) -> List[dict]:
    """Every grant one Squarespace order owes, as Stripe-shaped events.

    Pure apart from the email lookup, so the tests drive it directly."""
    if not isinstance(order, dict):
        return []
    if order.get("testmode") and not allow_test:
        return []
    if not _order_is_paid(order):
        return []
    order_id = re.sub(r"[^A-Za-z0-9]", "", str(order.get("id") or ""))
    if not order_id:
        return []

    email = str(order.get("customerEmail") or "").strip()
    username = _typed_username(order)
    lookup = find_uid_by_email or _deps.get("find_uid_by_email")
    # A lookup that FAILS (as opposed to finding nobody) raises out of here, so
    # poll_once retries the order next time instead of filing a buyer who has
    # an account as a guest.
    uid = (lookup(email) or "") if (email and lookup) else ""

    currency = str(((order.get("grandTotal") or {}).get("currency")) or "USD").lower()
    username_label = _deps.get("username_label") or "Currents and Critters Online Username"

    out: List[dict] = []
    for idx, item in enumerate(order.get("lineItems") or []):
        if not isinstance(item, dict):
            continue
        reward = reward_for_line_item(item)
        if reward is None:
            continue                      # a plush, a shirt: not ours to deliver
        kind, value, list_cents = reward
        paid_cents = _cents(item.get("unitPricePaid")) or list_cents
        if paid_cents != list_cents:
            print(f"[squarespace] note: order {order_id} paid {paid_cents}c for "
                  f"{item.get('productName')!r} (list {list_cents}c); granting the product anyway")
        line_id = re.sub(r"[^A-Za-z0-9]", "", str(item.get("id") or "")) or str(idx)
        try:
            qty = int(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty > MAX_UNITS_PER_LINE:
            print(f"[squarespace] !! order {order_id} line {line_id} has quantity {qty}; "
                  f"delivering {MAX_UNITS_PER_LINE}, check it by hand")
            qty = MAX_UNITS_PER_LINE
        meta = {
            "source": "squarespace",
            "product_name": str(item.get("productName") or "").strip()[:200],
            "squarespace_order_id": order_id,
            "squarespace_order_number": str(order.get("orderNumber") or ""),
        }
        meta["cc_coins" if kind == "coins" else "cc_tier"] = value
        custom_fields = []
        if username:
            custom_fields.append({
                "key": "username", "type": "text",
                "label": {"type": "custom", "custom": username_label},
                "text": {"value": username},
            })
        for n in range(max(1, qty)):
            sid = f"sqsp_{order_id}_{line_id}_{n}"
            out.append({
                "id": sid,
                "type": "checkout.session.completed",
                "data": {"object": {
                    "id": sid,
                    "object": "checkout.session",
                    "payment_status": "paid",
                    "currency": currency,
                    "amount_total": paid_cents,
                    "amount_subtotal": paid_cents,
                    "client_reference_id": uid or None,
                    "customer": "",
                    "customer_details": {"email": email},
                    "custom_fields": custom_fields,
                    "metadata": meta,
                }},
            })
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  THE API
# ═════════════════════════════════════════════════════════════════════════════
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{dt.microsecond // 1000:03d}Z"


def _get(params: Dict[str, str], key: str) -> dict:
    from urllib.parse import urlencode
    req = urllib.request.Request(API_URL + "?" + urlencode(params), headers={
        "Authorization": "Bearer " + key,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def fetch_orders(after: datetime, before: datetime, *, key: str,
                 http_get: Callable[[Dict[str, str], str], dict] = _get) -> List[dict]:
    """Every order modified in [after, before], all pages."""
    orders: List[dict] = []
    params = {"modifiedAfter": _iso(after), "modifiedBefore": _iso(before)}
    for _ in range(200):                 # 200 pages × 50 = 10,000 orders per poll
        page = http_get(params, key)
        orders.extend(o for o in (page.get("result") or []) if isinstance(o, dict))
        pag = page.get("pagination") or {}
        cursor = pag.get("nextPageCursor")
        if not pag.get("hasNextPage") or not cursor:
            break
        params = {"cursor": str(cursor)}  # Squarespace: the cursor goes ALONE
    return orders


# ═════════════════════════════════════════════════════════════════════════════
#  ONE POLL
# ═════════════════════════════════════════════════════════════════════════════
STATE_COLLECTION = "meta"
STATE_DOC = "squarespaceSync"


def _read_since(db) -> Optional[datetime]:
    try:
        snap = db.collection(STATE_COLLECTION).document(STATE_DOC).get()
        raw = ((snap.to_dict() or {}) if snap.exists else {}).get("polledUntil")
        if raw:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception as exc:  # noqa: BLE001
        print(f"[squarespace] could not read sync state: {exc}")
    return None


def _write_since(db, until: datetime) -> None:
    db.collection(STATE_COLLECTION).document(STATE_DOC).set(
        {"polledUntil": _iso(until)}, merge=True)


def poll_once(*, now: Optional[datetime] = None,
              http_get: Callable[[Dict[str, str], str], dict] = _get) -> Dict[str, Any]:
    """Fetch, convert, fulfil. Advances the saved window only when every grant
    in it went through, so a failure is retried on the next poll."""
    key = api_key()
    if not key:
        return {"ok": False, "error": "SQUARESPACE_API_KEY not set"}
    db = _deps["get_firestore"]()
    if db is None:
        return {"ok": False, "error": "Firestore unavailable"}

    now = now or datetime.now(timezone.utc)
    since = _read_since(db)
    if since is None:
        days = _int_env("SQUARESPACE_LOOKBACK_DAYS", 30, lo=1, hi=365)
        after = now - timedelta(days=days)
    else:
        after = since - OVERLAP

    counts: Dict[str, int] = {"orders": 0, "grants": 0}
    try:
        orders = fetch_orders(after, now, key=key, http_get=http_get)
    except urllib.error.HTTPError as exc:
        hint = " (check the key has the Orders permission)" if exc.code in (401, 403) else ""
        return {"ok": False, "error": f"Squarespace API HTTP {exc.code}{hint}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Squarespace API: {exc}"}

    allow_test = os.environ.get("SQUARESPACE_ALLOW_TEST_ORDERS", "").strip() == "1"
    failed = 0
    for order in orders:
        counts["orders"] += 1
        try:
            events = events_for_order(order, allow_test=allow_test)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[squarespace] !! order {order.get('id')} not converted, will retry: {exc}")
            continue
        for event in events:
            counts["grants"] += 1
            try:
                result = _deps["fulfil"](event)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[squarespace] !! {event['id']} failed, will retry: {exc}")
                continue
            counts[result] = counts.get(result, 0) + 1

    if failed == 0:
        _write_since(db, now)
    return {"ok": failed == 0, "failed": failed, "counts": counts,
            "window": [_iso(after), _iso(now)]}


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, "").strip() or default)))
    except ValueError:
        return default


def _loop() -> None:
    interval = _int_env("SQUARESPACE_POLL_SECONDS", 60, lo=20, hi=3600)
    while True:
        try:
            res = poll_once()
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"poll crashed: {exc}"}
        with _state_lock:
            _status["lastPollAt"] = datetime.now(timezone.utc).isoformat()
            if res.get("window"):
                _status["lastPolledUntil"] = res["window"][1]
            _status["lastResult"] = res.get("counts") or {}
            _status["lastError"] = "" if res.get("ok") else str(res.get("error") or "some grants failed")
        delivered = {k: v for k, v in (res.get("counts") or {}).items()
                     if k not in ("orders", "grants", "duplicate")}
        if not res.get("ok"):
            print(f"[squarespace] !! poll problem: {_status['lastError']}")
        elif delivered:
            print(f"[squarespace] delivered {delivered}")
        time.sleep(interval)


def start() -> bool:
    """Start the background poller once. False (and a loud line) without a key."""
    if not enabled():
        print("[squarespace] !! SQUARESPACE_API_KEY not set: Squarespace shop purchases "
              "are NOT being delivered to game accounts")
        return False
    with _state_lock:
        if _status["running"]:
            return True
        _status["running"] = True
    threading.Thread(target=_loop, daemon=True, name="squarespace-orders").start()
    print("[squarespace] order poller ON: Critter Coins / Supporter Tiers bought on the "
          "Squarespace shop are delivered automatically")
    return True
