"""Fact check: every number that is spoken, titled or shown must trace back to the fetched data.

Layered on top of script_gen.validate (which already forbids digits outside {PLACEHOLDERS}):
  1. facts vs snapshot  - placeholder strings are re-derived from the raw API snapshot and must match exactly
  2. numbers            - every number in the filled script/title must equal a number in some fact value
  3. attribution        - a placeholder like {ETH_MOVE} must sit in (or right after) a sentence that names Ether
  4. direction          - "Bitcoin jumped {BTC_MOVE}" with BTC_MOVE = "down 4.3%" is rejected
  5. on-screen          - the big counter / badges show exactly the fact strings the voice reads
check_script() returns (ok, reasons). script_gen.generate() regenerates on failure and falls back to the template.
"""
import re

NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
NAMES_WITH_DIGITS = re.compile(r"S&P 500|Nasdaq 100|Web3|24/7", re.I)
ALIASES = {
    "BTC": r"bitcoin|btc", "ETH": r"ether|ethereum|eth", "SOL": r"solana|sol", "XRP": r"xrp|ripple",
    "DOGE": r"doge|dogecoin", "SPX": r"s&p|s and p|stocks|wall street|index", "NDX": r"nasdaq|tech",
    "US10Y": r"ten[- ]year|treasury|yield|bond", "FED": r"fed|federal|rate", "CRYPTO": r"crypto|market|coins",
    "BTC_DOMINANCE": r"dominance|bitcoin|share",
}
UP_VERBS = r"rose|rises|rising|jump(?:ed|s)?|surg(?:ed|es)|soar(?:ed|s)|rall(?:ied|ies)|gain(?:ed|s)|climb(?:ed|s)|spik(?:ed|es)|higher|green|pump(?:ed|s)?"
DOWN_VERBS = r"fell|falls|falling|drop(?:ped|s)?|sl(?:id|ides)|plung(?:ed|es)|s[au]nk|sinks|tumbl(?:ed|es)|crash(?:ed|es)|slump(?:ed|s)|lower|red|dump(?:ed|s)?"
SKIP = re.compile(r"_ASOF|_NAME$|^CHART_PERIOD$|^CHART_ASOF$|^CAL\d")


def _nums(text):
    out = set()
    for m in NUM.finditer(NAMES_WITH_DIGITS.sub(" ", text)):
        v = m.group(0).replace("$", "").replace(",", "").rstrip("%")
        try: out.add(float(v))
        except ValueError: pass
    return out


def _sentences(t):
    return [s for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s]


def _alias(key, facts):
    if key.startswith("CHART_"):
        return r"\{CHART_NAME\}" + ("|" + re.escape(facts["CHART_NAME"]) if facts.get("CHART_NAME") else "")
    m = re.match(r"(GAINER|LOSER)(\d)_", key)
    if m:
        nk = f"{m.group(1)}{m.group(2)}_NAME"
        return r"\{" + nk + r"\}" + ("|" + re.escape(facts[nk]) if nk in facts else "")
    if key == "BTC_DOMINANCE": return ALIASES["BTC_DOMINANCE"]
    if key.startswith("FED_"): return ALIASES["FED"]
    if key.startswith("CRYPTO_"): return ALIASES["CRYPTO"]
    return ALIASES.get(key.split("_")[0])


def facts_vs_snapshot(facts, snap, fmt_name, cfg):
    from .script_gen import build_facts
    ref = build_facts(snap, fmt_name, cfg)
    return [f"fact {k}={facts[k]!r} != snapshot-derived {v!r}" for k, v in ref.items() if not k.startswith("_") and facts.get(k) != v]


def check_script(parts, facts):
    from .script_gen import fill, PH
    bad = []
    allowed = set()
    for k, v in facts.items():
        if not k.startswith("_"): allowed |= _nums(str(v))
    for k in ("hook", "body", "story", "take", "poll", "title"):
        raw = parts.get(k, "") or ""
        # (2) numbers in the filled text must all come from fact values
        extra = _nums(fill(raw, facts)) - allowed
        if extra:
            bad.append(f"{k}: number(s) {sorted(extra)} not in fetched data")
        sents = _sentences(raw)
        for i, s in enumerate(sents):
            ctx = " ".join(sents[max(0, i - 1):i + 1]) if k != "title" else raw
            for m in PH.finditer(s):
                key = m.group(1)
                if key not in facts or SKIP.search(key): continue
                # (3) attribution: the right asset is named in this or the previous sentence
                al = _alias(key, facts)
                ctx_n = re.sub(r"\{(?![A-Z0-9]+_NAME\})[A-Z0-9_]+\}", " ", ctx)   # placeholder names don't count as naming
                if al and not re.search(rf"(?:\b|(?=\{{))(?:{al})(?:\b|(?<=\}}))", ctx_n, re.I):
                    bad.append(f"{k}: {{{key}}} used without naming its asset: {s[:70]!r}")
                # (4) direction words right before a move placeholder must agree with the data
                val = str(facts[key]).lower()
                before = " ".join(s[:m.start()].split()[-4:])
                if val.startswith("down") and re.search(rf"\b(?:{UP_VERBS})\b", before, re.I):
                    bad.append(f"{k}: bullish verb before {{{key}}}={facts[key]!r}")
                if val.startswith("up") and re.search(rf"\b(?:{DOWN_VERBS})\b", before, re.I):
                    bad.append(f"{k}: bearish verb before {{{key}}}={facts[key]!r}")
                if val == "flat" and re.search(rf"\b(?:{UP_VERBS}|{DOWN_VERBS})\b", before, re.I):
                    bad.append(f"{k}: move verb before flat {{{key}}}")
    return (not bad), bad


def check_onscreen(job, facts):
    """Numbers drawn on screen must be the same strings the voice reads."""
    from .script_gen import money, move
    bad = []
    if job.get("big_value") != facts.get("CHART_LAST"):
        bad.append(f"big counter {job.get('big_value')!r} != CHART_LAST {facts.get('CHART_LAST')!r}")
    cn = (facts.get("CHART_NAME") or "").lower()
    for key, pat in (("SPX_LEVEL", r"s&p|s and p"), ("NDX_LEVEL", r"nasdaq")):
        if key in facts and re.search(pat, cn) and facts.get("CHART_LAST") != facts[key]:
            bad.append(f"chart {cn} last {facts.get('CHART_LAST')!r} != {key} {facts[key]!r}")
    sub = (job.get("big_sub") or "").split(" \u00B7 ")[0].lower()
    if sub and facts.get("CHART_MOVE") and sub != facts["CHART_MOVE"].lower():
        bad.append(f"chart badge {sub!r} != CHART_MOVE {facts['CHART_MOVE']!r}")
    for a in job.get("assets", []):
        key = a.get("key", "").upper()
        fk = {"SPX": "SPX_LEVEL", "NDX": "NDX_LEVEL", "US10Y": "US10Y"}.get(key, f"{key}_PRICE")
        if fk in facts and a.get("price") != facts[fk]:
            bad.append(f"badge {key} value {a.get('price')!r} != {fk} {facts[fk]!r}")
        mk = f"{key}_MOVE"
        if mk in facts and "chg" in a and key != "US10Y" and move(a["chg"]) != facts[mk]:
            bad.append(f"badge {key} move {move(a['chg'])!r} != {mk} {facts[mk]!r}")
    return (not bad), bad
