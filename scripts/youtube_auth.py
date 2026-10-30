"""One-time: get a YouTube refresh token (run locally, browser opens).
Needs a Google Cloud OAuth client of type "Desktop app" -> download JSON as client_secret.json.
Prints YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN for GitHub Secrets.
IMPORTANT: set the OAuth consent screen to "In production" first, otherwise the refresh token expires after 7 days."""
import json, sys
from google_auth_oauthlib.flow import InstalledAppFlow

import os
SCOPES = ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl", "https://www.googleapis.com/auth/yt-analytics.readonly"]
path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
c = json.load(open(path))["installed"]
print("YT_CLIENT_ID=" + c["client_id"]); print("YT_CLIENT_SECRET=" + c["client_secret"])
print("YT_REFRESH_TOKEN=" + creds.refresh_token)
