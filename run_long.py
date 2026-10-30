#!/usr/bin/env python3
"""Gus Markets Daily Update: ~5 minute 3840x2160 @ 60 fps landscape video (normal YouTube upload, not a Short).

  python run_long.py --format daily_update --dry-run           # render only, no upload
  python run_long.py --auto                                     # scheduled: daily_update (Mon-Fri) / weekend_update (Sat, Sun)
  python run_long.py --format daily_update --dry-run --stills 600,4000 --snap /tmp/snap_long.pkl --no-llm
  python run_long.py --format daily_update --dry-run --excerpt 0,60
"""
import argparse, json, os, re, sys, time, hashlib, pickle, subprocess, datetime as dt
from pathlib import Path
import numpy as np, soundfile as sf
from finshorts.config import load_config, OUT
from finshorts import market, news, tts, dedupe, people, factcheck
from finshorts import script_gen as SG
from finshorts.long_data import snapshot_long, next_session, MONTHS
from finshorts.long_script import build_long_facts, verify_facts, generate_long, spoken_parts, fill, OUTRO, OUTRO_WEEKEND
from finshorts.metadata import DISCLOSURE

SR = tts.SR
EXCL = re.compile(r"\b(usd\w*|dollar|treasury|yield|t-?bill|buidl|ousg|usyc|usdg|wrapped|staked)\b", re.I)
CH_NAMES = ["Cold open", "Market overview", "Top stories", "Crypto deep dive", "What to watch", "Gus's takeaway"]


def zurich(t):
    """UTC datetime -> Europe/Zurich (CET/CEST by EU rule), no tz database needed."""
    y = t.year
    def last_sun(m): d = dt.datetime(y, m, 31, 1, tzinfo=dt.timezone.utc); return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    off = 2 if last_sun(3) <= t < last_sun(10) else 1
    return t + dt.timedelta(hours=off)


def mvtxt(s):
    """'down 0.4%' -> '0.4%', 'flat' -> 'FLAT'."""
    return "FLAT" if s == "flat" else re.sub(r"^(up|down)\s+", "", s)


def sgn(v, eps=0.05): return (v >= eps) - (v <= -eps)


def downsample(pts, n=180):
    if len(pts) <= n: return list(pts)
    idx = np.linspace(0, len(pts) - 1, n).round().astype(int)
    return [pts[i] for i in idx]


def clean_movers(m, n=3):
    g = [x for x in m.get("gainers", []) if not EXCL.search(f"{x['name']} {x['symbol']}")][:n]
    l = [x for x in m.get("losers", []) if not EXCL.search(f"{x['name']} {x['symbol']}")][:n]
    return {**m, "gainers": g, "losers": l}


# ---------------- stories ----------------
STOP = set("the and for with from that this into after over amid says said could would will its their than about more what why how new as at by on in of to is are be was were has have had can may might just still crypto market markets stock stocks shares price prices report reports week day".split())


def _sig(h):
    return {w for w in re.findall(r"[a-z0-9]{3,}", h.lower()) if w not in STOP}


def same_event(a, b):
    """Two headlines about the same event (e.g. three takes on one exchange hack) -> True. Shared >= 2 distinctive words
    or > 35 % overlap of the shorter headline."""
    x, y = _sig(a["headline"]), _sig(b["headline"])
    sh = len(x & y)
    return sh >= 2 or sh / max(1, min(len(x), len(y))) > 0.35


def pick_stories(cfg, weekend, log):
    lc = cfg["long"]; nmax = int(lc.get("stories_max", 4)); hrs = float(lc.get("story_dedupe_hours", 72))
    items, ferr = news.fetch_feeds(cfg)
    log["feed_errors"] = ferr; log["feed_items"] = len(items)
    fresh = lambda s: not dedupe.story_used_recently(s, hours=hrs, log=dedupe.LONG_LOG)
    st = [s for s in news.verified_stories(cfg, ["stocks", "macro"], items) if fresh(s)]
    cr = [s for s in news.verified_stories(cfg, ["crypto"], items) if fresh(s)]
    n_cr = 2 if weekend else 1
    out = []
    for s in st[: nmax - min(n_cr, len(cr))] + cr[:n_cr]:          # dedupe across topics too
        if not any(same_event(s, o) for o in out): out.append(s)
    if len(out) < nmax:                                          # top up from the other list (still one story per event)
        for s in st + cr:
            if len(out) >= nmax: break
            if s not in out and not any(same_event(s, o) for o in out): out.append(s)
    log["verified_stories"] = [{k: s[k] for k in ("headline", "sources", "n_owners")} for s in out]
    return out[:nmax]


