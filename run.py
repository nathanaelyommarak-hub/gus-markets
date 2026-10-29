#!/usr/bin/env python3
"""FinShorts main entry point.

  python run.py --format crypto_update --dry-run      # render only, no approval, no upload
  python run.py --auto                                 # pick the slot due now (cron), render, approve, upload
  python run.py --slot s03                             # a specific slot
"""
import argparse, json, os, re, sys, time, hashlib, datetime as dt, traceback
from pathlib import Path
from finshorts.config import load_config, slot_for_now, videos_per_day, effective_slot, due_slot, slot_job_id, OUT
from finshorts import market, news, script_gen, tts, chart, render, metadata, dedupe, people, motion

CHART_ROTATION = [("coin", "bitcoin", 30), ("index", "^GSPC", 60), ("coin", "ethereum", 30), ("fred", "DGS10", 180), ("index", "^IXIC", 60)]


def pick_chart(cfg, fmt_name, snap, today):
    c = dict(cfg["formats"][fmt_name]["chart"])
    if c["kind"] == "rotate":
        k, ident, days = CHART_ROTATION[today.toordinal() % len(CHART_ROTATION)]
        c = {"kind": k, ("id" if k == "coin" else "symbol" if k == "index" else "series"): ident, "days": days}
    if c["kind"] == "movers":
        c = {"kind": "coin", "id": "bitcoin", "days": 1}
    if c["kind"] == "coin":
        pts = market.coin_history(cfg, c["id"], c["days"])
        name = snap["coins"][c["id"]]["name"] if c["id"] in snap.get("coins", {}) else c["id"].title()
        return pts, name, "{:,.0f}" if pts[-1][1] > 100 else "{:,.2f}", "CoinGecko", c["days"]
    if c["kind"] == "index":
        ih = market.index_history(cfg, c["symbol"], c["days"])
        return ih["points"], ih["name"], "{:,.0f}", ih["source"], c["days"]
    pts = market.fred_series(cfg, c["series"], c["days"])
    return pts, cfg["data"]["fred_series"].get(c["series"], c["series"]), "{:.2f}%", "FRED", c["days"]


def asof_label(pts, csrc):
    """On-screen as-of stamp: daily data -> 'CLOSE FRI OCT 23', intraday coins -> 'AS OF OCT 27, 10:05 UTC'."""
    last = str(pts[-1][0])
    if csrc == "CoinGecko" and "T" in last:
        t = dt.datetime.fromisoformat(last).astimezone(dt.timezone.utc)
        return f"AS OF {t:%b} {t.day}, {t:%H:%M} UTC".upper()
    d = dt.date.fromisoformat(last[:10])
    return f"CLOSE {d:%a %b} {d.day}".upper()


def period_words(days):
    return {1: "the past day", 5: "the past week", 7: "the past week", 30: "the past month", 60: "the past two months",
            90: "the past three months", 180: "the past six months", 365: "the past year"}.get(days, "recent weeks")


COIN_KEYS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "DOGE": "dogecoin"}
SPOKEN = {"BTC": "bitcoin", "ETH": "ether", "SOL": "solana", "XRP": "xrp", "DOGE": "dogecoin",
          "SPX": "s and p", "NDX": "nasdaq", "US10Y": "ten year"}


