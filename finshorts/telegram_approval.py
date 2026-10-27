"""1-click approval via Telegram Bot API (free): sends the MP4 with Approve / Reject inline buttons, waits for a tap."""
import json, time, requests
from .config import env

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, **kw):
    r = requests.post(API.format(token=env("TELEGRAM_BOT_TOKEN", required=True), method=method), timeout=120, **kw)
    r.raise_for_status()
    return r.json()["result"]


def request_approval(video_path, caption, job_id, timeout_minutes=25, on_timeout="skip"):
    chat = env("TELEGRAM_CHAT_ID", required=True)
    kb = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"ok:{job_id}"},
                               {"text": "❌ Reject", "callback_data": f"no:{job_id}"}]]}
    # drain old updates so we only react to new taps
    upd = _call("getUpdates", data={"timeout": 0})
    offset = (upd[-1]["update_id"] + 1) if upd else None
    with open(video_path, "rb") as f:   # bots may upload files up to 50 MB
        msg = _call("sendVideo", data={"chat_id": chat, "caption": caption[:1000], "supports_streaming": True,
                                       "reply_markup": json.dumps(kb)}, files={"video": f})
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        ups = _call("getUpdates", data={"timeout": 50, "offset": offset, "allowed_updates": json.dumps(["callback_query"])})
        for u in ups:
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq or not cq.get("data", "").endswith(job_id):
                continue
            ok = cq["data"].startswith("ok:")
            _call("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": "Approved" if ok else "Rejected"})
            _call("editMessageCaption", data={"chat_id": chat, "message_id": msg["message_id"],
                                              "caption": ("✅ APPROVED\n" if ok else "❌ REJECTED\n") + caption[:900]})
            return ok
    _call("sendMessage", data={"chat_id": chat, "text": f"⏱ No answer for {job_id} -> {on_timeout}"})
    return on_timeout == "approve"
