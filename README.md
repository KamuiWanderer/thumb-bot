# 🖼 YouTube Thumbnail Extractor Bot

A Telegram bot that extracts high-quality YouTube thumbnails and expands FastMotion M3U8 quality links.

## Features

- Send YouTube links → receive HQ `.jpg` thumbnails with rich metadata
- Supports `youtube.com/watch`, `youtu.be`, `youtube.com/shorts`, and direct `ytimg.com` CDN links
- FastMotion CDN M3U8 quality expander (1080p / 720p / 480p / 360p)
- Batch processing — mix any link types in one message

---

## 🚀 Deploy to Render (via GitHub)

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2 — Create a Render service

1. Go to [render.com](https://render.com) and sign in
2. Click **New → Background Worker**
3. Connect your GitHub account and select this repository
4. Render will auto-detect `render.yaml` — confirm the settings

### Step 3 — Set environment variables

In the Render dashboard under **Environment**, add these three secrets:

| Key | Value |
|-----|-------|
| `API_ID` | Your Telegram API ID (from [my.telegram.org](https://my.telegram.org)) |
| `API_HASH` | Your Telegram API Hash |
| `BOT_TOKEN` | Your bot token from [@BotFather](https://t.me/BotFather) |

### Step 4 — Deploy

Click **Save and Deploy**. Render will install dependencies and start the bot.  
Every future `git push` to `main` will trigger an automatic redeploy.

---

## 🛠 Local Development

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variables
export API_ID=your_api_id
export API_HASH=your_api_hash
export BOT_TOKEN=your_bot_token

# 5. Run the bot
python bot.py
```

---

## 📁 Project Structure

```
thumb-bot/
├── bot.py            # Main bot logic
├── requirements.txt  # Python dependencies
├── render.yaml       # Render deployment config
├── .gitignore        # Excludes secrets & session files
└── README.md         # This file
```

---

## ⚠️ Important Notes

- **Never commit `.session` files** — they contain your Telegram auth token. They are already excluded via `.gitignore`.
- **Never hardcode credentials** in `bot.py`. Always use environment variables.
- The bot runs as a **Background Worker** on Render (no web server needed).
