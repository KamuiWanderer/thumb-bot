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
#  CONFIGURATION  (loaded from environment variables)
# ─────────────────────────────────────────────
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Set this to your Render URL in the dashboard to enable self-ping
# e.g.  https://thumb-bot.onrender.com
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
#  FLASK KEEP-ALIVE SERVER
#  Render free tier sleeps after 15 min of no HTTP traffic.
#  This web server gives Render something to talk to,
#  and the self-ping loop calls it every 10 min automatically.
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
    uptime_secs = int((datetime.now(timezone.utc) - START_TIME).total_seconds())
    return {"status": "ok", "uptime_seconds": uptime_secs}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

def self_ping_loop():
    """Hits our own /health endpoint every 10 minutes to prevent Render sleep."""
    if not RENDER_URL:
        logger.info("RENDER_URL not set — self-ping disabled. Set it in Render env vars.")
        return
    while True:
        time.sleep(600)  # 10 minutes
        try:
            r = requests.get(RENDER_URL.rstrip("/") + "/health", timeout=10)
            logger.info(f"Self-ping OK → {r.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")


# ─────────────────────────────────────────────
#  TELEGRAM CLIENT
# ─────────────────────────────────────────────
bot = TelegramClient("KayiUploader", API_ID, API_HASH)


# ══════════════════════════════════════════════
#  URL PATTERNS
# ══════════════════════════════════════════════

FASTMOTION_RE = re.compile(
    r"(https?://cdn\.fastmotion\.io/)([\w-]+)/(\d+p)/([^\s]+)",
    re.IGNORECASE,
)
FASTMOTION_QUALITIES = ["1080p", "720p", "480p", "360p"]

YT_WATCH_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)"
    r"([\w-]{11})[^\s]*",
    re.IGNORECASE,
)

YT_IMG_RE = re.compile(
    r"https?://(?:img\.youtube\.com|i\.ytimg\.com)/vi(?:_webp)?/([\w-]{11})/([^\s]+\.(?:jpg|webp))",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────
#  QUALITY CONFIGURATION
# ─────────────────────────────────────────────
QUALITY_KEYS = [
    "maxresdefault",   # 1280×720
    "sddefault",       # 640×480
    "hqdefault",       # 480×360
    "mqdefault",       # 320×180
    "default",         # 120×90
]
QUALITY_LABELS = {
    "maxresdefault": "1280×720",
    "sddefault":     "640×480",
    "hqdefault":     "480×360",
    "mqdefault":     "320×180",
    "default":       "120×90",
}


# ══════════════════════════════════════════════
#  PARSING
# ══════════════════════════════════════════════

def extract_entries(text: str) -> list:
    results = []
    seen = set()

    for m in YT_WATCH_RE.finditer(text):
        vid = m.group(1)
        if vid not in seen:
            seen.add(vid)
            results.append({
                "vid":       vid,
                "watch_url": "https://www.youtube.com/watch?v=" + vid,
                "pref_key":  "maxresdefault",
            })

    for m in YT_IMG_RE.finditer(text):
        vid      = m.group(1)
        img_file = m.group(2).lower()
        pref_key = "maxresdefault"
        for key in QUALITY_KEYS:
            if key in img_file:
                pref_key = key
                break
        if vid not in seen:
            seen.add(vid)
            results.append({
                "vid":       vid,
                "watch_url": "https://www.youtube.com/watch?v=" + vid,
                "pref_key":  pref_key,
            })

    return results


# ══════════════════════════════════════════════
#  THUMBNAIL FETCH
# ══════════════════════════════════════════════

def fetch_thumbnail(video_id: str, pref_key: str = "maxresdefault"):
    try:
        start_idx = QUALITY_KEYS.index(pref_key)
    except ValueError:
        start_idx = 0

    order = QUALITY_KEYS[start_idx:] + QUALITY_KEYS[:start_idx]
    for key in order:
        url = f"https://i.ytimg.com/vi/{video_id}/{key}.jpg"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200 and len(r.content) > 5000:
                return r.content, key
        except Exception:
            pass

    return None, None


# ══════════════════════════════════════════════
#  METADATA FETCH
# ══════════════════════════════════════════════

def get_video_info(watch_url: str) -> dict:
    opts = {
        "quiet":              True,
        "no_warnings":        True,
        "skip_download":      True,
        "extract_flat":       False,
        "nocheckcertificate": True,
        "socket_timeout":     20,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(watch_url, download=False) or {}
    except Exception as e:
        logger.warning(f"Metadata fetch failed for {watch_url}: {e}")
        return {}


# ══════════════════════════════════════════════
#  CAPTION BUILDER
# ══════════════════════════════════════════════

def fmt_num(n) -> str:
    if n is None:
        return "N/A"
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)
    except Exception:
        return str(n)


def fmt_dur(sec) -> str:
    try:
        sec = int(sec)
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    except Exception:
        return "N/A"


def fmt_date(ds: str) -> str:
    if not ds or len(ds) != 8:
        return ds or "N/A"
    try:
        return datetime.strptime(ds, "%Y%m%d").strftime("%b %d, %Y")
    except Exception:
        return ds


def build_caption(info: dict, watch_url: str, got_key: str) -> str:
    title    = info.get("title")    or "Unknown Title"
    channel  = info.get("uploader") or info.get("channel") or "Unknown"
    views    = fmt_num(info.get("view_count"))
    likes    = fmt_num(info.get("like_count"))
    comments = fmt_num(info.get("comment_count"))
    date     = fmt_date(info.get("upload_date", ""))
    duration = fmt_dur(info.get("duration"))
    subs     = fmt_num(info.get("channel_follower_count"))
    res      = QUALITY_LABELS.get(got_key, got_key)

    lines = [f"🖼 **{title}**", "", f"👤 {channel}"]
    if subs and subs != "N/A":
        lines.append(f"🔔 {subs} subscribers")
    lines += [f"📅 {date}", f"⏱ {duration}", "", f"👁 {views} views", f"👍 {likes} likes"]
    if comments != "N/A":
        lines.append(f"💬 {comments} comments")
    lines += ["", f"📐 Thumbnail quality: `{res}`", f"🔗 {watch_url}"]
    return "\n".join(lines)


# ══════════════════════════════════════════════
#  FILENAME HELPER
# ══════════════════════════════════════════════

def safe_filename(text: str) -> str:
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:100] or "thumbnail"


