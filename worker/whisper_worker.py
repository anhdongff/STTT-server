import os
import sys
import time
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone

# ensure project root is on sys.path so top-level packages (cache_service, data_service, etc.)
# can be imported when running this script directly (python worker/whisper_worker.py)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cache_service.redis_client import blpop, rpush
from data_service.db import PostgresDB

# optional model import
try:
    from faster_whisper import WhisperModel
    _FW_AVAILABLE = True
except Exception:
    WhisperModel = None
    _FW_AVAILABLE = False

LOG = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
LANG_FILE = ROOT / "language-code.json"
TEMP_DIR = ROOT / "temp"

REDIS_WHISPER_QUEUE = "whisper_queue"
REDIS_NLLB_QUEUE = "nllb_queue"
REDIS_JOB_DONE = "job_done_queue"

WAIT_FOR_JOB_DONE = os.getenv("WAIT_FOR_JOB_DONE", "true").lower() in ("1", "true", "yes")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-medium")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", None)
WHISPER_BEAM = int(os.getenv("WHISPER_BEAM_SIZE", "6"))

SEGMENT_SECONDS = 60


def load_languages():
    try:
        return json.loads(LANG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


class WhisperWorker:
    def __init__(self):
        self.languages = load_languages()
        self.model = None
        if _FW_AVAILABLE:
            LOG.info("Loading faster-whisper model %s...", WHISPER_MODEL)
            try:
                # ctranslate2 requires a valid compute_type (not None). Use 'default' when env var not set.
                compute = WHISPER_COMPUTE if WHISPER_COMPUTE and WHISPER_COMPUTE.lower() != 'none' else 'default'
                # prefer CUDA if available; faster_whisper/ctranslate2 will error if device unsupported
                device = "cuda"
                try:
                    # prefer CUDA only if available
                    import torch
                    if not (hasattr(torch, 'cuda') and torch.cuda.is_available()):
                        device = "cpu"
                except Exception:
                    device = "cpu"
                print(f"whisper using device={device} compute={compute}")
                self.model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute)
            except Exception as exc:
                LOG.exception("Failed loading model: %s", exc)
                self.model = None
        else:
            LOG.warning("faster_whisper not available; worker will not process audio")

    def lang_code(self, code: str):
        if not code:
            return None
        if self.languages is None:
            # assume code is already whisper code
            return code
        entry = self.languages.get(code)
        if not entry:
            return None
        return entry.get("whisper")

    def run(self):
        LOG.info("Worker started. WAIT_FOR_JOB_DONE=%s", WAIT_FOR_JOB_DONE)
        while True:
            item = blpop(REDIS_WHISPER_QUEUE, timeout=0)
            # item is (key, value)
            try:
                if not item:
                    continue
                _, raw = item
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                job = json.loads(raw)
            except Exception as exc:
                LOG.exception("Invalid job payload: %s", exc)
                continue

            try:
                self.process_job(job)
            except Exception:
                LOG.exception("Error processing job %s", job.get('id'))

    def process_job(self, job: dict):
        child_id = job.get('id')
        input_file = Path(job.get('input_file_path'))
        offset = int(job.get('offset', 0))
        input_lang_key = job.get('input_language')
        output_lang_key = job.get('output_language')
        job_type = job.get('type')
        output_type = job.get('output_type')
        transcribe_path = Path(job.get('transcribe_file_path'))
        translate_path = Path(job.get('translate_file_path'))

        LOG.info("Processing child %s file=%s offset=%s", child_id, input_file, offset)

        # set status running
        try:
            with PostgresDB.from_env() as db:
                db.execute("UPDATE sttt.job_children SET status = %s WHERE id = %s", ('running', child_id))
        except Exception:
            LOG.exception("Failed to update job_children status to running for id=%s", child_id)

        in_whisper = self.lang_code(input_lang_key)

        # run model
        if self.model is None:
            LOG.error("Model not loaded; skipping processing")
            return

        try:
            segments, info = self.model.transcribe(str(input_file), language=in_whisper, beam_size=WHISPER_BEAM, task='transcribe')
        except Exception:
            LOG.exception("Model error for file %s", input_file)
            # mark failed
            with PostgresDB.from_env() as db:
                db.execute("UPDATE sttt.job_children SET status = %s WHERE id = %s", ('error', child_id))
            return

        seg_list = list(segments)

        # write to transcribe file (append)
        if not transcribe_path.parent.exists():
            transcribe_path.parent.mkdir(parents=True, exist_ok=True)
        if not translate_path.parent.exists():
            translate_path.parent.mkdir(parents=True, exist_ok=True)

        if output_type == 'text':
            # append each segment as a line
            with transcribe_path.open('a', encoding='utf-8') as fh:
                for s in seg_list:
                    text = s.get('text') if isinstance(s, dict) else getattr(s, 'text', '')
                    fh.write(text.replace('\n', ' ').strip() + '\n')
        else:
            # srt formatting; need sequence numbers
            existing = 0
            if transcribe_path.exists():
                # rough count: count lines that are just integers
                try:
                    with transcribe_path.open('r', encoding='utf-8') as fh:
                        for line in fh:
                            if line.strip().isdigit():
                                existing += 1
                except Exception:
                    existing = 0
            idx = existing + 1
            with transcribe_path.open('a', encoding='utf-8') as fh:
                for s in seg_list:
                    start = float(s.start) + offset * SEGMENT_SECONDS
                    end = float(s.end) + offset * SEGMENT_SECONDS
                    text = s.get('text') if isinstance(s, dict) else getattr(s, 'text', '')
                    fh.write(f"{idx}\n")
                    fh.write(self._format_srt_time(start) + " --> " + self._format_srt_time(end) + "\n")
                    fh.write(text.strip() + "\n\n")
                    idx += 1

        # if job_type is transcribe -> mark completed
        if job_type == 'transcribe':
            with PostgresDB.from_env() as db:
                db.execute("UPDATE sttt.job_children SET status = %s WHERE id = %s", ('completed', child_id))
            LOG.info("Child %s marked completed (transcribe)", child_id)
            return

        # else translate: create temp file and push to nllb_queue
        today = datetime.now(timezone.utc).strftime('%Y%m%d')
        temp_dir = TEMP_DIR / today
        temp_dir.mkdir(parents=True, exist_ok=True)
        rand_name = uuid.uuid4().hex
        ext = 'srt' if output_type == 'srt' else 'txt'
        temp_path = temp_dir / f"{rand_name}.{ext}"

        # write content similar to above into temp file
        if output_type == 'text':
            with temp_path.open('w', encoding='utf-8') as fh:
                for s in seg_list:
                    text = s.get('text') if isinstance(s, dict) else getattr(s, 'text', '')
                    fh.write(text.replace('\n', ' ').strip() + '\n')
        else:
            idx = 1
            with temp_path.open('w', encoding='utf-8') as fh:
                for s in seg_list:
                    start = float(s.start) + offset * SEGMENT_SECONDS
                    end = float(s.end) + offset * SEGMENT_SECONDS
                    text = s.get('text') if isinstance(s, dict) else getattr(s, 'text', '')
                    fh.write(f"{idx}\n")
                    fh.write(self._format_srt_time(start) + " --> " + self._format_srt_time(end) + "\n")
                    fh.write(text.strip() + "\n\n")
                    idx += 1

        # push to nllb_queue with input_file_path changed to temp_path
        job2 = dict(job)
        job2['input_file_path'] = str(temp_path.resolve())
        rpush(REDIS_NLLB_QUEUE, json.dumps(job2))

        # optionally wait for job_done
        if WAIT_FOR_JOB_DONE:
            LOG.info("Waiting for job done for child %s", child_id)
            while True:
                res = blpop(REDIS_JOB_DONE, timeout=0)
                if not res:
                    continue
                try:
                    _, raw = res
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    msg = json.loads(raw)
                except Exception:
                    continue
                # expect msg contains child_id and status
                if msg.get('id') == child_id:
                    LOG.info("Received job done for %s: %s", child_id, msg)
                    break

    def _format_srt_time(self, seconds: float) -> str:
        ms = int((seconds - int(seconds)) * 1000)
        t = int(seconds)
        hours = t // 3600
        minutes = (t % 3600) // 60
        secs = t % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


if __name__ == '__main__':
    w = WhisperWorker()
    w.run()