def badge_assets(cfg, snap, spoken):
    """Data for the asset badges (own design): only assets actually spoken in the script. All from APIs."""
    low = " " + re.sub(r"[^a-z0-9 ]", " ", spoken.lower()) + " "
    out = []
    for key, word in SPOKEN.items():
        if f" {word}" not in low and not (key == "ETH" and " ethereum " in low):
            continue
        try:
            if key in COIN_KEYS:
                c = snap.get("coins", {}).get(COIN_KEYS[key])
                if not c: continue
                pts = market.coin_history(cfg, COIN_KEYS[key], 1)
                t = dt.datetime.fromisoformat(str(c["asof"]).replace("Z", "+00:00")) if c.get("asof") else None
                out.append({"key": key, "ticker": c["symbol"], "name": c["name"], "price": script_gen.money(c["price"]),
                            "chg": c["chg_24h"], "period": "24h", "points": pts,
                            "asof": f"AS OF {t:%b} {t.day}, {t:%H:%M} UTC".upper() if t else ""})
            elif key in ("SPX", "NDX"):
                i = snap.get("indices", {}).get("^GSPC" if key == "SPX" else "^IXIC")
                if not i: continue
                ad = dt.date.fromisoformat(str(i["asof"])[:10])
                out.append({"key": key, "ticker": "S&P 500" if key == "SPX" else "NASDAQ", "name": "US stock index",
                            "price": f"{i['last']:,.0f}", "chg": i["chg_1d"], "period": "1 day", "points": i["points"][-15:],
                            "asof": f"CLOSE {ad:%a %b} {ad.day} · {i['source'].split(' (')[0]}".upper()})
            elif key == "US10Y":
                f = snap.get("fred", {}).get("DGS10")
                if not f: continue
                p = f["points"][-22:]; ch = p[-1][1] - p[0][1]
                ad = dt.date.fromisoformat(str(f["asof"])[:10])
                out.append({"key": key, "ticker": "US 10Y", "name": "10-year Treasury yield", "price": f"{f['last']:.2f}%",
                            "chg": ch * 10, "chg_txt": f"{abs(ch) * 100:.0f} BP", "period": "1 month", "points": p,
                            "asof": f"AS OF {ad:%b} {ad.day} · FRED".upper()})
        except Exception as e:
            print("badge", key, e, file=sys.stderr)
    return out


def news_topics(fmt_name, fmt):
    """Stock formats: stock/macro stories only. Crypto formats: crypto only. Config `news_topics` overrides."""
    if fmt.get("news_topics"):
        return list(fmt["news_topics"])
    kind = script_gen.kind_of(fmt_name, fmt)
    return ["crypto"] if kind in ("crypto", "movers") else ["macro"] if kind in ("macro", "calendar") else ["stocks", "macro"]


def segment_times(segs, words):
    """Map script segments to TTS word timestamps -> [{chapter, t0, t1}] (proportional if token counts differ)."""
    total = sum(len(t.split()) for _, t in segs); n = len(words); out = []; i = 0
    for ch, t in segs:
        k = len(t.split())
        a, b = round(i * n / total), max(round(i * n / total) + 1, round((i + k) * n / total))
        out.append({"chapter": ch, "t0": words[min(a, n - 1)]["s"], "t1": words[min(b, n) - 1]["e"], "text": t})
        i += k
    return out


def sample_story(items, pattern, topics):
    """TEST ONLY: build a story from feed items whose title matches `pattern` (may be single-source)."""
    hit = [i for i in items if re.search(pattern, i["title"], re.I) and i["feed_topic"] in topics]
    if not hit: raise SystemExit(f"--sample-story: no feed headline matches {pattern!r}")
    rep = hit[0]
    return {"id": "sample_" + hashlib.md5(rep["title"].encode()).hexdigest()[:8], "headline": rep["title"],
            "summary": rep["summary"], "sources": sorted({i["source"] for i in hit}), "owners": sorted({i["owner"] for i in hit}),
            "links": [i["link"] for i in hit][:5], "n_owners": len({i["owner"] for i in hit}), "topic_score": 0,
            "newest": max(i["ts"] for i in hit), "published": rep["ts"], "rep_source": rep["source"],
            "all_titles": [i["title"] for i in hit][:6]}


