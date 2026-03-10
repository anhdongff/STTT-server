Hướng dẫn sử dụng các lớp trong `data_service`

Files chính:
- `db.py` — `PostgresDB` helper để kết nối và thực thi truy vấn sang PostgreSQL (psycopg2).
- `sql_builder.py` — các builder đơn giản: `SelectBuilder`, `InsertBuilder`, `UpdateBuilder`, `DeleteBuilder`.
- `requirements.txt` — thư viện cần cài: `psycopg2-binary`.

Ví dụ sử dụng nhanh

1) Cài dependency

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

2) Kết nối và truy vấn

```python
from data_service.db import PostgresDB
from data_service.sql_builder import SelectBuilder, InsertBuilder

# Tải cấu hình từ .env bằng cách export hoặc dùng docker-compose
with PostgresDB.from_env() as db:
    # SELECT
    q, params = SelectBuilder('sttt.jobs').select('id','status').where('status = %s', ('pending',)).limit(10).build()
    rows = db.fetchall(q, params)
    print(rows)

    # INSERT
    ib = InsertBuilder('sttt.job_children').set('job_id', 1).set('input_file_path', '/tmp/file.wav')
    q2, params2 = ib.build()
    db.execute(q2, params2)
```

3) Transaction

```python
from data_service.db import PostgresDB

with PostgresDB.from_env() as db:
    with db.transaction():
        db.execute("UPDATE sttt.jobs SET status = %s WHERE id = %s", ('running', 1))
        db.execute("INSERT INTO sttt.job_children (job_id, input_file_path) VALUES (%s, %s)", (1, '/tmp/a'))
```

Gợi ý mở rộng

- Bạn có thể thêm logging, retry, connection pooling (pgbouncer hoặc psycopg2 pool).
- Thêm ORM nếu muốn (SQLAlchemy) cho mô hình phức tạp.

Nếu muốn tôi triển khai cùng bằng TypeScript/Node.js (pg) hoặc thêm async/await support, báo tôi biết để tôi tạo phiên bản tương ứng.