"""Gus Markets Daily Update: 3840x2160 landscape long-form renderer (~5 min), built on the premium.py design system.

Layout (px, 4K):  header 0-170 | stage x120-2760 y230-1830 | right column: rundown panel (2830-3740 x 230-980) + Gus
in the corner | lower thirds 1850-1970 | ticker 1995-2095 | chapter progress bar 2116-2134.
Everything is procedural (PIL + numpy); only data comes from the job dict built by run_long.py. Scene images are cached
once their entrance animations settle, so most frames are a handful of pastes."""
import math, os, re, json, time, random, subprocess, datetime as dt
import numpy as np, soundfile as sf
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from . import premium as P
from . import render as R
from .music import make_music
from .premium import (font, clamp, lerp, e_out, e_in, e_io, e_quint, e_back, window, text_img, vgrad, rmask, card,
                      pill, wrap, nice_ticks, tick_label, _ts,
                      F_ANTON, F_MONT, F_INTER, NAVY, CARD, CARD_HI, EDGE, MUTED, MUTED_D, MINT, CORAL, ORANGE, WHITE, INK)

W, H = 3840, 2160
SX, SY, SW, SH = 120, 230, 2640, 1600            # stage
RUN_BOX = (2830, 230, 910, 750)                    # rundown panel x, y, w, h
LT_Y = 1852                                        # lower third top
TK_Y, TK_H = 1995, 100                             # ticker lane
PB_Y = 2116                                        # progress bar
GUS_MODES = {"corner": (3290, 1420, 0.62), "sting": (1920, 1390, 0.78), "take": (1020, 1150, 1.08),
             "outro": (3000, 1120, 0.98)}
C = {}                                             # render context (inherited by forked workers)


def dircol(d): return MINT if d > 0 else CORAL if d < 0 else ORANGE


def blit_a(fr, im, x, y, alpha=1.0):
    """Paste RGBA at top-left (x, y) with a global alpha (fast path for alpha=1)."""
    if alpha <= 0.004: return
    if alpha < 0.996:
        a = im.getchannel("A").point(lambda v, k=alpha: int(v * k)); fr.paste(im, (int(x), int(y)), a)
    else:
        fr.paste(im, (int(x), int(y)), im)


def comp(dst, im, x, y, alpha=1.0):
    """alpha_composite onto an RGBA canvas at any (also negative) position, with global alpha."""
    if alpha <= 0.004: return
    if alpha < 0.996:
        im = im.copy(); im.putalpha(im.getchannel("A").point(lambda v, k=alpha: int(v * k)))
    x, y = int(x), int(y)
    cx0, cy0 = max(0, -x), max(0, -y)
    cx1, cy1 = min(im.width, dst.width - x), min(im.height, dst.height - y)
    if cx1 <= cx0 or cy1 <= cy0: return
    if (cx0, cy0, cx1, cy1) != (0, 0, im.width, im.height): im = im.crop((cx0, cy0, cx1, cy1))
    dst.alpha_composite(im, (x + cx0, y + cy0))


def scaled(im, s):
    if abs(s - 1) < 0.004: return im
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BILINEAR)


def arrow_pill(txt, d, size=54, h=None):
    return pill(txt, font(F_MONT, size), dircol(d), INK, padx=int(size * 0.75), h=h or int(size * 1.95), arrow=d)


# ---------------- static layers ----------------
def mini_logo(s):
    """Tiny procedural brand mark: orange fish with a navy tie in a circle (no third-party art)."""
    k = 4; S = s * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([0, 0, S - 1, S - 1], fill=(26, 46, 90, 255), outline=(120, 170, 240, 255), width=5 * k)
    d.ellipse([S * .20, S * .32, S * .68, S * .68], fill=ORANGE + (255,))
    d.polygon([(S * .64, S * .5), (S * .86, S * .30), (S * .86, S * .70)], fill=(255, 120, 40, 255))
    d.ellipse([S * .30, S * .41, S * .40, S * .51], fill=WHITE + (255,)); d.ellipse([S * .33, S * .44, S * .38, S * .49], fill=INK + (255,))
    d.polygon([(S * .42, S * .56), (S * .50, S * .56), (S * .46, S * .72)], fill=(30, 50, 110, 255))
    return im.resize((s, s), Image.LANCZOS)


def make_bg(job):
    mood_c = P.MOOD[job["mood"]]
    a = np.asarray(vgrad(W, H, (13, 27, 60), (4, 8, 20)).convert("RGB")).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a += np.exp(-(((xx - 1300) / 1900) ** 2 + ((yy - 700) / 1000) ** 2))[..., None] * np.array(mood_c, np.float32) * 0.10
    a += np.exp(-(((xx - 3300) / 1100) ** 2 + ((yy - 1500) / 900) ** 2))[..., None] * np.array((60, 120, 220), np.float32) * 0.12
    vig = 1 - 0.34 * (((xx - W / 2) / (W * 0.72)) ** 2 + ((yy - H / 2) / (H * 0.78)) ** 2)
    a *= np.clip(vig, 0.55, 1)[..., None]
    a += np.random.default_rng(7).normal(0, 1.1, a.shape[:2])[..., None]
    bg = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
    for x in range(40, W, 160): gd.line([(x, 175), (x, TK_Y)], fill=(120, 150, 220, 10), width=2)
    for y in range(190, TK_Y, 160): gd.line([(0, y), (W, y)], fill=(120, 150, 220, 10), width=2)
    bg.alpha_composite(g)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 0, W, 170], fill=(6, 12, 30, 255)); d.line([(0, 170), (W, 170)], fill=EDGE + (255,), width=3)
    bg.alpha_composite(mini_logo(116), (110, 27))
    d.text((250, 86), "GUS MARKETS", font=font(F_ANTON, 92), fill=WHITE, anchor="lm")
    lab = pill(job["label"], font(F_MONT, 44), mood_c, INK, padx=36, h=84)
    bg.alpha_composite(lab, (int(280 + font(F_ANTON, 92).getlength("GUS MARKETS")), 44))
    d.text((W - 110, 86), job["date_str"].upper(), font=font(F_INTER, 50), fill=(190, 202, 228), anchor="rm")
    d.rectangle([0, TK_Y, W, TK_Y + TK_H], fill=(6, 12, 30, 255))
    d.line([(0, TK_Y), (W, TK_Y)], fill=EDGE + (255,), width=3); d.line([(0, TK_Y + TK_H), (W, TK_Y + TK_H)], fill=EDGE + (255,), width=3)
    d.rectangle([0, TK_Y + 3, 330, TK_Y + TK_H - 3], fill=mood_c + (255,))
    d.text((165, TK_Y + TK_H / 2), "MARKETS", font=font(F_MONT, 46), fill=INK, anchor="mm")
    d.rectangle([0, TK_Y + TK_H + 3, W, H], fill=(4, 8, 20, 255))
    d.text((W - 110, LT_Y + 58), job["source_line"], font=font(F_INTER, 34, "Regular"), fill=(110, 125, 162), anchor="rm")
    return bg.convert("RGB")


def make_orbs(mood):
    out = []
    for col, r in ((P.MOOD[mood], 620), ((70, 120, 230), 720), (ORANGE, 420)):
        im = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([r * .35, r * .35, r * 1.65, r * 1.65], fill=col + (30,))
        out.append(im.filter(ImageFilter.GaussianBlur(r * 0.28)))
    return out


def header_chapter(job, idx):
    ch = job["chapters"][idx]
    txt = f"{idx:02d}  \u00B7  {ch['name'].upper()}" if idx else "TODAY'S BRIEFING"
    return text_img(txt, font(F_MONT, 46), (200, 212, 236), pad=4)


def build_rundown(job, cur):
    x, y, w, h = RUN_BOX
    im = card(w, h, r=44); d = ImageDraw.Draw(im)
    d.text((56, 72), "TODAY'S RUNDOWN", font=font(F_MONT, 42), fill=MUTED, anchor="lm")
    rows = job["chapters"][1:]
    rh = min(118, (h - 150) / max(1, len(rows)))
    for j, ch in enumerate(rows, start=1):
        yy = 150 + (j - 1) * rh + rh / 2
        state = "now" if j == cur else "done" if j < cur else "next"
        if state == "now":
            d.rounded_rectangle([28, yy - rh / 2 + 8, w - 28, yy + rh / 2 - 8], radius=28, fill=(40, 64, 120, 255))
            d.rounded_rectangle([28, yy - rh / 2 + 8, 40, yy + rh / 2 - 8], radius=6, fill=ORANGE + (255,))
        cc = ORANGE if state == "now" else (70, 92, 140) if state == "done" else (90, 110, 160)
        d.ellipse([66, yy - 32, 130, yy + 32], fill=cc + (255,) if state != "next" else None, outline=cc + (255,), width=5)
        if state == "done":
            d.line([(83, yy + 1), (95, yy + 13), (115, yy - 12)], fill=WHITE + (255,), width=7)
        else:
            d.text((98, yy + 1), str(j), font=font(F_MONT, 36), fill=INK if state == "now" else cc, anchor="mm")
        d.text((160, yy), ch["name"], font=font(F_MONT, 50),
               fill=WHITE if state == "now" else (150, 168, 205) if state == "next" else (96, 112, 150), anchor="lm")
    return im


def ticker_strip(items):
    f = font(F_INTER, 52, "SemiBold"); fb = font(F_INTER, 52, "Bold")
    parts = []
    for name, val, chg in items:
        parts.append((name + "  ", (170, 186, 222), f)); parts.append((val + "  ", WHITE, fb))
        if chg is not None:
            dd = (chg >= 0.05) - (chg <= -0.05)
            parts.append((("\u25B2 " if dd > 0 else "\u25BC " if dd < 0 else "\u25C6 ") + f"{abs(chg):.1f}%", dircol(dd), fb))
        parts.append(("      \u2022      ", (70, 90, 140), f))
    wt = int(sum(ff.getlength(p) for p, _, ff in parts)) + 20
    im = Image.new("RGBA", (wt, TK_H - 8), (0, 0, 0, 0)); d = ImageDraw.Draw(im); x = 0
    for p, c, ff in parts:
        d.text((x, (TK_H - 8) / 2), p, font=ff, fill=c, anchor="lm"); x += ff.getlength(p)
    return im


