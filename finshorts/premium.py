"""Premium renderer: native 2160x3840 @ 60 fps, motion-graphics scene system (own designs only).

Every animated value is a pure function of time t, so frames can be rendered out of order: the frame range is
split into segments that worker processes render + encode in parallel (fork, copy-on-write assets), then the
segments are joined losslessly (concat demuxer, -c copy) and muxed with the mixed audio.

Design system (all OFL fonts in assets/fonts):
  Anton            display numbers / big headings
  Montserrat XBold kinetic captions, chips, card titles
  Inter            body text, labels, source lines
  palette          navy #0B1630 / deep #050A18 / card #10203F, mint #22E39B (up), coral #FF4D5E (down),
                   Gus orange #FF8A3D (numbers + brand), white, muted #96A8CD
Scenes (driven by the script timeline): HERO (hook: big number + direction) -> CHART (real series drawn on, counter
follows the line head, asset badges slide in when spoken) -> SAVE pill -> NEWS card (headline lines reveal, outlet
chips) -> PERSON card (free-licensed portrait or own silhouette, Ken Burns) -> GUS'S TAKE (Gus rises + push-in)
-> POLL card -> loop wipe back into the hero. Gus is animated every frame: bob, tilt, tail swish, blinks,
lip-flap from the voice envelope, water level that tracks the market, bubbles, swaying seaweed.
"""
import math, os, re, random, subprocess, time, json, pickle, datetime as dt
import numpy as np, soundfile as sf
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from . import render as R
from . import motion as M
from .gus import draw_gus

ROOT = Path(__file__).resolve().parent.parent
F_ANTON = str(ROOT / "assets/fonts/Anton-Regular.ttf")
F_MONT = str(ROOT / "assets/fonts/Montserrat-ExtraBold.ttf")
F_INTER = str(ROOT / "assets/fonts/Inter.ttf")
W, H = 2160, 3840
NAVY, NAVY_D, CARD, CARD_HI, EDGE = (11, 22, 48), (5, 10, 24), (16, 32, 63), (26, 46, 90), (44, 64, 112)
MUTED, MUTED_D = (150, 168, 205), (104, 120, 160)
MINT, CORAL, ORANGE, WHITE, INK = R.MINT, R.CORAL, R.ORANGE, (255, 255, 255), (10, 18, 40)
MOOD = {"green": MINT, "red": CORAL, "neutral": ORANGE}
STAGE_X, STAGE_Y, STAGE_W, STAGE_H = 100, 540, 1960, 1620        # main card area
CAP_Y, CAP_Y_TAKE = 2335, 1240                                   # caption baseline centre (normal / Gus's take)
BOWL_C, BOWL_R = (1080, 2985), 500                               # fishbowl centre + radius (normal layout)
TAKE_C, TAKE_S = (1080, 2330), 1.28                              # Gus's take: bowl centre + scale
REG_W, REG_H, REG_BC = 1300, 1320, (650, 650)                    # Gus region and the bowl centre inside it
FISH_SCALE = 0.95 * BOWL_R / 345
CTX = {}


# ---------------- helpers ----------------
_FC = {}
def font(path, size, weight=None):
    key = (path, int(size), weight)
    if key not in _FC:
        f = ImageFont.truetype(path, int(size))
        if path == F_INTER:
            try: f.set_variation_by_name(weight or "Bold")
            except Exception: pass
        _FC[key] = f
    return _FC[key]


def clamp(x, a=0.0, b=1.0): return a if x < a else b if x > b else x
def lerp(a, b, k): return a + (b - a) * k
def e_out(k): k = clamp(k); return 1 - (1 - k) ** 3
def e_in(k): k = clamp(k); return k * k * k
def e_io(k): k = clamp(k); return k * k * (3 - 2 * k) if k < 1 else 1.0
def e_quint(k): k = clamp(k); return 1 - (1 - k) ** 5
def e_back(k, s=1.6): k = clamp(k) - 1; return k * k * ((s + 1) * k + s) + 1
def e_elastic(k):
    k = clamp(k)
    return 1.0 if k >= 1 else 1 - math.cos(k * math.pi * 4.5) * math.exp(-k * 6.5)


def window(t, t0, t1, fin=0.45, fout=0.3):
    """0..1 visibility of an element shown in [t0, t1] with eased in/out ramps."""
    if t < t0 or t > t1 + fout: return 0.0
    return min(e_out((t - t0) / fin) if fin else 1.0, 1 - e_in((t - t1) / fout) if t > t1 else 1.0)


def text_img(txt, f, fill, stroke=0, stroke_fill=INK, shadow=0, pad=None):
    """Tight RGBA image of a text run (+ optional stroke and soft drop shadow)."""
    pad = pad if pad is not None else stroke + shadow * 2 + 6
    l, t_, r, b = f.getbbox(txt, stroke_width=stroke)
    im = Image.new("RGBA", (r - l + 2 * pad, b - t_ + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad - l, pad - t_ + shadow * 0.6), txt, font=f, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        im = sh.filter(ImageFilter.GaussianBlur(shadow)); d = ImageDraw.Draw(im)
    d.text((pad - l, pad - t_), txt, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def vgrad(w, h, top, bot, alpha=255):
    k = np.linspace(0, 1, h)[:, None]
    rgb = (np.array(top)[None, :] * (1 - k) + np.array(bot)[None, :] * k).astype(np.uint8)
    a = np.full((h, 1), alpha, np.uint8)
    return Image.fromarray(np.repeat(np.concatenate([rgb, a], 1)[:, None, :], w, axis=1), "RGBA")


def rmask(w, h, r):
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def card(w, h, r=56, top=CARD_HI, bot=CARD, edge=EDGE, accent=None, alpha=246):
    """Brand card: vertical gradient, hairline edge, top sheen, optional accent bar on the left."""
    im = vgrad(w, h, top, bot, alpha)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), rmask(w, h, r)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, outline=edge + (255,), width=4)
    # top sheen: soft vertical fade (no hard band edge)
    sh_h = min(h, 420)
    fade = (np.clip(1 - np.arange(sh_h) / sh_h, 0, 1) ** 1.6 * 16).astype(np.uint8)
    sa = Image.new("L", (w, h), 0); sa.paste(Image.fromarray(np.repeat(fade[:, None], w, 1)), (0, 0))
    sheen = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    sheen.putalpha(ImageChops.multiply(sa, rmask(w, h, r)))
    im.alpha_composite(sheen)
    if accent:
        d.rounded_rectangle([0, 0, 22, h - 1], radius=11, fill=accent + (255,))
    return im


def shadowed(im, blur=34, off=(0, 26), a=165):
    pad = blur * 2
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, a), (pad + off[0], pad + off[1]), im.getchannel("A"))
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(im, (pad, pad))
    return out, pad


def pill(txt, f, fill, fg=INK, padx=44, h=None, arrow=0):
    tw = f.getlength(txt); h = h or int(f.size * 1.55)
    aw = int(h * 0.42) + 18 if arrow else 0
    w = int(tw + 2 * padx + aw)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=fill + (255,))
    x = padx
    if arrow:
        s = h * 0.21; cx, cy = x + s, h / 2
        if arrow > 0: d.polygon([(cx - s, cy + s * .8), (cx + s, cy + s * .8), (cx, cy - s)], fill=fg)
        else: d.polygon([(cx - s, cy - s * .8), (cx + s, cy - s * .8), (cx, cy + s)], fill=fg)
        x += aw
    d.text((x, h / 2), txt, font=f, fill=fg, anchor="lm")
    return im


def blit(fr, im, cx, cy, alpha=1.0, scale=1.0):
    """Paste RGBA `im` centred at (cx, cy) onto the RGB frame with alpha + scale (about its centre)."""
    if alpha <= 0.004 or scale <= 0.01: return
    if abs(scale - 1) > 0.004:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if alpha < 0.996:
        a = im.getchannel("A").point(lambda v, k=alpha: int(v * k))
        fr.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), a)
    else:
        fr.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)


