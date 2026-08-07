# Requirements — Processor Backend (DocxOCR)

## 1. Bối cảnh

Processor là thành phần chịu trách nhiệm xử lý OCR cho các đề đã được User submit. Backend này vận hành độc lập với phía User, cùng thao tác trên chung một Supabase project (Postgres + Storage) nhưng đứng ở phía server (có thể dùng `service_role` key, không bị giới hạn bởi RLS như phía User).

**Giả định về tech stack**: Python (FastAPI cho các endpoint quản trị/queue, cộng với một worker chạy nền) — phù hợp vì cần dùng OpenAI-compatible client để gọi llama.cpp server. Nếu có ràng buộc khác, cần điều chỉnh lại phần tương ứng.

## 2. Chức năng chính

### 2.1 Xem danh sách đề theo trạng thái

```
GET /processor/exams?status=pending|processing|finished
GET /processor/exams/{examId}
```

Trả về danh sách/chi tiết đề (kèm danh sách trang) để người vận hành Processor theo dõi tiến độ xử lý. Dữ liệu lấy trực tiếp từ bảng `exams`/`pages` đã có (dùng chung schema với phía User).

### 2.2 Đưa đề từ Pending vào hàng đợi xử lý

```
POST /processor/exams/{examId}/enqueue
```

Thao tác này gồm 2 phần cần thực hiện nhất quán với nhau:
1. **Di chuyển object trên Supabase Storage** — từ vị trí lưu trữ của đề đang `pending` sang vị trí dành cho `processing`.
2. **Cập nhật `status = 'processing'`** trong bảng `exams`.

**Vấn đề cần xử lý cẩn thận**: di chuyển file trên Storage và cập nhật bản ghi trong Postgres là hai thao tác trên hai hệ thống khác nhau, **không có transaction chung**. Nếu một trong hai bước thất bại giữa chừng, dữ liệu sẽ lệch trạng thái (ví dụ: đã move file nhưng chưa kịp update DB, hoặc ngược lại). Cần định nghĩa rõ:
- Thứ tự thực hiện (khuyến nghị: move Storage trước, update DB sau — vì việc phát hiện "đã move nhưng DB chưa cập nhật" dễ dò và tự sửa hơn chiều ngược lại).
- Cơ chế bù trừ (compensation): nếu update DB thất bại sau khi move Storage thành công, cần retry việc update DB (không move ngược lại file), tránh vòng lặp đảo qua đảo lại.
- Endpoint nên idempotent — gọi lại nhiều lần trên cùng một đề đã ở trạng thái `processing` không gây lỗi hay move file lần nữa.

### 2.3 Worker xử lý OCR

Tiến trình nền (không nhất thiết là HTTP endpoint), thực hiện tuần tự cho từng đề đang `processing`:

1. **Lấy một đề** từ hàng đợi (theo `status = 'processing'`, chưa có worker nào đang xử lý — xem lưu ý về khóa tiến trình ở mục 3).
2. **Tiền xử lý (preprocessing)** từng ảnh trang — ví dụ chuẩn hóa kích thước, tăng tương phản, xoay thẳng ảnh (cần định nghĩa cụ thể các bước này tùy chất lượng ảnh đầu vào thực tế).
3. **Gửi theo batch**: nhóm 4 ảnh cùng một đề thành một batch, gửi đến llama.cpp server (chạy tại `localhost`, expose OpenAI-compatible API) thông qua `openai` Python client, trỏ `base_url` về địa chỉ local của llama.cpp.
4. **Gom kết quả**: kết quả OCR trả về theo từng batch được ghép lại theo đúng thứ tự trang (`page_order`) của đề.
5. **Lưu & cập nhật**: ghi `ocr_text` cho từng trang, cập nhật `progress` sau mỗi batch hoàn tất, và khi toàn bộ trang đã xử lý xong: cập nhật `status = 'finished'`, `finished_at = now()`.

### 2.4 Xóa dữ liệu (reset)

```
POST /processor/admin/reset
```

Xóa toàn bộ bản ghi trong `exams`/`pages` và các object tương ứng trên Supabase Storage. Đây là thao tác **phá hủy dữ liệu không thể hoàn tác**, cần:
- Xác thực quyền admin riêng, tách biệt khỏi các endpoint vận hành thông thường.
- Cân nhắc giới hạn chỉ chạy được ở môi trường dev/staging (chặn bằng biến môi trường), tránh gọi nhầm lên production.
- Trả về xác nhận rõ ràng số lượng bản ghi/object đã xóa để đối soát.

