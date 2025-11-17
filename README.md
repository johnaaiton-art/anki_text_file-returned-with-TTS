# 🎧 TTS Anki Telegram Bot

A Telegram bot that:
- Accepts a tab-separated `.txt` file with English sentences, translations, and target phrases.
- Generates Google TTS audio for the target phrase (3rd column).
- Returns an updated Anki-ready `.txt` file + ZIP of MP3s.

## 🚀 Deployed on Railway
- Set these env vars in Railway:
  - `BOT_TOKEN` → from @BotFather
  - `GOOGLE_CREDENTIALS_JSON` → minified service account JSON

## 📁 File Format Example
