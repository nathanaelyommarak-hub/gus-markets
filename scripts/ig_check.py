"""Check Instagram token + remaining publish quota: IG_USER_ID=... IG_ACCESS_TOKEN=... python scripts/ig_check.py"""
import os, requests
v = "v26.0"; uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
print(requests.get(f"https://graph.facebook.com/{v}/{uid}", params={"fields": "username,account_type", "access_token": tok}, timeout=20).json())
print(requests.get(f"https://graph.facebook.com/{v}/{uid}/content_publishing_limit",
                   params={"fields": "config,quota_usage", "access_token": tok}, timeout=20).json())
