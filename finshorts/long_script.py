"""Script + facts for the ~5 minute daily long-form video ("Gus Markets Daily Update").

Same contract as the Shorts: the LLM never writes a number. Every price / level / % / date is a {PLACEHOLDER}
whose value is derived from the API snapshot; the filled script is fact-checked (numbers, attribution, direction,
freshness) and every placeholder value is re-verified against the raw snapshot numbers (verify_facts).
"""
import re, json, time, random, datetime as dt
import requests
from . import script_gen as SG, factcheck
from .script_gen import money, move, pct, big, idx, fill, PH, NUM_WORDS, ALLOWED, FRESH_WORDS
from .market import session_state, asof_words
from .config import env

SECTIONS = ("hook", "overview", "crypto", "watch", "take")
WORD_RANGES = {"hook": (12, 32), "overview": (95, 165), "crypto": (60, 115), "watch": (40, 80), "take": (22, 50),
               "story_text": (35, 70), "story_why": (15, 38)}
TOTAL_RANGE = (540, 640)      # prompt asks 560-620; CI calibration: Gemini prose ~0.59 s/word at speed 1.0 incl. gaps
                              # -> 560-620 words = 4:50-5:30 at voice speed 1.00-1.08 (752 words gave 6:42 even at 1.12)
COUNT_OK = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b(?![\s-]*(percent|%|thousand|million|billion|"
                      r"trillion|hundred|basis|points?|dollars?|bucks|cents?|x\b|times))", re.I)   # counting words ("three stories"), not data
OUTRO = ("That's the Gus Markets Daily Update. Sources are in the description, and none of this is financial advice. "
         "If Gus saved you some time, subscribe, and I'll see you tomorrow.")
OUTRO_WEEKEND = ("That's the Gus Markets Weekend Update. Sources are in the description, and none of this is financial advice. "
                 "If Gus saved you some time, subscribe, and I'll see you next time.")


# ---------------- facts ----------------
def _move_n(pts, n):
    if len(pts) <= n: return None
    return (pts[-1][1] / pts[-1 - n][1] - 1) * 100


def build_long_facts(snap, cfg, now_utc=None, weekend=False):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    f = SG.build_facts(snap, "daily_update", cfg, now_utc)
    raw = {}
    sess = session_state(now_utc); ref = sess["ny_date"]
    ind = snap.get("indices", {})
    for sym, key in (("^GSPC", "SPX"), ("^IXIC", "NDX"), ("^DJI", "DOW")):
        i = ind.get(sym)
        if not i: continue
        if key == "DOW":
            f["DOW_LEVEL"] = idx(i["last"]); f["DOW_MOVE"] = move(i["chg_1d"])
            ad = dt.date.fromisoformat(str(i["asof"])[:10]); f["DOW_ASOF_CLOSE"] = asof_words(ad, ref)
        raw[f"{key}_LEVEL"] = i["last"]; raw[f"{key}_MOVE"] = i["chg_1d"]
        w = _move_n(i["points"], 5)
        if w is not None:
            f[f"{key}_MOVE_1W"] = move(w); raw[f"{key}_MOVE_1W"] = w
    g = snap.get("gold")
    if g:
        f["GOLD_PRICE"] = money(g["last"]); f["GOLD_MOVE"] = move(g["chg_1d"])
        f["GOLD_ASOF_CLOSE"] = asof_words(dt.date.fromisoformat(g["asof"]), ref)
        raw["GOLD_PRICE"] = g["last"]; raw["GOLD_MOVE"] = g["chg_1d"]
        w = _move_n(g["points"], 5)
        if w is not None: f["GOLD_MOVE_1W"] = move(w); raw["GOLD_MOVE_1W"] = w
    fr = snap.get("fred", {})
    if "DCOILWTICO" in fr:
        o = fr["DCOILWTICO"]
        f["OIL_PRICE"] = f"${o['last']:,.2f}"; f["OIL_MOVE"] = move(o["chg_1d"])
        f["OIL_ASOF_CLOSE"] = asof_words(dt.date.fromisoformat(o["asof"]), ref)
        raw["OIL_PRICE"] = o["last"]; raw["OIL_MOVE"] = o["chg_1d"]
    if "DTWEXBGS" in fr:
        u = fr["DTWEXBGS"]
        f["USD_INDEX"] = f"{u['last']:.1f}"; f["USD_MOVE_1M"] = move(u["chg_1m_pct"])
        f["USD_ASOF_CLOSE"] = asof_words(dt.date.fromisoformat(u["asof"]), ref)
        raw["USD_INDEX"] = u["last"]; raw["USD_MOVE_1M"] = u["chg_1m_pct"]
    if "DGS2" in fr:
        f["US2Y"] = f"{fr['DGS2']['last']:.2f}%"; raw["US2Y"] = fr["DGS2"]["last"]
    if "DGS10" in fr: raw["US10Y"] = fr["DGS10"]["last"]; raw["US10Y_MOVE_1M"] = fr["DGS10"]["chg_1m"] * 100
    if "DFF" in fr: raw["FED_FUNDS"] = fr["DFF"]["last"]
    c = snap.get("coins", {})
    for cid, key in (("bitcoin", "BTC"), ("ethereum", "ETH")):
        if cid in c:
            raw[f"{key}_PRICE"] = c[cid]["price"]; raw[f"{key}_MOVE"] = c[cid]["chg_24h"]; raw[f"{key}_MOVE_7D"] = c[cid]["chg_7d"]
        h = snap.get("hist", {}).get(f"{cid}30")
        if h and cid in c and len(h) > 10:
            m30 = (c[cid]["price"] / h[0][1] - 1) * 100
            f[f"{key}_MOVE_30D"] = move(m30); raw[f"{key}_MOVE_30D"] = m30
    gl = snap.get("global")
    if gl:
        raw["CRYPTO_MCAP"] = gl["total_mcap_usd"]; raw["BTC_DOMINANCE"] = gl["btc_dominance"]; raw["CRYPTO_MCAP_MOVE"] = gl["mcap_chg_24h"]
    m = snap.get("movers")
    if m:
        for i, x in enumerate(m["gainers"][:2], 1): raw[f"GAINER{i}_MOVE"] = x["chg_24h"]
        for i, x in enumerate(m["losers"][:2], 1): raw[f"LOSER{i}_MOVE"] = x["chg_24h"]
    fo = snap.get("fomc")
    if fo:
        f["FOMC_DATE"] = fo["words"]; f["FOMC_DAY"] = fo["weekday"]; raw["FOMC_DATE"] = fo["date"]
    # flags
    f["_WEEKEND"] = "1" if weekend else ""
    return f, raw


def verify_facts(facts, raw):
    """Independent check: every placeholder value must equal its raw API number within rounding tolerance,
    and the direction word (up/down/flat) must match the sign of the raw move."""
    bad = []
    for k, v in raw.items():
        if k not in facts: continue
        s = str(facts[k])
        if k == "FOMC_DATE":
            d = dt.date.fromisoformat(v)
            if s != f"{SG_MONTH(d)} {d.day}": bad.append(f"{k}={s!r} vs {v}")
            continue
        if k == "US10Y_MOVE_1M":
            num = 0.0 if s == "flat" else float(re.findall(r"[\d.]+", s)[0]) * (1 if s.startswith("up") else -1)
            if abs(num - v) > 0.51 and not (s == "flat" and abs(v) < 1): bad.append(f"{k}={s!r} vs raw {v:.2f}bp")
            continue
        nums = re.findall(r"[\d,]*\.?\d+", s)
        if not nums and s != "flat": bad.append(f"{k}: no number in {s!r}"); continue
        nums = nums or ["0"]
        num = float(nums[0].replace(",", ""))
        if "trillion" in s: num *= 1e12
        elif "billion" in s: num *= 1e9
        elif "million" in s: num *= 1e6
        if "MOVE" in k:
            if s == "flat":
                if abs(v) >= 0.05: bad.append(f"{k}='flat' but raw {v:+.3f}%")
                continue
            sign = 1 if s.startswith("up") else -1
            dec = len(nums[0].split(".")[1]) if "." in nums[0] else 0
            if (v > 0) != (sign > 0) or abs(num * sign - v) > 0.5 * 10 ** -dec + 1e-6:
                bad.append(f"{k}={s!r} vs raw {v:+.3f}%")
            continue
        tol = max(abs(v) * 0.006, 0.006) if abs(v) < 1e6 else abs(v) * 0.006   # money rounds to 3 significant digits
        if abs(num - v) > tol:
            bad.append(f"{k}={s!r} vs raw {v}")
    return (not bad), bad


def SG_MONTH(d):
    return d.strftime("%B")


# ---------------- prompt ----------------
SYSTEM_LONG = """You write the script for "Gus Markets Daily Update", a ~5-minute English YouTube market-news video hosted by Gus,
a deadpan cartoon goldfish in a navy necktie. Gus is a cartoon: never claim to be human, an expert or a licensed adviser.
Style: calm, precise, premium newsreader; neutral and factual; dry humor only in the takeaway. Written for the ear (a TTS
voice reads it): short sentences, plain words, no lists, no symbols, no abbreviations, no emojis, no stage directions.
Write "the S and P five hundred", "the Nasdaq", "the Dow", "Bitcoin", "Ether", "the ten-year yield", "the two-year yield".

HARD RULES
1. Never write a digit or a number word. Every price, level, percentage, amount and date is one of the given {PLACEHOLDERS},
   copied exactly with braces. Only use placeholders from the list. Each placeholder must be in a sentence that names its
   asset (e.g. "Gold closed at {GOLD_PRICE}"). Move placeholders already contain the direction ("up 1.2%"), so use neutral
   verbs before them ("finished {SPX_MOVE}", "is {BTC_MOVE} over the day") - never "rose {X_MOVE}" or "fell {X_MOVE}".
2. Facts: use only the given data and the given verified stories. No invented causes, quotes, numbers, forecasts, price
   targets or predictions. Never say one market move caused another unless a story says so; use "alongside" or "while".
   No buy, sell or hold advice. Stories are paraphrased neutrally and attributed ("according to reports").
3. Time words: stock, gold, oil, dollar and yield data have their own as-of placeholders (e.g. {OIL_ASOF_CLOSE}); use them
   when the data is not from today. TIME_RULE
4. Output ONLY a JSON object with these keys:
 "hook": 18-25 words, the cold open (~10 seconds). First sentence max 9 words: the single most striking fact of the day,
         as a punchy question or statement. Then tease what is coming (the stories, crypto, what to watch). No greeting.
 "overview": 115-140 words. Walk through, in this order: the S and P five hundred, the Nasdaq, the Dow, Bitcoin, Ether,
         gold, oil, the dollar and the ten-year yield, each with its placeholders; end with one line on the overall picture.
 "stories": array with exactly N_STORIES objects, same order as the STORIES given, each
         {"kicker": "2-4 word label, Title Case, no numbers", "text": "45-60 words: what happened, who, neutral, attributed",
          "why": "20-30 words: why it matters for markets or everyday investors, general mechanism only, no new facts"}
 "crypto": 75-95 words crypto deep dive: Bitcoin and Ether today and over the month, total crypto market value and
         Bitcoin dominance, the biggest large-cap gainer and loser.
 "watch": 50-65 words: what to watch next. Use the calendar placeholders if given and the next Fed meeting
         ({FOMC_DATE}); the rate backdrop (ten-year, two-year, fed funds). Never invent events or dates.
 "take": 30-40 words. Starts with "My takeaway:". Gus's calm big-picture view, dry humor allowed, no advice.
 "title": max 65 characters, specific and curiosity-driven, may use placeholders, no clickbait lies, no emojis, no hashtags.
 "thumb": 2-4 WORDS for the thumbnail, ALL CAPS, no numbers (e.g. "BITCOIN SLIPS AGAIN").
 "summary": one sentence (max 30 words) for the video description; placeholders allowed.
Total spoken words (hook + overview + stories + crypto + watch + take) after filling: 560-620 (about five minutes of voice; the video must stay between 4:30 and 5:30).
Count carefully: longer answers are rejected."""

WEEKEND_NOTE = """WEEKEND EDITION: US markets are closed. The "overview" is a WEEK IN REVIEW: use the weekly placeholders
(*_MOVE_1W) for the S and P five hundred, the Nasdaq, the Dow and gold, with their as-of close placeholders, plus Bitcoin and
Ether over the week ({BTC_MOVE_7D}, {ETH_MOVE_7D}) and the day. "watch" = the week ahead."""


def _time_rule(facts):
    if facts.get("_STOCKS_TODAY_OK"):
        return "Stock numbers ARE today's completed session, so 'today' is fine for stocks."
    return ("Stock numbers are as of {SPX_ASOF_CLOSE}: attribute them with that placeholder, and never use today, tonight, "
            "right now, currently, live or so far anywhere except in the crypto section (crypto trades around the clock).")


def user_prompt(facts, stories, mood, weekend, recent_titles):
    ph = {k: v for k, v in facts.items() if not k.startswith("_")}
    st = [{"n": i + 1, "headline": s["headline"], "summary": (s.get("summary") or "")[:400], "outlets": s["sources"][:4],
           "other_headlines": s.get("all_titles", [])[:4]} for i, s in enumerate(stories)]
    u = (f"Market mood: {mood}. Edition: {'weekend' if weekend else 'weekday daily'}.\n"
         f"PLACEHOLDERS (name -> value; use the name in braces):\n{json.dumps(ph, indent=0)}\n\n"
         f"STORIES (verified by 2+ independent outlets; N_STORIES = {len(stories)}):\n{json.dumps(st, indent=0)}\n\n")
    if recent_titles:
        u += "Do not reuse these recent video titles or their wording: " + json.dumps(recent_titles[-6:]) + "\n"
    return u


def _sys(facts, n, weekend):
    s = SYSTEM_LONG.replace("TIME_RULE", _time_rule(facts)).replace("N_STORIES", str(n))
    return s + ("\n\n" + WEEKEND_NOTE if weekend else "")


def _llm(cfg, model, sys, user):
    key = env("GEMINI_API_KEY")
    if not key: raise RuntimeError("no GEMINI_API_KEY")
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=180,
                      headers={"x-goog-api-key": key},
                      json={"systemInstruction": {"parts": [{"text": sys}]}, "contents": [{"parts": [{"text": user}]}],
                            "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"}})
    r.raise_for_status()
    return SG._parse(r.json()["candidates"][0]["content"]["parts"][0]["text"])


