#!/usr/bin/env python3
"""Build the Snap & Score local-recognition card library.

Reads every official card sheet (horizontal_cards / vertical_cards /
oceans_cards page PNGs — each page IS one physical card: up/down cards carry a
top half + bottom half face, left/right cards a left + right face, ocean pages
one full face) and precomputes the recognition descriptors the in-browser
scanner compares photo crops against:

  • pHash    — 64-bit DCT perceptual hash (robust to blur / lighting / resize)
  • dHash    — 64-bit gradient hash
  • color    — 4×4 mean-RGB layout (48 ints)
  • edges    — 8-bin gradient-orientation histogram
  • badge    — 8×8 luma grid of each half's symbol badge (disambiguates copies
               of the same art that differ only by the printed symbol)

Reference pipeline mirrors the runtime crop pipeline: page → 200×280 working
image → percentile luma normalize → 64×64 base → descriptors. The browser
never re-processes the official art at scan time — it loads the JSON this
script writes.

Output:
  multiplayer/client/snap-card-library.json   (static asset served to players)
  test_snap_vision_fixtures.json              (cross-language descriptor
      fixtures consumed by test_snap_vision.js to prove the JS math matches
      this file bit-for-bit)

Run whenever the card art or the card database changes:

    python3 build_snap_card_library.py          # writes both files
    python3 build_snap_card_library.py --check  # verify only (build gate)

The descriptor math here MUST stay in lockstep with snap-vision-core.js
(DESCRIPTOR_VERSION guards it: bump both together or matching silently dies —
the fixtures test exists to catch exactly that).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

from PIL import Image

import fish_game_all_in_one as fish

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_LIBRARY = os.path.join(BASE_DIR, "multiplayer", "client", "snap-card-library.json")
OUT_FIXTURES = os.path.join(BASE_DIR, "test_snap_vision_fixtures.json")

DESCRIPTOR_VERSION = 2      # v2: per-channel normalize + per-half color layouts
CARD_W, CARD_H = 200, 280   # canonical portrait working size (5:7)
BASE_N = 64                 # square base the descriptors are computed from
GRAY_N = 32                 # pHash / edge-histogram working size

# Symbol-badge crop regions as fractions of the FULL portrait card (x0,y0,x1,y1).
# Measured on the 720×1008 sheets: the badge sits at the top-right of each face.
BADGE_REGIONS = {
    "h": {"up":   (0.790, 0.024, 0.960, 0.138),
          "down": (0.790, 0.524, 0.960, 0.638)},
    "v": {"left":  (0.330, 0.024, 0.495, 0.138),
          "right": (0.830, 0.024, 0.995, 0.138)},
    "o": {"ocean": (0.790, 0.024, 0.960, 0.138)},
}


# ── deterministic pixel math (mirrored 1:1 in snap-vision-core.js) ───────────
# Rounding is always floor(x + 0.5): Python's round() half-to-even and JS's
# Math.round() disagree on .5 boundaries, this rule matches in both languages.

def r05(v):
    return math.floor(v + 0.5)


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def box_downsample(src, sw, sh, ch, dw, dh):
    """Box-average resample. src is a flat row-major list with `ch` channels.
    Every source pixel lands in exactly one destination cell (floor(sx*dw/sw)),
    so the result is identical across Python and JS."""
    sums = [0.0] * (dw * dh * ch)
    counts = [0] * (dw * dh)
    for sy in range(sh):
        dy = (sy * dh) // sh
        row = sy * sw
        for sx in range(sw):
            dx = (sx * dw) // sw
            di = dy * dw + dx
            si = (row + sx) * ch
            b = di * ch
            for c in range(ch):
                sums[b + c] += src[si + c]
            counts[di] += 1
    out = [0.0] * (dw * dh * ch)
    for i in range(dw * dh):
        n = counts[i] or 1
        for c in range(ch):
            out[i * ch + c] = sums[i * ch + c] / n
    return out


def gray_of(rgb, count):
    return [luma(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]) for i in range(count)]


_DCT_COS = None


def _dct_cos():
    global _DCT_COS
    if _DCT_COS is None:
        n = GRAY_N
        _DCT_COS = [[math.cos(math.pi * (2 * x + 1) * u / (2 * n)) for x in range(n)]
                    for u in range(8)]
    return _DCT_COS


def phash_bits(gray32):
    """64-bit hex pHash: 8×8 low-frequency DCT block minus DC, thresholded on
    the mean of the 63 AC coefficients (top bit always 0). gray32 is row-major
    [y*N + x]."""
    n = GRAY_N
    cos = _dct_cos()
    # first pass over x for each row y: R[u][y] = Σ_x g(x,y)·cos_u[x]
    rows = [[0.0] * n for _ in range(8)]
    for u in range(8):
        cu = cos[u]
        ru = rows[u]
        for y in range(n):
            row = y * n
            s = 0.0
            for x in range(n):
                s += gray32[row + x] * cu[x]
            ru[y] = s
    coeffs = []
    for u in range(8):
        ru = rows[u]
        for v in range(8):
            if u == 0 and v == 0:
                continue
            cv = cos[v]
            s = 0.0
            for y in range(n):
                s += ru[y] * cv[y]
            coeffs.append(s)
    mean = sum(coeffs) / len(coeffs)
    bits = 0
    for cval in coeffs:
        bits = (bits << 1) | (1 if cval > mean else 0)
    return f"{bits:016x}"


def dhash_bits(gray64):
    g98 = box_downsample(gray64, BASE_N, BASE_N, 1, 9, 8)
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if g98[r * 9 + c] > g98[r * 9 + c + 1] else 0)
    return f"{bits:016x}"


def color_layout(base_rgb):
    c44 = box_downsample(base_rgb, BASE_N, BASE_N, 3, 4, 4)
    return [max(0, min(255, r05(v))) for v in c44]


# Orientation-bin boundaries at k·π/8: exact same literals live in
# snap-vision-core.js — binning via IEEE multiply/compare (no atan2/hypot,
# whose last-bit platform differences would break cross-language parity).
_EDGE_SIN = (0.3826834323650898, 0.7071067811865476, 0.9238795325112867, 1.0,
             0.9238795325112867, 0.7071067811865476, 0.3826834323650898)
_EDGE_COS = (0.9238795325112867, 0.7071067811865476, 0.3826834323650898, 0.0,
             -0.3826834323650898, -0.7071067811865476, -0.9238795325112867)


def edge_hist(gray32):
    n = GRAY_N
    hist = [0.0] * 8
    for y in range(1, n - 1):
        for x in range(1, n - 1):
            i = y * n + x
            gx = (gray32[i - n + 1] + 2 * gray32[i + 1] + gray32[i + n + 1]
                  - gray32[i - n - 1] - 2 * gray32[i - 1] - gray32[i + n - 1])
            gy = (gray32[i + n - 1] + 2 * gray32[i + n] + gray32[i + n + 1]
                  - gray32[i - n - 1] - 2 * gray32[i - n] - gray32[i - n + 1])
            mag = math.sqrt(gx * gx + gy * gy)
            if mag < 1e-9:
                continue
            if gy < 0 or (gy == 0 and gx < 0):   # canonicalize direction to [0, π)
                gx, gy = -gx, -gy
            b = 0
            for k in range(7):
                if gy * _EDGE_COS[k] - gx * _EDGE_SIN[k] >= 0:
                    b += 1
            hist[b] += mag
    total = sum(hist) or 1.0
    return [math.floor(h / total * 100000 + 0.5) / 100000 for h in hist]


def normalize_rgb(rgb, count):
    """Per-channel 2–98 percentile stretch. Normalizing each channel on its own
    cancels phone white-balance / warm-light color casts, not just exposure
    (shared by refs and photo crops so both see the same normalization)."""
    out = list(rgb)
    lo_i = int(0.02 * (count - 1))
    hi_i = int(0.98 * (count - 1))
    for ch in range(3):
        vals = sorted(rgb[i * 3 + ch] for i in range(count))
        lo, hi = vals[lo_i], vals[hi_i]
        if hi - lo < 8:
            continue
        scale = 255.0 / (hi - lo)
        for i in range(count):
            v = (rgb[i * 3 + ch] - lo) * scale
            out[i * 3 + ch] = 0.0 if v < 0 else 255.0 if v > 255 else v
    return out


def region_color_layout(base_rgb, x0, y0, x1, y1):
    """4×4 mean-RGB layout of a sub-region of the 64×64 base (half-face color
    fingerprint — the per-half equivalent of a card-name check)."""
    w, h = x1 - x0, y1 - y0
    crop = [0.0] * (w * h * 3)
    for y in range(h):
        for x in range(w):
            si = ((y0 + y) * BASE_N + (x0 + x)) * 3
            di = (y * w + x) * 3
            crop[di:di + 3] = base_rgb[si:si + 3]
    c44 = box_downsample(crop, w, h, 3, 4, 4)
    return [max(0, min(255, r05(v))) for v in c44]


def descriptors_from_base(base_rgb):
    """Full descriptor set incl. BOTH half-splits (hh = top/bottom, hv =
    left/right) — a photo crop doesn't know which kind of card it is yet, so
    it always carries both; refs keep only their own kind's split (`hc`)."""
    gray64 = gray_of(base_rgb, BASE_N * BASE_N)
    gray32 = box_downsample(gray64, BASE_N, BASE_N, 1, GRAY_N, GRAY_N)
    half = BASE_N // 2
    return {
        "p": phash_bits(gray32),
        "d": dhash_bits(gray64),
        "c": color_layout(base_rgb),
        "e": edge_hist(gray32),
        "hh": [region_color_layout(base_rgb, 0, 0, BASE_N, half),
               region_color_layout(base_rgb, 0, half, BASE_N, BASE_N)],
        "hv": [region_color_layout(base_rgb, 0, 0, half, BASE_N),
               region_color_layout(base_rgb, half, 0, BASE_N, BASE_N)],
    }


