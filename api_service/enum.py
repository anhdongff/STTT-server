from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"


class OutputType(str, Enum):
    TEXT = "text"
    SRT = "srt"