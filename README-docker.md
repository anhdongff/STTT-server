Hướng dẫn nhanh (Tiếng Việt) — chạy PostgreSQL và Redis bằng docker-compose

Tệp đã tạo:
- `docker-compose.yml` — khởi hai service: Postgres và DiceDB
- `.env.example` — mẫu biến môi trường

Khởi động nhanh

1. Sao chép `.env.example` thành `.env` và chỉnh sửa (nếu cần):

```bash
cp .env.example .env
# chỉnh giá trị trong .env (mật khẩu, port...)
```

2. Khởi động dịch vụ:

```bash
docker compose up -d
```

3. Kiểm tra trạng thái:

```bash
docker compose ps
```

Kết nối

- Kết nối tới Postgres từ máy host:

```bash
psql "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"
```

# Kết nối tới Redis bằng `redis-cli`:

```bash
redis-cli -p ${REDIS_PORT}
# hoặc
docker exec -it sttt-redis redis-cli
```

Ghi chú

- Sử dụng multi-container (khuyến nghị) giúp quản lý, nâng cấp và mở rộng dễ dàng hơn.
- Nếu bạn muốn một image đơn chạy cả hai dịch vụ, tôi có thể tạo `Dockerfile` kèm supervisor (ví dụ: `supervisord`).

Hoàn tất.
