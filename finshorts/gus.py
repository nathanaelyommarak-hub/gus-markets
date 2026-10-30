"""Gus the goldfish - procedurally drawn layered sprites (no external art, fully owned).

Layers: fishbowl (back glass, water, front glass highlights) + Gus sprite variants
(eyes open/half/closed x mouth 0..4, optional snorkel). Animated per frame by render.py.
"""
import math
from PIL import Image, ImageDraw, ImageFilter

SS = 3  # supersampling factor for anti-aliasing

ORANGE = (255, 138, 61)     # brand "Gus orange" #FF8A3D
ORANGE_D = (222, 102, 34)
ORANGE_L = (255, 182, 120)
BELLY = (255, 210, 150)
NAVY = (22, 34, 74)
NAVY_L = (44, 62, 120)
OUTLINE = (92, 44, 10)
WHITE = (250, 250, 245)
BLACK = (18, 18, 22)
YELLOW = (250, 204, 40)
RED = (215, 40, 40)

SPRITE_W, SPRITE_H = 540, 520


_S = SS  # current supersampling scale (SS * output scale), set by draw_gus


def _d(img):
    return ImageDraw.Draw(img)


def _ell(d, cx, cy, rx, ry, fill, outline=OUTLINE, w=4):
    s = _S
    d.ellipse([(cx - rx) * s, (cy - ry) * s, (cx + rx) * s, (cy + ry) * s], fill=fill,
              outline=outline, width=int(w * s) if outline else 0)


def _poly(d, pts, fill, outline=OUTLINE, w=4):
    s = _S
    p = [(x * s, y * s) for x, y in pts]
    d.polygon(p, fill=fill)
    if outline:
        d.line(p + [p[0]], fill=outline, width=int(w * s), joint="curve")


def _line(d, pts, fill, w):
    s = _S
    d.line([(x * s, y * s) for x, y in pts], fill=fill, width=int(w * s), joint="curve")


def _arc(d, cx, cy, rx, ry, a0, a1, fill, w):
    s = _S
    d.arc([(cx - rx) * s, (cy - ry) * s, (cx + rx) * s, (cy + ry) * s], a0, a1, fill=fill, width=int(w * s))


LIDS = {"open": 0.52, "half": 0.8, "closed": 1.08}