# ---------------- voice ----------------
def synth_sections(cfg, secs, jd, gaps):
    """secs: [(id, text)]; gaps: {id: seconds of silence BEFORE it}. -> voice.wav, words, {id: {t0, t1, words}}"""
    audio, words, sec, t = [], [], {}, 0.0
    for sid, text in secs:
        g = gaps.get(sid, 0.0)
        if g: audio.append(np.zeros(int(g * SR), np.float32)); t += g
        wp = jd / f"tts_{sid}.wav"
        w = tts.synthesize(cfg, text, str(wp))
        a, sr = sf.read(str(wp), dtype="float32")
        if a.ndim > 1: a = a.mean(1)
        # trim leading / trailing silence so gaps are exact
        nz = np.where(np.abs(a) > 0.004)[0]
        i0 = max(0, nz[0] - int(0.03 * sr)) if len(nz) else 0
        i1 = min(len(a), nz[-1] + int(0.08 * sr)) if len(nz) else len(a)
        a = a[i0:i1]; sh = i0 / sr
        ww = [{"w": x["w"], "s": round(t + max(0.0, x["s"] - sh), 3), "e": round(t + max(0.0, x["e"] - sh), 3)} for x in w]
        audio.append(a)
        sec[sid] = {"t0": t, "t1": t + len(a) / sr, "words": ww, "text": text}
        words += ww; t += len(a) / sr
    voice = str(jd / "voice.wav")
    sf.write(voice, np.concatenate(audio), SR)
    for p in jd.glob("tts_*.wav"): p.unlink()
    return voice, words, sec, t


# ---------------- visual data (all numbers from the snapshot / facts) ----------------
def close_lab(d):
    d = dt.date.fromisoformat(str(d)[:10]); return f"Close {d:%a %b} {d.day}"


