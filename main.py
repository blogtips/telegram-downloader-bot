import asyncio, os, re, tempfile, shutil, pathlib, logging, sys
from aiohttp import web
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import yt_dlp

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))

HELP_TEXT = (
    "Gửi link Douyin/TikTok/Facebook/Instagram.\n"
    "- Video công khai tải trực tiếp; video riêng tư có thể cần cookies.\n"
    "- Tối đa ~2GB theo Bot API."
)

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

def ua():
    return os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    )

def classify(url: str) -> str:
    u = url.lower()
    if any(d in u for d in ["douyin.com", "iesdouyin.com"]): return "douyin"
    if "tiktok.com" in u or "vm.tiktok.com" in u: return "tiktok"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "instagram.com" in u: return "instagram"
    return "unknown"

async def start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message("Chào bạn!\n" + HELP_TEXT)

async def ping_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message("pong ✅")

async def debug_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = msg.text or ""
    cap = msg.caption or ""
    await update.effective_chat.send_message(
        f"entities={msg.entities}\ncaption_entities={msg.caption_entities}\ntext={text}\ncaption={cap}"
    )

def extract_first_url(text: str) -> str | None:
    if not text: return None
    m = URL_RE.search(text)
    return m.group(1) if m else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message

    url = None
    if msg and msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                url = ent.url or (msg.text or "")[ent.offset: ent.offset + ent.length]
                break
    if not url and msg and msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type in ("url", "text_link"):
                url = ent.url or (msg.caption or "")[ent.offset: ent.offset + ent.length]
                break
    if not url:
        url = extract_first_url((msg.text or "") + " " + (msg.caption or ""))

    if not url:
        return

    src = classify(url)
    log.info("Received URL from %s: %s (src=%s)", chat.id, url, src)

    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VIDEO)
    except Exception as e:
        log.warning("send_chat_action failed: %s", e)

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
            fname = ydl.prepare_filename(info)
            if fname not in files and pathlib.Path(fname).exists():
                files.append(fname)

        sent = False
        for p in files:
            fp = pathlib.Path(p)
            if not fp.exists(): 
                continue
            if fp.stat().st_size > 2 * 1024 * 1024 * 1024:
                await chat.send_message("File quá lớn (>2GB) nên không thể gửi qua Bot API.")
                continue
            with fp.open("rb") as f:
                await chat.send_video(
                    video=f,
                    caption=f"✅ Đã xử lý: {src.upper()} (cố gắng không watermark)"
                )
            sent = True
            break

        if not sent:
            await chat.send_message("Không tìm thấy file đầu ra để gửi.")
    except Exception as e:
        log.exception("Download error: %s", e)
        await chat.send_message(f"❌ Lỗi tải/ghép video: {e}")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)

async def run_polling():
    if not BOT_TOKEN:
        log.error("Missing TELEGRAM_TOKEN environment variable.")
        return
    log.info("BOT_TOKEN seems set (length=%d, masked).", len(BOT_TOKEN))

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    await app.initialize()
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook deleted (if existed).")
    except Exception as e:
        log.warning("delete_webhook failed: %s", e)

    await app.start()
    log.info("Polling starting...")
    await app.updater.start_polling(allowed_updates=None)
    log.info("Polling started and running.")
    await asyncio.Event().wait()

def health_app():
    async def ok(_):
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/env", lambda req: web.json_response({
        "has_token": bool(BOT_TOKEN),
        "token_len": len(BOT_TOKEN) if BOT_TOKEN else 0
    }))
    return app

if __name__ == "__main__":
    log.info("Service booting, PORT=%s", PORT)
    loop = asyncio.get_event_loop()
    loop.create_task(run_polling())
    web.run_app(health_app(), port=PORT)
