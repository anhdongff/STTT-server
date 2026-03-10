"""api_service package initializer.

Loads environment variables from a .env file (if present) early so submodules
that read os.getenv at import-time will see the values.
"""
from ._env import load_dotenv

# load .env when the package is imported (idempotent)
load_dotenv()

__all__ = ["_env"]
# api_service package
