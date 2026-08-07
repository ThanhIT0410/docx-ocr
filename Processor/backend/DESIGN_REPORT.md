# DocxOCR — Báo cáo thiết kế (Processor backend)

Tài liệu này mô tả những gì đã được triển khai trong `Processor/backend`,
đối chiếu trực tiếp với `Processor/backend_requirements.md` (§2 các chức
năng chính, §3 các phần cần bổ sung). Đây là triển khai đầy đủ — code chạy
được, import/compile sạch, có test cho phần logic thuần — không phải khung
sườn rỗng. Phần **§7 "Tham số & quyết định còn thiếu"** liệt kê mọi thứ cần
người vận hành bổ sung hoặc xác nhận trước khi chạy thật; nên đọc trước
tiên.

Không có gì trong `User/` bị chỉnh sửa. Toàn bộ code mới nằm trong
`Processor/backend/`.

---

## 1. Tổng quan

Processor backend gồm **hai tiến trình Python độc lập**, chạy cùng codebase
nhưng khởi động riêng, mục đích khác nhau — cùng kiểu tổ chức với
`User/backend` (một `app/` FastAPI, chạy được trực tiếp bằng
`python -m app.<module>`, xem §3):

| Tiến trình | Lệnh chạy | Vai trò |
|---|---|---|
| **Admin API** | `python -m app.main` (FastAPI/uvicorn) | Vận hành/giám sát: xem danh sách đề, enqueue, reset, worker status |
| **Worker** | `python -m app.worker` | Vòng lặp nền: nhận đề `processing`, tiền xử lý ảnh, gọi llama.cpp theo batch, ghi kết quả |

Cả hai đều nói chuyện với **Supabase** (Postgres qua PostgREST + Storage)
bằng `service_role` key — không qua RLS. Worker còn nói chuyện với
**llama.cpp server** (local, OpenAI-compatible) qua thư viện `openai`.

```
┌─────────────────────────┐        ┌──────────────────────────┐
│   Admin API (FastAPI)    │        │   Worker (vòng lặp nền)   │
│   app.main :8800           │        │   app.worker                │
│                            │        │                            │
│  GET  /processor/exams     │        │  loop:                     │
│  GET  /processor/exams/:id │        │   health-check llama.cpp   │
│  POST /exams/:id/enqueue    │        │   claim 1 exam 'processing'│
│  POST /exams/:id/retry      │        │   batch pages (size 4)     │
│  POST /exams/:id/requeue    │        │   preprocess + OCR mỗi batch│
│  POST /admin/reset            │        │   lỗi kết nối llama.cpp?    │
│                                │        │   → reset processing→pending│
│  GET  /dashboard                │        │   lưu ocr_text ngay từng trang│
└──────────┬───────────────────┘        └──────────┬────────────────┘
           │  service_role key                        │  service_role key
           ▼                                           ▼
   ┌───────────────────────────────────────────────────────────┐
   │                     Supabase project                       │
   │   Postgres: exams, pages (+ claimed_by/claimed_at/failed)   │
   │   Storage:  bucket exam-pages (pending/... , processing/...)│
   └───────────────────────────────────────────────────────────┘
                                                        │
                                                        ▼  openai client, base_url local
                                              ┌────────────────────┐
                                              │  llama.cpp server    │
                                              │  (OpenAI-compatible)  │
                                              └────────────────────┘
```

Vì sao tách hai tiến trình thay vì chạy worker như một `BackgroundTask`
trong chính FastAPI app? Yêu cầu gốc (§2.3) tự nói rõ worker "không nhất
thiết là HTTP endpoint". Tách riêng nghĩa là: worker crash không kéo API
xuống theo (và ngược lại), có thể restart/scale từng cái độc lập bằng
process manager, và vòng lặp OCR (có thể chạy hàng phút mỗi batch) không
tranh giành event loop với các request admin ngắn.

---

## 2. Đối chiếu với yêu cầu gốc

| Mục trong `backend_requirements.md` | Trạng thái | Nơi triển khai |
|---|---|---|
| §2.1 Xem danh sách/chi tiết đề | ✅ | `controllers/exams.py` |
| §2.2 Enqueue (move Storage + update DB, compensation, idempotent) | ✅ | `services/enqueue_service.py` |
| §2.3 Worker OCR (preprocess, xử lý đồng thời, lưu & progress) | ✅ | `services/worker_service.py` + `preprocessing.py`, `ocr_client.py` |
| §2.4 Admin reset (destructive, xác thực riêng, giới hạn môi trường) | ✅ | `services/reset_service.py`, `controllers/admin.py` |
| §3.1 Khóa tiến trình cho worker | ✅ | `repositories.exams.claim_next_exam` (claimed_by/claimed_at, conditional UPDATE) |
| §3.2 Xử lý lỗi & retry | ✅ | `services/ocr_client.py` (backoff); lỗi riêng 1 đề → `repositories.exams.mark_failed` + endpoint `/retry`, `/requeue`; lỗi kết nối llama.cpp (hết retry) → reset **toàn bộ** đề `processing` về `pending` (xem §4.9/§4.11) |
| §3.3 Phục hồi khi worker crash giữa chừng | ✅ | lưu `ocr_text` ngay khi từng trang xong; worker resume bằng truy vấn "pages còn null" |
| §3.4 Health check llama.cpp | ✅ | `services/health_check.py`, gọi trước mỗi lần claim |
| §3.5 Giới hạn xử lý đồng thời | ✅ (mặc định 4) | `services/worker_service.py` — `asyncio.Semaphore(max_concurrent_pages)`, mỗi trang 1 coroutine, không dùng thread (xem §4.11) |
| §3.6 (không còn áp dụng — xem §4.8) | — | Bản đầu gộp N ảnh/1 request nên cần chia đều thành nhóm 4; giờ mỗi trang luôn 1 request riêng, không còn khái niệm "nhóm cuối thiếu ảnh" |
| §3.7 Bảo mật & phạm vi truy cập | ✅ | `app/security.py` (2 API key riêng biệt) — network isolation vẫn cần cấu hình hạ tầng, xem §7 |
| §3.8 Giám sát & ghi log | ✅ | `config/logging.py` (JSON, file xoay vòng) + `controllers/dashboard.py` (đã gộp worker status vào Dashboard, xem §4.16) |
| §3.9 Cấu hình tiền xử lý & tham số model | ✅ | `app/config/settings.py` (env `PROCESSOR_PREPROCESS_*`/`PROCESSOR_OCR_*`) + `app/prompts.py` (prompt, xem §4.9/§4.14) |

---

## 3. Cấu trúc thư mục

`app/` tổ chức theo *concern*, không phẳng: `config/`, `schemas/`,
`repositories/`, `storage/` mỗi cái là 1 package riêng dù hiện chỉ có 2
file/package — quyết định cố ý giữ nguyên ranh giới này ngay cả khi ít
file, để mỗi concern có chỗ phát triển thành nhiều file nhỏ sau này mà
không phải đổi cấu trúc lần nữa. `main.py`/`worker.py`/`dependencies.py`/
`constants.py`/`security.py`/`supabase_client.py` vẫn để phẳng vì mỗi cái
là 1 file độc lập, không thuộc riêng concern nào ở trên — `logging_config.py`
thì ngược lại, dọn vào `config/logging.py` vì nó cũng là một cách "cấu
hình" tiến trình (mức log, nơi ghi file), cùng nhóm với `settings.py`.
`app/prompts.py` (prompt gửi model — xem §4.9) cũng để phẳng: không thuộc
`config/` (không phải deployment/tham số môi trường) và chỉ 1 file nên
chưa cần package riêng. Exception nội bộ được định nghĩa ngay trong module raise ra nó
(giống `PreviewNotFoundError` nằm trong `services/preview_service.py` phía
User), không có `errors.py` dùng chung.