def wrap(text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        tr = (cur + " " + w).strip()
        if f.getlength(tr) <= maxw: cur = tr
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines


def fmt_like(v, ref):
    """Format a rolling counter value like the final on-screen string `ref`."""
    if ref.startswith("$"):
        from .script_gen import money
        return money(max(v, 0))
    if ref.endswith("%"): return f"{v:.2f}%"
    return f"{v:,.0f}"


def parse_num(s):
    try: return float(re.sub(r"[^0-9.\-]", "", s))
    except Exception: return None


def _ts(x):
    x = str(x)
    try:
        return dt.datetime.fromisoformat(x[:19]) if "T" in x else dt.datetime.fromisoformat(x[:10])
    except Exception:
        return None


# ---------------- background (static, baked once) ----------------
def make_background(job):
    mood_c = MOOD[job["mood"]]
    bg = vgrad(W, H, (13, 27, 60), (4, 8, 20)).convert("RGB")
    a = np.asarray(bg).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    glow = np.exp(-(((xx - W / 2) / 1500) ** 2 + ((yy - 700) / 1100) ** 2))[..., None]
    a = a + glow * np.array(mood_c, np.float32) * 0.13
    glow2 = np.exp(-(((xx - W / 2) / 1300) ** 2 + ((yy - 3000) / 900) ** 2))[..., None]
    a = a + glow2 * np.array((60, 120, 220), np.float32) * 0.10
    vig = 1 - 0.38 * (((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.7)) ** 2)
    a *= np.clip(vig, 0.55, 1)[..., None]
    a += np.random.default_rng(7).normal(0, 1.1, a.shape[:2])[..., None]     # dither against banding
    bg = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    # faint grid
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
    for x in range(60, W, 140): gd.line([(x, 480), (x, H)], fill=(120, 150, 220, 11), width=2)
    for y in range(480, H, 140): gd.line([(0, y), (W, y)], fill=(120, 150, 220, 11), width=2)
    bgr = bg.convert("RGBA"); bgr.alpha_composite(g)
    d = ImageDraw.Draw(bgr)
    # header: format pill + date + handle
    lab = pill(job["label"], font(F_MONT, 66), mood_c, INK, padx=50, h=124)
    bgr.alpha_composite(lab, (100, 96))
    d.text((W - 100, 128), job["date_str"].upper(), font=font(F_INTER, 54), fill=(190, 202, 228), anchor="rm")
    d.text((W - 100, 198), job.get("handle", ""), font=font(F_INTER, 48, "SemiBold"), fill=MUTED, anchor="rm")
    # ticker lane
    d.rounded_rectangle([0, 268, W, 380], radius=0, fill=(6, 12, 30, 255))
    d.line([(0, 268), (W, 268)], fill=EDGE + (255,), width=3); d.line([(0, 380), (W, 380)], fill=EDGE + (255,), width=3)
    # disclaimer / data line (bottom, small)
    d.text((W // 2, 3772), job["source_line"], font=font(F_INTER, 40, "Regular"), fill=(110, 125, 162), anchor="mm")
    return bgr.convert("RGB")


def make_orbs(mood):
    out = []
    for col, r in ((MOOD[mood], 520), ((70, 120, 230), 620), (ORANGE, 380), ((70, 120, 230), 450)):
        im = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([r * .35, r * .35, r * 1.65, r * 1.65], fill=col + (34,))
        out.append(im.filter(ImageFilter.GaussianBlur(r * 0.28)))
    return out


def ticker_strip(items):
    f = font(F_INTER, 60, "SemiBold"); fb = font(F_INTER, 60, "Bold")
    parts = []
    for name, val, chg in items:
        parts.append((name + "  ", (170, 186, 222), f)); parts.append((val + "  ", WHITE, fb))
        if chg is not None:
            parts.append((("\u25B2 " if chg > 0 else "\u25BC ") + f"{abs(chg):.1f}%", MINT if chg > 0 else CORAL, fb))
        parts.append(("      \u2022      ", (70, 90, 140), f))
    wt = int(sum(ff.getlength(p) for p, _, ff in parts)) + 20
    im = Image.new("RGBA", (wt, 112), (0, 0, 0, 0)); d = ImageDraw.Draw(im); x = 0
    for p, c, ff in parts:
        d.text((x, 56), p, font=ff, fill=c, anchor="lm"); x += ff.getlength(p)
    return im


# ---------------- Gus + fishbowl ----------------
LIDS = [0.52, 0.66, 0.80, 0.94, 1.08]
N_TAIL = 16


def _shade(im):
    """Soft volume shading (light from top-left) on a sprite, alpha kept."""
    a = np.asarray(im).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    k = 1.10 - 0.26 * np.clip((yy / h) * 0.75 + (xx / w) * 0.35, 0, 1)
    a[..., :3] = np.clip(a[..., :3] * k[..., None], 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def make_gus(snorkel):
    body = {(li, m): _shade(draw_gus(LIDS[li], m, snorkel, 0, scale=FISH_SCALE, layer="body")) for li in range(5) for m in range(5)}
    tail = [_shade(draw_gus("open", 0, False, 2 * math.pi * i / N_TAIL, scale=FISH_SCALE, layer="tail")) for i in range(N_TAIL)]
    return body, tail


def make_bowl(mood):
    cx, cy = REG_BC; r = BOWL_R
    top = cy - int(r * 0.80); rim_w = int(r * 0.62)
    S = 2
    mask = Image.new("L", (REG_W, REG_H), 0); md = ImageDraw.Draw(mask)
    md.ellipse([cx - r + 6, cy - r + 6, cx + r - 6, cy + r - 6], fill=255); md.rectangle([0, 0, REG_W, top], fill=0)
    # base: floor shadow, stand, mood glow ring
    base = Image.new("RGBA", (REG_W, REG_H), (0, 0, 0, 0)); bd = ImageDraw.Draw(base)
    bd.ellipse([cx - r - 40, cy - r - 40, cx + r + 40, cy + r + 40], outline=MOOD[mood] + (70,), width=26)
    base = base.filter(ImageFilter.GaussianBlur(26)); bd = ImageDraw.Draw(base)
    sh = Image.new("RGBA", (REG_W, REG_H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([cx - 430, cy + r + 20, cx + 430, cy + r + 110], fill=(0, 0, 0, 150))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    st = vgrad(560, 110, (40, 62, 116), (18, 30, 62)); st.putalpha(ImageChops.multiply(st.getchannel("A"), rmask(560, 110, 40)))
    base.alpha_composite(st, (cx - 280, cy + r - 60))
    bd = ImageDraw.Draw(base); bd.line([(cx - 250, cy + r - 52), (cx + 250, cy + r - 52)], fill=(90, 120, 190, 200), width=5)
    # back: glass tint, pebbles, sand
    back = Image.new("RGBA", (REG_W, REG_H), (0, 0, 0, 0)); kd = ImageDraw.Draw(back)
    kd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(160, 200, 245, 26))
    rnd = random.Random(3)
    kd.ellipse([cx - r + 40, cy + r - 150, cx + r - 40, cy + r + 60], fill=(214, 182, 120, 255))
    for _ in range(70):
        x = rnd.uniform(cx - r * 0.85, cx + r * 0.85); y = rnd.uniform(cy + r - 120, cy + r - 20)
        s = rnd.uniform(14, 30); c = rnd.choice([(255, 138, 61), (34, 200, 150), (240, 232, 214), (90, 120, 190), (255, 180, 110)])
        kd.ellipse([x - s, y - s * .7, x + s, y + s * .7], fill=c + (255,), outline=(40, 40, 60, 120), width=2)
    # front: glass outline, highlights, rim (supersampled)
    fg = Image.new("RGBA", (REG_W * S, REG_H * S), (0, 0, 0, 0)); gd = ImageDraw.Draw(fg)
    def E(b, **k): gd.ellipse([v * S for v in b], **k)
    def A(b, a0, a1, **k): gd.arc([v * S for v in b], a0, a1, **k)
    E([cx - r, cy - r, cx + r, cy + r], outline=(210, 232, 255, 190), width=9 * S)
    A([cx - r + 46, cy - r + 46, cx + r - 46, cy + r - 46], 148, 212, fill=(255, 255, 255, 150), width=22 * S)
    A([cx - r + 90, cy - r + 90, cx + r - 90, cy + r - 90], 300, 328, fill=(255, 255, 255, 110), width=14 * S)
    A([cx - r + 30, cy - r + 30, cx + r - 30, cy + r - 30], 20, 60, fill=(255, 255, 255, 60), width=10 * S)
    fg = fg.resize((REG_W, REG_H), Image.LANCZOS)
    cut = Image.new("L", (REG_W, REG_H), 255); ImageDraw.Draw(cut).rectangle([0, 0, REG_W, top - 4], fill=0)
    fg.putalpha(ImageChops.multiply(fg.getchannel("A"), cut))
    rim = Image.new("RGBA", (REG_W * S, REG_H * S), (0, 0, 0, 0)); rd = ImageDraw.Draw(rim)
    rd.ellipse([(cx - rim_w - 24) * S, (top - 38) * S, (cx + rim_w + 24) * S, (top + 38) * S], outline=(228, 242, 255, 235), width=13 * S)
    rd.arc([(cx - rim_w - 24) * S, (top - 38) * S, (cx + rim_w + 24) * S, (top + 38) * S], 200, 300, fill=(255, 255, 255, 255), width=7 * S)
    fg.alpha_composite(rim.resize((REG_W, REG_H), Image.LANCZOS))
    water = vgrad(REG_W, REG_H, (96, 178, 240), (28, 74, 168), 168)
    return {"mask": mask, "base": base, "back": back, "front": fg, "water": water, "top": top, "bottom": cy + r - 40,
            "np_mask": np.asarray(mask)}


def level_y(level):
    b = CTX["bowl"]
    return b["bottom"] - (b["bottom"] - (b["top"] + 26)) * level


def water_level(t):
    c = CTX; L0, tgt, dur = 0.70, c["level"], c["dur"]
    k = clamp(t / 1.6)
    lv = L0 + (tgt - L0) * (e_out(k) + 0.10 * math.sin(k * math.pi) * (1 if tgt >= L0 else -1))
    ch = c.get("chart_rev")
    if ch and ch[0] <= t <= ch[1] + 0.6:                       # water follows the chart line while it draws
        p = clamp((t - ch[0]) / (ch[1] - ch[0])); vn = c["chart_norm"]
        v = np.interp(e_io(p) * (len(vn) - 1), np.arange(len(vn)), vn)
        lv += (v - vn[-1]) * 0.22 * window(t, ch[0], ch[1], 0.3, 0.6)
    lv += 0.012 * math.sin(t * 1.3)
    if t > dur - 0.9: lv = lerp(lv, L0, e_io((t - (dur - 0.9)) / 0.8))     # loop: back to the opening level
    return clamp(lv, 0.30, 0.95)


def fish_pose(t):
    c = CTX; b = c["bowl"]
    sy = level_y(water_level(t))
    bob = 16 * math.sin(2 * math.pi * t / 2.3)
    fh = c["fish_h"]
    fx = REG_BC[0] + 34 * math.sin(2 * math.pi * t / 6.1)
    fy = min(sy + fh * 0.46, b["bottom"] - fh * 0.42) + bob
    ang = 3.2 * math.sin(2 * math.pi * t / 2.3 + 0.9)
    return fx, fy, ang, sy


def lid_at(t):
    c = CTX; lid = 0.52
    for b in c["blinks"]:
        d = t - b
        if 0 <= d < 0.22: lid = max(lid, 0.52 + 0.56 * math.sin(math.pi * d / 0.22))
    if c.get("deadpan") and t > c["deadpan"]:
        lid = max(lid, lerp(0.52, 0.86, e_io((t - c["deadpan"]) / 0.5)))
    if t > c["dur"] - 0.35: lid = 0.52
    return min(range(5), key=lambda i: abs(LIDS[i] - lid))


def render_gus(t, i):
    c = CTX; b = c["bowl"]
    fx, fy, ang, sy = fish_pose(t)
    reg = b["back"].copy()
    # water body under a two-wave surface
    pts = [(x, sy + 9 * math.sin(x / 70 + t * 3.1) + 5 * math.sin(x / 31 - t * 2.3)) for x in range(0, REG_W + 12, 12)]
    wm = Image.new("L", (REG_W, REG_H), 0)
    ImageDraw.Draw(wm).polygon(pts + [(REG_W, REG_H), (0, REG_H)], fill=255)
    reg.paste(b["water"], (0, 0), ImageChops.multiply(wm, b["water"].getchannel("A")))
    d = ImageDraw.Draw(reg)
    # seaweed (sways)
    for sx, hgt, ph in ((REG_BC[0] - 300, 330, 0.0), (REG_BC[0] + 330, 260, 1.7), (REG_BC[0] - 220, 210, 3.1)):
        base_y = b["bottom"] + 10; seg = [(sx + 26 * math.sin(t * 1.6 + ph + k * 0.5) * (k / 8), base_y - hgt * k / 8) for k in range(9)]
        d.line(seg, fill=(28, 168, 122, 255), width=26, joint="curve")
        d.line(seg, fill=(80, 220, 160, 255), width=8, joint="curve")
    # fish = tail + body, gently rotated
    mouth = c["mouth"][min(i, len(c["mouth"]) - 1)]
    tail = c["tail"][int((t * 1.7 % 1) * N_TAIL) % N_TAIL]
    fish = tail.copy(); fish.alpha_composite(c["body"][(lid_at(t), mouth)])
    fish = fish.rotate(ang, resample=Image.BICUBIC, expand=False)
    reg.alpha_composite(fish, (int(fx - fish.width / 2), int(fy - fish.height / 2)))
    # bubbles (deterministic: spawned at the fish mouth, rise to the surface)
    for (ts, x0, y0, rr, sp, ph) in c["bubbles"]:
        a = t - ts
        if a < 0 or a > 6: continue
        y = y0 - sp * a
        if y < sy + 10: continue
        x = x0 + 12 * math.sin(a * 3 + ph)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=(225, 242, 255, 220), width=4)
        d.ellipse([x - rr * .45, y - rr * .6, x - rr * .05, y - rr * .2], fill=(255, 255, 255, 200))
    d.line(pts, fill=(190, 228, 255, 235), width=8)
    al = np.minimum(np.asarray(reg.getchannel("A")), b["np_mask"])
    reg.putalpha(Image.fromarray(al))
    out = b["base"].copy(); out.alpha_composite(reg); out.alpha_composite(b["front"])
    return out


# ---------------- stage scenes ----------------
def build_hero(job):
    w, h = STAGE_W, 1240
    base = card(w, h, accent=None)
    d = ImageDraw.Draw(base)
    ch = job.get("chart") or {}
    col = MOOD[job["mood"]] if not job.get("big_dir") else (MINT if job["big_dir"] > 0 else CORAL)
    # faint full chart silhouette inside the hero
    pts = [p[1] for p in ch.get("pts", [])]
    deco = None
    if len(pts) > 2:
        lo, hi = min(pts), max(pts); hi = hi if hi > lo else lo + 1
        xs = np.linspace(60, w - 60, len(pts)); ys = h - 90 - (np.array(pts) - lo) / (hi - lo) * 420
        deco = Image.new("RGBA", (w, h), (0, 0, 0, 0)); dd = ImageDraw.Draw(deco)
        dd.polygon(list(zip(xs, ys)) + [(w - 60, h - 60), (60, h - 60)], fill=col + (26,))
        dd.line(list(zip(xs, ys)), fill=col + (120,), width=8, joint="curve")
        deco = deco.filter(ImageFilter.GaussianBlur(1.2))
    name = (ch.get("name") or job.get("big_label", "").split(" \u00B7 ")[0] or "MARKETS").upper()
    lbl = text_img(name, font(F_ANTON, 150), WHITE)
    sub = pill(job.get("big_sub", ""), font(F_MONT, 64), col, INK, padx=46, h=128, arrow=job.get("big_dir", 0))
    asof = job.get("big_label", "").split(" \u00B7 ")[-1]
    return {"base": base, "deco": deco, "label": lbl, "sub": sub, "asof": text_img(asof, font(F_INTER, 50, "SemiBold"), MUTED),
            "col": col, "w": w, "h": h, "value": job.get("big_value", ""),
            "v0": pts[0] if pts else None, "v1": parse_num(job.get("big_value", "")) if job.get("big_value") else None}


def draw_hero(S, t, tl):
    im = S["base"].copy(); w = S["w"]
    if S["deco"] is not None:
        rv = int(w * e_io((tl - 0.1) / 1.4))
        if rv > 4: im.alpha_composite(S["deco"].crop((0, 0, rv, S["h"])), (0, 0))
    k = e_back((tl - 0.05) / 0.5)
    im.alpha_composite(S["label"], (110, int(120 + (1 - k) * 60)))
    # rolling counter -> exact value string at the end
    p = e_quint((tl - 0.1) / 1.0)
    txt = S["value"] if p >= 0.999 or S["v0"] is None or S["v1"] is None else fmt_like(lerp(S["v0"], S["v1"], p), S["value"])
    f = font(F_ANTON, 330)
    tw = f.getlength(txt)
    while tw > w - 200: f = font(F_ANTON, f.size - 10); tw = f.getlength(txt)
    glow = Image.new("RGBA", (w, 460), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((110, 230), txt, font=f, fill=S["col"] + (110,), anchor="lm")
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28)), (0, 300))
    ImageDraw.Draw(im).text((110, 530), txt, font=f, fill=WHITE, anchor="lm")
    ks = e_back((tl - 0.55) / 0.45)
    if ks > 0:
        sp = S["sub"].resize((max(1, int(S["sub"].width * ks)), max(1, int(S["sub"].height * ks))), Image.BILINEAR) if ks < 0.995 else S["sub"]
        im.alpha_composite(sp, (110, int(800 - sp.height / 2 + 60 * (1 - ks))))
    if tl > 0.8:
        a = S["asof"].copy(); a.putalpha(a.getchannel("A").point(lambda v, q=e_out((tl - 0.8) / 0.4): int(v * q)))
        im.alpha_composite(a, (110, 940))
    return im


def nice_ticks(lo, hi, n=4):
    raw = (hi - lo) / n; mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    v = math.ceil(lo / step) * step; out = []
    while v <= hi + 1e-9: out.append(round(v, 10)); v += step
    return out


def tick_label(v, vf):
    if "%" in vf: return f"{v:.2f}".rstrip("0").rstrip(".") + "%"
    if abs(v) >= 1000: return f"{v:,.0f}"
    if abs(v) >= 10: return f"{v:,.1f}".rstrip("0").rstrip(".")
    return f"{v:,.2f}"


def build_chart(job, badges):
    ch = job["chart"]; w, h = STAGE_W, 1180
    col = MINT if ch["dir"] > 0 else CORAL if ch["dir"] < 0 else ORANGE
    base = card(w, h)
    d = ImageDraw.Draw(base)
    d.text((80, 112), ch["name"].upper(), font=font(F_ANTON, 112), fill=WHITE, anchor="lm")
    per = pill(ch["period"].upper(), font(F_MONT, 46), CARD_HI, MUTED, padx=34, h=88)
    base.alpha_composite(per, (int(100 + font(F_ANTON, 112).getlength(ch["name"].upper())), 70))
    pts = ch["pts"]; vals = np.array([p[1] for p in pts], float)
    lo, hi = vals.min(), vals.max(); span = (hi - lo) or abs(hi) * 0.01 or 1
    lo -= span * 0.10; hi += span * 0.10
    px0, py0, pw, ph = 70, 400, w - 360, 600       # plot box inside the card
    # grid + y labels (right) + x labels
    vf = ch.get("vfmt", "{:,.0f}")
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gdr = ImageDraw.Draw(grid)   # translucent lines need their own layer
    for v in nice_ticks(lo, hi):
        y = py0 + ph * (hi - v) / (hi - lo)
        gdr.line([(px0, y), (px0 + pw, y)], fill=(255, 255, 255, 26), width=3)
        d.text((px0 + pw + 34, y), tick_label(v, vf), font=font(F_INTER, 40, "SemiBold"), fill=MUTED_D, anchor="lm")
    tss = [_ts(p[0]) for p in pts]
    if all(tss):
        for k in range(4):
            j = int(round(k * (len(pts) - 1) / 3)); x = px0 + pw * j / (len(pts) - 1)
            lab = tss[j].strftime("%H:%M") if ch["days"] <= 1 else f"{tss[j]:%b} {tss[j].day}"
            d.text((x, py0 + ph + 52), lab.upper(), font=font(F_INTER, 38, "SemiBold"), fill=MUTED_D,
                   anchor="lm" if k == 0 else "rm" if k == 3 else "mm")
    base.alpha_composite(grid)
    d.text((80, h - 64), ch["source"], font=font(F_INTER, 36, "Regular"), fill=MUTED_D, anchor="lm")
    # plot layer (line + glow + gradient fill), supersampled, revealed left->right per frame
    S2 = 2
    xs = px0 + pw * np.arange(len(vals)) / max(1, len(vals) - 1)
    ys = py0 + ph * (hi - vals) / (hi - lo)
    pl = Image.new("RGBA", (w * S2, h * S2), (0, 0, 0, 0)); pd = ImageDraw.Draw(pl)
    poly = [(x * S2, y * S2) for x, y in zip(xs, ys)]
    fm = Image.new("L", pl.size, 0); ImageDraw.Draw(fm).polygon(poly + [(xs[-1] * S2, (py0 + ph) * S2), (xs[0] * S2, (py0 + ph) * S2)], fill=255)
    fillg = vgrad(w * S2, h * S2, col, NAVY, 255)
    gk = np.clip(1 - (np.arange(h * S2)[:, None] / S2 - ys.min()) / ((py0 + ph) - ys.min() + 1), 0, 1) * 92
    fa = (np.asarray(fm).astype(np.float32) / 255 * gk).astype(np.uint8)
    fillg.putalpha(Image.fromarray(fa)); pl.alpha_composite(fillg)
    gl = Image.new("RGBA", pl.size, (0, 0, 0, 0)); ImageDraw.Draw(gl).line(poly, fill=col + (200,), width=34 * S2, joint="curve")
    pl.alpha_composite(gl.filter(ImageFilter.GaussianBlur(22 * S2)))
    pd = ImageDraw.Draw(pl); pd.line(poly, fill=col + (255,), width=13 * S2, joint="curve")
    pl = pl.resize((w, h), Image.LANCZOS)
    first, last = vals[0], vals[-1]
    chg_pill = pill(job.get("big_sub", "").split(" \u00B7 ")[0], font(F_MONT, 58), col, INK, padx=42, h=116, arrow=ch["dir"])
    head = Image.new("RGBA", (220, 220), (0, 0, 0, 0)); hd = ImageDraw.Draw(head)
    hd.ellipse([40, 40, 180, 180], fill=col + (120,)); head = head.filter(ImageFilter.GaussianBlur(22)); hd = ImageDraw.Draw(head)
    hd.ellipse([78, 78, 142, 142], fill=WHITE, outline=col, width=12)
    vn = (vals - vals.min()) / ((vals.max() - vals.min()) or 1)
    return {"base": base, "plot": pl, "xs": xs, "ys": ys, "vals": vals, "w": w, "h": h, "col": col, "pill": chg_pill,
            "head": head, "value": job.get("big_value", ""), "v1": parse_num(job.get("big_value", "")) or last,
            "badges": badges, "plot_box": (px0, py0, pw, ph), "norm": vn, "vf": vf}


REVEAL_T0, REVEAL_LEN = 0.35, 1.8


def draw_chart(S, t, tl):
    """Chart card + badge rail (canvas = STAGE_W x STAGE_H)."""
    canvas = Image.new("RGBA", (STAGE_W, STAGE_H), (0, 0, 0, 0))
    im = S["base"].copy()
    p = e_io((tl - REVEAL_T0) / REVEAL_LEN)
    n = len(S["vals"]); fi = p * (n - 1)
    hx = float(np.interp(fi, np.arange(n), S["xs"])); hy = float(np.interp(fi, np.arange(n), S["ys"]))
    if p > 0.001:
        im.alpha_composite(S["plot"].crop((0, 0, int(hx) + 2, S["h"])), (0, 0))
        d = ImageDraw.Draw(im)
        px0, py0, pw, ph = S["plot_box"]
        for yy in range(int(py0), int(py0 + ph), 34):             # dashed guide under the head
            d.line([(hx, yy), (hx, yy + 16)], fill=(70, 88, 128, 255), width=3)
        pulse = 1 + 0.12 * math.sin(tl * 7)
        hd = S["head"] if abs(pulse - 1) < 0.01 else S["head"].resize((int(220 * pulse), int(220 * pulse)), Image.BILINEAR)
        im.alpha_composite(hd, (int(hx - hd.width / 2), int(hy - hd.height / 2)))
    v = float(np.interp(fi, np.arange(n), S["vals"]))
    txt = S["value"] if p >= 0.999 else fmt_like(v if p > 0 else S["vals"][0], S["value"]) if S["value"] else S["vf"].format(v)
    ImageDraw.Draw(im).text((80, 290), txt, font=font(F_ANTON, 190), fill=WHITE, anchor="lm")
    kp = e_back((tl - REVEAL_T0 - REVEAL_LEN + 0.2) / 0.45)
    if kp > 0.01:
        pp = S["pill"] if kp > 0.995 else S["pill"].resize((max(1, int(S["pill"].width * kp)), max(1, int(S["pill"].height * kp))), Image.BILINEAR)
        im.alpha_composite(pp, (int(S["w"] - 80 - S["pill"].width / 2 - pp.width / 2), int(290 - pp.height / 2)))
    canvas.alpha_composite(im, (0, 0))
    # badge rail: one badge at a time, newest slides in from the right
    for j, b in enumerate(S["badges"]):
        nxt = S["badges"][j + 1]["t"] if j + 1 < len(S["badges"]) else 1e9
        if t < b["t"] - 0.05 or t > nxt + 0.4: continue
        kin = e_back((t - b["t"]) / 0.5, 1.3); kout = e_in((t - nxt) / 0.35) if t > nxt else 0
        x = int((1 - kin) * 1400 - kout * 1500)
        bi = b["img"]
        if t - b["t"] < 1.0:     # sparkline reveal inside the badge
            bi = bi.copy(); sp = b["spark"]; rv = int(sp.width * e_io((t - b["t"] - 0.2) / 0.7))
            if rv > 2: bi.alpha_composite(sp.crop((0, 0, rv, sp.height)), b["spark_xy"])
        else:
            bi = b["full"]
        canvas.alpha_composite(bi, (x, STAGE_H - bi.height)) if 0 <= x < STAGE_W else None
        if x < 0 and x > -bi.width:
            canvas.alpha_composite(bi.crop((-x, 0, bi.width, bi.height)), (0, STAGE_H - bi.height))
    return canvas


def build_badge(a):
    w, h = STAGE_W, 400
    col = MINT if a["chg"] > 0.05 else CORAL if a["chg"] < -0.05 else ORANGE
    im = card(w, h, r=52, accent=col); d = ImageDraw.Draw(im)
    d.ellipse([70, 80, 310, 320], fill=(30, 52, 100, 255), outline=col + (255,), width=8)
    mono = a["ticker"].replace("^", "").replace("&", "").replace(" ", "")[:3]
    d.text((190, 200), mono, font=font(F_ANTON, 96 if len(mono) < 3 else 80), fill=WHITE, anchor="mm")
    d.text((370, 112), a["ticker"], font=font(F_ANTON, 92), fill=WHITE, anchor="lm")
    d.text((372, 192), a["name"].upper(), font=font(F_INTER, 44, "SemiBold"), fill=MUTED, anchor="lm")
    d.text((370, 272), a["price"], font=font(F_ANTON, 110), fill=WHITE, anchor="lm")
    txt = a.get("chg_txt") or f"{abs(a['chg']):.1f}%"
    pl = pill(txt, font(F_MONT, 54), col, INK, padx=36, h=104, arrow=(a["chg"] > 0.05) - (a["chg"] < -0.05))
    im.alpha_composite(pl, (w - 70 - pl.width, 56))
    d.text((w - 70, 196), a["period"].upper(), font=font(F_INTER, 38, "SemiBold"), fill=MUTED_D, anchor="rm")
    if a.get("asof"): d.text((372, 360), a["asof"], font=font(F_INTER, 32, "Regular"), fill=MUTED_D, anchor="lm")
    full = im.copy()
    sp = Image.new("RGBA", (640, 150), (0, 0, 0, 0))
    v = [p[1] for p in a.get("points") or []]
    if len(v) > 1:
        lo, hi = min(v), max(v); hi = hi if hi > lo else lo + 1
        xy = [(10 + 620 * i / (len(v) - 1), 140 - 130 * (x - lo) / (hi - lo)) for i, x in enumerate(v)]
        sd = ImageDraw.Draw(sp); sd.polygon(xy + [(630, 150), (10, 150)], fill=col + (46,)); sd.line(xy, fill=col + (255,), width=9, joint="curve")
        sd.ellipse([xy[-1][0] - 14, xy[-1][1] - 14, xy[-1][0] + 14, xy[-1][1] + 14], fill=col + (255,))
    xy0 = (w - 70 - 640, 228)
    full.alpha_composite(sp, xy0)
    return {"img": im, "full": full, "spark": sp, "spark_xy": xy0}


def build_news(story, now):
    w = STAGE_W
    fh = font(F_MONT, 104)
    lines = wrap(story["headline"].rstrip("."), fh, w - 200)
    if len(lines) > 5:
        fh = font(F_MONT, 86); lines = wrap(story["headline"].rstrip("."), fh, w - 200)[:6]
    lh = int(fh.size * 1.22)
    h = 300 + len(lines) * lh + 330
    base = card(w, h, top=(250, 251, 254), bot=(232, 237, 247), edge=(255, 255, 255))
    d = ImageDraw.Draw(base)
    hdr = vgrad(w, 210, (18, 34, 72), (11, 22, 48)); hdr.putalpha(ImageChops.multiply(hdr.getchannel("A"), rmask(w, 260, 56).crop((0, 0, w, 210))))
    base.alpha_composite(hdr)
    pub = dt.datetime.fromtimestamp(story.get("published") or story.get("newest") or now.timestamp(), dt.timezone.utc)
    breaking = (now - pub).total_seconds() < 3 * 3600
    tag, tcol = ("BREAKING", CORAL) if breaking else ("MARKET NEWS", ORANGE)
    tg = pill(tag, font(F_MONT, 58), tcol, WHITE, padx=44, h=116)
    d.text((w - 80, 105), f"{pub:%b} {pub.day}, {pub:%Y} \u00B7 {pub:%H:%M} UTC".upper(), font=font(F_INTER, 46, "SemiBold"), fill=(190, 202, 228), anchor="rm")
    line_imgs = [text_img(ln, fh, INK, pad=4) for ln in lines]
    src = story["sources"][:3]
    chips = [pill(s, font(F_INTER, 50, "Bold"), (225, 232, 245), INK, padx=40, h=104) for s in src]
    return {"base": base, "tag": tg, "lines": line_imgs, "lh": lh, "h": h, "w": w, "chips": chips, "breaking": breaking}


def draw_news(S, t, tl):
    im = S["base"].copy(); d = ImageDraw.Draw(im)
    k = e_back(tl / 0.45)
    tg = S["tag"]
    im.alpha_composite(tg, (int(70 - (1 - k) * 300), 105 - tg.height // 2))
    if S["breaking"]:   # pulsing live dot
        a = int(160 + 95 * math.sin(tl * 8))
        d.ellipse([90 + tg.width, 85, 130 + tg.width, 125], fill=(255, 77, 94, a))
    y = 300
    for j, li in enumerate(S["lines"]):
        kj = e_quint((tl - 0.25 - j * 0.14) / 0.5)
        if kj > 0.01:
            l2 = li.copy(); l2.putalpha(l2.getchannel("A").point(lambda v, q=kj: int(v * q)))
            im.alpha_composite(l2, (90, int(y + (1 - kj) * 50)))
        y += S["lh"]
    # highlighter sweep under the first line
    hk = e_io((tl - 0.9) / 0.6)
    if hk > 0 and S["lines"]:
        hl = Image.new("RGBA", (int(S["lines"][0].width * hk) + 1, int(S["lh"] * 0.42)), (34, 227, 155, 90))
        im.alpha_composite(hl, (90, int(300 + S["lh"] * 0.56)))
        im.alpha_composite(S["lines"][0], (90, 300))
    yb = S["h"] - 250
    d.line([(90, yb), (S["w"] - 90, yb)], fill=(205, 214, 232, 255), width=4)
    d.text((90, yb + 62), "REPORTED BY", font=font(F_MONT, 40), fill=(110, 124, 160), anchor="lm")
    x = 90
    for j, c in enumerate(S["chips"]):
        kc = e_back((tl - 0.8 - j * 0.16) / 0.4)
        if kc > 0.01:
            cc = c if kc > 0.995 else c.resize((max(1, int(c.width * kc)), max(1, int(c.height * kc))), Image.BILINEAR)
            im.alpha_composite(cc, (int(x + (c.width - cc.width) / 2), int(yb + 170 - cc.height / 2)))
        x += c.width + 28
        if x > S["w"] - 200: break
    return im


def build_person(p):
    w, h = STAGE_W, 1560
    base = card(w, h, accent=None, edge=(80, 64, 40))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w // 2 - 560, 60, w // 2 + 560, 1180], fill=ORANGE + (95,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
    por = p.get("por"); ph = None
    if por is not None:
        img = por["img"]
        if por["cutout"]:
            bb = img.getchannel("A").getbbox() or (0, 0, img.width, img.height); img = img.crop(bb)
            s = min(1300 / img.width, 1180 / img.height); ph = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        else:
            side = min(img.width, img.height)
            img = img.crop(((img.width - side) // 2, 0, (img.width + side) // 2, side)).resize((980, 980), Image.LANCZOS)
            m = Image.new("L", (980, 980), 0); ImageDraw.Draw(m).ellipse([0, 0, 979, 979], fill=255)
            ph = Image.new("RGBA", (1040, 1040), (0, 0, 0, 0)); ImageDraw.Draw(ph).ellipse([0, 0, 1039, 1039], fill=ORANGE + (255,))
            ph.paste(img, (30, 30), m)
    else:     # own stylized illustration: head-and-shoulders silhouette with initials
        ph = Image.new("RGBA", (900, 1100), (0, 0, 0, 0)); sd = ImageDraw.Draw(ph)
        sd.ellipse([250, 120, 650, 560], fill=(70, 98, 160, 255)); sd.rounded_rectangle([90, 600, 810, 1180], radius=300, fill=(70, 98, 160, 255))
        sd.polygon([(400, 600), (500, 600), (450, 820)], fill=WHITE + (255,)); sd.polygon([(432, 640), (468, 640), (480, 900), (450, 960), (420, 900)], fill=NAVY + (255,))
        ini = "".join(x[0] for x in p["name"].split()[:2]).upper()
        sd.text((450, 340), ini, font=font(F_ANTON, 200), fill=WHITE, anchor="mm")
    plate = Image.new("RGBA", (w - 120, 330), (0, 0, 0, 0)); pd = ImageDraw.Draw(plate)
    pd.rounded_rectangle([0, 0, plate.width - 1, 329], radius=40, fill=NAVY + (250,), outline=ORANGE + (255,), width=4)
    pd.rounded_rectangle([0, 0, 24, 329], radius=12, fill=ORANGE + (255,))
    fn = font(F_ANTON, 130)
    while fn.getlength(p["name"].upper()) > plate.width - 140: fn = font(F_ANTON, fn.size - 6)
    pd.text((70, 120), p["name"].upper(), font=fn, fill=WHITE, anchor="lm")
    if p.get("desc"):
        fd = font(F_INTER, 52, "SemiBold"); dl = wrap(p["desc"][0].upper() + p["desc"][1:], fd, plate.width - 140)[:1]
        if dl: pd.text((72, 240), dl[0], font=fd, fill=(190, 202, 228), anchor="lm")
    attr = text_img(p.get("attr", ""), font(F_INTER, 30, "Regular"), (130, 145, 180), pad=2) if p.get("attr") else None
    return {"base": base, "photo": ph, "plate": plate, "attr": attr, "w": w, "h": h}


def draw_person(S, t, tl, span):
    im = S["base"].copy()
    ph = S["photo"]
    if ph is not None:
        z = 1.0 + 0.07 * e_io(tl / max(1.0, span))                 # Ken Burns push-in
        k = e_out(tl / 0.6)
        p2 = ph.resize((int(ph.width * z), int(ph.height * z)), Image.BILINEAR)
        box = Image.new("RGBA", (S["w"], S["h"]), (0, 0, 0, 0))
        box.alpha_composite(p2, (int(S["w"] / 2 - p2.width / 2), int(1220 - p2.height + (1 - k) * 80)))
        box.putalpha(ImageChops.multiply(box.getchannel("A"), rmask(S["w"], S["h"], 56)))
        im.alpha_composite(box)
    kp = e_back((tl - 0.35) / 0.5, 1.2)
    im.alpha_composite(S["plate"], (int(60 - (1 - kp) * 1600), S["h"] - 400))
    if S["attr"] is not None and tl > 0.6:
        im.alpha_composite(S["attr"], (S["w"] - 70 - S["attr"].width, S["h"] - 58))
    return im


def poll_options(q):
    ql = q.lower()
    if re.search(r"\bA\b.+\bB\b.+\bC\b", q): return [("A", MINT, 0), ("B", ORANGE, 0), ("C", CORAL, 0)]   # weekly vote
    if re.search(r"\bup or down\b|\bhigher or lower\b", ql): return [("UP", MINT, 1), ("DOWN", CORAL, -1)]
    if re.search(r"\byes or no\b", ql): return [("YES", MINT, 0), ("NO", CORAL, 0)]
    m = re.search(r"^(\w+ing)\b.*\bor (\w+ing)\b", ql)
    if m and len(m.group(1)) <= 9 and len(m.group(2)) <= 9:
        return [(m.group(1).upper(), MINT, 0), (m.group(2).upper(), ORANGE, 0)]
    m = re.search(r"\b(?:an? |the )?([a-z][\w'-]*),? or (?:an? |the )?([a-z][\w'-]*)\b", ql)
    if m and len(m.group(1)) <= 9 and len(m.group(2)) <= 9 and m.group(1) not in ("it", "is", "this", "that"):
        return [(m.group(1).upper(), MINT, 0), (m.group(2).upper(), ORANGE, 0)]
    return [("COMMENT", ORANGE, 0)]


def build_poll(question):
    w = STAGE_W
    chip = pill("YOUR CALL", font(F_MONT, 58), ORANGE, INK, padx=44, h=116)
    q = re.sub(r"^your call:\s*", "", question, flags=re.I)
    fq = font(F_MONT, 98)
    lines = wrap(q.upper(), fq, w - 200)
    if len(lines) > 3: fq = font(F_MONT, 80); lines = wrap(q.upper(), fq, w - 200)[:4]
    qimgs = [text_img(ln, fq, WHITE, pad=4) for ln in lines]
    opts = poll_options(q)
    bw = (w - 160 - 60 * (len(opts) - 1)) // len(opts)
    btns = []
    for lab, col, arr in opts:
        b = Image.new("RGBA", (bw, 230), (0, 0, 0, 0)); bd = ImageDraw.Draw(b)
        bd.rounded_rectangle([0, 0, bw - 1, 229], radius=60, fill=col + (255,))
        gl = Image.new("L", (bw, 230), 0); ImageDraw.Draw(gl).rounded_rectangle([6, 6, bw - 7, 110], radius=54, fill=46)
        gloss = Image.new("RGBA", (bw, 230), (255, 255, 255, 0)); gloss.putalpha(gl.filter(ImageFilter.GaussianBlur(10)))
        b.alpha_composite(gloss); bd = ImageDraw.Draw(b)
        f = font(F_ANTON, 120); tw = f.getlength(lab); ax = 0
        if arr:
            s = 44; cx = bw / 2 - tw / 2 - 40; cy = 115
            if arr > 0: bd.polygon([(cx - s, cy + s * .8), (cx + s, cy + s * .8), (cx, cy - s)], fill=INK)
            else: bd.polygon([(cx - s, cy - s * .8), (cx + s, cy - s * .8), (cx, cy + s)], fill=INK)
            ax = 40
        bd.text((bw / 2 + ax, 115), lab, font=f, fill=INK, anchor="mm")
        btns.append(b)
    hint = text_img("REPLY WITH ONE WORD  \u2193", font(F_MONT, 50), MUTED, pad=4)
    lh = int(fq.size * 1.2)
    # card height follows the content (no empty band between a short question and the buttons)
    btn_y = 260 + len(qimgs) * lh + 90 + 115
    h = btn_y + 115 + 190
    base = card(w, h, edge=(60, 90, 150))
    return {"base": base, "chip": chip, "q": qimgs, "btns": btns, "hint": hint, "w": w, "h": h, "lh": lh,
            "btn_y": btn_y, "hint_y": btn_y + 115 + 70}


def draw_poll(S, t, tl):
    im = S["base"].copy()
    k = e_back(tl / 0.45)
    im.alpha_composite(S["chip"], (int(80 - (1 - k) * 400), 70))
    y = 260
    for j, q in enumerate(S["q"]):
        kj = e_quint((tl - 0.15 - j * 0.1) / 0.45)
        if kj > 0.01:
            q2 = q.copy(); q2.putalpha(q2.getchannel("A").point(lambda v, a=kj: int(v * a)))
            im.alpha_composite(q2, (int(S["w"] / 2 - q.width / 2), int(y + (1 - kj) * 40)))
        y += S["lh"]
    n = len(S["btns"]); bw = S["btns"][0].width
    for j, b in enumerate(S["btns"]):
        kb = e_elastic((tl - 0.45 - j * 0.15) / 0.9)
        if kb <= 0.01: continue
        pulse = 1 + 0.025 * math.sin((tl - j * 0.4) * 5) if tl > 1.4 else 1
        s = kb * pulse
        bb = b.resize((max(1, int(b.width * s)), max(1, int(b.height * s))), Image.BILINEAR)
        cx = 80 + bw / 2 + j * (bw + 60)
        im.alpha_composite(bb, (int(cx - bb.width / 2), int(S["btn_y"] - bb.height / 2)))
    if tl > 1.0:
        h2 = S["hint"].copy(); h2.putalpha(h2.getchannel("A").point(lambda v, a=e_out((tl - 1.0) / 0.4): int(v * a)))
        im.alpha_composite(h2, (int(S["w"] / 2 - h2.width / 2), S["hint_y"] + int(8 * math.sin(tl * 4))))
    return im


def build_why(text):
    """WHY IT MATTERS card (61-90 s format): orange chip + the sentence in large type, line-by-line reveal."""
    w = STAGE_W
    chip = pill("WHY IT MATTERS", font(F_MONT, 58), ORANGE, INK, padx=44, h=116)
    q = re.sub(r"^why it matters( to you)?:\s*", "", text, flags=re.I)
    q = q[:1].upper() + q[1:]
    fq = font(F_MONT, 92); lines = wrap(q, fq, w - 200)
    if len(lines) > 6: fq = font(F_MONT, 76); lines = wrap(q, fq, w - 200)[:8]
    qimgs = [text_img(ln, fq, WHITE, pad=4) for ln in lines]
    lh = int(fq.size * 1.28); h = 260 + len(qimgs) * lh + 120
    base = card(w, h, edge=(60, 90, 150), accent=ORANGE)
    return {"base": base, "chip": chip, "q": qimgs, "w": w, "h": h, "lh": lh}


def draw_why(S, t, tl, span):
    im = S["base"].copy()
    k = e_back(tl / 0.45)
    im.alpha_composite(S["chip"], (int(80 - (1 - k) * 400), 70))
    n = max(1, len(S["q"])); step = max(0.12, min(0.9, (span - 1.0) / n))
    y = 260
    for j, q in enumerate(S["q"]):
        kj = e_quint((tl - 0.2 - j * step) / 0.45)
        if kj > 0.01:
            q2 = q.copy(); q2.putalpha(q2.getchannel("A").point(lambda v, a=kj: int(v * a)))
            im.alpha_composite(q2, (100, int(y + (1 - kj) * 40)))
        y += S["lh"]
    return im


# ---------------- captions ----------------
def cap_text(w):
    t = w.upper().strip(",;:\"()\u201C\u201D").rstrip(".")
    return t or w.upper()


def build_phrases(words):
    phrases, cur = [], []
    f = font(F_MONT, 150)
    for i, w in enumerate(words):
        if cur:
            prev = cur[-1]; gap = w["s"] - prev["e"]
            wid = f.getlength(" ".join(cap_text(x["w"]) for x in cur + [w]))
            if len(cur) >= 3 or re.search(r"[.,!?:;]$", prev["w"]) or gap > 0.32 or wid > 1820:
                phrases.append(cur); cur = []
        cur.append(w)
    if cur: phrases.append(cur)
    out = []
    for j, ph in enumerate(phrases):
        t1 = phrases[j + 1][0]["s"] - 0.02 if j + 1 < len(phrases) else ph[-1]["e"] + 0.6
        out.append({"words": ph, "t0": ph[0]["s"] - 0.05, "t1": min(t1, ph[-1]["e"] + 0.7)})
    return out


def make_caption_cache(phrases):
    cache = {}
    f = font(F_MONT, 150)
    for ph in phrases:
        xs, x = [], 0
        for w in ph["words"]:
            txt = cap_text(w["w"]); col = R.word_color(w["w"])
            key = (txt, col)
            if key not in cache:
                plain = text_img(txt, f, WHITE if col == WHITE else col, stroke=12, stroke_fill=INK, shadow=10)
                if col == ORANGE:
                    act = text_img(txt, font(F_MONT, 150), ORANGE, stroke=14, stroke_fill=INK, shadow=12)
                else:
                    ti = text_img(txt, f, INK if col != WHITE else INK, stroke=0, pad=6)
                    bw, bh = ti.width + 60, int(f.size * 1.32)
                    act = Image.new("RGBA", (bw + 40, bh + 40), (0, 0, 0, 0)); ad = ImageDraw.Draw(act)
                    shd = Image.new("RGBA", act.size, (0, 0, 0, 0)); ImageDraw.Draw(shd).rounded_rectangle([20, 32, 20 + bw, 32 + bh], radius=34, fill=(0, 0, 0, 150))
                    act.alpha_composite(shd.filter(ImageFilter.GaussianBlur(10)))
                    ad = ImageDraw.Draw(act)
                    ad.rounded_rectangle([20, 20, 20 + bw, 20 + bh], radius=34, fill=(ORANGE if col == WHITE else col) + (255,))
                    act.alpha_composite(ti, (20 + (bw - ti.width) // 2, 20 + (bh - ti.height) // 2 + 4))
                cache[key] = (plain, act)
            wd = f.getlength(txt)
            xs.append((x, wd, key, w)); x += wd + f.getlength(" ") + 18
        ph["layout"] = xs; ph["width"] = x - f.getlength(" ") - 18
    return cache


def draw_captions(fr, t, cy):
    c = CTX
    for ph in c["phrases"]:
        if not (ph["t0"] <= t <= ph["t1"] + 0.12): continue
        fade = 1 - e_in((t - ph["t1"]) / 0.12) if t > ph["t1"] else 1.0
        x0 = W / 2 - ph["width"] / 2
        lay = ph["layout"]
        active = None
        for j, (x, wd, key, w) in enumerate(lay):
            nxt = lay[j + 1][3]["s"] if j + 1 < len(lay) else ph["t1"]
            if w["s"] - 0.03 <= t < nxt: active = j
        for j, (x, wd, key, w) in enumerate(lay):
            if t < w["s"] - 0.03: continue
            plain, act = c["capcache"][key]
            k = e_back((t - w["s"] + 0.03) / 0.2, 2.0)
            im = act if j == active else plain
            sc = (0.72 + 0.28 * k) * ((1.16 if key[1] == ORANGE else 1.06) if j == active else 1.0)
            if j == active and key[1] == ORANGE:
                sc *= 1 + 0.10 * max(0, 1 - (t - w["s"]) / 0.35)
            blit(fr, im, x0 + x + wd / 2, cy + (1 - k) * 40, alpha=clamp(k * 1.4) * fade, scale=sc)
        break


def burst(fr, t, s, cx, cy, col):
    """Number pop-up: expanding ring + radial sparks behind the caption."""
    k = (t - s) / 0.5
    if not (0 <= k < 1): return
    r = 160 + 420 * e_out(k); a = int(220 * (1 - k))
    sz = int(2 * r + 80)
    ov = Image.new("RGBA", (sz, sz), (0, 0, 0, 0)); d = ImageDraw.Draw(ov); c0 = sz / 2
    d.ellipse([c0 - r, c0 - r, c0 + r, c0 + r], outline=col + (a,), width=max(2, int(18 * (1 - k)) + 2))
    for j in range(12):
        ang = j * math.pi / 6 + 0.26
        r0, r1 = r * 0.78, r * (0.78 + 0.30 * (1 - k))
        d.line([(c0 + r0 * math.cos(ang), c0 + r0 * math.sin(ang) * 0.6), (c0 + r1 * math.cos(ang), c0 + r1 * math.sin(ang) * 0.6)],
               fill=col + (a,), width=10)
    fr.paste(ov, (int(cx - c0), int(cy - c0)), ov)


# ---------------- frame ----------------
def stage_draw(fr, t):
    c = CTX
    scs = c["scenes"]
    for j, sc in enumerate(scs):
        t0, t1 = sc["t0"], sc["t1"]
        # back-to-back successor: hold the outgoing card fully opaque until the incoming one is opaque
        hold = 0.2 if j + 1 < len(scs) and abs(scs[j + 1]["t0"] - t1) < 0.15 else 0.0
        if not (t0 - 0.02 <= t <= t1 + 0.34 + hold): continue
        tl = t - t0
        kin = e_back(tl / 0.5, 1.25) if tl < 0.5 else 1.0
        kout = e_in((t - t1 - hold) / 0.32) if t > t1 + hold else 0.0
        im = sc["draw"](t, tl)
        alpha = clamp(tl / 0.18) * (1 - kout)
        scale = (0.9 + 0.1 * kin) * (1 - 0.06 * kout)
        cx = STAGE_X + STAGE_W / 2 + (1 - kin) * 360 - kout * 520
        cy = sc["cy"]
        sh, pad = sc.get("_sh", (None, 0))
        blit(fr, im, cx, cy, alpha=alpha, scale=scale)


def swipe(fr, t, cuts, col):
    """Brand swipe transition: three diagonal bands sweep across the frame (below the header)."""
    d = ImageDraw.Draw(fr)
    for c0 in cuts:
        k = (t - c0 + 0.22) / 0.44
        if not (0 <= k <= 1): continue
        x = -1400 + (W + 2800) * e_io(k)
        for off, wid, cc in ((0, 300, col), (-360, 90, WHITE), (-520, 200, NAVY_D)):
            xx = x + off
            d.polygon([(xx, 480), (xx + wid, 480), (xx + wid - 900, H), (xx - 900, H)], fill=cc)


def render_frame(i):
    c = CTX; fps = c["fps"]; t = i / fps
    fr = c["bg"].copy()
    for j, ob in enumerate(c["orbs"]):     # slow drifting glows
        ox = W * (0.25 + 0.5 * (j % 2)) + 300 * math.sin(t * 0.21 + j * 1.9)
        oy = [900, 1700, 2600, 3300][j] + 160 * math.sin(t * 0.17 + j)
        fr.paste(ob, (int(ox - ob.width / 2), int(oy - ob.height / 2)), ob)
    d = ImageDraw.Draw(fr)
    # ticker scroll + header progress
    st = c["ticker"]; off = int((t * 220) % st.width)
    for x in (W - off - st.width, W - off, W - off + st.width):
        if -st.width < x < W: fr.paste(st, (x, 268), st)
    d.rectangle([0, 0, int(W * t / c["dur"]), 14], fill=c["mood_c"])
    chap = c["chapter_at"](t)
    if chap:
        colc = R.CHAPTER_COL.get(chap, WHITE)
        d.ellipse([100, 432, 140, 472], fill=colc)
        d.text((165, 452), chap, font=font(F_MONT, 52), fill=WHITE, anchor="lm")
        x0 = 165 + font(F_MONT, 52).getlength(chap) + 40
        for k, cn in enumerate(c["chapters"]):
            on = c["chapters"].index(chap) >= k if chap in c["chapters"] else False
            d.rounded_rectangle([x0 + k * 84, 444, x0 + k * 84 + 66, 460], radius=8, fill=colc if on else (52, 68, 110))
    # stage cards
    stage_draw(fr, t)
    # take chip
    tk = c["take"]
    m_take = window(t, tk[0], tk[1], 0.5, 0.45) if tk else 0.0
    if tk and m_take > 0.01:
        chip = c["take_chip"]; kc = e_back((t - tk[0] - 0.1) / 0.45)
        blit(fr, chip, W / 2, 760, alpha=m_take, scale=max(0.01, kc))
    # Gus
    g = render_gus(t, i)
    gm = e_io(m_take)
    gs = lerp(1.0, TAKE_S, gm) * (1 + 0.012 * math.sin(2 * math.pi * t / 2.3))
    gcx = lerp(BOWL_C[0], TAKE_C[0], gm); gcy = lerp(BOWL_C[1], TAKE_C[1], gm)
    kin = e_back((t + 0.25) / 0.6, 1.2)
    gcy += (1 - kin) * 260
    rx, ry = gcx + (REG_W / 2 - REG_BC[0]) * gs, gcy + (REG_H / 2 - REG_BC[1]) * gs
    blit(fr, g, rx, ry, scale=gs)
    # CTA pill
    cta = c["cta"]
    if cta:
        a = window(t, cta[0] - 0.1, cta[1] + 2.2, 0.35, 0.3)
        if a > 0.01:
            k = e_back((t - cta[0] + 0.1) / 0.45)
            blit(fr, c["save_pill"], W - 140 - c["save_pill"].width / 2, STAGE_Y - 20, alpha=a, scale=max(0.02, k))
    # captions + number pop-ups + arrows
    cy = lerp(CAP_Y, CAP_Y_TAKE, gm)
    for w in c["num_words"]:
        if 0 <= t - w["s"] < 0.5: burst(fr, t, w["s"], W / 2, cy, ORANGE)
    for w in c["dir_words"]:
        dtw = t - w["s"]
        if 0 <= dtw < 0.9:
            up = R.word_color(w["w"]) == MINT; ar = c["arrows"][up]
            a = min(1, dtw / 0.15) * (1 - e_in((dtw - 0.6) / 0.3))
            dy = (-1 if up else 1) * 90 * e_out(dtw / 0.9)
            for x in (180, W - 180):
                blit(fr, ar, x, cy + dy, alpha=a)
    draw_captions(fr, t, cy)
    swipe(fr, t, c["cuts"], c["mood_c"])
    # camera: hook punch-in, cut punches, take push-in, shake on big moves
    z, dx, dy = 1.0, 0.0, 0.0
    if t < 0.6: z = 1.07 - 0.07 * e_out(t / 0.6)
    for c0 in c["cuts"][1:-1]:
        if 0 <= t - c0 < 0.4: z = max(z, 1 + 0.022 * math.sin(math.pi * (t - c0) / 0.4))
    if tk: z = max(z, 1 + 0.045 * e_io(clamp((t - tk[0]) / max(0.5, tk[1] - tk[0]))) * m_take)
    for s0 in c["shakes"]:
        if 0 <= t - s0 < 0.45:
            amp = 22 * (1 - (t - s0) / 0.45); dx, dy = amp * math.sin(t * 90), amp * math.cos(t * 70); break
    arr = np.asarray(fr)
    if abs(z - 1) > 1e-4 or dx or dy:
        import cv2
        fx, fy = W / 2, (TAKE_C[1] if tk and m_take > 0 else H * 0.45)
        Mx = np.float32([[z, 0, (1 - z) * fx + dx], [0, z, (1 - z) * fy + dy]])
        arr = cv2.warpAffine(arr, Mx, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return arr


# ---------------- setup + parallel encode ----------------
def setup(job, cfg, dur):
    vc = cfg["video"]; fps = int(vc.get("fps", 60))
    tl = job.get("timeline") or []
    words = job["words"]
    c = {"fps": fps, "dur": dur, "mood_c": MOOD[job["mood"]], "level": job["level"]}
    c["bg"] = make_background(job); c["orbs"] = make_orbs(job["mood"]); c["ticker"] = ticker_strip(job["ticker"])
    c["bowl"] = make_bowl(job["mood"])
    c["body"], c["tail"] = make_gus(job["mood"] == "red")
    c["fish_h"] = next(iter(c["body"].values())).height
    env = R.envelope(job["voice_wav"], fps, int(dur * fps) + 2)
    c["mouth"] = [int(round(min(4, e * 4.4))) if e > 0.12 else 0 for e in env]
    rnd = random.Random(job["seed"])
    bl, tt = [], rnd.uniform(1.2, 2.4)
    while tt < dur: bl.append(tt); tt += rnd.uniform(2.2, 4.6)
    c["blinks"] = bl
    order = ["THE QUESTION", "THE NUMBERS", "THE PROOF", "WHY IT MATTERS", "GUS'S TAKE", "YOUR CALL"]
    c["chapters"] = [x for x in order if any(s["chapter"] == x for s in tl)]
    c["chapter_at"] = lambda t: R.chapter_at(tl, t) if tl else ""
    segs = {}
    for s in tl: segs.setdefault(s["chapter"], []).append(s)
    def first(ch): return segs[ch][0] if ch in segs else None
    t_num = first("THE NUMBERS")["t0"] if "THE NUMBERS" in segs else 2.0
    t_num_end = segs["THE NUMBERS"][-1]["t1"] if "THE NUMBERS" in segs else t_num + 4
    takes = segs.get("GUS'S TAKE", [])
    take = next((s for s in takes if "take" in s["text"].lower()[:12]), takes[0] if takes else None)
    proof, poll, whyseg = first("THE PROOF"), first("YOUR CALL"), first("WHY IT MATTERS")
    endcp = takes[-1] if takes and takes[-1] is not take else None
    c["take"] = (take["t0"] - 0.15, take["t1"] + 0.1) if take else None
    c["deadpan"] = endcp["t0"] if endcp else None
    sv = first("SAVE"); c["cta"] = (sv["t0"], sv["t1"]) if sv else None
    # bubbles
    bub, tt = [], 0.3
    while tt < dur:
        fx, fy, _, _ = (REG_BC[0], REG_BC[1], 0, 0)
        bub.append((tt, REG_BC[0] - 290 + rnd.uniform(-20, 20), REG_BC[1] + 120 + rnd.uniform(-30, 60), rnd.uniform(9, 22), rnd.uniform(140, 260), rnd.uniform(0, 6)))
        tt += rnd.uniform(0.25, 0.7)
    c["bubbles"] = bub
    # scenes
    scenes = []
    hero = build_hero(job)
    stage_end_data = proof["t0"] if proof else (whyseg["t0"] if whyseg else (c["take"][0] if c["take"] else dur - 0.4))
    scenes.append({"t0": -0.25, "t1": t_num - 0.05, "cy": STAGE_Y + 640, "draw": lambda t, tl, S=hero: draw_hero(S, t, tl + 0.0)})
    ch = job.get("chart")
    if ch and len(ch.get("pts", [])) > 2:
        assets = {a["key"]: a for a in job.get("assets", [])}
        ms = R._mentions(words, t_num, t_num_end, assets)
        badges = []
        for tm, a in ms:
            b = build_badge(a); b["t"] = max(tm, t_num + REVEAL_T0 + REVEAL_LEN + 0.3); badges.append(b)
        badges = [b for k_, b in enumerate(badges) if k_ == 0 or b["t"] - badges[k_ - 1]["t"] > 1.0]
        S = build_chart(job, badges)
        c["chart_rev"] = (t_num + REVEAL_T0, t_num + REVEAL_T0 + REVEAL_LEN); c["chart_norm"] = S["norm"]
        scenes.append({"t0": t_num, "t1": stage_end_data - 0.05, "cy": STAGE_Y + STAGE_H / 2,
                       "draw": lambda t, tl, S=S: draw_chart(S, t, tl)})
    if proof and job.get("news"):
        pe = proof["t1"] + 0.2
        N = build_news(job["news"], job["now"])
        sw = pe
        if job.get("person") and pe - proof["t0"] >= 4.0:
            sw = proof["t0"] + max(2.6, 0.5 * (pe - proof["t0"]))
            sur = R._norm(job["person"]["name"].split()[-1])
            for w in words:
                if proof["t0"] + 1.8 <= w["s"] <= pe - 1.8 and R._norm(w["w"]) == sur: sw = w["s"]; break
        scenes.append({"t0": proof["t0"] - 0.1, "t1": sw, "cy": STAGE_Y + N["h"] / 2 + max(0, (1450 - N["h"]) / 2),
                       "draw": lambda t, tl, S=N: draw_news(S, t, tl)})
        if sw < pe:
            P = build_person(job["person"])
            scenes.append({"t0": sw, "t1": pe, "cy": STAGE_Y + P["h"] / 2,
                           "draw": lambda t, tl, S=P, sp=pe - sw: draw_person(S, t, tl, sp)})
    if whyseg:
        WY = build_why(whyseg["text"]); we = whyseg["t1"] + 0.25
        scenes.append({"t0": whyseg["t0"] - 0.1, "t1": we, "cy": STAGE_Y + max(WY["h"] / 2, STAGE_H / 2 - 120),
                       "draw": lambda t, tl, S=WY, sp=we - whyseg["t0"]: draw_why(S, t, tl, sp)})
    if poll:
        PL = build_poll(poll["text"])
        scenes.append({"t0": poll["t0"] - 0.1, "t1": dur - 0.36, "cy": STAGE_Y + max(PL["h"] / 2, STAGE_H / 2 - 120), "draw": lambda t, tl, S=PL: draw_poll(S, t, tl)})
    c["scenes"] = scenes
    cuts = [0.0] + [s["t0"] for s in scenes[1:]] + ([c["take"][0]] if c["take"] else []) + [dur]
    c["cuts"] = sorted(set(round(x, 3) for x in cuts))
    c["take_chip"] = pill("GUS'S TAKE", font(F_MONT, 72), ORANGE, INK, padx=56, h=144)
    c["save_pill"] = pill("SAVE THIS", font(F_MONT, 58), ORANGE, INK, padx=48, h=118)
    c["phrases"] = build_phrases(words); c["capcache"] = make_caption_cache(c["phrases"])
    c["num_words"] = [w for w in words if R.word_color(w["w"]) == ORANGE]
    c["dir_words"] = [w for w in words if R.word_color(w["w"]) in (MINT, CORAL)]
    c["arrows"] = {True: M.arrow(True, 220), False: M.arrow(False, 220)}
    shake_pct = float(job.get("shake_pct", 3.0)); c["shakes"] = []
    for w in words:
        m_ = re.search(r"(\d+(?:\.\d+)?)%", w["w"])
        if m_ and float(m_.group(1)) >= shake_pct: c["shakes"].append(w["s"])
    # SFX events
    ev = [(0.0, "impact")] + [(x, "whoosh") for x in c["cuts"][1:]]
    for s in scenes[1:]: ev.append((s["t0"] + 0.15, "pop"))
    if ch and scenes and len(scenes) > 1:
        for b in (S["badges"] if ch and len(ch.get("pts", [])) > 2 else []): ev.append((b["t"], "pop"))
    for w in c["num_words"]: ev.append((w["s"], "tick"))
    if c["take"]: ev.append((c["take"][0] + 0.1, "impact_soft"))
    if poll: ev += [(poll["t0"] + 0.4, "ding"), (poll["t0"] + 0.55, "ding2")]
    if c["cta"]: ev.append((c["cta"][0], "pop"))
    c["sfx_events"] = ev
    return c


def _seg_worker(args):
    i0, i1, path, enc = args
    try:
        import cv2; cv2.setNumThreads(1)
    except Exception: pass
    p = subprocess.Popen(enc + [path], stdin=subprocess.PIPE)
    for i in range(i0, i1):
        p.stdin.write(render_frame(i).tobytes())
    p.stdin.close(); p.wait()
    if p.returncode: raise RuntimeError(f"segment encode failed {path}")
    return path


def encoder_args(vc, fps, threads):
    crf = str(vc.get("crf", 17)); preset = vc.get("preset", "veryfast")
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-maxrate", vc.get("maxrate", "60M"), "-bufsize", vc.get("bufsize", "120M"),
            "-profile:v", "high", "-level:v", "5.2", "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-bf", "2",
            "-x264-params", f"aq-mode=3:threads={threads}", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", "-an"]


def sfx_track(events, dur, sr=48000, seed=1):
    rnd = np.random.default_rng(seed)
    out = np.zeros(int(dur * sr) + sr)
    t = lambda d: np.arange(int(sr * d)) / sr
    def impact(gain=1.0):
        tt = t(0.7); f = 38 + 110 * np.exp(-tt * 9)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-tt * 5.5)
        nz = rnd.standard_normal(len(tt)) * np.exp(-tt * 30) * 0.25
        return (y + nz) * gain
    def ding(f0):
        tt = t(0.8); return (np.sin(2 * np.pi * f0 * tt) + 0.4 * np.sin(2 * np.pi * f0 * 2.01 * tt)) * np.exp(-tt * 6) * 0.5
    lib = {"pop": M.pop(sr) * 0.35, "tick": M.pop(sr, 0.05, 1800) * 0.2, "impact": impact(0.9), "impact_soft": impact(0.45),
           "ding": ding(1318.5), "ding2": ding(1975.5)}
    for tm, kind in events:
        s = M.whoosh(sr, rnd=rnd) * 0.5 if kind == "whoosh" else lib[kind]
        i = int(max(0, tm - (0.2 if kind == "whoosh" else 0)) * sr)
        if i >= len(out): continue
        out[i:i + len(s)] += s[:len(out) - i]
    return np.clip(out[:int(dur * sr)], -1, 1).astype(np.float32)


def render_video(job, out_mp4, cfg, workers=None, frames=None):
    """Render the premium Short. Returns duration (s). `frames` (list of indices) = stills only (debug)."""
    global CTX
    vc = cfg["video"]; fps = int(vc.get("fps", 60)); mcfg = cfg.get("motion", {})
    work = Path(out_mp4).resolve().parent
    tail = job.get("tail", 0.8)
    dur = sf.info(job["voice_wav"]).duration + tail
    t0 = time.time()
    CTX = setup(job, cfg, dur)
    timing = {"setup_s": round(time.time() - t0, 1)}
    if frames is not None:
        out = []
        for i in frames:
            p = work / f"still_{i:05d}.png"; Image.fromarray(render_frame(i)).save(p); out.append(str(p))
        return out
    mixed = str(work / "mix.wav")
    sfx_wav = None
    if mcfg.get("sfx", True):
        sfx_wav = str(work / "sfx.wav"); sf.write(sfx_wav, sfx_track(CTX["sfx_events"], dur, seed=job["seed"] % 1000), 48000)
    dur = R.mix_audio(job["voice_wav"], job["mood"], mixed, cfg["music"], job["seed"], tail=tail, sfx_wav=sfx_wav, sfx_db=mcfg.get("sfx_db", -9))
    n = int(round(dur * fps)); CTX["dur"] = dur
    ncpu = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
    workers = workers or int(vc.get("workers", 0)) or ncpu
    try:     # each worker = renderer (~0.5 GB) + its own 4K x264 encoder (~1 GB at preset medium) -> cap by free RAM
        avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
        workers = max(1, min(workers, int(avail / float(vc.get("gb_per_worker", 2.2)))))
        timing["mem_avail_gb"] = round(avail, 1)
    except Exception:
        pass
    nseg = max(1, workers * 2)
    bounds = [round(k * n / nseg) for k in range(nseg + 1)]
    enc = encoder_args(vc, fps, max(1, ncpu // workers + 1))
    segdir = work / "segs"; segdir.mkdir(exist_ok=True)
    tasks = [(bounds[k], bounds[k + 1], str(segdir / f"seg{k:03d}.mp4"), enc) for k in range(nseg) if bounds[k + 1] > bounds[k]]
    t1 = time.time()
    import multiprocessing as mp
    with mp.get_context("fork").Pool(workers) as pool:
        paths = pool.map(_seg_worker, tasks, chunksize=1)
    timing["frames_s"] = round(time.time() - t1, 1)
    lst = segdir / "list.txt"; lst.write_text("".join(f"file '{p}'\n" for p in paths))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", mixed,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-bsf:v", "h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1", "-c:a", "aac", "-b:a", str(vc.get("audio_bitrate", "256k")),
                    "-ar", "48000", "-movflags", "+faststart", "-shortest", out_mp4], check=True)
    for p in paths: Path(p).unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    try: segdir.rmdir()
    except OSError: pass
    timing.update({"frames": n, "fps_render": round(n / max(0.1, time.time() - t1), 2), "workers": workers, "total_s": round(time.time() - t0, 1)})
    (work / "render_timing.json").write_text(json.dumps(timing))
    print("premium render:", timing)
    return dur


if __name__ == "__main__":      # re-render a saved job:  python -m finshorts.premium out/<job>/job.pkl [--stills 0,300,...]
    import sys
    from .config import load_config
    job = pickle.load(open(sys.argv[1], "rb")); cfg = load_config()
    out = str(Path(sys.argv[1]).with_name(Path(sys.argv[1]).parent.name + ".mp4"))
    if len(sys.argv) > 3 and sys.argv[2] == "--stills":
        print(render_video(job, out, cfg, frames=[int(x) for x in sys.argv[3].split(",")]))
    else:
        print(render_video(job, out, cfg))
