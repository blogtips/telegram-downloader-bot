
# Telegram Downloader Bot — Render (v5.7)

Tập trung **khắc phục Facebook share/r** và case l.php.

## Điểm chính
- Bóc `share/r` qua `m.` / `www.` / `mbasic` / `oEmbed`, ưu tiên:
  - `mbasic.facebook.com/video_redirect/?src=...` (file trực tiếp)
  - `watch/?v=...`, `reel/...`, `video.php?...`, `plugins/video.php?href=...`
- Unwrap `l.php?u=...` (Facebook forwarder).
- **Fast-path tải trực tiếp** nếu thấy `video_redirect/?src=...` (không qua yt-dlp).
- Lệnh debug: `/trace`, `/tracejson`, `/cookiecheck`, `/debug`.

## Triển khai
1) Đưa code lên Git + giữ `Dockerfile` ở root.
2) Render → New → Web Service → Runtime Docker.
3) ENV: `TELEGRAM_TOKEN` (bắt buộc), `COOKIES_TXT` (tuỳ chọn).
4) Logs cần thấy:
   - `HTTP server started on 0.0.0.0:10000`
   - `Polling started and running.`
