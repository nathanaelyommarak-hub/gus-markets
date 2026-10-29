#!/usr/bin/env python3
"""Cheap pre-check for scheduled runs (needs only PyYAML): is any active slot due (incl. catch-up of late/dropped
GitHub cron runs, see config.due_slot) and not yet uploaded?
Writes due=true|false, slot and slot_date to $GITHUB_OUTPUT so the heavy install/render steps can be skipped."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finshorts.config import load_config, due_slot
from finshorts import dedupe

cfg = load_config()
s, why = due_slot(cfg, is_uploaded=lambda j: dedupe.slot_done(cfg, j), last_upload_ts=dedupe.last_upload_ts())
print(why)
if os.environ.get("GITHUB_OUTPUT"):
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"due={'true' if s else 'false'}\nslot={s['id'] if s else ''}\nslot_date={s['day'] if s else ''}\n")
