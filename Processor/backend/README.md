# DocxOCR — Processor backend

Xử lý OCR cho các đề đã được User submit. Xem `../backend_requirements.md`
cho yêu cầu gốc.

Một tiến trình duy nhất (không còn worker riêng — OCR chạy như background
task ngay trong tiến trình API, bật qua `POST /processor/ocr/start`):

```
python -m app.main      # admin API — mặc định :8800
```

## Setup (dev)

```
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate trên Unix
pip install -r requirements.txt

copy .env.example .env          # rồi điền các giá trị *** TODO — bổ sung ***
```

Áp dụng migration (Supabase SQL editor, cùng project với
`../../User/supabase/schema.sql`), theo đúng thứ tự:

```
migrations/0001_processor_worker_columns.sql
migrations/0002_drop_claim_columns.sql
migrations/0003_drop_progress_column.sql
```

Test:

```
pytest
```

## Endpoints

Base path `/processor` (trừ `/health`). Header bắt buộc:
`X-API-Key: <PROCESSOR_API_KEY>` trên mọi route; riêng reset cần thêm
`X-Admin-API-Key: <PROCESSOR_ADMIN_API_KEY>`.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/health` | Liveness của tiến trình API (không auth) |
| GET | `/processor/exams?status=` | Danh sách đề, lọc theo status (tùy chọn) |
| GET | `/processor/exams/{examId}` | Chi tiết đề kèm danh sách trang |
| POST | `/processor/queue/enqueue` | Đưa 1 hoặc nhiều đề `pending` vào hàng đợi xử lý (đổi `status → processing`) |
| POST | `/processor/queue/dequeue` | Rút 1 hoặc nhiều đề khỏi hàng đợi (đổi `status → pending`, xoá `ocr_text` các trang) |
| GET | `/processor/queue/progress` | Tiến độ OCR đang chạy (theo trang) + trạng thái pipeline (đang chạy/đã dừng/lỗi gì) |
| POST | `/processor/ocr/start` | Bật vòng lặp OCR nền — idempotent, tự dừng khi gặp lỗi |
| POST | `/processor/admin/reset` | Xóa toàn bộ exams/pages/Storage (dev/staging only) |
| GET | `/processor/dashboard` | Số liệu tổng quan: đếm theo status, health llama.cpp, dung lượng DB/Storage |

## Cấu trúc

`app/services/queue_service.py` (hàng đợi trong bộ nhớ), `result_handler.py`
(theo dõi tiến độ OCR theo trang), `ocr_pipeline.py` (vòng lặp xử lý chính),
`recovery.py` (đưa lại đề dở dang từ phiên trước vào hàng đợi lúc khởi động).
