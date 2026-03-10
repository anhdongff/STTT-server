import os
import re
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, status, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api_service import schemas
from api_service import auth
from api_service.enum import PostgresTableName, SqliteTableName
from api_service.response_builder import ResponseBuilder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import subprocess
import json
import logging
import traceback
from data_service.db import PostgresDB
from data_service.sqlite_db import SqliteDB

app = FastAPI(title="STTT API Service")

USE_SQLITE = os.getenv("USE_SQLITE", "false") == "true"

security = HTTPBearer()
# db = PostgresDB.from_env()  # create a global db instance for non-request operations (e.g. auth); request handlers will use context manager to get connections as needed
logger = logging.getLogger(__name__)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):

    # In lỗi chi tiết ra console
    logger.error("Validation error: %s", exc.errors())
    logger.error("Request body: %s", exc.body)

    # Trả lỗi đơn giản cho client
    error_response = ResponseBuilder.error(
        "Invalid request data",
        status_code=status.HTTP_400_BAD_REQUEST,
        action=ResponseBuilder.ACTION.TOAST
    )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_response,
    )

@app.exception_handler(Exception)
async def internal_exception_handler(request: Request, exc: Exception):

    # Log lỗi chi tiết ra console
    logger.error("Internal server error")
    logger.error("Path: %s %s", request.method, request.url.path)
    logger.error("Error: %s", str(exc))
    logger.error(traceback.format_exc())

    # Trả lỗi đơn giản cho client
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ResponseBuilder.error(
            "Internal server error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            action=ResponseBuilder.ACTION.TOAST
        )
    )

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    sub = auth.decode_access_token(token)
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = int(sub)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    user = auth.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


_FILENAME_SAFE_REGEX = re.compile(r'[^A-Za-z0-9]')
_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "upload"


