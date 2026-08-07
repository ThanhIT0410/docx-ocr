# Requirements & Architecture — DocxOCR (User side)

> Tài liệu tham chiếu UI đi kèm: `design-reference.html` — bản mô phỏng tương tác, là nguồn tham chiếu chính về layout, màu sắc và hành vi. Khi triển khai, đối chiếu trực tiếp file này thay vì chỉ dựa vào mô tả bên dưới.
>
> Nội dung trong tài liệu này ở mức thiết kế ý tưởng, có thể điều chỉnh trong quá trình triển khai miễn là đảm bảo trải nghiệm phù hợp với nhóm người dùng mục tiêu.

---

## 1. Bối cảnh hệ thống

Hệ thống gồm hai actor: **User** (người nộp đề cần xử lý OCR) và **Processor** (thành phần xử lý OCR, do đội khác phụ trách). Tài liệu này mô tả toàn bộ phía **User**: ứng dụng desktop, xử lý cục bộ, và mô hình dữ liệu.

## 2. Tech stack & kiến trúc

- **Frontend**: Nuxt 4, đóng gói thành ứng dụng desktop bằng Electron.
- **Backend cục bộ**: FastAPI (Python), đóng gói cùng ứng dụng và chạy như một tiến trình nền (sidecar) trên máy người dùng. Chịu trách nhiệm cho hai tác vụ chuyên biệt:
  - **Tách trang PDF** bằng `PyMuPDF`.
  - **Xuất kết quả** (export) bằng pipeline xử lý nội bộ có sử dụng mô hình PyTorch — đây là logic độc quyền của hệ thống, cần môi trường Python để chạy, không thể thay thế bằng thư viện JavaScript thông thường. Tạm thời cốt lõi của export sẽ được cung cấp sau
- **Storage & Database**: Supabase (Postgres + Storage). Các thao tác CRUD cơ bản (submit, truy vấn theo trạng thái, xem chi tiết, chỉnh sửa khi Pending) được gọi **trực tiếp từ Nuxt bằng `@supabase/supabase-js`**, dùng `anon` key kết hợp Row Level Security (RLS).

Nguyên tắc phân chia trách nhiệm: mọi thao tác dữ liệu thông thường (đọc/ghi bản ghi, theo dõi trạng thái) đi thẳng qua Supabase để giảm tầng trung gian. Riêng hai tác vụ đòi hỏi xử lý tệp nặng và logic độc quyền — tách trang PDF và xuất kết quả — được xử lý bởi backend FastAPI cục bộ, vì đây là nơi duy nhất chạy được pipeline PyTorch và cũng tránh phải tải tệp PDF gốc lên server chỉ để cắt trang.


## 3. Người dùng mục tiêu & nguyên tắc thiết kế

Người dùng mục tiêu là **người lớn tuổi, ít kinh nghiệm sử dụng công nghệ**. Đây là ràng buộc xuyên suốt toàn bộ thiết kế, được ưu tiên cao hơn tính thẩm mỹ hay sự tối giản về giao diện.

- **Đơn giản hóa thao tác**: mỗi màn hình chỉ tập trung vào một tác vụ chính; không sử dụng thao tác ẩn (menu chuột phải, phím tắt bắt buộc). Các luồng nhiều bước hiển thị rõ bước hiện tại và số bước còn lại.
- **Tooltip đầy đủ**: mọi icon và nút thao tác đều có tooltip giải thích bằng ngôn ngữ thông thường, tránh thuật ngữ kỹ thuật.
- **Khả năng đọc và thao tác**: chữ đủ lớn, độ tương phản cao, vùng bấm đủ rộng (tối thiểu ~40px chiều cao cho nút chính).
- **Xác nhận trước hành động không thể hoàn tác**: xóa đề hoặc xóa trang đều yêu cầu xác nhận.
- **Nhãn chữ đi kèm icon** cho mọi hành động chính (Gửi đề, Tiếp tục, Lưu thay đổi, Xuất kết quả) — không dùng icon đơn độc.
- **Trạng thái hiển thị rõ ràng**: kết hợp màu sắc và nhãn chữ cho các trạng thái Pending / Processing / Finished, không chỉ dựa vào màu.

