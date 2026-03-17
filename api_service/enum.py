from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class JobType(str, Enum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"


class OutputType(str, Enum):
    TEXT = "text"
    SRT = "srt"

class PostgresTableName(str, Enum):
    USERS = "sttt.users"
    JOBS = "sttt.jobs"
    CHILDREN_JOBS = "sttt.job_children"

class SqliteTableName(str, Enum):
    USERS = "users"
    JOBS = "jobs"
    CHILDREN_JOBS = "job_children"
    WHISPER_QUEUE = "whisper_queue"
    WHISPER_LARGE_QUEUE = "whisper_large_queue"
    NLLB_QUEUE = "nllb_queue"