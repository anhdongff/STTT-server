import os
import re
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, status, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
from typing import Dict, List
from contextlib import asynccontextmanager

from api_service import schemas
from api_service import auth
from api_service.enum import PostgresTableName, SqliteTableName, JobStatus
from api_service.response_builder import ResponseBuilder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import subprocess
import json
import logging
import traceback
from data_service.db import PostgresDB
from data_service.sqlite_db import SqliteDB
from websockets.frames import CloseCode

app = FastAPI(title="STTT API Service")

USE_SQLITE = os.getenv("USE_SQLITE", "false") == "true"
SMALL_JOB_THRESHOLD_SECONDS = int(os.getenv("SMALL_JOB_THRESHOLD_SECONDS", "300"))

# WebSocket job -> set of websockets
sockets_by_job: Dict[int, List[WebSocket]] = {}
# websocket (id) -> job id
socket_job_map: Dict[int, int] = {}
# lock to protect mappings
socket_map_lock = asyncio.Lock()

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

def get_current_user_middleware(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    user = get_current_user_by_jwt_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Thông tin xác thực không hợp lệ")
    return user

def get_current_user_by_jwt_token(token: str):
    try:
        sub = auth.decode_access_token(token)
        user_id = int(sub) if sub else None
        if user_id is None:
            return None
        user = auth.get_user_by_id(user_id)
        if not user:
            return None
        return user
    except Exception as exc:
        logger.error("Token decode error: %s", exc)
        return None


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


def _read_optional(path_str: str) -> str:
    """Return file contents as string if path exists, trying repo-relative fallback; empty string on error."""
    if not path_str:
        return ""
    try:
        repo_root = Path(__file__).resolve().parent.parent
        p = Path(path_str)
        if not p.exists():
            p = repo_root / path_str
        if p.exists():
            try:
                return p.read_text(encoding='utf-8', errors='replace')
            except Exception:
                return ""
    except Exception:
        return ""
    return ""

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
        content = ResponseBuilder.build({"attempts_left": remaining}, status_code=status.HTTP_401_UNAUTHORIZED, message="Thông tin xác thực không hợp lệ", action=ResponseBuilder.ACTION.TOAST)
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
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user_middleware)):
    # Validate content type
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chỉ cho phép tải lên tệp âm thanh")

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
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Tệp quá lớn. Kích thước tối đa cho phép là 100 MB.")
                fh.write(chunk)
    finally:
        await file.close()

    # success - return relative path
    rel_path = str(dest.relative_to(Path(__file__).resolve().parent.parent).as_posix())
    content = ResponseBuilder.success({"path": rel_path}, action=ResponseBuilder.ACTION.TOAST)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=content)

def submit_job_and_split(output_type: str, job_type: str, input_language: str, output_language: str, file_path: str, user_id: int):
    #  validate types
    if output_type not in ("text", "srt"):
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Loại đầu ra không hợp lệ'
    if job_type not in ("transcribe", "translate"):
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Loại công việc không hợp lệ'

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
    if not _valid_lang(input_language):
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Ngôn ngữ đầu vào không hợp lệ'
    if output_language and not _valid_lang(output_language):
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Ngôn ngữ đầu ra không hợp lệ'

    input_path = Path(file_path)
    if not input_path.exists():
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Tệp đầu vào không tồn tại'

    # ensure audio and get duration
    try:
        duration = _ffprobe_duration(input_path)
    except Exception as exc:
        return -1, None, status.HTTP_400_BAD_REQUEST, CloseCode.INVALID_DATA, 'Tệp âm thanh không hợp lệ'

    segments = [input_path]
    if duration > 60:
        try:
            segments = _ffmpeg_split(input_path, 60)
        except Exception as exc:
            return -1, None, status.HTTP_500_INTERNAL_SERVER_ERROR, CloseCode.INTERNAL_ERROR, 'Lỗi xử lý tệp âm thanh'

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
            user_id,
            'pending',
            total_children,
            0,
            None,
            job_type,
            output_type,
            input_language,
            output_language,
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
            # decide whether this child should be a large job (placed into whisper_large_queue)
            is_large = duration > SMALL_JOB_THRESHOLD_SECONDS
            if not USE_SQLITE:
                payload = {
                    'job_child_id': child_id,
                    'input_file_path': str(seg.resolve()),
                    'offset': offset,
                    'input_language': input_language,
                    'output_language': output_language,
                    'type': job_type,
                    'output_type': output_type,
                    'transcribe_file_path': trans_path,
                    'translate_file_path': translate_path,
                }
                from cache_service.redis_client import rpush as cache_rpush
                queue_name = 'whisper_large_queue' if is_large else 'whisper_queue'
                cache_rpush(queue_name, json.dumps(payload))
            else:
                table = SqliteTableName.WHISPER_LARGE_QUEUE.value if is_large else SqliteTableName.WHISPER_QUEUE.value
                insert_queue = f"INSERT INTO {table} (job_child_id, input_file_path, offset, input_language, output_language, type, output_type, transcribe_file_path, translate_file_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                db.execute(insert_queue, (child_id, str(seg.resolve()), offset, input_language, output_language, job_type, output_type, trans_path, translate_path))
            offset += 1
    return job_id, total_children, status.HTTP_201_CREATED, CloseCode.NORMAL_CLOSURE, None

