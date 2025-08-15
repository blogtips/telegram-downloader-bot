# Telegram Downloader Bot — Render (v5.2)

V5.2 bổ sung:
- Lệnh `/get <url>` (tải nhanh) và `/trace <url>` (trả về URL sau chuẩn hoá để bạn thấy kết quả resolver).
- Resolver Facebook `share/r` mạnh hơn: đọc HTML để tìm `og:video`, `video_id`, anchors `/reel/`, `watch/?v=`, `video.php?v=...`.
- Giữ nguyên các tính năng v5/5.1: cookies qua env `COOKIES_TXT`, retry Telegram, polling+web cùng event loop, normalize short links.

Triển khai như các bản trước.
