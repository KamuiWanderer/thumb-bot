"""
╔══════════════════════════════════════════════════╗
║       YouTube Thumbnail Extractor Bot            ║
║  Send multiple YT links → get HQ thumbnails      ║
╚══════════════════════════════════════════════════╝
"""

import os
import re
import asyncio
import logging
import tempfile
import threading
import time
import requests
import yt_dlp

from datetime import datetime, timezone
from flask import Flask
from telethon import TelegramClient, events

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
RENDER_URL = os.environ.get("RENDER_URL", "")

DOWNLOAD_DIR = tempfile.gettempdir()
START_TIME   = datetime.now(timezone.utc)

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  FLASK KEEP-ALIVE
# ─────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    uptime = datetime.now(timezone.utc) - START_TIME
    h, rem = divmod(int(uptime.total_seconds()), 3600)
    m, s   = divmod(rem, 60)
    return (
        f"<h2>🖼 YouTube Thumbnail Bot</h2>"
        f"<p>✅ Bot is <b>alive</b></p>"
        f"<p>⏱ Uptime: {h}h {m}m {s}s</p>"
        f"<p>🕒 Started: {START_TIME.strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
    )

@flask_app.route("/health")
def health():
    secs = int((datetime.now(timezone.utc) - START_TIME).total_seconds())
    return {"status": "ok", "uptime_seconds": secs}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

def self_ping_loop():
    if not RENDER_URL:
        logger.info("RENDER_URL not set — self-ping disabled")
        return
    while True:
        time.sleep(600)
        try:
            r = requests.get(RENDER_URL.rstrip("/") + "/health", timeout=10)
            logger.info(f"Self-ping OK → {r.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")

# ══════════════════════════════════════════════
#  URL PATTERNS
# ══════════════════════════════════════════════
FASTMOTION_RE = re.compile(
    r"(https?://cdn\.fastmotion\.io/)([\w-]+)/(\d+p)/([^\s]+)", re.IGNORECASE)
FASTMOTION_QUALITIES = ["1080p", "720p", "480p", "360p"]

YT_WATCH_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)"
    r"([\w-]{11})[^\s]*", re.IGNORECASE)

YT_IMG_RE = re.compile(
    r"https?://(?:img\.youtube\.com|i\.ytimg\.com)/vi(?:_webp)?/([\w-]{11})/([^\s]+\.(?:jpg|webp))",
    re.IGNORECASE)

QUALITY_KEYS = ["maxresdefault", "sddefault", "hqdefault", "mqdefault", "default"]
QUALITY_LABELS = {
    "maxresdefault": "1280×720",
    "sddefault":     "640×480",
    "hqdefault":     "480×360",
    "mqdefault":     "320×180",
    "default":       "120×90",
}

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════
def extract_entries(text):
    results, seen = [], set()
    for m in YT_WATCH_RE.finditer(text):
        vid = m.group(1)
        if vid not in seen:
            seen.add(vid)
            results.append({"vid": vid, "watch_url": "https://www.youtube.com/watch?v=" + vid, "pref_key": "maxresdefault"})
    for m in YT_IMG_RE.finditer(text):
        vid, img_file = m.group(1), m.group(2).lower()
        pref_key = next((k for k in QUALITY_KEYS if k in img_file), "maxresdefault")
        if vid not in seen:
            seen.add(vid)
            results.append({"vid": vid, "watch_url": "https://www.youtube.com/watch?v=" + vid, "pref_key": pref_key})
    return results

def fetch_thumbnail(video_id, pref_key="maxresdefault"):
    try:
        start_idx = QUALITY_KEYS.index(pref_key)
    except ValueError:
        start_idx = 0
    for key in QUALITY_KEYS[start_idx:] + QUALITY_KEYS[:start_idx]:
        try:
            r = requests.get(f"https://i.ytimg.com/vi/{video_id}/{key}.jpg", timeout=12)
            if r.status_code == 200 and len(r.content) > 5000:
                return r.content, key
        except Exception:
            pass
    return None, None

def get_video_info(watch_url):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "nocheckcertificate": True, "socket_timeout": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(watch_url, download=False) or {}
    except Exception as e:
        logger.warning(f"Metadata fetch failed: {e}")
        return {}

def fmt_num(n):
    if n is None: return "N/A"
    try:
        n = int(n)
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)
    except: return str(n)

def fmt_dur(sec):
    try:
        sec = int(sec)
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    except: return "N/A"

def fmt_date(ds):
    if not ds or len(ds) != 8: return ds or "N/A"
    try: return datetime.strptime(ds, "%Y%m%d").strftime("%b %d, %Y")
    except: return ds

def build_caption(info, watch_url, got_key):
    lines = [
        f"🖼 **{info.get('title') or 'Unknown Title'}**", "",
        f"👤 {info.get('uploader') or info.get('channel') or 'Unknown'}",
    ]
    subs = fmt_num(info.get("channel_follower_count"))
    if subs != "N/A": lines.append(f"🔔 {subs} subscribers")
    lines += [
        f"📅 {fmt_date(info.get('upload_date', ''))}",
        f"⏱ {fmt_dur(info.get('duration'))}",
        "", f"👁 {fmt_num(info.get('view_count'))} views",
        f"👍 {fmt_num(info.get('like_count'))} likes",
    ]
    comments = fmt_num(info.get("comment_count"))
    if comments != "N/A": lines.append(f"💬 {comments} comments")
    lines += ["", f"📐 Thumbnail quality: `{QUALITY_LABELS.get(got_key, got_key)}`", f"🔗 {watch_url}"]
    return "\n".join(lines)

