import os
import re
import uuid
import zipfile
import json
import datetime
from io import BytesIO
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.cloud import texttospeech
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- Configuration ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# Your Google Sheet ID (from URL)
GOOGLE_SHEET_ID = "1nr2eI8IEZBo55kErnRe4lOYhp6jhUHCkYiCw54-ATCw"
GOOGLE_SHEET_NAME = "Sheet1"  # or whatever your tab is called

def get_google_creds():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    return Credentials.from_service_account_info(creds_dict)

def get_tts_client():
    return texttospeech.TextToSpeechClient(credentials=get_google_creds())

def get_sheets_service():
    creds = get_google_creds()
    return build("sheets", "v4", credentials=creds)

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\s,]+', '_', text.strip())
    return clean

async def log_to_sheet(user, filename, file_size, num_lines, num_tts):
    try:
        service = get_sheets_service()
        sheet = service.spreadsheets()

        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        username = user.username or f"ID_{user.id}"
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "N/A"

        values = [[
            now,
            username,
            full_name,
            filename,
            file_size,
            num_lines,
            num_tts
        ]]

        body = {"values": values}
        sheet.values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_NAME}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()

    except Exception as e:
        # Never break the bot if logging fails
        print(f"[LOG ERROR] Failed to log to Google Sheet: {e}")

def setup_sheet_headers():
    """Optional: Run once to set headers (you can do this manually too)"""
    try:
        service = get_sheets_service()
        sheet = service.spreadsheets()
        headers = [["Timestamp", "Username", "Full Name", "Filename", "File Size (bytes)", "Lines", "TTS Requests"]]
        sheet.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{GOOGLE_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": headers}
        ).execute()
    except Exception as e:
        print(f"[SETUP] Header setup failed (maybe already done?): {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📤 Send a .txt file with lines like:\n"
        "English sentence\\tTranslation\\tTarget word(s)\n\n"
        "I'll generate TTS for the 3rd column and return an Anki-ready file + audio ZIP!"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Please send a .txt file.")
        return

    user = update.effective_user
    filename = update.message.document.file_name
    file_size = update.message.document.file_size  # in bytes

    try:
        file = await update.message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        content = file_bytes.decode("utf-8")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to read file: {str(e)}")
        return

    lines = content.strip().split("\n")
    updated_lines = []
    audio_files = {}
    tts_count = 0

    client = get_tts_client()

    for i, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) < 3:
            updated_lines.append(line)
            continue

        col3 = parts[2].strip()
        if not col3:
            updated_lines.append(line)
            continue

        safe_word = sanitize_filename(col3)
        unique_id = str(uuid.uuid4())[:8]
        audio_filename = f"{safe_word}__{unique_id}.mp3"

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
            tts_count += 1

        except Exception as e:
            updated_lines.append(line)

    # Log usage BEFORE sending files (so it's tracked even if send fails)
    await log_to_sheet(user, filename, file_size, len(lines), tts_count)

    # Prepare and send files
    updated_txt = "\n".join(updated_lines)
    txt_buffer = BytesIO(updated_txt.encode("utf-8"))
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, data in audio_files.items():
            zf.writestr(fname, data)
    zip_buffer.seek(0)

    await update.message.reply_document(
        document=InputFile(txt_buffer, filename="anki_deck_with_audio.txt"),
        caption="✅ Updated Anki .txt file"
    )
    await update.message.reply_document(
        document=InputFile(zip_buffer, filename="anki_audio_files.zip"),
        caption="🔊 Extract into Anki media folder!"
    )

# --- Main ---
def main():
    # Optional: Uncomment once to set headers
    # setup_sheet_headers()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_document))
    app.run_polling()

if __name__ == "__main__":
    main()