def badge_grid(card_rgb, w, h, region):
    x0, y0, x1, y1 = region
    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = max(px0 + 1, int(x1 * w)), max(py0 + 1, int(y1 * h))
    bw, bh = px1 - px0, py1 - py0
    g = [0.0] * (bw * bh)
    for y in range(bh):
        for x in range(bw):
            si = ((py0 + y) * w + (px0 + x)) * 3
            g[y * bw + x] = luma(card_rgb[si], card_rgb[si + 1], card_rgb[si + 2])
    g88 = box_downsample(g, bw, bh, 1, 8, 8)
    return [max(0, min(255, r05(v))) for v in g88]


# ── uid ↔ sheet-page mapping (same rule as artFor() in the client) ───────────

def page_map():
    """Returns ([(kind, page_no, img_rel, halves{side: {u,n,sym}})], problems)
    for every physical card, validated against the live engine card database."""
    db = fish.load_card_db()
    problems = []

    def expect(uid, side):
        cd = db.get(uid)
        if cd is None:
            problems.append(f"uid {uid} missing from card DB")
            return None
        d = str(cd.direction or "").strip().lower()
        sp = str(cd.species or "").strip().lower()
        ok = (sp == "ocean") if side == "ocean" else (d == side)
        if not ok:
            problems.append(f"uid {uid}: expected {side}, got dir={d!r} species={sp!r}")
        return {"u": uid, "n": str(cd.name), "sym": str(cd.symbol or "").strip()}

    pages = []
    for p in range(1, 49):
        pages.append(("h", p, f"horizontal_cards/page_{p:02d}.png",
                      {"up": expect(2 * p - 1, "up"), "down": expect(2 * p, "down")}))
    for p in range(1, 45):
        pages.append(("v", p, f"vertical_cards/page_{p:02d}.png",
                      {"left": expect(101 + 2 * (p - 1), "left"),
                       "right": expect(102 + 2 * (p - 1), "right")}))
    for p in range(1, 69):  # page 69 is END GAME — never on a board
        pages.append(("o", p, f"oceans_cards/page_{p:02d}.png",
                      {"ocean": expect(200 + p, "ocean")}))
    return pages, problems