@app.post("/submit-job")
def submit_job(req: schemas.SubmitJobRequest, user=Depends(get_current_user_middleware)):
    job_id, total_children, http_status_code, close_code, reason = submit_job_and_split(
        output_type=req.output_type.lower(),
        job_type=req.type.lower(),
        input_language=req.input_language,
        output_language=req.output_language,
        file_path=req.file_path,
        user_id=user["id"]
    )
    if job_id == -1:
        raise HTTPException(status_code=http_status_code, detail=reason)
    
    content = ResponseBuilder.success({'job_id': job_id, 'total_children': total_children}, action=ResponseBuilder.ACTION.TOAST)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=content)

@app.websocket("/submit_and_get_job")
async def websocket_submit_and_get_job(ws: WebSocket):
    # authenticate token from query string
    token = ws.query_params.get('token')
    user = None
    try:
        from api_service import auth as _auth
        user = _auth.get_current_user_by_jwt_token(token) if token else None
    except Exception:
        user = None

    if not user:
        # reject connection
        await ws.close(code=CloseCode.POLICY_VIOLATION, reason="Xác thực thất bại")
        return

    await ws.accept()

    # wait for initial payload
    try:
        msg = await ws.receive_json()
    except WebSocketDisconnect:
        return
    except Exception:
        await ws.close(code=CloseCode.INVALID_DATA, reason='Dữ liệu không hợp lệ')
        return

    # merge user id
    job_id, total_children, _, close_socket_code, close_reason = submit_job_and_split(output_type=msg.get('output_type', '').lower(),
                                      job_type=msg.get('type', '').lower(),
                                        input_language=msg.get('input_language'),
                                        output_language=msg.get('output_language'),
                                        file_path=msg.get('file_path'),
                                        user_id=user["id"])
    if job_id == -1 or job_id is None:
        await ws.close(code=close_socket_code, reason=close_reason)
        return

    # send back created info
    await ws.send_json({'job_id': job_id, 'total_children': total_children})

    # register socket for this job
    async with socket_map_lock:
        lst = sockets_by_job.setdefault(job_id, [])
        lst.append(ws)
        socket_job_map[id(ws)] = job_id

    try:
        # keep connection open until client disconnects
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # cleanup
        async with socket_map_lock:
            jid = socket_job_map.pop(id(ws), None)
            if jid:
                lst = sockets_by_job.get(jid)
                if lst and ws in lst:
                    lst.remove(ws)
                if lst and len(lst) == 0:
                    sockets_by_job.pop(jid, None)


async def _monitor_jobs_loop():
    print("Starting job monitor loop")
    last_run = datetime.now(timezone.utc)
    while True:
        start_time = datetime.now(timezone.utc)
        try:
            # blocking DB call in thread
            def _fetch():
                with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
                    rows = db.fetchall(f"SELECT * FROM {PostgresTableName.JOBS.value if not USE_SQLITE else SqliteTableName.JOBS.value} WHERE (status = %s OR status = %s OR (status = %s AND completed_children > 0)) AND datetime(updated_at) >= datetime(%s)", (JobStatus.COMPLETED, JobStatus.ERROR, JobStatus.RUNNING, last_run.isoformat()))
                    return rows

            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(None, _fetch)
            if rows:
                print(f"Job monitor found {len(rows)} updated jobs since {last_run.isoformat()}")
                # update last_run to the start of this loop
                last_run = start_time
                # dispatch to sockets
                async with socket_map_lock:
                    for r in rows:
                        jid = int(r['id'])
                        # prepare serializable payload, include transcribe/translate contents
                        out = {}
                        for k, v in r.items():
                            if isinstance(v, datetime):
                                out[k] = v.isoformat()
                            else:
                                out[k] = v
                        out['transcribe_content'] = _read_optional(out.get('transcribe_file_path'))
                        out['translate_content'] = _read_optional(out.get('translate_file_path'))

                        lst = sockets_by_job.get(jid, [])
                        for ws in list(lst):
                            try:
                                await ws.send_json(out)
                                # if job completed, close connection normally and remove
                                if out.get('status') == 'completed':
                                    try:
                                        await ws.close(code=CloseCode.NORMAL_CLOSURE, reason='Công việc đã hoàn thành')
                                    except Exception:
                                        pass
                                    try:
                                        lst.remove(ws)
                                    except Exception as e:
                                        print(f"Error occurred while removing socket: {e}")
                            except Exception as e:
                                print(f"Error occurred while sending JSON: {e}")
                                # remove dead sockets
                                try:
                                    lst.remove(ws)
                                except Exception as e:
                                    print(f"Error occurred while removing socket: {e}")
        except Exception as e:
            print(f"Error occurred in job monitor loop: {e}")
            # swallow errors but sleep to avoid tight loop
            await asyncio.sleep(1)
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app):
    # start background monitor when app starts
    task = asyncio.create_task(_monitor_jobs_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# attach lifespan to the app router
app.router.lifespan_context = lifespan


@app.get("/get-job/{job_id}")
def get_job(job_id: int, user=Depends(get_current_user_middleware)):
    # ensure job exists and belongs to requesting user
    try:
        with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
            row = db.fetchone(f"SELECT * FROM {PostgresTableName.JOBS.value if not USE_SQLITE else SqliteTableName.JOBS.value} WHERE id = %s AND user_id = %s", (job_id, user["id"]))
    except Exception:
        logger.exception('DB error while fetching job %s for user %s', job_id, user.get('id'))
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=ResponseBuilder.error("Lỗi máy chủ nội bộ", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR))

    if not row:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=ResponseBuilder.error("Không tìm thấy công việc", status_code=status.HTTP_404_NOT_FOUND))

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
