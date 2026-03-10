README — data_service

Mô tả

Thư mục `data_service` chứa mã và tài nguyên để tương tác với cơ sở dữ liệu PostgreSQL cho dự án STTT. Nó bao gồm:

- `schema.sql` — định nghĩa schema `sttt` (tables, triggers, functions). File có lệnh `DROP SCHEMA IF EXISTS sttt CASCADE;` nên thao tác này sẽ xóa dữ liệu hiện có.
- `db.py` — helper Python `PostgresDB` để kết nối và thực thi SQL (psycopg2). Hỗ trợ `execute`, `fetchone`, `fetchall`, `transaction`.
- `sql_builder.py` — các lớp builder đơn giản: `SelectBuilder`, `InsertBuilder`, `UpdateBuilder`, `DeleteBuilder` để compose SQL tham số hóa an toàn.
- `requirements.txt` — dependencies Python (psycopg2-binary).
- `README-env.md` — giải thích các biến trong `.env.example`.
- `README-usage.md` — ví dụ sử dụng nhanh (cài, chạy, ví dụ code).

Yêu cầu (lần đầu)

- Python 3.8+ (đề nghị)
- pip
- Docker & Docker Compose (để chạy Postgres + DiceDB)

Cài đặt local (Python, nếu bạn muốn chạy code trong `data_service`)

1. Tạo virtualenv và cài dependencies:

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux / Git Bash
source .venv/Scripts/activate # hoặc .venv/bin/activate

pip install -r data_service/requirements.txt
```

2. Thiết lập biến môi trường (cách đơn giản: copy file example và chỉnh)

```bash
cp .env.example .env
# chỉnh các giá trị POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT, REDIS_PORT
# nếu bạn muốn script start tự apply schema, set RUN_DB_INIT=true (cảnh báo destructive)
```

Chạy toàn bộ (Docker + optional DB init)

Từ thư mục gốc của workspace (nơi chứa `docker-compose.yml`), có hai lựa chọn:

A) Dùng script start (khuyến nghị)

- Unix / Git Bash / WSL:

```bash
chmod +x ./data_service/scripts/start.sh # nếu cần
./data_service/scripts/start.sh
```

- Windows PowerShell:

```powershell
.\data_service\scripts\start.ps1
```

Script sẽ:
- Chạy `docker compose up -d --remove-orphans` để start containers (Postgres, DiceDB)
- Nếu `.env` có `RUN_DB_INIT=true`, script chờ Postgres sẵn sàng rồi áp dụng `data_service/schema.sql` (đè/recreate schema)

B) Thực hiện thủ công (nếu bạn thích kiểm soát tay)

```bash
# start containers
docker compose up -d

# (tuỳ chọn) apply schema bằng pipe (Unix)
cat data_service/schema.sql | docker exec -i sttt-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1

# hoặc copy + run (Windows)
docker cp data_service/schema.sql sttt-postgres:/tmp/schema.sql
docker exec sttt-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -f /tmp/schema.sql -v ON_ERROR_STOP=1
```

Sử dụng API Python (ví dụ)

```python
from data_service.db import PostgresDB
from data_service.sql_builder import SelectBuilder, InsertBuilder

with PostgresDB.from_env() as db:
    # SELECT
    q, params = SelectBuilder('sttt.jobs').select('id','status').where('status = %s', ('pending',)).limit(10).build()
    rows = db.fetchall(q, params)

    # INSERT
    ib = InsertBuilder('sttt.job_children').set('job_id', 1).set('input_file_path', '/tmp/file.wav')
    q2, params2 = ib.build()
    db.execute(q2, params2)
```

Lưu ý quan trọng

- `schema.sql` hiện tại chứa `DROP SCHEMA IF EXISTS sttt CASCADE;` — việc chạy file này sẽ xóa toàn bộ dữ liệu trước đó trong schema `sttt`. Chỉ chạy trong môi trường phát triển hoặc khi bạn muốn reset dữ liệu.
- Để production, nên chuyển sang migration tool (Flyway, Alembic, Liquibase...) và sử dụng secrets manager cho mật khẩu.

Gợi ý mở rộng

- Thêm logging cho `db.py` (logging module) để dễ debug.
- Dùng connection pool (`psycopg2.pool.SimpleConnectionPool`) nếu có nhiều kết nối.
- Tạo tests cho `sql_builder.py` (unit tests) để đảm bảo build đúng các câu SQL với tham số.

Nếu bạn muốn tôi:
- viết tests cho `sql_builder.py`, hoặc
- chuyển `db.py` sang async (asyncpg) hoặc Node.js/TypeScript version,
- thêm backup tự động trước khi apply schema (dump SQL),
hãy cho biết chọn mục nào tôi sẽ tiếp tục implement.
