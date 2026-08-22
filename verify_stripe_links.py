"""Check the 7 LIVE Payment Links against what the code expects, for real.

test_stripe_payments.py proves the BUTTONS point at the right URLs. It cannot
prove what those URLs actually DO, because that lives in your Stripe account.
This script closes that gap: it asks Stripe about each link and checks the four
things that silently break the money path.

Why each check matters:

  1. PRICE. The webhook grants by `amount_total` alone (COIN_PACKS_BY_CENTS /
     SUPPORTER_TIERS_BY_CENTS), it never sees the URL. A link priced $35 behind
     the "Tide Turner" button charges $35 and grants ocean-ally, and nothing in
     the code can notice.
  2. THE THREE CUSTOM QUESTIONS. A label is a behaviour key (see the comment on
     CF_WALL_NAME_LABEL). Miss the wall-name questions and the buyer defaults to
     ANONYMOUS, they pay and never appear on the Reef Wall. Miss the username
     question and a signed-out purchase can never be matched to their account.
     ⚠️ These belong on ALL SEVEN links, coin packs included: every purchase
     counts toward the lifetime total that sizes a name on the wall.
  3. ACTIVE. A deactivated link is a dead button.
  4. REDIRECT. Without ?session_id={CHECKOUT_SESSION_ID} the /thanks page can't
     confirm the purchase landed.

Run:
    STRIPE_SECRET_KEY=sk_live_... python3 verify_stripe_links.py

Read-only: it only ever issues GETs. Exits non-zero if anything is wrong.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms

API = "https://api.stripe.com/v1"

# The live links, and what the code believes each one is. Keep in step with
# TestLivePaymentLinks.LINKS in test_stripe_payments.py.
EXPECTED = [
    ("https://buy.stripe.com/fZufZi6En1FqgIV38eds400",  100, "1,000 Critter Coins"),
    ("https://buy.stripe.com/dRm9AU6En1Fq64h6kqds401",  500, "5,250 Critter Coins"),
    ("https://buy.stripe.com/5kQ00k2o75VGeANaAGds402", 1000, "11,500 Critter Coins"),
    ("https://buy.stripe.com/4gMdRaaUDbg078l7ouds403", 2000, "25,000 Critter Coins"),
    ("https://buy.stripe.com/cNi6oI3sbfwggIV7ouds404", 1500, "Wave Warrior"),
    ("https://buy.stripe.com/5kQcN6geX83O2S5gZ4ds405", 3500, "Ocean Ally"),
    ("https://buy.stripe.com/00wfZi6EnfwgcsFcIOds406", 5000, "Tide Turner"),
]

OK, BAD, WARN = "  ✓", "  ✗", "  !"


def _get(path: str, key: str, **params) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        raise SystemExit(f"Stripe API error {e.code} on {path}: {body}")


def _all_payment_links(key: str) -> dict:
    """Every Payment Link in the account, keyed by its short buy.stripe.com URL."""
    out, starting_after = {}, None
    while True:
        params = {"limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        page = _get("payment_links", key, **params)
        data = page.get("data") or []
        for link in data:
            if link.get("url"):
                out[link["url"]] = link
        if not page.get("has_more") or not data:
            break
        starting_after = data[-1]["id"]
    return out


def _labels(link: dict) -> list:
    """The custom-question labels this link actually asks, lowercased."""
    out = []
    for f in link.get("custom_fields") or []:
        lab = (f.get("label") or {}).get("custom")
        if lab:
            out.append(lab.strip().lower())
    return out


def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        print("Set STRIPE_SECRET_KEY (sk_live_… to check the live links).")
        return 2

    mode = "LIVE" if key.startswith("sk_live_") else "TEST"
    print(f"Checking 7 Payment Links against a {mode}-mode key.\n")
    if mode == "TEST":
        print("! A test key cannot see live links, every one will read as MISSING.\n")

    links = _all_payment_links(key)
    failures = 0

    for url, want_cents, label in EXPECTED:
        print(f"{label}  (${want_cents / 100:,.2f})")
        link = links.get(url)
        if link is None:
            print(f"{BAD} NOT FOUND in this account: {url}")
            print(f"{BAD} the button opens a link this Stripe account doesn't own")
            failures += 1
            print()
            continue

        # 1) price, the only thing the webhook matches on.
        items = _get(f"payment_links/{link['id']}/line_items", key, limit=100)
        total = sum(int(i.get("amount_total") or 0) for i in items.get("data") or [])
        currency = (items.get("data") or [{}])[0].get("currency", "")
        if total == want_cents and currency.lower() == "usd":
            print(f"{OK} price {total} cents USD")
        else:
            print(f"{BAD} price is {total} cents {currency.upper()}, expected "
                  f"{want_cents} cents USD → buyer would be granted the WRONG product")
            failures += 1

        # …and confirm the real webhook code turns that price into this product.
        kind, value = ms._reward_for_session(
            {"currency": "usd", "amount_total": total, "metadata": link.get("metadata") or {}})
        if kind is None:
            print(f"{BAD} the webhook grants NOTHING for {total} cents")
            failures += 1
        else:
            print(f"{OK} webhook grants {kind}={value}")

        # 2) the three questions that carry the buyer onto the Reef Wall.
        have = _labels(link)
        for required in (ms.CF_WALL_NAME_LABEL, ms.CF_WALL_PUBLIC_LABEL):
            if required.strip().lower() in have:
                print(f'{OK} asks "{required}"')
            else:
                print(f'{BAD} MISSING "{required}" → this buyer defaults to '
                      f'ANONYMOUS and never reaches the Reef Wall')
                failures += 1
        if any(u.strip().lower() in have for u in ms.CF_USERNAME_LABELS):
            print(f'{OK} asks for the game username')
        else:
            print(f'{BAD} MISSING "{ms.CF_USERNAME_LABEL}" → a signed-out purchase '
                  f'can never be matched to an account')
            failures += 1

        # 3) active.
        if link.get("active"):
            print(f"{OK} active")
        else:
            print(f"{BAD} INACTIVE, the button is dead")
            failures += 1

        # 4) redirect back to /thanks with the session id.
        after = ((link.get("after_completion") or {}).get("redirect") or {}).get("url") or ""
        if "{CHECKOUT_SESSION_ID}" in after and "/thanks" in after:
            print(f"{OK} returns to {after}")
        elif after:
            print(f"{WARN} redirect is {after}: /thanks can only confirm the "
                  f"purchase if it ends in /thanks?session_id={{CHECKOUT_SESSION_ID}}")
        else:
            print(f"{WARN} no redirect set: buyers stay on Stripe's receipt page")
        print()

    # The webhook secret: live links + a test-mode secret = every real payment
    # is rejected at the signature gate, money taken and nothing granted.
    whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not whsec:
        print(f"{WARN} STRIPE_WEBHOOK_SECRET is not set in THIS shell: check it "
              f"on Render, and that it's the LIVE endpoint's signing secret.")
    print("=" * 66)
    if failures:
        print(f"{failures} problem(s) found: fix these in the Stripe Dashboard.")
        return 1
    print("All 7 links check out: right price, right product, wall questions present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
