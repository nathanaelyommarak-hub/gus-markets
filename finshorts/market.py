"""Market data from free APIs. Every number used in a script must come from here."""
import os, csv, io, json, time, hashlib, datetime as dt
import requests
from .config import CACHE, env

UA = {"User-Agent": "Mozilla/5.0 (FinShorts research bot)"}


def _cached(key, minutes, fn):
    p = CACHE / ("m_" + hashlib.md5(key.encode()).hexdigest() + ".json")
    if p.exists() and time.time() - p.stat().st_mtime < minutes * 60:
        return json.loads(p.read_text())
    data = fn()
    p.write_text(json.dumps(data))
    return data


# ---------------- CoinGecko (Demo plan: 100 calls/min, 10k calls/month, attribution required) ----------
def _cg(cfg, path, params):
    base = cfg["data"]["coingecko"]["base_url"]
    h = dict(UA)
    if env("COINGECKO_API_KEY"):
        h["x-cg-demo-api-key"] = env("COINGECKO_API_KEY")
    r = requests.get(base + path, params=params, headers=h, timeout=20)
    r.raise_for_status()
    return r.json()


def coins_snapshot(cfg):
    ids = ",".join(cfg["data"]["coingecko"]["coins"])
    def f():
        return _cg(cfg, "/coins/markets", {"vs_currency": "usd", "ids": ids,
                   "price_change_percentage": "24h,7d"})
    rows = _cached("cg_markets" + ids, cfg["data"]["cache_minutes"], f)
    return {r["id"]: {"name": r["name"], "symbol": r["symbol"].upper(), "price": r["current_price"],
                      "chg_24h": r.get("price_change_percentage_24h_in_currency") or r.get("price_change_percentage_24h") or 0.0,
                      "chg_7d": r.get("price_change_percentage_7d_in_currency") or 0.0,
                      "mcap": r.get("market_cap"), "source": "CoinGecko",
                      "asof": r.get("last_updated")} for r in rows}


def coin_history(cfg, coin_id, days):
    def f():
        d = _cg(cfg, f"/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": days})
        return d["prices"]
    pts = _cached(f"cg_hist_{coin_id}_{days}", cfg["data"]["cache_minutes"], f)
    return [(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).isoformat(), p) for t, p in pts]


def global_crypto(cfg):
    def f():
        return _cg(cfg, "/global", {})["data"]
    g = _cached("cg_global", cfg["data"]["cache_minutes"], f)
    return {"total_mcap_usd": g["total_market_cap"]["usd"], "mcap_chg_24h": g.get("market_cap_change_percentage_24h_usd", 0.0),
            "btc_dominance": g["market_cap_percentage"]["btc"], "source": "CoinGecko"}


def top_movers(cfg, n=3):
    def f():
        return _cg(cfg, "/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50,
                   "page": 1, "price_change_percentage": "24h"})
    rows = _cached("cg_top50", cfg["data"]["cache_minutes"], f)
    rows = [r for r in rows if r.get("price_change_percentage_24h") is not None
            and r["symbol"].lower() not in ("usdt", "usdc", "dai", "usde", "fdusd", "usds", "pyusd", "steth", "wbtc", "weth", "wsteth", "weeth")]
    rows.sort(key=lambda r: r["price_change_percentage_24h"])
    pick = lambda r: {"name": r["name"], "symbol": r["symbol"].upper(), "price": r["current_price"], "chg_24h": r["price_change_percentage_24h"]}
    return {"gainers": [pick(r) for r in rows[::-1][:n]], "losers": [pick(r) for r in rows[:n]], "source": "CoinGecko"}


# ---------------- FRED (free key, 120 req/min) ----------------
def fred_series(cfg, series_id, days=120):
    def f():
        start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        key = env("FRED_API_KEY")
        if key:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations", timeout=20,
                             params={"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start})
            r.raise_for_status()
            obs = [(o["date"], o["value"]) for o in r.json()["observations"]]
        else:  # keyless CSV download of the same series (public FRED graph export)
            r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id, "cosd": start},
                             headers=UA, timeout=20)
            r.raise_for_status()
            rd = csv.reader(io.StringIO(r.text)); next(rd)
            obs = [(a, b) for a, b in rd]
        return [(d, float(v)) for d, v in obs if v not in (".", "")]
    return _cached(f"fred_{series_id}_{days}", 180, f)


