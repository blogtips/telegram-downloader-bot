import asyncio, os, re, tempfile, shutil, pathlib, logging, sys, urllib.parse
from aiohttp import web, ClientSession, ClientTimeout
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

FB_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")

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

async def resolve_redirects(url: str) -> str:
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as s:
            for method in ("HEAD", "GET"):
                async with s.request(method, url, allow_redirects=True, headers={
                    "User-Agent": FB_MOBILE_UA,
                    "Referer": "https://m.facebook.com/",
                    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                }) as resp:
                    final = str(resp.url)
                    if final and final != url:
                        return final
        return url
    except Exception as e:
        log.warning("resolve_redirects failed for %s: %s", url, e)
        return url

def fb_mobile_variant(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "facebook.com" in host and not host.startswith("m."):
        new_host = "m.facebook.com"
        return urllib.parse.urlunparse(parsed._replace(netloc=new_host))
    return url

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

    if src == "facebook":
        url = await resolve_redirects(url)
        url = fb_mobile_variant(url)
        log.info("FB normalized URL: %s", url)

    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VIDEO)
    except Exception as e:
        log.warning("send_chat_action failed: %s", e)

    cookies_path = os.environ.get("YTDLP_COOKIES")

    headers = {"User-Agent": FB_MOBILE_UA if src == "facebook" else ua(),
               "Referer": "https://m.facebook.com/" if src == "facebook" else "https://www.google.com",
               "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"}

    ydl_opts = {
        "outtmpl": "%(title).200B.%(id)s.%(ext)s",
        "format": "mp4/best/bestvideo+bestaudio",
        "noplaylist": True,
        "quiet": True,
        "nocheckcertificate": True,
        "http_headers": headers,
        "merge_output_format": "mp4",
        "extractor_args": {
            "facebook": {
                "app_id": ["0"]
            }
        }
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
    except yt_dlp.utils.DownloadError as e:
        hint = ""
        if src == "facebook":
            hint = ("\n👉 Gợi ý FB: Link 'share/r' đã được chuyển hướng & dùng m.facebook.com. "
                    "Nếu vẫn lỗi, có thể video **không công khai** → cần cookies; "
                    "hãy thêm `cookies.txt` và set `YTDLP_COOKIES=/app/cookies.txt`.")
        await chat.send_message(f"❌ yt-dlp lỗi: {e}{hint}")
        log.exception("yt-dlp DownloadError: %s", e)
    except Exception as e:
        await chat.send_message(f"❌ Lỗi tải/ghép video: {e}")
        log.exception("Download error: %s", e)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)

async def start_web():
    app = web.Application()
    async def ok(_): return web.Response(text="ok")
    async def env(_): 
        return web.json_response({
            "has_token": bool(BOT_TOKEN),
            "token_len": len(BOT_TOKEN) if BOT_TOKEN else 0
        })
    app.router.add_get("/", ok)
    app.router.add_get("/env", env)

    runner = web.AppRunner(app, access_log=logging.getLogger("aiohttp.access"))
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    log.info("HTTP server started on 0.0.0.0:%s", PORT)

async def start_polling():
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

async def main():
    log.info("Service booting, PORT=%s", PORT)
    await asyncio.gather(
        start_web(),
        start_polling(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
