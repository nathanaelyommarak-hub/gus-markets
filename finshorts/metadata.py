"""Per-platform title / description / caption / pinned comment (English). 3-4 hashtags max."""
TAGS = {
    "crypto": ["#bitcoin", "#crypto", "#cryptonews", "#ethereum"],
    "stocks": ["#stocks", "#stockmarket", "#investing", "#sp500"],
    "macro": ["#economy", "#interestrates", "#finance", "#fed"],
}
DISCLOSURE = ("Gus is a cartoon goldfish, not a financial adviser. Voice: synthetic (Kokoro TTS). Script: AI-assisted, "
              "numbers come only from the data sources listed. Animation: procedural. No sponsors, no affiliate links, no paid promotion.")


def hashtags(fmt, n):
    tags = []
    for i in range(4):  # interleave topics so a multi-topic format gets a mix
        for t in fmt["topic"]:
            if i < len(TAGS[t]) and TAGS[t][i] not in tags:
                tags.append(TAGS[t][i])
    return tags[:n]


def pinned_comment(cfg, poll, story=None):
    ch = cfg["channel"]
    q = poll.strip() or "Up or down from here?"
    txt = f"\U0001F4CA Your call: {q} Reply with one word.\n"
    if story:
        txt += "Sources: " + ", ".join(story["sources"][:3]) + ".\n"
    txt += f"{ch['disclaimer']} {ch['handle']} never DMs you and never asks for money or wallet keys."
    return txt


def build(cfg, fmt_name, title, spoken, story, sources_used, poll="", credits=()):
    ch = cfg["channel"]; fmt = cfg["formats"][fmt_name]
    n = int(cfg["upload"]["instagram"].get("max_hashtags", 4))
    tags = hashtags(fmt, n)
    src = "Market data: " + ", ".join(sorted(set(sources_used)))
    if "CoinGecko" in " ".join(sources_used):
        src += " (Price data by CoinGecko)"
    story_lines = ""
    if story:
        story_lines = "\nStory verified across: " + ", ".join(story["sources"][:4]) + "\n" + "\n".join(story["links"][:3])
    credit_lines = ("\n\nImage credits:\n" + "\n".join(credits)) if credits else ""
    base_title = title.strip().rstrip(".")
    yt_tags_line = " ".join(["#Shorts"] + tags[: n - 1])          # 4 hashtags total
    yt_desc = (f"{spoken}\n\n{ch['mascot']} the goldfish explains markets in under a minute. {ch['catchphrase']}\n\n"
               f"{src}{story_lines}{credit_lines}\n\n{ch['disclaimer']}\n{DISCLOSURE}\n\n{yt_tags_line}")
    ig_caption = (f"{base_title}\n\n{('Your call: ' + poll) if poll else ''}\n\n{src}{credit_lines}\n{ch['disclaimer']} "
                  f"Cartoon fish, synthetic voice, no sponsors.\n\n" + " ".join(tags[:n]))
    return {
        "youtube": {"title": base_title[:100], "description": yt_desc[:4900],
                    "tags": [t.lstrip("#") for t in tags] + ["shorts", "finance news", "gus markets"]},
        "instagram": {"caption": ig_caption[:2150]},
        "pinned_comment": pinned_comment(cfg, poll, story),
        "poll": poll,
    }
