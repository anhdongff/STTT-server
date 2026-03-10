"""Small wrapper around redis.Redis for cache operations with fail-safe behavior.

Functions return safe defaults when redis is not installed or unavailable and
log warnings. This keeps the application resilient if DiceDB/Redis is down.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
import logging
from typing import Optional
from pathlib import Path

env_path = Path(__file__).resolve().parents[1] / ".env"

load_dotenv(env_path)  # load .env from repository root (if present)

_client = None
_REDIS_AVAILABLE = False

try:
    import redis
    from redis.exceptions import RedisError
    _REDIS_AVAILABLE = True
except Exception:
    redis = None
    RedisError = Exception
    _REDIS_AVAILABLE = False


def _get_client() -> Optional["redis.Redis"]:
    global _client
    if not _REDIS_AVAILABLE:
        return None
    if _client is None:
        host = os.getenv("REDIS_HOST", "localhost")
        port_raw = os.getenv("REDIS_PORT", "6380")
        try:
            port = int(port_raw)
        except Exception:
            port = 6379
        _client = redis.Redis(
            host=host,
            port=port,
            decode_responses=True,
            socket_connect_timeout=2,
            # socket_timeout=2,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _client


def exists(key: str) -> bool:
    try:
        c = _get_client()
        if c is None:
            return False
        return c.exists(key) == 1
    except RedisError as exc:
        logging.warning("Redis exists() error for %s: %s", key, exc)
        return False


def incr(key: str) -> int:
    try:
        c = _get_client()
        if c is None:
            return 0
        return int(c.incr(key))
    except RedisError as exc:
        logging.warning("Redis incr() error for %s: %s", key, exc)
        return 0


def expire(key: str, seconds: int) -> bool:
    try:
        c = _get_client()
        if c is None:
            return False
        return c.expire(key, seconds)
    except RedisError as exc:
        logging.warning("Redis expire() error for %s: %s", key, exc)
        return False


def set_key(key: str, value: str, ex: Optional[int] = None) -> bool:
    try:
        c = _get_client()
        if c is None:
            return False
        return c.set(key, value, ex=ex)
    except RedisError as exc:
        logging.warning("Redis set() error for %s: %s", key, exc)
        return False


def delete(key: str) -> int:
    try:
        c = _get_client()
        if c is None:
            return 0
        return c.delete(key)
    except RedisError as exc:
        logging.warning("Redis delete() error for %s: %s", key, exc)
        return 0


def ping() -> bool:
    try:
        c = _get_client()
        if c is None:
            return False
        return c.ping()
    except RedisError as exc:
        logging.warning("Redis ping error: %s", exc)
        return False


def rpush(key: str, value: str) -> int:
    try:
        c = _get_client()
        if c is None:
            return 0
        return c.rpush(key, value)
    except RedisError as exc:
        logging.warning("Redis rpush() error for %s: %s", key, exc)
        return 0


def blpop(key: str, timeout: int = 0):
    """Blocking pop left. Returns tuple (key, value) or None on timeout/error."""
    try:
        c = _get_client()
        if c is None:
            return None
        res = c.blpop(key, timeout=timeout)
        return res
    except RedisError as exc:
        logging.warning("Redis blpop() error for %s: %s", key, exc)
        return None
