# Verified limits & terms (checked 2026-10-27)

| Service | Free limit | Notes | Source |
|---|---|---|---|
| YouTube Data API v3 | `videos.insert`: own bucket, **100 calls/day** (since 2026-06-01). All other methods: 10,000 units/day. Reset at midnight Pacific Time. | Upload cost was cut from ~1600 to ~100 units on 2025-12-04, then moved into its own bucket. The videos.insert page (updated 2026-10-15) says uploads from unverified projects are *no longer* locked to private. The 2020 revision-history entry still describes the private lock → **test with the first upload**, keep the audit (YOUTUBE_AUDIT.md) ready. OAuth consent screen in "Testing" → refresh token expires after 7 days → set it to "In production". | https://developers.google.com/youtube/v3/determine_quota_cost · https://developers.google.com/youtube/v3/docs/videos/insert · https://developers.google.com/youtube/v3/revision_history |
| Instagram Graph API | **100 API-published posts / rolling 24 h** (carousel = 1). Query `/{ig-id}/content_publishing_limit`. | Professional account (Business or Creator). Containers expire after 24 h. Resumable upload (`rupload.facebook.com`, no hosting) only with *Facebook Login for Business* (instagram_basic, instagram_content_publish, pages_read_engagement, Page link). *Business Login for Instagram* (graph.instagram.com) needs a public `video_url`. Advanced Access requires App Review; your own account works in Standard Access/dev mode as app admin. | https://developers.facebook.com/docs/instagram-platform/content-publishing |
| GitHub Actions | **Public repo: free & unlimited** (standard runners). Private (Free plan): 2,000 min/month, 500 MB artifacts. | Cost per run ≈ 3–4 min (install + ~75 s render) → 10/day ≈ 1,100 min/month → too much for a private repo unless the env is cached; **use a public repo**. Scheduled jobs can start late. | https://docs.github.com/en/billing/concepts/product-billing/github-actions |
| CoinGecko Demo | 100 calls/min (per the docs page), 10k credits/month | Attribution "Data provided by CoinGecko" is required (in the description + on screen). ~6 calls/video → 10/day ≈ 1,800/month. | https://www.coingecko.com/en/api/pricing · https://www.coingecko.com/en/api_terms |
| FRED | 120 requests/min, free key | Index series (SP500, NASDAQCOM) carry third-party copyright (S&P Dow Jones Indices / Nasdaq), and the data lags 1–3 trading days. Treasury/Fed series are public domain. | https://fred.stlouisfed.org/docs/api/terms_of_use.html |
| Alpha Vantage | 25 requests/day | Fresher index levels (ETF proxies). | https://www.alphavantage.co/support/#api-key |
| Yahoo chart endpoint | unofficial | Terms: personal use only → **dev fallback only**; skipped automatically on GitHub Actions (CI=true). | https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html |
| Groq (free) | openai/gpt-oss-120b: 30 RPM, 1K RPD, 8K TPM, 200K TPD | ~1.5K tokens/script → 10/day is fine. | https://console.groq.com/docs/rate-limits |
| Gemini API (free) | Per project, shown in AI Studio | Free-tier data may be used to improve Google's products. New projects: current Flash-Lite/Flash models. | https://ai.google.dev/gemini-api/docs/rate-limits |
| Cloudflare R2 (optional) | 10 GB-month, 1M Class A, 10M Class B, free egress | Activation probably needs a payment method → default is GitHub Release or resumable upload. | https://developers.cloudflare.com/r2/pricing/ |
| Telegram Bot API | free; bot upload ≤ 50 MB | Videos are ~5 MB. | https://core.telegram.org/bots/api#sendvideo |

## Licenses of the components
- Kokoro-82M weights + voices: Apache-2.0 (https://huggingface.co/hexgrad/Kokoro-82M). G2P misaki: MIT; espeak-ng fallback is GPL-3, only called as a separate library/process.
- Fonts Montserrat ExtraBold (static instance of the variable font, wght 800), Inter (Anton legacy): SIL OFL 1.1 (assets/fonts/*.txt).
- Music: synthesized at runtime with numpy (finshorts/music.py) → no third-party rights, no Content-ID risk.
- Gus: drawn procedurally in code (finshorts/gus.py) → own IP.