# ---------------- validation ----------------
def spoken_parts(parts):
    """Ordered (section_id, text) of everything the voice reads (outro excluded)."""
    out = [("hook", parts["hook"]), ("overview", parts["overview"])]
    for i, s in enumerate(parts.get("stories", [])):
        out += [(f"story{i}", s["text"]), (f"why{i}", s["why"])]
    out += [("crypto", parts["crypto"]), ("watch", parts["watch"]), ("take", parts["take"])]
    return out


def validate_long(parts, facts, n_stories):
    if len(parts.get("stories", [])) != n_stories:
        return False, [f"expected {n_stories} stories, got {len(parts.get('stories', []))}"]
    bad = []
    texts = dict(spoken_parts(parts)); texts["title"] = parts["title"]; texts["summary"] = parts.get("summary", "")
    texts["thumb"] = parts.get("thumb", "")
    for i, s in enumerate(parts["stories"]): texts[f"kicker{i}"] = s.get("kicker", "")
    for k, t in texts.items():
        for ph in PH.findall(t):
            if ph not in facts or ph.startswith("_"): bad.append(f"unknown placeholder {ph} in {k}")
        stripped = ALLOWED.sub("", PH.sub("", t))
        if re.search(r"\d", stripped): bad.append(f"digit outside placeholder in {k}: {stripped[:60]!r}")
        m = re.search(NUM_WORDS, COUNT_OK.sub("", stripped), re.I) if k not in ("title",) else None
        if m:
            ctx = COUNT_OK.sub("", stripped); j = m.start()
            bad.append(f"number word '{m.group(0)}' outside placeholder in {k} (\"...{ctx[max(0, j - 40):j + 30]}...\"): "
                       "use a {PLACEHOLDER} or rephrase without the number")
    if PH.search(parts.get("thumb", "")) or PH.search("".join(s.get("kicker", "") for s in parts["stories"])):
        bad.append("thumb / kicker must not contain placeholders")
    for k, (lo, hi) in WORD_RANGES.items():
        if k.startswith("story_"):
            for i, s in enumerate(parts["stories"]):
                n = len(fill(s["text" if k == "story_text" else "why"], facts).split())
                if not lo <= n <= hi: bad.append(f"story {i} {k[6:]}: {n} words not in [{lo},{hi}]")
        else:
            n = len(fill(parts[k], facts).split())
            if not lo <= n <= hi: bad.append(f"{k}: {n} words not in [{lo},{hi}]")
    total = sum(len(fill(t, facts).split()) for _, t in spoken_parts(parts))
    if not TOTAL_RANGE[0] <= total <= TOTAL_RANGE[1]: bad.append(f"total {total} words not in {TOTAL_RANGE}")
    if len(fill(parts["title"], facts)) > 80: bad.append("title too long")
    if len(parts["hook"].split(".")[0].split()) > 12: bad.append("hook first sentence too long")
    if not parts["take"].lower().startswith("my takeaway"): bad.append("take must start with 'My takeaway:'")
    # freshness: only the crypto section may say "today / right now" when stock data is not today's
    if not facts.get("_STOCKS_TODAY_OK"):
        for k, t in texts.items():
            if k in ("crypto", "summary") or k.startswith("kicker"): continue
            m = FRESH_WORDS.search(t)
            if m: bad.append(f"freshness word {m.group(0)!r} in {k} (stock data as of {facts.get('SPX_ASOF_CLOSE')})")
    # fact check (numbers / attribution / direction) per section
    for k, t in texts.items():
        if k.startswith("kicker") or k == "thumb": continue
        ok, why = factcheck.check_script({"title" if k == "title" else "body": t}, facts)
        if not ok: bad += [f"{k}: {w}" for w in why]
    return (not bad), bad