def load_card_working_rgb(path):
    """Page PNG → flat RGB floats at the canonical 200×280 working size.
    PIL's BOX resample is a true box average — the same operation the runtime
    warp+downsample performs, just in fast C."""
    img = Image.open(path).convert("RGB").resize((CARD_W, CARD_H), Image.BOX)
    return [float(b) for b in img.tobytes()]


def build():
    t0 = time.time()
    pages, problems = page_map()
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        raise SystemExit(f"card DB ↔ page mapping broken ({len(problems)} problems)")

    cards = []
    fixtures = []
    for kind, page_no, rel, halves in pages:
        path = os.path.join(BASE_DIR, rel)
        if not os.path.exists(path):
            raise SystemExit(f"missing card image: {rel}")
        work = load_card_working_rgb(path)
        norm = normalize_rgb(work, CARD_W * CARD_H)
        base = box_downsample(norm, CARD_W, CARD_H, 3, BASE_N, BASE_N)
        desc = descriptors_from_base(base)
        # refs keep only the half-split matching their own kind (crops carry both)
        hc = {"h": desc["hh"], "v": desc["hv"], "o": [desc["c"]]}[kind]
        badges = {side: badge_grid(norm, CARD_W, CARD_H, BADGE_REGIONS[kind][side])
                  for side in halves}
        entry = {
            "id": f"{kind}{page_no:02d}",
            "kind": kind,
            "img": "/" + rel,
            "halves": halves,
            "p": desc["p"], "d": desc["d"], "c": desc["c"], "e": desc["e"],
            "hc": hc,
            "b": badges,
        }
        cards.append(entry)
        # representative fixtures for the cross-language descriptor test; the
        # expected values are computed FROM the rounded base so JS can
        # reproduce them exactly from the JSON alone
        if entry["id"] in ("h01", "v01", "o08"):
            base_r = [math.floor(v * 10000 + 0.5) / 10000 for v in base]
            fixtures.append({"id": entry["id"],
                             "base": base_r,
                             "expect": descriptors_from_base(base_r)})

    uid_count = sum(len(h) for c in cards for h in [c["halves"]])
    library = {
        "libraryVersion": time.strftime("%Y%m%d") + f"-d{DESCRIPTOR_VERSION}",
        "descriptorVersion": DESCRIPTOR_VERSION,
        "cardW": CARD_W,
        "cardH": CARD_H,
        "baseN": BASE_N,
        "grayN": GRAY_N,
        "badgeRegions": BADGE_REGIONS,
        "cards": cards,
    }
    print(f"built {len(cards)} physical cards / {uid_count} playable faces "
          f"in {time.time() - t0:.1f}s")
    return library, fixtures


def main():
    check_only = "--check" in sys.argv
    library, fixtures = build()
    if check_only:
        try:
            with open(OUT_LIBRARY, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except FileNotFoundError:
            raise SystemExit("snap-card-library.json does not exist — run the builder")
        a = {c["id"]: (c["p"], c["d"]) for c in existing.get("cards", [])}
        b = {c["id"]: (c["p"], c["d"]) for c in library["cards"]}
        if a != b:
            changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            raise SystemExit(f"snap-card-library.json is stale — rerun the builder "
                             f"(differs on: {', '.join(changed[:8])} …)")
        print("library is up to date ✓")
        return
    with open(OUT_LIBRARY, "w", encoding="utf-8") as f:
        json.dump(library, f, separators=(",", ":"))
    with open(OUT_FIXTURES, "w", encoding="utf-8") as f:
        json.dump({"descriptorVersion": DESCRIPTOR_VERSION, "fixtures": fixtures}, f)
    print(f"wrote {OUT_LIBRARY} ({os.path.getsize(OUT_LIBRARY) // 1024}KB)")
    print(f"wrote {OUT_FIXTURES}")


if __name__ == "__main__":
    main()
