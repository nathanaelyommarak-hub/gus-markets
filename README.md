# finshorts – automatischer Finance-Shorts-Kanal mit „Gus“ 🐟

Vollautomatische englischsprachige Shorts/Reels (Krypto, Aktien, Makro), 0 € Budget.
Kanalname = Variable `CHANNEL_NAME` (GitHub → Settings → Variables).

## Ablauf (run.py)
1. **Daten**: CoinGecko (Krypto), FRED (Zinsen, Indizes), Alpha Vantage (optional) → `market.py`. Alle Zahlen kommen nur von hier.
2. **News**: 16 RSS-Feeds → Clustering → nur Storys mit ≥2 unabhängigen Quellen (verschiedene Eigentümer) → `news.py`.
3. **Skript**: LLM (Groq → Gemini → Ollama → Template-Fallback) schreibt nur Text mit Platzhaltern wie `{BTC_PRICE}`; `validate()` verwirft jede Ziffer bzw. jedes Zahlwort, das nicht aus einem Platzhalter stammt. Hook ≤ 2 s, 30–45 s, eigener Take, Catchphrase am Ende.
4. **TTS**: Kokoro-82M `am_michael` (Apache-2.0) mit Zeitstempeln pro Wort; Fallback Piper `en_US-john-medium` + faster-whisper.
5. **Video** 1080×1920/30 fps: Gus (grün/rot/neutral, Schnorchel an roten Tagen), Wasserstand nach Marktlage, Wippen, Blinzeln, Mundbewegung nach Audiopegel, matplotlib-Chart, eingebrannte Wort-Untertitel, Ticker, selbst generierte Musik (Ducking, -14 LUFS).
6. **Freigabe** (optional): Telegram-Video mit ✅/❌-Buttons, Timeout → überspringen.
7. **Upload**: YouTube `videos.insert` (resumable), Instagram Reels (resumable ohne Hosting, oder `video_url` über GitHub Release/R2). Dedupe-Log `state/upload_log.json` (Hash + Job-ID + Story-ID 36 h).

## Dateien
```
config/config.yaml      Kanal, Slots (10 Formate), Launch-Phasen (3/Tag ab 28.10., 10/Tag ab 11.11.2026), Feeds, LLM, TTS, Upload
run.py                  CLI: --auto | --slot s03 | --format quiz, --dry-run, --no-llm
finshorts/              market, news, script_gen, tts, gus, chart, music, render,
                        metadata, dedupe, telegram_approval, upload_youtube,
                        upload_instagram, hosting, config
scripts/youtube_auth.py Refresh-Token holen (einmalig, lokal)
scripts/telegram_chat_id.py, scripts/ig_check.py
.github/workflows/finshorts.yml   Cron pro Slot (UTC)
docs/LIMITS.md          geprüfte Quotas/Lizenzen mit Links
docs/YOUTUBE_AUDIT.md   Audit-Checkliste + Antwortentwürfe
tts_samples/            TTS-Vergleich (WAV+MP3)
```

## Setup
1. **Repo**: Ordner als **öffentliches** GitHub-Repo pushen (Actions dann unbegrenzt gratis). Secrets bleiben trotzdem geheim.
2. **Lokal testen**:
   ```bash
   sudo apt install ffmpeg espeak-ng
   python3.11 -m venv .venv && . .venv/bin/activate
   pip install --extra-index-url https://download.pytorch.org/whl/cpu torch==2.6.0+cpu
   pip install -r requirements.txt
   python run.py --format crypto_update --dry-run      # → out/<job>/<job>.mp4
   ```
3. **Gratis-Keys** (alle als GitHub Secrets, siehe `.env.example`):
   - `COINGECKO_API_KEY`: coingecko.com → Developer Dashboard → Demo-Key
   - `FRED_API_KEY`: fredaccount.stlouisfed.org → API Keys
   - `ALPHAVANTAGE_API_KEY` (optional, 25 Anfragen/Tag)
   - `GROQ_API_KEY`: console.groq.com, und/oder `GEMINI_API_KEY`: aistudio.google.com
4. **YouTube**:
   - console.cloud.google.com → neues Projekt → „YouTube Data API v3“ aktivieren.
   - OAuth-Zustimmungsbildschirm: Typ Extern, Datenschutz-URL eintragen, auf **„In Produktion“** stellen (sonst läuft der Token nach 7 Tagen ab).
   - Anmeldedaten → OAuth-Client „Desktop-App“ → JSON als `client_secret.json` speichern.
   - `python scripts/youtube_auth.py` ausführen und die 3 Werte als Secrets `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` speichern.
   - Ersten Upload prüfen: Ist er „privat (gesperrt)“, das Audit-Formular aus `docs/YOUTUBE_AUDIT.md` einreichen.
