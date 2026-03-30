# Tài liệu API - STTT API Service

Tài liệu này mô tả chi tiết các endpoint của dịch vụ API trong repository (`api_service/main.py`). Nội dung bằng tiếng Việt, bao gồm mô tả, các tham số, ví dụ request/response, và các mã lỗi thường gặp.

## Tổng quan
- Base: ứng dụng FastAPI (khởi tạo trong `api_service/main.py`).
- Xác thực: Bearer token (HTTP Authorization: `Bearer <access_token>`). Token lấy từ endpoint `/login`.
- Response chuẩn: ResponseBuilder (kiểu JSON) có cấu trúc:
  - `data`: payload (hoặc null nếu lỗi)
  - `meta`: { status_code, message, success, action, ... }
  - Ví dụ thành công:
    {
      "data": {...},
      "meta": {"status_code":200, "message":"OK", "success":true, "action":"toast"}
    }

## Các schema chính (tóm tắt từ `api_service/schemas.py`)
- LoginRequest: { email: string, password: string }
- SendVerifyCodeRequest: { email: string, type: "password_reset" | "email_verification" }
- SignUpRequest: { email: string, password: string }
- VerifyNewAccountRequest: { email: string, code: string(6) }
- ForgetPasswordRequest: { email: string, code: string(6), new_password: string }
- TokenResponse: { access_token: string, token_type: "bearer", expires_in: int }
- SubmitJobRequest: { file_path: string, output_type: "text"|"srt", input_language: string, output_language?: string, type: "transcribe"|"translate", metadata?: object }

## Xác thực & quyền
- Những endpoint có `Depends(get_current_user_middleware)` yêu cầu header `Authorization: Bearer <token>`.
- Nếu token không hợp lệ => 401 (kèm action `logout`).
- Nếu tài khoản chưa verified => 403 (action `verify`).

---

## Endpoint chi tiết

### 1) POST /login
- Mục đích: Đăng nhập, trả access token.
- Body (JSON):
  - `email` (string, email)
  - `password` (string)
- Response (200): data chứa `{ access_token, expires_in, user }` (user không có password_hash và một vài trường nhạy cảm).
- Lỗi: 401 nếu thông tin sai; 423 nếu tài khoản bị khóa tạm thời.

Ví dụ request (curl):

```bash
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"secret"}'
```

### 2) POST /upload
- Mục đích: Upload file âm thanh (multipart/form-data).
- Auth: yêu cầu Bearer token.
- Form: file field tên `file`.
- Chú ý giới hạn:
  - Chỉ chấp nhận `content-type` bắt đầu bằng `audio/`.
  - Kích thước tối đa 100 MB. Nếu vượt => HTTP 413.
  - File được lưu vào thư mục `upload/YYYYMMDD/` với tên an toàn (sanitize) theo định dạng `{userId}-{timestamp_ms}-{safe_original_name}[.ext]`.
- Response (201): data `{ "path": "upload/20260328/123-...-file.wav" }` (relative path trong repo).

Ví dụ curl (upload):

```bash
curl -X POST http://localhost:8000/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/audio.wav"
```

### 3) POST /submit-job
- Mục đích: Tạo job xử lý tệp (transcribe/translate).
- Auth: yêu cầu Bearer token.
- Body (JSON): `SubmitJobRequest` (xem schema ở trên). `file_path` là đường dẫn file trả về bởi `/upload` (hoặc đường dẫn tuyệt đối có thể truy cập trên filesystem của server).
- Hành vi:
  - Kiểm tra file tồn tại và là tệp âm thanh (dùng ffprobe để lấy duration).
  - Nếu dài hơn 60s, tách thành segment (ffmpeg) và tạo child jobs.
  - Chèn job và children vào DB, push vào queue (Redis hoặc sqlite queue tùy cấu hình).
- Response (201): data `{ job_id, total_children }`.
- Lỗi: 400 cho dữ liệu không hợp lệ, 500 khi xử lý file/DB lỗi.

Ví dụ request:

```bash
curl -X POST http://localhost:8000/submit-job \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"file_path":"upload/20260328/123-...-file.wav","output_type":"text","input_language":"vi","type":"transcribe"}'
```

### 4) POST /send-verify-code
- Mục đích: Gửi mã xác thực (email) cho các thao tác như đăng ký, quên mật khẩu.
- Body: `{ email, type }` với `type` là `password_reset` hoặc `email_verification`.
- Response: 200 với `{ sent_at }` (ISO timestamp) khi thành công.
- Lỗi: 400 nếu không thể gửi (hạn chế gửi hoặc cấu hình mail chưa đúng).

