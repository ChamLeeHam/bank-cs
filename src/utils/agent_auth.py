"""本地演示坐席登录：密码只保存加盐哈希，会话保存在进程内。"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

AUTH_FILE = Path(__file__).resolve().parents[2] / "storage" / "agent_auth.json"
SESSION_SECONDS = 8 * 60 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 5 * 60
ITERATIONS = 600_000
_lock = threading.RLock()
_sessions = {}
_failures = {}


def is_configured():
    return AUTH_FILE.is_file()


def _password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS).hex()


def setup(username, password):
    """首次设置，独占创建避免并发覆盖。"""
    salt = secrets.token_bytes(16)
    record = {"username": username, "salt": salt.hex(), "password_hash": _password_hash(password, salt),
              "iterations": ITERATIONS}
    with _lock:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)


def login(username, password, client_key):
    with _lock:
        attempts, until = _failures.get(client_key, (0, 0))
        now = time.monotonic()
        if until > now:
            return None, "locked"
        if until:
            attempts = 0
        try:
            record = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            salt = bytes.fromhex(record["salt"])
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, record["iterations"]).hex()
            valid = hmac.compare_digest(username, record["username"]) and hmac.compare_digest(actual, record["password_hash"])
        except (OSError, ValueError, KeyError, TypeError):
            return None, "unavailable"
        if not valid:
            attempts += 1
            _failures[client_key] = (attempts, now + LOCK_SECONDS if attempts >= MAX_FAILURES else 0)
            return None, "locked" if attempts >= MAX_FAILURES else "invalid"
        _failures.pop(client_key, None)
        token = secrets.token_urlsafe(32)
        _sessions[token] = now + SESSION_SECONDS
        return token, None


def is_authenticated(token):
    if not token:
        return False
    with _lock:
        expiry = _sessions.get(token, 0)
        if expiry <= time.monotonic():
            _sessions.pop(token, None)
            return False
        return True


def logout(token):
    with _lock:
        _sessions.pop(token, None)
