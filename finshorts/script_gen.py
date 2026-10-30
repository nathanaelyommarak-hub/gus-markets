"""Script generation. Numbers ONLY via placeholders filled from API facts; LLM text is validated."""
import re, json, random, hashlib, time, datetime as dt
import requests
from .config import env
from .market import session_state, asof_words
from . import factcheck

NUM_WORDS = r"\b(percent|percentage points?|thousand|million|billion|trillion|hundred|dozen|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b"
PH = re.compile(r"\{([A-Z0-9_]+)\}")
# names that contain numbers but are not data points
ALLOWED = re.compile(r"S&P 500|S and P five hundred|Nasdaq 100|ten[- ]year|two[- ]year|thirty[- ]year|Dow Jones|Web3|24/7|\bone\b", re.I)


# ---------------- number formatting (what the voice will read) ----------------
def money(v):
    if v >= 10000: return f"${round(v, -2):,.0f}"
    if v >= 1000: return f"${round(v, -1):,.0f}"
    if v >= 10: return f"${v:,.0f}"
    if v >= 1: return f"${v:,.2f}"
    return f"${v:.3f}"

def pct(v):
    a = abs(v)
    return f"{a:.0f}%" if a >= 10 else f"{a:.1f}%"

def move(v):
    if abs(v) < 0.05: return "flat"
    return ("up " if v > 0 else "down ") + pct(v)

def big(v):
    for d, w in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if v >= d: return f"${v/d:.2f} {w}".replace(".00 ", " ")
    return money(v)

def idx(v): return f"{v:,.0f}"


def build_facts(snap, fmt_name, cfg, now_utc=None):
    """Facts = {PLACEHOLDER: spoken string}. Keys starting with '_' are flags, never placeholders."""
    f = {}
    sess = session_state(now_utc)
    c = snap.get("coins", {})
    for cid, key in (("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"), ("ripple", "XRP"), ("dogecoin", "DOGE")):
        if cid in c:
            f[f"{key}_PRICE"] = money(c[cid]["price"]); f[f"{key}_MOVE"] = move(c[cid]["chg_24h"])
            f[f"{key}_MOVE_7D"] = move(c[cid]["chg_7d"])
    g = snap.get("global")
    if g:
        f["CRYPTO_MCAP"] = big(g["total_mcap_usd"]); f["BTC_DOMINANCE"] = pct(g["btc_dominance"])
        f["CRYPTO_MCAP_MOVE"] = move(g["mcap_chg_24h"])
    for sym, key in (("^GSPC", "SPX"), ("^IXIC", "NDX")):
        i = snap.get("indices", {}).get(sym)
        if i:
            f[f"{key}_LEVEL"] = idx(i["last"]); f[f"{key}_MOVE"] = move(i["chg_1d"])
            ad = dt.date.fromisoformat(str(i["asof"])[:10])
            f[f"{key}_ASOF"] = ad.strftime("%A")
            f[f"{key}_ASOF_CLOSE"] = asof_words(ad, sess["ny_date"])
            if key == "SPX":
                # "today" only if the data IS today's completed session; stale = misses a completed session
                f["_STOCKS_TODAY_OK"] = "1" if (ad == sess["ny_date"] and sess["today_complete"]) else ""
                f["_STOCKS_STALE"] = "1" if ad < sess["last_completed"] else ""
                f["_STOCKS_ASOF_DATE"] = ad.isoformat()
    fr = snap.get("fred", {})
    if "DGS10" in fr:
        f["US10Y"] = f"{fr['DGS10']['last']:.2f}%"
        f["US10Y_ASOF_CLOSE"] = asof_words(dt.date.fromisoformat(str(fr["DGS10"]["asof"])[:10]), sess["ny_date"])
        d = fr["DGS10"]["chg_1m"] * 100
        f["US10Y_MOVE_1M"] = "flat" if abs(d) < 1 else f"{'up' if d > 0 else 'down'} {abs(d):.0f} basis points"
    if "DFF" in fr: f["FED_FUNDS"] = f"{fr['DFF']['last']:.2f}%"
    for n, (day, name) in enumerate(snap.get("calendar", [])[:3], 1):
        f[f"CAL{n}_DAY"] = day; f[f"CAL{n}_NAME"] = name
    m = snap.get("movers")
    if m:
        for i, x in enumerate(m["gainers"][:2], 1):
            f[f"GAINER{i}_NAME"] = x["name"]; f[f"GAINER{i}_MOVE"] = move(x["chg_24h"])
        for i, x in enumerate(m["losers"][:2], 1):
            f[f"LOSER{i}_NAME"] = x["name"]; f[f"LOSER{i}_MOVE"] = move(x["chg_24h"])
    return f