def build_visuals(snap, facts, now):
    ind = snap.get("indices", {}); i90 = snap.get("indices90", {}); fr = snap.get("fred", {}); c = snap.get("coins", {})
    tiles, charts = [], {}

    def add(key, name, short, sub, value, mfact, chg, scale, period, pts, rx, vfmt, src, days, chart_name=None, chg_txt=None):
        if not pts: return
        mt = chg_txt or mvtxt(facts.get(mfact, "flat"))
        d = sgn(chg) if chg_txt is None else sgn(chg, 0.5)
        tiles.append({"key": key, "name": name, "short": short, "sub": sub, "value": value, "move_txt": mt, "dir": d, "chg": chg,
                      "scale": scale, "period": period, "spark": [p[1] for p in pts[-30:]], "rx": rx})
        cp = downsample(pts)
        a, b = cp[0][1], cp[-1][1]
        if "%" in vfmt:
            bp = (b - a) * 100; ptxt, pd = f"{abs(bp):.0f} BP", sgn(bp, 0.5)
        else:
            pc = (b / a - 1) * 100; ptxt, pd = (mvtxt(SG.move(pc)), sgn(pc))
        charts[key] = {"name": chart_name or name, "period": f"{days}D" if days < 60 else f"{round(days / 30)}M", "value": value,
                       "move_txt": ptxt, "dir": pd, "pts": cp, "vfmt": vfmt, "source": src, "days": days,
                       "move_label": f"{f'{days}-day' if days <= 31 else f'{round(days / 30)}-month'} change"}

    for sym, key, nm, sh, rx in (("^GSPC", "spx", "S&P 500", "S&P", r"s&p|sp500"), ("^IXIC", "ndx", "Nasdaq", "NDX", r"nasdaq"),
                                 ("^DJI", "dow", "Dow Jones", "DOW", r"dow")):
        i = ind.get(sym)
        if not i or f"{key.upper() if key != 'ndx' else 'NDX'}_LEVEL" not in facts and key != "ndx": continue
        fk = {"spx": "SPX", "ndx": "NDX", "dow": "DOW"}[key]
        if f"{fk}_LEVEL" not in facts: continue
        pts = (i90.get(sym) or i)["points"]
        add(key, nm, sh, close_lab(i["asof"]), facts[f"{fk}_LEVEL"], f"{fk}_MOVE", i["chg_1d"], 1.5, "1 day", pts, rx, "{:,.0f}",
            f"Source: {i['source']} \u00B7 {close_lab(i['asof'])}", 90 if sym in i90 else 40)
    for cid, key, nm, sh, rx, fk in (("bitcoin", "btc", "Bitcoin", "BTC", r"bitcoins?|bitcoin's", "BTC"),
                                     ("ethereum", "eth", "Ether", "ETH", r"ether|ethereum|ether's", "ETH")):
        if cid in c and f"{fk}_PRICE" in facts:
            hh = snap.get("hist", {}); h = hh.get(f"{cid}30") or hh.get(f"{cid}7") or []
            t = dt.datetime.fromisoformat(str(c[cid]["asof"]).replace("Z", "+00:00"))
            add(key, nm, sh, f"CoinGecko \u00B7 {t:%H:%M} UTC", facts[f"{fk}_PRICE"], f"{fk}_MOVE", c[cid]["chg_24h"], 5.0, "24 hours",
                h, rx, "{:,.0f}" if c[cid]["price"] > 100 else "{:,.2f}", f"Source: CoinGecko \u00B7 as of {t:%b} {t.day}, {t:%H:%M} UTC", 30 if hh.get(f"{cid}30") else 7)
            if key in charts: charts[key + "30"] = charts[key]
    g = snap.get("gold")
    if g and "GOLD_PRICE" in facts:
        add("gold", "Gold", "GOLD", close_lab(g["asof"]), facts["GOLD_PRICE"], "GOLD_MOVE", g["chg_1d"], 2.0, "1 day", g["points"][-90:],
            r"gold", "{:,.0f}", f"Source: {g['source']} \u00B7 {close_lab(g['asof'])}", 90, "Gold (USD per ounce)")
    if "DCOILWTICO" in fr and "OIL_PRICE" in facts:
        o = fr["DCOILWTICO"]
        add("oil", "WTI crude", "OIL", close_lab(o["asof"]), facts["OIL_PRICE"], "OIL_MOVE", o["chg_1d"], 3.0, "1 day", o["points"][-66:],
            r"oil|crude", "{:,.2f}", f"Source: FRED / EIA WTI spot \u00B7 {close_lab(o['asof'])}", 90, "WTI crude oil")
    if "DTWEXBGS" in fr and "USD_INDEX" in facts:
        u = fr["DTWEXBGS"]
        add("usd", "US dollar", "USD", f"Fed broad index \u00B7 {close_lab(u['asof'])[6:]}", facts["USD_INDEX"], "USD_MOVE_1M",
            u["chg_1m_pct"], 1.0, "1 month", u["points"][-66:], r"dollar|dollar's", "{:,.1f}",
            f"Source: FRED DTWEXBGS (Fed nominal broad dollar index, not DXY) \u00B7 {close_lab(u['asof'])}", 90, "US dollar index (Fed broad)")
    if "DGS10" in fr and "US10Y" in facts:
        y = fr["DGS10"]; bp = y["chg_1m"] * 100
        add("us10y", "10Y yield", "US 10Y", close_lab(y["asof"]), facts["US10Y"], "US10Y_MOVE_1M", bp, 25.0, "1 month",
            y["points"][-66:], r"ten-year|10-year|treasury|treasuries", "{:.2f}%", f"Source: FRED DGS10 \u00B7 {close_lab(y['asof'])}", 90,
            "10-year Treasury yield", chg_txt=("FLAT" if abs(bp) < 1 else f"{abs(bp):.0f} BP"))
    return tiles, charts