### 5) POST /sign-up
- Mục đích: Tạo tài khoản mới.
- Body: `{ email, password }`.
- Hành vi: kiểm tra email tồn tại; lưu password hash (bcrypt).
- Response: 201 với `{ email }` khi thành công.
- Lỗi: 409 nếu email đã tồn tại; 500 khi DB lỗi.

### 6) POST /verify-new-account
- Mục đích: Xác thực mã email (khi sign-up).
- Body: `{ email, code }`.
- Hành vi: nếu thành công cập nhật cột `verified` trong DB.
- Response: 200 với `{ email }` khi thành công.
- Lỗi: 400 nếu mã sai/hết hạn.

### 7) POST /forget-password
- Mục đích: Đặt lại mật khẩu sử dụng mã xác thực.
- Body: `{ email, code, new_password }`.
- Hành vi: nếu code hợp lệ, cập nhật `password_hash` và `last_reset_password_at`.
- Response: 200 với `{ email }` khi thành công.

### 8) WebSocket /submit_and_get_job
- Mục đích: Tạo job qua websocket và nhận cập nhật realtime.
- Kết nối: `ws://<host>/submit_and_get_job?token=<access_token>` (token là access token Bearer)
- Bước flow:
  1. Client mở websocket kèm token query param.
  2. Server kiểm tra token, accept connection.
  3. Client gửi JSON payload tương tự `SubmitJobRequest` (các trường: `output_type`, `type`, `input_language`, `output_language`, `file_path`, `metadata`).
  4. Server tạo job, trả `{ job_id, total_children }` rồi giữ kết nối mở.
  5. Khi job được cập nhật (monitor loop), server gửi object job (cột DB) + `transcribe_content` và `translate_content` (nội dung file nếu có) tới tất cả websocket đăng ký cho job đó.
  6. Khi job hoàn thành server sẽ đóng kết nối với code `1000` (NORMAL_CLOSURE) và reason tương ứng.

- Lưu ý: message push là toàn bộ record job (các trường chuyển về ISO string cho datetime), kèm 2 trường `transcribe_content`, `translate_content` là nội dung file (chuỗi) nếu có.

### 9) GET /get-job/{job_id}
- Mục đích: Lấy thông tin chi tiết của job (cùng với nội dung transcribe/translate nếu có).
- Auth: yêu cầu Bearer token (user chỉ có thể lấy job thuộc mình).
- Path param: job_id (int)
- Response (200): data là object job kèm `transcribe_content` và `translate_content`.
- Lỗi: 404 nếu không tìm thấy, 500 nếu DB lỗi.

### 10) GET /get-job (list)
- Mục đích: Liệt kê job của user, hỗ trợ phân trang.
- Query params:
  - `start` (int, >=0) default 0
  - `limit` (int, >=1) default 20
  - `count` (bool) nếu true trả kèm `meta.pagination.total`
- Response: danh sách job (mỗi item là record với trường stringified cho datetime).

---

## Cảnh báo & yêu cầu môi trường
- Để xử lý audio dài/tra cứu duration cần có `ffprobe` và `ffmpeg` cài trên server PATH.
- Redis (nếu dùng Postgres mode) hoặc sqlite queue (nếu `USE_SQLITE=true`) được dùng để enqueue child-jobs.
- Biến môi trường quan trọng:
  - `USE_SQLITE`: nếu `true` thì dùng sqlite (đa dụng cho dev/local).
  - `SMALL_JOB_THRESHOLD_SECONDS`: ngưỡng để phân queue nhỏ/lớn.
  - `JWT_EXP_MINUTES`: thời gian sống của token (dùng cho response expires_in).

## Ví dụ thực tế nhanh
1) Đăng nhập -> lấy token
2) Upload file
3) Gọi `/submit-job` với `file_path` trả về từ bước 2
4) Mở websocket (tùy chọn) để nhận cập nhật realtime
5) Hoặc dùng `GET /get-job/{job_id}` để poll trạng thái

---

## Ghi chú triển khai
- Mã nguồn liên quan: `api_service/main.py`, `api_service/schemas.py`, `api_service/enum.py`, `api_service/response_builder.py`.
- Nếu cần, tôi có thể mở rộng tài liệu với:
  - Mẫu response chi tiết (thực tế) cho từng endpoint
  - Chuỗi JSON Schema / OpenAPI (tự động từ FastAPI)
  - Hướng dẫn triển khai môi trường (ffmpeg, redis, env vars)


----
Tài liệu tạo tự động bởi trợ lý – nếu bạn muốn tôi thêm ví dụ response thực tế hoặc chuyển đổi thành OpenAPI/Swagger cụ thể, cho biết là tôi sẽ mở rộng.