# ---------------- charts + tiles ----------------
def build_chart(ch, w, h, title_size=104, value_size=190):
    """Generic animated line chart card. ch: name, period, value, move_txt, dir, pts[(date, v)], vfmt, source, days."""
    col = dircol(ch["dir"])
    base = card(w, h); d = ImageDraw.Draw(base)
    ft = font(F_ANTON, title_size)
    d.text((80, 104), ch["name"].upper(), font=ft, fill=WHITE, anchor="lm")
    per = pill(ch["period"].upper(), font(F_MONT, 42), CARD_HI, MUTED, padx=32, h=80)
    base.alpha_composite(per, (int(110 + ft.getlength(ch["name"].upper())), 64))
    pts = ch["pts"]; vals = np.array([p[1] for p in pts], float)
    lo, hi = vals.min(), vals.max(); span = (hi - lo) or abs(hi) * 0.01 or 1
    lo -= span * 0.10; hi += span * 0.12
    px0, py0 = 80, 420; pw, ph = w - 80 - 250, h - 420 - 190
    vf = ch.get("vfmt", "{:,.0f}")
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gdr = ImageDraw.Draw(grid)
    for v in nice_ticks(lo, hi):
        y = py0 + ph * (hi - v) / (hi - lo)
        gdr.line([(px0, y), (px0 + pw, y)], fill=(255, 255, 255, 24), width=3)
        d.text((px0 + pw + 34, y), tick_label(v, vf), font=font(F_INTER, 38, "SemiBold"), fill=MUTED_D, anchor="lm")
    tss = [_ts(p[0]) for p in pts]
    if all(tss):
        for k in range(5):
            j = int(round(k * (len(pts) - 1) / 4)); x = px0 + pw * j / (len(pts) - 1)
            lab = tss[j].strftime("%H:%M") if ch.get("days", 30) <= 1 else f"{tss[j]:%b} {tss[j].day}"
            d.text((x, py0 + ph + 50), lab.upper(), font=font(F_INTER, 36, "SemiBold"), fill=MUTED_D,
                   anchor="lm" if k == 0 else "rm" if k == 4 else "mm")
    base.alpha_composite(grid)
    d.text((80, h - 60), ch["source"], font=font(F_INTER, 34, "Regular"), fill=MUTED_D, anchor="lm")
    S2 = 2
    xs = px0 + pw * np.arange(len(vals)) / max(1, len(vals) - 1)
    ys = py0 + ph * (hi - vals) / (hi - lo)
    pl = Image.new("RGBA", (w * S2, h * S2), (0, 0, 0, 0))
    poly = [(x * S2, y * S2) for x, y in zip(xs, ys)]
    fm = Image.new("L", pl.size, 0)
    ImageDraw.Draw(fm).polygon(poly + [(xs[-1] * S2, (py0 + ph) * S2), (xs[0] * S2, (py0 + ph) * S2)], fill=255)
    fillg = vgrad(w * S2, h * S2, col, NAVY, 255)
    gk = np.clip(1 - (np.arange(h * S2)[:, None] / S2 - ys.min()) / ((py0 + ph) - ys.min() + 1), 0, 1) * 88
    fillg.putalpha(Image.fromarray((np.asarray(fm).astype(np.float32) / 255 * gk).astype(np.uint8))); pl.alpha_composite(fillg)
    gl = Image.new("RGBA", pl.size, (0, 0, 0, 0)); ImageDraw.Draw(gl).line(poly, fill=col + (190,), width=30 * S2, joint="curve")
    pl.alpha_composite(gl.filter(ImageFilter.GaussianBlur(20 * S2)))
    ImageDraw.Draw(pl).line(poly, fill=col + (255,), width=12 * S2, joint="curve")
    pl = pl.resize((w, h), Image.LANCZOS)
    head = Image.new("RGBA", (200, 200), (0, 0, 0, 0)); hd = ImageDraw.Draw(head)
    hd.ellipse([36, 36, 164, 164], fill=col + (120,)); head = head.filter(ImageFilter.GaussianBlur(20)); hd = ImageDraw.Draw(head)
    hd.ellipse([72, 72, 128, 128], fill=WHITE, outline=col, width=11)
    mp = arrow_pill(ch["move_txt"], ch["dir"], 56) if ch.get("move_txt") else None
    return {"base": base, "plot": pl, "xs": xs, "ys": ys, "vals": vals, "w": w, "h": h, "head": head, "pill": mp,
            "value": ch.get("value", ""), "vf": vf, "box": (px0, py0, pw, ph), "vsize": value_size,
            "mlabel": ch.get("move_label", "")}


CH_T0, CH_LEN = 0.30, 1.7


def draw_chart(S, tl):
    if tl > CH_T0 + CH_LEN + 0.8 and "_done" in S: return S["_done"]
    im = S["base"].copy()
    p = e_io((tl - CH_T0) / CH_LEN)
    n = len(S["vals"]); fi = p * (n - 1)
    hx = float(np.interp(fi, np.arange(n), S["xs"])); hy = float(np.interp(fi, np.arange(n), S["ys"]))
    if p > 0.001:
        im.alpha_composite(S["plot"].crop((0, 0, int(hx) + 2, S["h"])), (0, 0))
        if p < 0.999:
            d = ImageDraw.Draw(im); px0, py0, pw, ph = S["box"]
            for yy in range(int(py0), int(py0 + ph), 32): d.line([(hx, yy), (hx, yy + 15)], fill=(70, 88, 128, 255), width=3)
        comp(im, S["head"], hx - 100, hy - 100)
    v = float(np.interp(fi, np.arange(n), S["vals"]))
    txt = S["value"] if p >= 0.999 else P.fmt_like(v if p > 0 else S["vals"][0], S["value"]) if S["value"] else S["vf"].format(v)
    ImageDraw.Draw(im).text((80, 285), txt, font=font(F_ANTON, S["vsize"]), fill=WHITE, anchor="lm")
    if S["pill"] is not None:
        kp = e_back((tl - CH_T0 - CH_LEN + 0.25) / 0.45)
        if kp > 0.01:
            pp = scaled(S["pill"], kp); px = S["w"] - 80 - S["pill"].width
            comp(im, pp, px + (S["pill"].width - pp.width) / 2, 250 - pp.height / 2)
            if S["mlabel"]:
                ImageDraw.Draw(im).text((S["w"] - 80, 370), S["mlabel"].upper(), font=font(F_INTER, 36, "SemiBold"), fill=MUTED, anchor="rm")
    if tl > CH_T0 + CH_LEN + 0.8: S["_done"] = im
    return im


def heat_tint(w, h, col, inten, r):
    a = (np.linspace(1, 0.15, w)[None, :] * np.ones((h, 1)) * 120 * inten).astype(np.uint8)
    im = Image.new("RGBA", (w, h), col + (0,)); im.putalpha(ImageChops.multiply(Image.fromarray(a), rmask(w, h, r)))
    return im


def spark(vals, w, h, col, width=8):
    sp = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    if len(vals) > 1:
        lo, hi = min(vals), max(vals); hi = hi if hi > lo else lo + 1
        xy = [(12 + (w * 2 - 24) * i / (len(vals) - 1), h * 2 - 14 - (h * 2 - 28) * (x - lo) / (hi - lo)) for i, x in enumerate(vals)]
        sd = ImageDraw.Draw(sp); sd.polygon(xy + [(w * 2 - 12, h * 2), (12, h * 2)], fill=col + (40,))
        sd.line(xy, fill=col + (255,), width=width * 2, joint="curve")
        sd.ellipse([xy[-1][0] - 16, xy[-1][1] - 16, xy[-1][0] + 16, xy[-1][1] + 16], fill=col + (255,))
    return sp.resize((w, h), Image.LANCZOS)


def build_tile(a, w, h):
    col = dircol(a["dir"])
    im = card(w, h, r=44)
    im.alpha_composite(heat_tint(w, h, col, clamp(0.25 + abs(a.get("chg") or 0) / a.get("scale", 1.5)) if a["dir"] else 0.12, 44))
    d = ImageDraw.Draw(im)
    d.text((50, 72), a["name"].upper(), font=font(F_ANTON, 70), fill=WHITE, anchor="lm")
    d.text((52, 138), a["sub"].upper(), font=font(F_INTER, 32, "SemiBold"), fill=MUTED, anchor="lm")
    fv = font(F_ANTON, 128)
    while fv.getlength(a["value"]) > w - 100: fv = font(F_ANTON, fv.size - 6)
    d.text((48, 262), a["value"], font=fv, fill=WHITE, anchor="lm")
    mp = arrow_pill(a["move_txt"], a["dir"], 44)
    im.alpha_composite(mp, (w - 44 - mp.width, 34))
    d.text((w - 46, 138), a["period"].upper(), font=font(F_INTER, 32, "SemiBold"), fill=MUTED_D, anchor="rm")
    sp = spark(a.get("spark") or [], w - 96, h - 380, col, 7)
    full = im.copy(); full.alpha_composite(sp, (48, 352))
    return {"img": im, "full": full, "spark": sp}


def build_mini(a, w, h, active):
    col = dircol(a["dir"])
    im = card(w, h, r=30, top=(44, 70, 130) if active else CARD_HI, bot=(32, 54, 104) if active else CARD,
              edge=ORANGE if active else EDGE)
    im.alpha_composite(heat_tint(w, h, col, 0.35 if a["dir"] else 0.1, 30))
    d = ImageDraw.Draw(im)
    if active: d.rounded_rectangle([0, 0, 14, h - 1], radius=7, fill=ORANGE + (255,))
    d.text((40, h * 0.34), a["short"].upper(), font=font(F_ANTON, 50), fill=WHITE, anchor="lm")
    d.text((40, h * 0.74), a["value"], font=font(F_INTER, 38, "Bold"), fill=(200, 212, 236), anchor="lm")
    mt = ("\u25B2 " if a["dir"] > 0 else "\u25BC " if a["dir"] < 0 else "") + a["move_txt"]
    d.text((w - 34, h * 0.5), mt, font=font(F_MONT, 42), fill=col, anchor="rm")
    return im


GRID_STAGGER = 0.08


def active_key(focus, t):
    cur = None
    for (tm, key) in focus:
        if tm <= t: cur = key
    return cur


