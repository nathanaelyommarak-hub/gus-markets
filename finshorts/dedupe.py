"""Dedupe log: never upload the same video (or the same story twice in a row) to the same platform."""
import json, hashlib, re, time
from .config import STATE

LOG = STATE / "upload_log.json"
LONG_LOG = STATE / "long_log.json"     # daily long-form video: own file -> no rebase conflicts with the Shorts workflow


def _load(log=None):
    log = log or LOG
    return json.loads(log.read_text()) if log.exists() else {"uploads": [], "stories": []}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def already_uploaded(platform, video_hash=None, job_id=None, log=None):
    for u in _load(log)["uploads"]:
        if u["platform"] == platform and (u["hash"] == video_hash or (job_id and u.get("job_id") == job_id)):
            return u
    return None


def record_upload(platform, video_hash, job_id, remote_id, log=None, **extra):
    d = _load(log)
    d["uploads"].append({"platform": platform, "hash": video_hash, "job_id": job_id, "remote_id": remote_id, "ts": time.time(), **extra})
    (log or LOG).write_text(json.dumps(d, indent=1))


def _tok(title):
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 2}


def _sim(a, b):
    return len(a & b) / max(1, len(a | b))


def story_used_recently(story, hours=24 * 7, threshold=0.5, log=None):
    """True if this story (same id, or any headline of its cluster ~= a used headline) was already published.
    `story` may be a dict from news.verified_stories or a bare id. Window: 7 days (feeds only carry < 18 h old items)."""
    sid = story["id"] if isinstance(story, dict) else story
    titles = [_tok(t) for t in ((story.get("all_titles") or [story.get("headline")]) if isinstance(story, dict) else [])]
    for s in _load(log)["stories"]:
        if time.time() - s["ts"] >= hours * 3600:
            continue
        if s["id"] == sid:
            return True
        used = [set(t) for t in s.get("tokens", [])]
        if any(_sim(a, b) >= threshold for a in titles for b in used if a and b):
            return True
    return False


def record_story(story, log=None):
    sid = story["id"] if isinstance(story, dict) else story
    toks = [sorted(_tok(t)) for t in ((story.get("all_titles") or [story.get("headline")]) if isinstance(story, dict) else [])]
    d = _load(log)
    if any(s["id"] == sid for s in d["stories"][-50:]):
        return
    d["stories"].append({"id": sid, "headline": story.get("headline", "") if isinstance(story, dict) else "",
                                      "tokens": toks[:6], "ts": time.time()})
    d["stories"] = d["stories"][-500:]
    (log or LOG).write_text(json.dumps(d, indent=1))


def last_upload_ts(platforms=None):
    """Unix ts of the newest recorded upload (optionally only these platforms), or None."""
    ts = [u.get("ts") or 0 for u in _load()["uploads"] if not platforms or u["platform"] in platforms]
    return max(ts) if ts else None


def slot_done(cfg, job_id):
    """A slot counts as done once its job_id was uploaded to any enabled platform."""
    plats = [p for p in ("youtube", "instagram") if cfg["upload"][p]["enabled"]]
    return bool(plats) and any(already_uploaded(p, job_id=job_id) for p in plats)