## 4. Cấu trúc điều hướng

**Primary sidebar** (thanh icon cố định bên trái) gồm hai mục, mỗi icon có tooltip:
1. Tải đề lên (Upload)
2. Tất cả tài liệu (All documents)

Chọn một mục sẽ kích hoạt icon tương ứng và mở **secondary sidebar** gắn liền, nội dung thay đổi theo mục đang chọn (chi tiết tại mục 5 và 6).

## 5. Chức năng: Tải đề lên

**Secondary sidebar** hiển thị danh sách "Đã gửi gần đây" — tên đề, số trang, thời gian gửi.

Luồng chính gồm hai bước, có stepper hiển thị tiến độ:

**Bước 1 — Tải tệp**
- Vùng kéo-thả nhận bộ ảnh (JPG/PNG) hoặc tệp PDF; mỗi tệp/bộ tệp tương ứng với một "đề".
- Tệp PDF được gửi tới FastAPI sidecar để tách thành từng trang riêng lẻ trước khi chuyển sang bước 2; trạng thái tách hiển thị bằng progress bar, nút Tiếp tục bị khóa cho đến khi hoàn tất.
- Mỗi đề có ô nhập tên riêng ("Tên đề"), giá trị mặc định gợi ý và cho phép chỉnh sửa trực tiếp; tên này được dùng để hiển thị đề trong "Đã gửi gần đây" và "Tất cả tài liệu", thay vì tên tệp gốc.
- Danh sách đề đang chờ hỗ trợ kéo-thả sắp xếp thứ tự và xóa từng đề (có xác nhận trước khi xóa).
- Nút "Tiếp tục — sắp xếp trang" chỉ kích hoạt khi toàn bộ tệp đã tách xong.

**Bước 2 — Sắp xếp trang & gửi**
- Hiển thị tên đề đang thao tác ở đầu trang.
- Lưới các trang/ảnh (gộp từ mọi tệp ở bước 1), mỗi thẻ gồm hình minh họa, số thứ tự và nguồn gốc.
- Hỗ trợ kéo-thả sắp xếp lại thứ tự trang và xóa từng trang cụ thể.
- Nút "Quay lại" (về bước 1) và nút "Gửi đề" (submit).
- Sau khi gửi: đề được thêm vào "Đã gửi gần đây", chuyển sang trạng thái Pending trong "Tất cả tài liệu", và form reset về bước 1.

## 6. Chức năng: Tất cả tài liệu

**Secondary sidebar** chia thành ba nhóm, mỗi nhóm có nhãn màu và số lượng: Pending (chờ xử lý), Processing (đang xử lý), Finished (hoàn thành). Chọn một tài liệu sẽ mở nó ở khu vực nội dung chính.

**Nội dung chính theo trạng thái:**
- **Pending**: xem lại toàn bộ trang/ảnh đã upload; cho phép xóa trang và sắp xếp lại thứ tự; có nút "Lưu thay đổi".
- **Processing**: chỉ xem (read-only), không cho sửa/xóa/sắp xếp; có banner giải thích trạng thái.
- **Finished**: khu vực nội dung chia đôi — bên trái là ảnh gốc từng trang, bên phải là kết quả OCR tương ứng, cuộn đồng bộ hai bên; có nút "Xuất kết quả".

## 7. Design tokens

