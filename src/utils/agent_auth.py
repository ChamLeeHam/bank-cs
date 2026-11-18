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
    record = {"users": [_make_user(username, password, "supervisor", None)]}
    with _lock:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)


def _make_user(username, password, role, team, email=None):
    salt = secrets.token_bytes(16)
    return {"username": username, "salt": salt.hex(), "password_hash": _password_hash(password, salt),
            "iterations": ITERATIONS, "role": role, "team": team, "email": email}


def _read_users():
    record = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    if isinstance(record.get("users"), list):
        return [{"email": None, **user} for user in record["users"]]
    # Existing single-account files become supervisor accounts without changing passwords.
    if all(key in record for key in ("username", "salt", "password_hash", "iterations")):
        return [{"email": None, **record, "role": "supervisor", "team": None}]
    raise ValueError("invalid agent auth file")


def identity(token):
    if not token:
        return None
    with _lock:
        session = _sessions.get(token)
        if not session:
            return None
        if isinstance(session, tuple):
            expiry, username = session
        else:  # compatibility with pre-upgrade in-memory sessions
            expiry, username = session, None
        if expiry <= time.monotonic():
            _sessions.pop(token, None)
            return None
        if username is None:
            return None
        user = next((item for item in _read_users() if item.get("username") == username), None)
        if not user:
            _sessions.pop(token, None)
            return None
        return {"username": user["username"], "role": user.get("role", "agent"), "team": user.get("team")}


def add_user(username, password, team, email=None):
    with _lock:
        users = _read_users()
        if any(user.get("username", "").casefold() == username.casefold() for user in users):
            raise FileExistsError(username)
        users.append(_make_user(username, password, "agent", team, email))
        AUTH_FILE.write_text(json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8")
    return {"username": username, "role": "agent", "team": team, "email": email}


def list_users():
    with _lock:
        return [{"username": user["username"], "role": user.get("role", "agent"), "team": user.get("team"),
                 "email": user.get("email")}
                for user in _read_users()]


def get_user(username):
    with _lock:
        return next(({"username": user["username"], "role": user.get("role", "agent"),
                       "team": user.get("team"), "email": user.get("email")} for user in _read_users()
                     if user.get("username") == username), None)


def set_email(username, email):
    with _lock:
        users = _read_users()
        user = next((item for item in users if item.get("username", "").casefold() == username.casefold()), None)
        if not user:
            return None
        user["email"] = email
        AUTH_FILE.write_text(json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8")
        return {"username": user["username"], "role": user.get("role", "agent"), "email": email}


def notification_emails(agent_username):
    with _lock:
        users = _read_users()
        supervisor = next((user for user in users if user.get("role") == "supervisor"), None)
        agent = next((user for user in users
                      if agent_username and user.get("username", "").casefold() == agent_username.casefold()), None)
        if not supervisor or not supervisor.get("email"):
            return None
        # During demos, an agent without a personal mailbox uses the supervisor mailbox.
        return {"agent_email": (agent or {}).get("email") or supervisor["email"],
                "supervisor_email": supervisor["email"]}


def login(username, password, client_key):
    with _lock:
        attempts, until = _failures.get(client_key, (0, 0))
        now = time.monotonic()
        if until > now:
            return None, "locked"
        if until:
            attempts = 0
        try:
            users = _read_users()
            if not users:
                raise ValueError("no agent users configured")
            user = next((item for item in users
                         if hmac.compare_digest(username, item.get("username", ""))), users[0])
            salt = bytes.fromhex(user["salt"])
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, user["iterations"]).hex()
            valid = (hmac.compare_digest(actual, user["password_hash"])
                     and hmac.compare_digest(username, user.get("username", "")))
        except (OSError, ValueError, KeyError, TypeError):
            return None, "unavailable"
        if not valid:
            attempts += 1
            _failures[client_key] = (attempts, now + LOCK_SECONDS if attempts >= MAX_FAILURES else 0)
            return None, "locked" if attempts >= MAX_FAILURES else "invalid"
        _failures.pop(client_key, None)
        token = secrets.token_urlsafe(32)
        _sessions[token] = (now + SESSION_SECONDS, user["username"])
        return token, None


def is_authenticated(token):
    if not token:
        return False
    with _lock:
        session = _sessions.get(token)
        expiry = session[0] if isinstance(session, tuple) else session
        if not expiry or expiry <= time.monotonic():
            _sessions.pop(token, None)
            return False
        return True


def logout(token):
    with _lock:
        _sessions.pop(token, None)
