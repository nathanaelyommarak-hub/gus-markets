"""Dedupe log: never upload the same video (or the same story twice in a row) to the same platform."""
import json, hashlib, time
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


def story_used_recently(story_id, hours=36):
    return any(s["id"] == story_id and time.time() - s["ts"] < hours * 3600 for s in _load()["stories"])


def record_story(story_id):
    d = _load(); d["stories"].append({"id": story_id, "ts": time.time()}); d["stories"] = d["stories"][-500:]
    LOG.write_text(json.dumps(d, indent=1))
