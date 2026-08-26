"""Centre CURRENTS & CRITTERS in the sign-in painting.

Run once, against the artwork as it came out of the paint program, and only if
the title is off-centre. It is kept because the next time login-bg.png is
redrawn the same thing will probably be true of it, and the awkward parts here
(the panel seam, the reef) took several tries to get right.
Check first:  the gold lettering's centre against the image's centre.

The title was painted 46px right of the image's middle. Moving it is three
steps: lift the lettering off its background, rebuild the background it was
covering, and put the lettering back down 46px to the left.

The hard part is the rebuild, and the trick is to do it COLUMN by column rather
than row by row. Read along a row and every letter is a wide hole to bridge, so
the whole band ends up a smooth ramp and the field reads as a flat patch. Read
down a column and the hole is short and everything that makes this band look
like something (the hard vertical seam between the two ocean panels, the purple
corals, the navy-to-water tone) is a property OF that column, so it survives.
"""
import numpy as np
from PIL import Image

SRC, OUT = "multiplayer/client/login-bg.png", "multiplayer/client/login-bg.png"
Y0, Y1   = 26, 226        # the band the title lives in
SEAM_LO, SEAM_HI = 799, 823
DX       = -46            # move the title this far left
GRAIN    = np.array([1.35, 1.6, 3.2], dtype=np.float32)   # measured off the flat field
REF      = 6              # clean rows sampled either side of the hole

rng  = np.random.default_rng(20260826)
im   = Image.open(SRC).convert("RGB")
W, H = im.size
full = np.asarray(im).astype(np.float32)
band = full[Y0:Y1].copy()
bh, bw, _ = band.shape
R, G, B = band[..., 0], band[..., 1], band[..., 2]

def dilate(m, r):
    out = m.copy()
    for _ in range(r):
        s = out.copy()
        s[1:, :] |= out[:-1, :]; s[:-1, :] |= out[1:, :]
        s[:, 1:] |= out[:, :-1]; s[:, :-1] |= out[:, 1:]
        out = s
    return out

# ── the gold of the lettering ──────────────────────────────────────────
gold = (R > 200) & (G > 150) & (B < 175) & ((R - B) > 70)
gold[:, :235] = False; gold[:, 1425:] = False

# Everything within this of the gold is repainted. Generous on purpose: the
# lettering carries a wide soft glow, and a surviving scrap of it reads as a
# ghost edge 46px from the letter it belongs to.
gone = dilate(gold, 42)

# ── the background under it, one column at a time ──────────────────────
bg = band.copy()
for x in range(bw):
    m = gone[:, x]
    if not m.any():
        continue
    ys  = np.where(m)[0]
    top, bot = ys[0], ys[-1]
    above = band[max(0, top - REF):top, x]
    below = band[bot + 1:bot + 1 + REF, x]
    if len(above) == 0 and len(below) == 0:
        continue
    a_ref = above.mean(axis=0) if len(above) else below.mean(axis=0)
    b_ref = below.mean(axis=0) if len(below) else a_ref
    span  = max(1, bot - top)
    t = ((np.arange(top, bot + 1) - top) / span)[:, None]
    bg[top:bot + 1, x] = a_ref * (1 - t) + b_ref * t

# ── the reef at the right-hand end ─────────────────────────────────────
# A column rebuild wants the scene to vary slowly DOWN a column, and at the
# right-hand end it does not: that corner is coral blobs and the hard diagonal
# where the two right-hand oceans meet, so rebuilt columns come out as streaks.
# It is a diagonal scene, so it is rebuilt diagonally: every pixel is taken from
# 105px along and 45px up, which is one step down the same panel edge (measured
# off it: the edge climbs 0.427 for every 1 across) and lands on clean reef past
# where the old lettering reached.
DIAG_X, DIAG_Y = 105, -45
FADE_LO, FADE_HI = 1300, 1340        # blended in over this, so there is no join
ys, xs = np.mgrid[0:bh, 0:bw]
sy = np.clip(ys + DIAG_Y, 0, bh - 1)
sx = np.clip(xs + DIAG_X, 0, bw - 1)
diag = band[sy, sx]
w = np.clip((xs - FADE_LO) / float(FADE_HI - FADE_LO), 0.0, 1.0)[..., None]
w = np.where(gone[..., None], w, 0.0)
bg = bg * (1 - w) + diag * w

# A rebuilt column is perfectly smooth and the field around it is not, so it
# reads as a patch. Put the field's own grain back on, at the level measured
# off it.
bg = np.where(gone[..., None], bg + rng.normal(0, 1, (bh, bw, 3)).astype(np.float32) * GRAIN, bg)

# ── what actually IS the lettering ─────────────────────────────────────
# Whatever inside that halo differs from the rebuilt background: the gold, the
# navy outline, the light rim, the drop shadow and the wide glow, each at the
# strength it really has, so soft edges stay soft. Anything within a few levels
# of the background stays behind, which is what stops the field travelling with
# the letters.
diff = np.abs(band - bg).max(axis=2)
a = np.clip((diff - 8.0) / 22.0, 0.0, 1.0)
a[~gone] = 0.0
# Nothing of the seam travels except the gold of the & painted over it: the
# background there is a crisp white line, and a stranded copy of it 46px to the
# left is the one artefact you cannot look past.
a[:, SEAM_LO:SEAM_HI] *= gold[:, SEAM_LO:SEAM_HI]

k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
a = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 0, a)
a = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, a)
a = np.clip(a, 0, 1)

# ── lift, rebuild, put back down 46px to the left ──────────────────────
out = np.where(gone[..., None], bg, band)
lay_a = np.zeros_like(a);    lay_a[:, :DX] = a[:, -DX:]
lay_c = np.zeros_like(band); lay_c[:, :DX] = band[:, -DX:]
out = out * (1.0 - lay_a[..., None]) + lay_c * lay_a[..., None]

full[Y0:Y1] = out
Image.fromarray(np.clip(full, 0, 255).astype(np.uint8)).save(OUT)

b = np.asarray(Image.open(OUT).convert("RGB")).astype(np.int16)
gm = (b[..., 0] > 228) & (b[..., 1] > 175) & (b[..., 1] < 240) & (b[..., 2] < 160) & ((b[..., 0] - b[..., 2]) > 90)
gm[:40] = False; gm[200:] = False
xs = np.where(gm.sum(axis=0) > 5)[0]
print("title x %d..%d  centre %.1f   image centre %.1f" % (xs.min(), xs.max(), (xs.min()+xs.max())/2, W/2))
