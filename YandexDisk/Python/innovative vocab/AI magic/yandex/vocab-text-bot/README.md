# Telegram Vocab Bot Setup Guide

## Prerequisites
- Yandex Cloud VM with Python 3.8+
- Telegram Bot Token
- DeepSeek API Key
- Google Cloud credentials JSON file

## Step 1: Prepare Your Local Repository
```bash
# In your local project folder
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/johnaaiton-art/text-creator-for-anki-vocab-words.git
git push -u origin main
```

## Step 2: Connect to Yandex Cloud VM
```bash
ssh your-username@your-vm-ip
```

## Step 3: Clone Repository on VM
```bash
cd /home/user
git clone https://github.com/johnaaiton-art/text-creator-for-anki-vocab-words.git
cd text-creator-for-anki-vocab-words
```

## Step 4: Install Dependencies
```bash
python3 -m pip install --upgrade pip
pip3 install -r requirements.txt
```

## Step 5: Upload Google Credentials

**Option A: Using SCP from your local machine:**
```bash
# From your local computer (PowerShell or Git Bash)
scp /path/to/your/google-creds.json your-username@your-vm-ip:/home/user/google-creds.json
```

**Option B: Create file directly on VM:**
```bash
# On the VM
nano /home/user/google-creds.json
# Paste your JSON content, then Ctrl+X, Y, Enter to save
```

## Step 6: Set Environment Variables
```bash
# Create .env file or export directly
export TELEGRAM_TOKEN="your_telegram_bot_token_here"
export DEEPSEEK_API_KEY="your_deepseek_api_key_here"
export GOOGLE_CREDS_PATH="/home/user/google-creds.json"
```

**To make permanent, add to ~/.bashrc:**
```bash
echo 'export TELEGRAM_TOKEN="your_token"' >> ~/.bashrc
echo 'export DEEPSEEK_API_KEY="your_key"' >> ~/.bashrc
echo 'export GOOGLE_CREDS_PATH="/home/user/google-creds.json"' >> ~/.bashrc
source ~/.bashrc
```

## Step 7: Test the Bot
```bash
python3 bot.py
```

You should see: `Bot starting...`

## Step 8: Run as Background Service (Optional)

**Option A: Using screen:**
```bash
screen -S vocab_bot
python3 bot.py
# Press Ctrl+A, then D to detach
# To reattach: screen -r vocab_bot
```

**Option B: Using systemd service:**

Create service file:
```bash
sudo nano /etc/systemd/system/vocab-bot.service
```

Paste this content:
```ini
[Unit]
Description=Telegram Vocab Bot
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/user/text-creator-for-anki-vocab-words
Environment="TELEGRAM_TOKEN=your_token"
Environment="DEEPSEEK_API_KEY=your_key"
Environment="GOOGLE_CREDS_PATH=/home/user/google-creds.json"
ExecStart=/usr/bin/python3 /home/user/text-creator-for-anki-vocab-words/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable vocab-bot
sudo systemctl start vocab-bot
sudo systemctl status vocab-bot
```

## Step 9: Monitor Logs
```bash
# If using systemd:
sudo journalctl -u vocab-bot -f

# If using screen:
screen -r vocab_bot
```

## Updating the Bot
```bash
cd /home/user/text-creator-for-anki-vocab-words
git pull origin main
# If using systemd:
sudo systemctl restart vocab-bot
# If using screen:
# Ctrl+C to stop, then python3 bot.py
```

## Troubleshooting

### Bot not responding
- Check if running: `ps aux | grep bot.py`
- Check environment variables: `echo $TELEGRAM_TOKEN`
- Check logs for errors

### Google TTS errors
- Verify credentials file exists: `ls -l /home/user/google-creds.json`
- Check file permissions: `chmod 600 /home/user/google-creds.json`

### DeepSeek API errors
- Verify API key is correct
- Check internet connectivity from VM
- Verify DeepSeek API quota/limits

## Usage Flow

1. User sends .txt file or pastes words
2. Bot asks for column number (if file)
3. Bot shows preview and asks confirmation
4. User selects level (C2, C1, B2, B1, A2, A1)
5. User enters topic/question
6. Bot generates text with highlighted vocab words
7. Bot sends HTML file
8. Bot generates and sends audio (except C2)

## Audio Speeds by Level
- C1: 100% speed
- B2/B1: 85% speed
- A2/A1: 70% speed

## File Structure
```
text-creator-for-anki-vocab-words/
├── bot.py              # Main bot code
├── requirements.txt    # Python dependencies
├── README.md          # This file
└── .gitignore         # Git ignore file
```

## Important Notes

- C2 level texts are 500 words and do NOT include audio
- All other levels include audio narration at appropriate speeds
- Vocabulary words are highlighted in blue in the HTML
- Bot filters common function words automatically
- Supports English, Spanish, and Chinese