| Token | Giá trị | Vai trò |
|---|---|---|
| `--bg` | `#EEF1F5` | Nền chung |
| `--surface` | `#FFFFFF` | Thẻ / panel |
| `--ink` | `#16213A` | Chữ chính |
| `--accent` | `#2B4FD8` | Hành động chính, trạng thái active |
| `--pending` | `#B97E10` | Trạng thái chờ xử lý |
| `--processing` | `#2B4FD8` | Trạng thái đang xử lý |
| `--finished` | `#12855F` | Trạng thái hoàn thành |
| Font chữ | Be Vietnam Pro | Toàn bộ giao diện |
| Font số liệu | IBM Plex Mono | Số trang, số liệu kỹ thuật |

Nếu cần icon, tham khảo Lucide

Chi tiết đầy đủ tham chiếu trực tiếp `design-reference.html`.

## 8. Data model (Supabase / Postgres)

Quan hệ một-nhiều giữa `exams` và `pages`, không cần bảng trung gian.

```sql
CREATE TABLE exams (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | processing | finished
    progress        SMALLINT DEFAULT 0,                 -- 0-100
    error_message   TEXT,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id         UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    page_order      SMALLINT NOT NULL,
    file_path       TEXT NOT NULL,          -- key trên Supabase Storage
    ocr_text        JSONB                    -- null cho đến khi xử lý xong
);

CREATE INDEX idx_pages_exam_id ON pages(exam_id);
CREATE INDEX idx_exams_status ON exams(status);
```

- `ocr_text` dùng JSONB để lưu kết quả OCR có cấu trúc (theo câu hỏi, block, tọa độ...) thay vì văn bản thuần.
- `progress` có thể tính động (tỷ lệ trang đã có `ocr_text`) hoặc do Processor cập nhật trực tiếp.
- **RLS bắt buộc bật trên cả hai bảng**, vì frontend truy vấn trực tiếp bằng `anon` key. Nếu hệ thống có nhiều người dùng, bổ sung cột `user_id` và policy giới hạn truy cập theo chủ sở hữu.

## 9. Data access

### 9.1 CRUD cơ bản — gọi thẳng Supabase từ Nuxt (`@supabase/supabase-js`)

- **Submit**: tải từng ảnh trang (đã tách xong ở bước 5) lên Supabase Storage, ghi record vào `exams` và `pages` với `status = 'pending'`.
- **Truy vấn theo trạng thái / toàn bộ**: lọc theo `status` trên bảng `exams`.
- **Truy vấn theo id**: lấy chi tiết một đề kèm danh sách trang liên quan.
- **Chỉnh sửa khi Pending**: cập nhật/xóa trực tiếp trên bảng `pages`, được giới hạn bằng RLS policy theo `exams.status`.
- **Truy vấn kết quả OCR**: đọc `pages.ocr_text` khi `status = 'finished'`.
- **Đồng bộ trạng thái theo thời gian thực**: đăng ký lắng nghe thay đổi trên bảng `exams` để cập nhật sidebar tự động, không cần polling.

### 9.2 FastAPI sidecar (local) — tách trang & xuất kết quả

Chạy tại `http://127.0.0.1:{port}`, do Electron main process khởi động cùng ứng dụng.

**Tách trang PDF:**
- `POST /preview` — nhận ảnh/PDF; nếu là PDF, tách trang bằng `PyMuPDF`, lưu các ảnh trang cùng metadata (tên đề, thứ tự) vào thư mục preview tạm trên đĩa; trả về danh sách trang cho Nuxt hiển thị.
- `PATCH /preview/{previewId}` — cập nhật preview: đổi tên, sắp xếp lại thứ tự, xóa trang cụ thể.
- `DELETE /preview/{previewId}` — xóa toàn bộ preview khỏi đĩa.
- `GET /preview` — liệt kê các preview còn dang dở (phục vụ trường hợp người dùng tắt ứng dụng giữa chừng).

**Xuất kết quả:**
- `POST /export` — nhận metadata của đề cùng kết quả OCR theo từng trang (`ocr_text`) và các signed URL ảnh gốc (do Nuxt lấy từ Supabase Storage rồi chuyển vào request). FastAPI chạy pipeline nội bộ (bao gồm mô hình PyTorch) để dựng file kết quả cuối cùng và trả về cho Nuxt để lưu xuống máy người dùng.