def draw_gus(eyes="open", mouth=0, snorkel=False, tail_phase=0.0, scale=1.0, layer="all"):
    """Return RGBA sprite (SPRITE_W*scale x SPRITE_H*scale). Fish faces LEFT (towards viewer-left), deadpan.
    eyes: "open" | "half" | "closed" or a float eyelid position (0.52 open .. 1.08 closed).
    layer: "all" | "tail" (tail fan only, drawn behind) | "body" (everything except the tail) - lets the renderer
    animate the tail continuously at 60 fps without pre-rendering every eyes x mouth x tail combination."""
    global _S
    _S = SS * scale
    W, H = SPRITE_W, SPRITE_H
    img = Image.new("RGBA", (int(W * _S), int(H * _S)), (0, 0, 0, 0))
    d = _d(img)
    cx, cy = 250, 250          # body centre
    # --- tail (fan) ---
    sw = 14 * math.sin(tail_phase)
    if layer == "body":
        tail_phase = None
    tail = [(cx + 150, cy - 10), (cx + 255, cy - 120 + sw), (cx + 238, cy - 40 + sw * .6),
            (cx + 262, cy + 10 + sw * .3), (cx + 240, cy + 70 + sw * .6), (cx + 258, cy + 125 + sw), (cx + 150, cy + 25)]
    if tail_phase is not None:
        _poly(d, tail, ORANGE_L)
        for k in (-80, -40, 0, 40, 80):
            _line(d, [(cx + 165, cy + 8), (cx + 238, cy + k * 1.2 + sw * .7)], ORANGE_D, 3)
    if layer == "tail":
        return img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
    # --- dorsal fin ---
    dors = [(cx - 70, cy - 128), (cx - 20, cy - 190), (cx + 30, cy - 178), (cx + 70, cy - 186), (cx + 95, cy - 150),
            (cx + 120, cy - 128), (cx + 60, cy - 110)]
    _poly(d, dors, ORANGE_L)
    for x0 in (-30, 10, 50, 85):
        _line(d, [(cx + x0, cy - 120), (cx + x0 + 5, cy - 172)], ORANGE_D, 3)
    # --- bottom fins ---
    _poly(d, [(cx + 20, cy + 120), (cx + 70, cy + 175), (cx + 105, cy + 150), (cx + 80, cy + 110)], ORANGE_L)
    # --- body ---
    _ell(d, cx, cy, 175, 145, ORANGE, w=5)
    # belly highlight
    # colour-filled layer + blurred alpha only (blurring RGBA over transparent black gives a dark fringe)
    ba = Image.new("L", img.size, 0); _ell(_d(ba), cx - 30, cy + 55, 120, 70, 170, outline=None)
    belly = Image.new("RGBA", img.size, BELLY[:3] + (0,))
    belly.putalpha(ba.filter(ImageFilter.GaussianBlur(10 * _S)))
    bm = Image.new("L", img.size, 0); _ell(_d(bm), cx, cy, 170, 140, 255, outline=None)
    from PIL import ImageChops as _IC
    belly.putalpha(_IC.multiply(belly.getchannel("A"), bm))
    img.alpha_composite(belly)
    d = _d(img)
    # scales (arcs)
    for row, yy in enumerate(range(cy - 90, cy + 80, 34)):
        for xx in range(cx - 10 + (row % 2) * 17, cx + 150, 34):
            if ((xx - cx) / 165) ** 2 + ((yy - cy) / 135) ** 2 < 0.78:
                _arc(d, xx, yy, 17, 14, 200, 340, ORANGE_D, 3)
    # gill line
    _arc(d, cx - 55, cy - 5, 70, 110, 300, 60, ORANGE_D, 4)
    # --- necktie (collar band under the head + tie) ---
    collar = [(cx - 95, cy + 95), (cx - 60, cy + 118), (cx - 10, cy + 128), (cx + 20, cy + 122), (cx + 22, cy + 105),
              (cx - 10, cy + 110), (cx - 60, cy + 100), (cx - 92, cy + 78)]
    _poly(d, collar, NAVY, outline=BLACK, w=3)
    kx, ky = cx - 40, cy + 118
    _poly(d, [(kx - 16, ky - 12), (kx + 16, ky - 12), (kx + 11, ky + 14), (kx - 11, ky + 14)], NAVY_L, outline=BLACK, w=3)
    _poly(d, [(kx - 11, ky + 14), (kx + 11, ky + 14), (kx + 24, ky + 105), (kx, ky + 130), (kx - 24, ky + 105)],
          NAVY, outline=BLACK, w=3)
    _line(d, [(kx - 8, ky + 40), (kx + 14, ky + 60)], NAVY_L, 4)
    # --- pectoral fin (in front) ---
    _poly(d, [(cx + 5, cy + 45), (cx + 70, cy + 70), (cx + 85, cy + 120), (cx + 40, cy + 112)], ORANGE_L)
    # --- face ---
    ex1, ey = cx - 118, cy - 40       # front eye
    ex2 = cx - 45                     # back eye (a bit bigger, perspective)
    for ex, r in ((ex2, 40), (ex1, 36)):
        _ell(d, ex, ey, r, r * 1.05, WHITE, outline=BLACK, w=4)
        _ell(d, ex - r * .25, ey + r * .2, r * .38, r * .42, BLACK, outline=None)
        _ell(d, ex - r * .35, ey + r * .05, r * .1, r * .1, WHITE, outline=None)
        # heavy eyelid - deadpan look
        lid = LIDS[eyes] if isinstance(eyes, str) else float(eyes)
        s = _S
        top = ey - r * 1.08
        lid_img = Image.new("L", img.size, 0); ld = _d(lid_img)
        ld.rectangle([(ex - r - 6) * s, top * s, (ex + r + 6) * s, (top + 2 * r * 1.05 * lid) * s], fill=255)
        eye_mask = Image.new("L", img.size, 0); em = _d(eye_mask)
        em.ellipse([(ex - r) * s, (ey - r * 1.05) * s, (ex + r) * s, (ey + r * 1.05) * s], fill=255)
        from PIL import ImageChops
        m = ImageChops.multiply(lid_img, eye_mask)
        img.paste(Image.new("RGBA", img.size, ORANGE + (255,)), (0, 0), m)
        d = _d(img)
        ly = top + 2 * r * 1.05 * lid
        _line(d, [(ex - r * .95, ly), (ex + r * .95, ly)], BLACK, 5)
        _arc(d, ex, ey, r, r * 1.05, 0, 360, BLACK, 4)
        _line(d, [(ex - r * .9, ey - r * 1.32), (ex + r * .9, ey - r * 1.22)], OUTLINE, 6)   # flat brow
    # --- mouth ---
    mx, my = cx - 158, cy + 42
    if mouth <= 0:
        _arc(d, mx + 14, my + 14, 24, 12, 200, 320, BLACK, 5)      # small frown
    else:
        h = 5 + mouth * 6
        _ell(d, mx + 12, my + 6, 15 + mouth * 2, h, (90, 20, 20), outline=BLACK, w=4)
    # cheek
    _arc(d, mx + 40, my + 5, 22, 18, 250, 330, ORANGE_D, 3)
    # --- snorkel ---
    if snorkel:
        sx = mx + 8
        _line(d, [(sx + 10, my + 10), (sx - 22, my + 10)], YELLOW, 16)
        _line(d, [(sx - 22, my + 10), (sx - 32, my - 8), (sx - 34, cy - 205)], YELLOW, 16)
        _line(d, [(sx - 22, my + 10), (sx - 32, my - 8), (sx - 34, cy - 205)], (190, 150, 20), 3)
        _ell(d, sx - 34, cy - 212, 14, 10, RED, outline=BLACK, w=3)
        _ell(d, sx + 10, my + 10, 9, 11, RED, outline=BLACK, w=3)
    return img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)


