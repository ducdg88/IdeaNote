# Idea Note

App Windows ghi chú nhanh thay cho việc tự gửi vào Zalo hay Facebook: chữ và ảnh, gom theo dự án, việc cần làm, việc gấp, nhắc hẹn, chép ngữ cảnh sang Claude. Có sẵn **bản điện thoại** (mở bằng trình duyệt, quét mã QR) và **kho kiến thức hỏi được bằng AI**, tìm được cả chữ trong ảnh chụp.

**Riêng tư từ gốc:** mọi dữ liệu nằm trên máy bạn, không tài khoản, không máy chủ của tác giả, không thống kê. Xem [SECURITY.md](SECURITY.md).

## Cài trên máy tính (1 lần là có cả bản điện thoại)
1. Vào mục **Releases** của repo này, tải `IdeaNote-v…-win64.zip`.
2. Giải nén ra một thư mục của bạn, ví dụ `D:\IdeaNote` (đừng để trong `Program Files`, app cần ghi dữ liệu cạnh file chạy).
3. Bấm đúp `IdeaNote.exe`. Windows SmartScreen hỏi thì bấm **More info** rồi **Run anyway** (app tự build, chưa ký số).
4. Muốn bật cùng Windows để nhắc hẹn luôn chạy: Cài đặt, tick "Tự chạy ngầm khi bật máy".