def run(cfg, fmt_name, slot_id="manual", dry_run=True, no_llm=False, sample=None, slot_date=None):
    if sample:
        dry_run = True; slot_id = slot_id + "_sample"
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    job_id = f"{(slot_date or now.date()):%Y%m%d}_{slot_id}_{fmt_name}"   # slot_date: catch-up after midnight keeps the slot's day
    jd = OUT / job_id; jd.mkdir(parents=True, exist_ok=True)
    log = {"job_id": job_id, "format": fmt_name, "started": now.isoformat()}
    fmt = cfg["formats"][fmt_name]
    if no_llm:
        cfg["llm"]["providers"] = ["template"]

    snap = market.snapshot(cfg)
    log["data_errors"] = snap["errors"]
    story = None
    if fmt.get("needs_story"):
        items, ferr = news.fetch_feeds(cfg)
        log["feed_errors"] = ferr; log["feed_items"] = len(items)
        cands = [s for s in news.verified_stories(cfg, news_topics(fmt_name, fmt), items) if not dedupe.story_used_recently(s)]
        log["news_topics"] = news_topics(fmt_name, fmt)
        log["verified_stories"] = [{k: s[k] for k in ("headline", "sources", "n_owners")} for s in cands[:5]]
        story = cands[0] if cands else None
        if sample:
            story = sample_story(items, sample, ["stocks", "macro"] if "crypto" not in news_topics(fmt_name, fmt) else ["crypto"])
            log["sample_story"] = "TEST ONLY - headline injected via --sample-story (not cross-verified); dry run forced, never upload"
    mood, mv = market.mood(snap, fmt["topic"])
    facts = script_gen.build_facts(snap, fmt_name, cfg)
    pts, cname, vfmt, csrc, cdays = pick_chart(cfg, fmt_name, snap, now.date())
    asof = asof_label(pts, csrc)
    chg = (pts[-1][1] / pts[0][1] - 1) * 100
    spot = next((v for v in snap.get("coins", {}).values() if v["name"] == cname), None) if csrc == "CoinGecko" else None
    if spot and cdays in (1, 7):  # keep the big number + signal identical to what the voice says
        chg = spot["chg_24h"] if cdays == 1 else spot["chg_7d"]
    _last = str(pts[-1][0])
    facts.update({"CHART_NAME": cname, "CHART_PERIOD": period_words(cdays),
                  "CHART_ASOF": ("the latest update" if csrc == "CoinGecko" and "T" in _last
                                 else market.asof_words(dt.date.fromisoformat(_last[:10]), market.session_state(now)["ny_date"])),
                  "CHART_LAST": (vfmt.format(pts[-1][1]) if "%" in vfmt else script_gen.money(spot["price"] if spot else pts[-1][1]) if csrc == "CoinGecko" else f"{pts[-1][1]:,.0f}"),
                  "CHART_MOVE": (f"{'up' if pts[-1][1] > pts[0][1] else 'down'} {abs(pts[-1][1]-pts[0][1])*100:.0f} basis points" if "%" in vfmt else script_gen.move(chg))})
    parts, provider, glog = script_gen.generate(cfg, fmt_name, facts, story, mood, seed=job_id)
    spoken, title, segs = script_gen.assemble(parts, facts, cfg["channel"]["catchphrase"], story)
    log.update({"script_provider": provider, "script_log": glog, "mood": mood, "mood_value": mv, "story": story and
                {k: story[k] for k in ("id", "headline", "sources", "links")}})
    (jd / "script.txt").write_text(spoken + "\n")
    (jd / "facts.json").write_text(json.dumps({"facts": facts, "snapshot_ts": snap["ts"]}, indent=1))

    # ---- duration budget: hard cap (video = voice + music tail) ----
    import soundfile as sf
    V = cfg["video"]; kk = cfg["tts"]["kokoro"]
    cap, tail_max, tail_min, safety = float(V.get("hard_max_seconds", 40.0)), float(V.get("tail", 0.8)), 0.3, 0.1
    sp_min, sp_max, wps = float(V.get("min_speed", 0.88)), float(V.get("max_speed", 1.2)), float(V.get("est_words_per_sec", 2.45))
    voice_budget = cap - tail_max - safety          # 39.1 s of voice -> <= 40.0 s video
    trims = []
    while len(spoken.split()) / (wps * min(float(V.get("comfortable_speed", 1.1)), kk["speed"] * 1.05)) > voice_budget:   # pre-TTS estimate
        ns = script_gen.trim_segments(segs)
        if ns is None: break
        segs = ns; spoken = script_gen.spoken_of(segs); trims.append("pre")

    # ---- TTS: adjust speed within limits, trim if still too long ----
    voice = str(jd / "voice.wav")
    t1 = time.time()
    words = tts.synthesize(cfg, spoken, voice)
    d = sf.info(voice).duration
    if d < V["min_seconds"] - 1:
        kk["speed"] = round(max(sp_min, kk["speed"] * d / V["min_seconds"]), 2)
        words = tts.synthesize(cfg, spoken, voice); d = sf.info(voice).duration
    sp_soft = float(V.get("comfortable_speed", 1.1))   # above this Gus sounds rushed -> trim the script first
    for _ in range(6):
        if d <= voice_budget:
            break
        need = kk["speed"] * d / (voice_budget - 0.4)
        ns = script_gen.trim_segments(segs) if need > sp_soft else None
        if ns is not None:
            segs = ns; spoken = script_gen.spoken_of(segs); trims.append(f"trim@{d:.2f}s")
        elif need > kk["speed"] + 0.005 and kk["speed"] < sp_max:
            kk["speed"] = round(min(sp_max, need), 2)
        else:
            break
        words = tts.synthesize(cfg, spoken, voice); d = sf.info(voice).duration
    tail = round(max(tail_min, min(tail_max, cap - safety - d)), 2)
    # validate BEFORE render: never render something over the cap
    if d + tail > cap:
        raise RuntimeError(f"duration check failed: voice {d:.2f}s + tail {tail}s > {cap}s even at speed {kk['speed']} after trims {trims}")
    log.update({"tts_speed": kk["speed"], "trims": trims, "planned_duration": round(d + tail, 2), "tail": tail})
    (jd / "script.txt").write_text(spoken + "\n")
    log["tts_seconds"] = round(time.time() - t1, 1); log["voice_duration"] = round(d, 2)
    (jd / "words.json").write_text(json.dumps(words))
    timeline = segment_times(segs, words)
    (jd / "timeline.json").write_text(json.dumps(timeline, indent=1))

    # ---- chart ----
    sub = f"{script_gen.move(chg).upper()} · {period_words(cdays).upper()}" if "%" not in vfmt else f"{facts['CHART_MOVE'].upper()} · {period_words(cdays).upper()}"
    chart.render_chart(pts, cname.upper(), sub, "green" if pts[-1][1] > pts[0][1] else "red" if pts[-1][1] < pts[0][1] else "neutral",
                       str(jd / "chart.png"), value_fmt=vfmt, source=f"Source: {csrc} · {asof.title().replace('Utc', 'UTC')}")
    log["chart_asof"] = asof

    # ---- render ----
    level = {"green": 0.86, "neutral": 0.72, "red": 0.56}[mood] + max(-0.04, min(0.04, mv * 0.01))
    c = snap.get("coins", {}); ix = snap.get("indices", {}); fr = snap.get("fred", {})
    ticker = []
    for cid in ("bitcoin", "ethereum", "solana"):
        if cid in c: ticker.append((c[cid]["symbol"], script_gen.money(c[cid]["price"]), c[cid]["chg_24h"]))
    for sym in ("^GSPC", "^IXIC"):
        if sym in ix: ticker.append((ix[sym]["name"], f"{ix[sym]['last']:,.0f}", ix[sym]["chg_1d"]))
    if "DGS10" in fr: ticker.append(("US 10Y", f"{fr['DGS10']['last']:.2f}%", None))
    sources = sorted({"CoinGecko"} | {i["source"] for i in ix.values()} | ({"FRED"} if fr else set()) | {csrc})
    # ---- motion-graphics data: asset badges, news card, person portrait (commercial-safe licenses only) ----
    assets = badge_assets(cfg, snap, spoken)
    person_card, person_name, credits = None, "", []
    if story and cfg.get("motion", {}).get("people", True):
        plog = []
        try:
            found = people.find_people(story)
            if found:
                pp = found[0]; person_name = pp["name"]
                por = people.portrait(pp, cutout=cfg.get("motion", {}).get("cutout", True), log=plog)
                person_card = motion.person_card(por, pp["name"], pp.get("desc", ""),
                                                 f"Photo: {por['meta']['artist'][:40]} · {por['meta']['license']} · Wikimedia Commons" if por else "")
                if por:
                    credits.append(por["meta"]["attribution"])
                log["person"] = {"name": pp["name"], "image": por and {k: por["meta"].get(k) for k in
                                 ("file_page", "license", "license_url", "artist", "modified")}, "silhouette": por is None}
        except Exception as e:
            plog.append(f"people: {e}")
        if plog: log["people_log"] = plog
    kind = script_gen.kind_of(fmt_name, fmt)
    out_mp4 = str(jd / f"{job_id}.mp4")
    t2 = time.time()
    dur = render.render_video({"words": words, "voice_wav": voice, "mood": mood, "level": level, "label": fmt["label"],
                               "channel": cfg["channel"]["name"], "date_str": now.strftime("%b %d, %Y"),
                               "chart_png": str(jd / "chart.png"), "ticker": ticker, "seed": int(hashlib.md5(job_id.encode()).hexdigest()[:6], 16),
                               "handle": cfg["channel"]["handle"], "timeline": timeline, "tail": tail,
                               "assets": assets, "news": story, "now": now, "person_card": person_card, "person_name": person_name,
                               "shake_pct": 3.0 if kind in ("crypto", "movers") else 1.0,
                               "big_value": facts["CHART_LAST"], "big_sub": sub, "big_label": f"{cname.upper()} · {asof}",
                               "big_dir": ((chg > 0.05) - (chg < -0.05)) if "%" not in vfmt else (pts[-1][1] > pts[0][1]) - (pts[-1][1] < pts[0][1]),
                               "proof": story and {"headline": story["headline"], "sources": story["sources"][:3]},
                               "source_line": f"{cfg['channel']['disclaimer']} · Data: {', '.join(sorted({s.split(' (')[0] for s in sources}))}"},
                              out_mp4, cfg)
    log["render_seconds"] = round(time.time() - t2, 1); log["video_duration"] = round(dur, 2)
    import subprocess
    probed = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out_mp4],
                                  capture_output=True, text=True).stdout.strip() or 0)
    log["probed_duration"] = round(probed, 3)
    if probed > cap + 0.02:
        raise RuntimeError(f"rendered file {probed:.3f}s exceeds the {cap}s cap")
    meta = metadata.build(cfg, fmt_name, title, spoken, story, sources, poll=parts.get("poll", ""), credits=credits)
    (jd / "metadata.json").write_text(json.dumps(meta, indent=1))
    log["video"] = out_mp4
    vh = dedupe.file_hash(out_mp4); log["sha256"] = vh

    # ---- approval + upload ----
    if not dry_run:
        approved = True
        tg_ready = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
        if cfg["approval"]["telegram"] and not tg_ready:
            log["approval"] = "skipped (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set) -> auto-publish"
        if cfg["approval"]["telegram"] and tg_ready:
            from finshorts.telegram_approval import request_approval
            approved = request_approval(out_mp4, f"{meta['youtube']['title']}\n\n{spoken}", job_id,
                                        cfg["approval"]["timeout_minutes"], cfg["approval"]["on_timeout"])
        log["approved"] = approved
        if approved:
            if cfg["upload"]["youtube"]["enabled"]:
                if dedupe.already_uploaded("youtube", vh, job_id):
                    log["youtube"] = "skipped (duplicate)"
                else:
                    from finshorts.upload_youtube import upload as yt_up
                    vid, priv = yt_up(out_mp4, meta["youtube"], cfg["upload"]["youtube"])
                    dedupe.record_upload("youtube", vh, job_id, vid); log["youtube"] = {"id": vid, "privacy": priv}
                    if priv and priv != cfg["upload"]["youtube"].get("privacy", "public"):
                        print(f"::warning title=YouTube privacy::uploaded as '{priv}' instead of "
                              f"'{cfg['upload']['youtube'].get('privacy')}' - unverified API project? (see docs/YOUTUBE_AUDIT.md)")
                    if story: dedupe.record_story(story)   # immediately, so a later crash can't cause a re-post
            if cfg["upload"]["instagram"]["enabled"]:
                if dedupe.already_uploaded("instagram", vh, job_id):
                    log["instagram"] = "skipped (duplicate)"
                else:
                    from finshorts.upload_instagram import upload as ig_up
                    icfg = dict(cfg["upload"]["instagram"]); icfg["hosting_provider"] = cfg["upload"]["hosting"]["provider"]
                    icfg["delete_after_publish"] = cfg["upload"]["hosting"]["delete_after_publish"]
                    mid = ig_up(out_mp4, meta["instagram"]["caption"], icfg)
                    dedupe.record_upload("instagram", vh, job_id, mid); log["instagram"] = {"id": mid}
            if story and any(isinstance(log.get(p), dict) for p in ("youtube", "instagram")):
                dedupe.record_story(story)
            from finshorts import community
            log["community"] = community.publish(cfg, meta, log)
    log["total_seconds"] = round(time.time() - t0, 1)
    (jd / "run_log.json").write_text(json.dumps(log, indent=1, default=str))
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format"); ap.add_argument("--slot"); ap.add_argument("--auto", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--slot-date", help="UTC date (YYYY-MM-DD) the slot belongs to (catch-up after midnight)")
    ap.add_argument("--sample-story", help="TEST ONLY: regex of a feed headline to force as the story (forces --dry-run)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    sd = dt.date.fromisoformat(a.slot_date) if a.slot_date else dt.datetime.now(dt.timezone.utc).date()
    if a.auto:
        s, why = due_slot(cfg, is_uploaded=lambda j: dedupe.slot_done(cfg, j), last_upload_ts=dedupe.last_upload_ts())
        print(why)
        if s:
            sd = dt.date.fromisoformat(s["day"])
        elif a.dry_run:   # manual dry run without a format: render the top-priority slot's format as a test
            s = effective_slot(cfg, cfg["schedule"]["slots"][0], sd) or \
                {**cfg["schedule"]["slots"][0], "format": "chart_of_the_day"}
            s = {**s, "id": "dry"}
            print(f"no slot due now -> dry-run test of format {s['format']}")
        if not s:
            return 0
        fmt, sid = s["format"], s["id"]
    elif a.slot:
        s = next(x for x in cfg["schedule"]["slots"] if x["id"] == a.slot)
        s = effective_slot(cfg, s, sd)   # weekend overrides (by the slot's own day)
        if not s:
            print(f"slot {a.slot} is skipped on {sd} (weekend override)"); return 0
        fmt, sid = s["format"], s["id"]
        jid = slot_job_id(s, sd)
        if not a.dry_run and dedupe.slot_done(cfg, jid):
            print(f"slot {jid} already uploaded -> nothing to do"); return 0
    else:
        fmt, sid = a.format or "crypto_update", "manual"
    log = run(cfg, fmt, sid, dry_run=a.dry_run or bool(a.sample_story), no_llm=a.no_llm, sample=a.sample_story,
              slot_date=sd if sid not in ("manual", "dry") else None)
    print(json.dumps(log, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
