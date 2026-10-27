import os, datetime as dt
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = ROOT / "out"
CACHE = ROOT / ".cache"
for d in (STATE, OUT, CACHE):
    d.mkdir(exist_ok=True)


def load_config(path=None):
    path = Path(path or os.environ.get("FINSHORTS_CONFIG", ROOT / "config" / "config.yaml"))
    cfg = yaml.safe_load(path.read_text())
    if os.environ.get("CHANNEL_NAME"):
        cfg["channel"]["name"] = os.environ["CHANNEL_NAME"]
    if os.environ.get("CHANNEL_HANDLE"):
        cfg["channel"]["handle"] = os.environ["CHANNEL_HANDLE"]
    return cfg


def videos_per_day(cfg, today=None):
    """Active slots today. `schedule.phases` ([{from: date, per_day: n}, ...]) wins; before the first phase -> 0.
    Legacy linear ramp (start_date/start_per_day/target_per_day/ramp_days) is used only if no phases are set."""
    s = cfg["schedule"]
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if s.get("phases"):
        n = 0
        for p in sorted(s["phases"], key=lambda p: str(p["from"])):
            if today >= dt.date.fromisoformat(str(p["from"])):
                n = int(p["per_day"])
        return max(0, min(n, len(s["slots"])))
    start = dt.date.fromisoformat(str(s["start_date"]))
    days = max(0, (today - start).days)
    n = s["start_per_day"] + (s["target_per_day"] - s["start_per_day"]) * days / max(1, s["ramp_days"])
    return int(min(s["target_per_day"], max(s["start_per_day"], int(n))))


def slot_for_now(cfg, now=None, window_min=40):
    """Return the active slot whose UTC time is within `window_min` before now, else None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    active = cfg["schedule"]["slots"][: videos_per_day(cfg, now.date())]
    for s in active:
        h, m = map(int, s["utc"].split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if 0 <= (now - t).total_seconds() <= window_min * 60:
            return effective_slot(cfg, s, now.date())
    return None


def effective_slot(cfg, slot, day):
    """Apply weekend overrides (stock markets closed). Returns None if the slot is skipped that day."""
    wk = {5: "sat", 6: "sun"}.get(day.weekday())
    ov = (cfg["schedule"].get("weekend_overrides") or {}).get(wk, {}) if wk else {}
    f = ov.get(slot["format"], slot["format"])
    return None if f == "skip" else {**slot, "format": f}


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"missing env var {name}")
    return v