Dữ liệu nằm ở thư mục `data\` cạnh `IdeaNote.exe` (ghi chú `notes.json`, ảnh `img\`, sao lưu mỗi ngày ở `backup\`, giữ 30 bản). Nâng cấp bản mới: giải nén đè lên, **giữ nguyên thư mục `data\`**.

## Dùng trên điện thoại
Không cần cài gì thêm: app trên máy tính có sẵn máy chủ nhỏ ở cổng 41900.
1. Điện thoại bắt **cùng Wi-Fi** với máy tính.
2. Trong app bấm **Điện thoại**, quét mã QR bằng camera hoặc Zalo.
3. Lần đầu trình duyệt báo "Kết nối không riêng tư" (chứng chỉ do chính máy tính tạo): **Nâng cao**, rồi **Tiếp tục truy cập**.
4. Chọn **Thêm vào màn hình chính** để có icon như app.
5. Điện thoại không vào được: trong hộp Điện thoại bấm **Mở tường lửa cho điện thoại** (cần bấm Yes quyền Admin một lần, chỉ mở cho mạng Private).

Máy tính phải đang bật và mở Idea Note (chạy ngầm ở khay cũng được) thì điện thoại mới vào được.

## Dùng
- Gõ ở ô trên cùng, **Ctrl+Enter** lưu. **Ctrl+V** dán ảnh, kéo thả ảnh vào. Ô "Dự án" để gom theo dự án.
- Danh sách giữa: 1 dòng mỗi ghi chú, **↑ ↓** chuyển, bấm vòng tròn để đánh dấu xong, **Delete** xoá (vào thùng rác).
- Khung phải: sửa trực tiếp (tự lưu) chữ, trạng thái, gấp, dự án, ngày, giờ nhắc, tiến độ, ảnh.
- Cửa sổ thu nhỏ được tới khoảng 600×450: hẹp thì cột trái tự ẩn (bấm **☰** để hiện).
- **Chép cho Claude** (Ctrl+Shift+C): chép cả danh sách đang xem kèm đường dẫn ảnh. **Xuất gói**: thư mục CONTEXT.md kèm ảnh.
- **Ctrl+Alt+N** ở bất cứ đâu: mở app để ghi ngay.

### Tìm kiếm (Ctrl+F)
- Danh sách hiện ngay khi gõ. Gõ nhiều từ thì ghi chú phải chứa đủ các từ (không cần đúng thứ tự, không cần gõ dấu).
- Kết quả chia nhóm: khớp tiêu đề, khớp nội dung, khớp chữ trong ảnh. Cạnh tiêu đề có dòng khớp đầu tiên, dòng lấy từ ảnh có dấu 📝.

### Xem ảnh
Bấm vào ảnh bất kỳ để mở trình xem toàn màn hình:
- **← →** (hoặc vuốt ngang trên điện thoại) chuyển ảnh, dải ảnh nhỏ bên dưới để nhảy nhanh.
- **Lăn chuột** hoặc chụm 2 ngón để phóng to đúng chỗ, kéo để di chuyển, **bấm đúp** để vừa khung hoặc 100%.
- **Chữ trong ảnh** (phím T): xem và chép chữ máy đọc được trong ảnh.

### Kho kiến thức: Hỏi (Ctrl+K)
Idea Note vừa là chỗ ghi việc vừa là chỗ lưu kiến thức (ảnh chụp slide, màn hình, ghi chú buổi học).
- **Máy tự đọc chữ trong mọi ảnh** ở nền. Ô Tìm tìm được cả chữ trong ảnh.
- Bấm **Hỏi kiến thức** rồi hỏi bằng lời thường, ví dụ "kiến thức về CoS?". AI trả lời ngắn, **mỗi ý ghi nguồn [1] [2]**; bên dưới là thẻ nguồn: ghi chú nào, dự án nào, ngày nào, ảnh số mấy. Bấm thẻ để mở đúng ghi chú, bấm ảnh để mở đúng ảnh đó.
- Chọn phạm vi "Tất cả" hoặc 1 dự án. Tìm theo nghĩa hiểu cả viết tắt (CoS là Chief of Staff) và câu hỏi không trùng chữ.

Mẹo lưu kiến thức để hỏi lại cho trúng:
1. Gom mỗi nguồn học vào **1 dự án**, mỗi buổi 1 ghi chú.
2. Đặt **Mục chính** rõ ràng, ví dụ "Buổi 8: đội agent chạy 24/7".
3. Chụp nhiều slide vào **cùng 1 ghi chú** của buổi đó; thêm 1 đến 3 dòng tự tóm tắt ý chính (AI ưu tiên chữ bạn tự viết).

Cần có (tất cả chạy **trong máy**, không gửi ra ngoài):
- Đọc chữ trong ảnh: OCR có sẵn của Windows. Muốn đọc đúng dấu tiếng Việt: cài Tesseract kèm gói `vie`, hoặc chép file `vie.traineddata` vào `models\tessdata\` (app tự đọc lại toàn bộ ảnh bằng gói mới).
- Tìm theo nghĩa và trả lời: [Ollama](https://ollama.com) với `ollama pull bge-m3` và `ollama pull qwen2.5:7b` (máy yếu dùng `qwen2.5:3b`). Không có Ollama thì vẫn tìm theo từ khoá và liệt kê ghi chú nguồn.

### Giọng nói
- Nút **Nói** trong ô ghi nhanh: nói tới đâu chữ vào ô tới đó, sửa rồi Ctrl+Enter lưu.
- **Nút mic nhỏ trên bong bóng** hoặc **Ctrl+Alt+V**: nói là tự lưu, hiểu giờ hẹn ("3 giờ chiều mai", "30 phút nữa"), "gấp", "dự án X", "việc … xong rồi", câu hỏi "hôm nay có việc gì".
- **Ghi âm** trên điện thoại: máy tính nghe rồi tự lưu.
- Model nghe nằm ở `models\`. Máy mới chưa có thì lần đầu dùng mic app tự tải bản `small` (khoảng 480 MB) từ Hugging Face.
- **Trợ lý** (tuỳ chọn): trò chuyện tự do bằng Ollama `qwen2.5:7b`.

## Bảo mật và quyền riêng tư
- Repo và gói tải về **không chứa dữ liệu cá nhân** của ai: `data\` bị loại khỏi git và khỏi gói cài đặt.
- Bản điện thoại cần khoá riêng, chặn đoán khoá, chặn trang web lạ gọi vào app, HTTPS trong mạng nhà.
- Chi tiết, danh sách các chỗ app kết nối ra ngoài, cách báo lỗ hổng: [SECURITY.md](SECURITY.md).

## Chạy từ mã nguồn
```
pip install -r requirements.txt
python ideanote_app.py
```
- `ideanote_app.py`: giao diện Qt. `server.py`: dữ liệu, nhắc hẹn, máy chủ cho điện thoại (trang `index.html`).
- `knowledge.py`: kho kiến thức (đọc chữ trong ảnh, tìm, hỏi AI). `assistant.py` và `voicerules.py`: giọng nói. `mailer.py`: nhắc qua Gmail và Google Lịch.

## Đóng gói bản mới
`python build_release.py` tạo `release\IdeaNote-v…-win64.zip` (chỉ chương trình, không có dữ liệu), rồi đưa lên mục Releases của repo.

## Giấy phép
MIT, xem [LICENSE](LICENSE). Dùng, sửa, chia sẻ tự do, giữ nguyên dòng ghi tên tác giả.


---

Made by [DUCPT](https://ducpt.com/?utm_source=github&utm_medium=readme&utm_campaign=IdeaNote) — AI agents, automation and digital products for one-person businesses. 
