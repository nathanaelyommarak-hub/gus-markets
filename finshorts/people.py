"""Person portraits for named people in a story - commercial-safe licenses ONLY.

Source: Wikimedia Commons / Wikipedia API (no scraping). Accepted: Public Domain (incl. US-government works),
CC0, CC BY, CC BY-SA. Rejected: anything NC / ND / fair use / unknown. US-government public-domain official
portraits are preferred. Every image is cached per person in assets/people/<slug>/ with meta.json holding
license, author, file page and the attribution line that is appended to the video description.
Background removal (rembg, MIT, model u2netp) is applied only to PD / CC0 / CC BY images - CC BY-SA images are
used as a plain framed crop so we don't create a share-alike derivative.  No image -> stylized silhouette card.
"""
import json, re, io, hashlib, time
from pathlib import Path
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "assets/people"
UA = {"User-Agent": "FinShortsBot/1.0 (Gus Markets; finance shorts; contact via GitHub repo) python-requests"}
WP, CM, WD = "https://en.wikipedia.org/w/api.php", "https://commons.wikimedia.org/w/api.php", "https://www.wikidata.org/w/api.php"

# surname -> full name (feeds usually say "Powell says ..."); full names in headlines are detected directly
ALIASES = {"powell": "Jerome Powell", "lagarde": "Christine Lagarde", "bessent": "Scott Bessent", "warsh": "Kevin Warsh",
           "hassett": "Kevin Hassett", "waller": "Christopher Waller", "musk": "Elon Musk", "huang": "Jensen Huang",
           "saylor": "Michael Saylor", "dimon": "Jamie Dimon", "buffett": "Warren Buffett", "trump": "Donald Trump",
           "ueda": "Kazuo Ueda", "nagel": "Joachim Nagel", "zuckerberg": "Mark Zuckerberg", "altman": "Sam Altman",
           "nadella": "Satya Nadella", "pichai": "Sundar Pichai", "bezos": "Jeff Bezos", "yellen": "Janet Yellen",
           "gensler": "Gary Gensler", "atkins": "Paul Atkins", "fink": "Larry Fink", "lutnick": "Howard Lutnick"}
NOT_NAMES = set("wall street new york white house federal reserve stock market bank of the us u s fed chair".split())
SAFE = re.compile(r"^(public domain|pd\b|pd-|cc0|cc[ -]by(?:[ -]sa)?[ -]?\d(\.\d)?|cc[ -]by(?:[ -]sa)?$)", re.I)
BAD = re.compile(r"\b(nc|nd|non-?commercial|no ?deriv|fair use|copyrighted)\b", re.I)


def _get(url, params):
    params = dict(params, format="json", formatversion=2)
    r = requests.get(url, params=params, headers=UA, timeout=20); r.raise_for_status()
    return r.json()


def license_ok(short):
    s = (short or "").strip()
    return bool(SAFE.search(s)) and not BAD.search(s)


def license_rank(short, title, credit):
    s = short.lower(); txt = f"{title} {credit}".lower()
    gov = any(k in txt for k in ("official portrait", "federal reserve", "white house", "u.s. government", "united states",
                                 "department of", "congress", "senate", ".gov", "official photo"))
    if s.startswith(("public domain", "pd")): return 0 if gov else 1
    if s.startswith("cc0"): return 2
    if "sa" not in s: return 3
    return 4


def find_people(story, max_n=1):
    """Names in the story (headline + titles + summary) that resolve to a human on Wikidata."""
    if not story: return []
    text = " ".join([story.get("headline", ""), story.get("summary", "")] + story.get("all_titles", []))
    cands = {}
    for m in re.finditer(r"\b([A-Z][a-z]+(?:[- ][A-Z][a-z]+){1,2})\b", text):
        n = m.group(1)
        if not set(n.lower().split()) <= NOT_NAMES: cands[n] = cands.get(n, 0) + 1
    for tok in re.findall(r"[A-Za-z]+", text):
        if tok.lower() in ALIASES and tok[0].isupper():
            full = ALIASES[tok.lower()]; cands[full] = cands.get(full, 0) + 1
    out = []
    for name, _ in sorted(cands.items(), key=lambda kv: -kv[1]):
        info = resolve(name)
        if info:
            out.append(info)
            if len(out) >= max_n: break
    return out