def sprite_set(snorkel=False):
    """Pre-render all variants: dict[(eyes, mouth, tail_idx)] -> RGBA."""
    out = {}
    for e in ("open", "half", "closed"):
        for m in range(5):
            for ti in range(4):
                out[(e, m, ti)] = draw_gus(e, m, snorkel, tail_phase=ti * math.pi / 2)
    return out


# ---------------- fishbowl ----------------
class Bowl:
    def __init__(self, cx=540, cy=1270, r=300):
        self.cx, self.cy, self.r = cx, cy, r
        self.top = cy - int(r * 0.80)        # rim height
        self.rim_w = int(r * 0.62)
        W, H = 1080, 1920
        s = SS
        # mask of bowl interior (circle cut at rim)
        m = Image.new("L", (W, H), 0); md = ImageDraw.Draw(m)
        md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        md.rectangle([0, 0, W, self.top], fill=0)
        self.mask = m
        # front glass highlights
        g = Image.new("RGBA", (W * s // 2, H * s // 2), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        k = s / 2
        gd.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], outline=(210, 230, 255, 170), width=int(6 * k))
        gd.arc([(cx - r + 40) * k, (cy - r + 40) * k, (cx + r - 40) * k, (cy + r - 40) * k], 150, 215,
               fill=(255, 255, 255, 120), width=int(14 * k))
        gd.arc([(cx - r + 70) * k, (cy - r + 70) * k, (cx + r - 70) * k, (cy + r - 70) * k], 300, 330,
               fill=(255, 255, 255, 90), width=int(10 * k))
        # rim
        gd.ellipse([(cx - self.rim_w - 18) * k, (self.top - 30) * k, (cx + self.rim_w + 18) * k, (self.top + 30) * k],
                   outline=(225, 240, 255, 220), width=int(9 * k))
        g = g.resize((W, H), Image.LANCZOS)
        # cut away the part of the circle outline above the rim
        cut = Image.new("L", (W, H), 255); ImageDraw.Draw(cut).rectangle([0, 0, W, self.top - 28], fill=0)
        a = g.getchannel("A"); from PIL import ImageChops
        rim_only = Image.new("L", (W, H), 0)
        ImageDraw.Draw(rim_only).ellipse([cx - self.rim_w - 30, self.top - 40, cx + self.rim_w + 30, self.top + 40], fill=255)
        g.putalpha(ImageChops.lighter(ImageChops.multiply(a, cut), ImageChops.multiply(a, rim_only)))
        self.front = g
        # base / shadow
        b = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bd = ImageDraw.Draw(b)
        bd.ellipse([cx - 230, cy + r - 40, cx + 230, cy + r + 30], fill=(170, 200, 230, 110), outline=(220, 235, 255, 180), width=4)
        bd.ellipse([cx - 330, cy + r + 10, cx + 330, cy + r + 60], fill=(0, 0, 0, 90))
        self.base = b.filter(ImageFilter.GaussianBlur(2))
        # back glass tint
        bk = Image.new("RGBA", (W, H), (170, 205, 240, 28)); bk.putalpha(m.point(lambda v: 28 if v else 0))
        self.back = bk

    def level_y(self, level):
        """level 0..1 (0 = bottom, 1 = rim) -> surface y pixel."""
        bottom = self.cy + self.r - 30
        return int(bottom - (bottom - (self.top + 18)) * level)