# ══════════════════════════════════════════════
#  BOT COMMANDS
# ══════════════════════════════════════════════

@bot.on(events.NewMessage(pattern="/start"))
async def cmd_start(event):
    await event.respond(
        "🖼 **YouTube Thumbnail Extractor + M3U8 Quality Expander**\n\n"
        "**YouTube thumbnails:**\n"
        "Send YouTube links → get HQ .jpg files with full details.\n"
        "• `youtube.com/watch?v=…`\n"
        "• `youtu.be/…`\n"
        "• `youtube.com/shorts/…`\n"
        "• `img.youtube.com/vi/ID/maxresdefault.jpg`\n"
        "• `i.ytimg.com/vi/ID/hqdefault.jpg`\n\n"
        "**FastMotion M3U8 quality expander:**\n"
        "Send any `cdn.fastmotion.io` link → get all quality variants (1080p / 720p / 480p / 360p).\n\n"
        "**Commands:**\n"
        "• /ping — check if bot is alive\n"
        "• /status — uptime & server info\n\n"
        "Batch supported — mix any link types in one message! ✅"
    )


@bot.on(events.NewMessage(pattern="/ping"))
async def cmd_ping(event):
    """Quick alive check."""
    sent       = await event.respond("🏓 Pinging…")
    latency_ms = max(0, int(
        (datetime.now(timezone.utc) - sent.date.replace(tzinfo=timezone.utc)).total_seconds() * 1000
    ))
    await sent.edit(
        f"🏓 **Pong!**\n"
        f"⚡ Latency: `{latency_ms} ms`\n"
        f"✅ Bot is alive and running."
    )


@bot.on(events.NewMessage(pattern="/status"))
async def cmd_status(event):
    """Show uptime and health info."""
    uptime     = datetime.now(timezone.utc) - START_TIME
    total_secs = int(uptime.total_seconds())
    h, rem     = divmod(total_secs, 3600)
    m, s       = divmod(rem, 60)

    ping_status = f"✅ Pinging `{RENDER_URL}` every 10 min" if RENDER_URL else "⚠️ Disabled — set `RENDER_URL` env var"

    await event.respond(
        "📊 **Bot Status**\n\n"
        f"🟢 **Status:** Online\n"
        f"⏱ **Uptime:** `{h}h {m}m {s}s`\n"
        f"🕒 **Started:** `{START_TIME.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
        f"🌐 **Keep-alive server:** ✅ Running\n"
        f"🔁 **Self-ping:** {ping_status}\n\n"
        f"🤖 **Bot:** YouTube Thumbnail Extractor\n"
        f"📡 **Features:** YT thumbnails • FastMotion M3U8"
    )