def check_visuals(tiles, facts):
    """On-screen numbers must be the spoken facts (same string)."""
    bad = []
    fk = {"spx": "SPX_LEVEL", "ndx": "NDX_LEVEL", "dow": "DOW_LEVEL", "btc": "BTC_PRICE", "eth": "ETH_PRICE", "gold": "GOLD_PRICE",
          "oil": "OIL_PRICE", "usd": "USD_INDEX", "us10y": "US10Y"}
    mk = {"spx": "SPX_MOVE", "ndx": "NDX_MOVE", "dow": "DOW_MOVE", "btc": "BTC_MOVE", "eth": "ETH_MOVE", "gold": "GOLD_MOVE",
          "oil": "OIL_MOVE", "usd": "USD_MOVE_1M"}
    for a in tiles:
        if a["value"] != facts.get(fk[a["key"]]): bad.append(f"{a['key']} value {a['value']} != {facts.get(fk[a['key']])}")
        if a["key"] in mk:
            f = facts.get(mk[a["key"]], "flat")
            if a["move_txt"] != mvtxt(f) or (a["dir"] > 0) != f.startswith("up") or (a["dir"] < 0) != f.startswith("down"):
                bad.append(f"{a['key']} move {a['dir']} {a['move_txt']} != {f}")
    return bad


def crypto_panel(snap, facts):
    gl = snap.get("global") or {}; m = clean_movers(snap.get("movers") or {})
    row = lambda x: (x["name"], x["symbol"].upper(), x["chg_24h"], SG.pct(x["chg_24h"]))
    return {"mcap": facts.get("CRYPTO_MCAP", "").upper(), "mcap_move": mvtxt(facts.get("CRYPTO_MCAP_MOVE", "flat")),
            "mcap_dir": sgn(gl.get("mcap_chg_24h", 0)), "dom": float(gl.get("btc_dominance", 0)), "dom_txt": facts.get("BTC_DOMINANCE", ""),
            "gainers": [row(x) for x in m.get("gainers", [])[:3]], "losers": [row(x) for x in m.get("losers", [])[:3]]}


def rates_panel(snap, facts):
    fr = snap.get("fred", {}); y = fr.get("DGS10")
    pts = downsample(y["points"][-130:], 160)
    bp = (pts[-1][1] - pts[0][1]) * 100
    ch = {"name": "10-year yield", "period": "6M", "value": facts["US10Y"], "move_txt": "FLAT" if abs(bp) < 1 else f"{abs(bp):.0f} BP",
          "dir": sgn(bp, 0.5), "pts": pts, "vfmt": "{:.2f}%", "source": f"Source: FRED DGS10 \u00B7 {close_lab(y['asof'])}", "days": 180,
          "move_label": "6-month change"}
    stats = []
    if "US2Y" in facts:
        d2 = fr["DGS2"]["chg_1m"] * 100
        stats.append({"label": "2-year yield", "value": facts["US2Y"], "sub": f"{'+' if d2 > 0 else '-' if d2 < 0 else ''}{abs(d2):.0f} bp in a month", "dir": sgn(d2, 0.5)})
    if "FED_FUNDS" in facts:
        stats.append({"label": "Fed funds rate", "value": facts["FED_FUNDS"], "sub": "effective rate \u00B7 FRED", "dir": None})
    if "US10Y_MOVE_1M" in facts:
        d10 = fr["DGS10"]["chg_1m"] * 100
        stats.append({"label": "10Y, one month", "value": ("FLAT" if abs(d10) < 1 else f"{'+' if d10 > 0 else '-'}{abs(d10):.0f} BP"),
                      "sub": "change in basis points", "dir": sgn(d10, 0.5)})
    return {"chart": ch, "stats": stats[:3]}


def calendar_panel(snap, facts, now, weekend):
    from finshorts.premium import ORANGE, MINT
    ev = []
    ny = market.session_state(now)["ny_date"]
    ss = market.session_state(now)
    nx = ny if (market._is_session(ny) and not ss.get("today_complete")) else next_session(ny)
    ev.append({"top": f"{nx:%b}", "big": str(nx.day), "weekday": f"{nx:%A}", "title": "Next US stock market session",
               "src": "NYSE trading calendar", "col": MINT})
    fo = snap.get("fomc")
    if fo:
        d = dt.date.fromisoformat(fo["date"])
        ev.append({"top": f"{d:%b}", "big": str(d.day), "weekday": fo["weekday"], "title": "Fed interest rate decision (FOMC)",
                   "src": "federalreserve.gov", "col": ORANGE})
    for n in range(1, 3):
        if f"CAL{n}_NAME" in facts:
            ev.append({"top": "DATA", "big": facts[f"CAL{n}_DAY"][:3].upper(), "weekday": facts[f"CAL{n}_DAY"], "title": facts[f"CAL{n}_NAME"],
                       "src": "FRED release calendar", "col": (120, 170, 240)})
    if len(ev) < 3:
        ev.append({"top": "Crypto", "big": "24/7", "weekday": "every day", "title": "Bitcoin and Ether keep trading through nights and weekends",
                   "src": "CoinGecko", "col": (120, 170, 240)})
    return ev[:4], ("The week ahead" if weekend else "Official calendars only")


