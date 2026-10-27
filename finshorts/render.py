"""Compose the 1080x1920 Short: background, header, chart, Gus in his bowl, captions, ticker; mux audio."""
import math, random, subprocess, json, re
import numpy as np, soundfile as sf
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .gus import sprite_set, Bowl
from .music import make_music

ROOT = Path(__file__).resolve().parent.parent
F_TITLE = str(ROOT / "assets/fonts/Montserrat-ExtraBold.ttf")   # titles + captions (brand)
F_INTER = str(ROOT / "assets/fonts/Inter.ttf")                   # body (brand)
W, H = 1080, 1920
NAVY, NAVY_D, NAVY_C = (11, 22, 48), (5, 10, 24), (18, 32, 66)
MINT, CORAL, ORANGE, WHITE = (34, 227, 155), (255, 77, 94), (255, 138, 61), (255, 255, 255)
MOOD_COL = {"green": MINT, "red": CORAL, "neutral": ORANGE}
UP_WORDS = set("UP HIGHER GAIN GAINS GAINER GAINED RALLY RALLIES GREEN RISE RISES RISING ROSE PUMP PUMPED WIN WINS WINNING WINNER BULLISH JUMP JUMPED SURGE".split())
DOWN_WORDS = set("DOWN LOWER LOSS LOSE LOSES LOSER LOSERS DROP DROPS DROPPED DIP RED FELL FALL FALLING CRASH PANIC DUMP DUMPED CRACKING BEARISH SLUMP SLID".split())
CHAPTER_COL = {"THE QUESTION": ORANGE, "THE NUMBERS": MINT, "THE PROOF": WHITE, "GUS'S TAKE": ORANGE, "YOUR CALL": MINT}
FISH_SCALE = 0.95


_FC = {}


def font(path, size, bold=True):
    key = (path, size, bold)
    if key in _FC: return _FC[key]
    f = ImageFont.truetype(path, size)
    if path == F_INTER:
        try:
            f.set_variation_by_name("Bold" if bold else "Regular")
        except Exception:
            pass
    _FC[key] = f
    return f


# ---------------- audio ----------------
def mix_audio(voice_wav, mood, out_wav, music_cfg, seed, tail=0.8, sfx_wav=None, sfx_db=-9):
    dur = sf.info(voice_wav).duration + tail
    if music_cfg.get("source") == "file" and music_cfg.get("file"):
        music = music_cfg["file"]
    else:
        music = str(Path(out_wav).with_name("music.wav"))
        make_music(dur + 0.5, mood, music, seed=seed)
    vol = music_cfg.get("volume_db", -23)
    fc = (f"[0:a]aresample=48000,apad=pad_dur={tail},asplit=2[v][sc];"
          f"[1:a]aresample=48000,volume={vol}dB[m];"
          f"[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[md];")
    ins = ["-i", voice_wav, "-i", music]
    if sfx_wav:
        ins += ["-i", sfx_wav]
        fc += (f"[2:a]aresample=48000,volume={sfx_db}dB[fx];"
               f"[v][md][fx]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[out]")
    else:
        fc += f"[v][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", fc,
                    "-map", "[out]", "-ar", "48000", "-ac", "2", "-t", f"{dur:.2f}", out_wav], check=True)
    return dur


def envelope(voice_wav, fps, n_frames):
    a, sr = sf.read(voice_wav)
    if a.ndim > 1: a = a.mean(1)
    hop = sr / fps
    env = np.array([np.sqrt(np.mean(a[int(i * hop):int((i + 1) * hop)] ** 2)) if int(i * hop) < len(a) else 0.0
                    for i in range(n_frames)])
    ref = np.percentile(env[env > 0], 90) if (env > 0).any() else 1
    env = np.clip(env / (ref + 1e-9), 0, 1)
    sm = np.copy(env)
    for i in range(1, len(sm)):
        sm[i] = max(env[i], sm[i - 1] * 0.55)
    return sm


