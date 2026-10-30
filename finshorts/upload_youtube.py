"""YouTube Data API v3 upload (videos.insert, resumable). Shorts = vertical, <= 3 min (we stay <= 90 s); #Shorts goes in the description."""
from .config import env

# Full scope set of the current refresh token (youtube, youtube.upload, youtube.force-ssl, yt-analytics.readonly).
# The refresh request must NOT down-scope the access token, otherwise captions.insert / thumbnails.set fail;
# scopes=None lets Google issue an access token with every scope the refresh token was granted.
SCOPES_FULL = ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.upload",
               "https://www.googleapis.com/auth/youtube.force-ssl", "https://www.googleapis.com/auth/yt-analytics.readonly"]
SCOPES = None


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN", required=True),
                        client_id=env("YT_CLIENT_ID", required=True), client_secret=env("YT_CLIENT_SECRET", required=True),
                        token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(video_path, meta, ycfg):
    from googleapiclient.http import MediaFileUpload
    yt = _service()
    body = {"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"][:15],
                        "categoryId": ycfg.get("category_id", "25"), "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": ycfg.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": bool(ycfg.get("contains_synthetic_media", True)), "embeddable": True}}
    req = yt.videos().insert(part="snippet,status", body=body, notifySubscribers=ycfg.get("notify_subscribers", False),
                             media_body=MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 << 20, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"], resp.get("status", {}).get("privacyStatus")


def _why(e):
    try:
        import json as _j
        err = _j.loads(e.content.decode())["error"]
        return f"{err.get('code')} {(err.get('errors') or [{}])[0].get('reason', '')}: {err.get('message', '')[:160]}"
    except Exception:
        return f"{type(e).__name__}: {str(e)[:160]}"


def set_thumbnail(video_id, jpg_path):
    """thumbnails.set (youtube.upload / youtube scope). Unverified channels get 403 -> returned as text, never raised."""
    try:
        from googleapiclient.http import MediaFileUpload
        _service().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(jpg_path, mimetype="image/jpeg")).execute()
        return "ok"
    except Exception as e:
        w = _why(e)
        if "403" in w or "forbidden" in w.lower():
            return ("not set: channel not allowed to upload custom thumbnails (verify the channel by phone at "
                    "youtube.com/verify) - " + w)
        return "not set: " + w


def insert_caption(video_id, srt_path, lang="en", name="English"):
    """captions.insert needs the youtube.force-ssl scope; with an upload-only token this returns a message, never raises."""
    try:
        from googleapiclient.http import MediaFileUpload
        _service().captions().insert(part="snippet", body={"snippet": {"videoId": video_id, "language": lang, "name": name,
                                                                        "isDraft": False}},
                                     media_body=MediaFileUpload(srt_path, mimetype="application/octet-stream")).execute()
        return "ok"
    except Exception as e:
        w = _why(e)
        if "insufficient" in w.lower() or "403" in w:
            return ("not uploaded: the refresh token lacks the youtube.force-ssl scope (re-run scripts/youtube_auth.py and update the YT_REFRESH_TOKEN secret) - " + w)
        return "not uploaded: " + w


def scope_check(video_id=None):
    """Dry-run-safe, read-only: refresh the token, report granted scopes, captions.list on an existing video."""
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN", required=True),
                        client_id=env("YT_CLIENT_ID", required=True), client_secret=env("YT_CLIENT_SECRET", required=True),
                        token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    creds.refresh(Request())
    info = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": creds.token}, timeout=30).json()
    granted = sorted(info.get("scope", "").split())
    out = {"granted": granted, "missing": [x for x in SCOPES_FULL if x not in granted]}
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    if not video_id:
        ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        video_id = yt.playlistItems().list(part="contentDetails", playlistId=up, maxResults=1).execute()["items"][0]["contentDetails"]["videoId"]
    try:
        caps = yt.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
        out["captions_list"] = f"ok ({len(caps)} track(s) on {video_id})"
    except Exception as e:
        out["captions_list"] = "FAILED " + _why(e)
    return out
