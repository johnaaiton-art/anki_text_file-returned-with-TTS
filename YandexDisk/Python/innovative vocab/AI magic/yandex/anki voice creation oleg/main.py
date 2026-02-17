import os
import io
import uuid
import zipfile
import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.cloud import texttospeech
from google.oauth2 import service_account

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ── Google credentials from file ──────────────────────────────────────────────
CREDS_FILE = os.path.join(os.path.dirname(__file__), "google-creds.json")

def get_tts_client():
    creds = service_account.Credentials.from_service_account_file(
        CREDS_FILE,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return texttospeech.TextToSpeechClient(credentials=creds)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── TTS helper ────────────────────────────────────────────────────────────────
def synthesize_speech(text: str) -> bytes:
    """Return MP3 bytes for the given English text."""
    client = get_tts_client()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    return response.audio_content

# ── Core processing ───────────────────────────────────────────────────────────
def process_anki_file(file_bytes: bytes):
    """
    Parse tab-separated file with 3 columns:
      col1: gapped sentence
      col2: Russian translation
      col3: answer word(s)

    For each row:
      - Generate TTS MP3 for col3
      - Give it a random filename
      - Add [sound:filename.mp3] as col4

    Returns:
      - updated_txt_bytes: the 4-column tab-separated text
      - zip_bytes: zip archive containing all MP3 files
    """
    text = file_bytes.decode("utf-8", errors="replace")
    lines = text.strip().splitlines()

    updated_lines = []
    mp3_files = {}  # filename -> mp3 bytes

    for line in lines:
        line = line.strip()
        if not line:
            updated_lines.append(line)
            continue

        parts = line.split("\t")

        # Need at least 3 columns
        if len(parts) < 3:
            updated_lines.append(line)
            continue

        col1 = parts[0]
        col2 = parts[1]
        col3 = parts[2]

        # Generate TTS for the answer word (col3)
        try:
            mp3_bytes = synthesize_speech(col3.strip())
            filename = f"{uuid.uuid4().hex}.mp3"
            mp3_files[filename] = mp3_bytes
            sound_tag = f"[sound:{filename}]"
            logger.info(f"Generated TTS for: {col3.strip()} -> {filename}")
        except Exception as e:
            logger.error(f"TTS failed for '{col3}': {e}")
            sound_tag = f"[TTS_ERROR: {e}]"

        updated_lines.append(f"{col1}\t{col2}\t{col3}\t{sound_tag}")

    # Build updated txt
    updated_txt = "\n".join(updated_lines) + "\n"
    updated_txt_bytes = updated_txt.encode("utf-8")

    # Build zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, mp3_data in mp3_files.items():
            zf.writestr(filename, mp3_data)
    zip_bytes = zip_buffer.getvalue()

    return updated_txt_bytes, zip_bytes

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a .txt file with 3 tab-separated columns:\n\n"
        "1. Gapped sentence\n"
        "2. Russian translation\n"
        "3. Answer word(s)\n\n"
        "I'll generate TTS audio for each answer word and send back:\n"
        "- Updated .txt with Anki [sound:] tags in column 4\n"
        "- A .zip file with all the MP3s"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Please send a .txt file.")
        return

    await update.message.reply_text("Processing your file, please wait...")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()

        updated_txt, zip_bytes = process_anki_file(bytes(file_bytes))

        await update.message.reply_document(
            document=io.BytesIO(updated_txt),
            filename="anki_updated.txt",
            caption="Updated file with [sound:] tags in column 4",
        )

        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename="anki_audio.zip",
            caption="MP3 audio files - import into Anki media folder",
        )

        logger.info(f"Successfully processed {doc.file_name}")

    except Exception as e:
        logger.error(f"Error processing file: {e}")
        await update.message.reply_text(f"Error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env file.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)

    logger.info("Anki TTS Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
