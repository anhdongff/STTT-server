import os
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple, Optional

# # ensure project root is on sys.path so imports like cache_service and data_service work
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cache_service.redis_client import blpop, rpush
from data_service.db import PostgresDB
from data_service.sqlite_db import SqliteDB
from api_service.enum import JobStatus, SqliteTableName, PostgresTableName

LOG = logging.getLogger("nllb_worker")
logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
LANG_FILE = ROOT / "language-code.json"

REDIS_NLLB_QUEUE = "nllb_queue"
REDIS_JOB_DONE = "job_done_queue"

WAIT_FOR_JOB_DONE = os.getenv("WAIT_FOR_JOB_DONE", "true").lower() in ("1", "true", "yes")
NLLB_MODEL = os.getenv("NLLB_MODEL", "facebook/nllb-200-distilled-600M")
NLLB_NUM_BEAMS = int(os.getenv("NLLB_NUM_BEAMS", "2"))
HF_TOKEN = os.getenv("HF_TOKEN")

USE_SQLITE = os.getenv("USE_SQLITE", "false") == "true"


def load_languages():
    try:
        return json.loads(LANG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_srt(path: Path) -> Tuple[List[str], List[Tuple[float, float]]]:
    text_segments: List[str] = []
    times: List[Tuple[float, float]] = []
    try:
        data = path.read_text(encoding="utf-8")
    except Exception:
        return [], []
    parts = [p.strip() for p in data.split('\n\n') if p.strip()]
    for p in parts:
        lines = p.splitlines()
        if len(lines) < 2:
            continue
        # second line expected to be time
        time_line = lines[1].strip()
        try:
            start_s, _, end_s = time_line.replace(' ', '').partition('-->')
            def to_seconds(t: str) -> float:
                # format HH:MM:SS,mmm
                hms, ms = t.split(',') if ',' in t else (t, '0')
                hh, mm, ss = hms.split(':')
                return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
            start = to_seconds(start_s)
            end = to_seconds(end_s)
        except Exception:
            start = 0.0
            end = 0.0
        # remaining lines are text
        txt = '\n'.join(lines[2:]).strip()
        text_segments.append(txt)
        times.append((start, end))
    return text_segments, times


def parse_text(path: Path) -> List[str]:
    try:
        lines = [l.rstrip('\n') for l in path.read_text(encoding='utf-8').splitlines()]
        return [l for l in lines if l.strip()]
    except Exception:
        return []


class NLLBWorker:
    def __init__(self):
        self.languages = load_languages()
        self.model = None
        self.tokenizer = None
        self.device = 'cuda'
        self._load_model()

    def nllb_code(self, code: str) -> Optional[str]:
        if not code:
            return None
        if self.languages is None:
            return code
        entry = self.languages.get(code)
        if not entry:
            return None
        return entry.get('nllb')

    def _load_model(self):
        if self.model is not None:
            return
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        except Exception as exc:
            LOG.exception("Transformers/torch not available: %s", exc)
            raise

        self.device = 'cuda' if (hasattr(torch, 'cuda') and torch.cuda.is_available()) else 'cpu'
        LOG.info("Loading NLLB model %s on device=%s", NLLB_MODEL, self.device)

        # if HF_TOKEN:
        #     kwargs['use_auth_token'] = HF_TOKEN
        self.tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL, dtype=torch.float16, device_map=self.device)
        if self.device == 'cuda':
            try:
                self.model.to('cuda')
            except Exception:
                LOG.exception('Failed moving model to CUDA')

    def translate_batch(self, texts: List[str], src: str, tgt: str) -> List[str]:
        LOG.info("Translating batch of %d segments from %s to %s with NLLB model %s", len(texts), src, tgt, NLLB_MODEL)
        if not texts:
            return []
        if self.model is None:
            self._load_model()
        import torch
        batch_size = 8
        outputs: List[str] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i+batch_size]
            try:
                # if tokenizer supports src_lang attribute (mbart-like), set it so tokenization is correct
                if hasattr(self.tokenizer, 'src_lang'):
                    try:
                        self.tokenizer.src_lang = src
                    except Exception:
                        LOG.debug('Failed to set tokenizer.src_lang, continuing')

                enc = self.tokenizer(chunk, return_tensors='pt', padding=True, truncation=True)
                device = 'cuda' if (hasattr(torch, 'cuda') and torch.cuda.is_available()) else 'cpu'
                enc = {k: v.to(device) for k, v in enc.items()}

                gen_kwargs = dict(num_beams=NLLB_NUM_BEAMS)

                # many seq2seq HF models accept forced_bos_token_id to force output language
                try:
                    if tgt:
                        fb = self.tokenizer.convert_tokens_to_ids(tgt)
                        gen_kwargs['forced_bos_token_id'] = fb
                except Exception:
                    LOG.debug('Failed to set forced_bos_token_id for tgt=%s', tgt)

                # also set decoder_start_token_id as an extra safety for some model variants
                # gen_kwargs['decoder_start_token_id'] = int(forced_bos)

                with torch.no_grad():
                    out = self.model.generate(**enc, **gen_kwargs)
                decoded = self.tokenizer.batch_decode(out, skip_special_tokens=True)
                outputs.extend(decoded)
            except Exception as exc:
                LOG.exception('Error during translation chunk starting at index %d: %s', i, exc)
                # bubble up to caller to handle marking job as errored
                raise
        return outputs

    def run(self):
        LOG.info("NLLB worker started; waiting on %s (BLPOP timeout=0)", REDIS_NLLB_QUEUE)
        while True:
            if not USE_SQLITE:
                item = blpop(REDIS_NLLB_QUEUE, timeout=0)
                if not item:
                    time.sleep(0.1)
                    continue
                try:
                    _, raw = item
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    job = json.loads(raw)
                except Exception as exc:
                    LOG.exception('Invalid job payload: %s', exc)
                    continue

                try:
                    self.process_job(job)
                except Exception:
                    LOG.exception('Error processing nllb job %s', job.get('id'))
            else:
                with SqliteDB.from_env() as db:
                    row = db.fetchone(f"SELECT * FROM {SqliteTableName.NLLB_QUEUE.value} WHERE status = %s ORDER BY id LIMIT 1", (JobStatus.PENDING.value,))
                    if row:
                        job = dict(row)
                        # mark as in_progress before processing to avoid multiple workers picking the same job
                        db.execute(f"UPDATE {SqliteTableName.NLLB_QUEUE.value} SET status = %s WHERE id = %s", (JobStatus.RUNNING.value, job['id']))
                        try:
                            self.process_job(job)
                        except Exception:
                            LOG.exception('Error processing nllb job %s', job.get('id'))
                    else:
                        time.sleep(0.35)  # no pending jobs, wait before polling again

    def process_job(self, job: dict):
        child_id = job.get('job_child_id')
        input_path = Path(job.get('input_file_path'))
        output_type = job.get('output_type')
        transcribe_file = Path(job.get('transcribe_file_path'))
        translate_file = Path(job.get('translate_file_path'))
        input_lang = job.get('input_language')
        output_lang = job.get('output_language')

        LOG.info('NLLB processing child=%s file=%s', child_id, input_path)

        # determine nllb language codes
        src = self.nllb_code(input_lang) or input_lang
        tgt = self.nllb_code(output_lang) or output_lang

        # parse input file into list of segments
        texts: List[str] = []
        times: List[Tuple[float, float]] = []
        if output_type == 'text':
            texts = parse_text(input_path)
        else:
            texts, times = parse_srt(input_path)

        if not texts:
            LOG.warning('No segments found in %s', input_path)
            # still mark completed to avoid infinite loops
            try:
                with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
                    db.execute(f"UPDATE {PostgresTableName.CHILDREN_JOBS.value if not USE_SQLITE else SqliteTableName.CHILDREN_JOBS.value} SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (JobStatus.COMPLETED.value, child_id))
            except Exception:
                LOG.exception('Failed to mark job_children completed for id=%s', child_id)
            if WAIT_FOR_JOB_DONE and not USE_SQLITE:
                rpush(REDIS_JOB_DONE, json.dumps({'id': child_id}))
            return

        # translate
        try:
            translated = self.translate_batch(texts, src, tgt)
        except Exception:
            LOG.exception('Translation failed for child %s', child_id)
            try:
                with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
                    db.execute(f"UPDATE {PostgresTableName.CHILDREN_JOBS.value if not USE_SQLITE else SqliteTableName.CHILDREN_JOBS.value} SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (JobStatus.ERROR.value, child_id))
            except Exception:
                LOG.exception('Failed to mark job_children error for id=%s', child_id)
            return

        # write results to translate_file in same format
        try:
            if not translate_file.parent.exists():
                translate_file.parent.mkdir(parents=True, exist_ok=True)

            if output_type == 'text':
                with translate_file.open('a', encoding='utf-8') as fh:
                    for line in translated:
                        fh.write(line.replace('\n', ' ').strip() + '\n')
            else:
                # write srt blocks preserving times
                # determine existing index
                existing = 0
                if translate_file.exists():
                    try:
                        with translate_file.open('r', encoding='utf-8') as fh:
                            for ln in fh:
                                if ln.strip().isdigit():
                                    existing += 1
                    except Exception:
                        existing = 0
                idx = existing + 1
                with translate_file.open('a', encoding='utf-8') as fh:
                    for (start, end), txt in zip(times, translated):
                        fh.write(f"{idx}\n")
                        fh.write(self._format_srt_time(start) + " --> " + self._format_srt_time(end) + "\n")
                        fh.write(txt.strip() + "\n\n")
                        idx += 1
        except Exception:
            LOG.exception('Failed writing translate_file for child %s', child_id)

        # mark completed
        try:
            with (PostgresDB.from_env() if not USE_SQLITE else SqliteDB.from_env()) as db:
                db.execute(f"UPDATE {PostgresTableName.CHILDREN_JOBS.value if not USE_SQLITE else SqliteTableName.CHILDREN_JOBS.value} SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (JobStatus.COMPLETED.value, child_id))
        except Exception:
            LOG.exception('Failed to update job_children status to completed for id=%s', child_id)

        if WAIT_FOR_JOB_DONE and not USE_SQLITE:
            rpush(REDIS_JOB_DONE, json.dumps({'id': child_id}))

    def _format_srt_time(self, seconds: float) -> str:
        ms = int((seconds - int(seconds)) * 1000)
        t = int(seconds)
        hours = t // 3600
        minutes = (t % 3600) // 60
        secs = t % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


if __name__ == '__main__':
    w = NLLBWorker()
    w.run()