## 3. Những phần cần bổ sung — gợi ý

Danh sách yêu cầu gốc còn thiếu một số điểm quan trọng để vận hành ổn định, nên bổ sung trước khi triển khai:

### 3.1 Khóa tiến trình cho worker (concurrency control)
Nếu chạy nhiều worker song song (hoặc worker tự khởi động lại), cần cơ chế đảm bảo **một đề chỉ được một worker xử lý tại một thời điểm** — ví dụ dùng `SELECT ... FOR UPDATE SKIP LOCKED` khi lấy đề từ hàng đợi, hoặc thêm cột `claimed_by`/`claimed_at` để nhận diện đề đang bị worker nào giữ.

### 3.2 Xử lý lỗi & retry
- Khi gọi llama.cpp thất bại (timeout, lỗi model, mất kết nối local) cho một batch: cần quyết định retry bao nhiêu lần, có backoff hay không, và khi nào đánh dấu cả đề là lỗi.
- Bảng `exams` đã có sẵn cột `error_message` — cần worker ghi rõ nguyên nhân lỗi vào đây khi thất bại, để người vận hành biết cách xử lý tiếp (retry thủ công, hoặc báo cho User).
- Nên có trạng thái hoặc cách đánh dấu riêng cho đề bị lỗi (ví dụ giữ nguyên `status = 'processing'` nhưng có `error_message`, hoặc thêm giá trị `failed` cho `status` — cần quyết định và đồng bộ với phía User nếu ảnh hưởng đến hiển thị).

### 3.3 Khả năng phục hồi khi worker bị dừng giữa chừng
Nếu worker crash hoặc restart khi đang xử lý dở một đề (ví dụ đã xong 2/5 batch), cần **lưu kết quả từng batch ngay khi hoàn tất** (không chờ xử lý xong toàn bộ đề mới ghi), để worker khởi động lại có thể tiếp tục từ batch còn thiếu thay vì xử lý lại từ đầu.

### 3.4 Kiểm tra sức khỏe llama.cpp server trước khi xử lý
Trước khi lấy đề từ hàng đợi, nên kiểm tra llama.cpp server đang sẵn sàng (health check). Nếu server local chưa khởi động hoặc đang quá tải, worker nên tạm dừng lấy đề mới thay vì liên tục thất bại từng batch.

### 3.5 Giới hạn số batch xử lý đồng thời
Vì llama.cpp server chạy trên cùng máy (giới hạn tài nguyên GPU/CPU), cần xác định rõ **worker xử lý tuần tự (1 batch tại một thời điểm)** hay cho phép một mức độ song song nhất định — tránh gửi nhiều request cùng lúc làm quá tải server model.

### 3.6 Trường hợp số trang không chia hết cho 4
Batch cuối cùng của một đề có thể có ít hơn 4 ảnh — cần đảm bảo logic gộp batch xử lý đúng cho trường hợp này, không giả định mọi batch đều đủ 4 ảnh.

### 3.7 Bảo mật & phạm vi truy cập
- Backend Processor nên chỉ chạy trong mạng nội bộ/không public ra internet, vì nó dùng `service_role` key (toàn quyền, bỏ qua RLS).
- Các endpoint `enqueue` và `admin/reset` cần xác thực (ví dụ API key nội bộ hoặc cơ chế đăng nhập riêng cho người vận hành), không nên để mở như API công khai.

### 3.8 Giám sát & ghi log
- Log lại thời gian xử lý mỗi batch, mỗi đề — phục vụ việc theo dõi hiệu năng và phát hiện đề xử lý bất thường lâu.
- Cân nhắc thêm endpoint hoặc cơ chế theo dõi tình trạng worker (đang chạy, đang xử lý đề nào, đã xử lý bao nhiêu đề trong ngày).

### 3.9 Cấu hình tiền xử lý & tham số model
Các tham số như kích thước ảnh sau khi resize, prompt gửi kèm ảnh tới llama.cpp, temperature/sampling của model — nên đưa ra file cấu hình riêng thay vì hard-code, để dễ điều chỉnh khi chất lượng OCR chưa đạt yêu cầu mà không cần sửa code worker.