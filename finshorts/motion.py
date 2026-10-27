"""Motion-graphics building blocks (own designs, brand colors, no publisher/coin/company logos).

Cards are pre-rendered once per video as RGBA; render.py only moves/scales/fades them per frame.
SFX (whoosh / pop / tick) are synthesised with numpy - no third-party samples."""
import math, datetime as dt
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from . import render as R

CARD_W = 960


def ease_out(k):  # cubic
    k = max(0.0, min(1.0, k)); return 1 - (1 - k) ** 3


def ease_back(k, s=1.7):  # overshoot
    k = max(0.0, min(1.0, k)); k -= 1; return k * k * ((s + 1) * k + s) + 1


def ease_in_out(k):
    k = max(0.0, min(1.0, k)); return 3 * k * k - 2 * k * k * k


def _shadow(im, r=18, off=10, a=150):
    sh = Image.new("RGBA", (im.width + 2 * r + off, im.height + 2 * r + off), (0, 0, 0, 0))
    m = Image.new("L", im.size, 0); m.paste(im.getchannel("A").point(lambda v: a if v > 0 else 0))
    sh.paste((0, 0, 0, 255), (r + off, r + off), m)
    sh = sh.filter(ImageFilter.GaussianBlur(r / 2))
    sh.alpha_composite(im, (r, r))
    return sh


def _spark(d, pts, box, col, width=6):
    x0, y0, x1, y1 = box
    v = [p[1] for p in pts]
    if len(v) < 2: return
    lo, hi = min(v), max(v); hi = hi if hi > lo else lo + 1
    xy = [(x0 + (x1 - x0) * i / (len(v) - 1), y1 - (y1 - y0) * (vv - lo) / (hi - lo)) for i, vv in enumerate(v)]
    d.polygon(xy + [(x1, y1), (x0, y1)], fill=col + (50,))
    d.line(xy, fill=col + (255,), width=width, joint="curve")
    d.ellipse([xy[-1][0] - 9, xy[-1][1] - 9, xy[-1][0] + 9, xy[-1][1] + 9], fill=col + (255,))


def asset_badge(a):
    """a = dict(ticker, name, price, chg, period, points, asof). Own design: monogram disc (not a logo)."""
    w, h = CARD_W, 250
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    col = R.MINT if a["chg"] > 0.05 else R.CORAL if a["chg"] < -0.05 else R.ORANGE
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=(16, 30, 62, 248), outline=col, width=5)
    d.rounded_rectangle([0, 0, 22, h - 1], radius=11, fill=col)
    # monogram disc (generic, our own - no official logo)
    d.ellipse([46, 46, 46 + 144, 46 + 144], fill=(28, 48, 92), outline=col, width=4)
    mono = a["ticker"].replace("^", "")[:3]
    fm = R.font(R.F_TITLE, 56 if len(mono) < 3 else 44)
    d.text((118, 118), mono, font=fm, fill=R.WHITE, anchor="mm")
    d.text((220, 62), a["ticker"], font=R.font(R.F_TITLE, 46), fill=R.WHITE, anchor="lm")
    d.text((220, 112), a["name"].upper(), font=R.font(R.F_INTER, 28), fill=(170, 185, 215), anchor="lm")
    d.text((220, 172), a["price"], font=R.font(R.F_TITLE, 60), fill=R.WHITE, anchor="lm")
    # change pill
    fs = R.font(R.F_TITLE, 36)
    txt = a.get("chg_txt") or f"{abs(a['chg']):.1f}%"
    pw = int(d.textlength(txt, font=fs)) + 80
    px1 = w - 40; px0 = px1 - pw
    d.rounded_rectangle([px0, 34, px1, 94], radius=30, fill=col)
    tx, ty = px0 + 30, 64
    if a["chg"] > 0.05: d.polygon([(tx - 12, ty + 11), (tx + 12, ty + 11), (tx, ty - 13)], fill=R.NAVY)
    elif a["chg"] < -0.05: d.polygon([(tx - 12, ty - 11), (tx + 12, ty - 11), (tx, ty + 13)], fill=R.NAVY)
    else: d.rectangle([tx - 12, ty - 4, tx + 12, ty + 4], fill=R.NAVY)
    d.text((px0 + 50, ty), txt, font=fs, fill=R.NAVY, anchor="lm")
    d.text((px1, 114), a["period"].upper(), font=R.font(R.F_INTER, 22), fill=(150, 168, 205), anchor="rm")
    _spark(d, a.get("points") or [], (w - 380, 130, w - 40, 210), col)
    if a.get("asof"):
        d.text((220, 228), a["asof"], font=R.font(R.F_INTER, 20, bold=False), fill=(120, 135, 170), anchor="lm")
    return im


