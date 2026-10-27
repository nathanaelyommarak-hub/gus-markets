"""Community automation after a successful upload. Everything is OFF by default (config.community.*).

- telegram_poll     : native poll in the Gus Markets Telegram channel (Bot API sendPoll; bot needs can_post_messages)
- youtube_comment   : comment on the new Short (commentThreads.insert = 50 quota units of the 10,000/day bucket;
                      needs OAuth scope youtube.force-ssl). The API cannot PIN comments -> pin manually in Studio/app.
- instagram_comment : POST /{ig-media-id}/comments (instagram_manage_comments). Pin manually in the app.
Failures never break the run; they are only logged.
"""
import re, requests
from .config import env


FIXED = {"winner or buy the loser": ["Chase the winner", "Buy the loser"], "release": ["Inflation", "Jobs", "Fed / rates", "GDP"],
         "explain next": ["Inflation", "ETFs", "Bond yields", "Market cap"], "myth should": ["Timing the market", "Cheap coins", "Gold always wins"],
         "or waiting": ["Buying", "Waiting"], "which coin": ["Bitcoin", "Ether", "Solana", "Something else"]}


def poll_options(q):
    ql = q.lower()
    for k, v in FIXED.items():
        if k in ql: return v
    if "up or down" in ql: return ["Up \u2B06\uFE0F", "Down \u2B07\uFE0F", "Sideways \u27A1\uFE0F"]
    if "yes or no" in ql: return ["Yes", "No"]
    m = re.search(r"\b(\w+) or (?:a |an |the )?(\w+)\b", q, re.I)
    if m and m.group(1).lower() != m.group(2).lower():
        return [m.group(1).capitalize(), m.group(2).capitalize(), "Not sure"]
    return ["Bullish", "Bearish", "Just here for the fish"]


def telegram_poll(cfg, question):
    tok, chat = env("TELEGRAM_BOT_TOKEN", required=True), env("TELEGRAM_COMMUNITY_CHAT_ID", required=True)
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendPoll", timeout=30, json={
        "chat_id": chat, "question": question[:300], "options": [{"text": o} for o in poll_options(question)],
        "is_anonymous": True})  # open_period max is 600 s -> polls stay open; close manually or via stopPoll
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def youtube_comment(video_id, text):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN", required=True), client_id=env("YT_CLIENT_ID", required=True),
                        client_secret=env("YT_CLIENT_SECRET", required=True), token_uri="https://oauth2.googleapis.com/token")
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    res = yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": video_id,
                                     "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
    return res["id"]


def instagram_comment(cfg, media_id, text):
    v = cfg["upload"]["instagram"]["graph_version"]
    host = "https://graph.facebook.com" if cfg["upload"]["instagram"]["mode"] == "resumable" else "https://graph.instagram.com"
    r = requests.post(f"{host}/{v}/{media_id}/comments", timeout=30,
                      data={"message": text, "access_token": env("IG_ACCESS_TOKEN", required=True)})
    r.raise_for_status()
    return r.json()["id"]


def publish(cfg, meta, log):
    c, out = cfg.get("community", {}), {}
    q = meta.get("poll") or "Up or down from here?"
    slot = log.get("job_id", "").split("_")[1] if "_" in log.get("job_id", "") else ""
    daily = c.get("telegram_poll") and slot == c.get("telegram_poll_slot", "s01")
    steps = [("telegram_poll", daily, lambda: telegram_poll(cfg, q))]
    yt = log.get("youtube"); ig = log.get("instagram")
    if isinstance(yt, dict):
        steps.append(("youtube_comment", c.get("youtube_comment"), lambda: youtube_comment(yt["id"], meta["pinned_comment"])))
    if isinstance(ig, dict):
        steps.append(("instagram_comment", c.get("instagram_comment"), lambda: instagram_comment(cfg, ig["id"], meta["pinned_comment"])))
    for name, on, fn in steps:
        if not on: continue
        try:
            out[name] = fn()
        except Exception as e:  # never fail the run for community extras
            out[name] = f"error: {type(e).__name__}: {str(e)[:150]}"
    return out
