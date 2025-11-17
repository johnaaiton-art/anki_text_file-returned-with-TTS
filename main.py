import os
import re
import zipfile
import json
from io import StringIO, BytesIO
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, ContextTypes
from google.cloud import texttospeech
from google.oauth2.service_account import Credentials

# --- Configuration ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

def get_tts_client():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict)
    return texttospeech.TextToSpeechClient(credentials=creds)

def sanitize_filename(text: str) -> str:
    # Keep it safe for filenames and Anki
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\s,]+', '_', text.strip())
    return f"{clean}.mp3"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📤 Send a .txt file with lines like:\n"
        "If you ____ ___ ____ positive people...\\tпроводить много времени с кем-то\\thang out with\n\n"
        "I'll generate TTS for the 3rd column and return an Anki-ready file + audio ZIP!"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Please send a .txt file.")
        return

    try:
        file = await update.message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        content = file_bytes.decode("utf-8")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to read file: {str(e)}")
        return

    lines = content.strip().split("\n")
    updated_lines = []
    audio_files = {}  # {filename: mp3_bytes}

    client = get_tts_client()

    for i, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) < 3:
            await update.message.reply_text(f"⚠️ Line {i+1} skipped: invalid format (needs 3 columns).")
            updated_lines.append(line)
            continue

        col3 = parts[2].strip()
        if not col3:
            updated_lines.append(line)
            continue

        # Generate unique but readable filename
        base_name = sanitize_filename(col3)
        # Ensure uniqueness in case of duplicates
        counter = 1
        audio_filename = base_name
        while audio_filename in audio_files:
            name, ext = os.path.splitext(base_name)
            audio_filename = f"{name}_{counter}{ext}"
            counter += 1

        try:
            synthesis_input = texttospeech.SynthesisInput(text=col3)
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Standard-C",
                ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=0.8
            )
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            audio_files[audio_filename] = response.audio_content
            anki_sound = f"[sound:{audio_filename}]"
            updated_line = "\t".join(parts[:3] + [anki_sound])
            updated_lines.append(updated_line)

        except Exception as e:
            await update.message.reply_text(f"⚠️ TTS failed for line {i+1} ('{col3}'): {str(e)}")
            updated_lines.append(line)

    # Prepare updated .txt
    updated_txt = "\n".join(updated_lines)
    txt_buffer = BytesIO(updated_txt.encode("utf-8"))
    txt_buffer.name = "anki_deck_with_audio.txt"

    # Prepare ZIP of audio files
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, data in audio_files.items():
            zf.writestr(fname, data)
    zip_buffer.seek(0)
    zip_buffer.name = "anki_audio_files.zip"

    # Send both files
    await update.message.reply_document(
        document=InputFile(txt_buffer, filename="anki_deck_with_audio.txt"),
        caption="✅ Updated Anki .txt file (import this into Anki)"
    )
    await update.message.reply_document(
        document=InputFile(zip_buffer, filename="anki_audio_files.zip"),
        caption="🔊 Audio files (extract into your Anki media folder)"
    )

# --- Main ---
from telegram.ext import ApplicationBuilder

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_document))
    app.run_polling()

if __name__ == "__main__":
    main()
