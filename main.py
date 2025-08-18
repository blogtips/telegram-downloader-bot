import os
import asyncio
import yt_dlp

from aiogram import Bot, Dispatcher, types
from aiogram.types import InputFile
from aiogram.utils.executor import start_webhook

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

# URL public của service trên Render.
# Ưu tiên WEBHOOK_BASE_URL (bạn tự set), nếu không có dùng RENDER_EXTERNAL_URL (Render tự cấp khi deploy)
BASE_URL = os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
if not BASE_URL:
    # Có thể lần chạy đầu chưa có RENDER_EXTERNAL_URL (tuỳ Render). Bạn có thể set tay WEBHOOK_BASE_URL.
    print("⚠️ Missing WEBHOOK_BASE_URL/RENDER_EXTERNAL_URL; you may set it later and redeploy.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # Đơn giản: dùng token trong path
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", "10000"))  # Render đặt PORT

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

def download_video(url: str, filename: str = "video.mp4"):
    ydl_opts = {
        "outtmpl": filename,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return filename

@dp.message_handler(commands=["start"])
async def start_cmd(msg: types.Message):
    await msg.reply("👋 Gửi link TikTok / Douyin / Facebook / Instagram để tải video 🎥")

@dp.message_handler()
async def handle_url(msg: types.Message):
    url = (msg.text or "").strip()
    if any(x in url for x in ["tiktok.com", "douyin.com", "facebook.com", "fb.watch", "instagram.com"]):
        await msg.reply("⏳ Đang tải video...")
        filename = "video.mp4"
        try:
            download_video(url, filename)
            await msg.reply_video(InputFile(filename))
        except Exception as e:
            await msg.reply(f"❌ Lỗi: {e}")
        finally:
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except Exception:
                pass
    else:
        await msg.reply("Không nhận diện được link.")

async def on_startup(dp: Dispatcher):
    # Đăng ký webhook nếu đã có BASE_URL
    if BASE_URL:
        webhook_url = BASE_URL.rstrip("/") + WEBHOOK_PATH
        await bot.set_webhook(webhook_url)
        print(f"✅ Webhook set: {webhook_url}")
    else:
        print("⚠️ BASE_URL not set. Set WEBHOOK_BASE_URL or rely on RENDER_EXTERNAL_URL and redeploy.")

async def on_shutdown(dp: Dispatcher):
    await bot.delete_webhook()
    await bot.session.close()

if __name__ == "__main__":
    # start_webhook sẽ tự tạo web server aiohttp (health checks của Render vẫn trả 200 ở '/')
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
        skip_updates=True,
    )
