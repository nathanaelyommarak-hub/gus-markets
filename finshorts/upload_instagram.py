"""Instagram Reels publishing via the official Instagram Platform API.

mode=resumable : Instagram API with Facebook Login (graph.facebook.com) -> upload bytes to rupload.facebook.com,
                 no public hosting needed. Needs IG professional account linked to a FB Page.
mode=url       : Instagram API with Instagram Login (graph.instagram.com) -> video_url must be public (R2 / GitHub Release).
Rate limit: 100 API-published posts per IG account per rolling 24 h (check /content_publishing_limit).
"""
import os, time, requests
from .config import env
from . import hosting


def _host(mode):
    return "https://graph.facebook.com" if mode == "resumable" else "https://graph.instagram.com"


def publishing_quota(icfg):
    v, ig, tok = icfg["graph_version"], env("IG_USER_ID", required=True), env("IG_ACCESS_TOKEN", required=True)
    r = requests.get(f"{_host(icfg['mode'])}/{v}/{ig}/content_publishing_limit", timeout=30,
                     params={"fields": "quota_usage,config", "access_token": tok})
    r.raise_for_status()
    return r.json()


def upload(video_path, caption, icfg):
    v, ig, tok, mode = icfg["graph_version"], env("IG_USER_ID", required=True), env("IG_ACCESS_TOKEN", required=True), icfg["mode"]
    base = f"{_host(mode)}/{v}"
    q = publishing_quota(icfg).get("data", [{}])[0]
    if q.get("quota_usage", 0) >= q.get("config", {}).get("quota_total", 100):
        raise RuntimeError("Instagram 24h publishing limit reached")
    handle = None
    params = {"media_type": "REELS", "caption": caption, "share_to_feed": str(icfg.get("share_to_feed", True)).lower(),
              "access_token": tok}
    if mode == "resumable":
        params["upload_type"] = "resumable"
        c = requests.post(f"{base}/{ig}/media", data=params, timeout=60); c.raise_for_status()
        cid = c.json()["id"]
        size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            u = requests.post(f"https://rupload.facebook.com/ig-api-upload/{v}/{cid}", data=f, timeout=600,
                              headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)})
        u.raise_for_status()
    else:
        url, handle = hosting.host(video_path, icfg.get("hosting_provider", "github_release"))
        params["video_url"] = url
        c = requests.post(f"{base}/{ig}/media", data=params, timeout=60); c.raise_for_status()
        cid = c.json()["id"]
    try:
        for _ in range(60):                       # up to ~10 min processing
            s = requests.get(f"{base}/{cid}", params={"fields": "status_code,status", "access_token": tok}, timeout=30).json()
            if s.get("status_code") == "FINISHED": break
            if s.get("status_code") in ("ERROR", "EXPIRED"): raise RuntimeError(f"container {s}")
            time.sleep(10)
        p = requests.post(f"{base}/{ig}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
        p.raise_for_status()
        return p.json()["id"]
    finally:
        if handle and icfg.get("delete_after_publish", True):
            try: hosting.unhost(handle)
            except Exception: pass
