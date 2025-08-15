
# Telegram Downloader Bot — Render (v5.5)

**Thay đổi chính:**
- Nếu resolver phát hiện `mbasic.facebook.com/video_redirect/?src=...`, bot sẽ **tải trực tiếp** file (không dùng yt-dlp), tăng tỉ lệ thành công cho các link share.
- Thêm lệnh `/cookiecheck` để kiểm tra nhanh bot có cookies chưa.
- Giữ `/get`, `/trace`, retry khi deploy, normalize short links, polling + web.

## Gợi ý debug nhanh
- `/trace <url>` xem `normalized` + candidates.
- Nếu có candidate `video_redirect/?src=...` → dùng `/get <candidate>` hoặc gửi thẳng link share, bot sẽ tự tải trực tiếp.
- Với nội dung không công khai → thêm env `COOKIES_TXT` (Netscape cookies).

