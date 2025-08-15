# Telegram Downloader Bot — Render.com (v5)

Bot Telegram nhận link Douyin/TikTok/Facebook/Instagram và gửi lại video (cố gắng không watermark) bằng `yt-dlp`.

**Điểm nổi bật v5**
- Web server + polling chạy song song (không block event loop)
- Chuẩn hoá URL: unshorten `share/r`, `vm.tiktok.com`, `v.douyin.com`, `l.facebook.com`...
- Mobile UA/Referer cho Facebook, auto chuyển sang `m.facebook.com`
- Retry Telegram API khi khởi động (tránh deploy fail do timeout)
- Hỗ trợ cookies qua env `COOKIES_TXT` (không cần commit file)
- Lệnh `/start`, `/ping`, `/debug`

## Deploy trên Render
1. Push repo này lên GitHub.
2. Render → New → Web Service → Runtime **Docker**.
3. Env:
   - `TELEGRAM_TOKEN` (bắt buộc)
   - `COOKIES_TXT` (tùy chọn) – dán nội dung cookies.txt, bot sẽ tạo `/app/cookies.txt` và dùng.
   - `USER_AGENT` (tùy chọn).
4. Manual Deploy.
5. Logs cần thấy:
   - `HTTP server started on 0.0.0.0:10000`
   - `Webhook deleted (if existed).`
   - `Polling starting...` → `Polling started and running.`

## Test
- DM `/ping` → `pong ✅`
- Gửi link video TT/DY/FB/IG.
- FB/IG không công khai → cần `COOKIES_TXT`.

## Local
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=xxxx:yyyy
python main.py
```

**Lưu ý**
- Bot API ~2GB/file.
- Dùng group: tắt Privacy Mode qua @BotFather `/setprivacy`.
- Lỗi `Conflict getUpdates`: chỉ chạy **1 instance** cho mỗi token.
