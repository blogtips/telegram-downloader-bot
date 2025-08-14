# Telegram Downloader Bot (Render.com)

Bot Telegram nhận link Douyin/TikTok/Facebook/Instagram và gửi lại video (cố gắng không watermark) dùng `yt-dlp`.
Cách chạy tốt nhất: deploy Web Service trên Render với Docker (long-polling) + endpoint health check `/`.

## Biến môi trường
- `TELEGRAM_TOKEN` (bắt buộc): token từ @BotFather
- `USER_AGENT` (tùy chọn): UA cho request
- `YTDLP_COOKIES` (tùy chọn): đường dẫn tới cookies file (ví dụ `/app/cookies.txt`) để tải video FB/IG riêng tư

## Chạy local
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=xxxxxxxx:yyyy
python main.py
```

## Docker local
```bash
docker build -t tg-dl-bot .
docker run -e TELEGRAM_TOKEN=xxxxxxxx:yyyy -p 10000:10000 tg-dl-bot
```

## Triển khai Render
- Kết nối repo GitHub, tạo **Web Service**, Runtime Docker, Plan **Free**.
- Đặt env: `TELEGRAM_TOKEN` (required), `USER_AGENT` tùy chọn, `YTDLP_COOKIES` nếu cần.
- Health check path `/`.
- Scale = 1 instance.
