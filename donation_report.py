#!/usr/bin/env python3
"""What came in this month, and what that owes to ocean conservation.

    python3 donation_report.py                 # this month, UTC
    python3 donation_report.py 2026-08          # a named month
    python3 donation_report.py --json           # the raw payload

Reads /api/admin/donations on the live game server, which is the only thing
with permission to read Firestore. That endpoint sums the PAYMENTS themselves
rather than the lifetime totals the Supporter Reef Wall is sized from, because
a lifetime total cannot say which month the money arrived in.

The key comes from ADMIN_RECOVERY_KEY in the environment, or --key. It is the
same key the supporter review list uses, and it is never printed.

THE SHARE IS ROUNDED UP to the cent, server-side. The pledge is a promise, so
the arithmetic errs towards donating a cent more, never a cent less.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://play.currentsandcritters.com"


def money(cents: int) -> str:
    return "${:,.2f}".format((int(cents) or 0) / 100)


def fetch(base: str, key: str, month: str, timeout: int) -> dict:
    qs = {"admin_key": key}
    if month:
        qs["month"] = month
    url = base.rstrip("/") + "/api/admin/donations?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(url, headers={"User-Agent": "donation_report"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def report(d: dict) -> str:
    pct = d.get("sharePct", 5)
    gross = int(d.get("grossCents") or 0)
    share = int(d.get("conservationShareCents") or 0)
    rows = d.get("payments") or []

    out = []
    out.append("Donations for %s (UTC)" % d.get("month", "?"))
    out.append("=" * 46)
    if not rows:
        out.append("No payments recorded in this month.")
    else:
        for r in rows:
            out.append("  %s  %9s  %-28s %s" % (
                str(r.get("at", ""))[:10], money(r.get("amountCents") or 0),
                str(r.get("product") or "")[:28], r.get("supporter") or ""))
        out.append("")
        byp = d.get("byProduct") or {}
        if len(byp) > 1:
            out.append("By product")
            for name, b in sorted(byp.items(), key=lambda kv: -kv[1]["cents"]):
                out.append("  %-30s %2d x  %9s" % (name[:30], b["count"], money(b["cents"])))
            out.append("")
    out.append("Payments counted:        %d" % int(d.get("paymentCount") or 0))
    out.append("Total taken:             %s" % money(gross))
    out.append("%d%% to ocean conservation: %s   <-- donate this" % (pct, money(share)))

    # Anything the server could not place is said out loud rather than quietly
    # left out: a total that silently drops rows is worse than a short one.
    unpaid = int(d.get("skippedUnpaid") or 0)
    untimed = int(d.get("skippedMissingTimestamp") or 0)
    if unpaid or untimed:
        out.append("")
        out.append("Not counted:")
        if unpaid:
            out.append("  %d payment(s) not settled yet (Stripe paymentStatus is not paid)" % unpaid)
        if untimed:
            out.append("  %d payment(s) with no timestamp, so no month to put them in" % untimed)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("month", nargs="?", default="", help="YYYY-MM (default: this month, UTC)")
    ap.add_argument("--base", default=os.environ.get("CC_SERVER_BASE", DEFAULT_BASE))
    ap.add_argument("--key", default=os.environ.get("ADMIN_RECOVERY_KEY", ""))
    ap.add_argument("--timeout", type=int, default=60, help="Render cold starts are slow")
    ap.add_argument("--json", action="store_true", help="print the raw payload")
    a = ap.parse_args(argv)

    if not a.key:
        print("No admin key. Set ADMIN_RECOVERY_KEY in the environment, or pass --key.",
              file=sys.stderr)
        return 2
    try:
        d = fetch(a.base, a.key, a.month.strip(), a.timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        print("Server said %s: %s" % (exc.code, body), file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("Could not reach %s: %s" % (a.base, exc), file=sys.stderr)
        return 1

    if not d.get("ok"):
        print("Report failed: %s" % d.get("error", "unknown"), file=sys.stderr)
        return 1
    print(json.dumps(d, indent=2) if a.json else report(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
