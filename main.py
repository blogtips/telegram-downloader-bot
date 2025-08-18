import os
import yt_dlp
from aiogram import Bot, Dispatcher, types
from aiogram.types import InputFile   # <— dùng InputFile thay cho FSInputFile
from aiogram.utils import executor

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

def download_video(url: str, filename: str = "video.mp4"):
    """Tải video từ link bằng yt-dlp"""
    ydl_opts = {
        "outtmpl": filename,
        # cố gắng lấy mp4; nếu site tách audio/video, yt-dlp sẽ merge bằng ffmpeg
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return filename

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.reply("👋 Gửi link TikTok / Douyin / Facebook / Instagram để tải video 🎥")

@dp.message_handler()
async def handle_url(msg: types.Message):
    url = (msg.text or "").strip()
    if any(x in url for x in ["tiktok.com", "douyin.com", "facebook.com", "fb.watch", "instagram.com"]):
        await msg.reply("⏳ Đang tải video...")
        filename = "video.mp4"
        try:
            download_video(url, filename)
            video = InputFile(filename)  # <— dùng InputFile
            await msg.reply_video(video)
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

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
