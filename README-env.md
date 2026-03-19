Giải thích các trường trong `.env.example`

File `.env.example` (nằm ở gốc workspace) chứa các biến môi trường dùng bởi `docker-compose.yml`.
Dưới đây là ý nghĩa từng biến và gợi ý cấu hình:
- USE_SQLITE
  - Mô tả: Nếu `true`, hệ thống sẽ sử dụng SQLite thay vì Postgres + Redis. Dùng cho thử nghiệm nhỏ hoặc môi trường không hỗ trợ Docker. Lưu ý: không cần cấu hình các biến liên quan đến Postgres/Redis nếu dùng SQLite.
  - PHẢI ĐẶT `true` DO DOCKER KHÔNG ĐƯỢC HỖ TRỢ
- POSTGRES_USER
  - Mô tả: Tên user mặc định sẽ được tạo trong PostgreSQL (BỎ QUA NẾU USE_SQLITE = true).
  - Ví dụ: `postgres` hoặc `appuser`.
- POSTGRES_PASSWORD
  - Mô tả: Mật khẩu cho `POSTGRES_USER`. Bắt buộc, không để rỗng trong môi trường production (BỎ QUA NẾU USE_SQLITE = true).
  - Gợi ý: dùng chuỗi mạnh, lưu trong secret manager nếu có.
- POSTGRES_DB
  - Mô tả: Tên database mặc định sẽ được tạo khi container Postgres khởi động (BỎ QUA NẾU USE_SQLITE = true).
  - Ví dụ: `stttdb`.
- POSTGRES_PORT
  - Mô tả: Cổng trên host sẽ map tới cổng 5432 trong container Postgres (BỎ QUA NẾU USE_SQLITE = true).
  - Mặc định: `5433`.
- DICEDB_PORT
  - Mô tả: Cổng trên host sẽ map tới cổng 6379 trong container DiceDB (Redis-compatible) (BỎ QUA NẾU USE_SQLITE = true).
  - Mặc định: `6380`.
- RUN_DB_INIT
  - Mô tả: Nếu đặt `true`, script khởi động sẽ chờ Postgres sẵn sàng rồi áp dụng `data_service/schema.sql` để tạo/đè schema `sttt`. Dùng cho local dev. Cảnh báo: sẽ xoá dữ liệu cũ nếu đã có schema (BỎ QUA NẾU USE_SQLITE = true).
  - Mặc định: `false`.
- DICEDB_HOST
  - Mô tả: Host/địa chỉ nơi DiceDB (Redis-compatible) chạy (BỎ QUA NẾU USE_SQLITE = true).
  - Mặc định `localhost`.
- JWT_SECRET
  - Mô tả: Khoá bí mật dùng để ký JWT cho API. Bắt buộc đổi trong production.
- JWT_ALGORITHM
  - Mô tả: Thuật toán ký JWT (ví dụ: `HS256`, `RS256`).
  - Mặc định `HS256`.
- JWT_EXP_MINUTES
  - Mô tả: Thời gian sống của access token tính bằng phút.
  - Mặc định `60`.
- LOGIN_LOCK_THRESHOLD
  - Mô tả: Số lần đăng nhập sai liên tiếp trước khi khoá tài khoản tạm thời.
  - Mặc định `5`.
- LOGIN_LOCK_WINDOW_SECONDS
  - Mô tả: Cửa sổ thời gian (giây) để tính số lần đăng nhập sai (ví dụ 900 = 15 phút).
  - Mặc định `900`.
- LOGIN_LOCK_DURATION_SECONDS
  - Mô tả: Thời lượng (giây) khoá tài khoản khi vượt ngưỡng (ví dụ 900 = 15 phút).
  - Mặc định `900`.
- WAIT_FOR_JOB_DONE
  - Mô tả: Cấu hình cho worker. Nếu `true`, worker sẽ chờ job hiện tại hoàn thành trước khi lấy job mới. Có thể đặt `false` để worker liên tục lấy job mới mà không cần chờ, nhưng cần đảm bảo hệ thống xử lý đủ nhanh để tránh quá tải.
- WHISPER_MODEL
  - Mô tả: Tên model HuggingFace cho thành phần phiên âm. Ví dụ: `Systran/faster-whisper-medium` hoặc `large-v3`. Model `large-v3` là một lựa chọn nhanh và nhẹ hơn so với các model lớn hơn, nhưng vẫn cung cấp chất lượng tốt cho nhiều ngôn ngữ.
- WHISPER_COMPUTE_TYPE
  - Mô tả: Loại tính toán cho model phiên âm. Ví dụ: `int8`, `float16`, `float32`. `int8` thường nhanh nhất và tiết kiệm VRAM nhất, nhưng có thể giảm chất lượng một chút.
  - Mặc định `int8` nếu model hỗ trợ, nếu không sẽ tự động hạ xuống `float16` hoặc `float32`.
- WHISPER_BEAM_SIZE
  - Mô tả: Số lượng beams cho thuật toán beam search khi giải mã phiên âm. Giá trị cao hơn có thể cải thiện chất lượng nhưng tăng thời gian xử lý.
  - Mặc định `6`.
- NLLB_MODEL
  - Mô tả: Tên model HuggingFace cho thành phần dịch. Ví dụ: `facebook/nllb-200-distilled-600M` (nhẹ hơn) hoặc `facebook/nllb-200-distilled-1.3B` (chất lượng cao hơn nhưng nặng hơn).
- NLLB_NUM_BEAMS
  - Mô tả: Số lượng beams cho thuật toán beam search khi giải mã dịch. Giá trị cao hơn có thể cải thiện chất lượng dịch nhưng tăng thời gian xử lý.
  - Mặc định `2`.
- SMALL_TASK_THRESHOLD_SECONDS
  - Mô tả: Ngưỡng thời gian (giây) để phân loại một tác vụ là "nhỏ". Tác vụ có thời gian ước tính dưới ngưỡng này sẽ được ưu tiên xử lý trên CPU, trong khi tác vụ lớn hơn sẽ được gửi đến GPU nếu có.
  - Mặc định `300` (5 phút).
- SMALL_TASK_MAX_WORKERS
  - Mô tả: Số lượng worker tối đa để xử lý các tác vụ nhỏ trên CPU. Cần điều chỉnh dựa trên số lõi CPU và tải hệ thống.
  - Mặc định `4`.
- EMAIL_USER
  - Mô tả: Địa chỉ email dùng để gửi thông báo, email xác nhận, hoặc email đặt lại mật khẩu. Cần cấu hình nếu bạn muốn sử dụng tính năng email.
- EMAIL_APP_PASS
  - Mô tả: Mật khẩu ứng dụng (app password) cho `EMAIL_USER`. Nếu bạn dùng Gmail, cần tạo app password trong cài đặt bảo mật của tài khoản Google và sử dụng mật khẩu đó ở đây.

Hướng dẫn nhanh

1. Sao chép `.env.example` -> `.env`:

```bash
cp .env.example .env
```

2. Chỉnh sửa `.env` theo môi trường của bạn (đổi mật khẩu, port nếu cần).

Bảo mật

- Không commit file `.env` chứa mật khẩu vào git.
- Với môi trường production, sử dụng secret manager (Vault, AWS Secrets Manager, Docker secrets...) để bảo vệ mật khẩu.

Nếu bạn muốn thêm biến khác (ví dụ: `PGDATA` để thay đổi thư mục data, hoặc cấu hình DiceDB như `DICEDB_MAXMEM`), cho tôi biết để tôi cập nhật mẫu `.env.example` và `docker-compose.yml` kèm hướng dẫn.