# ---------------- metadata ----------------
def mmss(t): t = int(t); return f"{t // 60:02d}:{t % 60:02d}"


def build_meta(cfg, title, parts, facts, stories, chapters, sources, credits, weekend):
    ch = cfg["channel"]; lc = cfg["long"]
    chap = "\n".join(f"{mmss(0 if j == 0 else c['t0'])} {c['name']}" for j, c in enumerate(chapters))
    st = ""
    for j, s in enumerate(stories, 1):
        st += f"\n{j}. {s['headline']}\n   Reported by: {', '.join(s['sources'][:4])}\n" + "".join(f"   {l}\n" for l in s["links"][:2])
    cred = ("\nImage credits:\n" + "\n".join(credits) + "\n") if credits else ""
    desc = (f"{fill(parts.get('summary', ''), facts)}\n\n"
            f"{'Weekend Update' if weekend else 'Daily Update'} from {ch['name']}: {ch['mascot']} the goldfish walks through stocks, crypto, "
            f"gold, oil, the dollar and Treasury yields, the day's top stories (each verified by at least two independent newsrooms) "
            f"and what to watch next.\n\nChapters\n{chap}\n\nStories{st}\n"
            f"Market data: {', '.join(sources)} (Price data by CoinGecko). Every number in this video comes from these sources and "
            f"is checked automatically against them before publishing. Oil and the dollar index are published with a delay and are "
            f"shown with their as-of dates. The dollar index is the Fed's broad trade-weighted index, not the ICE DXY.\n{cred}\n"
            f"NOT FINANCIAL ADVICE. {ch['disclaimer']} Nothing in this video is a recommendation to buy, sell or hold anything.\n"
            f"{DISCLOSURE}\n\n{ch['catchphrase']}\n\n#stockmarket #crypto #investing")
    tags = ["stock market", "daily market update", "stock market news", "crypto news", "bitcoin", "ethereum", "S&P 500", "Nasdaq",
            "Dow Jones", "gold price", "oil price", "treasury yields", "Federal Reserve", "finance news", "Gus Markets", "market recap"]
    yt = {"title": title[:100], "description": desc[:4900], "tags": tags}
    return {"youtube": yt}


