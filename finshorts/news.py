"""RSS ingestion + cross-source fact check (>= N independent publishers per story)."""
import re, time, html, hashlib, calendar, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import feedparser, requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; FinShortsBot/0.1; RSS reader)"}
STOP = set("""a an the and or of to in on for with at by from as is are was were be been it its this that these those
after before over under into amid about than more most new says say said could would will may might can up down out
why how what who when where which while just now today week year years day days report reports per vs via his her their
our your you we they he she them us not no yes all any some one two three first last next top big""".split())
TOPIC_KW = {
    "crypto": "bitcoin btc ether ethereum eth crypto solana xrp ripple stablecoin token defi etf blockchain coinbase binance doge memecoin altcoin miner halving tether".split(),
    "stocks": "stock stocks shares s&p nasdaq dow earnings wall street rally selloff nvidia apple tesla microsoft amazon meta alphabet ipo index futures equities".split(),
    "macro": "fed inflation cpi pce rates rate yield yields treasury powell jobs payrolls unemployment gdp recession tariff dollar ecb economy central bank".split(),
}


def _tokens(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or "")).lower()
    toks = re.findall(r"[a-z][a-z0-9&$'.-]+", text)
    toks = [t.strip(".'-") for t in toks]
    alias = {"btc": "bitcoin", "eth": "ether", "ethereum": "ether", "s&p": "sp500", "stocks": "stock", "rates": "rate",
             "yields": "yield", "shares": "stock", "federal": "fed", "fomc": "fed", "tariffs": "tariff"}
    return {alias.get(t, t) for t in toks if len(t) > 2 and t not in STOP}


def fetch_feeds(cfg):
    max_age = cfg["news"]["max_age_hours"] * 3600
    now = time.time()

    def one(f):
        try:
            r = requests.get(f["url"], headers=UA, timeout=15)
            d = feedparser.parse(r.content)
        except Exception as e:
            return [], f"{f['name']}: {e}"
        items = []
        for e in d.entries[:60]:
            tt = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(tt) if tt else now
            if now - ts > max_age:
                continue
            title = html.unescape(e.get("title", "")).strip()
            summ = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", "")))[:400].strip()
            items.append({"source": f["name"], "owner": f["owner"], "feed_topic": f["topic"], "title": title,
                          "summary": summ, "link": e.get("link", ""), "ts": ts,
                          "tok": _tokens(title + " " + title + " " + summ[:200])})
        return items, None

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, cfg["news"]["feeds"]))
    items, errs, seen = [], [], set()
    for it, err in res:
        if err:
            errs.append(err)
        for i in it:
            k = i["title"].lower()
            if k not in seen:
                seen.add(k); items.append(i)
    return items, errs


def _sim(a, b):
    ta, tb = _tokens(a["title"]), _tokens(b["title"])
    if not ta or not tb:
        return 0.0
    j_title = len(ta & tb) / len(ta | tb)
    j_all = len(a["tok"] & b["tok"]) / max(1, len(a["tok"] | b["tok"]))
    return max(j_title, 1.6 * j_all)


def cluster(items, thr):
    clusters = []
    for it in sorted(items, key=lambda x: -x["ts"]):
        best, bs = None, 0.0
        for c in clusters:
            s = max(_sim(it, m) for m in c)
            if s > bs:
                best, bs = c, s
        if best is not None and bs >= thr:
            best.append(it)
        else:
            clusters.append([it])
    return clusters


# unambiguous crypto words (no "etf"/"token"/"miner"): any of these keeps a story out of stock/macro formats
CRYPTO_CORE = set("bitcoin btc ether ethereum crypto cryptocurrency solana xrp ripple stablecoin defi blockchain binance doge memecoin altcoin tether".split())


def topic_score(c, topics):
    toks = set().union(*[m["tok"] for m in c])
    return sum(len(toks & set(TOPIC_KW[t])) for t in topics)


# ---------------- relevance: finance / markets / crypto / macro / companies only ----------------
OFFTOPIC = re.compile(r"\b(kidnap\w*|abduct\w*|murder\w*|homicide|kill(ed|ing|s)?|shoot(ing|er)s?|stabb\w*|assault\w*|rape\w*|"
                      r"tortur\w*|hostage\w*|ransom\w*|prison|jail\w*|sentenced|manslaughter|gunman|gunmen|"
                      r"celebrity|actor|actress|singer|football|soccer|nba|nfl|olympic\w*|"
                      r"weather|hurricane|earthquake|wildfire|obituary|dies|dead|death)\b", re.I)
