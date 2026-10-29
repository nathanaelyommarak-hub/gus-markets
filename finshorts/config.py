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


def slot_job_id(slot, day):
    return f"{day:%Y%m%d}_{slot['id']}_{slot['format']}"


def due_slot(cfg, now=None, is_uploaded=None, last_upload_ts=None):
    """Catch-up scheduler. GitHub cron runs are often 30-60 min late or dropped entirely, so every run picks ANY
    active slot whose time has passed within `schedule.catch_up.max_late_min` and whose job_id
    ("YYYYMMDD_sNN_format", date = the slot's own UTC day, so a 21:15 slot caught after midnight keeps its day)
    is not uploaded yet. At most one slot per run, and none if the last upload was less than
    `catch_up.min_gap_min` ago (no flood after an outage). If several are missed, the most recently due slot
    wins (freshest content); older ones follow in later runs while still within max_late_min.
    is_uploaded(job_id) -> bool; last_upload_ts = unix ts of the newest upload (any platform) or None.
    Returns (slot dict incl. day/job_id/late_min, or None; reason)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cu = cfg["schedule"].get("catch_up") or {}
    max_late = float(cu.get("max_late_min", 180)) * 60
    min_gap = float(cu.get("min_gap_min", 30)) * 60
    cands, done = [], []
    for day in (now.date() - dt.timedelta(days=1), now.date()):
        for s in cfg["schedule"]["slots"][: videos_per_day(cfg, day)]:
            h, m = map(int, s["utc"].split(":"))
            t = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=dt.timezone.utc)
            late = (now - t).total_seconds()
            if not (0 <= late <= max_late):
                continue
            es = effective_slot(cfg, s, day)
            if not es:
                continue
            jid = slot_job_id(es, day)
            if is_uploaded and is_uploaded(jid):
                done.append(jid); continue
            cands.append({**es, "day": day.isoformat(), "job_id": jid, "late_min": round(late / 60)})
    if not cands:
        return None, (f"no active slot pending at {now:%H:%M} UTC (videos/day today = {videos_per_day(cfg, now.date())}"
                      + (f"; already uploaded: {', '.join(done)}" if done else "") + ")")
    if last_upload_ts and now.timestamp() - last_upload_ts < min_gap:
        ago = round((now.timestamp() - last_upload_ts) / 60)
        return None, (f"pending {', '.join(c['job_id'] for c in cands)} but last upload was {ago} min ago "
                      f"(< {min_gap / 60:.0f} min gap) -> next run")
    c = min(cands, key=lambda c: c["late_min"])
    return c, f"slot {c['id']} ({c['format']}) due, {c['late_min']} min after slot time [{c['job_id']}]"


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