# ---------------- main job ----------------
def run(cfg, fmt_name, dry_run=True, no_llm=False, slot_date=None, snap_path=None, stills=None, excerpt=None):
    t0 = time.time()
    lc = cfg["long"]; fmt = cfg["formats"][fmt_name]; weekend = fmt_name == "weekend_update"
    now = dt.datetime.now(dt.timezone.utc)
    job_id = f"{(slot_date or now.date()):%Y%m%d}_long_{fmt_name}"
    jd = OUT / job_id; jd.mkdir(parents=True, exist_ok=True)
    log = {"job_id": job_id, "format": fmt_name, "started": now.isoformat(), "dry_run": dry_run}
    if not dry_run and dedupe.already_uploaded("youtube", job_id=job_id, log=dedupe.LONG_LOG):
        log["youtube"] = "skipped (already uploaded for this day)"; print(log["youtube"]); return log
    # ---- data ----
    if snap_path:
        snap = pickle.load(open(snap_path, "rb")); log["snapshot"] = f"loaded {snap_path} (TEST)"
    else:
        snap = snapshot_long(cfg)
        pickle.dump(snap, open(jd / "snap.pkl", "wb"))
    if snap.get("movers"): snap["movers"] = clean_movers(snap["movers"])
    log["data_errors"] = snap.get("errors", [])
    for e in log["data_errors"]: print(f"::warning title=data::{str(e)[:200]}")
    facts, raw = build_long_facts(snap, cfg, now, weekend)
    ok, bad = verify_facts(facts, raw)
    if not ok: raise RuntimeError("fact-check: placeholder values disagree with the API numbers: " + "; ".join(bad))
    log["facts_verified"] = len([k for k in raw if k in facts])
    stories = []
    try:
        stories = pick_stories(cfg, weekend, log)
    except Exception as e:
        log["stories_error"] = str(e)[:200]
    mood, mv = market.mood(snap, ("stocks", "crypto", "macro"))
    recent = [u.get("title", "") for u in dedupe._load(dedupe.LONG_LOG)["uploads"]][-6:]
    parts, provider, glog, keep = generate_long(cfg, facts, stories, mood, weekend, seed=job_id, recent_titles=recent, allow_llm=not no_llm)
    stories = [stories[i] for i in keep]
    log.update({"script_provider": provider, "script_log": glog, "mood": mood, "stories": [s["headline"] for s in stories]})
    F = lambda t: fill(t, facts)
    catch = cfg["channel"]["catchphrase"]
    secs = [("hook", F(parts["hook"])), ("overview", F(parts["overview"]))]
    for i, s in enumerate(parts["stories"]):
        secs += [(f"story{i}", F(s["text"])), (f"why{i}", F(s["why"]))]
    secs += [("crypto", F(parts["crypto"])), ("watch", F(parts["watch"])), ("take", F(parts["take"])), ("catch", catch),
             ("outro", OUTRO_WEEKEND if weekend else OUTRO)]
    (jd / "script.txt").write_text("\n\n".join(f"[{k}]\n{t}" for k, t in secs) + "\n")
    fok, fbad = factcheck.check_script({"body": " ".join(t for k, t in secs if k not in ("catch", "outro"))}, facts)
    if not fok: raise RuntimeError("fact-check (filled script): " + "; ".join(fbad))
    # ---- voice: per-section TTS with designed gaps ----
    G = lc.get("gaps", {})
    gaps = {"overview": G.get("sting", 3.2) + G.get("card", 1.6), "story0": G.get("card", 1.6), "crypto": G.get("card", 1.6),
            "watch": G.get("card", 1.6), "take": G.get("card", 1.6), "catch": 0.45, "outro": 1.1}
    for i in range(len(parts["stories"])):
        gaps[f"why{i}"] = G.get("why", 0.5)
        if i: gaps[f"story{i}"] = G.get("story", 1.0)
    kk = cfg["tts"]["kokoro"]; kk["speed"] = float(lc.get("tts_speed", 1.0))
    lo, hi = lc.get("target_seconds", [270, 330])
    t1 = time.time()
    for attempt in range(3):
        voice, words, sec, vdur = synth_sections(cfg, secs, jd, gaps)
        total = vdur + float(lc.get("tail", 4.0))
        if lo <= total <= hi or attempt == 2: break
        sp = kk["speed"] * total / (hi - 8 if total > hi else lo + 8)
        sp = max(0.92, min(1.12, sp))
        if abs(sp - kk["speed"]) < 0.01: break
        kk["speed"] = round(sp, 3)
    log.update({"tts_seconds": round(time.time() - t1, 1), "tts_speed": kk["speed"], "voice_duration": round(vdur, 2)})
    dur = round(vdur + float(lc.get("tail", 4.0)), 3)
    (jd / "words.json").write_text(json.dumps(words))
    # ---- timeline ----
    cardlen = G.get("card", 1.6)
    st0 = sec["hook"]["t1"] + 0.15; st1 = st0 + G.get("sting", 3.2) - 0.1
    first = {1: "overview", 2: "story0", 3: "crypto", 4: "watch", 5: "take"}
    cards, chapters = [], [{"name": CH_NAMES[0], "sub": "", "lt": "", "t0": 0.0}]
    subs = {1: ("Stocks, crypto, gold, oil, the dollar and yields", "Index levels and moves from official data"),
            2: (f"{len(stories)} stories, each confirmed by two or more newsrooms", "Headline, explainer, why it matters"),
            3: ("Bitcoin, Ether, market value and the biggest movers", "CoinGecko data, updated around the clock"),
            4: ("The calendar and the rate backdrop", "Official calendars and Treasury yields"),
            5: ("The big picture, from a goldfish", "Calm view, no advice")}
    if weekend: subs[1] = ("The week in review across markets", "Weekly moves from official data")
    for idx in range(1, 6):
        if first[idx] not in sec: continue
        ts = sec[first[idx]]["t0"]
        c0 = ts - cardlen - 0.05 if idx > 1 else st1 + 0.02
        cards.append({"t0": c0, "t1": ts - 0.05, "idx": idx})
        chapters.append({"name": CH_NAMES[idx], "sub": subs[idx][0], "lt": subs[idx][1], "t0": c0})
    # ---- visuals ----
    tiles, charts = build_visuals(snap, facts, now)
    vb = check_visuals(tiles, facts)
    if vb: raise RuntimeError("fact-check (on-screen tiles): " + "; ".join(vb))
    ev, wsub = calendar_panel(snap, facts, now, weekend)
    cp = crypto_panel(snap, facts)
    level = {"green": 0.86, "neutral": 0.72, "red": 0.56}[mood] + max(-0.04, min(0.04, mv * 0.01))
    persons, credits = [], []
    for s in stories:
        pr = None
        if cfg.get("motion", {}).get("people", True):
            try:
                found = people.find_people(s)
                if found:
                    pp = found[0]; por = people.portrait(pp, cutout=cfg["motion"].get("cutout", True), log=[])
                    pr = {"por": por, "name": pp["name"], "desc": pp.get("desc", ""),
                          "attr": f"Photo: {por['meta']['artist'][:48]} \u00B7 {por['meta']['license']} \u00B7 Wikimedia Commons" if por else ""}
                    if por: credits.append(por["meta"]["attribution"])
            except Exception as e:
                log.setdefault("people_log", []).append(str(e)[:120])
        persons.append(pr)
    tk = []
    for a in tiles:
        tk.append((a["short"], a["value"], a["chg"] if a["key"] != "us10y" else None))
    big_mv = max((a for a in tiles if a["key"] in ("spx", "btc", "ndx")), key=lambda a: abs(a["chg"]) / a["scale"], default=None)
    sources = sorted({"CoinGecko", "FRED", "Alpha Vantage", "federalreserve.gov"})
    zt = zurich(now)
    job = {"seed": int(hashlib.md5(job_id.encode()).hexdigest()[:6], 16), "fps": int(lc["video"].get("fps", 60)), "mood": mood,
           "level": level, "label": fmt["label"], "date_str": f"{zt:%a %b} {zt.day}, {zt:%Y}", "date_long": f"{zt:%A, %B} {zt.day}",
           "date_short": f"{zt:%b} {zt.day}", "handle": cfg["channel"]["handle"],
           "source_line": f"Not financial advice \u00B7 Data: {', '.join(sources)}",
           "disclaimer_long": ("Not financial advice. For entertainment and education only. Gus is a cartoon goldfish, the voice is "
                               "synthetic, and every number comes from the public data sources listed in the description."),
           "outro_lines": ["A new Daily Update every evening", "plus three Shorts a day"],
           "voice_wav": voice, "dur": dur, "words": words, "sec": sec, "chapters": chapters, "sting": (st0, st1), "cards": cards,
           "tiles": tiles, "charts": charts, "crypto": cp, "rates": rates_panel(snap, facts), "events": ev, "watch_sub": wsub,
           "stories": [{"headline": s["headline"], "sources": s["sources"], "published": s.get("published"),
                        "kicker": parts["stories"][i]["kicker"] or "Market news", "why": F(parts["stories"][i]["why"]), "person": persons[i]}
                       for i, s in enumerate(stories)],
           "teasers": [(a["short"], ("\u25B2 " if a["dir"] > 0 else "\u25BC " if a["dir"] < 0 else "") + a["move_txt"], a["dir"])
                       for a in tiles if a["key"] in ("spx", "btc", "us10y")][:3],
           "hook_bg": [p[1] for p in charts.get("spx", charts.get("btc", {"pts": []}))["pts"]],
           "catchphrase": catch, "catch_t": (sec["catch"]["t0"], sec["catch"]["t1"]), "ticker": tk,
           "thumb": (parts.get("thumb") or "DAILY UPDATE").upper(),
           "thumb_num": ((f"{big_mv['short']} {'+' if big_mv['dir'] > 0 else '-' if big_mv['dir'] < 0 else ''}{big_mv['move_txt']}", big_mv["dir"])
                         if big_mv else ("", 0)),
           "chapter_moods": [(mood, 100), (mood, 96), ("neutral", 92), (mood, 104), ("neutral", 90), (mood, 84)][:len(chapters)],
           "now": now}
    with open(jd / "job.pkl", "wb") as fh: pickle.dump(job, fh)
    from finshorts import longform as L
    out_mp4 = str(jd / f"{job_id}.mp4")
    if stills:
        paths = L.render_video(job, out_mp4, cfg, frames=stills)
        thumb = L.make_thumbnail(job, str(jd / "thumbnail.jpg"))
        log.update({"stills": paths, "thumbnail": thumb}); (jd / "run_log.json").write_text(json.dumps(log, indent=1, default=str))
        return log
    t2 = time.time()
    rdur = L.render_video(job, out_mp4, cfg, span=excerpt)
    log["render_seconds"] = round(time.time() - t2, 1)
    try: log["render_timing"] = json.loads((jd / "render_timing.json").read_text())
    except Exception: pass
    thumb = L.make_thumbnail(job, str(jd / "thumbnail.jpg"))
    srt = L.make_srt(words, str(jd / f"{job_id}.en.srt"))
    probed = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out_mp4],
                                  capture_output=True, text=True).stdout.strip() or 0)
    log.update({"video": out_mp4, "probed_duration": round(probed, 2), "planned_duration": dur, "thumbnail": thumb, "srt": srt})
    title = F(parts["title"]).strip().rstrip(".")
    meta = build_meta(cfg, title, parts, facts, stories, chapters, sources, credits, weekend)
    (jd / "metadata.json").write_text(json.dumps(meta, indent=1))
    if excerpt: log["excerpt"] = excerpt
    if not dry_run and not excerpt:
        if not lc.get("upload", {}).get("enabled", True):
            log["youtube"] = "disabled in config (long.upload.enabled=false)"
        elif not (lo - 30 <= probed <= hi + 60):
            raise RuntimeError(f"rendered duration {probed:.1f}s outside the allowed range -> not uploading")
        else:
            from finshorts import upload_youtube as Y
            vh = dedupe.file_hash(out_mp4)
            ycfg = {**cfg["upload"]["youtube"], **lc.get("upload", {})}
            vid, priv = Y.upload(out_mp4, meta["youtube"], ycfg)
            dedupe.record_upload("youtube", vh, job_id, vid, log=dedupe.LONG_LOG, title=title)
            for s in stories: dedupe.record_story(s, log=dedupe.LONG_LOG)
            log["youtube"] = {"id": vid, "privacy": priv, "url": f"https://www.youtube.com/watch?v={vid}"}
            log["thumbnail_set"] = Y.set_thumbnail(vid, thumb)
            log["captions"] = Y.insert_caption(vid, srt) if lc.get("upload", {}).get("captions", True) else "disabled"
            for k in ("thumbnail_set", "captions"):
                if not str(log[k]).startswith("ok"): print(f"::warning title=YouTube {k}::{log[k]}")
    log["total_seconds"] = round(time.time() - t0, 1)
    (jd / "run_log.json").write_text(json.dumps(log, indent=1, default=str))
    return log


def fmt_for_day(d):
    return "weekend_update" if d.weekday() >= 5 else "daily_update"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format"); ap.add_argument("--auto", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true"); ap.add_argument("--config"); ap.add_argument("--slot-date")
    ap.add_argument("--snap", help="TEST: pickled snapshot instead of live APIs (forces --dry-run)")
    ap.add_argument("--stills", help="comma list of frame indices -> PNG stills only (forces --dry-run)")
    ap.add_argument("--excerpt", help="t0,t1 seconds -> render only this span (forces --dry-run)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    sd = dt.date.fromisoformat(a.slot_date) if a.slot_date else dt.datetime.now(dt.timezone.utc).date()
    fmt = a.format or fmt_for_day(sd)
    dry = a.dry_run or bool(a.snap or a.stills or a.excerpt)
    stills = [int(x) for x in a.stills.split(",")] if a.stills else None
    excerpt = tuple(float(x) for x in a.excerpt.split(",")) if a.excerpt else None
    log = run(cfg, fmt, dry_run=dry, no_llm=a.no_llm, slot_date=sd, snap_path=a.snap, stills=stills, excerpt=excerpt)
    print(json.dumps(log, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