# ---------------- Stocks / indices ----------------
def _yahoo(symbol, days):
    rng = "5d" if days <= 5 else "1mo" if days <= 31 else "3mo" if days <= 92 else "1y"
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"range": rng, "interval": "1d"}, headers=UA, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    return [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c) for t, c in zip(ts, cl) if c is not None]


def _alphavantage(av_symbol):
    key = env("ALPHAVANTAGE_API_KEY")
    if not key:
        raise RuntimeError("no ALPHAVANTAGE_API_KEY")
    r = requests.get("https://www.alphavantage.co/query", timeout=20,
                     params={"function": "TIME_SERIES_DAILY", "symbol": av_symbol, "apikey": key})
    j = r.json()
    if "Time Series (Daily)" not in j:
        raise RuntimeError(str(j)[:200])
    s = j["Time Series (Daily)"]
    return sorted((d, float(v["4. close"])) for d, v in s.items())


def index_history(cfg, symbol, days=30):
    meta = cfg["data"]["stocks"]["indices"][symbol]
    errs = []
    for prov in cfg["data"]["stocks"]["providers"]:
        try:
            if prov == "fred":
                pts = fred_series(cfg, meta["fred"], days + 10)
                src = "FRED (S&P Dow Jones Indices / Nasdaq)"
            elif prov == "alphavantage":
                # AV only has the ETF (SPY/QQQ ~ 1/10 of the index level) -> never show the ETF price as the index.
                # Index levels come from FRED; AV only extends the series by sessions FRED has not published yet,
                # scaled by the index/ETF ratio on the latest common date (one-day error << 0.1 %).
                av = _cached(f"av_{meta['av']}", 360, lambda: _alphavantage(meta["av"]))
                fr = fred_series(cfg, meta["fred"], days + 10)
                avd = dict(av); frd = dict(fr)
                common = sorted(set(avd) & set(frd))
                if not common:
                    raise RuntimeError("no common date between FRED index and AV ETF for scaling")
                ratio = frd[common[-1]] / avd[common[-1]]
                newer = [(d, v * ratio) for d, v in av if d > fr[-1][0]]
                pts = list(fr) + newer
                src = (f"FRED + Alpha Vantage ({meta['av']}-scaled latest close)" if newer else "FRED (S&P Dow Jones Indices / Nasdaq)")
            elif prov == "yahoo":
                if os.environ.get("CI") == "true" and not os.environ.get("ALLOW_YAHOO"):
                    continue  # Yahoo ToS: personal use only -> never in production runs
                pts = _cached(f"yh_{symbol}_{days}", cfg["data"]["cache_minutes"], lambda: _yahoo(symbol, days))
                src = "Yahoo Finance (dev only)"
            else:
                continue
            cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
            pts = [p for p in pts if p[0][:10] >= cutoff]
            if len(pts) >= 2:
                return {"name": meta["name"], "points": pts, "last": pts[-1][1], "prev": pts[-2][1],
                        "chg_1d": (pts[-1][1] / pts[-2][1] - 1) * 100, "asof": pts[-1][0], "source": src}
        except Exception as e:  # noqa
            errs.append(f"{prov}: {e}")
    raise RuntimeError("no stock provider worked: " + "; ".join(errs))


