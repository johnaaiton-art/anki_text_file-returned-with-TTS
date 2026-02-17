import os
import json
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.cloud import texttospeech
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ── Google credentials loaded from file (not env JSON string) ────────────────
CREDS_FILE = os.path.join(os.path.dirname(__file__), "google-creds.json")

def get_google_credentials():
    """Load Google service account credentials from google-creds.json."""
    if not os.path.exists(CREDS_FILE):
        raise FileNotFoundError(
            f"google-creds.json not found at {CREDS_FILE}. "
            "Please place your service account JSON file there."
        )
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/cloud-platform",
    ]
    creds = service_account.Credentials.from_service_account_file(
        CREDS_FILE, scopes=scopes
    )
    return creds

# ── Google clients ────────────────────────────────────────────────────────────
def get_tts_client():
    creds = get_google_credentials()
    return texttospeech.TextToSpeechClient(credentials=creds)

def get_sheets_service():
    creds = get_google_credentials()
    return build("sheets", "v4", credentials=creds)

# ── Config ────────────────────────────────────────────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SHEET_NAME     = os.getenv("SHEET_NAME", "Sheet1")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── TTS helper ────────────────────────────────────────────────────────────────
def synthesize_speech(text: str, lang: str = "en-US") -> bytes:
    """Return MP3 audio bytes for the given text."""
    client = get_tts_client()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang,
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    return response.audio_content

# ── Sheets helper ─────────────────────────────────────────────────────────────
def append_to_sheet(values: list):
    """Append a row to the configured Google Sheet."""
    if not SPREADSHEET_ID:
        logger.warning("SPREADSHEET_ID not set — skipping sheet append.")
        return
    service = get_sheets_service()
    body = {"values": [values]}
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send me any English text and I'll return an MP3 audio file!\n"
        "The text will also be saved to Google Sheets."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    await update.message.reply_text("🎙️ Generating audio…")

    try:
        # 1. Synthesise speech
        audio_bytes = synthesize_speech(text)

        # 2. Send as voice note / audio file
        await update.message.reply_audio(
            audio=audio_bytes,
            filename="tts_output.mp3",
            caption=f"📝 {text[:100]}{'…' if len(text) > 100 else ''}",
        )

        # 3. Save to Google Sheets
        append_to_sheet([text])
        logger.info(f"Processed: {text[:60]}")

    except Exception as e:
        logger.error(f"Error processing text: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env file.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("Bot started. Polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
