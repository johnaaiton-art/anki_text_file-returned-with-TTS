import os
import sys
import time
import subprocess
from datetime import datetime
from faster_whisper import WhisperModel
import torch
import glob
import shutil
import atexit
import re
import tempfile
import random
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import threading

# ─────────────────────────────────────────────
#  GLOBAL CONFIG  (shared by all students)
# ─────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
STUDENTS_DIR = os.path.join(SCRIPT_DIR, "students")
SHARED_DIR   = os.path.join(SCRIPT_DIR, "shared")


class VocabularyCoach:

    # ──────────────────────────────────────────
    #  INIT
    # ──────────────────────────────────────────
    def __init__(self):
        print("\n" + "="*60)
        print("  VOCABULARY COACH")
        print("="*60 + "\n")

        # ── pick student ──────────────────────
        self.student_name = self._pick_student()
        self.student_dir  = os.path.join(STUDENTS_DIR, self.student_name.lower())

        # ── load all config & JSON files ─────
        self.load_config()

        # ── integrations ─────────────────────
        self.setup_google_sheets()
        self.setup_telegram()

        # ── FFmpeg ───────────────────────────
        self.ffmpeg_path  = shutil.which("ffmpeg")
        self.ffprobe_path = shutil.which("ffprobe")
        if not self.ffmpeg_path:
            for p in [r"C:\ffmpeg\bin\ffmpeg.exe"]:
                if os.path.exists(p):
                    self.ffmpeg_path  = p
                    self.ffprobe_path = p.replace("ffmpeg", "ffprobe")
                    break
        if not self.ffmpeg_path:
            raise Exception("❌ FFmpeg not found!")
        print(f"✅ FFmpeg:  {self.ffmpeg_path}")
        print(f"✅ FFprobe: {self.ffprobe_path}\n")

        # ── Whisper ───────────────────────────
        self.device       = "cuda" if torch.cuda.is_available() else "cpu"
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        if self.device == "cuda":
            print(f"✅ CUDA: {torch.cuda.get_device_name(0)}")
        else:
            print("⚠️  Using CPU (slower)")
        print(f"Loading Whisper on {self.device.upper()}...")
        t0 = time.time()
        self.model = WhisperModel("base.en", device=self.device, compute_type=self.compute_type)
        print(f"✅ Whisper loaded in {time.time()-t0:.1f}s\n")

        # ── runtime state ─────────────────────
        self.recording_folder        = self.shared_config['obs']['recording_folder']
        self.current_recording_file  = None
        self.last_processed_position = 0
        self.chunk_duration          = 5

        self.recent_roasts       = {}
        self.recent_praises      = {}
        self.roast_cooldown      = 60
        self.praise_cooldown     = 60
        self.global_last_telegram = 0
        self.telegram_cooldown   = 8

        self.temp_files = []
        atexit.register(self.cleanup_temp_files)

        # ── OBS alert file ────────────────────
        self.alert_file = os.path.join(SCRIPT_DIR, "obs_alert.txt")
        with open(self.alert_file, "w", encoding="utf-8") as f:
            f.write("")
        print(f"✅ Alert file: {self.alert_file}\n")

    # ──────────────────────────────────────────
    #  STUDENT SELECTION
    # ──────────────────────────────────────────
    def _pick_student(self):
        """List available student folders and let user choose."""
        if not os.path.isdir(STUDENTS_DIR):
            raise Exception(f"❌ Students folder not found: {STUDENTS_DIR}")

        folders = sorted([
            d for d in os.listdir(STUDENTS_DIR)
            if os.path.isdir(os.path.join(STUDENTS_DIR, d))
        ])

        if not folders:
            raise Exception(f"❌ No student folders found in {STUDENTS_DIR}")

        print("Available students:\n")
        for i, name in enumerate(folders, 1):
            print(f"  {i}. {name.capitalize()}")
        print()

        while True:
            choice = input("Enter student name or number: ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(folders):
                    name = folders[idx].capitalize()
                    print(f"\n✅ Student: {name}\n")
                    return name
            else:
                # match by name (case-insensitive)
                for f in folders:
                    if f.lower() == choice.lower():
                        print(f"\n✅ Student: {f.capitalize()}\n")
                        return f.capitalize()
            print("  ⚠️  Not found, try again.")

    # ──────────────────────────────────────────
    #  CONFIG LOADING
    # ──────────────────────────────────────────
    def load_config(self):
        """Load shared config + all per-student JSON files that exist."""

        # ── shared global config ──────────────
        shared_cfg_path = os.path.join(SHARED_DIR, "global_config.json")
        with open(shared_cfg_path, "r", encoding="utf-8") as f:
            self.shared_config = json.load(f)

        sd = self.student_dir  # shorthand

        # ── per-student config ────────────────
        student_cfg_path = os.path.join(sd, "config.json")
        with open(student_cfg_path, "r", encoding="utf-8") as f:
            self.student_config = json.load(f)

        self.telegram_alerts = self.student_config.get("telegram_alerts", True)
        self.sheets_logging  = self.student_config.get("sheets_logging",  True)
        features             = self.student_config.get("features", {})

        # ── banned words (always loaded if file exists) ───
        self.banned_words = []
        bw_path = os.path.join(sd, "banned_words.json")
        if os.path.exists(bw_path):
            with open(bw_path, "r", encoding="utf-8") as f:
                self.banned_words = json.load(f).get("words", [])

        # ── vocab concepts (option 1) ─────────
        self.vocab_concepts = {}
        if features.get("vocab", False):
            vc_path = os.path.join(sd, "vocab_concepts.json")
            if os.path.exists(vc_path):
                with open(vc_path, "r", encoding="utf-8") as f:
                    self.vocab_concepts = json.load(f)

        # ── option 2 : metaphors / grammar / phrasal ───
        self.option2_data   = {}
        self.option2_type   = features.get("option2", None)  # "metaphors" | "grammar" | "phrasal_verbs" | None
        if self.option2_type:
            opt2_path = os.path.join(sd, f"{self.option2_type}.json")
            if os.path.exists(opt2_path):
                with open(opt2_path, "r", encoding="utf-8") as f:
                    self.option2_data = json.load(f)

        # ── roast templates ───────────────────
        self.roast_templates = []
        self.adj_negative    = []
        self.adj_positive    = []
        self.verbs           = []
        self.nouns           = []
        roast_style = self.student_config.get("roast_style", "standard")
        roast_file  = f"roast_templates_{roast_style}.json" if roast_style != "standard" else "roast_templates.json"
        roast_path  = os.path.join(sd, roast_file)
        # fall back to standard if specific file missing
        if not os.path.exists(roast_path):
            roast_path = os.path.join(sd, "roast_templates.json")
        if os.path.exists(roast_path):
            with open(roast_path, "r", encoding="utf-8") as f:
                rd = json.load(f)
            self.roast_templates = rd.get("templates", [])
            self.adj_negative    = rd.get("adj_negative", [])
            self.adj_positive    = rd.get("adj_positive", [])
            self.verbs           = rd.get("verbs", [])
            self.nouns           = rd.get("nouns", [])

        # ── praise templates ──────────────────
        self.praise_templates = []
        praise_path = os.path.join(sd, "praise_templates.json")
        if os.path.exists(praise_path):
            with open(praise_path, "r", encoding="utf-8") as f:
                self.praise_templates = json.load(f).get("templates", [])

        # ── summary ───────────────────────────
        print(f"✅ Banned words loaded:   {len(self.banned_words)}")
        print(f"✅ Vocab concepts loaded: {len(self.vocab_concepts)}")
        print(f"✅ Option-2 ({self.option2_type or 'none'}) loaded: {len(self.option2_data)}")
        print(f"✅ Roast templates:       {len(self.roast_templates)}")
        print(f"✅ Praise templates:      {len(self.praise_templates)}")
        print(f"   Telegram alerts: {'ON' if self.telegram_alerts else 'OFF'}")
        print(f"   Sheets logging:  {'ON' if self.sheets_logging else 'OFF'}\n")

    # ──────────────────────────────────────────
    #  GOOGLE SHEETS
    # ──────────────────────────────────────────
    def setup_google_sheets(self):
        if not self.sheets_logging:
            self.sheet = None
            return
        try:
            creds_dict = self.shared_config['google_sheets']['credentials']
            scope = ['https://spreadsheets.google.com/feeds',
                     'https://www.googleapis.com/auth/drive']
            creds  = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)

            spreadsheet_id = self.shared_config['google_sheets']['spreadsheet_id']
            spreadsheet    = client.open_by_key(spreadsheet_id)

            try:
                self.sheet = spreadsheet.worksheet(self.student_name)
            except gspread.exceptions.WorksheetNotFound:
                print(f"⚠️  Sheet tab '{self.student_name}' not found — creating it...")
                self.sheet = spreadsheet.add_worksheet(
                    title=self.student_name, rows=1000, cols=10)

            if not self.sheet.row_values(1):
                self.sheet.append_row(
                    ["Date", "Time", "Event Type", "Trigger/Word Used", "Target Word", "Context"])

            print(f"✅ Google Sheets → tab: {self.student_name}\n")
        except Exception as e:
            print(f"⚠️  Google Sheets setup failed: {e}\n")
            self.sheet = None

    # ──────────────────────────────────────────
    #  TELEGRAM
    # ──────────────────────────────────────────
    def setup_telegram(self):
        try:
            self.telegram_token = self.shared_config['telegram']['bot_token']
            self.telegram_chat_id = self.shared_config['telegram']['students'].get(
                self.student_name)
            if self.telegram_chat_id:
                print(f"✅ Telegram configured for {self.student_name}\n")
            else:
                print(f"⚠️  No Telegram chat_id for {self.student_name} — alerts disabled\n")
        except Exception as e:
            print(f"⚠️  Telegram setup failed: {e}\n")
            self.telegram_token   = None
            self.telegram_chat_id = None

    # ──────────────────────────────────────────
    #  MESSAGING
    # ──────────────────────────────────────────
    def send_telegram_message(self, message):
        if not self.telegram_alerts:
            return False
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        current_time = time.time()
        if current_time - self.global_last_telegram < self.telegram_cooldown:
            print("[DEBUG] Telegram cooldown active")
            return False

        try:
            url  = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {"chat_id": self.telegram_chat_id, "text": message}
            r    = requests.post(url, json=data)
            if r.status_code == 200:
                print("[DEBUG] Telegram message sent!")
                self.global_last_telegram = current_time
                return True
            else:
                print(f"[DEBUG] Telegram failed: {r.text}")
                return False
        except Exception as e:
            print(f"[DEBUG] Telegram error: {e}")
            return False

    def log_to_sheets(self, event_type, word_used, target_word, context):
        if not self.sheet:
            return
        try:
            now = datetime.now()
            self.sheet.append_row([
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                event_type,
                word_used,
                target_word,
                context
            ])
            print(f"[DEBUG] Logged to Sheets: {event_type}")
        except Exception as e:
            print(f"[DEBUG] Sheets error: {e}")

    # ──────────────────────────────────────────
    #  MESSAGE GENERATION
    # ──────────────────────────────────────────
    def generate_roast(self, word_used):
        if not self.roast_templates:
            return f"⚠️ {self.student_name}, avoid '{word_used}'!"

        template     = random.choice(self.roast_templates)
        option2_list = [v['metaphor'] if 'metaphor' in v else v.get('keyword', '')
                        for v in self.option2_data.values()] or ["better vocabulary"]

        message = template.format(
            name  = self.student_name,
            adj1  = random.choice(self.adj_negative) if self.adj_negative else "basic",
            adj2  = random.choice(self.adj_negative) if self.adj_negative else "weak",
            verb1 = random.choice(self.verbs)        if self.verbs        else "improve",
            noun1 = random.choice(self.nouns)        if self.nouns        else "vocabulary",
            **{f"metaphor{i}": random.choice(option2_list) for i in range(1, 31)}
        )
        message += (f"\n\nYou said: '{word_used}'\n"
                    f"Try being more {random.choice(self.adj_positive) if self.adj_positive else 'precise'}! ✨")
        return message

    def generate_praise(self, word_used):
        if not self.praise_templates:
            return f"✨ {self.student_name} used: '{word_used}' — nice!"
        template = random.choice(self.praise_templates)
        return template.format(name=self.student_name, word=word_used)

    # ──────────────────────────────────────────
    #  DETECTION
    # ──────────────────────────────────────────
    def detect_banned_word(self, text):
        text_lower = text.lower()
        for word in self.banned_words:
            if ' ' in word:
                if word in text_lower:
                    return word
            else:
                if re.search(rf'\b{re.escape(word)}\b', text_lower):
                    return word
        return None

    def detect_target_word_usage(self, text):
        text_lower = text.lower()

        # vocab concepts
        for concept, data in self.vocab_concepts.items():
            if data['keyword'].lower() in text_lower:
                return ("vocab", data['keyword'], data['definition'])

        # option-2 (metaphors / grammar / phrasal verbs)
        for key, data in self.option2_data.items():
            target = data.get('metaphor', data.get('keyword', data.get('phrase', ''))).lower()
            if target and target in text_lower:
                return (self.option2_type or "option2", target, data.get('definition', ''))

        return None

    def detect_missed_opportunity(self, text):
        text_lower = text.lower()

        # ── vocab missed ──────────────────────
        for concept, data in self.vocab_concepts.items():
            if data['keyword'].lower() in text_lower:
                continue  # they used it — not a miss
            for trigger in data.get("triggers", []):
                if ' ' in trigger:
                    if trigger in text_lower:
                        return ("vocab", trigger, data['keyword'], data['definition'])
                else:
                    if re.search(rf'\b{re.escape(trigger)}\b', text_lower):
                        return ("vocab", trigger, data['keyword'], data['definition'])

        # ── option-2 missed (needs 2+ triggers) ──
        for key, data in self.option2_data.items():
            target = data.get('metaphor', data.get('keyword', data.get('phrase', ''))).lower()
            if target and target in text_lower:
                continue
            hits = []
            for trigger in data.get("triggers", []):
                if ' ' in trigger:
                    if trigger in text_lower:
                        hits.append(trigger)
                else:
                    if re.search(rf'\b{re.escape(trigger)}\b', text_lower):
                        hits.append(trigger)
            if len(hits) >= 2:
                label = data.get('metaphor', data.get('keyword', data.get('phrase', key)))
                return (self.option2_type or "option2",
                        " + ".join(hits), label, data.get('definition', ''))

        return None

    # ──────────────────────────────────────────
    #  TEXT PROCESSING (core logic)
    # ──────────────────────────────────────────
    def process_text(self, text):
        current_time = time.time()

        # PRIORITY 1 — target word used → PRAISE
        target_result = self.detect_target_word_usage(text)
        if target_result:
            word_type, word, definition = target_result
            if word not in self.recent_praises or \
               current_time - self.recent_praises[word] >= self.praise_cooldown:
                self.recent_praises[word] = current_time
                praise = self.generate_praise(word)
                print(f"\n✨ PRAISE! Used: {word}")
                self.send_telegram_message(praise)
                self.log_to_sheets(f"Target Word Used ({word_type})", word, word, text)
                self.show_obs_alert(f"✨ NICE! {word}\n{definition}", duration=12)

        # PRIORITY 2 — banned word → ROAST
        banned = self.detect_banned_word(text)
        if banned:
            if banned not in self.recent_roasts or \
               current_time - self.recent_roasts[banned] >= self.roast_cooldown:
                self.recent_roasts[banned] = current_time
                roast = self.generate_roast(banned)
                print(f"\n🚫 BANNED: {banned}")
                self.send_telegram_message(roast)
                self.log_to_sheets("Banned Word", banned, "(avoid)", text)
                self.show_obs_alert(f"⚠️ AVOID: {banned}\nUse stronger vocab!", duration=12)

        # PRIORITY 3 — missed opportunity → ROAST
        missed = self.detect_missed_opportunity(text)
        if missed:
            word_type, trigger_used, target_word, definition = missed
            key = f"{word_type}:{target_word}"
            if key not in self.recent_roasts or \
               current_time - self.recent_roasts[key] >= self.roast_cooldown:
                self.recent_roasts[key] = current_time
                roast  = self.generate_roast(trigger_used)
                roast += f"\n\n💡 You should have said: '{target_word}'\n📖 {definition}"
                print(f"\n💔 MISSED: {trigger_used} → '{target_word}'")
                self.send_telegram_message(roast)
                self.log_to_sheets(f"Missed Opportunity ({word_type})",
                                   trigger_used, target_word, text)
                self.show_obs_alert(f"💡 TRY: {target_word}\n{definition}", duration=12)

    # ──────────────────────────────────────────
    #  OBS ALERT
    # ──────────────────────────────────────────
    def show_obs_alert(self, text, duration=12):
        try:
            with open(self.alert_file, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            print(f"   ⚠️  Alert file error: {e}")
        threading.Timer(duration, self.clear_alert).start()

    def clear_alert(self):
        try:
            with open(self.alert_file, "w", encoding="utf-8") as f:
                f.write("")
        except:
            pass

    # ──────────────────────────────────────────
    #  AUDIO / FFMPEG
    # ──────────────────────────────────────────
    def cleanup_temp_files(self):
        for f in self.temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass

    def find_most_recent_recording(self):
        files = []
        for ext in ['*.mp4', '*.flv', '*.mkv']:
            files.extend(glob.glob(os.path.join(self.recording_folder, ext)))
        return max(files, key=os.path.getmtime) if files else None

    def extract_audio_chunk(self, video_file, start_time, duration):
        temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix='coach_')
        os.close(temp_fd)
        self.temp_files.append(temp_path)
        try:
            cmd = [self.ffmpeg_path,
                   "-ss", str(start_time), "-i", video_file,
                   "-t", str(duration), "-vn",
                   "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
                   "-y", temp_path]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0 and os.path.exists(temp_path):
                return temp_path
            return None
        except Exception as e:
            print(f"   ⚠️  Extract error: {e}")
            return None

    def get_video_duration(self, video_file):
        try:
            result = subprocess.run(
                [self.ffprobe_path, "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 video_file],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                s = result.stdout.strip()
                if s and s != 'N/A':
                    return float(s)
        except:
            pass
        return 0

    # ──────────────────────────────────────────
    #  MAIN LOOP
    # ──────────────────────────────────────────
    def monitor_and_process(self):
        print("\n" + "="*60)
        print("  READY")
        print("="*60)
        print("\n1. Start OBS and begin recording")
        print("2. Script processes audio every 5 seconds")
        print(f"\n📝 OBS file:        {self.alert_file}")
        print(f"⏱️  Chunk size:      {self.chunk_duration}s")
        print(f"🔕 Roast cooldown:  {self.roast_cooldown}s")
        print(f"🔕 Praise cooldown: {self.praise_cooldown}s")
        print(f"🔕 TG cooldown:     {self.telegram_cooldown}s")
        print("\nPress Ctrl+C to stop\n")

        input("Press Enter when recording has started...")
        print("\n🎥 Looking for recording...")

        while not self.current_recording_file:
            self.current_recording_file = self.find_most_recent_recording()
            if not self.current_recording_file:
                print("   Waiting...")
                time.sleep(2)

        print(f"✅ Found: {os.path.basename(self.current_recording_file)}")
        print("🎤 Processing...\n" + "="*60 + "\n")

        self.last_processed_position = 0
        consecutive_errors = 0

        while True:
            try:
                current_duration = self.get_video_duration(self.current_recording_file)
                available_audio  = current_duration - self.last_processed_position

                if available_audio >= self.chunk_duration:
                    chunk_to_process = min(self.chunk_duration, available_audio)
                    start = self.last_processed_position
                    print(f"⏱️  {start:.0f}s – {start + chunk_to_process:.0f}s")

                    audio_chunk = self.extract_audio_chunk(
                        self.current_recording_file, start, chunk_to_process)

                    if audio_chunk:
                        segments, _ = self.model.transcribe(
                            audio_chunk, beam_size=5, language="en", vad_filter=True)
                        text = " ".join(seg.text for seg in segments)

                        if text.strip():
                            print(f"   📝 {text}")
                            self.process_text(text)

                        try:
                            os.remove(audio_chunk)
                            self.temp_files.remove(audio_chunk)
                        except:
                            pass

                        self.last_processed_position += chunk_to_process
                        consecutive_errors = 0
                    else:
                        print("   ⚠️  Could not extract chunk")
                        consecutive_errors += 1

                time.sleep(2)

            except KeyboardInterrupt:
                print("\n\n🛑 Stopped by user")
                break

            except Exception as e:
                consecutive_errors += 1
                print(f"⚠️  Error ({consecutive_errors}/5): {e}")
                if consecutive_errors >= 5:
                    print("❌ Too many errors — re-scanning for recording...")
                    self.current_recording_file = None
                    while not self.current_recording_file:
                        self.current_recording_file = self.find_most_recent_recording()
                        time.sleep(2)
                    consecutive_errors = 0
                time.sleep(5)

        print("\n" + "="*60)
        print("  SESSION COMPLETE")
        print("="*60)
        print(f"\n📊 Audio processed: {self.last_processed_position:.0f}s")
        print(f"📊 Roasts:          {len(self.recent_roasts)}")
        print(f"📊 Praises:         {len(self.recent_praises)}\n")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        coach = VocabularyCoach()
        coach.monitor_and_process()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