# ---------------- captions: ONE word, ALL CAPS, colored keywords ----------------
def word_color(w):
    u = re.sub(r"[^A-Z0-9$%.,']", "", w.upper()).strip(".,")
    if re.search(r"[0-9$%]", u): return ORANGE
    if u in UP_WORDS: return MINT
    if u in DOWN_WORDS: return CORAL
    return WHITE


def current_word(words, t):
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["e"] + 0.5
        end = min(nxt, max(w["e"], w["s"] + 0.18) + 0.35)
        if w["s"] - 0.04 <= t < end:
            return w
    return None


def draw_word(img, w, t, y=930, scale=1.0):
    """Kinetic caption: numbers pop bigger, up/down words get a colored underline swipe. Returns text width."""
    d = ImageDraw.Draw(img)
    txt = w["w"].upper().strip(",;:\"()").rstrip(".") or w["w"].upper()
    col = word_color(w["w"])
    pop = min(1.0, max(0.0, (t - w["s"] + 0.04) / 0.09))
    boost = 1.22 if col == ORANGE else 1.1 if col in (MINT, CORAL) else 1.0
    size = int(150 * scale * (1.12 - 0.12 * pop) * (boost - (boost - 1) * min(1, (t - w["s"]) / 0.25) * 0.5))
    f = font(F_TITLE, size)
    while d.textlength(txt, font=f) > 980 and size > 60:
        size -= 8; f = font(F_TITLE, size)
    tw = d.textlength(txt, font=f)
    if col in (MINT, CORAL):
        k = min(1.0, max(0.0, (t - w["s"]) / 0.18))
        x0 = W // 2 - tw / 2
        d.rounded_rectangle([x0, y + size * 0.42, x0 + tw * k, y + size * 0.42 + 16], radius=8, fill=col)
    d.text((W // 2, y), txt, font=f, fill=col, stroke_width=10, stroke_fill=NAVY_D, anchor="mm")
    return tw


# ---------------- static background ----------------
def background(mood, label, handle, date_str, chart_png, source_line, big=None):
    top, bot = np.array(NAVY), np.array(NAVY_D)
    grad = (top[None, :] * (1 - np.linspace(0, 1, H))[:, None] + bot[None, :] * np.linspace(0, 1, H)[:, None]).astype(np.uint8)
    bg = Image.fromarray(np.repeat(grad[:, None, :], W, axis=1))
    lines = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ld = ImageDraw.Draw(lines)
    for k, (col, amp, yb) in enumerate([((60, 90, 170, 80), 30, 1080), (MOOD_COL[mood] + (70,), 24, 1180),
                                         ((60, 90, 170, 55), 18, 1460)]):
        pts = [(x, yb + amp * math.sin(x / 70 + k * 2)) for x in range(0, W + 10, 10)]
        ld.line(pts, fill=col, width=4)
    bg = Image.alpha_composite(bg.convert("RGBA"), lines.filter(ImageFilter.GaussianBlur(1)))
    d = ImageDraw.Draw(bg)
    # header pill (format label) + date
    c = MOOD_COL[mood]
    f = font(F_TITLE, 50)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle([60, 72, 60 + tw + 56, 158], radius=24, fill=c)
    d.text((88, 115), label, font=f, fill=NAVY, anchor="lm")
    d.text((W - 60, 115), date_str.upper(), font=font(F_INTER, 32), fill=(170, 185, 215), anchor="rm")
    d.text((W - 60, 205), handle, font=font(F_INTER, 30), fill=(150, 168, 205), anchor="rm")
    # chart card + big number with green/red signal
    d.rounded_rectangle([30, 250, W - 30, 830], radius=36, fill=NAVY_C, outline=(40, 58, 104), width=3)
    ch = Image.open(chart_png).convert("RGBA")
    bg.alpha_composite(ch, (40, 262))
    if big:
        d.text((72, 300), big["label"], font=font(F_INTER, 30), fill=(170, 185, 215), anchor="lm")
        fv = font(F_TITLE, 96)
        d.text((68, 380), big["value"], font=fv, fill=WHITE, anchor="lm")
        x = 68 + d.textlength(big["value"], font=fv) + 26
        sc = MINT if big["dir"] > 0 else CORAL if big["dir"] < 0 else ORANGE
        fs = font(F_TITLE, 34); sub = big["sub"]
        pw = d.textlength(sub, font=fs) + 78
        if x + pw > W - 60:  # not enough room next to the value -> under it
            x, yy = 68, 452
        else:
            yy = 384
        d.rounded_rectangle([x, yy - 30, x + pw, yy + 30], radius=30, fill=sc)
        tx = x + 28
        if big["dir"] > 0: d.polygon([(tx - 10, yy + 10), (tx + 10, yy + 10), (tx, yy - 12)], fill=NAVY)
        elif big["dir"] < 0: d.polygon([(tx - 10, yy - 10), (tx + 10, yy - 10), (tx, yy + 12)], fill=NAVY)
        else: d.rectangle([tx - 10, yy - 4, tx + 10, yy + 4], fill=NAVY)
        d.text((x + 50, yy), sub, font=fs, fill=NAVY, anchor="lm")
    # fixed disclaimer / attribution
    d.text((W // 2, 1872), source_line, font=font(F_INTER, 24, bold=False), fill=(120, 135, 170), anchor="mm")
    return bg


def chapter_at(timeline, t):
    cur = timeline[0]["chapter"] if timeline else ""
    for seg in timeline:
        if seg["chapter"] != "SAVE" and t >= seg["t0"] - 0.05:
            cur = seg["chapter"]
    return cur


def draw_chapter(img, timeline, t):
    """Chapter line at the top: colored dot + chapter name + 5 progress ticks."""
    d = ImageDraw.Draw(img)
    ch = chapter_at(timeline, t)
    order = ["THE QUESTION", "THE NUMBERS", "THE PROOF", "GUS'S TAKE", "YOUR CALL"]
    present = [c for c in order if any(s["chapter"] == c for s in timeline)]
    col = CHAPTER_COL.get(ch, WHITE)
    d.ellipse([62, 193, 86, 217], fill=col)
    d.text((100, 205), ch, font=font(F_TITLE, 32), fill=WHITE, anchor="lm")
    x0 = 100 + d.textlength(ch, font=font(F_TITLE, 32)) + 24
    for k, c in enumerate(present):
        on = present.index(ch) >= k if ch in present else False
        d.rounded_rectangle([x0 + k * 46, 200, x0 + k * 46 + 36, 210], radius=5, fill=col if on else (52, 68, 110))


def save_pill(t, t0, t1):
    """'SAVE THIS' pill with a drawn bookmark; pops in at the CTA, stays ~2.5 s."""
    if not (t0 - 0.15 <= t <= t1 + 2.5): return None
    k = min(1.0, (t - t0 + 0.15) / 0.2)
    sc = 0.6 + 0.4 * (1 - (1 - k) ** 3) + 0.06 * math.sin(min(1, k) * math.pi)
    f = font(F_TITLE, int(44 * sc))
    tmp = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    tw = tmp.textlength("SAVE THIS", font=f)
    wpx, hpx = int(tw + 110 * sc), int(84 * sc)
    im = Image.new("RGBA", (wpx, hpx), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, wpx - 1, hpx - 1], radius=hpx // 2, fill=ORANGE)
    bx, by, bw, bh = int(30 * sc), int(18 * sc), int(30 * sc), int(48 * sc)
    d.polygon([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx + bw // 2, by + bh - int(12 * sc)), (bx, by + bh)], fill=NAVY)
    d.text((bx + bw + int(18 * sc), hpx // 2), "SAVE THIS", font=f, fill=NAVY, anchor="lm")
    return im


def _wrap(d, text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=f) <= maxw: cur = trial
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines


def source_card(proof):
    """Our own proof card (no third-party screenshots): headline quote + independent outlets."""
    cw, chh = W - 60, 580
    im = Image.new("RGBA", (cw, chh), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, cw - 1, chh - 1], radius=36, fill=(247, 249, 252), outline=MINT, width=6)
    d.rounded_rectangle([40, 40, 40 + 330, 100], radius=30, fill=MINT)
    d.line([(64, 70), (78, 84), (102, 56)], fill=NAVY, width=7)
    d.text((118, 70), "VERIFIED STORY", font=font(F_TITLE, 30), fill=NAVY, anchor="lm")
    fh = font(F_INTER, 50)
    lines = _wrap(d, "\u201C" + proof["headline"].rstrip(".") + "\u201D", fh, cw - 100)[:5]
    y = 150
    for ln in lines:
        d.text((50, y), ln, font=fh, fill=NAVY, anchor="la"); y += 64
    fs = font(F_INTER, 32)
    src = " \u00B7 ".join(proof["sources"])
    d.text((50, chh - 70), f"Reported by: {src}", font=fs, fill=(70, 84, 120), anchor="lm")
    return im


def ticker_strip(items):
    f = font(F_INTER, 38)
    tmp = Image.new("RGBA", (10, 10)); d = ImageDraw.Draw(tmp)
    parts = []
    for name, val, chg in items:
        col = MINT if chg is not None and chg > 0 else CORAL if chg is not None and chg < 0 else (220, 220, 230)
        arrow = "" if chg is None else ("▲ " if chg > 0 else "▼ ")
        parts.append((f"{name} ", (200, 210, 235))); parts.append((f"{val} ", (255, 255, 255)))
        if chg is not None: parts.append((f"{arrow}{abs(chg):.1f}%", col))
        parts.append(("     ", (0, 0, 0)))
    wtot = int(sum(d.textlength(p, font=f) for p, _ in parts)) + 10
    strip = Image.new("RGBA", (wtot, 70), (0, 0, 0, 0)); sd = ImageDraw.Draw(strip)
    x = 0
    for p, col in parts:
        sd.text((x, 35), p, font=f, fill=col, anchor="lm"); x += sd.textlength(p, font=f)
    return strip


# ---------------- main renderer (scene layer driven by script beats) ----------------
ASSET_MATCH = [("BTC", ["bitcoin"]), ("ETH", ["ether"]), ("ETH", ["ethereum"]), ("SOL", ["solana"]), ("XRP", ["xrp"]),
               ("DOGE", ["dogecoin"]), ("SPX", ["s", "and", "p"]), ("NDX", ["nasdaq"]), ("US10Y", ["ten", "year"])]
GUS_BOX = (190, 940, 890, 1670)          # bowl + stand region (full layout)
PIP_SCALE, PIP_XY = 0.34, (W - 40 - int(700 * 0.34), 236)
CARD_CY = 935                           # overlay cards are centered here; captions move to CAP_Y_OVL
CAP_Y, CAP_Y_OVL = 930, 1455


def _norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def _mentions(words, t0, t1, assets):
    """First spoken time of each asset inside [t0, t1] -> [(t, asset)]."""
    toks = [(_norm(w["w"]), w["s"]) for w in words if t0 - 0.05 <= w["s"] <= t1 + 0.05]
    out, seen = [], set()
    for key, pat in ASSET_MATCH:
        if key in seen or key not in assets: continue
        for i in range(len(toks) - len(pat) + 1):
            if [x for x, _ in toks[i:i + len(pat)]] == pat:
                out.append((toks[i][1], assets[key])); seen.add(key); break
    return sorted(out, key=lambda x: x[0])[:3]


def build_scenes(job, dur):
    """Overlay windows from the timeline: badges on NUMBERS, news card (+ person card) on PROOF."""
    from . import motion as M
    tl, words = job.get("timeline") or [], job["words"]
    assets = {a["key"]: a for a in job.get("assets", [])}
    scenes = []
    for seg in tl:
        if seg["chapter"] == "THE NUMBERS":
            ms = _mentions(words, seg["t0"], seg["t1"], assets)
            if ms and seg["t1"] - ms[0][0] > 1.2:
                items = [{"t": t, "img": M._shadow(M.asset_badge(a))} for t, a in ms]
                scenes.append({"kind": "badges", "t0": ms[0][0] - 0.3, "t1": seg["t1"] + 0.15, "items": items})
        elif seg["chapter"] == "THE PROOF" and job.get("news"):
            st, t0, t1 = job["news"], seg["t0"] - 0.2, seg["t1"] + 0.25
            nc = M._shadow(M.news_card(st, job["now"]))
            pc = job.get("person_card")
            if pc is not None and t1 - t0 >= 4.0:
                sw = t0 + max(2.4, 0.5 * (t1 - t0))
                sur = _norm(job.get("person_name", "x").split()[-1])
                for w in words:   # switch when the person's surname is spoken, if that is sensible
                    if seg["t0"] + 1.6 <= w["s"] <= t1 - 1.8 and _norm(w["w"]) == sur:
                        sw = w["s"]; break
                scenes.append({"kind": "card", "t0": t0, "t1": sw, "img": nc})
                scenes.append({"kind": "card", "t0": sw, "t1": t1, "img": M._shadow(pc)})
            else:
                scenes.append({"kind": "card", "t0": t0, "t1": t1, "img": nc})
    return scenes


def _ovl_amount(scenes, t, tr=0.32):
    """0..1 how far we are in overlay layout (Gus in PiP)."""
    m = 0.0
    wins = []
    for s in sorted(scenes, key=lambda s: s["t0"]):     # merge back-to-back windows
        if wins and s["t0"] - wins[-1][1] < 0.6: wins[-1][1] = max(wins[-1][1], s["t1"])
        else: wins.append([s["t0"], s["t1"]])
    for a, b in wins:
        m = max(m, min(1.0, (t - a) / tr, (b - t) / tr))
    return max(0.0, m), wins


def render_video(job, out_mp4, cfg):
    """job: words, voice_wav, mood, level, label, channel, handle, date_str, chart_png, ticker, source_line, seed,
    timeline, big_*, tail, assets[], news, now, person_card, person_name, shake_pct"""
    from . import motion as M
    vc = cfg["video"]; fps = vc["fps"]; mcfg = cfg.get("motion", {})
    work = Path(out_mp4).parent
    mixed = str(work / "mix.wav")
    tail = job.get("tail", 0.8)
    dur = sf.info(job["voice_wav"]).duration + tail
    n = int(dur * fps)
    tl = job.get("timeline") or []
    scenes = build_scenes(job, dur) if mcfg.get("enabled", True) else []
    _, wins = _ovl_amount(scenes, 0)
    # ---- beat events: transitions (wipe + whoosh), pops, number ticks, shakes ----
    cuts = sorted({round(s["t0"], 2) for s in tl[1:] if s["chapter"] != "SAVE"} | {round(a, 2) for a, _ in wins} |
                  {round(b, 2) for _, b in wins if b < dur - 0.5})
    trans = []
    for c in cuts:
        if not trans or c - trans[-1] > 0.7: trans.append(c)
    events = [(c, "whoosh") for c in trans]
    for s in scenes:
        if s["kind"] == "badges": events += [(it["t"], "pop") for it in s["items"]]
        else: events.append((s["t0"] + 0.1, "pop"))
    shake_pct = float(job.get("shake_pct", 3.0)); shakes = []
    for w in job["words"]:
        if word_color(w["w"]) == ORANGE:
            events.append((w["s"], "tick"))
            m_ = re.search(r"(\d+(?:\.\d+)?)%", w["w"])
            if m_ and float(m_.group(1)) >= shake_pct: shakes.append(w["s"])
    sfx_wav = None
    if mcfg.get("sfx", True):
        sfx_wav = str(work / "sfx.wav")
        sf.write(sfx_wav, M.sfx_track(events, dur, seed=job["seed"] % 1000), 48000)
    dur = mix_audio(job["voice_wav"], job["mood"], mixed, cfg["music"], job["seed"], tail=tail, sfx_wav=sfx_wav,
                    sfx_db=mcfg.get("sfx_db", -9))
    env = envelope(job["voice_wav"], fps, n)
    rnd = random.Random(job["seed"])
    big = {"label": job.get("big_label", ""), "value": job.get("big_value", ""), "sub": job.get("big_sub", ""),
           "dir": job.get("big_dir", 0)} if job.get("big_value") else None
    bg = background(job["mood"], job["label"], job.get("handle", job["channel"]), job["date_str"], job["chart_png"],
                    job["source_line"], big).convert("RGB")
    bg_dark = Image.blend(bg, Image.new("RGB", bg.size, NAVY_D), 0.8)
    bg_dark.paste(bg.crop((0, 0, W, 232)), (0, 0))                   # header stays readable
    cta = next((s for s in tl if s["chapter"] == "SAVE"), None)
    take = next((s for s in tl if s["chapter"] == "GUS'S TAKE"), None)
    poll = next((s for s in tl if s["chapter"] == "YOUR CALL"), None)
    bowl = Bowl(cx=540, cy=1255, r=345)
    gx0, gy0, gx1, gy1 = GUS_BOX
    shell = Image.new("RGBA", (gx1 - gx0, gy1 - gy0), (0, 0, 0, 0))
    shell.alpha_composite(bowl.base.crop(GUS_BOX)); shell.alpha_composite(bowl.back.crop(GUS_BOX))
    front_g = bowl.front.crop(GUS_BOX)
    mask_g = np.array(bowl.mask.crop(GUS_BOX))
    sprites = {k: v.resize((int(v.width * FISH_SCALE), int(v.height * FISH_SCALE)), Image.LANCZOS)
               for k, v in sprite_set(snorkel=job["mood"] == "red").items()}
    sw, sh = next(iter(sprites.values())).size
    strip = ticker_strip(job["ticker"])
    arrows = {True: M.arrow(True), False: M.arrow(False)}
    mood_c = MOOD_COL[job["mood"]]
    blinks, tt = [], rnd.uniform(1.5, 3)
    while tt < dur:
        blinks.append(tt); tt += rnd.uniform(2.4, 5.0)
    bubbles = []
    target = job["level"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-i", mixed, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", vc["preset"],
           "-crf", str(vc["crf"]), "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", out_mp4]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        t = i / fps
        m, _ = _ovl_amount(scenes, t); me = M.ease_in_out(m)
        fr = (bg_dark.copy() if me >= 0.999 else bg.copy() if me <= 0.001 else Image.blend(bg, bg_dark, me)).convert("RGBA")
        # ---- Gus layer (water, fish, bubbles inside the bowl) ----
        k = min(1, t / 2.2)
        ease = 1 - (1 - k) ** 3 + 0.06 * math.sin(k * math.pi) * (1 if target > 0.7 else -1)
        level = 0.70 + (target - 0.70) * ease
        sy = bowl.level_y(level) - gy0
        reg = Image.new("RGBA", shell.size, (0, 0, 0, 0)); rd = ImageDraw.Draw(reg)
        pts = [(x, sy + 7 * math.sin(x / 45 + t * 3.1) + 4 * math.sin(x / 23 - t * 2.3)) for x in range(0, reg.width + 8, 8)]
        rd.polygon(pts + [(reg.width, reg.height), (0, reg.height)], fill=(64, 140, 215, 205))
        rd.line(pts, fill=(170, 215, 255, 230), width=5)
        bob = 9 * math.sin(2 * math.pi * t / 2.3)
        fcx = bowl.cx - gx0 + 10 * math.sin(2 * math.pi * t / 6.1)
        fcy = min(sy + 175, (bowl.cy + bowl.r - 30 - gy0) - 150) + bob
        mouth = int(round(min(4, env[i] * 4.4))) if env[i] > 0.12 else 0
        eyes = "open"
        for b in blinks:
            dtb = t - b
            if 0 <= dtb < 0.07 or 0.17 <= dtb < 0.23: eyes = "half"
            elif 0.07 <= dtb < 0.17: eyes = "closed"
        if t > dur - 1.2: eyes = "half"
        reg.alpha_composite(sprites[(eyes, mouth, (i // 4) % 4)], (int(fcx - sw / 2), int(fcy - sh / 2)))
        tint = Image.new("RGBA", reg.size, (0, 0, 0, 0))
        ImageDraw.Draw(tint).polygon(pts + [(reg.width, reg.height), (0, reg.height)], fill=(70, 150, 230, 38))
        reg.alpha_composite(tint)
        if rnd.random() < (0.10 + 0.25 * env[i]):
            bubbles.append([fcx - sw * 0.3 + rnd.uniform(-10, 10), fcy - 10, rnd.uniform(4, 10), rnd.uniform(1.5, 3.2)])
        for b in bubbles:
            b[1] -= b[3]; b[0] += 0.8 * math.sin(b[1] / 15)
            if b[1] > sy + 6:
                rd.ellipse([b[0] - b[2], b[1] - b[2], b[0] + b[2], b[1] + b[2]], outline=(220, 240, 255, 200), width=2)
        bubbles = [b for b in bubbles if b[1] > sy + 6]
        reg.putalpha(Image.fromarray(np.minimum(np.array(reg.getchannel("A")), mask_g)))
        gus = shell.copy(); gus.alpha_composite(reg); gus.alpha_composite(front_g)
        # ---- camera: punch-in on the hook, push-in on Gus's take, pulse on the poll ----
        z = 1.0
        if t < 0.6: z = 1.14 - 0.14 * M.ease_out(t / 0.6)
        if take and take["t0"] <= t <= take["t1"] + 0.3:
            z = max(z, 1 + 0.12 * min(M.ease_out((t - take["t0"]) / 0.5), M.ease_out((take["t1"] + 0.3 - t) / 0.3)))
        if poll and 0 <= t - poll["t0"] < 0.5: z = max(z, 1 + 0.07 * math.sin(math.pi * (t - poll["t0"]) / 0.5))
        sc = (1 - me) * z + me * PIP_SCALE
        cx = (1 - me) * ((gx0 + gx1) / 2) + me * (PIP_XY[0] + 350 * PIP_SCALE)
        cy = (1 - me) * ((gy0 + gy1) / 2 - (z - 1) * 120) + me * (PIP_XY[1] + 365 * PIP_SCALE)
        gw, gh = int(gus.width * sc), int(gus.height * sc)
        if me > 0.02:   # PiP frame
            pad = 14 * me
            box = [cx - gw / 2 - pad, cy - gh / 2 - pad, cx + gw / 2 + pad, cy + gh / 2 + pad]
            ImageDraw.Draw(fr).rounded_rectangle(box, radius=int(30 * me + 6), fill=NAVY_C, outline=mood_c, width=max(1, int(5 * me)))
        g2 = gus if abs(sc - 1) < 1e-3 else gus.resize((max(1, gw), max(1, gh)), Image.BILINEAR)
        fr.paste(g2, (int(cx - g2.width / 2), int(cy - g2.height / 2)), g2)
        # ---- overlay scenes ----
        for s_ in scenes:
            if not (s_["t0"] - 0.05 <= t <= s_["t1"] + 0.3): continue
            out_k = M.ease_in_out((t - s_["t1"]) / 0.3) if t > s_["t1"] else 0.0
            if s_["kind"] == "card":
                kin = M.ease_back((t - s_["t0"]) / 0.4)
                img = s_["img"]; scl = 0.75 + 0.25 * kin
                if abs(scl - 1) > 0.01: img = img.resize((int(img.width * scl), int(img.height * scl)), Image.BILINEAR)
                x = int(W / 2 - img.width / 2 + (1 - min(1, kin)) * 300 - out_k * 1200)
                fr.paste(img, (x, int(CARD_CY - img.height / 2)), img)
            else:
                items = s_["items"]; hh = items[0]["img"].height - 10
                ytop = CARD_CY - (len(items) * hh) / 2
                for j, it in enumerate(items):
                    if t < it["t"] - 0.05: continue
                    kin = M.ease_back((t - it["t"]) / 0.38)
                    x = int(W / 2 - it["img"].width / 2 + (1 - kin) * 900 - out_k * (1200 + 120 * j))
                    fr.paste(it["img"], (x, int(ytop + j * hh)), it["img"])
        # ---- chapter line, progress bar, SAVE THIS ----
        d = ImageDraw.Draw(fr)
        if tl: draw_chapter(fr, tl, t)
        d.rectangle([0, 0, int(W * t / dur), 9], fill=mood_c)
        if cta and me < 0.5:
            pill = save_pill(t, cta["t0"], cta["t1"])
            if pill is not None: fr.alpha_composite(pill, (W - 60 - pill.width, 812 - pill.height // 2))
        # ---- caption + number pop-up ring + animated arrows ----
        cw = current_word(job["words"], t)
        if cw:
            cy_cap = CAP_Y + (CAP_Y_OVL - CAP_Y) * me
            col = word_color(cw["w"])
            if col == ORANGE and t - cw["s"] < 0.4:
                kr = (t - cw["s"]) / 0.4; r_ = 120 + 260 * M.ease_out(kr)
                ring = Image.new("RGBA", (int(2 * r_ + 20), int(2 * r_ + 20)), (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse([10, 10, 10 + 2 * r_, 10 + 2 * r_], outline=ORANGE + (int(200 * (1 - kr)),), width=10)
                fr.alpha_composite(ring, (int(W / 2 - ring.width / 2), int(cy_cap - ring.height / 2)))
            tw = draw_word(fr, cw, t, y=int(cy_cap), scale=1 - 0.2 * me)
            if col in (MINT, CORAL) and t - cw["s"] < 0.9:
                ka = min(1, (t - cw["s"]) / 0.25); up = col == MINT
                ar = arrows[up]; dy = (-1 if up else 1) * 40 * M.ease_out((t - cw["s"]) / 0.9)
                ax = int(max(10, W / 2 - tw / 2 - ar.width - 20)); ay = int(cy_cap - ar.height / 2 + dy)
                a2 = ar.copy(); a2.putalpha(a2.getchannel("A").point(lambda v, ka=ka: int(v * ka)))
                fr.alpha_composite(a2, (ax, ay))
                fr.alpha_composite(a2, (int(min(W - ar.width - 10, W / 2 + tw / 2 + 20)), ay))
        # ---- wipe transitions ----
        for c in trans:
            if 0 <= t - c < 0.3:
                kx = (t - c) / 0.3; x0 = -500 + (W + 1000) * M.ease_in_out(kx)
                band = [(x0, 232), (x0 + 260, 232), (x0 + 60, H - 240), (x0 - 200, H - 240)]
                ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
                od.polygon(band, fill=mood_c + (70,)); od.polygon([(p[0] + 140, p[1]) for p in band], fill=WHITE + (35,))
                fr.alpha_composite(ov)
        # ---- flashing ticker tape ----
        d = ImageDraw.Draw(fr)
        d.rectangle([0, 1606, W, 1694], fill=NAVY_D)
        flash = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.6)
        d.rectangle([0, 1606, W, 1611], fill=tuple(int(c * (0.45 + 0.55 * flash)) for c in mood_c))
        off = int((t * 110) % strip.width)
        fr.alpha_composite(strip, (W - off, 1615)); fr.alpha_composite(strip, (W - off - strip.width, 1615))
        if off > W: fr.alpha_composite(strip, (W - off + strip.width, 1615))
        tagc = ORANGE if int(t * 2) % 2 == 0 else WHITE
        d.rectangle([0, 1612, 206, 1694], fill=tagc)
        d.text((103, 1653), "MARKETS", font=font(F_TITLE, 34), fill=NAVY, anchor="mm")
        # ---- shake on big moves ----
        arr = np.asarray(fr.convert("RGB"))
        for s0 in shakes:
            if 0 <= t - s0 < 0.45:
                amp = 16 * (1 - (t - s0) / 0.45)
                dx, dy = int(amp * math.sin(t * 90)), int(amp * math.cos(t * 70))
                arr = np.roll(arr, (dy, dx), axis=(0, 1)); break
        proc.stdin.write(arr.tobytes())
    proc.stdin.close(); proc.wait()
    if proc.returncode: raise RuntimeError("ffmpeg failed")
    return dur
