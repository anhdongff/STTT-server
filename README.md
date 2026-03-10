 # STTT — Hệ thống Speech-to-Text và Dịch (tổng hợp)

 STTT (Simple Transcribe/Translate) là một project nhỏ cung cấp API để:
 - upload file âm thanh
 - tạo job phiên âm / dịch
 - có các worker chạy nền để phiên âm (Whisper/faster-whisper) và dịch (NLLB)

 Tệp README này tổng hợp yêu cầu phần mềm, phần cứng, các biến môi trường, cách cài đặt và hướng dẫn chạy dịch vụ trong môi trường development, cùng tóm tắt các endpoint chính.

 ## Các thành phần chính

 - `api_service/` — FastAPI server (xử lý xác thực, upload, tạo job, lấy job)
 - `data_service/` — helper cho Postgres và file `schema.sql`
 - `cache_service/` — wrapper Redis dùng cho queue và khóa đăng nhập
 - `worker/` — các worker nền:
   - `whisper_worker.py` — chuyển audio -> transcript
   - `nllb_worker.py` — dịch transcript -> output dịch
 - `upload/` — thư mục lưu file upload (mặc định)

 ## Yêu cầu phần mềm

 - Hệ điều hành: Linux hoặc macOS khuyến nghị cho GPU; Windows vẫn chạy được cho development.
 - Python: 3.10+ (3.8+ có thể chạy nhưng một số dependency yêu cầu phiên bản mới hơn). Dùng virtualenv.
 - Gói hệ thống:
   - `ffmpeg` (bắt buộc, dùng để probe/split audio)
   - Docker & Docker Compose (khuyến nghị để chạy Postgres + Redis khi phát triển)

 Các dependency Python theo từng thành phần (đã có các file `requirements.txt` tương ứng):
 - `api_service/requirements.txt`
 - `worker/requirements.txt` (faster-whisper, transformers, accelerate, ...)
 - `data_service/requirements.txt`

 Bạn có thể cài chung vào một virtualenv hoặc tách environment cho worker nếu muốn.

 ## Yêu cầu phần cứng (khuyến nghị)

 - CPU-only: phù hợp thử nghiệm nhỏ, nhưng tốc độ phiên âm và dịch sẽ chậm.
 - GPU: khuyến nghị nếu xử lý nhiều hoặc dùng model lớn.
   - GPU CUDA với >= 12GB VRAM tốt cho faster-whisper và NLLB lớn. Model `facebook/nllb-200-distilled-600M` nhẹ hơn nhưng vẫn được hưởng lợi từ GPU.
   - Nếu dùng GPU, cài PyTorch compatible với CUDA theo hướng dẫn chính thức.

 ## Biến môi trường (.env)

 Tạo file `.env` ở thư mục gốc repo với bằng cách copy `.env.example` và chỉnh sửa theo môi trường của bạn.

 Không commit secrets thực tế vào git.

 ## Cài đặt (development)

 1. Clone repository và vào thư mục project.
 2. Tạo virtualenv và kích hoạt:

 ```bash
 python -m venv .venv
 # Linux / macOS
 source .venv/bin/activate
 # Windows PowerShell
 .\.venv\Scripts\Activate.ps1
 ```

 3. Cài dependencies:

 ```bash
 pip install -r api_service/requirements.txt
 pip install -r data_service/requirements.txt
 # Nếu chạy worker cục bộ:
 pip install -r worker/requirements.txt
 ```

 Ghi chú:
 - Nếu dùng GPU NVIDIA, cài PyTorch phù hợp với CUDA trước khi cài `transformers`/`accelerate` ví dụ: `pip install torch --index-url https://download.pytorch.org/whl/cu126`.
 - `ffmpeg` phải cài bằng package manager và có trên PATH.

 4. Khởi động Postgres + Redis (chỉ khi USE_SQLITE=false):

 ```bash
 ./data_service/scripts/start.sh
 ```

 Nếu `RUN_DB_INIT=true` script sẽ nạp `data_service/schema.sql` (LƯU Ý: sẽ DROP và recreate schema `sttt`).

 5. Người dùng mặc định: schema có seed 1 user `admin@example.com` (hash placeholder). Bạn có thể thêm user trực tiếp vào DB.

 ## Chạy dịch vụ
  - Worker (chạy nền):

 ```bash
 # Whisper worker
 python -m worker.whisper_worker
 # NLLB worker
 python -m worker.nllb_worker
 ```

 - API server (development):

 ```bash
 uvicorn api_service.main:app --host 0.0.0.0 --port 8125 --reload
 ```

 Workers lắng nghe queue trên Redis (config trong `.env`).

 ## Cấu trúc DB & helper

 - `data_service/schema.sql` chứa schema, trigger để đồng bộ trạng thái job cha/con.
 - `data_service/db.py` là helper đơn giản dùng `psycopg2`.

 ## Các endpoint chính

 - POST /login — body: `email`, `password` → trả access token (JWT)
 - POST /upload — upload audio multipart (trả `path`, ví dụ `upload/20260310/..`)
 - POST /submit-job — tạo job; body gồm `file_path`, `type` (`transcribe`|`translate`), `input_language`, `output_language`, `output_type` (`text`|`srt`)
 - GET /get-job/{job_id} — (đã thêm) lấy thông tin job; cần header `Authorization: Bearer <token>`; endpoint kiểm tra job thuộc user, và đính kèm hai trường `transcribe_content` và `translate_content` (nếu file không tồn tại thì trả chuỗi rỗng)

 Xác thực: các endpoint bảo vệ yêu cầu header `Authorization: Bearer <token>`.

 ## Đường dẫn file

 - `/upload` trả đường dẫn tương đối như `upload/20260310/filename.mp3`. Khi submit job bạn có thể cung cấp đường dẫn tương đối đó (server sẽ resolve) hoặc đường dẫn tuyệt đối.
 - `transcribe_file_path` và `translate_file_path` lưu trong DB là đường dẫn được tạo bởi `submit-job` (thường là đường dẫn tuyệt đối). `GET /get-job` sẽ cố đọc nội dung file nếu tồn tại.

 ## Khắc phục sự cố (Troubleshooting)

 - Lỗi quyền/không đọc ghi file: kiểm tra working directory khi khởi server và quyền truy cập file.
 - Dịch luôn về 1 ngôn ngữ: xem logs của worker `nllb_worker.py`, kiểm tra `NLLB_MODEL`, `HF_TOKEN` và mapping trong `language-code.json` (trường `nllb`).
 - Hiệu năng: dùng GPU, hoặc chọn model nhỏ hơn.

 ## Ghi chú phát triển

 - Job queue đơn giản dùng Redis `rpush`/`blpop`.
 - Trigger trong DB cập nhật trạng thái job cha từ các job con.

 ## Bảo mật & Triển khai sản xuất

 - Thay `JWT_SECRET` thành giá trị mạnh và không commit vào repo.
 - Dùng TLS/HTTPS trong production.
 - Chạy workers dưới process manager (systemd, supervisor, container orchestrator).
 - Cân nhắc hạn chế đường dẫn file khi trả nội dung cho user (hiện code sử dụng đường dẫn lưu trong DB).

 ## Ví dụ chạy nhanh (local, dev)

 1. Tạo và kích hoạt venv
 2. Cài dependencies cho API
 3. Chạy `./data_service/scripts/start.sh` để khởi Postgres + Redis
 4. Chạy API server
 5. Gọi `/login`, `/upload`, `/submit-job`, `/get-job` với header Authorization

 Nếu bạn muốn, tôi có thể bổ sung hướng dẫn cho Windows (PowerShell) hoặc thêm `docker-compose.override.yml` để chạy API + workers trong container — cho tôi biết lựa chọn của bạn.

 ---

 README (phiên bản tiếng Việt) được tạo theo yêu cầu. Nếu bạn muốn tôi mở rộng thêm phần cài đặt GPU (các lệnh cài `torch`+CUDA cụ thể) hoặc hướng dẫn Windows chi tiết, tôi sẽ bổ sung.