def resolve(name):
    """Wikipedia page -> Wikidata human check -> description (e.g. 'Chair of the Federal Reserve')."""
    try:
        q = _get(WP, {"action": "query", "titles": name, "redirects": 1, "prop": "pageprops|description|pageimages",
                      "piprop": "name", "ppprop": "wikibase_item|page_image_free|disambiguation"})
        pg = q["query"]["pages"][0]
        if pg.get("missing") or "disambiguation" in pg.get("pageprops", {}): return None
        qid = pg.get("pageprops", {}).get("wikibase_item")
        if not qid: return None
        e = _get(WD, {"action": "wbgetentities", "ids": qid, "props": "claims"})["entities"][qid]
        if not any(c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") == "Q5" for c in e["claims"].get("P31", [])):
            return None
        p18 = [c["mainsnak"]["datavalue"]["value"] for c in e["claims"].get("P18", []) if "datavalue" in c["mainsnak"]]
        return {"name": pg["title"], "qid": qid, "desc": pg.get("description", ""),
                "files": [f for f in [pg.get("pageprops", {}).get("page_image_free")] + p18 if f]}
    except Exception:
        return None


def _imageinfo(titles):
    q = _get(CM, {"action": "query", "titles": "|".join(titles), "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
                  "iiurlwidth": 900})
    return q["query"]["pages"]


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def portrait(person, cutout=True, log=None):
    """Return dict(img=RGBA PIL, cutout=bool, meta=...) or None. Cached per person."""
    slug = re.sub(r"[^a-z0-9]+", "_", person["name"].lower()).strip("_")
    d = CACHE / slug; mf = d / "meta.json"
    if mf.exists():
        meta = json.loads(mf.read_text())
        if meta.get("none"): return None
        f = d / ("cutout.png" if (d / "cutout.png").exists() else "photo.png")
        return {"img": Image.open(f).convert("RGBA"), "cutout": f.name == "cutout.png", "meta": meta}
    d.mkdir(parents=True, exist_ok=True)
    titles = ["File:" + f if not f.startswith("File:") else f for f in person.get("files", [])]
    try:   # plus a Commons search for official portraits
        s = _get(CM, {"action": "query", "list": "search", "srnamespace": 6, "srlimit": 8,
                      "srsearch": f'"{person["name"]}" official portrait'})
        titles += [r["title"] for r in s["query"]["search"]]
    except Exception:
        pass
    titles = list(dict.fromkeys(titles))[:20]
    best = None
    if titles:
        for pg in _imageinfo(titles):
            ii = (pg.get("imageinfo") or [{}])[0]; md = ii.get("extmetadata", {})
            short = _strip(md.get("LicenseShortName", {}).get("value", ""))
            if not ii.get("thumburl") or ii.get("mime") not in ("image/jpeg", "image/png") or not license_ok(short):
                continue
            if ii.get("width", 0) < 300 or ii.get("height", 0) < ii.get("width", 1) * 0.9:   # portrait-ish only
                continue
            title_l = pg["title"].lower()
            if not any(p.lower() in title_l for p in person["name"].split()[-1:]):           # must name the person
                continue
            credit = _strip(md.get("Credit", {}).get("value", ""))
            r = (license_rank(short, pg["title"], credit), titles.index(pg["title"]) if pg["title"] in titles else 99)
            if best is None or r < best[0]:
                best = (r, pg, ii, md, short, credit)
    if not best:
        mf.write_text(json.dumps({"none": True, "name": person["name"], "checked": time.strftime("%Y-%m-%d")}))
        return None
    _, pg, ii, md, short, credit = best
    raw = requests.get(ii["thumburl"], headers=UA, timeout=30).content
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.save(d / "photo.png")
    artist = _strip(md.get("Artist", {}).get("value", "")) or "unknown"
    meta = {"name": person["name"], "desc": person.get("desc", ""), "file": pg["title"],
            "file_page": ii.get("descriptionurl", ""), "license": short,
            "license_url": md.get("LicenseUrl", {}).get("value", ""), "artist": artist[:120], "credit": credit[:160],
            "sha1": hashlib.sha1(raw).hexdigest()}
    is_pd = short.lower().startswith(("public domain", "pd", "cc0"))
    meta["attribution"] = (f"Photo of {person['name']}: {artist[:80]}, {short}" + ("" if is_pd else f" ({meta['license_url']})")
                           + f", via Wikimedia Commons - {meta['file_page']}")
    cut = False
    if cutout and "sa" not in short.lower():      # no share-alike derivative
        try:
            from rembg import remove, new_session
            out = remove(im, session=new_session("u2netp"))
            if out.getchannel("A").getbbox():
                out.save(d / "cutout.png"); cut = True; meta["modified"] = "background removed"
        except Exception as e:
            if log is not None: log.append(f"rembg unavailable: {e}")
    mf.write_text(json.dumps(meta, indent=1))
    img = Image.open(d / ("cutout.png" if cut else "photo.png")).convert("RGBA")
    return {"img": img, "cutout": cut, "meta": meta}