# ---------------- template fallback ----------------
def _sent(*xs): return " ".join(x for x in xs if x)


def _absmv(facts, k):
    m = re.findall(r"[\d.]+", facts.get(k, "") or "")
    return float(m[0]) if m else 0.0


def template_long(facts, stories, mood, weekend, rnd):
    F = lambda k: k in facts
    asof = "{SPX_ASOF_CLOSE}" if F("SPX_ASOF_CLOSE") else "the latest close"
    spx_up = facts.get("SPX_MOVE", "").startswith("up")
    if weekend:
        hook = _sent("Bitcoin is {BTC_MOVE_7D} this week." if F("BTC_MOVE_7D") else "Here is your week in markets.",
                     "Here is the week in review, the stories that moved it, a crypto deep dive, and what to watch next.")
    elif F("SPX_MOVE"):
        hook = _sent("The S and P five hundred finished {SPX_MOVE}.",
                     "Here is what moved markets, the stories behind it, a crypto deep dive, and what to watch next.")
    else:
        hook = "Bitcoin is {BTC_MOVE} over the day. Here are the markets, the stories, crypto, and what to watch next."
    ov = []
    if weekend and F("SPX_MOVE_1W"):
        ov.append(f"Let's start with the week. At {asof}, the S and P five hundred stood at {{SPX_LEVEL}}, {{SPX_MOVE_1W}} for the week.")
        if F("NDX_MOVE_1W"): ov.append("The Nasdaq closed at {NDX_LEVEL}, {NDX_MOVE_1W} over the week.")
        if F("DOW_MOVE_1W"): ov.append("The Dow ended at {DOW_LEVEL}, {DOW_MOVE_1W} for the week.")
    elif F("SPX_LEVEL"):
        ov.append(f"Let's start with stocks. At {asof}, the S and P five hundred stood at {{SPX_LEVEL}}, {{SPX_MOVE}} on the day.")
        if F("NDX_LEVEL"): ov.append("The tech-heavy Nasdaq finished at {NDX_LEVEL}, {NDX_MOVE}.")
        if F("DOW_LEVEL"): ov.append("And the Dow, the old blue-chip index, ended at {DOW_LEVEL}, {DOW_MOVE}.")
        same = facts.get("NDX_MOVE", "")[:2] == facts.get("SPX_MOVE", "")[:2]
        ov.append(("Tech and the broad market moved in the same direction, " + ("with tech doing more of the moving." if _absmv(facts, "NDX_MOVE") > _absmv(facts, "SPX_MOVE") else "with the broad market leading."))
                  if same else "Tech and the broad market went separate ways, which usually means investors are rotating rather than running.")
        if F("SPX_MOVE_1W"): ov.append("Zooming out to the week, the S and P five hundred is {SPX_MOVE_1W}.")
    if F("BTC_PRICE"):
        ov.append("In crypto, Bitcoin trades at {BTC_PRICE}, {BTC_MOVE} over the past day." if not weekend else
                  "In crypto, Bitcoin trades at {BTC_PRICE}, {BTC_MOVE_7D} over the week.")
    if F("ETH_PRICE"): ov.append("Ether sits at {ETH_PRICE}, {ETH_MOVE}.")
    if F("GOLD_PRICE"): ov.append("Gold, the classic safe haven, closed at {GOLD_PRICE} an ounce at {GOLD_ASOF_CLOSE}, {GOLD_MOVE}.")
    if F("OIL_PRICE"): ov.append("Oil, measured by the West Texas benchmark, was at {OIL_PRICE} a barrel at {OIL_ASOF_CLOSE}, {OIL_MOVE} on that day.")
    if F("USD_INDEX"): ov.append("The Fed's broad dollar index stood at {USD_INDEX} at {USD_ASOF_CLOSE}, {USD_MOVE_1M} over the month.")
    if F("US10Y"): ov.append("And the ten-year Treasury yield, the price of long-term money, is at {US10Y}, {US10Y_MOVE_1M} over the past month.")
    ov.append(rnd.choice(["Put together, it was a day of mixed signals rather than one clear story.",
                          "So the big picture: plenty of motion, and not much of it in a straight line.",
                          "Taken together, the tape tells a story of caution more than conviction."]))
    st = []
    for i, s in enumerate(stories):
        hl = s["headline"].rstrip(".")
        st.append({"kicker": ["Top Story", "Also Moving", "On The Radar", "Crypto Watch"][min(i, 3)],
                   "text": _sent(f"{['First', 'Next', 'Also', 'And finally'][min(i, 3)]}, the headline: {hl}.",
                                 f"The story was reported by {' and '.join(s['sources'][:2])}" + (", among others." if len(s["sources"]) > 2 else "."),
                                 "We only cover stories that several independent newsrooms have reported.",
                                 "The details are still developing, so treat the first reports with care, and read the original coverage linked in the description."),
                   "why": [
                       "Why it matters: stories like this can shift what investors expect, and in markets, expectations are what prices actually trade on, long before the final numbers arrive.",
                       "Why it matters: when a story changes the outlook for company profits or for interest rates, prices tend to adjust quickly, and that adjustment can spill into other markets.",
                       "Why it matters: headlines like this often move sentiment first, and the fundamentals catch up later, so the second reaction is usually more informative than the first.",
                       "Why it matters: crypto prices react fast to news about rules, flows and big holders, and those reactions often spill over into the wider risk mood."][min(i, 3) if i < 3 or "crypto" not in hl.lower() else 3]})
    cr = []
    if F("BTC_PRICE"):
        cr.append("Now the crypto deep dive. Bitcoin is at {BTC_PRICE}, {BTC_MOVE} over the past day.")
        if F("BTC_MOVE_30D"): cr.append("Over the past month, Bitcoin is {BTC_MOVE_30D}.")
        if F("BTC_MOVE_7D"): cr.append("Over the week, Bitcoin is {BTC_MOVE_7D}.")
    if F("ETH_PRICE"):
        cr.append("Ether trades at {ETH_PRICE}, {ETH_MOVE} on the day.")
        if F("ETH_MOVE_30D"): cr.append("Over the month, Ether is {ETH_MOVE_30D}.")
    if F("CRYPTO_MCAP"): cr.append("The total crypto market is worth {CRYPTO_MCAP}, {CRYPTO_MCAP_MOVE} in a day.")
    if F("BTC_DOMINANCE"): cr.append("Bitcoin's share of that, its dominance, is {BTC_DOMINANCE}.")
    if F("GAINER1_NAME"): cr.append("Among the largest coins, the top gainer is {GAINER1_NAME}, {GAINER1_MOVE}.")
    if F("LOSER1_NAME"): cr.append("The biggest laggard is {LOSER1_NAME}, {LOSER1_MOVE}.")
    if F("LOSER1_NAME") and F("GAINER1_NAME"):
        cr.append("When even the best performer barely moves, the market is telling you the selling was broad." if facts.get("GAINER1_MOVE", "").startswith(("flat", "down")) or _absmv(facts, "GAINER1_MOVE") < 1
                  else "Gains and losses were spread unevenly, which is typical when traders pick favorites rather than buy everything.")
    cr.append("Crypto trades around the clock, every day of the week, so these numbers can change quickly, and volatility tends to rise when liquidity is thin.")
    wa = ["Now, what to watch next."]
    for n in (1, 2, 3):
        if F(f"CAL{n}_NAME"): wa.append(f"On {{CAL{n}_DAY}}, we get {{CAL{n}_NAME}}.")
    if F("FOMC_DATE"): wa.append("The next Federal Reserve rate decision is due on {FOMC_DAY}, {FOMC_DATE}.")
    if F("FED_FUNDS"): wa.append("Until then, the Fed funds rate sits at {FED_FUNDS}.")
    if F("US2Y"): wa.append("The two-year Treasury yield, which tracks rate expectations, is at {US2Y}.")
    if F("US10Y"): wa.append("And the ten-year yield, which feeds into mortgages and company borrowing, is at {US10Y}.")
    wa.append("Those yields are the backdrop for everything else on this screen: when they climb, borrowing gets more expensive and riskier assets usually feel it.")
    take = rnd.choice([
        "My takeaway: markets are a weather report, not a prophecy. Note the moves, check the reasons behind them, and keep your plan boring. Boring plans survive. Exciting plans make great stories, mostly for other people.",
        "My takeaway: the noise is loud and the trend is quiet. I watch the quiet part, because it has a much better memory than I do. And I am a goldfish, so that is saying something.",
        "My takeaway: one day is a data point, not a destiny. Zoom out, keep your bowl clean, and let the headlines swim past. The fish who chases every bubble mostly gets tired."])
    parts = {"hook": hook, "overview": " ".join(ov), "stories": st, "crypto": " ".join(cr), "watch": " ".join(wa), "take": take,
             "title": ("Week In Review: Stocks, Crypto & What's Next" if weekend else
                       ("S&P 500 {SPX_MOVE}: Daily Market Update" if F("SPX_MOVE") else "Daily Market Update: Stocks, Crypto & Rates")),
             "thumb": ("WEEK IN REVIEW" if weekend else ("STOCKS UP" if spx_up else "STOCKS SLIP") if F("SPX_MOVE") else "DAILY UPDATE"),
             "summary": "Stocks, crypto, gold, oil, the dollar and yields, the top verified stories, and what to watch next."}
    return parts