def fill(text, facts):
    return PH.sub(lambda m: facts[m.group(1)], text)


FRESH_WORDS = re.compile(r"\b(today|today's|tonight|right now|this morning|this afternoon|this evening|so far|"
                         r"just (?:closed|opened|now)|live|currently|at the moment|as we speak|intraday)\b", re.I)
STOCK_KINDS = {"stocks", "calendar", "macro"}


def freshness_ok(parts, facts, kind):
    """Stock-data formats may not imply freshness unless the index data is today's completed session."""
    if kind not in STOCK_KINDS or facts.get("_STOCKS_TODAY_OK"):
        return True, "ok"
    for k in ("hook", "body", "story", "take", "poll", "title"):
        m = FRESH_WORDS.search(parts.get(k, ""))
        if m:
            return False, f"freshness word '{m.group(0)}' in {k} but stock data is as of {facts.get('SPX_ASOF_CLOSE', 'an older close')}"
    return True, "ok"


def validate(parts, facts, min_w=48, max_w=92):
    """Reject anything with numbers not coming from placeholders. Word range excludes CTA/source line/catchphrase."""
    full = " ".join(parts.get(k, "") for k in ("hook", "body", "story", "take", "poll"))
    for k, t in parts.items():
        for ph in PH.findall(t):
            if ph not in facts:
                return False, f"unknown placeholder {ph} in {k}"
        stripped = ALLOWED.sub("", PH.sub("", t))
        if re.search(r"\d", stripped):
            return False, f"digit outside placeholder in {k}: {stripped[:80]}"
        if k != "title" and re.search(NUM_WORDS, stripped, re.I):
            return False, f"number word outside placeholder in {k}"
    words = len(fill(full, facts).split())
    if not (min_w <= words <= max_w):
        return False, f"word count {words} not in [{min_w},{max_w}]"
    if len(parts["hook"].split()) > 10:
        return False, "hook too long (must land in first 2 s)"
    return True, "ok"


# ---------------- LLM providers ----------------
SYSTEM = """You write 25-40 second English scripts for "Gus Markets", a premium finance-news YouTube Short hosted by Gus,
a deadpan, chubby orange goldfish in a navy necktie who lives in a fishbowl (water rises on green days, he wears a
snorkel on red days). Gus is openly a cartoon fish, never claims to be a human or a licensed expert. Voice: calm, dry
newsreader with understated goldfish humor. Every word must earn its place. Rules - follow ALL:
1. NEVER write any digit or number word. Any price, percentage, level or amount MUST be written ONLY as one of the
   provided {PLACEHOLDERS} exactly (e.g. "Bitcoin is {BTC_MOVE} at {BTC_PRICE}"). Use only placeholders from the list.
   Put each placeholder in a sentence that NAMES its asset (never "{ETH_MOVE}" in a sentence about Solana).
   Never put a direction verb that contradicts the value (no "jumped {BTC_MOVE}" when it is "down ..."): prefer
   neutral verbs like "is", "sits at", "closed", "trades".
2. Do not invent facts, causes or quotes. Only use the provided facts and the verified story. No predictions, no
   buy/sell advice, no promotions, no links.
3. hook: the strongest, most surprising angle of TODAY's data as a QUESTION, 5-8 words, must land within 1.5 seconds
   when read aloud, ends with "?". Concrete beats vague ("Did Bitcoin just lose its floor?" not "What happened today?").
4. body: deliver the payoff fast - the key numbers first, then one line on why it matters, 22-38 words. Short sentences.
   Stock index levels are "as of" the given weekday close.
5. story: if a VERIFIED STORY is given, one neutral sentence (max 22 words) accurately paraphrasing it in your own
   words (who did what), no numbers, no added causes; else "".
6. take: Gus's own short opinion/context, 8-16 words, deadpan, starts with "My take:". No catchphrase.
7. poll: one short audience question answerable in one word in the comments (max 10 words, ends with "?"), ideally
   an either/or ("dip or drop?") that echoes the hook so the video loops back to its first line.
8. title: max 60 characters, a curiosity question or clear statement, may use placeholders, no clickbait lies, no hashtags.
Return ONLY JSON: {"hook": "...", "body": "...", "story": "...", "take": "...", "poll": "...", "title": "..."}"""


