"""Dedupe log: never upload the same video (or the same story twice in a row) to the same platform."""
import json, hashlib, re, time
from .config import STATE

LOG = STATE / "upload_log.json"


def _load():
    return json.loads(LOG.read_text()) if LOG.exists() else {"uploads": [], "stories": []}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def already_uploaded(platform, video_hash=None, job_id=None):
    for u in _load()["uploads"]:
        if u["platform"] == platform and (u["hash"] == video_hash or (job_id and u.get("job_id") == job_id)):
            return u
    return None


def record_upload(platform, video_hash, job_id, remote_id):
    d = _load()
    d["uploads"].append({"platform": platform, "hash": video_hash, "job_id": job_id, "remote_id": remote_id, "ts": time.time()})
    LOG.write_text(json.dumps(d, indent=1))


def _tok(title):
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 2}


def _sim(a, b):
    return len(a & b) / max(1, len(a | b))


def story_used_recently(story, hours=24 * 7, threshold=0.5):
    """True if this story (same id, or any headline of its cluster ~= a used headline) was already published.
    `story` may be a dict from news.verified_stories or a bare id. Window: 7 days (feeds only carry < 18 h old items)."""
    sid = story["id"] if isinstance(story, dict) else story
    titles = [_tok(t) for t in ((story.get("all_titles") or [story.get("headline")]) if isinstance(story, dict) else [])]
    for s in _load()["stories"]:
        if time.time() - s["ts"] >= hours * 3600:
            continue
        if s["id"] == sid:
            return True
        used = [set(t) for t in s.get("tokens", [])]
        if any(_sim(a, b) >= threshold for a in titles for b in used if a and b):
            return True
    return False


def record_story(story):
    sid = story["id"] if isinstance(story, dict) else story
    toks = [sorted(_tok(t)) for t in ((story.get("all_titles") or [story.get("headline")]) if isinstance(story, dict) else [])]
    d = _load()
    if any(s["id"] == sid for s in d["stories"][-50:]):
        return
    d["stories"].append({"id": sid, "headline": story.get("headline", "") if isinstance(story, dict) else "",
                                      "tokens": toks[:6], "ts": time.time()})
    d["stories"] = d["stories"][-500:]
    LOG.write_text(json.dumps(d, indent=1))
