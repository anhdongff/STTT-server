import os
from pathlib import Path
from typing import Optional

_LOADED = False


def _find_env_file(start: Optional[Path] = None) -> Optional[Path]:
    start = Path(start or __file__).resolve().parent
    candidates = [".env", ".env.local", ".env.example"]
    for p in [start, *start.parents]:
        for name in candidates:
            f = p / name
            if f.exists():
                return f
    return None


def load_dotenv() -> bool:
    """Locate a .env file (or .env.local / .env.example) upward from this package and load values

    Values already present in os.environ are not overwritten.
    Returns True if a file was found and loaded.
    """
    global _LOADED
    if _LOADED:
        return True
    env_path = _find_env_file()
    if not env_path:
        return False
    try:
        with env_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                # only set if not already in env so we don't override real env vars
                os.environ.setdefault(key, val)
        _LOADED = True
        return True
    except Exception:
        return False
