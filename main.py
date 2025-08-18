
import asyncio, os, re, tempfile, shutil, pathlib, logging, sys, urllib.parse, json, html as html_lib
from aiohttp import web, ClientSession, ClientTimeout
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from telegram.error import TimedOut, NetworkError, RetryAfter
import yt_dlp
from yt_dlp.utils import DownloadError, UnsupportedError

logging.basicConfig(stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))

HELP_TEXT = (
    "Gửi link Douyin/TikTok/Facebook/Instagram.\n"
    "- Video công khai tải trực tiếp; video riêng tư có thể cần cookies.\n"
    "- Tối đa ~2GB theo Bot API.\n\n"
    "Lệnh: /ping, /debug, /get <url>, /trace <url>, /tracejson <url>, /cookiecheck"
)

URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

UA_FB_M = ("Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")
UA_FB_W = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_FB_B = ("Mozilla/5.0 (Linux; Android 9; Nexus 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.94 Mobile Safari/537.36")

HDR_M = {"User-Agent": UA_FB_M, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://m.facebook.com/"}
HDR_W = {"User-Agent": UA_FB_W, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://www.facebook.com/"}
HDR_B = {"User-Agent": UA_FB_B, "Accept-Language": "en-US,en;q=0.9,vi;q=0.8", "Referer": "https://mbasic.facebook.com/"}

# Cookies
if os.environ.get("COOKIES_TXT"):
    try:
        path = "/app/cookies.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(os.environ["COOKIES_TXT"])
        os.environ["YTDLP_COOKIES"] = path
        log.info("cookies.txt created from COOKIES_TXT env, length=%d", len(os.environ["COOKIES_TXT"]))
    except Exception as e:
        log.warning("Failed to write cookies.txt from env: %s", e)

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
    bad = {"mibextid","sfnsn","s","fbclid","gclid","utm_source","utm_medium","utm_campaign","utm_term","utm_content","wtsid","refsrc","_rdr"}
    qs = {k:v for k,v in qs.items() if k not in bad}
    new_q = urllib.parse.urlencode([(k,v2) for k,vals in qs.items() for v2 in vals])
    return urllib.parse.urlunparse(parsed._replace(query=new_q))

async def http_get(url: str, headers, return_text=False):
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as s:
        async with s.get(url, allow_redirects=True, headers=headers) as resp:
            if return_text:
                return await resp.text()
            return str(resp.url)

def _swap_host(url: str, host: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(p._replace(netloc=host))

def _add(cands, base_url, val, why):
    if not val: return
    val = html_lib.unescape(val)
    val = urllib.parse.urljoin(base_url, val)
    if val not in [c["url"] for c in cands]:
        cands.append({"url": val, "why": why})

def _unwrap_lphp(url: str) -> str:
    # facebook l.php?u=<encoded target>
    p = urllib.parse.urlparse(url)
    if p.path.endswith("/l.php"):
        q = urllib.parse.parse_qs(p.query)
        u = q.get("u", [None])[0]
        if u:
            return urllib.parse.unquote(u)
    return url

async def fb_collect_candidates(url: str):
    cands = []
    # 1) Unwrap l.php early
    unwrapped = _unwrap_lphp(url)
    if unwrapped != url:
        _add(cands, url, unwrapped, "l.php unwrap")
        url = unwrapped

    # 2) m. and www.
    for hdr, tag in ((HDR_M, "m.html"), (HDR_W, "www.html")):
        try:
            html = await http_get(url, headers=hdr, return_text=True)
        except Exception as e:
            log.warning("fb_collect fetch failed: %s", e); html = ""

        m = re.search(r'<meta[^>]+http-equiv=[\'"]refresh[\'"][^>]+content=[\'"]\s*\d+\s*;\s*url=([^\'"]+)[\'"]', html, re.I)
        if m: _add(cands, url, m.group(1), f"{tag} meta refresh")

        for prop in ("og:video:url","og:video:secure_url","og:video","og:url"):
            m = re.search(rf'<meta[^>]+property=[\'"]{re.escape(prop)}[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            if m: _add(cands, url, m.group(1), f"{tag} {prop}")

        for rx, why in [
            (r'href=[\'"](/reel/[^\'"]+)[\'"]', f"{tag} reel"),
            (r'href=[\'"](/watch/\?v=\d+)[\'"]', f"{tag} watch"),
            (r'href=[\'"](/video\.php\?[^\'"]*v=\d+)[^\'"]*[\'"]', f"{tag} video.php"),
            (r'href=[\'"](/story\.php\?[^\'"]*story_fbid=\d+[^\'"]*)[\'"]', f"{tag} story"),
            (r'href=[\'"](/l\.php\?u=[^\'"]+)[\'"]', f"{tag} l.php"),
        ]:
            m = re.search(rx, html, re.I)
            if m: _add(cands, url, m.group(1), why)

        for m_ in re.finditer(r'data-lynx-uri=[\'"]([^\'"]+)[\'"]', html, re.I):
            _add(cands, url, urllib.parse.unquote(m_.group(1)), f"{tag} data-lynx-uri")

        for m_ in re.finditer(r'data-store=[\'"]([^\'"]+)[\'"]', html, re.I):
            try:
                j = html_lib.unescape(m_.group(1))
                d = json.loads(j)
                href = d.get("href") or d.get("src") or d.get("finalUrl")
                if href: _add(cands, url, href, f"{tag} data-store")
            except Exception: pass

        m = re.search(r'{"video_id":"(\d+)"}', html)
        if m: _add(cands, url, f"https://m.facebook.com/watch/?v={m.group(1)}", f"{tag} inline video_id")
        m = re.search(r'"reel_id":"(\d+)"', html)
        if m: _add(cands, url, f"https://m.facebook.com/reel/{m.group(1)}", f"{tag} inline reel_id")

    # 3) mbasic
    try:
        mbasic = _swap_host(url, "mbasic.facebook.com")
        html_b = await http_get(mbasic, headers=HDR_B, return_text=True)
        for m in re.finditer(r'href=[\'"](/video_redirect/\?src=[^\'"]+)[\'"]', html_b, re.I):
            _add(cands, mbasic, m.group(1), "mbasic video_redirect")
        for rx, why in [
            (r'href=[\'"](/reel/[^\'"]+)[\'"]', "mbasic reel"),
            (r'href=[\'"](/watch/\?v=\d+)[\'"]', "mbasic watch"),
            (r'href=[\'"](/video\.php\?[^\'"]*v=\d+)[^\'"]*[\'"]', "mbasic video.php"),
        ]:
            m = re.search(rx, html_b, re.I)
            if m: _add(cands, mbasic, m.group(1), why)
    except Exception as e:
        log.warning("mbasic fetch failed: %s", e)

    # 4) oEmbed
    try:
        enc = urllib.parse.quote(url, safe="")
        oembed = f"https://www.facebook.com/plugins/video/oembed.json/?url={enc}"
        async with ClientSession(timeout=ClientTimeout(total=10)) as s:
            async with s.get(oembed, headers=HDR_W) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    html = data.get("html") or ""
                    m = re.search(r'src=[\'"]([^\'"]+plugins/video\.php\?href=[^\'"]+)[\'"]', html)
                    if m: _add(cands, url, urllib.parse.unquote(m.group(1)), "oEmbed plugins/video.php")
    except Exception as e:
        log.warning("oEmbed fetch failed: %s", e)

    # dedup and sort
    seen, out = set(), []
    for c in cands:
        u = c["url"]
        if u and u not in seen:
            seen.add(u); out.append(c)

    def score(u):
        if "video_redirect/?src=" in u: return 0
        if "/watch/?" in u: return 1
        if "/reel/" in u: return 2
        if "/video.php" in u: return 3
        if "plugins/video.php" in u: return 4
        if "/story.php" in u: return 5
        if "/l.php" in u: return 6
        return 7
    out.sort(key=lambda c: score(c["url"]))
    return out

async def normalize_url(url: str, src: str):
    orig = url.strip()
    if not orig.startswith(("http://","https://")):
        orig = "https://" + orig
    url = strip_tracking_params(orig)

    # handle l.php unwrap quick
    url = _unwrap_lphp(url)

    # follow redirects quickly
    for hdr in (HDR_M, HDR_W):
        try:
            final = await http_get(url, headers=hdr, return_text=False)
            if final: url = final
        except Exception as e:
            log.warning("redirect follow failed: %s", e)

    candidates = []
    if src == "facebook" and ("facebook.com/share/" in url):
        cands = await fb_collect_candidates(url)
        candidates = cands
        if cands:
            best = cands[0]["url"]
            p = urllib.parse.urlparse(best)
            if "facebook.com" in p.netloc and not p.netloc.startswith("m.") and "video_redirect" not in best:
                best = urllib.parse.unparse(p._replace(netloc="m.facebook.com"))
            url = best
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

async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"entities={update.effective_message.entities}\n"
        f"caption_entities={update.effective_message.caption_entities}\n"
        f"text={update.effective_message.text}\n"
        f"caption={update.effective_message.caption}"
    )

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
        msg += "\ncandidates (top 10):\n" + "\n".join(f"- {c['url']}  [{c['why']}]" for c in cands[:10])
    await update.effective_chat.send_message(msg)

async def tracejson_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Dùng: /tracejson <URL>")
        return
    url = parts[1]
    src = classify(url)
    norm, cands = await normalize_url(url, src)
    data = {"src": src, "original": url, "normalized": norm, "candidates": cands[:20]}
    # escape markdownv2 special chars
    s = json.dumps(data, ensure_ascii=False, indent=2).replace("\\","\\\\").replace("_","\\_").replace("*","\\*").replace("[","\\[").replace("]","\\]").replace("(","\\(").replace(")","\\)").replace("~","\\~").replace("`","\\`").replace(">","\\>").replace("#","\\#").replace("+","\\+").replace("-","\\-").replace("=","\\=").replace("|","\\|").replace("{","\\{").replace("}","\\}").replace(".","\\.")
    await update.effective_chat.send_message("```json\n" + s + "\n```", parse_mode="MarkdownV2")

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
        if cands: log.info("Candidates: %s", cands[:5])
    except Exception as e:
        log.warning("normalize_url error: %s", e)
        norm_url, cands = url, []

    # Fast-path: direct file via mbasic video_redirect
    if src == "facebook" and "video_redirect/?src=" in norm_url:
        try:
            await chat.send_message("🔎 Tìm thấy link file trực tiếp, đang tải...")
            timeout = ClientTimeout(total=600)
            async with ClientSession(timeout=timeout) as s:
                async with s.get(norm_url, headers=HDR_B, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
                    with os.fdopen(fd, "wb") as out:
                        while True:
                            chunk = await resp.content.read(512 * 1024)
                            if not chunk: break
                            out.write(chunk)
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

    if src == "facebook" and ("facebook.com/share/" in norm_url):
        msg = "Link share của Facebook chưa trỏ tới URL video cụ thể."
        if cands:
            msg += "\nMình gợi ý các URL khả dĩ:\n" + "\n".join(f"- {c['url']}  [{c['why']}]" for c in cands[:10])
        msg += "\nBạn cũng có thể dùng /trace hoặc /tracejson để xem chi tiết."
        await chat.send_message(msg)
        return

    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.UPLOAD_VIDEO)
    except Exception as e:
        log.warning("send_chat_action failed: %s", e)

    cookies_path = os.environ.get("YTDLP_COOKIES")
    headers = {
        "User-Agent": UA_FB_M if src == "facebook" else UA_FB_W,
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
    app.add_handler(CommandHandler("tracejson", tracejson_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    async def _init(): await app.initialize()
    async def _delwh(): return await app.bot.delete_webhook(drop_pending_updates=True)
    async def _start(): await app.start()

    for fn, name in ((_init, "app.initialize"), (_delwh, "delete_webhook"), (_start, "app.start")):
        try:
            await retry_telegram(fn, name="startup")
        except Exception as e:
            log.warning("%s failed (continue): %s", name, e)

    log.info("Polling starting...")
    try:
        await app.updater.start_polling(allowed_updates=None, timeout=60, poll_interval=0.8)
        log.info("Polling started and running.")
    except Exception as e:
        log.exception("start_polling failed: %s", e)

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