def news_card(story, now_utc):
    """Own headline card: tag (BREAKING only if < 3 h old), headline, outlet names as text, publish date."""
    w = CARD_W
    tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    fh = R.font(R.F_TITLE, 54)
    lines = R._wrap(tmp, story["headline"].rstrip("."), fh, w - 100)
    if len(lines) > 5:
        fh = R.font(R.F_TITLE, 44); lines = R._wrap(tmp, story["headline"].rstrip("."), fh, w - 100)[:6]
    h = 160 + int(len(lines) * fh.size * 1.18) + 170
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=(247, 249, 252, 255))
    d.rounded_rectangle([0, 0, w - 1, 120], radius=40, fill=R.NAVY)
    d.rectangle([0, 80, w - 1, 120], fill=R.NAVY)
    pub = dt.datetime.fromtimestamp(story.get("published") or story.get("newest") or now_utc.timestamp(), dt.timezone.utc)
    breaking = (now_utc - pub).total_seconds() < 3 * 3600
    tag, tcol = ("BREAKING", R.CORAL) if breaking else ("NEWS", R.ORANGE)
    ft = R.font(R.F_TITLE, 40)
    tw = d.textlength(tag, font=ft)
    d.rounded_rectangle([36, 28, 36 + tw + 60, 92], radius=16, fill=tcol)
    d.text((66, 60), tag, font=ft, fill=R.WHITE, anchor="lm")
    d.text((w - 40, 60), f"{pub:%b} {pub.day}, {pub:%Y} · {pub:%H:%M} UTC".upper(), font=R.font(R.F_INTER, 28),
           fill=(190, 200, 225), anchor="rm")
    y = 160
    for ln in lines:
        d.text((50, y), ln, font=fh, fill=R.NAVY, anchor="la"); y += int(fh.size * 1.18)
    # outlets (names as plain text - no logos) + verified check
    d.line([(50, h - 130), (w - 50, h - 130)], fill=(215, 222, 235), width=3)
    d.ellipse([50, h - 100, 100, h - 50], fill=R.MINT)
    d.line([(62, h - 75), (72, h - 64), (90, h - 88)], fill=R.NAVY, width=6)
    src = " · ".join(story["sources"][:3])
    fs = R.font(R.F_INTER, 32)
    while d.textlength(src, font=fs) > w - 300 and fs.size > 22: fs = R.font(R.F_INTER, fs.size - 2)
    d.text((120, h - 92), "REPORTED BY", font=R.font(R.F_TITLE, 22), fill=(110, 124, 160), anchor="lm")
    d.text((120, h - 60), src, font=fs, fill=R.NAVY, anchor="lm")
    return im