def draw_grid(S, t, tl):
    """3x3 heat tiles; the tile being talked about gets an orange ring."""
    if tl > 2.4 and "_static" in S: im = S["_static"].copy()
    else:
        im = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        for j, T in enumerate(S["tiles"]):
            k = e_back((tl - 0.1 - j * GRID_STAGGER) / 0.45, 1.3)
            if k <= 0.01: continue
            x, y = S["pos"][j]
            rv = e_io((tl - 0.45 - j * GRID_STAGGER) / 0.7)
            if rv >= 0.999: ti = T["full"]
            else:
                ti = T["img"].copy()
                if rv > 0.01: ti.alpha_composite(T["spark"].crop((0, 0, max(1, int(T["spark"].width * rv)), T["spark"].height)), (48, 352))
            tis = scaled(ti, 0.86 + 0.14 * k)
            comp(im, tis, x + (S["tw"] - tis.width) / 2, y + (S["th"] - tis.height) / 2, clamp(k * 1.4))
        if tl > 2.4: S["_static"] = im.copy()
    act = active_key(S["focus"], t)
    if act is not None and act in S["keys"] and tl > 1.0:
        j = S["keys"].index(act); x, y = S["pos"][j]
        comp(im, S["ring"], x - 14, y - 14)
    return im


def draw_rail(S, t, tl):
    if tl > 1.2: im = S["rail_base"].copy()
    else:
        im = Image.new("RGBA", (S["rw"], SH), (0, 0, 0, 0))
        for j, m in enumerate(S["minis"]):
            k = e_out((tl - j * 0.05) / 0.45)
            if k > 0.01: comp(im, m, (1 - k) * -200, j * (S["mh"] + S["mg"]), k)
    act = active_key(S["focus"], t)
    if act in S["keys"]:
        j = S["keys"].index(act); im.alpha_composite(S["minis_act"][j], (0, j * (S["mh"] + S["mg"])))
    return im


# ---------------- story cards ----------------
def build_headline(st, w, idx, n, now):
    fh = font(F_MONT, 112)
    lines = wrap(st["headline"].rstrip("."), fh, w - 220)
    if len(lines) > 4: fh = font(F_MONT, 92); lines = wrap(st["headline"].rstrip("."), fh, w - 220)[:5]
    lh = int(fh.size * 1.24)
    h = 330 + len(lines) * lh + 330
    base = card(w, h, top=(250, 251, 254), bot=(232, 237, 247), edge=(255, 255, 255))
    hdr = vgrad(w, 220, (18, 34, 72), (11, 22, 48))
    hdr.putalpha(ImageChops.multiply(hdr.getchannel("A"), rmask(w, 280, 56).crop((0, 0, w, 220))))
    base.alpha_composite(hdr); d = ImageDraw.Draw(base)
    pub = dt.datetime.fromtimestamp(st.get("published") or now.timestamp(), dt.timezone.utc)
    d.text((w - 80, 110), f"STORY {idx + 1} OF {n}  \u00B7  {pub:%b} {pub.day}, {pub:%H:%M} UTC".upper(),
           font=font(F_INTER, 44, "SemiBold"), fill=(190, 202, 228), anchor="rm")
    tag = pill(st.get("kicker", "Market news").upper(), font(F_MONT, 56), CORAL if idx == 0 else ORANGE, WHITE, padx=44, h=112)
    lines_i = [text_img(ln, fh, INK, pad=4) for ln in lines]
    chips = [pill(s, font(F_INTER, 48, "Bold"), (225, 232, 245), INK, padx=38, h=98) for s in st["sources"][:4]]
    return {"base": base, "tag": tag, "lines": lines_i, "lh": lh, "h": h, "w": w, "chips": chips}