# ---------------- generate ----------------
def generate_long(cfg, facts, stories, mood, weekend, seed, recent_titles=(), allow_llm=True):
    lc = cfg.get("long", {})
    log = []
    sysp = _sys(facts, len(stories), weekend); up = user_prompt(facts, stories, mood, weekend, list(recent_titles))
    if allow_llm and env("GEMINI_API_KEY"):
        for model in lc.get("llm_models", ["gemini-flash-latest", "gemini-flash-lite-latest"]):
            fb = ""
            for attempt in range(int(lc.get("llm_retries", 3))):
                try:
                    p = _llm(cfg, model, sysp, up + fb)
                    parts = {k: str(p.get(k, "")).strip() for k in ("hook", "overview", "crypto", "watch", "take", "title", "thumb", "summary")}
                    parts["stories"] = [{"kicker": str(s.get("kicker", "")).strip(), "text": str(s.get("text", "")).strip(),
                                         "why": str(s.get("why", "")).strip()} for s in (p.get("stories") or [])][:len(stories)]
                    parts["thumb"] = parts["thumb"].upper()
                    st_ = parts.pop("stories"); parts = SG.normalize(parts, facts)
                    parts["stories"] = [SG.normalize(x, facts) for x in st_]
                    try:
                        ok, why = validate_long(parts, facts, len(stories))
                    except KeyError as ke:          # unknown placeholder (e.g. gold data missing) -> retry with feedback
                        ok, why = False, [f"unknown placeholder {{{ke.args[0]}}}: that data is not available today, do not use it"]
                    log.append(f"{model}#{attempt}: " + ("ok, fact-check passed" if ok else "; ".join(why)[:400]))
                    if ok:
                        return parts, model, log, list(range(len(stories)))
                    fb = "\n\nYOUR PREVIOUS ANSWER WAS REJECTED. Fix exactly these problems and return the full JSON again:\n- " + "\n- ".join(why[:12])
                    tot = sum(len(fill(t, facts).split()) for _, t in spoken_parts(parts))
                    if tot > TOTAL_RANGE[1]:
                        fb += f"\n- LENGTH: you wrote {tot} spoken words; the limit is 560-620. CUT about {tot - 590} words (shorten overview, crypto and story texts)."
                    elif tot < TOTAL_RANGE[0]:
                        fb += f"\n- LENGTH: you wrote {tot} spoken words; the target is 560-620. ADD about {590 - tot} words."
                except Exception as e:
                    code = getattr(getattr(e, "response", None), "status_code", None)
                    log.append(f"{model}#{attempt}: {type(e).__name__} {code or ''} {str(e)[:100]}".replace(env("GEMINI_API_KEY") or "\0", "***"))
                    if code not in (429, 500, 502, 503, 504) and not isinstance(e, (requests.Timeout, requests.ConnectionError)):
                        break
                    time.sleep(min(40, 10 * (attempt + 1)))
    else:
        log.append("llm: skipped (no GEMINI_API_KEY or --no-llm)")
    rnd = random.Random(seed)
    parts = template_long(facts, stories, mood, weekend, rnd)
    # template stories with digits in the headline -> drop that story (numbers only from APIs)
    keep = [i for i, s in enumerate(parts["stories"]) if not re.search(r"\d", ALLOWED.sub("", s["text"]))
            and not re.search(NUM_WORDS, ALLOWED.sub("", s["text"]), re.I)]
    parts["stories"] = [parts["stories"][i] for i in keep]
    ok, why = validate_long(parts, facts, len(parts["stories"]))
    hard = [w for w in why if "words not in" not in w]          # template may be shorter; never wrong
    if hard:
        raise RuntimeError("template fact-check failed: " + "; ".join(hard))
    log.append("template: " + ("ok" if ok else "length notes: " + "; ".join(why)))
    return parts, "template", log, keep
