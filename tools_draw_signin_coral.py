"""Repaint the sign-in ocean: lift the rotted plank out and plant a coral.

This is what turned the pier plank hidden in auth-ocean.jpg into the staghorn
coral that replaced it in 1.6.97. Kept, like tools_center_signin_title.py,
because the next time that artwork is regenerated this is the recipe: both
halves took several tries to get right.

    python3 tools_draw_signin_coral.py        (writes auth-ocean.jpg + .webp)

TWO THINGS THAT WERE NOT OBVIOUS
  1. The water is MODELLED, not diffused. Filling the hole by averaging its
     boundary drags the kelp green up into it; a quadratic surface fitted to
     every clean water pixel in a ring around the hole leaves water that
     matches at the seam and is smooth in the middle. Kelp inside the mask is
     protected, so the blades are still real scenery afterwards.
  2. Each shading pass is its OWN layer. ImageDraw does not blend: drawing with
     a translucent fill SETS the alpha, so painting a highlight at alpha 150
     leaves the middle of the branch half-transparent, and the first attempt
     came out grey over open water instead of coral.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageChops
import numpy as np
import math, os, random

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(ROOT, "multiplayer", "client", "auth-ocean.jpg")
WEBP = os.path.join(ROOT, "multiplayer", "client", "auth-ocean.webp")

im = Image.open(SRC).convert("RGB")
a = np.asarray(im).astype(np.float64)
H, W, _ = a.shape

# ── The mask ──────────────────────────────────────────────────────────
RECT = (55, 1675, 277, 1850)          # the composite's soft haze box
x0, y0, x1, y1 = RECT

plank = np.zeros((H, W), bool)
plank[1700:1860, 0:350] = a[1700:1860, 0:350, 0] > 45      # warm = wood

pm = Image.fromarray((plank * 255).astype(np.uint8))
pm = pm.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MaxFilter(9))
plank_d = np.asarray(pm) > 127

rect = np.zeros((H, W), bool)
rect[y0:y1, x0:x1] = True

g_minus_b = a[:, :, 1] - a[:, :, 2]
kelp = g_minus_b > -34                                     # green blades

# A one-shot record, so it refuses to run on artwork that has already been
# repainted: the coral is warm too, and this would erase half of it and plant a
# second colony on top.
_ys, _xs = np.nonzero(plank)
_dense = (plank.sum() / max(1.0, (_ys.max() - _ys.min() + 1) * (_xs.max() - _xs.min() + 1))
          if len(_ys) else 0.0)
# A plank is a solid slab and fills most of its own bounding box; a coral colony
# is branches and gaps and fills about a sixth of one.
if not len(_ys) or _dense < 0.5:
    raise SystemExit(
        "auth-ocean.jpg has no pier plank in it. This tool is the record of the "
        "1.6.97 repaint; run it against the artwork as it was before that.")

rows = np.arange(H)[:, None].repeat(W, 1)
mask = plank_d | (rect & (~kelp | (rows < 1726)))

# ── Fit the water ─────────────────────────────────────────────────────
# Everything clean in a ring around the hole, x 0..470, y 1600..1900.
RY0, RY1, RX0, RX1 = 1600, 1900, 0, 470
yy, xx = np.mgrid[RY0:RY1, RX0:RX1]
sub = a[RY0:RY1, RX0:RX1]
ok = (~mask[RY0:RY1, RX0:RX1]) & (~kelp[RY0:RY1, RX0:RX1])
# critters / rocks: anything far off water hue
ok &= (sub[:, :, 2] > 110) & (sub[:, :, 0] < 60)
Y = (yy[ok] - 1750) / 100.0
X = (xx[ok] - 160) / 100.0
A = np.stack([np.ones_like(X), X, Y, X * X, X * Y, Y * Y], axis=1)
Yg = (yy - 1750) / 100.0
Xg = (xx - 160) / 100.0
Ag = np.stack([np.ones_like(Xg), Xg, Yg, Xg * Xg, Xg * Yg, Yg * Yg], axis=-1)
fit = np.zeros_like(sub)
for c in range(3):
    coef, *_ = np.linalg.lstsq(A, sub[:, :, c][ok], rcond=None)
    fit[:, :, c] = Ag @ coef
    resid = sub[:, :, c][ok] - (A @ coef)
    print("channel", c, "rms residual", round(float(np.sqrt((resid ** 2).mean())), 2))

fill = a.copy()
fill[RY0:RY1, RX0:RX1] = fit
rng = np.random.default_rng(7)
fill += rng.normal(0, 1.6, fill.shape)          # grain, not a plastic gradient

# ── Feather and composite ─────────────────────────────────────────────
mm = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
alpha = (np.asarray(mm).astype(np.float64) / 255.0)[:, :, None]
out = a * (1 - alpha) + fill * alpha
erased = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
print("plank lifted")


# ══ THE CORAL ══════════════════════════════════════════════════════════
SS = 4                      # supersample
assert (W, H) == (1600, 2000), "the artwork changed shape; the numbers below are its pixels"

INK   = (28, 40, 64)        # the art's outline, a very dark teal-navy
BODY  = (188, 106, 101)     # the branch, in this water
LIT   = (219, 146, 128)     # its upper-left side, lit from the surface
DEEP  = (139, 68, 82)       # the shaded underside
TIP   = (235, 196, 174)     # the pale growing tips staghorn coral has


def newlayer():
    im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def bez(p0, p1, p2, t):
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
    return x, y


def sweep(draw, p0, p1, p2, r0, r1, colour, steps=110, dx=0.0, dy=0.0, rpad=0.0):
    """A circle of interpolated radius swept along a quadratic bezier."""
    for i in range(steps + 1):
        t = i / steps
        x, y = bez(p0, p1, p2, t)
        r = (r0 + (r1 - r0) * t + rpad) * SS
        x = (x + dx) * SS
        y = (y + dy) * SS
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)


def fade(im, k):
    """Scale a layer's alpha, which is the blending ImageDraw will not do."""
    r, g, b, a = im.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * k))))