def _user_prompt(fmt_name, fmt, facts, story, extra):
    s = ""
    if story:
        s = (f"VERIFIED STORY (covered by {story['n_owners']} independent outlets: {', '.join(story['sources'][:4])}):\n"
             f"Headlines: {json.dumps(story['all_titles'][:4])}\nSummary: {story['summary'][:300]}\n"
             "Paraphrase in your own words; drop any numbers from the headlines.\n")
    return (f"FORMAT: {fmt_name} ({fmt['label']}). {extra}\nPLACEHOLDERS available (value shown for context only, "
            f"write the placeholder not the value):\n" + "\n".join(f"{{{k}}} = {v}" for k, v in facts.items() if not k.startswith("_")) + "\n" + s)


def _parse(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0))


def llm_groq(cfg, sys, user):
    key = env("GROQ_API_KEY")
    if not key: raise RuntimeError("no GROQ_API_KEY")
    c = cfg["llm"]["groq"]
    r = requests.post(c["base_url"] + "/chat/completions", timeout=60, headers={"Authorization": f"Bearer {key}"},
                      json={"model": c["model"], "temperature": cfg["llm"]["temperature"],
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse(r.json()["choices"][0]["message"]["content"])


def llm_gemini(cfg, sys, user):
    key = env("GEMINI_API_KEY")
    if not key: raise RuntimeError("no GEMINI_API_KEY")
    m = cfg["llm"]["gemini"]["model"]
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", timeout=60,
                      headers={"x-goog-api-key": key},
                      json={"systemInstruction": {"parts": [{"text": sys}]}, "contents": [{"parts": [{"text": user}]}],
                            "generationConfig": {"temperature": cfg["llm"]["temperature"], "responseMimeType": "application/json"}})
    r.raise_for_status()
    return _parse(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def llm_ollama(cfg, sys, user):
    c = cfg["llm"]["ollama"]
    r = requests.post(c["base_url"] + "/api/chat", timeout=180, json={"model": c["model"], "stream": False, "format": "json",
                      "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse(r.json()["message"]["content"])


# ---------------- template fallback ----------------
# Structure (tradingempire learnings, adapted): QUESTION hook (0 s) -> numbers -> "Save this" CTA (~12 s)
# -> proof (own source card, multi-outlet) -> Gus's take -> poll question -> catchphrase.
CTA = "Save this for later."
TAKES = {
    "red": ["My take: red days are when tourists leave the bowl.",
            "My take: a red day is a price, not a verdict. Panic is optional.",
            "My take: zoom out before you freak out. The bowl survived worse."],
    "green": ["My take: green is nice. Overconfidence is not. Stay humble, stay wet.",
              "My take: enjoy the high water. Nobody rings a bell at the top.",
              "My take: rallies feel obvious only afterwards. Position size beats vibes."],
    "neutral": ["My take: a flat day is still a day. Most investing is waiting.",
                "My take: boring markets are where patient money gets built.",
                "My take: no big move, no big decision. Sometimes the best trade is a nap."],
}
EXPLAIN = {
    "bond_yields": ("What is a bond yield, really?", "It is what lenders demand to lend to a government. When yields rise, borrowing gets pricier and future profits are worth less. As of {US10Y_ASOF_CLOSE}, the ten year yield was {US10Y}, {US10Y_MOVE_1M} in a month."),
    "rate_cuts": ("Why does everyone obsess over the Fed?", "The Fed sets the overnight rate banks charge each other. Cheaper money tends to lift risky assets, expensive money does the opposite. The effective fed funds rate is {FED_FUNDS}. Markets trade expectations, not decisions."),
    "market_cap": ("What does market cap actually tell you?", "Market cap is price times the number of coins or shares. It tells you size, not value. The whole crypto market is worth about {CRYPTO_MCAP}, and Bitcoin holds {BTC_DOMINANCE} of it."),
}
MYTHS = [
    ("Is a cheap coin a bargain?", "Myth. Price per coin means nothing without supply. Market cap is what counts. All of crypto is worth {CRYPTO_MCAP} today, and Bitcoin alone holds {BTC_DOMINANCE} of it."),
    ("Does one red day end a trend?", "Myth. Single days are noise. Bitcoin is {BTC_MOVE} today but {BTC_MOVE_7D} on the week. Zoom out before you decide what the trend is."),
]
POLLS = {
    "crypto": ["Bitcoin higher or lower by next week? Up or down?", "Is this a dip or a drop? One word."],
    "movers": ["Chase the winner or buy the loser? Pick one.", "Which coin wins tomorrow? Name it."],
    "chart": ["Does this line keep going? Yes or no?", "Up or down from here? One word."],
    "explainer": ["Which money term should I explain next?", "Did that click? Yes or no?"],
    "quiz": ["Did you guess right? Yes or no?", "Too easy? Yes or no?"],
    "macro": ["Yields higher or lower next month? Up or down?"],
    "calendar": ["Which release are you watching this week?"],
    "stocks": ["S and P higher or lower by next week? Up or down?", "Buying stocks this week, or waiting? One word."],
    "myth": ["Ever fallen for this one? Yes or no?", "Which myth should I bust next?"],
}
KIND = {"crypto_update": "crypto", "crypto_morning": "crypto", "top_movers": "movers", "midday_movers": "movers",
        "chart_of_the_day": "chart", "explainer": "explainer", "quiz": "quiz", "macro_watch": "macro",
        "macro_calendar": "calendar", "myth_buster": "myth"}


def kind_of(fmt_name, fmt):
    return fmt.get("kind") or KIND.get(fmt_name, "stocks")


def template_script(fmt_name, fmt, facts, story, mood, rnd):
    h = lambda *xs: rnd.choice(xs)
    kind = kind_of(fmt_name, fmt)
    if kind == "chart" and "BTC_PRICE" in facts and "CHART_NAME" not in facts:
        kind = "crypto"
    crypto_body = "Bitcoin trades at {BTC_PRICE}, {BTC_MOVE} in a day and {BTC_MOVE_7D} on the week. Ether is {ETH_MOVE} at {ETH_PRICE}."
    if kind == "crypto":
        hook = (h("Did crypto move while you slept?", "What happened to Bitcoin overnight?") if fmt_name == "crypto_morning" else
                h("Bitcoin {BTC_MOVE} today. Panic or shrug?", "Is crypto calm or cracking today?", "Did you check Bitcoin today?"))
        body = crypto_body + " The whole market is worth {CRYPTO_MCAP}, {CRYPTO_MCAP_MOVE}."
        title = h("Bitcoin {BTC_MOVE}: panic or shrug?", "Crypto today: what {BTC_PRICE} Bitcoin means", "Is crypto calm or cracking?")
    elif kind == "movers":
        hook = h("Who pumped and who dumped today?", "Which big coin is winning today?")
        body = ("Biggest gainer among the large caps: {GAINER1_NAME}, {GAINER1_MOVE}. Biggest loser: {LOSER1_NAME}, {LOSER1_MOVE}. "
                "Bitcoin, the benchmark, is {BTC_MOVE} at {BTC_PRICE}.")
        title = h("{GAINER1_NAME} {GAINER1_MOVE}: who pumped, who dumped?", "Today's crypto winners and losers")
    elif kind == "chart":
        hook = h("Can one chart explain this market?", "See this line? Here is why it matters.")
        body = ("This is {CHART_NAME} over {CHART_PERIOD}. It is {CHART_MOVE} in that window and sits at {CHART_LAST} as of {CHART_ASOF}. "
                "One line is not the whole story, but it shows where money has been leaning.")
        title = h("{CHART_NAME} {CHART_MOVE}: the chart of the day", "Can one chart explain this market?")
    elif kind == "explainer":
        k = rnd.choice([k for k in EXPLAIN if all(p in facts for p in PH.findall(EXPLAIN[k][1]))] or ["market_cap"])
        hook, body = EXPLAIN[k]
        title = hook.replace(", really", "") + " Explained by a goldfish"
    elif kind == "quiz":
        hook = "Is Ether higher or lower than last week?"
        body = ("Lock in your guess. I already forgot the question. Answer: Ether is {ETH_MOVE_7D} on the week at {ETH_PRICE}. "
                "Bitcoin, for comparison, is {BTC_MOVE_7D}. Weekly moves beat hourly noise.")
        title = "Quiz: is Ether higher or lower than last week?"
    elif kind == "macro":
        hook = h("What is the bond market telling you?", "Are rates quietly moving everything?")
        body = ("As of {US10Y_ASOF_CLOSE}, the ten year Treasury yield was {US10Y}, {US10Y_MOVE_1M} over the past month. The fed funds rate sits at {FED_FUNDS}. "
                "Yields are the price of money, and the price of money moves everything else.")
        title = "What is the bond market telling you?"
    elif kind == "calendar":
        hook = "What could move markets this week?"
        body = ("On the calendar: {CAL1_NAME} on {CAL1_DAY}, {CAL2_NAME} on {CAL2_DAY}, and {CAL3_NAME} on {CAL3_DAY}. "
                "Big releases mean big swings, in stocks and crypto. Know the dates before they hit.")
        title = "What could move markets this week?"
        if "CAL1_NAME" not in facts:  # no FRED key / no releases -> rates context instead
            body = ("No headline release I can confirm. So, rates: as of {US10Y_ASOF_CLOSE}, the ten year yield was {US10Y}, "
                    "{US10Y_MOVE_1M} in a month, and fed funds sit at {FED_FUNDS}. That is the backdrop for everything.")
    elif kind == "myth":
        hook, body = rnd.choice(MYTHS)
        title = "Myth busted: " + hook
    else:  # stocks: pre_market, us_open, close_recap, recap, week_recap, week_ahead
        if facts.get("_STOCKS_TODAY_OK"):   # index data = today's completed session
            hook = {"close_recap": h("Did stocks win or lose today?", "Green or red on Wall Street today?"),
                    "week_recap": "Did your portfolio survive this week?"}.get(fmt_name, h("Did stocks win or lose today?", "What did Wall Street do today?"))
            body = ("At {SPX_ASOF_CLOSE}, the S and P five hundred stood at {SPX_LEVEL}, {SPX_MOVE}. The Nasdaq ended at {NDX_LEVEL}, {NDX_MOVE}. "
                    "The ten year yield is {US10Y}. Bitcoin is {BTC_MOVE}.")
        else:                               # as-of wording, no freshness words
            hook = {"pre_market": "What matters before the next bell?", "us_open": "Where did Wall Street leave off?",
                    "close_recap": h("How did stocks finish at the last close?", "Green or red at the last close?"),
                    "week_recap": "Did your portfolio survive this week?", "week_ahead": "Ready for the new market week?"}.get(
                        fmt_name, h("How did stocks finish at the last close?", "What did Wall Street do last session?"))
            ndx = ("The Nasdaq was at" if facts.get("NDX_ASOF_CLOSE") == facts.get("SPX_ASOF_CLOSE")
                   else "As of {NDX_ASOF_CLOSE}, the Nasdaq was at")   # each index keeps its own as-of date
            body = ("As of {SPX_ASOF_CLOSE}, the S and P five hundred stood at {SPX_LEVEL}, {SPX_MOVE}. " + ndx + " {NDX_LEVEL}, {NDX_MOVE}. "
                    "The ten year yield was {US10Y}. Bitcoin is {BTC_MOVE} over the past day.")
        title = hook if rnd.random() < .5 else "S&P 500 {SPX_MOVE}: " + hook[0].lower() + hook[1:]
    st = ""
    if story and len(story["headline"].split()) <= 18:
        st = f"The story: {story['headline'].rstrip('.')}."
    return {"hook": hook, "body": body, "story": st, "take": h(*TAKES[mood]),
            "poll": h(*POLLS.get(kind, POLLS["stocks"])), "title": title}


def _prune(parts, facts):
    """Drop sentences whose placeholders are missing (e.g. a provider was down)."""
    for k in ("hook", "body", "story", "take", "poll", "title"):
        sents = re.split(r"(?<=[.!?])\s+", parts.get(k, ""))
        parts[k] = " ".join(s for s in sents if all(p in facts for p in PH.findall(s)))
    parts["hook"] = parts["hook"] or "What moved markets?"
    parts["title"] = parts["title"] or "What moved markets?"
    return parts


def generate(cfg, fmt_name, facts, story, mood, seed):
    fmt = cfg["formats"][fmt_name]
    rnd = random.Random(seed)
    kind = kind_of(fmt_name, fmt)
    extra = f"Format: {fmt['label']}. Market mood: {mood}."
    if kind == "explainer":
        extra += " Explain one concept simply (bond yields, rate cuts, or market cap) using the facts as a live example."
    if kind == "quiz":
        extra += " The hook is the quiz question; in the body tell viewers to lock in a guess, then reveal the answer using placeholders."
    if kind == "calendar":
        extra += " List the upcoming releases with their weekday placeholders."
    if kind in STOCK_KINDS and not facts.get("_STOCKS_TODAY_OK") and "SPX_ASOF_CLOSE" in facts:
        extra += (" IMPORTANT: stock index numbers are as of {SPX_ASOF_CLOSE}. Attribute them with that placeholder"
                  " (e.g. 'At {SPX_ASOF_CLOSE}, the S and P...'). Never use today, tonight, right now, live, currently, so far.")
    log = []
    for prov in cfg["llm"]["providers"]:
        if prov == "template":
            parts = _prune(template_script(fmt_name, fmt, facts, story, mood, rnd), facts)
            ok, why = validate(parts, facts, min_w=40, max_w=100)
            if not ok and "digit" in why and parts["story"]:   # headline contained digits -> drop story sentence
                parts["story"] = ""
                ok, why = validate(parts, facts, min_w=40, max_w=100)
            fok, fwhy = freshness_ok(parts, facts, kind)
            if not fok:
                raise RuntimeError("template freshness bug: " + fwhy)
            cok, cwhy = factcheck.check_script(parts, facts)
            if not cok:
                raise RuntimeError("template fact-check failed: " + "; ".join(cwhy))
            log.append(f"template: {why}")
            return parts, "template", log
        need_key = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}.get(prov)
        if need_key and not env(need_key):
            log.append(f"{prov}: skipped (no {need_key})")
            continue
        if prov == "ollama" and not env("OLLAMA_HOST") and env("CI") == "true":
            log.append("ollama: skipped (CI, no OLLAMA_HOST)")
            continue
        fn = {"groq": llm_groq, "gemini": llm_gemini, "ollama": llm_ollama}[prov]
        for attempt in range(cfg["llm"]["max_retries"] + 1):
            try:
                parts = fn(cfg, SYSTEM, _user_prompt(fmt_name, fmt, facts, story, extra))
                parts = {k: str(parts.get(k, "")).strip() for k in ("hook", "body", "story", "take", "poll", "title")}
                if not story: parts["story"] = ""
                ok, why = validate(parts, facts)
                if ok and not parts["hook"].rstrip().endswith("?"):
                    ok, why = False, "hook must be a question"
                if ok:
                    ok, why = freshness_ok(parts, facts, kind)
                if ok:      # every number / attribution / direction must match the fetched data, else regenerate
                    ok, reasons = factcheck.check_script(parts, facts)
                    why = "ok, fact-check passed" if ok else "fact-check: " + "; ".join(reasons)[:300]
                log.append(f"{prov}#{attempt}: {why}")
                if ok:
                    return parts, prov, log
            except Exception as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                log.append(f"{prov}#{attempt}: {type(e).__name__}: {str(e)[:120]}".replace(env("GEMINI_API_KEY") or "\0", "***"))
                transient = code in (429, 500, 502, 503, 504) or isinstance(e, (requests.Timeout, requests.ConnectionError))
                if not transient:
                    break
                time.sleep(min(30, 5 * (attempt + 1)))   # rate limit / overload -> back off and retry same provider
    raise RuntimeError("no script")


def _clean(t):
    return re.sub(r"\s+", " ", t).replace(" ,", ",").strip()


def spoken_of(segs):
    return _clean(" ".join(t for _, t in segs))


def trim_segments(segs):
    """One trim step toward the 40 s cap; returns new segs or None if nothing optional is left.
    Never touches: hook, first NUMBERS block, CTA, poll, catchphrase."""
    segs = list(segs)
    idx = [i for i, (c, _) in enumerate(segs) if c == "THE NUMBERS"]
    if len(idx) > 1:                                   # (a) second numbers block, last sentence first
        i = idx[-1]; sents = re.split(r"(?<=[.!?])\s+", segs[i][1])
        if len(sents) > 1:
            segs[i] = (segs[i][0], " ".join(sents[:-1])); return segs
        return segs[:i] + segs[i + 1:]
    for i, (c, t) in enumerate(segs):                  # (b) proof attribution 3 -> 2 sources
        m = re.search(r" Reported independently by (.+?), (.+?) and (.+?)\.$", t) if c == "THE PROOF" else None
        if m:
            segs[i] = (c, t[:m.start()] + f" Reported independently by {m.group(1)} and {m.group(2)}."); return segs
    takes = [i for i, (c, _) in enumerate(segs) if c == "GUS'S TAKE"]
    if len(takes) > 1:                                 # (c) drop the opinion line (keeps the catchphrase)
        return segs[:takes[0]] + segs[takes[0] + 1:]
    return None


def assemble(parts, facts, catchphrase, story=None, cta_after_words=22):
    """Return (spoken_text, title, segments). segments = [(chapter, text)] in spoken order; the renderer
    times chapter line / SAVE THIS pill / source card from them."""
    body = _clean(fill(parts["body"], facts))
    # insert the CTA at the first sentence end once ~cta_after_words have been spoken (~11-12 s at 2.4 w/s)
    hook = _clean(fill(parts["hook"], facts))
    sents, b1, b2, n = re.split(r"(?<=[.!?])\s+", body), [], [], len(hook.split())
    for s in sents:
        if not b2 and (n < cta_after_words or not b1):
            b1.append(s); n += len(s.split())
        else:
            b2.append(s)
    segs = [("THE QUESTION", hook), ("THE NUMBERS", " ".join(b1)), ("SAVE", CTA)]
    if b2: segs.append(("THE NUMBERS", " ".join(b2)))
    proof = _clean(fill(parts.get("story", ""), facts))
    if proof and story:
        src = story["sources"][:3]
        proof += (" Reported independently by " + ", ".join(src[:-1]) + " and " + src[-1] + ".") if len(src) > 1 else f" Reported by {src[0]}."
        segs.append(("THE PROOF", proof))
    segs.append(("GUS'S TAKE", _clean(fill(parts["take"], facts))))
    poll = _clean(fill(parts.get("poll", ""), facts))
    if poll:
        segs.append(("YOUR CALL", poll if poll.lower().startswith("your call") else "Your call: " + poll))
    segs.append(("GUS'S TAKE", catchphrase))
    segs = [(c, t) for c, t in segs if t]
    spoken = _clean(" ".join(t for _, t in segs))
    return spoken, fill(parts["title"], facts), segs