def _ffprobe_duration(path: Path) -> float:
    """Return duration in seconds using ffprobe, raise RuntimeError if not available or fails."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return float(out.strip())
    except Exception as exc:
        raise RuntimeError(f"ffprobe failed: {exc}")


def _ffmpeg_split(path: Path, segment_seconds: int = 60) -> list:
    """Split audio file into segments of at most segment_seconds. Returns list of Paths.
    Uses ffmpeg segment muxer; produced files are in same dir and named base_000.ext
    Requires ffmpeg installed.
    """
    out_dir = path.parent
    stem = path.stem
    ext = path.suffix.lstrip('.')
    pattern = f"{stem}_%03d.{ext}"
    out_path = out_dir / pattern
    cmd = [
        "ffmpeg", "-y", "-i", str(path), "-f", "segment", "-segment_time", str(segment_seconds), "-c", "copy", str(out_path)
    ]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg split failed: {exc.output.decode('utf-8', errors='ignore')}" )
    # collect generated files
    files = sorted(out_dir.glob(f"{stem}_*.{ext}"))
    return files


def _sanitize_filename(name: str) -> str:
    # replace non-alnum with underscore
    return _FILENAME_SAFE_REGEX.sub("_", name)


def _ensure_max_length(filename: str, max_len: int = 200) -> str:
    if len(filename) <= max_len:
        return filename
    # try to keep extension
    parts = filename.rsplit('.', 1)
    if len(parts) == 2:
        base, ext = parts
        keep = max_len - (len(ext) + 1)
        return base[:keep] + '.' + ext
    return filename[:max_len]

@app.post("/login", response_model=schemas.TokenResponse)
def login(req: schemas.LoginRequest):
    email = req.email.lower()

    # check lock
    if auth.is_locked(email):
        content = ResponseBuilder.error(
            "Account locked due to repeated failed attempts. Try later.", status_code=status.HTTP_423_LOCKED,
            action=ResponseBuilder.ACTION.TOAST
        )
        return JSONResponse(status_code=status.HTTP_423_LOCKED, content=content)

    user = auth.get_user_by_email(email)
    if not user:
        # register failed attempt to mitigate enumeration
        auth.register_failed_attempt(email)
        content = ResponseBuilder.error("Invalid credentials", status_code=status.HTTP_401_UNAUTHORIZED, action=ResponseBuilder.ACTION.TOAST)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=content)

    # verify password
    hashed = user.get("password_hash")
    if not hashed or not auth.verify_password(req.password, hashed):
        cnt = auth.register_failed_attempt(email)
        remaining = max(0, auth.LOCK_THRESHOLD - cnt)
        content = ResponseBuilder.build({"attempts_left": remaining}, status_code=status.HTTP_401_UNAUTHORIZED, message="Invalid credentials", action=ResponseBuilder.ACTION.TOAST)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=content)

    # success
    auth.reset_failed_attempts(email)
    token = auth.create_access_token(subject=user["id"])
    # remove password hash from response (work on a copy)
    try:
        return_user = dict(user)
        return_user.pop("password_hash", None)
    except Exception:
        # fallback if user is not a mapping
        return_user = user
    content = ResponseBuilder.success({"access_token": token, "expires_in": int(os.getenv("JWT_EXP_MINUTES", "60")) * 60, "user": return_user}, action=ResponseBuilder.ACTION.TOAST)
    return JSONResponse(status_code=status.HTTP_200_OK, content=content)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    # Validate content type
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only audio files are allowed")

    # Stream to disk with size limit
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    utc_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    day_dir = _UPLOAD_DIR / utc_date
    day_dir.mkdir(parents=True, exist_ok=True)

    # prepare filename
    user_id = user["id"]
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    original_name = Path(file.filename).name
    orig_stem = original_name
    orig_ext = ''
    if '.' in original_name:
        orig_stem, orig_ext = original_name.rsplit('.', 1)
    safe_stem = _sanitize_filename(orig_stem)
    ext = _sanitize_filename(orig_ext) if orig_ext else ''
    filename_base = f"{user_id}-{timestamp_ms}-{safe_stem}"
    filename = f"{filename_base}.{ext}" if ext else filename_base
    filename = _ensure_max_length(filename, 200)

    dest = day_dir / filename
    size = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_FILE_BYTES:
                    # remove partial file
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")
                fh.write(chunk)
    finally:
        await file.close()

    # success - return relative path
    rel_path = str(dest.relative_to(Path(__file__).resolve().parent.parent).as_posix())
    content = ResponseBuilder.success({"path": rel_path}, action=ResponseBuilder.ACTION.TOAST)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=content)


@app.post("/submit-job")
def submit_job(req: schemas.SubmitJobRequest, user=Depends(get_current_user)):
    # validate types
    output_type = req.output_type.lower()
    if output_type not in ("text", "srt"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="output_type must be 'text' or 'srt'")
    job_type = req.type.lower()
    if job_type not in ("transcribe", "translate"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="type must be 'transcribe' or 'translate'")

    # load language codes if available
    repo_root = Path(__file__).resolve().parent.parent
    lang_file = repo_root / "language-code.json"
    languages = None
    if lang_file.exists():
        try:
            languages = json.loads(lang_file.read_text(encoding='utf-8'))
        except Exception:
            languages = None

    def _valid_lang(code: str) -> bool:
        if not code:
            return False
        if languages is None:
            return True
        return code in languages

    if not _valid_lang(req.input_language):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid input_language")
    if req.output_language and not _valid_lang(req.output_language):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid output_language")

    input_path = Path(req.file_path)
    if not input_path.exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_path not found")

    # ensure audio and get duration
    try:
        duration = _ffprobe_duration(input_path)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"ffprobe error: {exc}")

    segments = [input_path]
    if duration > 60:
        try:
            segments = _ffmpeg_split(input_path, 60)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"audio split failed: {exc}")

    total_children = len(segments)

    # prepare output file paths
    out_ext = "srt" if output_type == "srt" else "txt"
    trans_dir = repo_root / "transcribe"
    translate_dir = repo_root / "translate"
    trans_dir.mkdir(parents=True, exist_ok=True)
    translate_dir.mkdir(parents=True, exist_ok=True)

    # insert job and children in transaction
    with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()).transaction() as db:
        # insert job
        trans_path = str((trans_dir / f"job_" ).resolve())  # placeholder; will finalize after insert
        trans_file = f"transcribe/job_output.{out_ext}"
        translate_file = f"translate/job_output.{out_ext}"
        q = (
            f"INSERT INTO {PostgresTableName.JOBS.value if not USE_SQLITE else SqliteTableName.JOBS.value} (user_id, status, total_children, completed_children, error_message, type, output_type, input_language, output_language, transcribe_file_path, translate_file_path, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
        )
        # Note: metadata column exists; pass empty json
        params = (
            user["id"],
            'pending',
            total_children,
            0,
            None,
            job_type,
            output_type,
            req.input_language,
            req.output_language,
            translate_file,
            trans_file,
            '{}'
        )
        row = db.fetchone(q, params)
        job_id = row['id']

        # now finalize file paths using job_id
        trans_path = str((trans_dir / f"{job_id}.{out_ext}").resolve())
        translate_path = str((translate_dir / f"{job_id}.{out_ext}").resolve())
        db.execute(f"UPDATE {PostgresTableName.JOBS.value if not USE_SQLITE else SqliteTableName.JOBS.value} SET transcribe_file_path = %s, translate_file_path = %s WHERE id = %s", (trans_path, translate_path, job_id))

        # insert children and enqueue messages
        insert_q = f"INSERT INTO {PostgresTableName.CHILDREN_JOBS.value if not USE_SQLITE else SqliteTableName.CHILDREN_JOBS.value} (job_id, input_file_path, status, metadata) VALUES (%s, %s, %s, %s) RETURNING id"
        offset = 0
        for seg in segments:
            child_row = db.fetchone(insert_q, (job_id, str(seg.resolve()), 'pending', '{}'))
            child_id = child_row['id']
            # push to whisper_queue
            if not USE_SQLITE:
                payload = {
                    'job_child_id': child_id,
                    'input_file_path': str(seg.resolve()),
                    'offset': offset,
                    'input_language': req.input_language,
                    'output_language': req.output_language,
                    'type': job_type,
                    'output_type': output_type,
                    'transcribe_file_path': trans_path,
                    'translate_file_path': translate_path,
                }
                from cache_service.redis_client import rpush as cache_rpush
                cache_rpush('whisper_queue', json.dumps(payload))
            else:
                insert_queue = f"INSERT INTO {SqliteTableName.WHISPER_QUEUE.value} (job_child_id, input_file_path, offset, input_language, output_language, type, output_type, transcribe_file_path, translate_file_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                db.execute(insert_queue, (child_id, str(seg.resolve()), offset, req.input_language, req.output_language, job_type, output_type, trans_path, translate_path))
            offset += 1

    content = ResponseBuilder.success({'job_id': job_id, 'total_children': total_children}, action=ResponseBuilder.ACTION.TOAST)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=content)


@app.get("/get-job/{job_id}")
def get_job(job_id: int, user=Depends(get_current_user)):
    # ensure job exists and belongs to requesting user
    try:
        with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
            row = db.fetchone(f"SELECT * FROM {PostgresTableName.JOBS.value if not USE_SQLITE else SqliteTableName.JOBS.value} WHERE id = %s AND user_id = %s", (job_id, user["id"]))
    except Exception:
        logger.exception('DB error while fetching job %s for user %s', job_id, user.get('id'))
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=ResponseBuilder.error("Internal server error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR))

    if not row:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=ResponseBuilder.error("Job not found", status_code=status.HTTP_404_NOT_FOUND))

    repo_root = Path(__file__).resolve().parent.parent

    def _read_optional(path_str: str) -> str:
        if not path_str:
            return ""
        try:
            p = Path(path_str)
            if not p.exists():
                # try relative to repo root
                p = repo_root / path_str
            if p.exists():
                try:
                    return p.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    return ""
        except Exception:
            return ""
        return ""

    transcribe_content = _read_optional(row.get('transcribe_file_path'))
    translate_content = _read_optional(row.get('translate_file_path'))

    # copy row and add contents; ensure values are JSON serializable
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    out['transcribe_content'] = transcribe_content
    out['translate_content'] = translate_content

    return JSONResponse(status_code=status.HTTP_200_OK, content=ResponseBuilder.success(out))
