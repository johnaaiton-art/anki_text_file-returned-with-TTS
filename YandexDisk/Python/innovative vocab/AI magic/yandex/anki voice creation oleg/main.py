import os
import io
import uuid
import zipfile
import logging
import requests

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.cloud import texttospeech
from google.oauth2 import service_account

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

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

# ── DeepSeek helper ───────────────────────────────────────────────────────────
def get_russian_translation(english_word: str) -> str:
    """Get Russian translation(s) from DeepSeek API."""
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set - returning placeholder")
        return "[NO_API_KEY]"
    
    prompt = (
        f"Translate this English word or phrase to Russian: '{english_word}'. "
        "Provide 1-2 common Russian equivalents. If multiple, separate with /. "
        "Reply with ONLY the Russian translation(s), no other text."
    )
    
    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        translation = data["choices"][0]["message"]["content"].strip()
        logger.info(f"DeepSeek translation: {english_word} -> {translation}")
        return translation
    except Exception as e:
        logger.error(f"DeepSeek API error for '{english_word}': {e}")
        return f"[ERROR: {str(e)[:30]}]"

# ── Core processing - FILE (3-column gapped format) ──────────────────────────
def process_file_3column(file_bytes: bytes):
    """
    Process uploaded .txt file with 3 columns:
      col1: gapped sentence
      col2: Russian translation
      col3: answer word(s)
    
    Returns: (updated_txt_bytes, zip_bytes)
    """
    text = file_bytes.decode("utf-8", errors="replace")
    lines = text.strip().splitlines()

    updated_lines = []
    mp3_files = {}

    for line in lines:
        line = line.strip()
        if not line:
            updated_lines.append(line)
            continue

        parts = line.split("\t")

        if len(parts) < 3:
            updated_lines.append(line)
            continue

        col1, col2, col3 = parts[0], parts[1], parts[2]

        try:
            mp3_bytes = synthesize_speech(col3.strip())
            filename = f"{uuid.uuid4().hex}.mp3"
            mp3_files[filename] = mp3_bytes
            sound_tag = f"[sound:{filename}]"
            logger.info(f"TTS: {col3.strip()} -> {filename}")
        except Exception as e:
            logger.error(f"TTS failed for '{col3}': {e}")
            sound_tag = f"[TTS_ERROR]"

        updated_lines.append(f"{col1}\t{col2}\t{col3}\t{sound_tag}")

    updated_txt = "\n".join(updated_lines) + "\n"
    updated_txt_bytes = updated_txt.encode("utf-8")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, mp3_data in mp3_files.items():
            zf.writestr(filename, mp3_data)
    zip_bytes = zip_buffer.getvalue()

    return updated_txt_bytes, zip_bytes

# ── Core processing - TEXT (word list) ───────────────────────────────────────
def process_text_wordlist(text: str):
    """
    Process pasted word list (one word/phrase per line).
    For each word:
      - Get Russian translation from DeepSeek
      - Generate TTS
      - Format: Russian | English | [sound:xxx.mp3]
    
    Returns: (updated_txt_bytes, zip_bytes)
    """
    lines = text.strip().splitlines()
    
    updated_lines = []
    mp3_files = {}

    for line in lines:
        word = line.strip()
        if not word:
            continue

        # Get Russian translation
        russian = get_russian_translation(word)

        # Generate TTS
        try:
            mp3_bytes = synthesize_speech(word)
            filename = f"{uuid.uuid4().hex}.mp3"
            mp3_files[filename] = mp3_bytes
            sound_tag = f"[sound:{filename}]"
            logger.info(f"Word: {word} | RU: {russian} | TTS: {filename}")
        except Exception as e:
            logger.error(f"TTS failed for '{word}': {e}")
            sound_tag = f"[TTS_ERROR]"

        updated_lines.append(f"{russian}\t{word}\t{sound_tag}")

    updated_txt = "\n".join(updated_lines) + "\n"
    updated_txt_bytes = updated_txt.encode("utf-8")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, mp3_data in mp3_files.items():
            zf.writestr(filename, mp3_data)
    zip_bytes = zip_buffer.getvalue()

    return updated_txt_bytes, zip_bytes

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Anki TTS Bot - Two Modes*\n\n"
        "*Mode 1: Upload .txt file (3 columns)*\n"
        "  • Gapped sentence | Russian | Answer word\n"
        "  • Returns: 4-column file with TTS tags\n\n"
        "*Mode 2: Paste word list*\n"
        "  • One word/phrase per line\n"
        "  • Auto-translates to Russian via DeepSeek\n"
        "  • Returns: Russian | English | TTS\n\n"
        "Both modes return:\n"
        "  ✅ Updated .txt file\n"
        "  🎵 .zip with MP3s",
        parse_mode="Markdown"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded .txt file (3-column format)"""
    doc = update.message.document

    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("⚠️ Please send a .txt file.")
        return

    await update.message.reply_text("⏳ Processing file (3-column mode)...")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()

        updated_txt, zip_bytes = process_file_3column(bytes(file_bytes))

        await update.message.reply_document(
            document=io.BytesIO(updated_txt),
            filename="anki_updated.txt",
            caption="✅ 3-column format with TTS tags in column 4",
        )

        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename="anki_audio.zip",
            caption="🎵 MP3 files",
        )

        logger.info(f"Processed file: {doc.file_name}")

    except Exception as e:
        logger.error(f"Error processing file: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pasted word list"""
    text = update.message.text.strip()
    
    if not text:
        return

    # Ignore commands
    if text.startswith("/"):
        return

    await update.message.reply_text("⏳ Processing word list (translation mode)...")

    try:
        updated_txt, zip_bytes = process_text_wordlist(text)

        await update.message.reply_document(
            document=io.BytesIO(updated_txt),
            filename="anki_vocab.txt",
            caption="✅ Russian | English | TTS",
        )

        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename="anki_audio.zip",
            caption="🎵 MP3 files",
        )

        logger.info(f"Processed word list ({len(text.splitlines())} words)")

    except Exception as e:
        logger.error(f"Error processing word list: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("Anki TTS Bot started (dual-mode)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