# ══════════════════════════════════════════════
#  FASTMOTION M3U8 HANDLER
# ══════════════════════════════════════════════

@bot.on(
    events.NewMessage(
        incoming=True,
        func=lambda e: e.text and "cdn.fastmotion.io" in e.text.lower(),
    )
)
async def handle_fastmotion(event):
    text = event.text.strip()
    m = FASTMOTION_RE.search(text)

    if not m:
        await event.reply(
            "❌ Could not parse the FastMotion URL.\n"
            "Format:\nhttps://cdn.fastmotion.io/UUID/QUALITYp/video.m3u8"
        )
        return

    base     = m.group(1)
    uuid     = m.group(2)
    filename = m.group(4)

    links = [f"{base}{uuid}/{q}/{filename}" for q in FASTMOTION_QUALITIES]
    await event.reply("\n\n".join(links))


# ══════════════════════════════════════════════
#  MAIN YOUTUBE HANDLER
# ══════════════════════════════════════════════

@bot.on(
    events.NewMessage(
        incoming=True,
        func=lambda e: e.text and (
            "youtu" in e.text.lower() or
            "ytimg" in e.text.lower()
        ),
    )
)
async def handle_links(event):
    entries = extract_entries(event.text)

    if not entries:
        await event.reply("❌ No valid YouTube links found in your message.")
        return

    total  = len(entries)
    status = await event.reply(
        f"🔍 Found **{total}** link{'s' if total > 1 else ''}. Starting…"
    )

    success = 0
    failed  = []
    loop    = asyncio.get_event_loop()

    for idx, entry in enumerate(entries, start=1):
        vid       = entry["vid"]
        watch_url = entry["watch_url"]
        pref_key  = entry["pref_key"]

        await status.edit(
            f"⏳ **{idx} / {total}**\n\n"
            f"🎬 Fetching info + thumbnail…\n"
            f"🔗 `{watch_url}`"
        )

        info_task  = loop.run_in_executor(None, get_video_info,  watch_url)
        thumb_task = loop.run_in_executor(None, fetch_thumbnail, vid, pref_key)

        info              = await info_task
        img_data, got_key = await thumb_task

        title = info.get("title") or vid

        if img_data is None:
            failed.append(f"`{watch_url}` — thumbnail unavailable")
            continue

        await status.edit(
            f"⏳ **{idx} / {total}**\n\n"
            f"📤 Uploading: **{title[:55]}**"
        )

        fname = safe_filename(title) + ".jpg"
        fpath = os.path.join(DOWNLOAD_DIR, f"{vid}_{fname}")

        try:
            with open(fpath, "wb") as f:
                f.write(img_data)

            caption = build_caption(info, watch_url, got_key)

            await bot.send_file(
                event.chat_id,
                fpath,
                caption=caption,
                force_document=True,
                attributes=[],
            )
            success += 1

        except Exception as e:
            logger.error(f"Upload failed for {vid}: {e}")
            failed.append(f"`{watch_url}` — {str(e)[:80]}")

        finally:
            if os.path.exists(fpath):
                os.remove(fpath)

    if failed:
        fail_text = "\n".join(f"• {f}" for f in failed)
        await status.edit(
            f"✅ Done! **{success}/{total}** thumbnails sent.\n\n"
            f"❌ **Failed ({len(failed)}):**\n{fail_text}"
        )
    else:
        await status.edit(
            f"✅ All **{total}** thumbnail{'s' if total > 1 else ''} sent successfully!"
        )




async def main():
    await bot.start(bot_token=BOT_TOKEN)

    print("━" * 52)
    print("  🖼   YouTube Thumbnail Extractor Bot")
    print("  ✅   watch links + img.youtube.com links")
    print("  ✅   Rich captions • Named JPG docs • Batch")
    print("  📡   FastMotion M3U8 quality expander")
    print("  🌐   Flask keep-alive server running…")
    print("  🔁   Self-ping loop running…")
    print("  🔴   Waiting for links…")
    print("━" * 52)

    await bot.run_until_disconnected()


if __name__ == "__main__":
    # Start Flask web server in background thread (keeps Render awake)
    threading.Thread(target=run_flask, daemon=True).start()

    # Start self-ping loop in background thread
    threading.Thread(target=self_ping_loop, daemon=True).start()

    asyncio.run(main())
