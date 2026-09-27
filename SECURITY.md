# Bảo mật và quyền riêng tư

Idea Note là app chạy trên máy của bạn. Không có máy chủ của tác giả, không có tài khoản, không có thống kê.

## Dữ liệu của bạn nằm ở đâu
- Ghi chú, ảnh, chữ đọc từ ảnh, sao lưu: thư mục `data\` cạnh `IdeaNote.exe`, chỉ trên máy bạn.
- Repo này và các bản tải về **không chứa** dữ liệu của ai. Thư mục `data\` bị loại khỏi git (`.gitignore`) và khỏi gói cài đặt (`build_release.py` chỉ đóng gói chương trình).
- Mật khẩu ứng dụng Gmail (nếu bạn dùng nhắc hẹn qua email) được mã hoá bằng Windows DPAPI, chỉ tài khoản Windows của bạn giải mã được.

## App gọi ra ngoài internet ở đâu
Chỉ khi bạn bật hoặc bấm:
- Gửi email nhắc hẹn: kết nối `smtp.gmail.com:465` bằng tài khoản Gmail của chính bạn.
- Nút Lịch / Gmail: mở trang Google trong trình duyệt của bạn.
- Lần đầu dùng mic: tải mô hình nhận dạng giọng nói (Whisper) từ Hugging Face.
Trang giới thiệu (GitHub Pages) không có form và không thu thập gì: nút "Đăng ký miễn phí" chỉ là liên kết sang ducpt.com, việc đăng ký diễn ra trực tiếp ở đó.
Đọc chữ trong ảnh, tìm theo nghĩa và hỏi AI chạy trong máy (OCR của Windows hoặc Tesseract, Ollama ở `127.0.0.1`). Ghi chú và ảnh không bị gửi đi đâu.

## Bản điện thoại (cùng Wi-Fi)
- Máy chủ nghe ở cổng `41900`, có HTTPS bằng chứng chỉ tự ký tạo trên máy bạn.
- Truy cập từ máy khác cần khoá riêng của bạn (trong mã QR). Đoán sai khoá 20 lần thì bị khoá tạm 5 phút.
- Chỉ nhận yêu cầu có `Host` là `localhost` hoặc địa chỉ IP, và yêu cầu ghi dữ liệu phải cùng nguồn (`Origin`). Việc này chặn trang web lạ trong trình duyệt của bạn gọi vào app (DNS rebinding, CSRF).
- Có các header `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`; cookie `HttpOnly`, `SameSite=Strict`, thêm `Secure` khi qua HTTPS.
- Nút "Mở tường lửa" chỉ mở cổng cho mạng **Private**, không mở cho mạng Public.
- Đừng mở cổng `41900` ra internet (port forward). App thiết kế cho mạng nhà.

## Báo lỗ hổng
Vui lòng báo riêng qua **GitHub Security Advisories** của repo này (tab Security, "Report a vulnerability"), đừng đăng công khai trước khi có bản vá.
