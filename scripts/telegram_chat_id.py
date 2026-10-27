"""Send any message to your bot first, then: TELEGRAM_BOT_TOKEN=... python scripts/telegram_chat_id.py"""
import os, requests
r = requests.get(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getUpdates", timeout=20).json()
for u in r.get("result", []):
    m = u.get("message") or {}
    if m: print("TELEGRAM_CHAT_ID=", m["chat"]["id"], m["chat"].get("username"))