```
Processor/backend/
├── app/
│   ├── main.py                    # FastAPI app; `python -m app.main` chạy trực tiếp (uvicorn.run trong __main__)
│   ├── worker.py                  # entrypoint vòng lặp worker; `python -m app.worker`
│   ├── prompts.py                 # OCR_PROMPT (layout-HTML) + ALLOWED_TAGS/ATTRIBUTES — xem §4.9
│   ├── dependencies.py            # FastAPI Depends dùng chung (db, storage)
│   ├── constants.py               # status values, storage prefix, header names
│   ├── security.py                # 2 dependency xác thực API key
│   ├── supabase_client.py         # Supabase client singleton (service_role)
│   ├── config/
│   │   ├── settings.py            # Settings (pydantic-settings, env PROCESSOR_*) — kể cả tham số preprocessing/OCR, không còn YAML riêng (§4.14)
│   │   └── logging.py             # JSON logging, stdout + file xoay vòng
│   ├── schemas/
│   │   ├── models.py              # Pydantic mirror của bảng DB: Exam, Page, DocumentLayout, OcrPageResult
│   │   └── dto.py                 # Pydantic request/response HTTP (ExamListItem, ResetResponse, ...)
│   ├── repositories/
│   │   ├── exams.py               # CRUD + claim + retry cho bảng exams
│   │   └── pages.py               # CRUD cho bảng pages
│   ├── storage/
│   │   ├── paths.py               # quy ước key pending/processing
│   │   └── client.py              # wrapper move/exists/download/remove (StorageHelper)
│   ├── controllers/
│   │   ├── exams.py               # §2.1 — GET /processor/exams, /{id}
│   │   ├── enqueue.py             # §2.2 — POST .../enqueue + .../retry + .../requeue
│   │   ├── admin.py               # §2.4 — POST /processor/admin/reset
│   │   └── dashboard.py           # §3.8 — GET /processor/dashboard (xem §4.16)
│   └── services/
│       ├── enqueue_service.py     # §2.2 — logic move+update+compensation+cap (+ ExamNotFoundError, InvalidExamStateError, StorageMoveError, ProcessingCapacityExceededError)
│       ├── reset_service.py       # §2.4 (+ DestructiveOpsDisabledError)
│       ├── preprocessing.py       # §2.3 bước 2 — resize/contrast/deskew, trả PreprocessResult (bytes + origin/input dims)
│       ├── ocr_client.py          # §2.3 bước 3 / §3.2 — 1 ảnh/1 request async tới llama.cpp + retry (+ OcrBatchError, OcrConnectivityError)
│       ├── postprocessing.py     # parse response HTML layout-block -> DocumentLayout[] (CATEGORY_MAP, clean_html)
│       ├── health_check.py        # §3.4
│       ├── supabase_management.py # §4.16 — DB/Storage size qua Management API (personal access token, KHÔNG phải service_role)
│       ├── dashboard_service.py   # §4.16 — lắp GET /processor/dashboard
│       └── worker_service.py      # §2.3 toàn bộ — vòng lặp chính, asyncio.Semaphore cho §3.5 (xem §4.11)
├── migrations/
│   └── 0001_processor_worker_columns.sql   # ALTER exams: claimed_by/claimed_at/failed
├── tests/
│   └── test_paths.py
├── requirements.txt                # một file duy nhất, có section "Dev / test" (pytest) — như User/backend
├── .env.example
└── README.md
```

`GET /health` không có controller riêng — định nghĩa inline ngay trong
`app/main.py`, giống hệt cách User's `app/main.py` định nghĩa `/health`
trực tiếp thay vì tạo một router chỉ để phục vụ 1 endpoint trivial.

---

## 4. Chi tiết từng module

### 4.1 `app/config/settings.py` — `Settings`

