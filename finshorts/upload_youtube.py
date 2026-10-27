"""YouTube Data API v3 upload (videos.insert, resumable). Shorts = vertical, <= 3 min (we stay <= 40 s); #Shorts goes in the description."""
from .config import env

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


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
                       "containsSyntheticMedia": bool(ycfg.get("contains_synthetic_media", False)), "embeddable": True}}
    req = yt.videos().insert(part="snippet,status", body=body, notifySubscribers=ycfg.get("notify_subscribers", False),
                             media_body=MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 << 20, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"], resp.get("status", {}).get("privacyStatus")
