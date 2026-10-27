#!/usr/bin/env python3
"""Cheap pre-check for scheduled runs (needs only PyYAML): is a slot due now and not yet uploaded?
Writes due=true|false (+ slot) to $GITHUB_OUTPUT so the heavy install/render steps can be skipped."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finshorts.config import load_config, slot_for_now, videos_per_day
from finshorts import dedupe

cfg = load_config()
now = dt.datetime.now(dt.timezone.utc)
s = slot_for_now(cfg, now)
due, why = False, ""
if not s:
    why = f"no active slot due at {now:%H:%M} UTC (videos/day today = {videos_per_day(cfg, now.date())})"
else:
    jid = f"{now:%Y%m%d}_{s['id']}_{s['format']}"
    plats = [p for p in ("youtube", "instagram") if cfg["upload"][p]["enabled"]]
    if plats and all(dedupe.already_uploaded(p, job_id=jid) for p in plats):
        why = f"slot {jid} already uploaded"
    else:
        due, why = True, f"slot {s['id']} ({s['format']}) due"
print(why)
if os.environ.get("GITHUB_OUTPUT"):
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"due={'true' if due else 'false'}\nslot={s['id'] if s else ''}\n")