def safe_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    return re.sub(r"\s+", " ", text).strip()[:100] or "thumbnail"

# ══════════════════════════════════════════════
#  MAIN ASYNC FUNCTION
# ══════════════════════════════════════════════
async def main():
    bot = TelegramClient("KayiUploader", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)

    print("━" * 52)
    print("  🖼   YouTube Thumbnail Extractor Bot")
    print("  ✅   Running on Python asyncio.run()")
    print("  🌐   Flask keep-alive active")
    print("  🔴   Waiting for links…")
    print("━" * 52)

    @bot.on(events.NewMessage(pattern="/start"))
    async def cmd_start(event):
        await event.respond(
            "🖼 **YouTube Thumbnail Extractor + M3U8 Quality Expander**\n\n"
            "Send YouTube links → get HQ .jpg files with full details.\n"
            "• `youtube.com/watch?v=…`\n• `youtu.be/…`\n• `youtube.com/shorts/…`\n\n"
            "**FastMotion M3U8:**\nSend any `cdn.fastmotion.io` link → get all quality variants.\n\n"
            "**Commands:** /ping — /status\n\n"
            "Batch supported ✅"
        )

    @bot.on(events.NewMessage(pattern="/ping"))
    async def cmd_ping(event):
        sent = await event.respond("🏓 Pinging…")
        ms = max(0, int((datetime.now(timezone.utc) - sent.date.replace(tzinfo=timezone.utc)).total_seconds() * 1000))
        await sent.edit(f"🏓 **Pong!**\n⚡ Latency: `{ms} ms`\n✅ Bot is alive.")

    @bot.on(events.NewMessage(pattern="/status"))
    async def cmd_status(event):
        uptime = datetime.now(timezone.utc) - START_TIME
        h, rem = divmod(int(uptime.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        ping_status = f"✅ Pinging every 10 min" if RENDER_URL else "⚠️ Set RENDER_URL env var to enable"
        await event.respond(
            f"📊 **Bot Status**\n\n"
            f"🟢 **Status:** Online\n"
            f"⏱ **Uptime:** `{h}h {m}m {s}s`\n"
            f"🕒 **Started:** `{START_TIME.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
            f"🌐 **Keep-alive:** ✅ Running\n"
            f"🔁 **Self-ping:** {ping_status}"
        )

    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.text and "cdn.fastmotion.io" in e.text.lower()))
    async def handle_fastmotion(event):
        m = FASTMOTION_RE.search(event.text.strip())
        if not m:
            await event.reply("❌ Could not parse the FastMotion URL.\nFormat:\nhttps://cdn.fastmotion.io/UUID/QUALITYp/video.m3u8")
            return
        base, uuid, filename = m.group(1), m.group(2), m.group(4)
        await event.reply("\n\n".join(f"{base}{uuid}/{q}/{filename}" for q in FASTMOTION_QUALITIES))

    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.text and ("youtu" in e.text.lower() or "ytimg" in e.text.lower())))
    async def handle_links(event):
        entries = extract_entries(event.text)
        if not entries:
            await event.reply("❌ No valid YouTube links found.")
            return

        total  = len(entries)
        status = await event.reply(f"🔍 Found **{total}** link{'s' if total > 1 else ''}. Starting…")
        success, failed = 0, []
        loop = asyncio.get_event_loop()

        for idx, entry in enumerate(entries, 1):
            vid, watch_url, pref_key = entry["vid"], entry["watch_url"], entry["pref_key"]
            await status.edit(f"⏳ **{idx}/{total}**\n\n🎬 Fetching…\n🔗 `{watch_url}`")

            info, (img_data, got_key) = await asyncio.gather(
                loop.run_in_executor(None, get_video_info, watch_url),
                loop.run_in_executor(None, fetch_thumbnail, vid, pref_key),
            )

            if img_data is None:
                failed.append(f"`{watch_url}` — thumbnail unavailable")
                continue

            title = info.get("title") or vid
            await status.edit(f"⏳ **{idx}/{total}**\n\n📤 Uploading: **{title[:55]}**")

            fpath = os.path.join(DOWNLOAD_DIR, f"{vid}_{safe_filename(title)}.jpg")
            try:
                with open(fpath, "wb") as f:
                    f.write(img_data)
                await bot.send_file(event.chat_id, fpath, caption=build_caption(info, watch_url, got_key), force_document=True, attributes=[])
                success += 1
            except Exception as e:
                logger.error(f"Upload failed for {vid}: {e}")
                failed.append(f"`{watch_url}` — {str(e)[:80]}")
            finally:
                if os.path.exists(fpath):
                    os.remove(fpath)

        if failed:
            await status.edit(f"✅ **{success}/{total}** sent.\n\n❌ **Failed:**\n" + "\n".join(f"• {f}" for f in failed))
        else:
            await status.edit(f"✅ All **{total}** thumbnail{'s' if total > 1 else ''} sent successfully!")

    await bot.run_until_disconnected()

# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()
    asyncio.run(main())
