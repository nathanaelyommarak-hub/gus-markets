"""Temporary public hosting for Instagram's video_url mode (only needed when instagram.mode = url)."""
import os, requests
from pathlib import Path
from .config import env


def host(path, provider):
    name = Path(path).name
    if provider == "r2":
        import boto3
        s3 = boto3.client("s3", endpoint_url=f"https://{env('R2_ACCOUNT_ID', required=True)}.r2.cloudflarestorage.com",
                          aws_access_key_id=env("R2_ACCESS_KEY_ID", required=True),
                          aws_secret_access_key=env("R2_SECRET_ACCESS_KEY", required=True), region_name="auto")
        s3.upload_file(path, env("R2_BUCKET", required=True), name, ExtraArgs={"ContentType": "video/mp4"})
        return env("R2_PUBLIC_BASE", required=True).rstrip("/") + "/" + name, ("r2", name)
    # GitHub Release asset on a "media" pre-release of this repo (free, public repo -> public URL)
    repo, tok = env("GITHUB_REPOSITORY", required=True), env("GITHUB_TOKEN", required=True)
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/media", headers=h, timeout=30)
    if r.status_code == 404:
        r = requests.post(f"https://api.github.com/repos/{repo}/releases", headers=h, timeout=30,
                          json={"tag_name": "media", "name": "media (temporary Reels hosting)", "prerelease": True})
    r.raise_for_status(); rel = r.json()
    with open(path, "rb") as f:
        a = requests.post(rel["upload_url"].split("{")[0], params={"name": name}, data=f, timeout=300,
                          headers={**h, "Content-Type": "video/mp4"})
    a.raise_for_status(); a = a.json()
    return a["browser_download_url"], ("gh", a["id"])


def unhost(handle):
    kind, ident = handle
    if kind == "r2":
        import boto3
        s3 = boto3.client("s3", endpoint_url=f"https://{env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                          aws_access_key_id=env("R2_ACCESS_KEY_ID"), aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
                          region_name="auto")
        s3.delete_object(Bucket=env("R2_BUCKET"), Key=ident)
    else:
        requests.delete(f"https://api.github.com/repos/{env('GITHUB_REPOSITORY')}/releases/assets/{ident}", timeout=30,
                        headers={"Authorization": f"Bearer {env('GITHUB_TOKEN')}"})