Thiết kế này giữ cho FastAPI sidecar không cần lưu trữ hay biết bất kỳ khóa truy cập Supabase nào — mọi dữ liệu cần thiết được Nuxt truy vấn trước (bằng `anon` key, tuân theo RLS) rồi mới chuyển sang FastAPI để xử lý.

## 10. Đóng gói & vận hành
 
- **Một điểm khởi động duy nhất**: người dùng chỉ cài đặt và mở một ứng dụng duy nhất (một icon, một installer); FastAPI sidecar phải được ứng dụng tự khởi động ngầm khi mở app và tự tắt khi đóng app — người dùng không bao giờ cần tự chạy hay biết đến sự tồn tại của tiến trình backend.
- **Đóng gói frontend**: `electron-builder` tạo installer cho từng hệ điều hành, gộp sẵn binary FastAPI đã build vào bên trong gói cài đặt (ví dụ thư mục `resources/` của Electron) để cả hai được phân phối cùng một lần cài.
- **Đóng gói backend**: `PyInstaller` build FastAPI (kèm theo các phụ thuộc PyMuPDF, PyTorch, và trọng số mô hình) thành một binary độc lập, không yêu cầu máy người dùng cài Python. Cần lưu ý dung lượng cài đặt sẽ tăng đáng kể do kèm theo runtime PyTorch và trọng số mô hình.
- **Vòng đời tiến trình**: Electron main process `spawn()` binary FastAPI ngay khi ứng dụng khởi động và `kill()` khi đóng ứng dụng — đây là cơ chế hiện thực cho yêu cầu "một điểm khởi động duy nhất" ở trên. Cần xử lý thêm: dò cổng còn trống trước khi `spawn()` (tránh xung đột nếu cổng mặc định đã bị chiếm), và đảm bảo tiến trình FastAPI được `kill()` sạch kể cả khi Electron bị đóng đột ngột (ví dụ qua sự kiện `before-quit`), tránh để lại tiến trình rác chạy nền.
- **Bind cục bộ**: FastAPI chỉ bind vào `127.0.0.1`, không mở ra mạng ngoài.
- **CORS**: chỉ cho phép origin của Electron renderer gọi vào sidecar.
- **Bảo mật Supabase**: `anon` key có thể tồn tại trong mã nguồn frontend theo đúng mô hình của Supabase, nhưng an toàn dữ liệu phụ thuộc hoàn toàn vào RLS policy. Không đưa `service_role` key vào bất kỳ thành phần nào của ứng dụng, kể cả FastAPI sidecar.
- **Thư mục dữ liệu cục bộ**: dùng `app.getPath('userData')` (Electron) để xác định vị trí lưu preview theo từng hệ điều hành, truyền đường dẫn này cho FastAPI khi khởi động.
- **Dọn dẹp**: xóa thư mục preview ngay sau khi submit thành công; định kỳ dọn các preview quá hạn (ví dụ trên 7 ngày) khi ứng dụng khởi động.
- **Thời gian khởi động**: cần đo và tối ưu thời gian nạp mô hình PyTorch khi FastAPI sidecar khởi động lần đầu, vì đây thường là điểm chậm nhất trong toàn bộ trải nghiệm mở ứng dụng — cân nhắc nạp mô hình bất đồng bộ, hiển thị trạng thái "Đang khởi động" thay vì để giao diện đứng im.
- **Ghi log**: log lỗi ra file cục bộ để hỗ trợ debug, do không có hệ thống log tập trung.
- **Xử lý mất kết nối**: khi thao tác với Supabase thất bại do mất mạng, hiển thị rõ trạng thái "Mất kết nối" thay vì để giao diện treo im lặng.