5. **Instagram**:
   - IG-Konto auf Professional (Creator/Business) umstellen und mit einer Facebook-Seite verknüpfen.
   - developers.facebook.com → App (Typ Business) → Produkt „Instagram“ mit **Facebook Login for Business**.
   - Berechtigungen: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, `pages_show_list`.
   - Im Graph API Explorer User-Token holen und in einen Long-Lived-Token (60 Tage) bzw. einen Page-Token tauschen. Dann `IG_USER_ID` (über `/me/accounts?fields=instagram_business_account`) und `IG_ACCESS_TOKEN` setzen.
   - Als App-Admin reicht Standard Access für das eigene Konto. App Review/Advanced Access nur für fremde Konten.
   - Mit `python scripts/ig_check.py` testen.
6. **Telegram** (optional): @BotFather → `/newbot` → `TELEGRAM_BOT_TOKEN`. Dem Bot schreiben, dann `scripts/telegram_chat_id.py` → `TELEGRAM_CHAT_ID`. In config `approval.telegram: true`.
7. **Variablen**: `CHANNEL_NAME`, `CHANNEL_HANDLE`. In `config.yaml`: `schedule.start_date` und `upload.*.enabled: true`.
8. Actions → finshorts → „Run workflow“ mit dry_run=true testen, danach läuft der Cron.

## Lizenzen
Kokoro-Gewichte und -Stimmen Apache-2.0 · Fonts OFL · Musik und Gus selbst generiert · CoinGecko-Attribution in Beschreibung und Video · Yahoo nur lokal/dev.

## Bekannte Lücken
- FRED-Indexdaten hinken 1–3 Handelstage hinterher. Für frische Kurse den Alpha-Vantage-Key setzen (ETF-Proxys SPY/QQQ, 25 Anfragen/Tag).
- LLM-Pfad noch nicht live getestet (ohne Keys lief der Template-Fallback).
- Kein echter Upload ausgeführt.
- Der Instagram-Token läuft ab (60 Tage), außer du nutzt einen System-User-Token (Business Manager).

## Gus Markets – Branding & Struktur (Stand 27.10.2026)
- Kanal: **Gus Markets**, Handle **@GusMarkets** (config `channel`, per env überschreibbar).
- Farben: Navy #0B1630, Mint #22E39B (up), Coral #FF4D5E (down), Gus-Orange #FF8A3D, Weiß. Fonts: Montserrat ExtraBold (Titel/Captions, statische Instanz wght 800), Inter (Text). OFL-Lizenzen in `assets/fonts/`.
- Videoaufbau (25–40 s): Frage-Hook ab Sekunde 0 → Zahlen → „Save this for later.“ + SAVE-THIS-Pill (~10–13 s) → eigene Quellenkarte („Verified story“, keine fremden Screenshots) → Gus' Take → Poll-Frage → „Already forgot the dip. Next.“
- Renderer: Ein-Wort-Captions in Großbuchstaben (Zahlen orange, Up-Wörter mint, Down-Wörter coral), Kapitelzeile oben (THE QUESTION / THE NUMBERS / THE PROOF / GUS'S TAKE / YOUR CALL), große Zahl + ▲/▼-Signal im Chart-Kopf, fester Disclaimer unten.
- Metadaten: max. 4 Hashtags, Offenlegung (Cartoon-Fisch, synthetische Stimme, keine Sponsoren), Pinned-Comment-Text in `metadata.json`.
- Sendeplan: 10 Slots, abends gewichtet, Wochenend-Overrides (`schedule.weekend_overrides`), Crons in `.github/workflows/finshorts.yml`.
- Community (`finshorts/community.py`, alles default aus): 1 Telegram-Poll/Tag (sendPoll, Slot s01), optional YT-Kommentar (50 Quota-Units, Scope youtube.force-ssl → `YT_WITH_COMMENTS=1 python scripts/youtube_auth.py`), optional IG-Kommentar. Anpinnen geht nur manuell.
- `macro_calendar` braucht `FRED_API_KEY` (FRED-Release-Kalender); ohne Key fällt das Format auf Zins-Kontext zurück.

## Sicherheitsschalter & Website

- **Geplante Läufe sind standardmäßig AUS.** Der Job in `.github/workflows/finshorts.yml` läuft per Cron nur, wenn die Repo-Variable `FINSHORTS_ENABLED` auf `true` steht (Settings → Secrets and variables → Actions → Variables). Manuelle Läufe (`workflow_dispatch`) sind standardmäßig `dry_run = true` (kein Upload).
- **Website / Privacy / Terms** (für die Google-OAuth-Prüfung): Quelle in `site/`, veröffentlicht über den Branch `gh-pages` → https://nathanaelyommarak-hub.github.io/gus-markets/
- Secrets (`secrets/`, `.env`, `client_secret*.json`, Token-Dateien) sind per `.gitignore` ausgeschlossen und gehören ausschließlich in GitHub Actions Secrets.