BRANCHES = []               # (base, ctrl, tip, r0, r1, depth)


def grow(base, ang, length, r0, depth, rng):
    """One branch, then the two or three it forks into."""
    curve = rng.uniform(-0.30, 0.30)
    tip = (base[0] + math.sin(ang) * length,
           base[1] - math.cos(ang) * length)
    ctrl = (base[0] + math.sin(ang + curve) * length * 0.55,
            base[1] - math.cos(ang + curve) * length * 0.55)
    r1 = r0 * rng.uniform(0.60, 0.72)
    BRANCHES.append((base, ctrl, tip, r0, r1, depth))
    if depth >= 3 or length < 26:
        return
    forks = 3 if (depth == 0 and rng.random() < 0.6) else 2
    spread = rng.uniform(0.42, 0.72)
    for k in range(forks):
        off = (k - (forks - 1) / 2) * spread
        grow(tip, ang + off + rng.uniform(-0.10, 0.10),
             length * rng.uniform(0.58, 0.76), r1, depth + 1, rng)


rng = random.Random(11)
# The colony: a tall head and two lower ones leaning out to the right, all
# rooted below the bottom edge so nothing ends in a rounded stump.
grow((190, 2016), -0.14, 78, 10.0, 0, rng)
grow((166, 2020), -0.42, 60, 8.2, 0, rng)
grow((222, 2020), 0.32, 52, 7.4, 0, rng)
grow((262, 2022), 0.48, 38, 6.0, 1, rng)

BRANCHES.sort(key=lambda b: b[5])       # deepest first

ink_l,  ink_d  = newlayer()
body_l, body_d = newlayer()
deep_l, deep_d = newlayer()
lit_l,  lit_d  = newlayer()
tip_l,  tip_d  = newlayer()

for p0, c, p2, r0, r1, dep in BRANCHES:
    sweep(ink_d,  p0, c, p2, r0, r1, INK + (255,), rpad=1.9)
for p0, c, p2, r0, r1, dep in BRANCHES:
    sweep(body_d, p0, c, p2, r0, r1, BODY + (255,))
for p0, c, p2, r0, r1, dep in BRANCHES:
    sweep(deep_d, p0, c, p2, r0 * 0.60, r1 * 0.60, DEEP + (255,), dx=1.5, dy=1.5)
for p0, c, p2, r0, r1, dep in BRANCHES:
    sweep(lit_d,  p0, c, p2, r0 * 0.40, r1 * 0.46, LIT + (255,), dx=-1.6, dy=-1.6)

starts = {(round(b[0][0]), round(b[0][1])) for b in BRANCHES}
for p0, c, p2, r0, r1, dep in BRANCHES:
    if (round(p2[0]), round(p2[1])) in starts:
        continue                                   # not a tip, a fork
    x, y = p2
    r = r1 * 0.82 * SS
    tip_d.ellipse([x * SS - r, (y + 0.35) * SS - r,
                   x * SS + r, (y + 0.35) * SS + r], fill=TIP + (255,))

# Shading and tips are clipped to the branch itself: a highlight that leaks
# past the outline is what makes drawn art look like a decal.
body_a = body_l.split()[3]
for lay in (deep_l, lit_l, tip_l):
    lay.putalpha(ImageChops.darker(lay.split()[3], body_a))

colony = Image.alpha_composite(ink_l, body_l)
colony = Image.alpha_composite(colony, fade(deep_l, 0.45))
colony = Image.alpha_composite(colony, fade(lit_l, 0.60))
colony = Image.alpha_composite(colony, fade(tip_l, 0.85))
coral = colony.resize((W, H), Image.LANCZOS)

# ── Sit it in the water ───────────────────────────────────────────────
bg = np.asarray(erased).astype(np.float64)
ca = np.asarray(coral).astype(np.float64)
rgb, alpha = ca[:, :, :3], (ca[:, :, 3:4] / 255.0)

water = np.array([10.0, 112.0, 158.0])
rgb = rgb * 0.90 + water * 0.10          # depth haze, or it is a sticker

# A soft ambient shadow where it meets the floor, painted UNDER the colony.
sh = Image.new("L", (W, H), 0)
ImageDraw.Draw(sh).ellipse([118, 1966, 300, 2012], fill=88)
sh = sh.filter(ImageFilter.GaussianBlur(15))
sa = (np.asarray(sh).astype(np.float64) / 255.0)[:, :, None] * 0.5
floor = bg * (1 - sa) + np.array([6.0, 78.0, 112.0]) * sa

out = floor * (1 - alpha) + rgb * alpha
final = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
final.save(SRC, "JPEG", quality=88, optimize=True, progressive=True, subsampling=1)
# The .webp SIBLING is what every modern browser is actually served, so a
# PNG/JPEG edited without regenerating it ships the OLD picture to everyone.
final.save(WEBP, "WEBP", quality=82, method=6)

ys, xs = np.nonzero(np.asarray(coral)[:, :, 3] > 40)
print("coral bbox  x %d..%d  y %d..%d" % (xs.min(), xs.max(), ys.min(), ys.max()))
print("spot  cx=%.4f cy=%.4f  w=%.4f h=%.4f" % (
    (xs.min() + xs.max()) / 2 / W, (ys.min() + ys.max()) / 2 / H,
    (xs.max() - xs.min()) / W, (ys.max() - ys.min()) / H))
print("wrote", SRC, "and", WEBP)
