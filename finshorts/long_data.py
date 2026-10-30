"""Extra market data for the daily long-form video (on top of market.snapshot()). Official / free APIs only.

  Dow Jones   FRED DJIA (+ Alpha Vantage DIA-scaled latest close, same method as S&P / Nasdaq)
  Gold        Alpha Vantage GOLD_SILVER_HISTORY (daily XAU/USD)               - 1 call/day (cached 6 h)
  WTI oil     FRED DCOILWTICO (EIA spot, publishes with a few days lag -> always shown with its as-of date)
  US dollar   FRED DTWEXBGS = Fed nominal broad dollar index (NOT the ICE "DXY"; labelled as such)
  2Y yield    FRED DGS2
  Fed meeting federalreserve.gov FOMC calendar (official page, parsed; cached 12 h)
  BTC / ETH   CoinGecko 30-day and 7-day history
"""
import copy, re, datetime as dt
import requests
from . import market
from .config import env

UA = market.UA
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _av(params):
    key = env("ALPHAVANTAGE_API_KEY")
    if not key:
        raise RuntimeError("no ALPHAVANTAGE_API_KEY")
    r = requests.get("https://www.alphavantage.co/query", params={**params, "apikey": key}, timeout=25)
    j = r.json()
    if "data" not in j and "price" not in j:
        raise RuntimeError("Alpha Vantage: " + str(list(j.keys()))[:80])
    return j


def gold(cfg, days=120):
    j = market._cached("av_gold_hist_daily", 360, lambda: _av({"function": "GOLD_SILVER_HISTORY", "symbol": "GOLD", "interval": "daily"}))
    pts = sorted((d["date"][:10], float(d["price"])) for d in j["data"] if d.get("price"))
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    pts = [p for p in pts if p[0] >= cutoff]
    if len(pts) < 5:
        raise RuntimeError("gold history too short")
    return {"name": "Gold", "points": pts, "last": pts[-1][1], "prev": pts[-2][1],
            "chg_1d": (pts[-1][1] / pts[-2][1] - 1) * 100, "asof": pts[-1][0], "source": "Alpha Vantage (XAU/USD daily)"}


def fred_entry(cfg, sid, label, days=200, chg_n=22):
    pts = market.fred_series(cfg, sid, days)
    if len(pts) < 3:
        raise RuntimeError(f"{sid}: too few observations")
    return {"label": label, "points": pts, "last": pts[-1][1], "prev": pts[-2][1], "asof": pts[-1][0],
            "chg_1d": (pts[-1][1] / pts[-2][1] - 1) * 100,
            "chg_1m": pts[-1][1] - pts[max(0, len(pts) - chg_n)][1],
            "chg_1m_pct": (pts[-1][1] / pts[max(0, len(pts) - chg_n)][1] - 1) * 100, "source": "FRED"}


def fomc_meetings():
    """[(date_of_decision ISO, 'December 9')] from the Fed's official FOMC calendar page."""
    def f():
        r = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers=UA, timeout=25)
        r.raise_for_status()
        h = r.text; out = []
        for m in re.finditer(r"(\d{4}) FOMC Meetings", h):
            year = int(m.group(1)); seg = h[m.end(): m.end() + 20000]
            nxt = re.search(r"\d{4} FOMC Meetings", seg)
            seg = seg[: nxt.start()] if nxt else seg
            for mon, days in re.findall(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<", seg, re.S):
                mon = mon.split("/")[-1].strip(); d = re.findall(r"\d+", days)
                if mon in MONTHS and d:
                    out.append((dt.date(year, MONTHS.index(mon) + 1, int(d[-1])).isoformat(), "*" in days))
        return sorted(set(out))
    return market._cached("fed_fomc_calendar", 720, f)


def next_fomc(today=None):
    today = today or dt.date.today()
    for d, sep in fomc_meetings():
        dd = dt.date.fromisoformat(d)
        if dd >= today:
            return {"date": d, "words": f"{MONTHS[dd.month - 1]} {dd.day}", "weekday": dd.strftime("%A"),
                    "days": (dd - today).days, "sep": sep, "source": "federalreserve.gov FOMC calendar"}
    return None


def next_session(after):
    d = after + dt.timedelta(days=1)
    while not market._is_session(d):
        d += dt.timedelta(days=1)
    return d


def snapshot_long(cfg):
    snap = market.snapshot(cfg)
    c2 = copy.deepcopy(cfg)
    c2["data"]["stocks"]["indices"]["^DJI"] = {"name": "Dow Jones", "fred": "DJIA", "av": "DIA"}
    try:
        snap["indices"]["^DJI"] = market.index_history(c2, "^DJI", 40)
    except Exception as e:
        snap["errors"].append(f"^DJI: {e}")
    for sym in ("^GSPC", "^IXIC"):       # 3-month history for the focus charts
        try:
            snap.setdefault("indices90", {})[sym] = market.index_history(cfg, sym, 95)
        except Exception as e:
            snap["errors"].append(f"{sym} 90d: {e}")
    try:
        snap["gold"] = gold(cfg)
    except Exception as e:
        snap["errors"].append(f"gold: {e}")
    for sid, label in (("DCOILWTICO", "WTI crude oil (EIA spot)"), ("DGS2", "2-year Treasury yield"),
                       ("DTWEXBGS", "US dollar index (Fed broad)"), ("DGS10", "10-year Treasury yield"),
                       ("DFF", "Effective Fed funds rate")):
        try:
            snap["fred"][sid] = fred_entry(cfg, sid, label, 200, chg_n=22 if sid != "DTWEXBGS" else 21)
        except Exception as e:
            snap["errors"].append(f"fred {sid}: {e}")
    snap["hist"] = {}
    for cid in ("bitcoin", "ethereum"):
        for days in (30, 7):
            try:
                snap["hist"][f"{cid}{days}"] = market.coin_history(cfg, cid, days)
            except Exception as e:
                snap["errors"].append(f"{cid} {days}d: {e}")
    try:
        snap["fomc"] = next_fomc()
    except Exception as e:
        snap["errors"].append(f"fomc: {e}")
    return snap
