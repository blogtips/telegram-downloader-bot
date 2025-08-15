import asyncio, os, re, tempfile, shutil, pathlib, logging, sys, urllib.parse
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
    "Lệnh: /ping, /debug, /get <url>, /trace <url>, /tracehtml <url>, /cookiecheck"
)

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

UA_FB_M = ("Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")
UA_FB_W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_FB_B = ("Mozilla/5.0 (Linux; Android 9; Nexus 5) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/99.0.4844.94 Mobile Safari/537.36")

HDR_M = {"User-Agent": UA_FB_M, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://m.facebook.com/"}
HDR_W = {"User-Agent": UA_FB_W, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://www.facebook.com/"}
HDR_B = {"User-Agent": UA_FB_B, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://mbasic.facebook.com/"}

# Cookies from env (optional)
if os.environ.get("COOKIES_TXT"):
    try:
        path = "/app/cookies.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(os.environ["COOKIES_TXT"])
        os.environ["YTDLP_COOKIES"] = path
        log.info("cookies.txt created from COOKIES_TXT env, length=%d", len(os.environ["COOKIES_TXT"]))
    except Exception as e:
        log.warning("Failed to write cookies.txt from env: %s", e)

def ua_default():
    return os.environ.get("USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36")

def classify(url: str) -> str:
    u = url.lower()
    if any(d in u for d in ["douyin.com", "iesdouyin.com", "v.douyin.com"]): return "douyin"
    if any(d in u for d in ["tiktok.com", "vm.tiktok.com"]): return "tiktok"
    if any(d in u for d in ["facebook.com", "fb.watch", "l.facebook.com", "m.facebook.com", "mbasic.facebook.com"]): return "facebook"
    if "instagram.com" in u: return "instagram"
    return "unknown"

def strip_tracking_params(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    bad = {"mibextid","sfnsn","s","fbclid","gclid","utm_source","utm_medium","utm_campaign","utm_term","utm_content","wtsid","refsrc"}
    qs = {k:v for k,v in qs.items() if k not in bad}
    new_q = urllib.parse.urlencode([(k,v2) for k,vals in qs.items() for v2 in vals])
    return urllib.parse.urlunparse(parsed._replace(query=new_q))

async def fetch(url: str, headers, return_text=False):
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as s:
        async with s.get(url, allow_redirects=True, headers=headers) as resp:
            if return_text:
                return await resp.text()
            return str(resp.url)

def _join(base, path):
    return urllib.parse.urljoin(base, path)

def _swap_host(url: str, host: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(p._replace(netloc=host))

def _first_match(html, base, patterns):
    for rx in patterns:
        m = re.search(rx, html, re.I)
        if m:
            return _join(base, urllib.parse.unquote(m.group(1)))
    return None

async def fb_collect_candidates(url: str):
    cands = []
    # m. and www.
    for hdr in (HDR_M, HDR_W):
        try:
            html = await fetch(url, headers=hdr, return_text=True)
        except Exception as e:
            log.warning("fb_collect_candidates fetch failed: %s", e)
            continue
        # meta refresh
        m = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\s*\d+\s*;\s*url=([^"\']+)["\']', html, re.I)
        if m: cands.append(_join(url, urllib.parse.unquote(m.group(1))))
        # og:video*, og:url
        for prop in ("og:video:url","og:video:secure_url","og:video","og:url"):
            m = re.search(rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
            if m: cands.append(_join(url, m.group(1)))
        # anchors
        a = _first_match(html, url, [
            r'href=["\'](/reel/[^"\']+)["\']',
            r'href=["\'](/watch/\?v=\d+)["\']',
            r'href=["\'](/video\.php\?[^"\']*v=\d+)[^"\']*["\']',
            r'href=["\'](/story\.php\?[^"\']*story_fbid=\d+[^"\']*)["\']',
        ])
        if a: cands.append(a)
        # inline ids
        m = re.search(r'{"video_id":"(\d+)"}', html)
        if m: cands.append(f"https://m.facebook.com/watch/?v={m.group(1)}")
        m = re.search(r'"reel_id":"(\d+)"', html)
        if m: cands.append(f"https://m.facebook.com/reel/{m.group(1)}")
    # mbasic (often has video_redirect)
    try:
        mbasic = _swap_host(url, "mbasic.facebook.com")
        html_b = await fetch(mbasic, headers=HDR_B, return_text=True)
        m = re.search(r'href=["\'](/video_redirect/\?src=[^"\']+)["\']', html_b, re.I)
        if m: cands.append(_join(mbasic, m.group(1)))
        a = _first_match(html_b, mbasic, [
            r'href=["\'](/reel/[^"\']+)["\']',
            r'href=["\'](/watch/\?v=\d+)["\']',
            r'href=["\'](/video\.php\?[^"\']*v=\d+)[^"\']*["\']'
        ])
        if a: cands.append(a)
    except Exception as e:
        log.warning("mbasic fetch failed: %s", e)
    # oEmbed
    try:
        enc = urllib.parse.quote(url, safe="")
        oembed = f"https://www.facebook.com/plugins/video/oembed.json/?url={enc}"
        async with ClientSession(timeout=ClientTimeout(total=10)) as s:
            async with s.get(oembed, headers=HDR_W) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    html = data.get("html") or ""
                    m = re.search(r'src=["\']([^"\']+plugins/video\.php\?href=[^"\']+)["\']', html)
                    if m:
                        cands.append(urllib.parse.unquote(m.group(1)))
    except Exception as e:
        log.warning("oEmbed fetch failed: %s", e)

    # dedup & sort
    seen, dedup = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            dedup.append(c)
    def score(u):
        if "video_redirect/?src=" in u: return 0
        if "/watch/?" in u: return 1
        if "/reel/" in u: return 2
        if "/video.php" in u: return 3
        if "plugins/video.php" in u: return 4
        if "/story.php" in u: return 5
        return 6
    dedup.sort(key=score)
    return dedup

async def normalize_url(url: str, src: str):
    orig = url.strip()
    if not orig.startswith(("http://","https://")):
        orig = "https://" + orig
    url = strip_tracking_params(orig)

    try:
        final_m = await fetch(url, headers=HDR_M, return_text=False)
        if final_m: url = final_m
    except Exception as e:
        log.warning("normalize mobile redirect failed: %s", e)
    try:
        final_w = await fetch(url, headers=HDR_W, return_text=False)
        if final_w: url = final_w
    except Exception as e:
        log.warning("normalize desktop redirect failed: %s", e)

    candidates = []
    if src == "facebook" and ("facebook.com/share/" in url):
        candidates = await fb_collect_candidates(url)
        if candidates:
            url = candidates[0]
        p = urllib.parse.urlparse(url)
        if "facebook.com" in p.netloc and not p.netloc.startswith("m.") and "video_redirect" not in url:
            url = urllib.parse.urlunparse(p._replace(netloc="m.facebook.com"))
    return url, candidates

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

async def cookiecheck_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    has = bool(os.environ.get("YTDLP_COOKIES")) and pathlib.Path(os.environ.get("YTDLP_COOKIES")).exists()
    await update.effective_chat.send_message(f"cookies_present={has} path={os.environ.get('YTDLP_COOKIES','')}")

async def trace_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Dùng: /trace <URL>")
        return
    url = parts[1]
    src = classify(url)
    norm, cands = await normalize_url(url, src)
    msg = f"src={src}\noriginal={url}\nnormalized={norm}"
    if cands:
        msg += "\ncandidates:\n" + "\n".join(f"- {c}" for c in cands[:10])
    await update.effective_chat.send_message(msg)

async def tracehtml_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # alias to /trace for now (we already parse HTML)
    await trace_cmd(update, context)

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Dùng: /get <URL>")
        return
    update.effective_message.text = parts[1]
    await handle_message(update, context)

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
        await chat.send_message("Mình không thấy URL trong tin. Hãy gửi link trực tiếp hoặc dùng: /get <URL>")
        return

    src = classify(url)
    log.info("Received URL from %s: %s (src=%s)", chat.id, url, src)

    try:
        norm_url, cands = await normalize_url(url, src)
        log.info("Normalized URL: %s", norm_url)
        if cands: log.info("Candidates: %s", cands)
    except Exception as e:
        log.warning("normalize_url error: %s", e)
        norm_url, cands = url, []

    # Special fast-path: mbasic video_redirect => direct mp4; download with aiohttp to improve success
    if src == "facebook" and "video_redirect/?src=" in norm_url:
        try:
            await chat.send_message("🔎 Tìm thấy link file trực tiếp, đang tải...")
            timeout = ClientTimeout(total=600)
            async with ClientSession(timeout=timeout) as s:
                async with s.get(norm_url, headers=HDR_B, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    # final url may be CDN mp4
                    final_url = str(resp.url)
                    # stream to temp file
                    fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
                    with os.fdopen(fd, "wb") as out:
                        while True:
                            chunk = await resp.content.read(512 * 1024)
                            if not chunk: break
                            out.write(chunk)
            # send video
            if pathlib.Path(tmp_path).stat().st_size > 2*1024*1024*1024:
                await chat.send_message("File quá lớn (>2GB) nên không thể gửi qua Bot API.")
            else:
                with open(tmp_path, "rb") as f:
                    await chat.send_video(video=f, caption="✅ Đã tải trực tiếp từ mbasic (không dùng yt-dlp)")
            os.remove(tmp_path)
            return
        except Exception as e:
            log.exception("direct mbasic download failed: %s", e)
            await chat.send_message(f"❌ Tải trực tiếp thất bại, sẽ thử yt-dlp. Lý do: {e}")

    # If still share link unresolved
    if src == "facebook" and ("facebook.com/share/" in norm_url):
        msg = "Link share của Facebook chưa trỏ tới URL video cụ thể."
        if cands:
            msg += "\nMình gợi ý các URL khả dĩ (hãy thử một trong các link sau):\n" + "\n".join(f"- {c}" for c in cands[:10])
        msg += "\nBạn cũng có thể dùng /trace <URL> để xem chi tiết."
        await chat.send_message(msg)
        return

    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VIDEO)
    except Exception as e:
        log.warning("send_chat_action failed: %s", e)

    cookies_path = os.environ.get("YTDLP_COOKIES")
    headers = {
        "User-Agent": UA_FB_M if src == "facebook" else ua_default(),
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
            hint = ("\n👉 FB: Nếu vẫn unsupported, có thể link share không dẫn tới trang video (ảnh/bài viết). "
                    "Dùng /trace để xem candidates, hoặc mở video và copy trực tiếp URL `/reel/<ID>` / `watch/?v=<ID>`.")
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

async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"entities={update.effective_message.entities}\n"
        f"caption_entities={update.effective_message.caption_entities}\n"
        f"text={update.effective_message.text}\n"
        f"caption={update.effective_message.caption}"
    )

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
    app.add_handler(CommandHandler("cookiecheck", cookiecheck_cmd))
    app.add_handler(CommandHandler("trace", trace_cmd))
    app.add_handler(CommandHandler("tracehtml", tracehtml_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    async def _init(): await app.initialize()
    async def _delwh(): return await app.bot.delete_webhook(drop_pending_updates=True)
    async def _start(): await app.start()

    # retry around startup network calls
    for fn, name in ((_init, "app.initialize"), (_delwh, "delete_webhook"), (_start, "app.start")):
        try:
            await retry_telegram(fn, name)
            if name == "delete_webhook":
                log.info("Webhook deleted (if existed).")
        except Exception as e:
            log.warning("%s failed (continue running): %s", name, e)

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