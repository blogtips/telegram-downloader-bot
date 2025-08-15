import asyncio, os, re, tempfile, shutil, pathlib, logging, sys, urllib.parse, json
from aiohttp import web, ClientSession, ClientTimeout
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from telegram.error import TimedOut, NetworkError, RetryAfter
import yt_dlp
from yt_dlp.utils import DownloadError, UnsupportedError

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
    "- Tối đa ~2GB theo Bot API.\n\n"
    "Lệnh: /ping, /debug, /get <url>, /trace <url>"
)

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

FB_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")

ASYNC_REDIRECT_HEADERS = {
    "User-Agent": FB_MOBILE_UA,
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Referer": "https://m.facebook.com/",
}

# Optional cookies via env
if os.environ.get("COOKIES_TXT"):
    try:
        path = "/app/cookies.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(os.environ["COOKIES_TXT"])
        os.environ["YTDLP_COOKIES"] = path
        log.info("cookies.txt created from COOKIES_TXT env, length=%d", len(os.environ["COOKIES_TXT"]))
    except Exception as e:
        log.warning("Failed to write cookies.txt from env: %s", e)

def ua():
    return os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    )

def classify(url: str) -> str:
    u = url.lower()
    if any(d in u for d in ["douyin.com", "iesdouyin.com", "v.douyin.com"]): return "douyin"
    if any(d in u for d in ["tiktok.com", "vm.tiktok.com"]): return "tiktok"
    if any(d in u for d in ["facebook.com", "fb.watch", "l.facebook.com", "m.facebook.com"]): return "facebook"
    if "instagram.com" in u: return "instagram"
    return "unknown"