def draw_headline(S, tl):
    if tl > 2.0 and "_done" in S: return S["_done"]
    im = S["base"].copy(); d = ImageDraw.Draw(im)
    k = e_back(tl / 0.45)
    comp(im, S["tag"], 80 - (1 - k) * 300, 110 - S["tag"].height // 2)
    y = 320
    for j, li in enumerate(S["lines"]):
        kj = e_quint((tl - 0.25 - j * 0.13) / 0.5)
        if kj > 0.01: comp(im, li, 100, y + (1 - kj) * 50, kj)
        y += S["lh"]
    hk = e_io((tl - 0.9) / 0.6)
    if hk > 0 and S["lines"]:
        hl = Image.new("RGBA", (int(S["lines"][0].width * hk) + 1, int(S["lh"] * 0.40)), (34, 227, 155, 90))
        im.alpha_composite(hl, (100, int(320 + S["lh"] * 0.56))); im.alpha_composite(S["lines"][0], (100, 320))
    yb = S["h"] - 250
    d.line([(100, yb), (S["w"] - 100, yb)], fill=(205, 214, 232, 255), width=4)
    d.text((100, yb + 62), "REPORTED BY", font=font(F_MONT, 40), fill=(110, 124, 160), anchor="lm")
    x = 100
    for j, c in enumerate(S["chips"]):
        kc = e_back((tl - 0.8 - j * 0.15) / 0.4)
        if kc > 0.01:
            cc = scaled(c, kc); comp(im, cc, x + (c.width - cc.width) / 2, yb + 168 - cc.height / 2)
        x += c.width + 26
        if x > S["w"] - 300: break
    if tl > 2.0: S["_done"] = im
    return im


def build_person(p, w, h):
    base = card(w, h, edge=(80, 64, 40))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w // 2 - 420, 60, w // 2 + 420, h - 360], fill=ORANGE + (90,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(80)))
    por = p.get("por"); box_w, box_h = w - 80, h - 340
    if por is not None:
        img = por["img"]
        if por["cutout"]:
            bb = img.getchannel("A").getbbox() or (0, 0, img.width, img.height); img = img.crop(bb)
            s = min(box_w / img.width, box_h / img.height); ph = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        else:
            side = min(img.width, img.height); D = min(box_w, box_h) - 60
            img = img.convert("RGB").crop(((img.width - side) // 2, 0, (img.width + side) // 2, side)).resize((D, D), Image.LANCZOS)
            m = Image.new("L", (D, D), 0); ImageDraw.Draw(m).ellipse([0, 0, D - 1, D - 1], fill=255)
            ph = Image.new("RGBA", (D + 40, D + 40), (0, 0, 0, 0)); ImageDraw.Draw(ph).ellipse([0, 0, D + 39, D + 39], fill=ORANGE + (255,))
            ph.paste(img, (20, 20), m)
    else:     # own stylized silhouette with initials
        ph = Image.new("RGBA", (640, 800), (0, 0, 0, 0)); sd = ImageDraw.Draw(ph)
        sd.ellipse([170, 60, 470, 400], fill=(70, 98, 160, 255)); sd.rounded_rectangle([40, 430, 600, 860], radius=240, fill=(70, 98, 160, 255))
        ini = "".join(x[0] for x in p["name"].split()[:2]).upper()
        sd.text((320, 230), ini, font=font(F_ANTON, 150), fill=WHITE, anchor="mm")
    im = base.copy()
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    comp(lay, ph, w / 2 - ph.width / 2, h - 310 - ph.height)
    lay.putalpha(ImageChops.multiply(lay.getchannel("A"), rmask(w, h, 56)))
    im.alpha_composite(lay)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([30, h - 300, w - 30, h - 90], radius=36, fill=NAVY + (250,), outline=ORANGE + (255,), width=4)
    fn = font(F_ANTON, 96)
    while fn.getlength(p["name"].upper()) > w - 140: fn = font(F_ANTON, fn.size - 4)
    d.text((70, h - 228), p["name"].upper(), font=fn, fill=WHITE, anchor="lm")
    if p.get("desc"):
        fd = font(F_INTER, 40, "SemiBold"); dl = wrap(p["desc"][0].upper() + p["desc"][1:], fd, w - 140)[:1]
        if dl: d.text((72, h - 150), dl[0], font=fd, fill=(190, 202, 228), anchor="lm")
    if p.get("attr"):
        fa = font(F_INTER, 26, "Regular"); at = p["attr"]
        while fa.getlength(at) > w - 80 and len(at) > 20: at = at[:-4] + "\u2026"
        d.text((w / 2, h - 45), at, font=fa, fill=(130, 145, 180), anchor="mm")
    return {"img": im}


def build_why(text, words, w):
    m0 = re.match(r"^\s*why it matters:\s*", text, flags=re.I)
    txt = text[m0.end():] if m0 else text
    sents = [s for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]
    f = font(F_MONT, 82); lh = int(82 * 1.32)
    blocks = []; y = 300
    for s in sents:
        ls = wrap(s, f, w - 330)
        blocks.append({"lines": [(text_img(l, f, WHITE, pad=4), text_img(l, f, (120, 138, 180), pad=4)) for l in ls], "y": y, "n": len(s.split())})
        y += len(ls) * lh + 40
    h = max(900, y + 120)
    base = card(w, h, accent=ORANGE)
    base.alpha_composite(pill("WHY IT MATTERS", font(F_MONT, 56), ORANGE, INK, padx=46, h=112), (150, 90))
    d = ImageDraw.Draw(base)
    d.ellipse([w - 250, 70, w - 90, 230], outline=ORANGE + (255,), width=10)
    d.text((w - 170, 152), "!", font=font(F_ANTON, 120), fill=ORANGE, anchor="mm")
    ws = [x for x in words if x["w"].strip()]
    wi = len(m0.group(0).split()) if m0 else 0
    starts = []
    for b in blocks:
        starts.append(ws[min(wi, len(ws) - 1)]["s"] if ws else 0); wi += b["n"]
    return {"base": base, "blocks": blocks, "starts": starts, "lh": lh, "h": h, "w": w}


def draw_why(S, t, tl):
    im = S["base"].copy()
    cur = max([j for j, s in enumerate(S["starts"]) if t >= s - 0.15] or [0])
    for j, b in enumerate(S["blocks"]):
        k = e_out((t - S["starts"][j] + 0.2) / 0.45) if j else e_out((tl - 0.2) / 0.45)
        if k <= 0.01: continue
        for li, (lw, lm) in enumerate(b["lines"]):
            comp(im, lw if j == cur else lm, 150, b["y"] + li * S["lh"] + (1 - k) * 40, k)
    return im


# ---------------- crypto ----------------
def build_mcap(cr, w, h):
    im = card(w, h); d = ImageDraw.Draw(im)
    d.text((90, 110), "TOTAL CRYPTO MARKET", font=font(F_ANTON, 100), fill=WHITE, anchor="lm")
    d.text((90, 320), cr["mcap"], font=font(F_ANTON, 220), fill=WHITE, anchor="lm")
    mp = arrow_pill(cr["mcap_move"], cr["mcap_dir"], 60)
    im.alpha_composite(mp, (96, 470))
    d.text((96 + mp.width + 40, 470 + mp.height / 2), "24 HOURS", font=font(F_INTER, 42, "SemiBold"), fill=MUTED, anchor="lm")
    d.text((90, 790), "BITCOIN DOMINANCE", font=font(F_MONT, 54), fill=MUTED, anchor="lm")
    d.text((90, 940), cr["dom_txt"], font=font(F_ANTON, 200), fill=ORANGE, anchor="lm")
    d.text((96, 1100), "Bitcoin's share of total crypto market value", font=font(F_INTER, 44, "Regular"), fill=MUTED, anchor="lm")
    for k, (lab, col) in enumerate((("Bitcoin", ORANGE), ("All other coins", (80, 110, 180)))):
        y = 1250 + k * 90
        d.rounded_rectangle([96, y - 24, 144, y + 24], radius=10, fill=col + (255,))
        d.text((176, y), lab, font=font(F_INTER, 46, "SemiBold"), fill=WHITE, anchor="lm")
    d.text((90, h - 60), "Source: CoinGecko global market data", font=font(F_INTER, 34, "Regular"), fill=MUTED_D, anchor="lm")
    return {"base": im, "dom": cr["dom"], "w": w, "h": h}


def draw_mcap(S, tl):
    if tl > 2.2 and "_done" in S: return S["_done"]
    im = S["base"].copy()
    R_ = 480; cx, cy = S["w"] - 720, S["h"] / 2 + 40
    k = e_io((tl - 0.3) / 1.4)
    L = Image.new("RGBA", (4 * R_, 4 * R_), (0, 0, 0, 0)); d = ImageDraw.Draw(L)
    d.ellipse([0, 0, 4 * R_ - 1, 4 * R_ - 1], fill=(56, 82, 140, 255))
    ang = 360 * S["dom"] / 100 * k
    if ang > 0.5: d.pieslice([0, 0, 4 * R_ - 1, 4 * R_ - 1], -90, -90 + ang, fill=ORANGE + (255,))
    L = L.resize((2 * R_, 2 * R_), Image.LANCZOS)
    hole = Image.new("L", (2 * R_ * 2, 2 * R_ * 2), 255); ImageDraw.Draw(hole).ellipse([R_ * 0.9, R_ * 0.9, R_ * 3.1, R_ * 3.1], fill=0)
    L.putalpha(ImageChops.multiply(L.getchannel("A"), hole.resize((2 * R_, 2 * R_), Image.LANCZOS)))
    comp(im, L, cx - R_, cy - R_)
    d2 = ImageDraw.Draw(im)
    d2.text((cx, cy - 20), f"{S['dom'] * k:.0f}%", font=font(F_ANTON, 170), fill=WHITE, anchor="mm")
    d2.text((cx, cy + 110), "BTC SHARE", font=font(F_MONT, 46), fill=MUTED, anchor="mm")
    if tl > 2.2: S["_done"] = im
    return im


def build_movers(cr, w, h):
    im = card(w, h); d = ImageDraw.Draw(im)
    d.text((90, 110), "BIGGEST MOVERS", font=font(F_ANTON, 100), fill=WHITE, anchor="lm")
    d.text((w - 90, 110), "TOP 50 COINS BY MARKET VALUE \u00B7 24H", font=font(F_INTER, 40, "SemiBold"), fill=MUTED, anchor="rm")
    rows = [("GAINERS", cr["gainers"]), ("LAGGARDS", cr["losers"])]
    mx = max([abs(c) for _, rr in rows for (_, _, c, _) in rr] + [1.0])
    bars, y = [], 290
    for lab, rr in rows:
        d.text((90, y), lab, font=font(F_MONT, 52), fill=MUTED, anchor="lm"); y += 90
        for (name, sym, chg, txt) in rr:
            d.text((90, y + 60), sym, font=font(F_ANTON, 80), fill=WHITE, anchor="lm")
            nm = name if font(F_INTER, 44).getlength(name) < 520 else name[:16] + "\u2026"
            d.text((390, y + 60), nm, font=font(F_INTER, 44, "SemiBold"), fill=(190, 202, 228), anchor="lm")
            bars.append((y, chg, txt)); y += 150
        y += 50
    d.text((90, h - 60), "Source: CoinGecko \u00B7 stablecoins and tokenized cash funds excluded", font=font(F_INTER, 34, "Regular"), fill=MUTED_D, anchor="lm")
    return {"base": im, "bars": bars, "mx": mx, "w": w}


def draw_movers(S, tl):
    if tl > 2.2 and "_done" in S: return S["_done"]
    im = S["base"].copy(); d = ImageDraw.Draw(im)
    x0, bw = 1000, S["w"] - 1000 - 440
    for j, (y, chg, txt) in enumerate(S["bars"]):
        k = e_out((tl - 0.2 - j * 0.12) / 0.7)
        if k <= 0: continue
        dd = (chg >= 0.05) - (chg <= -0.05); col = dircol(dd)
        L = max(16, bw * abs(chg) / S["mx"] * k)
        d.rounded_rectangle([x0, y + 22, x0 + L, y + 98], radius=36, fill=col + (255,))
        d.text((x0 + L + 30, y + 60), ("+" if dd > 0 else "\u2212" if dd < 0 else "") + txt, font=font(F_MONT, 60), fill=col, anchor="lm")
    if tl > 2.2: S["_done"] = im
    return im


# ---------------- what to watch ----------------
def build_stat(lab, val, sub, d_, w, h):
    im = card(w, h, r=44, accent=dircol(d_) if d_ is not None else MUTED_D); d = ImageDraw.Draw(im)
    d.text((70, 76), lab.upper(), font=font(F_MONT, 44), fill=MUTED, anchor="lm")
    d.text((66, h / 2 + 20), val, font=font(F_ANTON, 150), fill=WHITE, anchor="lm")
    d.text((70, h - 62), sub.upper(), font=font(F_INTER, 34, "SemiBold"), fill=MUTED_D, anchor="lm")
    return im


def build_rates(job):
    rc = job["rates"]
    ch = build_chart(rc["chart"], 1700, SH, 96, 170)
    stats = [build_stat(s["label"], s["value"], s["sub"], s.get("dir"), SW - 1700 - 40, int((SH - 80) / 3)) for s in rc["stats"]]
    return {"chart": ch, "stats": stats}


def draw_rates(S, tl):
    if tl > 3.0 and "_done" in S: return S["_done"]
    im = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    im.alpha_composite(draw_chart(S["chart"], tl), (0, 0))
    sh = S["stats"][0].height if S["stats"] else 0
    for j, st in enumerate(S["stats"]):
        k = e_back((tl - 0.6 - j * 0.18) / 0.5, 1.2)
        if k > 0.01: comp(im, st, 1740 + (1 - k) * 200, j * (sh + 40), clamp(k * 1.3))
    if tl > 3.0: S["_done"] = im
    return im


def build_calendar(job, w):
    ev = job["events"][:4]
    n = max(1, len(ev)); gap = 50; tw = int((w - 180 - gap * (n - 1)) / n); th = 960
    h = 260 + th + 120
    im = card(w, h); d = ImageDraw.Draw(im)
    d.text((90, 125), "WHAT TO WATCH", font=font(F_ANTON, 110), fill=WHITE, anchor="lm")
    d.text((w - 90, 125), job.get("watch_sub", "").upper(), font=font(F_INTER, 42, "SemiBold"), fill=MUTED, anchor="rm")
    tiles = []
    for e in ev:
        t = Image.new("RGBA", (tw, th), (0, 0, 0, 0)); td = ImageDraw.Draw(t)
        td.rounded_rectangle([0, 0, tw - 1, th - 1], radius=40, fill=(245, 247, 252, 255))
        col = e.get("col", ORANGE)
        td.rounded_rectangle([0, 0, tw - 1, 190], radius=40, fill=col + (255,)); td.rectangle([0, 120, tw - 1, 190], fill=col + (255,))
        td.text((tw / 2, 96), e["top"].upper(), font=font(F_MONT, 62), fill=INK, anchor="mm")
        fb = font(F_ANTON, 250)
        while fb.getlength(e["big"]) > tw - 80: fb = font(F_ANTON, fb.size - 10)
        td.text((tw / 2, 375), e["big"], font=fb, fill=INK, anchor="mm")
        td.text((tw / 2, 555), e.get("weekday", "").upper(), font=font(F_MONT, 48), fill=(90, 104, 140), anchor="mm")
        ft = font(F_MONT, 54); yy = 670
        for ln in wrap(e["title"], ft, tw - 80)[:3]:
            td.text((tw / 2, yy), ln, font=ft, fill=INK, anchor="mm"); yy += 70
        td.text((tw / 2, th - 56), e.get("src", ""), font=font(F_INTER, 32, "SemiBold"), fill=(120, 132, 165), anchor="mm")
        tiles.append(t)
    return {"base": im, "tiles": tiles, "tw": tw, "gap": gap, "h": h}


def draw_calendar(S, tl):
    if tl > 2.0 and "_done" in S: return S["_done"]
    im = S["base"].copy()
    for j, t in enumerate(S["tiles"]):
        k = e_back((tl - 0.25 - j * 0.16) / 0.5, 1.3)
        if k <= 0.01: continue
        ts = scaled(t, 0.8 + 0.2 * k)
        comp(im, ts, 90 + j * (S["tw"] + S["gap"]) + (t.width - ts.width) / 2, 250 + (t.height - ts.height) / 2, clamp(k * 1.4))
    if tl > 2.0: S["_done"] = im
    return im


# ---------------- cold open, takeaway, outro ----------------
def build_hook(job):
    sec = job["sec"]["hook"]; f = font(F_MONT, 150); lh = 200; maxw = SW - 300
    toks = [w for w in sec["words"] if w["w"].strip()]
    lay, x, y = [], 0, 0
    for w in toks:
        txt = w["w"].strip(); col = R.word_color(txt)
        col = col if col in (ORANGE, MINT, CORAL) else WHITE
        im = text_img(txt, f, col, shadow=10)
        if x and x + f.getlength(txt) > maxw: x = 0; y += lh
        lay.append({"img": im, "x": x, "y": y, "s": w["s"]}); x += f.getlength(txt + " ")
    tease = []
    for (lab, val, d_) in job["teasers"]:
        p_ = card(760, 230, r=40, accent=dircol(d_))
        pd = ImageDraw.Draw(p_)
        pd.text((70, 70), lab.upper(), font=font(F_MONT, 44), fill=MUTED, anchor="lm")
        pd.text((70, 160), val, font=font(F_ANTON, 96), fill=dircol(d_) if d_ else WHITE, anchor="lm")
        tease.append(p_)
    bgl = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    pts = job.get("hook_bg") or []
    if len(pts) > 2:
        v = np.array(pts, float); lo, hi = v.min(), v.max(); hi = hi if hi > lo else lo + 1
        xy = [(SW * i / (len(v) - 1), SH * 0.80 - SH * 0.5 * (a - lo) / (hi - lo)) for i, a in enumerate(v)]
        lay2 = Image.new("RGBA", (SW, SH), (0, 0, 0, 0)); bd = ImageDraw.Draw(lay2)
        bd.polygon(xy + [(SW, SH), (0, SH)], fill=P.MOOD[job["mood"]] + (14,))
        bd.line(xy, fill=P.MOOD[job["mood"]] + (46,), width=14, joint="curve")
        lay2.putalpha(ImageChops.multiply(lay2.getchannel("A"), rmask(SW, SH, 56))); bgl = lay2
    kick = pill("TODAY ON GUS MARKETS", font(F_MONT, 54), P.MOOD[job["mood"]], INK, padx=44, h=108)
    return {"lay": lay, "tease": tease, "bgl": bgl, "kick": kick, "y0": 330 if y <= 2 * lh else 290}


def draw_hook(S, t, tl):
    im = S["bgl"].copy()
    k = e_back(tl / 0.45); comp(im, S["kick"], 140 - (1 - k) * 400, 120, clamp(k * 1.5))
    for w in S["lay"]:
        kk = e_out((t - w["s"] + 0.06) / 0.28)
        if kk <= 0.01: continue
        ws = scaled(w["img"], 0.9 + 0.1 * e_back((t - w["s"] + 0.06) / 0.3))
        comp(im, ws, 140 + w["x"] + (w["img"].width - ws.width) / 2, S["y0"] + w["y"] + (1 - kk) * 40 + (w["img"].height - ws.height) / 2, kk)
    for j, tz in enumerate(S["tease"]):
        k = e_back((tl - 1.4 - j * 0.25) / 0.5, 1.3)
        if k > 0.01:
            tzs = scaled(tz, 0.85 + 0.15 * k)
            comp(im, tzs, 140 + j * 820 + (tz.width - tzs.width) / 2, SH - 330 + (1 - k) * 60, clamp(k * 1.4))
    return im


def build_take(job):
    sec = job["sec"]["take"]; w = 1900
    f = font(F_MONT, 84); lh = 118; maxw = w - 240
    toks = [x for x in sec["words"] if x["w"].strip()]
    lay, x, y = [], 0, 0
    for tk in toks:
        txt = tk["w"].strip()
        if x and x + f.getlength(txt) > maxw: x = 0; y += lh
        lay.append({"txt": txt, "x": x, "y": y, "s": tk["s"]}); x += f.getlength(txt + " ")
    h = 320 + y + lh + 110
    base = card(w, h, accent=ORANGE)
    base.alpha_composite(pill("GUS'S TAKEAWAY", font(F_MONT, 60), ORANGE, INK, padx=50, h=120), (130, 90))
    d = ImageDraw.Draw(base)
    d.text((w - 140, 190), "\u201D", font=font(F_ANTON, 320), fill=(60, 84, 140), anchor="mm")
    for it in lay: d.text((130 + it["x"], 290 + it["y"]), it["txt"], font=f, fill=(84, 102, 146))
    return {"base": base, "lay": lay, "f": f, "h": h, "w": w, "n_lit": -1}


def draw_take(S, t, tl):
    n = sum(1 for it in S["lay"] if t >= it["s"] - 0.04)
    if n == S.get("_n") and "_img" in S: return S["_img"]
    im = S["base"].copy(); d = ImageDraw.Draw(im)
    for it in S["lay"][:n]: d.text((130 + it["x"], 290 + it["y"]), it["txt"], font=S["f"], fill=WHITE)
    S["_n"], S["_img"] = n, im
    return im


def build_catch(text):
    return text_img(text.upper(), font(F_ANTON, 124), ORANGE, stroke=6, stroke_fill=INK, shadow=12)


def build_outro(job):
    w, h = 2300, 1500
    im = card(w, h); d = ImageDraw.Draw(im)
    d.text((w / 2, 150), "THANKS FOR WATCHING", font=font(F_MONT, 64), fill=MUTED, anchor="mm")
    d.text((w / 2, 330), job["handle"].upper(), font=font(F_ANTON, 200), fill=WHITE, anchor="mm")
    for j, l in enumerate(job.get("outro_lines", [])):
        d.text((w / 2, 520 + j * 80), l, font=font(F_INTER, 56, "SemiBold"), fill=(190, 202, 228), anchor="mm")
    fd = font(F_INTER, 40, "Regular"); yy = h - 270
    for l in wrap(job["disclaimer_long"], fd, w - 260)[:4]:
        d.text((w / 2, yy), l, font=fd, fill=MUTED_D, anchor="mm"); yy += 56
    bw, bh = 1180, 230     # self-made subscribe button (plus icon, no platform logos)
    btn = Image.new("RGBA", (bw, bh), (0, 0, 0, 0)); bd = ImageDraw.Draw(btn)
    bd.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=bh // 2, fill=ORANGE + (255,))
    bd.ellipse([40, 35, 200, 195], fill=INK + (255,)); bd.rectangle([110, 70, 130, 160], fill=ORANGE + (255,)); bd.rectangle([75, 105, 165, 125], fill=ORANGE + (255,))
    bd.text((bw / 2 + 80, bh / 2), "SUBSCRIBE", font=font(F_ANTON, 140), fill=INK, anchor="mm")
    done = Image.new("RGBA", (bw, bh), (0, 0, 0, 0)); dd = ImageDraw.Draw(done)
    dd.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=bh // 2, fill=(40, 60, 110, 255), outline=ORANGE + (255,), width=6)
    dd.line([(85, 118), (115, 148), (170, 85)], fill=ORANGE + (255,), width=16)
    dd.text((bw / 2 + 80, bh / 2), "SUBSCRIBED", font=font(F_ANTON, 130), fill=WHITE, anchor="mm")
    return {"base": im, "btn": btn, "done": done, "w": w, "h": h, "by": 760}


def draw_outro(S, t, tl):
    if tl > 3.2 and "_done" in S: return S["_done"]
    im = S["base"].copy()
    k = e_back((tl - 0.4) / 0.5, 1.4)
    b = S["done"] if tl > 2.6 else S["btn"]
    press = 1 - 0.06 * math.sin(math.pi * clamp((tl - 2.3) / 0.3))
    bs = scaled(b, max(0.01, k) * press)
    if k > 0.01: comp(im, bs, S["w"] / 2 - bs.width / 2, S["by"] + 115 - bs.height / 2)
    if tl > 3.2: S["_done"] = im
    return im


# ---------------- sting, chapter cards, lower thirds ----------------
def build_sting(job):
    mood_c = P.MOOD[job["mood"]]
    a = np.asarray(vgrad(W, H, (16, 32, 72), (4, 8, 20)).convert("RGB")).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a += np.exp(-(((xx - W / 2) / 1400) ** 2 + ((yy - 1250) / 900) ** 2))[..., None] * np.array(mood_c, np.float32) * 0.22
    ang = np.arctan2(yy - 1300, xx - W / 2)
    a += ((np.sin(ang * 18) > 0.6).astype(np.float32) * np.exp(-((np.hypot(xx - W / 2, yy - 1300)) / 2200) ** 2))[..., None] * 9
    bg = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    sub = text_img(f"{job['label']}  \u00B7  {job['date_long'].upper()}", font(F_MONT, 72), mood_c, pad=6)
    letters, f, x = [], font(F_ANTON, 300), 0
    for ch in "GUS MARKETS":
        if ch != " ": letters.append((text_img(ch, f, WHITE, shadow=16), x))
        x += f.getlength(ch)
    return {"bg": bg, "sub": sub, "letters": letters, "ww": x}


def draw_sting(fr, S, ts, D):
    kin = e_io(ts / 0.42); kout = e_in((ts - (D - 0.45)) / 0.45)
    if kin <= 0 or kout >= 1: return
    m = Image.new("L", (W, H), 0); md = ImageDraw.Draw(m)
    st = Image.new("L", (W, H), 0); sd = ImageDraw.Draw(st)
    if kout > 0:          # exits to the left: right edge sweeps from W+400 to -400
        x1 = int(W + 400 - (W + 800) * kout)
        md.polygon([(-400, 0), (x1 + 300, 0), (x1, H), (-400, H)], fill=255)
        sd.polygon([(x1 + 300, 0), (x1 + 380, 0), (x1 + 80, H), (x1, H)], fill=255)
    else:                 # enters from the right: left edge sweeps from W+400 to -400
        x0 = int(W + 400 - (W + 800) * kin)
        md.polygon([(x0 + 300, 0), (W + 400, 0), (W + 400, H), (x0, H)], fill=255)
        if kin < 1: sd.polygon([(x0 + 300, 0), (x0 + 380, 0), (x0 + 80, H), (x0, H)], fill=255)
    fr.paste(S["bg"], (0, 0), m)
    fr.paste(ORANGE, (0, 0), st)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bx = W / 2 - S["ww"] / 2; by = 300
    for j, (li, lx) in enumerate(S["letters"]):
        k = e_back((ts - 0.45 - j * 0.045) / 0.4, 1.8)
        if k > 0.01: comp(lay, li, bx + lx - 20, by + (1 - k) * 140, clamp(k * 1.6))
    ks = e_out((ts - 1.0) / 0.4)
    if ks > 0.01: comp(lay, S["sub"], W / 2 - S["sub"].width / 2, by + 400 + (1 - ks) * 30, ks)
    sw = (ts - 1.3) / 0.7
    if 0 < sw < 1:      # light sweep over the wordmark
        sx = int(bx - 300 + (S["ww"] + 600) * sw)
        g = Image.new("L", (W, H), 0); ImageDraw.Draw(g).polygon([(sx, by - 40), (sx + 120, by - 40), (sx + 20, by + 400), (sx - 100, by + 400)], fill=150)
        g = ImageChops.multiply(g.filter(ImageFilter.GaussianBlur(24)), lay.getchannel("A"))
        lay.paste((255, 236, 200, 255), (0, 0), g)
    if kout > 0: lay.putalpha(ImageChops.multiply(lay.getchannel("A"), m))
    fr.paste(lay, (0, 0), lay)


def build_chapter_card(job, idx):
    ch = job["chapters"][idx]
    im = card(SW, SH, top=(22, 40, 86), bot=(8, 16, 38), edge=EDGE)
    d = ImageDraw.Draw(im)
    num = text_img(f"{idx:02d}", font(F_ANTON, 520), (255, 138, 61, 40), stroke=10, stroke_fill=ORANGE, pad=20)
    im.alpha_composite(num, (150, int(SH / 2 - num.height / 2 - 30)))
    tx = 150 + num.width + 90
    ft = font(F_ANTON, 220)
    while ft.getlength(ch["name"].upper()) > SW - tx - 120: ft = font(F_ANTON, ft.size - 10)
    d.text((tx, SH / 2 - 70), ch["name"].upper(), font=ft, fill=WHITE, anchor="ls")
    d.rounded_rectangle([tx, SH / 2 - 20, tx + 420, SH / 2], radius=10, fill=ORANGE + (255,))
    fs = font(F_INTER, 64, "SemiBold")
    for j, l in enumerate(wrap(ch.get("sub", ""), fs, SW - tx - 120)[:2]):
        d.text((tx, SH / 2 + 110 + j * 84), l, font=fs, fill=MUTED, anchor="lm")
    return im


def draw_card(fr, img, t, t0, t1):
    kin = e_quint((t - t0) / 0.42); kout = e_in((t - (t1 - 0.3)) / 0.4)
    if kin <= 0 or kout >= 1: return
    if kout <= 0:
        wv = int(SW * kin)
        if wv > 0: cr = img.crop((SW - wv, 0, SW, SH)); fr.paste(cr, (SX, SY), cr)
    else:
        cut = int(SW * kout); cr = img.crop((cut, 0, SW, SH)); fr.paste(cr, (SX + cut, SY), cr)


def build_lt(a, b, col):
    fa = font(F_ANTON, 64); fb = font(F_INTER, 44, "SemiBold")
    w = int(60 + fa.getlength(a) + 50 + fb.getlength(b) + 70); h = 112
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=24, fill=(8, 16, 38, 240), outline=EDGE + (255,), width=3)
    d.rounded_rectangle([0, 0, 18, h - 1], radius=9, fill=col + (255,))
    d.text((52, h / 2), a, font=fa, fill=WHITE, anchor="lm")
    d.text((52 + fa.getlength(a) + 44, h / 2 + 2), b, font=fb, fill=(190, 202, 228), anchor="lm")
    return im


# ---------------- frame ----------------
def draw_scene(fr, s, t):
    tl = t - s["t0"]
    kin = e_out(tl / 0.5); kout = e_in((t - s["t1"]) / 0.4) if t > s["t1"] else 0
    if kin <= 0.001 or kout >= 0.999: return
    im = s["draw"](t, tl)
    alpha = min(kin, 1 - kout)
    x = s["x"] + (1 - kin) * s.get("dx", 220) - kout * 260
    y = s["y"]
    if s.get("shadow", True):
        sh = s.get("_shadow")
        if sh is None or sh[0] != im.size:
            m = Image.new("RGBA", (im.width + 120, im.height + 140), (0, 0, 0, 0))
            m.paste((0, 0, 0, 150), (60, 84), im.getchannel("A")); m = m.filter(ImageFilter.GaussianBlur(30))
            s["_shadow"] = sh = (im.size, m)
        if alpha > 0.5: blit_a(fr, sh[1], x - 60, y - 60, alpha * alpha)
    blit_a(fr, im, x, y, alpha)


def gus_pose(t):
    keys = C["gus_keys"]; cur = keys[0][1]; prev = cur; tk = -99
    for tm, mode in keys:
        if tm <= t: prev, cur, tk = cur, mode, tm
    a, b = GUS_MODES[prev], GUS_MODES[cur]
    k = e_io((t - tk) / 0.7)
    return lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)


def chapter_idx(t):
    ci = 0
    for j, ch in enumerate(C["chapters"]):
        if t >= ch["t0"] - 0.05: ci = j
    return ci


def render_frame(i):
    c = C; t = i / c["fps"]
    fr = c["bg"].copy()
    for j, ob in enumerate(c["orbs"]):
        ox = [900, 2300, 3300][j] + 220 * math.sin(t * 0.11 + j * 2.1); oy = [800, 1300, 1000][j] + 160 * math.cos(t * 0.09 + j)
        fr.paste(ob, (int(ox - ob.width / 2), int(max(175 + ob.height / 2, oy) - ob.height / 2)), ob)
    ci = chapter_idx(t)
    hc = c["hdr"][ci]; fr.paste(hc, (int(W / 2 - hc.width / 2 + 150), int(86 - hc.height / 2)), hc)
    st0, st1 = c["sting"]
    rv = window(t, 0.3, c["take_t0"], 0.5, 0.5)
    if rv > 0.01:
        rp = c["rundown"][ci]
        blit_a(fr, rp, RUN_BOX[0] + (1 - rv) * 300, RUN_BOX[1], rv)
    for s in c["scenes"]:
        if s["t0"] - 0.01 <= t <= s["t1"] + 0.45: draw_scene(fr, s, t)
    for cc in c["cards"]:
        if cc["t0"] - 0.01 <= t <= cc["t1"] + 0.15: draw_card(fr, c["card_img"][cc["idx"]], t, cc["t0"], cc["t1"])
    for lt in c["lts"]:
        if lt["t0"] <= t <= lt["t1"] + 0.4:
            k = e_quint((t - lt["t0"]) / 0.5); ko = e_in((t - lt["t1"]) / 0.4) if t > lt["t1"] else 0
            im = lt["img"]; wv = int(im.width * k)
            if wv > 4: blit_a(fr, im.crop((0, 0, wv, im.height)), SX, LT_Y, 1 - ko)
    tk = c["ticker"]; off = int((t * 140) % tk.width)
    lane = Image.new("RGBA", (W - 350, tk.height), (0, 0, 0, 0))
    lane.alpha_composite(tk.crop((off, 0, min(tk.width, off + lane.width), tk.height)), (0, 0))
    if tk.width - off < lane.width: lane.alpha_composite(tk.crop((0, 0, lane.width - (tk.width - off), tk.height)), (tk.width - off, 0))
    fr.paste(lane, (350, TK_Y + 4), lane)
    d = ImageDraw.Draw(fr); dur = c["dur"]
    for (a, b) in c["pb_segs"]:
        xa, xb = a / dur * W + 5, b / dur * W - 5
        d.rectangle([xa, PB_Y, xb, PB_Y + 16], fill=(40, 56, 96))
        if t > a: d.rectangle([xa, PB_Y, min(xb, t / dur * W), PB_Y + 16], fill=ORANGE)
    if st0 <= t <= st1: draw_sting(fr, c["sting_S"], t - st0, st1 - st0)
    gx, gy, gs = gus_pose(t)
    if st0 + 0.2 <= t <= st0 + 1.2: gs *= 1 + 0.06 * math.sin(math.pi * clamp((t - st0 - 0.2) / 1.0))
    gi = scaled(P.render_gus(t, i), gs)
    fr.paste(gi, (int(gx - gi.width / 2), int(gy - gi.height / 2)), gi)
    cp = c.get("catch")
    if cp and cp["t0"] <= t <= cp["t1"] + 0.4:
        k = e_back((t - cp["t0"]) / 0.4, 1.6); ko = e_in((t - cp["t1"]) / 0.4) if t > cp["t1"] else 0
        im = scaled(cp["img"], 0.7 + 0.3 * k)
        blit_a(fr, im, cp["x"] - im.width / 2, cp["y"] - im.height / 2, clamp(k * 1.5) * (1 - ko))
    return np.asarray(fr)


# ---------------- setup ----------------
def mentions(words, tiles):
    out = []
    for w_ in words:
        tok = re.sub(r"[^a-z0-9&\-]", "", w_["w"].lower())
        for a in tiles:
            if re.fullmatch(a["rx"], tok): out.append((w_["s"], a["key"])); break
    for j in range(len(words) - 2):          # "S and P" is three tokens
        if re.sub(r"\W", "", words[j]["w"].lower()) == "s" and re.sub(r"\W", "", words[j + 1]["w"].lower()) == "and":
            out.append((words[j]["s"], "spx"))
    out.sort(); ded = []
    for tm, k in out:
        if ded and ded[-1][1] == k: continue
        ded.append((tm, k))
    return ded


def crypto_marks(words, CJ):
    t0 = words[0]["s"] if words else 0
    found = {}
    names = {g[0].lower().split()[0] for g in CJ["gainers"] + CJ["losers"]}
    for j, w_ in enumerate(words):
        tok = re.sub(r"[^a-z']", "", w_["w"].lower()).rstrip("'s")
        nxt = re.sub(r"[^a-z]", "", words[j + 1]["w"].lower()) if j + 1 < len(words) else ""
        if w_["s"] <= t0 + 3: continue
        if tok in ("ether", "ethereum") and "eth" not in found: found["eth"] = w_["s"]
        if ((tok in ("total", "whole") and nxt in ("crypto", "market")) or tok == "dominance"
                or (tok == "market" and nxt in ("value", "cap", "capitalization"))) and "mcap" not in found: found["mcap"] = w_["s"]
        if (tok in ("gainer", "gainers", "laggard", "laggards", "losers", "loser", "movers") or tok in names) and "movers" not in found:
            found["movers"] = w_["s"]
    marks = sorted(((k, v - 0.25) for k, v in found.items()), key=lambda x: x[1])
    out, last = [], t0
    for k, tm in marks:
        if tm - last >= 3.0: out.append((k, tm)); last = tm
    return out


def watch_marks(sec):
    ws = sec["words"]; t0, t1 = sec["t0"], sec["t1"]
    tc = tr = None
    for w_ in ws:
        tok = re.sub(r"[^a-z]", "", w_["w"].lower())
        if tc is None and tok in ("federal", "fomc", "calendar", "release", "decision", "meeting", "session", "data"): tc = w_["s"]
        if tr is None and tok in ("yield", "treasury", "funds", "yields"): tr = w_["s"]
    tc = tc if tc is not None else t0 + 0.4 * (t1 - t0)
    tr = tr if tr is not None else t0 + 0.6 * (t1 - t0)
    if abs(tc - tr) < 3.0:
        if tc <= tr: tr = tc + 3.0
        else: tc = tr + 3.0
    return max(t0, tc - 0.25), max(t0, tr - 0.25)


def setup(job, cfg):
    fps = int(job.get("fps", 60)); dur = job["dur"]
    c = {"fps": fps, "dur": dur, "chapters": job["chapters"], "sting": tuple(job["sting"])}
    c["bg"] = make_bg(job); c["orbs"] = make_orbs(job["mood"]); c["ticker"] = ticker_strip(job["ticker"])
    c["hdr"] = [header_chapter(job, j) for j in range(len(job["chapters"]))]
    c["rundown"] = [build_rundown(job, j) for j in range(len(job["chapters"]))]
    g = {"bowl": P.make_bowl(job["mood"]), "level": job["level"], "dur": dur + 10, "deadpan": None, "chart_rev": None}
    g["body"], g["tail"] = P.make_gus(job["mood"] == "red"); g["fish_h"] = next(iter(g["body"].values())).height
    env = R.envelope(job["voice_wav"], fps, int(dur * fps) + 2)
    g["mouth"] = [int(round(min(4, e * 4.4))) if e > 0.12 else 0 for e in env]
    rnd = random.Random(job["seed"]); bl, tt = [], rnd.uniform(1.2, 2.4)
    while tt < dur: bl.append(tt); tt += rnd.uniform(2.4, 4.8)
    g["blinks"] = bl
    bub, tt = [], 0.3
    while tt < dur:
        bub.append((tt, P.REG_BC[0] - 290 + rnd.uniform(-20, 20), P.REG_BC[1] + 120 + rnd.uniform(-30, 60), rnd.uniform(9, 22),
                    rnd.uniform(140, 260), rnd.uniform(0, 6)))
        tt += rnd.uniform(0.35, 0.9)
    g["bubbles"] = bub
    P.CTX = g
    sec = job["sec"]; st0, st1 = c["sting"]
    c["take_t0"] = sec["take"]["t0"] - 1.8
    c["gus_keys"] = [(-99, "corner"), (st0 + 0.15, "sting"), (st1 - 0.55, "corner"), (sec["take"]["t0"] - 1.3, "take"),
                     (sec["outro"]["t0"] - 0.2, "outro")]
    scenes = []
    ev = [(st0 + 0.05, "whoosh"), (st0 + 0.5, "impact"), (st0 + 1.0, "ding"), (st0 + 1.15, "ding2")]

    def add(t0, t1, draw, x=SX, y=SY, **kw):
        scenes.append({"t0": t0, "t1": t1, "draw": draw, "x": x, "y": y, **kw}); ev.append((max(0, t0) + 0.1, "pop"))
    HK = build_hook(job)
    add(0.0, st0 + 0.2, lambda t, tl, S=HK: draw_hook(S, t, tl), shadow=False, dx=0)
    ev.append((0.0, "impact_soft"))
    for j in range(len(job["teasers"])): ev.append((1.5 + j * 0.25, "tick"))
    c["card_img"] = {cc["idx"]: build_chapter_card(job, cc["idx"]) for cc in job["cards"]}
    c["cards"] = job["cards"]
    for cc in job["cards"]: ev += [(cc["t0"] + 0.05, "whoosh"), (cc["t0"] + 0.45, "impact_soft")]
    # overview: heat grid -> rail + focus charts
    ov = sec["overview"]; tiles = job["tiles"]; keys = [a["key"] for a in tiles]
    focus = mentions(ov["words"], tiles)
    gap = 36; tw, th = (SW - 2 * gap) / 3, (SH - 2 * gap) / 3
    G = {"tiles": [build_tile(a, int(tw), int(th)) for a in tiles],
         "pos": [(int((j % 3) * (tw + gap)), int((j // 3) * (th + gap))) for j in range(len(tiles))],
         "tw": tw, "th": th, "keys": keys, "focus": focus}
    ring = Image.new("RGBA", (int(tw) + 28, int(th) + 28), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle([3, 3, int(tw) + 24, int(th) + 24], radius=56, outline=ORANGE + (255,), width=9)
    G["ring"] = ring
    grid_end = ov["t0"] + max(6.0, min(14.0, 0.30 * (ov["t1"] - ov["t0"])))
    later = [tm for tm, k in focus if tm >= grid_end - 0.5]
    tB = max((later[0] - 0.3) if later else grid_end, ov["t0"] + 5.0)
    add(ov["t0"] - 0.25, tB, lambda t, tl, S=G: draw_grid(S, t, tl), shadow=False)
    for j in range(len(tiles)): ev.append((ov["t0"] - 0.1 + j * GRID_STAGGER, "tick"))
    rw, mg = 640, 14; mh = int((SH - 8 * mg) / 9)
    RL = {"minis": [build_mini(a, rw, mh, False) for a in tiles], "minis_act": [build_mini(a, rw, mh, True) for a in tiles],
          "rw": rw, "mh": mh, "mg": mg, "keys": keys, "focus": focus}
    rb = Image.new("RGBA", (rw, SH), (0, 0, 0, 0))
    for j, m in enumerate(RL["minis"]): rb.alpha_composite(m, (0, j * (mh + mg)))
    RL["rail_base"] = rb
    ov_end = ov["t1"] + 0.3
    add(tB, ov_end, lambda t, tl, S=RL: draw_rail(S, t, tl), shadow=False, dx=-160)
    seq = [(tm, k) for tm, k in focus if tm >= tB - 0.2 and k in job["charts"]]
    if not seq or seq[0][0] > tB + 1.5: seq = [(tB, keys[0])] + seq
    sel = []
    for tm, k in seq:
        if sel and (tm - sel[-1][0] < 3.2 or k == sel[-1][1]): continue
        sel.append((max(tm - 0.2, tB), k))
    c["focus_sel"] = sel
    cw = SW - rw - 40
    for j, (tm, k) in enumerate(sel):
        t1 = sel[j + 1][0] if j + 1 < len(sel) else ov_end
        S = build_chart(job["charts"][k], cw, SH)
        add(tm, t1, lambda t, tl, S=S: draw_chart(S, tl), x=SX + rw + 40)
    # stories
    ns = len(job["stories"])
    for j, st in enumerate(job["stories"]):
        a, b = sec[f"story{j}"], sec[f"why{j}"]
        nxt = sec[f"story{j + 1}"]["t0"] if j + 1 < ns else b["t1"] + 0.9
        per = st.get("person")
        hw = SW - 920 if per else SW
        HL = build_headline(st, hw, j, ns, job["now"])
        add(a["t0"] - 0.15, b["t0"] - 0.2, lambda t, tl, S=HL: draw_headline(S, tl), y=SY + max(0, (SH - HL["h"]) / 2 - 40))
        if per:
            PC = build_person(per, 880, min(SH, 1500))
            add(a["t0"] + 0.6, b["t0"] - 0.2, lambda t, tl, S=PC: S["img"], x=SX + SW - 880, y=SY + (SH - PC["img"].height) / 2, dx=300)
        WY = build_why(st["why"], b["words"], SW)
        add(b["t0"] - 0.1, nxt - 0.45, lambda t, tl, S=WY: draw_why(S, t, tl), y=SY + (SH - WY["h"]) / 2)
    # crypto
    cr = sec["crypto"]; CJ = job["crypto"]
    seg = [("btc", cr["t0"] - 0.2)] + crypto_marks(cr["words"], CJ)
    for j, (kind, tm) in enumerate(seg):
        t1 = seg[j + 1][1] if j + 1 < len(seg) else cr["t1"] + 0.3
        if kind in ("btc", "eth"):
            S = build_chart(job["charts"][kind + "30"], SW, SH); add(tm, t1, lambda t, tl, S=S: draw_chart(S, tl))
        elif kind == "mcap":
            S = build_mcap(CJ, SW, SH); add(tm, t1, lambda t, tl, S=S: draw_mcap(S, tl))
        else:
            S = build_movers(CJ, SW, SH); add(tm, t1, lambda t, tl, S=S: draw_movers(S, tl))
    # what to watch
    wt = sec["watch"]
    t_cal, t_rates = watch_marks(wt)
    RT = build_rates(job); CA = build_calendar(job, SW)
    cal_y = SY + (SH - CA["h"]) / 2
    if t_cal <= t_rates:
        add(wt["t0"] - 0.2, t_rates, lambda t, tl, S=CA: draw_calendar(S, tl), y=cal_y)
        add(t_rates, wt["t1"] + 0.3, lambda t, tl, S=RT: draw_rates(S, tl), shadow=False)
    else:
        add(wt["t0"] - 0.2, t_cal, lambda t, tl, S=RT: draw_rates(S, tl), shadow=False)
        add(t_cal, wt["t1"] + 0.3, lambda t, tl, S=CA: draw_calendar(S, tl), y=cal_y)
    # takeaway + catchphrase
    tk = sec["take"]; TK = build_take(job)
    ty = int(max(SY + 20, 1040 - TK["h"] / 2 - 60))
    add(tk["t0"] - 0.6, sec["outro"]["t0"] - 0.4, lambda t, tl, S=TK: draw_take(S, t, tl), x=1840, y=ty)
    ev.append((tk["t0"] - 0.6, "impact_soft"))
    if job.get("catch_t"):
        ci = build_catch(job["catchphrase"])
        c["catch"] = {"img": ci, "t0": job["catch_t"][0] - 0.05, "t1": sec["outro"]["t0"] - 0.4, "x": 1840 + TK["w"] / 2,
                      "y": min(LT_Y - ci.height / 2 - 10, ty + TK["h"] + 40 + ci.height / 2)}
        ev.append((job["catch_t"][0], "impact"))
    OU = build_outro(job)
    add(sec["outro"]["t0"] - 0.1, dur + 1, lambda t, tl, S=OU: draw_outro(S, t, tl), x=200, y=SY + (SH - OU["h"]) / 2)
    ev += [(sec["outro"]["t0"] + 2.5, "ding")]
    c["scenes"] = scenes
    lts = []
    for cc in job["cards"]:
        ch = job["chapters"][cc["idx"]]
        lts.append({"t0": cc["t1"] + 0.3, "t1": cc["t1"] + 5.0, "img": build_lt(f"{cc['idx']:02d}  {ch['name'].upper()}", ch.get("lt", ""), ORANGE)})
    for j, st in enumerate(job["stories"]):
        if j == 0: continue
        a = sec[f"story{j}"]
        lts.append({"t0": a["t0"] + 0.2, "t1": a["t0"] + 4.5, "img": build_lt(f"STORY {j + 1}", "Reported by " + ", ".join(st["sources"][:3]), CORAL)})
    lts.append({"t0": tk["t0"] + 0.2, "t1": tk["t0"] + 5.0, "img": build_lt("GUS", "Cartoon goldfish \u00B7 not a financial adviser", ORANGE)})
    c["lts"] = lts
    chs = job["chapters"]
    c["pb_segs"] = [(chs[j]["t0"], chs[j + 1]["t0"] if j + 1 < len(chs) else dur) for j in range(len(chs))]
    c["sting_S"] = build_sting(job)
    c["sfx_events"] = ev
    return c


# ---------------- audio ----------------
def music_bed(job, out, seed):
    """Self-synthesised bed: one procedural segment per chapter (mood/bpm), overlap-added with 1.5 s crossfades."""
    sr = 48000; dur = job["dur"]; chs = job["chapters"]; moods = job.get("chapter_moods") or [job["mood"]] * len(chs)
    bed = np.zeros((int((dur + 2) * sr), 2), np.float32)
    tmp = Path(out).with_suffix(".seg.wav")
    for j, ch in enumerate(chs):
        a = max(0.0, ch["t0"] - 0.75); b = min(dur + 1, (chs[j + 1]["t0"] + 0.75) if j + 1 < len(chs) else dur + 1)
        mood, bpm = moods[j] if isinstance(moods[j], (tuple, list)) else (moods[j], None)
        make_music(b - a, mood, str(tmp), seed=seed + j * 17, **({"bpm": bpm} if bpm else {}))
        y, _ = sf.read(str(tmp), dtype="float32", always_2d=True)
        if y.shape[1] == 1: y = np.repeat(y, 2, 1)
        i = int(a * sr); n = min(len(y), len(bed) - i); bed[i:i + n] += y[:n]
    tmp.unlink(missing_ok=True)
    pk = float(np.abs(bed).max()) or 1.0
    sf.write(out, (bed[:int(dur * sr)] / pk * 0.8).astype(np.float32), sr)
    return out


def make_audio(job, cfg, work):
    lc = cfg.get("long", {})
    vdur = sf.info(job["voice_wav"]).duration
    bed = music_bed(job, str(work / "music_bed.wav"), job["seed"])
    sfx = str(work / "sfx.wav"); sf.write(sfx, P.sfx_track(C["sfx_events"], job["dur"], seed=job["seed"] % 1000), 48000)
    mixed = str(work / "mix.wav")
    dur = R.mix_audio(job["voice_wav"], job["mood"], mixed, {"source": "file", "file": bed, "volume_db": lc.get("music_db", -24)},
                      job["seed"], tail=max(0.0, job["dur"] - vdur), sfx_wav=sfx, sfx_db=lc.get("sfx_db", -12))
    return mixed, dur


# ---------------- encode ----------------
def encoder_args(vc, fps, threads):
    crf = str(vc.get("crf", 18)); preset = vc.get("preset", "veryfast")
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-maxrate", vc.get("maxrate", "50M"), "-bufsize", vc.get("bufsize", "100M"),
            "-profile:v", "high", "-level:v", "5.2", "-pix_fmt", "yuv420p", "-g", str(fps), "-bf", "2",
            "-x264-params", f"aq-mode=3:threads={threads}", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", "-an"]


def _seg_worker(args):
    i0, i1, path, enc = args
    p = subprocess.Popen(enc + [path], stdin=subprocess.PIPE)
    for i in range(i0, i1): p.stdin.write(render_frame(i).tobytes())
    p.stdin.close(); p.wait()
    if p.returncode: raise RuntimeError(f"segment encode failed {path}")
    return path


def render_video(job, out_mp4, cfg, frames=None, span=None, workers=None):
    """Render the Daily Update. frames=[i,...] -> PNG stills; span=(t0, t1) -> excerpt (seconds). Returns duration/paths."""
    global C
    lc = cfg.get("long", {}); vc = lc.get("video", {}); fps = int(job.get("fps", 60))
    work = Path(out_mp4).resolve().parent; work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    C = setup(job, cfg); timing = {"setup_s": round(time.time() - t0, 1)}
    if frames is not None:
        out = []
        for i in frames:
            p = work / f"still_{i:05d}.png"; Image.fromarray(render_frame(i)).save(p); out.append(str(p))
        return out
    mixed, dur = make_audio(job, cfg, work); C["dur"] = dur
    timing["audio_s"] = round(time.time() - t0 - timing["setup_s"], 1)
    n_all = int(round(dur * fps))
    i_a, i_b = (int(span[0] * fps), min(n_all, int(span[1] * fps))) if span else (0, n_all)
    n = i_b - i_a
    ncpu = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
    workers = workers or int(vc.get("workers", 0)) or ncpu
    try:
        avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
        workers = max(1, min(workers, int(avail / float(vc.get("gb_per_worker", 2.2))))); timing["mem_avail_gb"] = round(avail, 1)
    except Exception: pass
    nseg = max(1, min(workers * 6, n // (4 * fps) or 1))
    bounds = [i_a + round(k * n / nseg) for k in range(nseg + 1)]
    enc = encoder_args(vc, fps, max(1, ncpu // workers + 1))
    segdir = work / "segs"; segdir.mkdir(exist_ok=True)
    tasks = [(bounds[k], bounds[k + 1], str(segdir / f"seg{k:03d}.mp4"), enc) for k in range(nseg) if bounds[k + 1] > bounds[k]]
    t1 = time.time()
    import multiprocessing as mp
    with mp.get_context("fork").Pool(workers) as pool:
        paths = pool.map(_seg_worker, tasks, chunksize=1)
    timing["frames_s"] = round(time.time() - t1, 1)
    lst = segdir / "list.txt"; lst.write_text("".join(f"file '{p}'\n" for p in paths))
    aud = ["-ss", f"{i_a / fps:.3f}", "-t", f"{n / fps:.3f}", "-i", mixed] if span else ["-i", mixed]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), *aud,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-bsf:v", "h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1",
                    "-c:a", "aac", "-b:a", str(vc.get("audio_bitrate", "320k")), "-ar", "48000", "-movflags", "+faststart", "-shortest", out_mp4],
                   check=True)
    for p in paths: Path(p).unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    try: segdir.rmdir()
    except OSError: pass
    timing.update({"frames": n, "fps_render": round(n / max(0.1, timing["frames_s"]), 2), "workers": workers,
                   "segments": len(tasks), "total_s": round(time.time() - t0, 1)})
    (work / "render_timing.json").write_text(json.dumps(timing))
    print("longform render:", timing)
    return dur


# ---------------- captions + thumbnail ----------------
def make_srt(words, path, max_chars=64, max_dur=4.5):
    def ts(x):
        ms = int(round(x * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    cues, cur = [], []
    for w_ in words:
        if not w_["w"].strip(): continue
        if cur and (len(" ".join(x["w"] for x in cur + [w_])) > max_chars or w_["e"] - cur[0]["s"] > max_dur
                    or w_["s"] - cur[-1]["e"] > 0.7):
            cues.append(cur); cur = []
        cur.append(w_)
        if re.search(r"[.!?]$", w_["w"]): cues.append(cur); cur = []
    if cur: cues.append(cur)
    out = []
    for k, cu in enumerate(cues, 1):
        out.append(f"{k}\n{ts(cu[0]['s'])} --> {ts(max(cu[-1]['e'], cu[0]['s'] + 0.8))}\n{' '.join(x['w'].strip() for x in cu)}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")
    return path


def make_thumbnail(job, out_jpg):
    w, h = 2560, 1440
    mood_c = P.MOOD[job["mood"]]
    a = np.asarray(vgrad(w, h, (20, 40, 92), (5, 10, 26)).convert("RGB")).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a += np.exp(-(((xx - 1900) / 900) ** 2 + ((yy - 800) / 700) ** 2))[..., None] * np.array(mood_c, np.float32) * 0.35
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    pts = job.get("hook_bg") or []
    if len(pts) > 2:
        v = np.array(pts, float); lo, hi = v.min(), v.max(); hi = hi if hi > lo else lo + 1
        xy = [(w * i / (len(v) - 1), h * 0.92 - h * 0.45 * (x - lo) / (hi - lo)) for i, x in enumerate(v)]
        ly = Image.new("RGBA", (w, h), (0, 0, 0, 0)); ImageDraw.Draw(ly).line(xy, fill=mood_c + (70,), width=20, joint="curve")
        im.alpha_composite(ly)
    g = P.render_gus(1.0, 60); g = scaled(g, 1.12)
    im.alpha_composite(g, (int(1900 - g.width / 2), int(790 - g.height / 2)))
    d = ImageDraw.Draw(im)
    im.alpha_composite(pill("DAILY UPDATE  \u00B7  " + job["date_short"].upper(), font(F_MONT, 64), ORANGE, INK, padx=50, h=130), (110, 100))
    txt, dn = job["thumb_num"]
    if txt:
        nf = font(F_ANTON, 330)
        while nf.getlength(txt) > 1500: nf = font(F_ANTON, nf.size - 10)
        im.alpha_composite(text_img(txt, nf, dircol(dn), stroke=12, stroke_fill=INK, shadow=18), (90, 300))
    fl = font(F_ANTON, 170); lines = wrap(job["thumb"].upper(), fl, 1450)
    while len(lines) > 3: fl = font(F_ANTON, fl.size - 10); lines = wrap(job["thumb"].upper(), fl, 1450)
    y = 760 if txt else 420
    for l in lines:
        im.alpha_composite(text_img(l, fl, WHITE, stroke=10, stroke_fill=INK, shadow=16), (100, y)); y += int(fl.size * 1.12)
    out = im.convert("RGB").resize((1280, 720), Image.LANCZOS)
    out.save(out_jpg, "JPEG", quality=90, optimize=True)
    return out_jpg
