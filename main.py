import asyncio, os, re, tempfile, shutil, pathlib
from aiohttp import web
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import yt_dlp

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))  # Render sets PORT for web services

HELP_TEXT = (
    "Gửi link Douyin/TikTok/Facebook/Instagram.\n"
    "- Video công khai tải trực tiếp; video riêng tư có thể cần cookies.\n"
    "- Tối đa ~2GB theo Bot API (giới hạn Telegram)."
)

def ua():
    return os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    )

def classify(url: str) -> str:
    u = url.lower()
    if any(d in u for d in ["douyin.com", "iesdouyin.com"]): return "douyin"
    if "tiktok.com" in u: return "tiktok"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "instagram.com" in u: return "instagram"
    return "unknown"

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Chào bạn!\n" + HELP_TEXT)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    url = msg.text.strip()
    if not re.match(r"^https?://", url):
        return

    src = classify(url)
    await msg.reply_chat_action("upload_video")

    cookies_path = os.environ.get("YTDLP_COOKIES")

    ydl_opts = {
        "outtmpl": "%(title).200B.%(id)s.%(ext)s",
        "format": "mp4/best",
        "noplaylist": True,
        "quiet": True,
        "nocheckcertificate": True,
        "http_headers": {"User-Agent": ua()},
        "merge_output_format": "mp4",
    }
    if cookies_path and pathlib.Path(cookies_path).exists():
        ydl_opts["cookiefile"] = cookies_path

    tmpdir = tempfile.mkdtemp(prefix="dl_")
    cwd = os.getcwd()
    files = []

    def hook(d):
        if d.get("status") == "finished":
            name = d.get("filename")
            if name:
                files.append(name)

    ydl_opts["progress_hooks"] = [hook]

    try:
        os.chdir(tmpdir)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # fallback name
            fname = ydl.prepare_filename(info)
            if fname not in files and pathlib.Path(fname).exists():
                files.append(fname)

        # send first available file
        sent = False
        for p in files:
            fp = pathlib.Path(p)
            if not fp.exists(): 
                continue
            # Basic size guard: Telegram Bot API limit ~2GB
            if fp.stat().st_size > 2 * 1024 * 1024 * 1024:
                await msg.reply_text("File quá lớn (>2GB) nên không thể gửi qua Bot API.")
                continue
            with fp.open("rb") as f:
                await msg.reply_video(
                    video=f,
                    caption=f"✅ Đã xử lý: {src.upper()} (cố gắng không watermark)",
                )
            sent = True
            break

        if not sent:
            await msg.reply_text("Không tìm thấy file đầu ra để gửi.")
    except Exception as e:
        await msg.reply_text(f"❌ Lỗi tải/ghép video: {e}")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)

async def run_polling():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()  # keep running

def health_app():
    async def ok(_): 
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", ok)
    return app

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_TOKEN")
    loop = asyncio.get_event_loop()
    loop.create_task(run_polling())
    web.run_app(health_app(), port=PORT)
