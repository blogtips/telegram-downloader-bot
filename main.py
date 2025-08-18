import os
import yt_dlp
from aiogram import Bot, Dispatcher, types
from aiogram.types import FSInputFile
from aiogram.utils import executor

BOT_TOKEN = os.getenv("BOT_TOKEN")  # lấy token từ Render Environment
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

def download_video(url: str, filename: str = "video.mp4"):
    """Tải video từ link bằng yt-dlp"""
    ydl_opts = {
        "outtmpl": filename,
        "format": "mp4",
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return filename

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.reply("👋 Gửi link TikTok / Douyin / Facebook / Instagram để tải video 🎥")

@dp.message_handler()
async def handle_url(msg: types.Message):
    url = msg.text.strip()
    if any(x in url for x in ["tiktok.com", "douyin.com", "facebook.com", "fb.watch", "instagram.com"]):
        await msg.reply("⏳ Đang tải video...")
        try:
            filename = "video.mp4"
            download_video(url, filename)
            video = FSInputFile(filename)
            await msg.reply_video(video)
            os.remove(filename)
        except Exception as e:
            await msg.reply(f"❌ Lỗi: {e}")
    else:
        await msg.reply("Không nhận diện được link.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