FIN_KW = set("""market markets stock stocks shares share equities index bond bonds yield yields treasury rate rates fed ecb boj
central bank banks banking inflation cpi pce gdp jobs payrolls unemployment economy economic recession tariff tariffs trade
dollar euro yen currency oil gold commodity commodities earnings revenue profit profits loss sales guidance forecast outlook
ipo merger acquisition deal buyout dividend buyback valuation investor investors investment fund funds etf etfs hedge
trading traders exchange exchanges listing regulator sec cftc fca regulation lawsuit settlement fine billion million
bitcoin btc ether ethereum crypto cryptocurrency stablecoin token tokens blockchain defi dex miners mining coinbase binance
nvidia apple tesla microsoft amazon meta alphabet google company companies ceo layoffs debt credit lending mortgage housing
price prices s&p nasdaq dow wall street futures hack exploit liquidations""".split())


def _keyword_relevant(s):
    text = " ".join([s["headline"]] + s.get("all_titles", [])[:3])
    if OFFTOPIC.search(s["headline"]):
        return False
    return len(_tokens(text) & FIN_KW) >= 2


def _llm_relevant(cfg, stories):
    """One Gemini call: which headlines are primarily finance / markets / crypto / macro / company-business news?
    Returns a set of indexes, or None if the LLM is unavailable (keyword filter result is then used alone)."""
    import json, os
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not stories:
        return None
    lines = "\n".join(f"{i}: {s['headline']}" for i, s in enumerate(stories))
    prompt = ("You filter news for a finance channel. For each numbered headline decide if it is PRIMARILY about financial "
              "markets, stocks, bonds, crypto markets or the crypto industry, the economy / macro policy, or a company's business. "
              "Answer false for crime / violence / court cases about individuals (kidnapping, murder, sentencing of people), "
              "celebrity, sports, weather, general politics without a market angle, even if money or crypto is mentioned.\n"
              f"{lines}\nReturn JSON: {{\"relevant\": [indexes of relevant headlines]}}")
    for m in (cfg.get("news", {}).get("relevance_models") or ["gemini-flash-latest", "gemini-flash-lite-latest"]):
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", timeout=60,
                              headers={"x-goog-api-key": key},
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
            r.raise_for_status()
            j = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            return {int(i) for i in j.get("relevant", [])}
        except Exception:
            time.sleep(3)
    return None


def relevant_stories(cfg, stories, log=None):
    kw = [s for s in stories if _keyword_relevant(s)]
    dropped = [s["headline"] for s in stories if s not in kw]
    ok = _llm_relevant(cfg, kw[:12])
    if ok is not None:
        dropped += [s["headline"] for i, s in enumerate(kw[:12]) if i not in ok]
        kw = [s for i, s in enumerate(kw[:12]) if i in ok]
    if log is not None:
        log.setdefault("relevance_dropped", []).extend(dropped)
        log["relevance_llm"] = "gemini" if ok is not None else "keyword-only (LLM unavailable)"
    return kw


def verified_stories(cfg, topics, items=None):
    """Return stories covered by >= min_independent_sources different owners, best first."""
    if items is None:
        items, _ = fetch_feeds(cfg)
    need = cfg["news"]["min_independent_sources"]
    topics = list(topics)
    # strict topic matching: only items from feeds of the allowed topics ...
    items = [i for i in items if i["feed_topic"] in topics]
    out = []
    for c in cluster(items, cfg["news"]["similarity_threshold"]):
        # ... and the cluster's dominant keyword topic must be allowed (e.g. a crypto story in a CNBC feed
        # never reaches a stock format, a stock story in a crypto feed never reaches a crypto format)
        scores = {t: topic_score(c, [t]) for t in TOPIC_KW}
        if max(scores.values()) == 0 or max(scores, key=lambda t: (scores[t], t in topics)) not in topics:
            continue
        if "crypto" not in topics and set().union(*[m["tok"] for m in c]) & CRYPTO_CORE:
            continue
        owners = {m["owner"] for m in c}
        if len(owners) < need:
            continue
        ts = topic_score(c, topics)
        if ts == 0:
            continue
        # prefer a headline without digits (numbers must come from APIs, not from headlines)
        rep = sorted(c, key=lambda m: (bool(re.search(r"\d", m["title"])), len(m["title"])))[0]
        sid = hashlib.sha1(" ".join(sorted(_tokens(rep["title"]))).encode()).hexdigest()[:12]
        out.append({"id": sid, "headline": rep["title"], "summary": rep["summary"],
                    "sources": sorted({m["source"] for m in c}), "owners": sorted(owners),
                    "links": [m["link"] for m in c][:5], "n_owners": len(owners), "topic_score": ts,
                    "newest": max(m["ts"] for m in c), "published": rep["ts"], "rep_source": rep["source"],
                    "all_titles": [m["title"] for m in c][:6]})
    out.sort(key=lambda s: (s["n_owners"], s["topic_score"], s["newest"]), reverse=True)
    return out