def snapshot(cfg):
    """Collect everything once per run. Facts dict = the ONLY numeric truth for scripts."""
    snap = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "errors": []}
    for name, fn in [("coins", lambda: coins_snapshot(cfg)), ("global", lambda: global_crypto(cfg)),
                     ("movers", lambda: top_movers(cfg))]:
        try:
            snap[name] = fn()
        except Exception as e:
            snap["errors"].append(f"{name}: {e}")
    snap["indices"] = {}
    for sym in cfg["data"]["stocks"]["indices"]:
        try:
            snap["indices"][sym] = index_history(cfg, sym, 40)
        except Exception as e:
            snap["errors"].append(f"{sym}: {e}")
    try:
        snap["calendar"] = release_calendar(cfg)
    except Exception as e:
        snap["errors"].append(f"calendar: {e}")
    snap["fred"] = {}
    for sid, label in cfg["data"]["fred_series"].items():
        try:
            pts = fred_series(cfg, sid, 200)
            snap["fred"][sid] = {"label": label, "points": pts, "last": pts[-1][1], "asof": pts[-1][0],
                                 "chg_1m": pts[-1][1] - pts[max(0, len(pts) - 22)][1], "source": "FRED"}
        except Exception as e:
            snap["errors"].append(f"fred {sid}: {e}")
    return snap


KEY_RELEASES = {  # FRED release name -> short spoken name (only these are announced)
    "Consumer Price Index": "the consumer price index", "Employment Situation": "the US jobs report",
    "Gross Domestic Product": "the GDP report", "Personal Income and Outlays": "the PCE inflation report",
    "Producer Price Index": "producer prices", "Advance Monthly Sales for Retail and Food Services": "retail sales",
    "Job Openings and Labor Turnover Survey": "the job openings report", "FOMC Press Release": "the Fed rate decision",
}


def release_calendar(cfg, days=7):
    """Upcoming key US data releases from FRED's release calendar (needs FRED_API_KEY). [(weekday, name)]"""
    key = env("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY needed for release calendar")
    today = dt.date.today()
    def f():
        r = requests.get("https://api.stlouisfed.org/fred/releases/dates", timeout=20, params={
            "api_key": key, "file_type": "json", "realtime_start": today.isoformat(),
            "realtime_end": (today + dt.timedelta(days=days)).isoformat(),
            "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 1000})
        r.raise_for_status()
        return r.json().get("release_dates", [])
    out, seen = [], set()
    for x in _cached(f"fred_cal_{today}", 720, f):
        nm = KEY_RELEASES.get(x.get("release_name"))
        if nm and nm not in seen:
            seen.add(nm); out.append((dt.date.fromisoformat(x["date"]).strftime("%A"), nm))
    return out


# ---------------- US session freshness ----------------
NYSE_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15"}  # full-day closures (nyse.com)


def _is_session(d):
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def session_state(now_utc=None):
    """NYSE state in New York time: last completed session date, and whether a session is in progress today."""
    from zoneinfo import ZoneInfo
    ny = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    d, mins = ny.date(), ny.hour * 60 + ny.minute
    today_session = _is_session(d)
    closed_today = today_session and mins >= 16 * 60
    last = d if closed_today else d - dt.timedelta(days=1)
    while not _is_session(last):
        last -= dt.timedelta(days=1)
    return {"ny_date": d, "last_completed": last, "today_complete": closed_today,
            "in_session": today_session and 9 * 60 + 30 <= mins < 16 * 60}


def asof_words(asof_date, ref_date):
    """'Friday's close' within 6 days, else 'the October 23 close'."""
    if 0 <= (ref_date - asof_date).days <= 6:
        return f"{asof_date.strftime('%A')}'s close"
    return f"the {asof_date.strftime('%B')} {asof_date.day} close"


def mood(snap, topic=("crypto",)):
    """green / red / neutral from the relevant 24h move."""
    vals = []
    if "crypto" in topic and "coins" in snap:
        vals.append(snap["coins"]["bitcoin"]["chg_24h"])
    if ("stocks" in topic or "macro" in topic) and "^GSPC" in snap.get("indices", {}):
        vals.append(snap["indices"]["^GSPC"]["chg_1d"])
    if not vals:
        return "neutral", 0.0
    v = sum(vals) / len(vals)
    return ("green" if v > 0.35 else "red" if v < -0.35 else "neutral"), v
