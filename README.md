# Telegram Downloader Bot (Render.com) — v2

Cải tiến:
- Bắt link từ `entities`/`caption_entities` (URL & TEXT_LINK), regex fallback.
- Dùng `ChatAction.UPLOAD_VIDEO` đúng chuẩn PTB v21.
- Logging chi tiết để xem trong Render Logs.
- Hoạt động cả DM và group (lưu ý Privacy Mode).

## Gợi ý nếu bot không phản hồi
1. **Kiểm tra Logs** (Render → Service → Logs). Bạn sẽ thấy dòng `Received URL ...` khi bot nhận tin.
2. **Bật chat riêng & gửi `/start`** trước khi gửi link (bắt buộc cho lần đầu).
3. **Group**: nếu dùng trong nhóm, @BotFather `/setprivacy` → **Disable** để bot đọc tin nhắn thường.
4. **Chỉ 1 instance** chạy polling (Scale=1). Nếu >1 sẽ conflict.
5. **TELEGRAM_TOKEN** phải đúng.
6. **Link phải đầy đủ `http(s)://...`** (regex & entity đã hỗ trợ hầu hết trường hợp).
