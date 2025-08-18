# Telegram Video Downloader Bot

Bot tải video từ TikTok, Douyin, Facebook, Instagram.

## Cách deploy trên Render

1. Fork repo này.
2. Tạo service mới trên [Render.com](https://render.com).
3. Chọn **Background Worker**.
4. Add biến môi trường:
   - `BOT_TOKEN=...` (token từ @BotFather).
5. Deploy → bot chạy ngay.

## Cách dùng
- Gõ `/start` để bắt đầu.
- Gửi link video TikTok/Douyin/Facebook/Instagram → bot sẽ gửi lại file video.
