STTT API Service

This folder contains a minimal FastAPI-based API server for the STTT project.

Files:
- `main.py` — FastAPI app with a `/login` endpoint.
- `auth.py` — authentication helpers: bcrypt verify, JWT creation, DiceDB-based lock tracking.
- `schemas.py` — Pydantic request/response schemas.
- `requirements.txt` — Python dependencies.

Quick start (local)

1. Create virtualenv and install dependencies (from repo root):

```bash
python -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate
pip install -r api_service/requirements.txt
```

2. Ensure Postgres and DiceDB are running (use `docker compose up -d` from project root).
3. Set environment variables (copy `.env.example` to `.env` and add `JWT_SECRET=...`).
4. Run the API server:

```bash
uvicorn api_service.main:app --reload --host 0.0.0.0 --port 8125
```

Test login (example):

POST http://localhost:8125/login
Content-Type: application/json

{
  "email": "admin@example.com",
  "password": "yourpassword"
}

Notes
- The script uses DiceDB (Redis-compatible) configured via `DICEDB_HOST`/`DICEDB_PORT` to store login fail counters and locks.
- JWT secret must be set via `JWT_SECRET` environment variable for production.
