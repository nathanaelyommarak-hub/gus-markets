#!/usr/bin/env python3
"""Cheap gate for the daily long-form workflow (needs only PyYAML): is today's Daily Update due and not yet uploaded?
Slot = config long.slot_utc (21:45 UTC = 22:45 Zurich winter). GitHub cron is often late or dropped, so catch-up crons
re-run this gate; it accepts runs up to long.catch_up_max_late_min after the slot (also past midnight: the run then
belongs to the previous UTC day). One video per day: state/long_log.json job_id <YYYYMMDD>_long_<format>.
Writes due / format / slot_date to $GITHUB_OUTPUT."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finshorts.config import load_config
from finshorts import dedupe


def due(cfg, now=None):
    lc = cfg["long"]; now = now or dt.datetime.now(dt.timezone.utc)
    hh, mm = map(int, lc["slot_utc"].split(":")); late = int(lc.get("catch_up_max_late_min", 240))
    for back in (0, 1):
        day = now.date() - dt.timedelta(days=back)
        slot = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=dt.timezone.utc)
        mins = (now - slot).total_seconds() / 60
        if -5 <= mins <= late:
            fmt = "weekend_update" if day.weekday() >= 5 else "daily_update"
            jid = f"{day:%Y%m%d}_long_{fmt}"
            if dedupe.already_uploaded("youtube", job_id=jid, log=dedupe.LONG_LOG):
                return None, f"{jid} already uploaded"
            return {"format": fmt, "day": day.isoformat(), "late_min": round(max(0, mins))}, f"{jid} due ({max(0, mins):.0f} min after slot)"
    return None, "no long-form slot due now"


if __name__ == "__main__":
    s, why = due(load_config())
    print(why)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"due={'true' if s else 'false'}\nformat={s['format'] if s else ''}\nslot_date={s['day'] if s else ''}\n")