def person_card(p, name, desc, attribution_short):
    """Portrait cutout / framed photo / silhouette + lower-third name plate."""
    w, h = CARD_W, 760
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=(16, 30, 62, 250), outline=R.ORANGE, width=5)
    # glow disc behind the head
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gd = ImageDraw.Draw(glow)
    gd.ellipse([w // 2 - 260, 40, w // 2 + 260, 560], fill=R.ORANGE + (90,))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
    box_h = 560
    if p is not None:
        ph = p["img"]
        if p["cutout"]:
            bb = ph.getchannel("A").getbbox() or (0, 0, ph.width, ph.height)
            ph = ph.crop(bb)
            s = min(620 / ph.width, box_h / ph.height)
            ph = ph.resize((int(ph.width * s), int(ph.height * s)), Image.LANCZOS)
            im.alpha_composite(ph, ((w - ph.width) // 2, 20 + box_h - ph.height))
        else:   # framed circular crop (no background removal, e.g. CC BY-SA)
            side = min(ph.width, ph.height)
            ph = ph.crop(((ph.width - side) // 2, 0, (ph.width + side) // 2, side)).resize((480, 480), Image.LANCZOS)
            m = Image.new("L", (480, 480), 0); ImageDraw.Draw(m).ellipse([0, 0, 479, 479], fill=255)
            d.ellipse([w // 2 - 250, 50, w // 2 + 250, 550], fill=R.ORANGE)
            im.paste(ph, (w // 2 - 240, 60), m)
    else:   # stylized silhouette (own drawing)
        cx = w // 2
        d.ellipse([cx - 120, 90, cx + 120, 330], fill=(60, 84, 140))
        d.rounded_rectangle([cx - 230, 350, cx + 230, 600], radius=150, fill=(60, 84, 140))
        d.text((cx, 210), "".join(x[0] for x in name.split()[:2]).upper(), font=R.font(R.F_TITLE, 96), fill=R.WHITE, anchor="mm")
    # lower third
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([30, 560, w - 30, h - 30], radius=28, fill=R.NAVY)
    d.rounded_rectangle([30, 560, 46, h - 30], radius=8, fill=R.ORANGE)
    fn = R.font(R.F_TITLE, 58)
    while d.textlength(name.upper(), font=fn) > w - 140: fn = R.font(R.F_TITLE, fn.size - 4)
    d.text((76, 610), name.upper(), font=fn, fill=R.WHITE, anchor="lm")
    if desc:
        fd = R.font(R.F_INTER, 30)
        dl = R._wrap(d, desc[0].upper() + desc[1:], fd, w - 140)[:1]
        if dl: d.text((76, 668), dl[0], font=fd, fill=(190, 200, 225), anchor="lm")
    if attribution_short:
        d.text((w - 46, h - 44), attribution_short, font=R.font(R.F_INTER, 18, bold=False), fill=(130, 145, 180), anchor="rb")
    return im


def arrow(up, size=150, col=None):
    col = col or (R.MINT if up else R.CORAL)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    s = size
    pts = [(s * .5, s * .06), (s * .94, s * .52), (s * .66, s * .52), (s * .66, s * .94), (s * .34, s * .94), (s * .34, s * .52), (s * .06, s * .52)]
    if not up: pts = [(x, s - y) for x, y in pts]
    d.polygon(pts, fill=col, outline=R.NAVY_D, width=6)
    return im


# ---------------- SFX ----------------
def _env(n, a, r):
    e = np.ones(n); na, nr = int(n * a), int(n * r)
    if na: e[:na] = np.linspace(0, 1, na) ** 2
    if nr: e[-nr:] = np.linspace(1, 0, nr) ** 2
    return e


def whoosh(sr, dur=0.38, rnd=None):
    rnd = rnd or np.random.default_rng(1)
    n = int(sr * dur); x = rnd.standard_normal(n)
    # moving band-pass via a swept one-pole low-pass + high-pass difference
    out = np.zeros(n); lp1 = lp2 = 0.0
    for i in range(n):
        k = i / n; fc = 300 + 5000 * math.sin(math.pi * k) ** 2
        a1 = 1 - math.exp(-2 * math.pi * fc / sr); a2 = 1 - math.exp(-2 * math.pi * (fc * 0.35) / sr)
        lp1 += a1 * (x[i] - lp1); lp2 += a2 * (x[i] - lp2); out[i] = lp1 - lp2
    out *= _env(n, 0.45, 0.5)
    return out / (np.abs(out).max() + 1e-9)


def pop(sr, dur=0.09, f0=900):
    n = int(sr * dur); t = np.arange(n) / sr
    f = f0 * np.exp(-t * 18)
    y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 45)
    return y / (np.abs(y).max() + 1e-9)


def sfx_track(events, dur, sr=48000, seed=1):
    """events: [(t, kind)] kind in whoosh/pop/tick -> mono float32 array."""
    rnd = np.random.default_rng(seed)
    out = np.zeros(int(dur * sr) + sr)
    cache = {"pop": pop(sr), "tick": pop(sr, 0.05, 1800)}
    for t, kind in events:
        s = whoosh(sr, rnd=rnd) * 0.55 if kind == "whoosh" else cache[kind] * (0.35 if kind == "pop" else 0.22)
        i = int(max(0, t - (0.15 if kind == "whoosh" else 0)) * sr)
        out[i:i + len(s)] += s[:len(out) - i]
    return out[:int(dur * sr)].astype(np.float32)
