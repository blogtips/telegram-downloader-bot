# Telegram Downloader Bot — Render (v5.4)

**Điểm mới v5.4 cho Facebook**:
- Thử thêm **mbasic.facebook.com** để lộ `video_redirect/?src=` (link file trực tiếp) — nhiều bot downloader dùng mẹo này.
- Parse cả `plugins/video.php?href=...` từ oEmbed khi video công khai.
- `/trace` hiển thị danh sách candidates; bạn có thể copy 1 link candidate gửi lại cho bot.

Giữ nguyên các tính năng cũ: cookies qua `COOKIES_TXT`, retry Telegram khi deploy, normalize short links, chạy web + polling cùng event loop, lệnh `/get`.
