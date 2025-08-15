
# Telegram Downloader Bot — Render (v5.6)

## Điểm mới
- Resolver Facebook `share/r` mạnh nhất: unescape `&amp;`, quét `data-lynx-uri`, `data-store` (JSON), `og:video*`, `l.php`, thử cả `mbasic`/`oEmbed`.
- Nếu phát hiện `mbasic.facebook.com/video_redirect/?src=...` → **tải trực tiếp** (không qua yt-dlp).
- Lệnh: `/get <url>`, `/trace <url>`, `/cookiecheck`, `/debug`.
- Giữ: cookies qua `COOKIES_TXT`, retry khi khởi động, web+polling song song.

## Triển khai nhanh (Render/Docker)
1) Push code lên GitHub (giữ `Dockerfile` ở root).
2) Render → New → Web Service → Runtime Docker.
3) Env: `TELEGRAM_TOKEN` (bắt buộc), (tuỳ chọn) `COOKIES_TXT` nếu cần xem nội dung không công khai.
4) Deploy → Logs cần thấy: `HTTP server started...` và `Polling started and running.`

## Kiểm tra
- `/ping` → `pong ✅`
- `/trace <link_share>` → xem `normalized` + `candidates`.
- Nếu có candidate `video_redirect/?src=...` → `/get <candidate>` hoặc gửi lại link share để bot tự tải trực tiếp.