`pydantic-settings.BaseSettings`, prefix `PROCESSOR_`, đọc từ env (và
`.env` khi dev qua `python-dotenv`/`pydantic-settings`'s `env_file`). Mọi
giá trị được validate ngay lúc khởi động (fail fast) thay vì lỗi giữa
chừng một request/iteration. `settings = Settings()` được tạo **1 lần duy
nhất ở module scope**; `get_settings()` chỉ là hàm bọc mỏng trả về đúng
instance đó — giữ lại vì `Depends(get_settings)` được dùng ở nhiều
controller/`security.py`/`dependencies.py` (FastAPI DI cần 1 callable),
không phải vì cần cache (không dùng `lru_cache` nữa — `Settings()` vốn dĩ
chỉ chạy 1 lần dù có cache hay không).

Field đáng chú ý:
- `resolved_worker_id`: nếu `PROCESSOR_WORKER_ID` để trống, tự sinh
  `hostname-pid` — đủ để phân biệt worker trong log/worker-status mà không
  cần người vận hành đặt tay khi chạy 1 instance.
- `is_destructive_ops_allowed`: `True` chỉ khi `env` là `dev`/`staging` —
  gate duy nhất cho `/admin/reset` (§2.4, §3.7).

### 4.2 `app/constants.py`, `app/security.py`

- **`constants.py`**: 4 giá trị `status` (`pending/processing/finished/
  failed`), prefix Storage `processing/`, tên 2 header xác thực. Tập trung
  ở một chỗ để repo/service/controller không tự ý gõ lại chuỗi.
- **`security.py`**: hai FastAPI dependency, `require_api_key` (header
  `X-API-Key`, áp cho mọi route `/processor/*`) và `require_admin_api_key`
  (header **riêng** `X-Admin-API-Key`, chỉ áp thêm cho `/admin/reset`). Cố
  tình dùng 2 header khác nhau — nếu dùng chung 1 header thì không thể so
  sánh cùng lúc với 2 secret khác nhau. So sánh bằng `secrets.compare_digest`
  (constant-time, chống timing attack).

Không có `errors.py` dùng chung: mỗi exception nội bộ (`ExamNotFoundError`,
`InvalidExamStateError`, `StorageMoveError`, `OcrBatchError`,
`DestructiveOpsDisabledError`) được định nghĩa ngay trong module
`services/*` raise ra nó, rồi controller tương ứng import trực tiếp để bắt
— giống hệt cách `PreviewNotFoundError` nằm trong
`User/backend/app/services/preview_service.py` và được
`app/controllers/preview.py` import thẳng, thay vì tách qua một module lỗi
dùng chung.

### 4.3 `app/supabase_client.py`, `app/repositories/exams.py`, `app/repositories/pages.py` + migration

- **`supabase_client.py`**: `get_supabase()` — singleton `supabase.Client`
  bằng `service_role` key. An toàn để dùng lại giữa nhiều request/iteration
  vì không giữ connection Postgres bền — mọi thứ đi qua HTTPS tới
  PostgREST.
- **`repositories/exams.py`**:
  - `claim_next_exam`: chọn ứng viên (status `processing`, `claimed_by`
    null HOẶC `claimed_at` cũ hơn `stale_claim_timeout_seconds`), rồi
    UPDATE có điều kiện đúng bằng giá trị `claimed_by` đã đọc được. Nếu
    một worker khác thắng race giữa lúc SELECT và UPDATE, điều kiện WHERE
    không còn khớp nữa → 0 dòng bị update → hàm trả `None`, caller
    (`services/worker_service.py`) coi như "không có gì để lấy" và poll
    lại. Đây là cơ chế khóa tiến trình thay cho
    `SELECT ... FOR UPDATE SKIP LOCKED` (xem §6.2 vì sao không dùng khóa
    hàng đợi kiểu đó).
  - `mark_finished` / `mark_failed` / `retry_failed_exam`: các transition
    trạng thái được định nghĩa tường minh, mỗi hàm chỉ làm đúng một việc.
  - `list_active_claims`, `count_by_status_since`, `count_by_status`: phục
    vụ `/dashboard` (§3.8/§4.16) mà không cần bảng heartbeat riêng.
- **`repositories/pages.py`**:
  - `list_unprocessed_pages`: `WHERE ocr_text IS NULL ORDER BY page_order`
    — dùng cho cả lần chạy đầu lẫn lần resume sau crash (§3.3), vì hai
    trường hợp chỉ khác nhau ở số dòng trả về.
  - `save_ocr_text`: nhận 1 `OcrPageResult`, serialize thành JSON khớp
    contract phía User (xem §6.3).
- **`migrations/0001_processor_worker_columns.sql`**: `ALTER TABLE exams`
  thêm `claimed_by text`, `claimed_at timestamptz`, và mở rộng
  `exams_status_check` để chấp nhận `'failed'`. Không đụng file
  `User/supabase/schema.sql` — chạy như một migration bổ sung, cùng
  project Supabase. **Cần được duyệt bởi người phụ trách User-side trước
  khi áp dụng lên project dùng chung** — xem §7.1.

### 4.4 `app/storage/paths.py`, `app/storage/client.py`

- **`storage/paths.py`**: quy ước key — pending giữ nguyên đúng như
  frontend User đang upload (`{examId}/{pageId}.{ext}`, theo comment trong
  `User/supabase/storage.sql`), Processor chỉ thêm tiền tố `processing/`
  khi enqueue. Vì RLS policy phía User cho bucket chỉ kiểm tra
  `bucket_id = 'exam-pages'` (không giới hạn theo path), quy ước này không
  cần đổi gì ở phía User.
- **`storage/client.py`** (`StorageHelper`): bọc `storage3` (client Storage
  của supabase-py) — `move`, `exists`, `download`, `remove` (remove theo lô
  100 object/lần theo giới hạn của Supabase Storage API, trả về
  `(deleted_count, failed_paths)` thay vì raise, vì `/admin/reset` cần số
  liệu chính xác kể cả khi xóa dở).

### 4.5 `app/services/enqueue_service.py` — trọng tâm của §2.2

Đây là phần yêu cầu gốc nhấn mạnh nhiều nhất ("Vấn đề cần xử lý cẩn
thận"), nên thiết kế được viết kỹ trong docstring của chính file. Tóm tắt:

1. **Thứ tự**: move Storage trước, update DB sau (đúng khuyến nghị trong
   yêu cầu gốc).
2. **Đơn vị nguyên tử = từng trang, không phải cả đề.** Sau khi move object
   của một trang xong, `file_path` của đúng trang đó được update ngay lập
   tức — không gom lại update một lần cho cả đề ở cuối. Vì vậy nếu crash
   giữa chừng (ví dụ xong trang 3/5), trang 1-3 đã hoàn tất trọn vẹn
   (object đã move + DB đã phản ánh), trang 4-5 hoàn toàn chưa đụng tới.
   Gọi lại enqueue chỉ cần xử lý tiếp từ trang 4 — việc "biết trang nào đã
   xong" suy ra trực tiếp từ tiền tố `processing/` trong `file_path`, không
   cần bảng trạng thái phụ.
3. **Tự phát hiện & tự sửa "đã move nhưng DB chưa cập nhật"**: nếu bước
   move báo lỗi "not found" ở nguồn, kiểm tra xem đích đã tồn tại chưa —
   nếu có nghĩa là lần chạy trước đã move thành công nhưng update DB thất
   bại ngay sau đó (ví dụ mất mạng). Trường hợp này chỉ cần chạy lại update
   DB, không move lại (đúng yêu cầu "tránh vòng lặp đảo qua đảo lại").
4. **Idempotent theo đúng nghĩa yêu cầu**: entry point kiểm tra
   `status` trước — đã `processing` thì coi bước move là no-op (mọi trang
   đã có tiền tố `processing/` sẽ tự bị bỏ qua trong vòng lặp), không lỗi;
   đã `finished`/`failed` thì từ chối bằng `409` (`InvalidExamStateError`)
   vì đó không phải trạng thái hợp lệ để enqueue lại.
5. **Giới hạn `processing` đồng thời** (bổ sung ngoài spec gốc): chỉ
   chuyển `pending` → `processing` (mục 4, nhánh thật sự "thêm mới") mới bị
   chặn — đếm số đề đang `processing` (`repositories.exams.count_by_status`),
   nếu ≥ `PROCESSOR_MAX_CONCURRENT_PROCESSING` (mặc định 20) thì từ chối
   bằng `409` (`ProcessingCapacityExceededError`), người gọi tự thử lại
   sau. Đây là kiểm tra "đọc rồi ghi" (đếm xong mới update), có race nhỏ
   nếu nhiều request `/enqueue` chạy đồng thời — chấp nhận được vì đây là
   giới hạn mềm bảo vệ tài nguyên (không phải bất biến đúng-sai như cơ chế
   `claim_next_exam`), và `/enqueue` không phải đường gọi tần suất cao.

`ExamNotFoundError`, `InvalidExamStateError`, `StorageMoveError`,
`ProcessingCapacityExceededError` được định nghĩa ngay trong file này
(không phải một `errors.py` dùng chung) — `app/controllers/enqueue.py`
import và bắt riêng `ExamNotFoundError` (→404), `InvalidExamStateError`
và `ProcessingCapacityExceededError` (→409 cả hai, nhưng ý nghĩa khác nhau:
một cái là sai trạng thái, một cái là hết chỗ); `StorageMoveError` không bị
controller bắt riêng, để nó nổi lên thành `500` (một lỗi Storage thật sự
bất thường, không phải input sai của người gọi).

### 4.6 `app/services/reset_service.py` — §2.4

Thứ tự ngược với enqueue: vì đây là xóa sạch (không có khái niệm "resume
đúng phần còn thiếu"), việc quét lấy toàn bộ danh sách (`file_path` của
pages, `id` của exams) được làm **trước** khi xóa bất cứ thứ gì, để không
bị rơi vào tình trạng "xóa DB rồi mới biết cần xóa Storage nào". Xóa
Storage trước, DB sau — pages cascade tự động theo FK có sẵn trong
`User/supabase/schema.sql` (`on delete cascade`), Storage thì không tự
cascade nên phải xóa tay. Trả về đúng 4 con số theo yêu cầu ("đối soát"):
`exams_deleted`, `pages_deleted`, `storage_objects_deleted`,
`storage_objects_failed`.

Gate `destructive_ops_allowed` là tham số truyền vào (không tự đọc
`Settings` bên trong service) để service này test được độc lập với env
thật. `DestructiveOpsDisabledError` định nghĩa ngay trong file này,
`app/controllers/admin.py` import trực tiếp để map sang `403`.

### 4.7 `app/services/preprocessing.py` — §2.3 bước 2

Dùng OpenCV (không phải Pillow thuần) vì deskew và tăng tương phản CLAHE
không có hàm tương đương gọn trong Pillow:
- `smart_resize` (1 hàm duy nhất, nhận thẳng ảnh vào/ra — không tách riêng
  bước "tính kích thước" và "resize" nữa): **port trực tiếp từ tiền xử lý ảnh gốc của
  Qwen2-VL** (`qwen_vl_utils`) — không phải resize đơn giản theo 1 cạnh dài
  nhất nữa. Thuật toán đảm bảo đồng thời 3 điều kiện: (1) cả 2 chiều chia
  hết cho `factor=28` (= patch size 14 × spatial merge size 2 của
  Qwen2-VL — hằng số kiến trúc model, không phải tham số chỉnh), (2) tổng
  số pixel nằm trong `[min_pixels, max_pixels]` (env `PROCESSOR_MIN_PIXELS`/
  `PROCESSOR_MAX_PIXELS`, mặc định 3136/11289600 — đúng mặc định của
  Qwen2-VL), (3) giữ tỉ lệ khung hình gần đúng nhất có thể trong giới hạn
  đó. Chọn `INTER_AREA` khi thu nhỏ, `INTER_CUBIC` khi phóng to (ảnh nhỏ
  hơn `min_pixels` sẽ bị phóng to, không chỉ thu nhỏ như trước). Raise
  `ValueError` nếu tỉ lệ khung hình > 200:1 (bảo vệ khỏi input dị dạng,
  ngưỡng gốc từ Qwen2-VL).
- `_deskew`: ngưỡng Otsu → `cv2.minAreaRect` trên vùng non-zero → xoay bù
  góc lệch, bỏ qua nếu góc vượt `deskew_max_angle_deg` (khả năng cao là
  contour nhận diện sai, không phải ảnh thật sự lệch nhiều) hoặc quá nhỏ
  (< 0.1°, không đáng xoay).
- `_enhance_contrast`: CLAHE trên kênh L của không gian màu LAB (không áp
  trực tiếp lên RGB để tránh lệch màu).

**Vì sao `min_pixels`/`max_pixels` là biến môi trường riêng** (không gộp
chung với các tham số tiền xử lý khác dù giờ cả hai đều ở `Settings`): đây
là **hợp đồng đầu vào của model** (ngân sách vision token), gắn với đúng
model nào đang chạy sau llama.cpp — cùng nhóm quyết định với
`PROCESSOR_LLAMACPP_MODEL`, không phải tham số chỉnh chất lượng ảnh mà
operator chỉnh bằng mắt như `deskew`/`enhance_contrast`.

`preprocess_image(image_bytes, settings)` nhận thẳng `Settings` (không còn
`PreprocessingConfig` — xem §4.14 đã xoá) và trả về `PreprocessResult`
(`content` + `origin_width/height` + `input_width/height`) thay vì chỉ
`bytes` — `input_*` (kích thước sau `smart_resize`) cần thiết để
`ocr_client.py` denormalize `data-bbox` đúng tỉ lệ (§4.9), `origin_*` (kích
thước gốc trước resize) được lưu lại trong `ocr_text` cho pipeline
reformat/export dùng sau này (§6.3).

### 4.8 (đã xoá) `app/services/batching.py`

Bản đầu có 1 hàm `chunk()` thuần chia trang thành các nhóm cố định 4 (test
ở `tests/test_batching.py`), vì khi đó "gộp N ảnh vào 1 request" và "giới
hạn xử lý đồng thời" là **cùng một cơ chế**. Sau khi đổi sang 1 ảnh/1
request async + `asyncio.Semaphore` (§4.9/§4.11), 2 việc đó tách rời hẳn:
không còn "nhóm" nào cần chia trước — mỗi trang là 1 coroutine độc lập,
semaphore giới hạn bao nhiêu coroutine chạy cùng lúc mà không cần biết
trước "trang nào thuộc nhóm nào". `chunk()` không còn chỗ dùng nên xoá
cùng file test của nó, thay vì giữ lại một hàm không ai gọi.

### 4.9 `app/services/ocr_client.py` + `app/services/postprocessing.py` — §2.3 bước 3 / §3.2

**Thay đổi lớn nhất so với bản đầu**: prompt thật (`app/prompts.py`'s
`OCR_PROMPT`, do bạn cung cấp — port từ Dots-OCR/Chandra-OCR) yêu cầu model
trả **HTML theo layout block** (`<div data-bbox="x0 y0 x1 y1" data-label="...">`),
**mỗi lần gọi đúng 1 ảnh** ("OCR this image" — số ít), không phải JSON
array nhiều trang như bản nháp ban đầu trong `config/ocr_config.yaml` (đã
xoá). Vì vậy:
- `OcrClient.process_image(image_bytes, origin_width, origin_height,
  input_width, input_height) -> OcrPageResult` thay cho `run_batch` cũ —
  đúng 1 ảnh, 1 request `chat.completions.create` (1 khối text = `OCR_PROMPT`
  cố định + 1 khối `image_url` base64 JPEG), trả thẳng `OcrPageResult` sẵn
  sàng lưu vào `ocr_text` (§6.3).
- `temperature`/`max_tokens` giờ là field của `OcrClient` (từ
  `PROCESSOR_OCR_TEMPERATURE`/`PROCESSOR_OCR_MAX_TOKENS`), không còn
  `ModelConfig` truyền theo từng lần gọi — vì chỉ có đúng 1 prompt cố định,
  không cần truyền lại mỗi batch nữa.
- `app/services/postprocessing.py` (mới) — port từ code xử lý response
  của hệ thống cũ bạn cung cấp:
  - `CATEGORY_MAP`: chuẩn hoá `data-label` model trả về (phòng khi model
    không theo đúng chỉ dẫn) — rút gọn so với bản gốc, chỉ giữ các mục có
    giá trị đích nằm trong đúng 11 nhãn `OCR_PROMPT` yêu cầu; nhãn không
    nhận diện được → fallback `"Text"` (bản gốc dùng `"unk"`, không hợp lệ
    với schema `DocumentLayout` ở đây).
  - `clean_html`: markdown-lite hoá `<b>/<i>/<u>` thành `**/*/__`, phát
    hiện `<h1>`-`<h6>` để **ghi đè** `category`/`level` (tín hiệu cấu trúc
    HTML mạnh hơn `data-label` — đúng chủ đích 2 tín hiệu chồng nhau trong
    prompt). Riêng `Table`/`List-item` **giữ nguyên HTML thô**, không làm
    sạch thành text — pipeline render bảng/danh sách (Test/
    reformat_service_v2.py) cần đúng cấu trúc thẻ.
  - **Fix**: `<img alt="...">` — nơi `OCR_PROMPT` yêu cầu model đặt mô tả
    ảnh/biểu đồ/sơ đồ — bị mất trắng cho mọi block `Picture` trước fix
    này, vì `BeautifulSoup.get_text()` chỉ lấy text node, không lấy giá
    trị attribute. Verify bằng test thật: `parse_layout_response` trên 1
    `<div data-label="Picture"><img alt="..."></div>` trả về
    `text=""`. Fix: trước khi `get_text()`, thay từng `<img>` bằng chính
    giá trị `alt` của nó (`img.replace_with(img.get("alt", ""))`) — mô tả
    giờ chảy vào `get_text()` như text bình thường; test lại xác nhận
    `text` giữ đúng nội dung mô tả, kể cả khi `<img>` nằm xen giữa văn bản
    khác trong cùng block.
  - `parse_layout_response(html, input_width, input_height)`: denormalize
    `data-bbox` (chuẩn hoá 0-1000) về pixel thật — **theo `input_*`**
    (kích thước ảnh model thực sự thấy sau resize), không phải `origin_*`,
    vì đó mới là không gian toạ độ model output ra. Bỏ nhánh
    `prompt_mode == "ocr"` của bản gốc (chỉ dùng 1 prompt cố định, luôn có
    layout block) và tham số `PIL.Image` (hệ thống này decode bằng OpenCV,
    gọi bằng width/height int có sẵn).

**Chính sách retry** (quyết định cho §3.2) — 2 loại lỗi, xử lý khác nhau
ở tầng gọi (`worker_service.py`, xem §4.11):
- Lỗi mạng/timeout/HTTP status (`APIConnectionError`, `APITimeoutError`,
  `APIStatusError`) → retry với exponential backoff (`tenacity`), tối đa
  `PROCESSOR_OCR_MAX_ATTEMPTS` lần (mặc định 3), độ trễ khởi điểm
  `PROCESSOR_OCR_BACKOFF_BASE_SECONDS` (mặc định 2s, nhân đôi mỗi lần). Hết
  số lần retry vẫn lỗi → raise **`OcrConnectivityError`** (không phải lỗi
  của riêng đề này — llama.cpp bản thân nó có vẻ đang down).
- Response trả về nhưng **không parse được** thành layout block hợp lệ →
  **không retry** — ở `temperature=0` model gần như deterministic nên thử
  lại khó có khả năng ra kết quả khác, trong khi lại tốn thời gian GPU vốn
  đang bị worker khác/trang khác chờ (§3.5). Lỗi này raise **`OcrBatchError`**
  (lớp cha) — vấn đề riêng của trang/đề này, không phải hạ tầng.

`OcrConnectivityError` là **subclass** của `OcrBatchError` — cùng 1 cây
exception nhưng `worker_service.py` bắt subclass trước (xem §4.11) để tách
2 cách xử lý: lỗi hạ tầng → reset toàn bộ `processing` về `pending`; lỗi
riêng đề → chỉ đề đó thành `failed`. Cả 2 exception định nghĩa ngay trong
`ocr_client.py`; `worker_service.py` import trực tiếp để bắt (không qua
`errors.py` dùng chung).

### 4.10 `app/services/health_check.py` — §3.4

llama.cpp server có `/health` ở gốc server (ngoài prefix `/v1` của phần
OpenAI-compatible) — gọi thẳng bằng `httpx` thay vì qua client `openai`.
Worker gọi hàm này **trước mỗi lần cố claim đề mới**; nếu không khỏe thì
ngủ `PROCESSOR_HEALTHCHECK_INTERVAL_SECONDS` rồi thử lại, không claim đề
(tránh vừa claim vừa chắc chắn fail).

### 4.11 `app/services/worker_service.py` — §2.3 toàn bộ, §3.1/§3.3/§3.5

**Toàn bộ class giờ là async** — `run_forever`/`_process_exam`/
`_process_pages`/`_process_pages_async` đều là `async def`, và `app/
worker.py::main()` chỉ gọi **đúng 1 lần** `asyncio.run(worker.
run_forever())` cho suốt vòng đời tiến trình worker (không phải sync loop
gọi `asyncio.run()` riêng cho từng đề như bản trước). Đây là fix cho một
bug thật đã verify bằng cách tái hiện: `OcrClient` bọc `AsyncOpenAI`, mà
`httpx.AsyncClient` bên dưới nó pool các keep-alive connection gắn với
event loop đầu tiên dùng nó; nếu mỗi đề gọi `asyncio.run()` riêng (tạo +
đóng loop mới mỗi lần), request của đề thứ 2 sẽ tái sử dụng connection cũ
đang gắn với loop đã đóng của đề 1 → `RuntimeError: Event loop is closed`.
Test tái hiện: 2 lệnh `asyncio.run()` liên tiếp dùng chung 1
`httpx.AsyncClient` → lỗi ngay ở lần gọi thứ 2; gộp lại thành 1
`asyncio.run()` bọc cả 2 lần gọi (chung 1 loop) → chạy bình thường. Cùng
1 nguyên lý mọi service async sống lâu (vd Uvicorn) đã áp dụng: dựng loop
đúng 1 lần, dùng xuyên suốt.

`WorkerService.run_forever()`: vòng lặp vô hạn — health check → claim →
xử lý nếu claim được, ngủ-poll nếu không (`await asyncio.sleep(...)` thay
`time.sleep()` vì giờ chạy trong loop, không được block thật sự lâu, dù
các lệnh Supabase/httpx sync khác trong cùng vòng lặp vẫn chấp nhận
block — xem giải thích `to_thread` bên dưới). `_process_exam` bọc toàn bộ
quá trình xử lý 1 đề trong try/except, **3 nhánh theo thứ tự cụ thể trước
chung** (bắt subclass trước lớp cha — bắt buộc, xem §4.9):
1. `OcrConnectivityError` (llama.cpp tự nó có vẻ down, không phải lỗi
   riêng đề này) → `repositories.exams.reset_all_processing_to_pending`:
   đẩy **toàn bộ** đề đang `processing` (kể cả đề hiện tại) về `pending`,
   không đánh dấu `failed` — mỗi đề sẽ cần gọi lại `POST /enqueue` (chịu
   giới hạn `PROCESSOR_MAX_CONCURRENT_PROCESSING`, xem §4.5) một khi
   llama.cpp khoẻ lại. Log số lượng đề bị reset để theo dõi mức độ ảnh
   hưởng của một lần llama.cpp gặp sự cố.
2. `OcrBatchError` (lớp cha, các lỗi còn lại — vd response sai shape) →
   `mark_failed` với thông điệp lỗi, chỉ đúng đề này.
3. Exception bất kỳ khác (an toàn cuối cùng, để một bug không bao giờ làm
   kẹt cả hàng đợi) → cũng `mark_failed`, kèm log đầy đủ traceback
   (`logger.exception`).

`_process_pages`: truy vấn lại "trang chưa có `ocr_text`" mỗi lần vào hàm
(không cache danh sách 1 lần từ đầu) — đây chính là cơ chế resume của
§3.3: một worker mới nhận claim (do stale) sẽ tự động chỉ thấy phần còn
thiếu, không cần biết gì về lịch sử xử lý trước đó. Nếu không còn trang
nào thì return sớm; nếu còn, `await self._process_pages_async(...)` trực
tiếp — không còn ranh giới sync/async nào trong file nữa, tất cả cùng
chạy trên 1 event loop persistent.

`_process_pages_async` (đã đổi theo §4.9/yêu cầu bỏ batching — mỗi trang
1 coroutine, không dùng thread): tạo **toàn bộ** coroutine `process_one`
cho mọi trang còn thiếu ngay từ đầu bằng `asyncio.gather`, mỗi coroutine
tự làm trọn 1 trang — `preprocess_image` → `ocr_client.process_image` →
`pages.save_ocr_text` → cập nhật `progress` — rồi lưu ngay khi xong, không
đợi các trang khác. Cải thiện thật so với bản batch cũ: trang nào xong là
lưu ngay, không phụ thuộc trang khác trong "nhóm" nào cả (khái niệm nhóm/
batch không còn tồn tại).

`process_one`'s try/except bắt **cả 3 loại**: `OcrConnectivityError`,
`OcrBatchError`, và một nhánh `except Exception` cuối cùng cho bất cứ lỗi
nào khác (`preprocess_image` decode/aspect-ratio lỗi, storage
download/DB lỗi, ...) — bắt buộc phải bắt hết ngay trong coroutine, không
được để lọt ra `asyncio.gather()`. Đã verify: `asyncio.gather()` **không**
tự huỷ các coroutine anh em khi một cái raise (chỉ propagate exception
đó ra chỗ await gather, các coroutine khác vẫn "chạy nền" bình thường) —
trên event loop persistent (xem trên), "chạy nền" đó có nghĩa là chúng
tiếp tục chạy xuyên sang cả đề tiếp theo mà worker đã claim, kể cả sau khi
đề hiện tại đã bị đánh dấu `failed`, và có thể ghi `ocr_text` vào một đề
đã failed. Bắt hết lỗi ngay trong `process_one` đảm bảo `gather()` luôn
hoàn tất bình thường, không còn coroutine mồ côi nào sống sót qua ranh
giới giữa 2 đề.

Giới hạn song song (§3.5): `asyncio.Semaphore(max_concurrent_pages)`,
mặc định `4` — mỗi coroutine `await` semaphore trước khi thực sự gọi
`ocr_client`, nên tại một thời điểm tối đa `max_concurrent_pages` request
đang bay tới llama.cpp cùng lúc; các coroutine còn lại chờ ở `async with
semaphore` mà không tốn gì (không phải thread, không phải tiến trình —
chỉ là một coroutine đang suspended). Cố ý **không dùng
`asyncio.to_thread`** cho các lệnh sync còn lại trong `process_one`
(`storage.download`, `preprocess_image`, `pages.save_ocr_text`,
`exams.set_progress`) — theo đúng yêu cầu "không dùng thread mà dùng
async": phần chờ tốn thời gian nhất luôn là round-trip HTTP tới
llama.cpp (đã async qua `AsyncOpenAI`), còn CPU/IO cho storage/DB/resize
tương đối ngắn nên chấp nhận chạy trực tiếp trên event loop, giữ toàn bộ
file single-threaded thật sự.

Xử lý lỗi giữa các coroutine chạy song song dùng chung 1 `stop_event`
(`asyncio.Event`) thay vì raise ngay giữa `asyncio.gather` (raise giữa
chừng sẽ hủy các coroutine khác đang chạy dở, mất công đã làm): trang nào
gặp `OcrConnectivityError` sẽ set `stop_event` rồi return — các coroutine
*chưa bắt đầu gọi model* (đang chờ ở đầu `process_one`) thấy cờ này sẽ bỏ
qua luôn, không tốn thêm 1 lần gọi/retry vô ích khi đã biết llama.cpp
down; các coroutine *đang gọi dở* vẫn được để chạy xong tự nhiên. Sau khi
`gather` xong hết, `_process_pages_async` mới raise lại lỗi kết nối (nếu
có, ưu tiên hơn) hoặc lỗi batch đầu tiên gặp phải cho `_process_exam` xử
lý tiếp theo đúng 3 nhánh ở trên — giữ nguyên ngữ nghĩa lỗi cũ, chỉ đổi
cơ chế song song bên dưới.

### 4.12 `app/controllers/` + `app/main.py`

Mỗi controller map 1:1 với một mục trong §2 của yêu cầu gốc (xem bảng §2
phía trên) — cùng vai trò với `User/backend/app/controllers/{preview,
export}.py`: chỉ dịch HTTP <-> service, không chứa logic nghiệp vụ (ngoại
lệ là `exams.py`, nơi logic đủ đơn giản — chỉ đọc — nên nằm thẳng trong
handler, không tách service riêng, giống mức độ đơn giản của
`GET /preview` phía User). Toàn bộ controller `/processor/*` yêu cầu
`X-API-Key` (`dependencies=[Depends(require_api_key)]` ở cấp `APIRouter`);
riêng `/processor/admin/reset` yêu cầu thêm `X-Admin-API-Key`.

`app/main.py` định nghĩa `GET /health` (không auth — liveness probe cho
chính tiến trình API, dùng cho process manager/orchestrator, không phải
health của llama.cpp) trực tiếp trong file, giống hệt cách
`User/backend/app/main.py` tự định nghĩa `/health` thay vì tách ra một
router riêng cho một endpoint trivial. `app/main.py` cũng có khối
`if __name__ == "__main__":` gọi `uvicorn.run(...)` — chạy trực tiếp được
bằng `python -m app.main`, không cần script `run_*.py` phụ (mirror đúng
`User/backend/app/main.py`).

**Cập nhật**: `app/main.py` giờ **có** bật `CORSMiddleware` — giả định ban
đầu ("không có trình duyệt nào gọi thẳng") không còn đúng từ khi
`Processor/frontend` (Electron) tồn tại: renderer của nó là Chromium thật,
gọi API này bằng `fetch()` giống hệt cách sidecar phía User được gọi.
`allow_origin_regex` chỉ khớp 2 dạng loopback: `http://localhost:3000`
(`nuxt dev`) và `http://127.0.0.1:<port ngẫu nhiên>` (app đã đóng gói —
`electron/staticServer.js` bên Processor/frontend chọn port ngẫu nhiên mỗi
lần mở, giống hệt cơ chế sidecar bên User) — không dùng `allow_origins=["*"]`,
nên không nới lỏng yêu cầu "chỉ chạy trong mạng nội bộ" (§3.7), vì cả 2
pattern đều chỉ chấp nhận từ chính máy đang chạy backend. `allow_headers`
chỉ mở đúng `Content-Type`/`X-API-Key`/`X-Admin-API-Key` — không dùng
wildcard vì 2 header key này chính là thứ cần bảo vệ.

### 4.13 `app/worker.py`

Entry point `main()`: dựng `Settings`, cấu hình logging, dựng
`OcrClient`/`StorageHelper` (nay truyền thêm `temperature`/`max_tokens` từ
`Settings` — xem §4.9), rồi chạy `WorkerService.run_forever()`. Bắt
`KeyboardInterrupt` để dừng sạch khi Ctrl+C lúc dev. Chạy bằng
`python -m app.worker` — đã có sẵn khối `if __name__ == "__main__": main()`.

### 4.14 (đã xoá) `app/config/ocr.py` + `config/ocr_config.yaml`

**Bỏ hẳn cơ chế config YAML ngoài** theo quyết định của bạn khi prompt
chuyển từ JSON-array-nhiều-trang (đặt trong YAML, dễ sửa không cần build)
sang `app/prompts.py` — một khi prompt là **code**, việc "sửa không cần
redeploy" của YAML không còn nhiều giá trị nữa (đổi prompt giờ vốn dĩ đã
cần review/test như mọi thay đổi code khác). Toàn bộ field trước đây nằm
trong `PreprocessingConfig`/`ModelConfig`/`BatchConfig` (YAML) đã dọn về
`Settings` (env, §4.1): `preprocess_deskew`, `preprocess_enhance_contrast`
và các tham số con của chúng, `ocr_temperature`, `ocr_max_tokens`.
`BatchConfig` (`batch.size`/`batch.max_concurrent`) hoá ra **chưa từng
được đọc ở đâu cả** (đã kiểm bằng grep trước khi xoá) — trùng lặp chết với
`Settings.max_concurrent_pages` hiện tại (bản thân field này sau đó cũng
đổi ý nghĩa — không còn là "số ảnh gộp 1 request" mà là "số coroutine
chạy đồng thời", xem §4.8/§4.11), nên không cần chuyển đi đâu cả.

Đánh đổi mất đi so với YAML: không còn hot-reload (dù bản YAML cũ cũng
chưa hỗ trợ hot-reload thật — chỉ là sửa file rồi restart, y hệt sửa env
giờ). Không có gì mất thật sự, chỉ gọn hơn 1 tầng cấu hình.

### 4.15 `tests/`

`test_paths.py`: unit test cho module logic thuần (không DB/network/
filesystem), chạy bằng `pytest`, đã pass (3/3). `test_batching.py` đã xoá
cùng `batching.py` (§4.8) khi bỏ cơ chế gộp trang thành nhóm cố định. Các
service còn lại (`enqueue_service`, `reset_service`, `worker_service`,
`ocr_client`) phụ thuộc Supabase/llama.cpp thật nên chưa có test tự động —
xem §7.4 (cần Supabase project test riêng hoặc mock `supabase.Client`/
`storage3` để test được mà không đụng dữ liệu thật).

### 4.16 `app/services/supabase_management.py` + `dashboard_service.py` + `controllers/dashboard.py` — Dashboard (§3.8, mới)

`GET /processor/dashboard` **thay hẳn** `GET /processor/worker/status` (đã
xoá, cùng `controllers/worker_status.py`) theo yêu cầu gộp trang Worker +
Admin phía frontend thành 1 trang Dashboard duy nhất — trả về mọi thứ
`worker/status` từng trả (`active`, `finished_today`, `failed_today`,
cùng shape `WorkerHeartbeat`) cộng 3 nhóm số liệu mới:

- **`llamacpp_healthy`**: gọi thẳng `services/health_check.py::check_llamacpp_health()`
  (§4.10, không đổi gì) — tín hiệu này **chưa từng có** ở frontend trước
  đây: `worker.vue` cũ chỉ tự gọi `GET /health` của chính Processor API
  (liveness của tiến trình, xem §4.12), không phải health của llama.cpp.
- **`counts`** (đếm đề theo 4 status, all-time — khác `finished_today`/
  `failed_today` vốn chỉ tính từ đầu ngày UTC): gọi
  `repositories/exams.py::count_by_status()` 4 lần (hàm này đã có sẵn từ
  trước, chỉ chưa ai dùng ngoài `count_by_status_since` — không cần thêm
  gì ở tầng repository).
- **`db_size_bytes`/`storage_size_bytes`**: đây là phần thật sự mới, và
  **không đi qua PostgREST/service_role** như mọi thứ khác trong Processor
  — lý do: PostgREST chỉ expose table/view/function đã định nghĩa sẵn,
  không có endpoint nào nhận một chuỗi SQL tuỳ ý để chạy, mà
  `pg_database_size(current_database())` và tổng
  `storage.objects.metadata->>'size'` theo `bucket_id` thì không có sẵn
  dưới dạng table/view/function nào để expose theo cách thông thường.
  Bạn đã cân nhắc và chọn dùng **Supabase Management API**
  (`POST https://api.supabase.com/v1/projects/{ref}/database/query`) thay
  vì viết migration tạo 2 function RPC — API này chạy thẳng chuỗi SQL và
  trả JSON, đổi lại cần một credential khác hẳn: **personal access token**
  (`PROCESSOR_SUPABASE_ACCESS_TOKEN`, tuỳ chọn) — token này scope theo
  **tài khoản** Supabase (có thể chạm tới mọi project bạn có), rộng hơn
  hẳn `service_role` key vốn chỉ giới hạn trong đúng 1 project. Đây là
  đánh đổi bạn đã xác nhận chấp nhận, không phải quyết định tự ý.
  `settings.supabase_project_ref` (property mới trong `config/settings.py`)
  tự tách ref ra từ `supabase_url` (`https://<ref>.supabase.co`) thay vì
  thêm 1 biến env thứ hai.

  `supabase_management.py` cố tình **không raise** ở bất kỳ nhánh lỗi nào
  (thiếu token, lỗi mạng, status khác 200) — chỉ log warning và trả `None`
  — vì đây là 2 trong nhiều field của response, một Dashboard cấu hình
  thiếu token không nên làm hỏng toàn bộ endpoint (`llamacpp_healthy`/
  `counts`/`active` vẫn phải trả đúng bình thường). Frontend hiển thị
  `null` thành "chưa cấu hình" thay vì lỗi (xem `Processor/DESIGN_REPORT.md`).

  2 câu SQL dùng (chạy qua Management API, không phải PostgREST):
  ```sql
  select pg_database_size(current_database()) as bytes;

  select bucket_id, count(*) as file_count, sum((metadata->>'size')::bigint) as bytes
  from storage.objects group by bucket_id;
  ```
  Cố tình lấy `bytes` thô, không dùng `pg_size_pretty(...)` — frontend cần
  số byte thật để so ngưỡng 500MB/1GB và tô màu (§ thiết kế mốc an toàn/
  báo động nằm ở `Processor/frontend`, xem `Processor/DESIGN_REPORT.md`),
  format hiển thị đẹp là việc của `formatBytes()` phía frontend, không nên
  parse ngược 1 chuỗi đã format sẵn kiểu "512 MB". Câu thứ 2 trả **mọi**
  bucket (không tham số hoá theo 1 bucket) — Python lọc đúng dòng khớp
  `settings.supabase_storage_bucket` sau khi nhận về, gọn hơn truyền tham
  số qua Management API.

  `dashboard_service.py::get_dashboard()` là nơi lắp ráp toàn bộ — giữ
  nguyên logic `worker_status.py` cũ (health/active/finished_today/
  failed_today) + gọi thêm `count_by_status` ×4 + 2 hàm trong
  `supabase_management.py`. `controllers/dashboard.py` chỉ dịch HTTP,
  không chứa logic gì (đúng quy tắc controllers/services đã áp dụng xuyên
  suốt file này).

---

## 5. API reference

Base path: tất cả endpoint (trừ `/health`) nằm dưới `/processor`. Header
bắt buộc: `X-API-Key: <PROCESSOR_API_KEY>` (mọi route); riêng reset cần
thêm `X-Admin-API-Key: <PROCESSOR_ADMIN_API_KEY>`.

| Method | Path | Mô tả | Response model |
|---|---|---|---|
| GET | `/health` | Liveness của tiến trình API (không auth) | `{"status": "ok"}` |
| GET | `/processor/exams?status=` | Danh sách đề, lọc theo status (tùy chọn) | `ExamListItem[]` |
| GET | `/processor/exams/{examId}` | Chi tiết đề kèm danh sách trang | `ExamDetail` |
| POST | `/processor/exams/{examId}/enqueue` | Chuyển `pending` → `processing`, move Storage; từ chối nếu đã đủ `PROCESSOR_MAX_CONCURRENT_PROCESSING` đề `processing` (§ mới) | `EnqueueResponse` |
| POST | `/processor/exams/{examId}/retry` | Chuyển `failed` → `processing` **ngay, bỏ qua giới hạn processing** — admin ép chạy lại tức thì (bổ sung ngoài spec gốc, xem §4.5/§4.9) | `RetryResponse` |
| POST | `/processor/exams/{examId}/requeue` | Chuyển `failed` → `pending` — đường phục hồi thông thường, gọi lại `/enqueue` sau đó sẽ chịu đúng giới hạn processing (§ mới) | `RetryResponse` |
| POST | `/processor/admin/reset` | Xóa toàn bộ exams/pages/Storage (dev/staging only) | `ResetResponse` |
| GET | `/processor/dashboard` | health llama.cpp + đề đang được worker giữ + số liệu hôm nay + đếm theo status + dung lượng DB/Storage (§4.16) | `DashboardResponse` |

Mã lỗi: `401` (thiếu/sai API key), `403` (`/admin/reset` gọi ngoài
dev/staging), `404` (không tìm thấy đề), `409` (enqueue một đề
`finished`/`failed`; retry/requeue một đề không ở trạng thái `failed`;
hoặc enqueue khi đã đủ `PROCESSOR_MAX_CONCURRENT_PROCESSING` đề đang
`processing`).

---

## 6. Data model — thay đổi & quyết định

### 6.1 Migration `0001_processor_worker_columns.sql`

```sql
alter table exams add column if not exists claimed_by text;
alter table exams add column if not exists claimed_at timestamptz;
create index if not exists idx_exams_status_claimed_by on exams(status, claimed_by);

alter table exams drop constraint if exists exams_status_check;
alter table exams add constraint exams_status_check
  check (status in ('pending', 'processing', 'finished', 'failed'));
```

Chạy trên **cùng project Supabase** đã có `User/supabase/schema.sql`, sau
khi schema đó tồn tại. Không sửa file của User.

### 6.2 Vì sao dùng `claimed_by`/`claimed_at` thay vì `SELECT ... FOR UPDATE SKIP LOCKED`

Yêu cầu gốc gợi ý cả hai cách. `FOR UPDATE SKIP LOCKED` cần một session/
transaction Postgres giữ khóa xuyên suốt từ lúc SELECT đến lúc COMMIT —
nhưng worker nói chuyện với Postgres hoàn toàn qua PostgREST (REST API,
không có khái niệm transaction dài hơn 1 request). Cột
`claimed_by`/`claimed_at` + UPDATE có điều kiện đạt được tính loại trừ
tương đương (Postgres tuần tự hóa các UPDATE cạnh tranh trên cùng 1 dòng)
mà không cần giữ kết nối mở — phù hợp với kiến trúc "chỉ có service_role
key qua HTTPS" hiện tại.

### 6.3 Contract `ocr_text` — **đã chốt**, không còn là placeholder

Bản đầu của báo cáo này dùng shape tạm `OcrBlock[]` (`{question?, text,
confidence?}`) vì chưa có prompt/model thật — tự suy đoán để có gì đó chạy
được. Giờ đã có prompt thật (`app/prompts.py`) + code post-process của hệ
thống cũ (`clean_html`/`post_process_html_response`, bạn cung cấp trực
tiếp) nên **shape này không còn là chỗ đoán nữa** — nó khớp chính xác với
`DocumentLayout`/`OCRResult` trong `Test/ocr.py` (schema pipeline
reformat/export cũ), tức là chọn đúng con đường đã có sẵn để tái dùng
pipeline đó sau này thay vì tạo ra một format thứ ba.

`pages.ocr_text` giờ là **1 object `OcrPageResult`** (`app/schemas/models.py`),
không phải mảng trần:

```python
class DocumentLayout(BaseModel):
    bbox: list[float] = []          # [x1,y1,x2,y2], trong không gian "input" (xem dưới)
    category: str = "Text"          # 1 trong 11 nhãn app/prompts.py yêu cầu
    text: str                       # markdown-lite, hoặc HTML thô nếu category Table/List-item
    level: int | None = None        # 1-5, chỉ có khi category == "Section-header"

class OcrPageResult(BaseModel):
    origin_width: int; origin_height: int     # kích thước ảnh gốc (trước resize)
    input_width: int;  input_height: int      # kích thước ảnh model thực sự thấy (sau smart_resize, §4.7)
    layouts: list[DocumentLayout] = []
```

Vì sao có cả `origin_*` lẫn `input_*`: `data-bbox` model trả về được
chuẩn hoá 0-1000 **theo ảnh nó thực sự thấy** (`input_*`, sau
`smart_resize`) — denormalize sai kích thước là lệch bbox toàn bộ. Nhưng
pipeline reformat/export (`Test/reformat_service_v2.py`) cần kích thước
**trang gốc thật** (`origin_*`) để làm việc như chọn khổ giấy chuẩn gần
nhất (`PageFormatNormalizer`) — nên lưu cả hai, không suy ra được cái này
từ cái kia một cách chính xác (tỉ lệ khung hình gần giống nhưng không hệt
100% sau khi làm tròn theo `factor=28`).

Phía User (`User/frontend/app/types/models.ts`) đã cập nhật type
`DocumentLayout`/`OcrPageResult` khớp 1:1 (field `origin_width` v.v. giữ
snake_case, giống các field khác vốn đã mirror thẳng tên cột/JSON, không
đổi sang camelCase), `FinishedPanel.vue` đọc `.layouts` thay vì mảng trần,
và `User/backend/app/services/export_service.py` (khi đó còn là stub, giờ
đã là pipeline thật) cũng đọc theo shape mới. Toàn bộ mục "cần xác nhận contract `ocr_text`" ở §7.1 bản trước
**coi như đã giải quyết** — không cần vòng xác nhận riêng nữa vì chính bạn
là người cung cấp cả prompt lẫn code post-process cho cả 2 phía.

### 6.4 Quy ước Storage key

- Pending (không đổi so với hiện trạng): `{examId}/{pageId}.{ext}`.
- Processing (mới, do Processor định nghĩa): `processing/{examId}/{pageId}.{ext}`.

`pages.file_path` luôn phản ánh vị trí thật — phía User đọc lại từ cột này
để tạo signed URL (không tự dựng path), nên quy ước trên không cần thay
đổi gì ở code phía User, miễn giả định đó đúng (chưa được xác minh trực
tiếp trong code frontend — xem §7.1).

---

## 7. Tham số & quyết định còn thiếu — cần bổ sung/xác nhận

### 7.1 Cần xác nhận với người phụ trách phía User (ưu tiên cao nhất)

1. **Trạng thái `failed` mới** (`exams.status`): `User/frontend/app/types/
   models.ts` hiện định nghĩa `ExamStatus = 'pending' | 'processing' |
   'finished'` — **không có `failed`**. Nếu migration 0001 được áp dụng
   nguyên trạng, một đề lỗi sẽ có `status` mà UI phía User chưa biết xử lý
   (dropdown lọc theo status, badge màu, v.v. có thể hiển thị sai hoặc bỏ
   sót đề đó). Cần một trong hai hướng, cả hai đều đòi hỏi thay đổi phía
   User (ngoài phạm vi code trong PR này):
   - (a) User bổ sung `'failed'` vào `ExamStatus` + UI hiển thị tương ứng
     (khuyến nghị — rõ ràng nhất cho người vận hành lẫn người nộp đề); hoặc
   - (b) đổi thiết kế Processor để không cần status mới (ví dụ giữ
     `processing` + một cờ riêng) — đánh đổi lại đúng nhược điểm đã phân
     tích trong migration §6.2 (dễ nhầm với đề đang xử lý bình thường).
2. ~~**Contract `ocr_text`**~~ — **đã chốt, xem §6.3** (không còn cần xác
   nhận riêng: prompt + code post-process cho cả 2 phía đều do bạn cung
   cấp trực tiếp trong cùng phiên làm việc này).
3. **Quy ước Storage key** (§6.4): giả định phía User luôn đọc
   `pages.file_path` từ DB để tạo signed URL, không tự ráp path theo quy
   ước riêng. Chưa được xác minh trực tiếp trong code frontend (nằm ngoài
   phạm vi công việc lần này) — nên xác nhận trước khi chạy enqueue lần
   đầu trên dữ liệu thật.

### 7.2 Biến môi trường bắt buộc (đánh dấu `*** TODO — bổ sung ***` trong `.env.example`)

| Biến | Ghi chú |
|---|---|
| `PROCESSOR_SUPABASE_URL` | URL project Supabase (chung với User) |
| `PROCESSOR_SUPABASE_SERVICE_ROLE_KEY` | **Bí mật** — không log, không commit |
| `PROCESSOR_LLAMACPP_BASE_URL` | Ví dụ `http://localhost:8080/v1` — phụ thuộc cách llama.cpp server được khởi chạy (chưa xác định trong yêu cầu gốc) |
| `PROCESSOR_LLAMACPP_MODEL` | Tên model gửi trong field `model` — llama.cpp thường chấp nhận chuỗi bất kỳ nhưng một số cấu hình có kiểm tra; cần xác nhận với người khởi chạy llama.cpp server |
| `PROCESSOR_API_KEY`, `PROCESSOR_ADMIN_API_KEY` | Tự sinh secret ngẫu nhiên (ví dụ `openssl rand -hex 32`), 2 giá trị khác nhau |

**Tuỳ chọn** (không bắt buộc để chạy — thiếu chỉ khiến 2 field trong
`GET /processor/dashboard` trả `null`, xem §4.16): `PROCESSOR_SUPABASE_ACCESS_TOKEN`
— personal access token của **tài khoản** Supabase (Dashboard → Account →
Access Tokens), khác hẳn `PROCESSOR_SUPABASE_SERVICE_ROLE_KEY` — giữ cẩn
thận ít nhất ngang mức đó, có thể còn hơn (scope rộng hơn 1 project).

### 7.3 Tham số nghiệp vụ chưa được kiểm chứng (có giá trị mặc định hợp lý nhưng cần tinh chỉnh sau khi có dữ liệu thật)

- `app/prompts.py::OCR_PROMPT` — **prompt gửi cho model chưa test trên ảnh
  scan thật hoặc trên model vision cụ thể sẽ chạy sau llama.cpp**, dù đã
  ported từ một prompt Dots-OCR/Chandra-OCR có sẵn (không phải viết mới từ
  đầu). Cần: mẫu ảnh thật + model đã chọn để đánh giá và chỉnh prompt,
  `PROCESSOR_OCR_TEMPERATURE`, `PROCESSOR_OCR_MAX_TOKENS`.
- Tham số tiền xử lý (ngưỡng deskew, CLAHE clip limit) — giá trị khởi điểm
  hợp lý cho ảnh scan văn bản nói chung, chưa tune theo chất lượng ảnh đầu
  vào thực tế của hệ thống này (yêu cầu gốc §2.3 bước 2 cũng ghi rõ "cần
  định nghĩa cụ thể tùy chất lượng ảnh đầu vào thực tế"). `min_pixels`/
  `max_pixels` (§4.7) thì khác — không cần tune theo chất lượng ảnh, chỉ
  cần đúng theo model thật sự chạy sau llama.cpp (mặc định đang giả định
  Qwen2-VL).
- `PROCESSOR_STALE_CLAIM_TIMEOUT_SECONDS` (mặc định 900s) — thời gian trước
  khi một claim bị coi là "worker đã chết, có thể lấy lại". Nên đặt dài
  hơn thời gian xử lý tối đa thực tế của một batch để tránh 2 worker cùng
  xử lý 1 đề; hiện chưa có số liệu benchmark thời gian OCR mỗi batch để
  chọn con số chính xác.
- `PROCESSOR_MAX_CONCURRENT_BATCHES` (mặc định `1`) — an toàn nhưng có thể
  chậm; cần benchmark khả năng chịu tải thực tế của llama.cpp server/GPU
  trước khi tăng.

### 7.4 Hạ tầng & vận hành (ngoài phạm vi code)

- Mạng nội bộ / firewall cho Processor API — code chỉ xác thực bằng API
  key (§3.7), **không** tự giới hạn nguồn kết nối; việc "không public ra
  internet" là yêu cầu hạ tầng, cần cấu hình ở tầng triển khai (VPC,
  security group, reverse proxy, v.v.), không phải thứ ứng dụng Python có
  thể tự đảm bảo.
- Process manager cho 2 tiến trình (`python -m app.main`,
  `python -m app.worker`) — restart khi crash, log rotation ở cấp OS, v.v.
  Chưa có Dockerfile/systemd unit/supervisor config trong PR này.
- Supabase project test riêng (hoặc mock tầng `supabase-py`/`storage3`) để
  viết integration test cho `enqueue_service`/`reset_service`/
  `worker_service` — hiện chỉ có unit test cho phần logic thuần (§4.15).
- `Realtime` cho bảng `exams`/`pages` (nếu phía User dùng để cập nhật UI
  không cần polling, theo `User/supabase/schema.sql`) — các UPDATE do
  Processor thực hiện qua PostgREST sẽ tự động kích hoạt Realtime nếu đã
  bật ở project, không cần code gì thêm ở đây; chỉ lưu ý nếu đang tắt.

---

## 8. Dependency & version

Tất cả version dưới đây là bản mới nhất có trên PyPI tại thời điểm kiểm
tra (2026-07-22), đã cài thử và import/test thành công trong
`.venv` cục bộ (`pip list` xác nhận đúng version pin).

| Package | Version | Lý do chọn |
|---|---|---|
| `fastapi` | 0.139.2 | Mới nhất; khớp version đang dùng ở `User/backend` để 2 backend không lệch quá xa |
| `uvicorn[standard]` | 0.51.0 | Mới nhất |
| `pydantic` | 2.13.4 | Khớp `User/backend/requirements.txt` |
| `pydantic-settings` | 2.14.2 | Quản lý `Settings` từ env — không dùng ở User backend nên đây là version mới nhất độc lập |
| `supabase` | 2.31.0 | Client chính thức — Postgres (PostgREST) + Storage trong 1 package |
| `openai` | 2.46.0 | Client gọi llama.cpp qua OpenAI-compatible API |
| `httpx` | 0.28.1 | Dùng trực tiếp cho health check `/health` của llama.cpp (ngoài phạm vi client `openai`) |
| `opencv-python-headless` | 4.13.0.92 | Bản headless (không kèm GUI libs) — phù hợp server, dùng cho resize/deskew/CLAHE |
| `numpy` | 2.5.1 | Phụ thuộc của OpenCV |
| `tenacity` | 9.1.4 | Retry/backoff cho `ocr_client.py` |
| `beautifulsoup4` | 4.15.0 | Parse response HTML layout-block trong `postprocessing.py` (dùng `html.parser` built-in của Python — không cần `lxml`) |
| `python-dotenv` | 1.2.2 | Chỉ dùng khi dev (`.env`); production nên set biến môi trường thật |
| `python-json-logger` | 4.1.0 | Log JSON có cấu trúc (dùng `pythonjsonlogger.json.JsonFormatter` — import path mới, tránh module `jsonlogger` cũ đã deprecated từ bản 4.x) |
| `pytest` (dev) | 9.1.1 | Test runner — nằm trong section "Dev / test" cuối `requirements.txt`, không tách file riêng, giống cách `User/backend/requirements.txt` gộp cả `pyinstaller` (build-time) vào một file duy nhất thay vì `requirements-dev.txt` |

Không có version nào bị ép xuống thấp hơn bản mới nhất hiện có — nếu sau
này bump version, ưu tiên kiểm tra lại `fastapi`/`pydantic` cùng lúc với
`User/backend` để hai backend không lệch quá xa (không bắt buộc phải giống
hệt vì đây là 2 dự án Python độc lập, nhưng dễ bảo trì hơn nếu gần nhau).

---

## 9. Cách chạy & kiểm thử

```bash
cd Processor/backend
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt

cp .env.example .env    # điền các giá trị *** TODO — bổ sung *** (§7.2)

pytest                  # 3 test, thuần logic (paths)

python -m app.main      # admin API — http://127.0.0.1:8800
python -m app.worker    # worker — chạy song song, process riêng
```

Đã xác minh trong phiên làm việc này: cài đặt đúng version pin trong
`requirements.txt`, `app.main`/`app.worker` import sạch không lỗi,
toàn bộ `.py` byte-compile thành công (`python -m compileall`), 3/3 unit
test pass, và các câu query PostgREST phức tạp nhất (`claim_next_exam`'s
`.or_()`, conditional `.update().is_()`, `.not_.is_()`) đã được kiểm tra
tạo đúng chuỗi filter (`or=(claimed_by.is.null,claimed_at.lt...)`,
`claimed_by=is.null`, `ocr_text=not.is.null`) bằng cách inspect trực tiếp
`request.params` của `postgrest-py` — chưa chạy được end-to-end vì chưa có
Supabase project / llama.cpp server thật để trỏ vào (xem §7.2, §7.4).

---

## 10. Giới hạn đã biết / việc tiếp theo

- Chưa có integration test chạm Supabase/llama.cpp thật (§7.4).
- Chưa có Dockerfile/process-manager config để deploy 2 tiến trình.
- Prompt & tham số tiền xử lý là điểm khởi đầu hợp lý, chưa tune bằng dữ
  liệu thật (§7.3).
- Không hot-reload `ocr_config.yaml` (§4.14) — cần restart worker.
- `/dashboard`'s `active` suy ra trạng thái từ chính bảng `exams`
  (`claimed_by`/`claimed_at`) thay vì bảng heartbeat riêng — sẽ không trả
  lời được câu "worker có đang sống không nếu nó không giữ đề nào" (ví dụ
  worker crash ngay sau khi release claim cuối cùng sẽ trông giống hệt
  "đang rảnh chờ việc"). Nếu cần phân biệt hai trường hợp này, bước tiếp
  theo hợp lý là thêm bảng `worker_heartbeats` nhỏ, worker tự ghi mỗi vòng
  lặp.
- `db_size_bytes`/`storage_size_bytes` (§4.16) đi qua Supabase Management
  API bằng personal access token — **chưa test được thật** trong sandbox
  này (không có project/token thật, xem §7.4 mở rộng); cũng chưa rõ rate
  limit thật của API này, nên `Processor/frontend` poll endpoint này chậm
  hơn hẳn các stat khác (15s, xem `Processor/DESIGN_REPORT.md`) — có thể
  cần điều chỉnh lại sau khi thấy hành vi thật.
- Giới hạn `PROCESSOR_MAX_CONCURRENT_PROCESSING` (§4.5) kiểm tra kiểu
  "đếm rồi mới ghi" (count-then-write), không atomic — nhiều request
  `POST /enqueue` chạy đúng lúc nhau về lý thuyết có thể vượt nhẹ giới
  hạn. Chấp nhận được vì đây là giới hạn mềm bảo vệ tài nguyên, không phải
  bất biến bắt buộc đúng như cơ chế claim (vốn đã atomic bằng conditional
  UPDATE, xem §6.2); nếu cần cứng hơn, có thể chuyển sang một constraint/
  trigger ở tầng Postgres.
- Endpoint `/retry` (failed→processing tức thì) cố ý **bỏ qua** giới hạn
  processing — là admin override, không phải đường phục hồi thông thường
  (đường thường là `/requeue` → `/enqueue`, có chịu giới hạn). Nếu dùng
  `/retry` dồn dập, giới hạn có thể bị vượt có chủ đích.
