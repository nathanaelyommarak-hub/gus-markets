"""CI check (read-only, no upload): token scopes + captions.list on the newest channel video. Never prints secrets."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from finshorts.upload_youtube import scope_check
try:
    r = scope_check(sys.argv[1] if len(sys.argv) > 1 else None)
except Exception as e:
    print(f"::warning title=youtube-scopes::check failed: {type(e).__name__}: {str(e)[:200]}"); sys.exit(0)
short = [x.rsplit("/", 1)[-1] for x in r["granted"]]
lvl = "notice" if not r["missing"] and r["captions_list"].startswith("ok") else "warning"
print(f"::{lvl} title=youtube-scopes::granted={','.join(short)} missing={[x.rsplit('/',1)[-1] for x in r['missing']]} captions.list={r['captions_list']}")