def strip_tracking_params(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    bad = {"mibextid","sfnsn","s","fbclid","gclid","utm_source","utm_medium","utm_campaign","utm_term","utm_content","wtsid","refsrc"}
    qs = {k:v for k,v in qs.items() if k not in bad}
    new_q = urllib.parse.urlencode([(k,v2) for k,vals in qs.items() for v2 in vals])
    return urllib.parse.urlunparse(parsed._replace(query=new_q))

async def fetch(url: str, return_text=False):
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as s:
        async with s.get(url, allow_redirects=True, headers=ASYNC_REDIRECT_HEADERS) as resp:
            if return_text:
                return await resp.text()
            return str(resp.url)

async def fb_extract_real_from_html(url: str) -> str | None:
    """Try hard to find a concrete video URL from share/r landing page HTML."""
    try:
        html = await fetch(url, return_text=True)
    except Exception as e:
        log.warning("fb_extract_real_from_html fetch failed: %s", e)
        return None

    # 1) Meta refresh
    m = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\s*\d+\s*;\s*url=([^"\']+)["\']', html, re.I)
    if m:
        return urllib.parse.urljoin(url, urllib.parse.unquote(m.group(1)))

    # 2) og:video / og:url
    for prop in ("og:video:url","og:video:secure_url","og:video","og:url"):
        m = re.search(rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            return urllib.parse.urljoin(url, m.group(1))

    # 3) Anchors to reel/watch/video.php/story.php
    for rx in [
        r'href=["\'](/reel/[^"\']+)["\']',
        r'href=["\'](/watch/\?v=\d+)["\']',
        r'href=["\'](/video\.php\?[^"\']*v=\d+)[^"\']*["\']',
        r'href=["\'](/story\.php\?[^"\']*story_fbid=\d+[^"\']*)["\']',
    ]:
        m = re.search(rx, html, re.I)
        if m:
            return urllib.parse.urljoin(url, urllib.parse.unquote(m.group(1)))

    # 4) Look for JSON with video_id
    for m in re.finditer(r'{"video_id":"(\d+)"', html):
        vid = m.group(1)
        return f"https://m.facebook.com/watch/?v={vid}"

    return None

async def normalize_url(url: str, src: str) -> str:
    url = url.strip()
    if not url.startswith(("http://","https://")):
        url = "https://" + url
    url = strip_tracking_params(url)

    # Follow redirects for short/share links
    try:
        final = await fetch(url, return_text=False)
        if final:
            url = final
    except Exception as e:
        log.warning("normalize_url redirect failed for %s: %s", url, e)

    # Facebook-specific tweaks
    if src == "facebook":
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if "facebook.com" in host and not host.startswith("m."):
            url = urllib.parse.urlunparse(parsed._replace(netloc="m.facebook.com"))
        # If still share/r or a non-concrete path, parse HTML for real video URL
        if "/share/r/" in url or "/share/" in url:
            real = await fb_extract_real_from_html(url)
            if real:
                url = real
    return url

def extract_first_url(text: str) -> str | None:
    if not text: return None
    m = URL_RE.search(text)
    return m.group(1) if m else None

async def retry_telegram(call, what="tg-call", tries=8, base_delay=1.5):
    for i in range(tries):
        try:
            return await call()
        except RetryAfter as e:
            delay = getattr(e, "retry_after", 5)
        except (TimedOut, NetworkError):
            delay = base_delay * (2 ** i)
        except Exception:
            raise
        await asyncio.sleep(min(delay, 30))
    raise TimedOut(f"{what} timed out after retries")

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

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Dùng: /get <URL>")
        return
    update.effective_message.text = parts[1]
    await handle_message(update, context)

async def trace_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Dùng: /trace <URL>")
        return
    url = parts[1]
    src = classify(url)
    norm = await normalize_url(url, src)
    await update.effective_chat.send_message(f"src={src}\noriginal={url}\nnormalized={norm}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message

    # Extract URL
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
        await chat.send_message("Mình không thấy URL trong tin. Hãy gửi link trực tiếp hoặc dùng: /get <URL>")
        return

    src = classify(url)
    log.info("Received URL from %s: %s (src=%s)", chat.id, url, src)

    # Normalize/resolve
    try:
        norm_url = await normalize_url(url, src)
        log.info("Normalized URL: %s", norm_url)
    except Exception as e:
        log.warning("normalize_url error: %s", e)
        norm_url = url

    # If still facebook share link, inform user
    if src == "facebook" and ("/share/r/" in norm_url or "/share/" in norm_url):
        await chat.send_message("Link share của Facebook chưa trỏ tới URL video cụ thể. Hãy dùng /trace <URL> để xem URL sau khi chuẩn hoá, hoặc mở video và copy link dạng `/reel/<ID>` hay `watch/?v=<ID>`.")
        return

    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VIDEO)
    except Exception as e:
        log.warning("send_chat_action failed: %s", e)

    cookies_path = os.environ.get("YTDLP_COOKIES")
    headers = {
        "User-Agent": FB_MOBILE_UA if src == "facebook" else ua(),
        "Referer": "https://m.facebook.com/" if src == "facebook" else "https://www.google.com",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
    }

    ydl_opts = {
        "outtmpl": "%(title).200B.%(id)s.%(ext)s",
        "format": "mp4/best/bestvideo+bestaudio",
        "noplaylist": True,
        "quiet": True,
        "nocheckcertificate": True,
        "http_headers": headers,
        "merge_output_format": "mp4",
        "extractor_args": {"facebook": {"app_id": ["0"]}},
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
            info = ydl.extract_info(norm_url, download=True)
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
                await chat.send_video(video=f, caption=f"✅ Đã xử lý: {src.upper()} (cố gắng không watermark)")
            sent = True
            break

        if not sent:
            await chat.send_message("Không tìm thấy file đầu ra để gửi.")
    except UnsupportedError as e:
        hint = ""
        if src == "facebook":
            hint = ("\n👉 FB: Link share đã được xử lý. Nếu vẫn unsupported, có thể đây không phải trang video (ví dụ ảnh/bài viết), "
                    "hoặc cần cookies. Dùng /trace <URL> để xem link sau chuẩn hoá.")
        await chat.send_message(f"❌ Unsupported URL: {e}{hint}")
        log.exception("Unsupported URL: %s", e)
    except DownloadError as e:
        hint = ""
        if src == "facebook":
            hint = ("\n👉 FB: Nếu video **không công khai**, cần cookies. "
                    "Đặt biến `COOKIES_TXT` trên Render; bot sẽ tự tạo `/app/cookies.txt`.")
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
        return web.json_response({"has_token": bool(BOT_TOKEN), "token_len": len(BOT_TOKEN) if BOT_TOKEN else 0})
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
        while True:
            await asyncio.sleep(60)

    log.info("BOT_TOKEN seems set (length=%d, masked).", len(BOT_TOKEN))

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("trace", trace_cmd))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    async def _init(): await app.initialize()
    async def _delwh(): return await app.bot.delete_webhook(drop_pending_updates=True)
    async def _start(): await app.start()

    await retry_telegram(_init, "app.initialize")
    try:
        await retry_telegram(_delwh, "delete_webhook")
        log.info("Webhook deleted (if existed).")
    except Exception as e:
        log.warning("delete_webhook failed (continue): %s", e)
    await retry_telegram(_start, "app.start")

    log.info("Polling starting...")
    try:
        await app.updater.start_polling(allowed_updates=None, timeout=60, poll_interval=0.8)
        log.info("Polling started and running.")
    except Exception as e:
        log.exception("start_polling failed (will keep process alive): %s", e)

    while True:
        await asyncio.sleep(60)

async def main():
    log.info("Service booting, PORT=%s", PORT)
    await asyncio.gather(start_web(), start_polling())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
