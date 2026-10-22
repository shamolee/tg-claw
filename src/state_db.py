"""SQLite 기반 상태 관리 — flag 파일 + JSON 상태 파일 대체."""

import os
import sqlite3
import time
import threading

_DB_PATH = None
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    name TEXT PRIMARY KEY,
    status TEXT DEFAULT 'idle',
    session_id TEXT DEFAULT '',
    pid INTEGER DEFAULT 0,
    query_count INTEGER DEFAULT 0,
    restart_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    was_busy INTEGER DEFAULT 0,
    no_resume INTEGER DEFAULT 0,
    start_time REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    command TEXT NOT NULL,
    args TEXT DEFAULT '',
    created_at REAL,
    processed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS relay_config (
    bot_id TEXT,
    chat_id TEXT,
    enabled INTEGER DEFAULT 1,
    PRIMARY KEY (bot_id, chat_id)
);

CREATE TABLE IF NOT EXISTS poll_offsets (
    bot_id TEXT PRIMARY KEY,
    offset_val INTEGER DEFAULT 0,
    updated_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS teleclaw_state (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at REAL DEFAULT 0
);
"""


def init(db_path: str = ""):
    """DB 초기화. db_path 미지정 시 STATE_DIR/teleClaw.db"""
    global _DB_PATH
    if not db_path:
        from .config import STATE_DIR, DB_FILE
        os.makedirs(STATE_DIR, exist_ok=True)
        db_path = DB_FILE
    _DB_PATH = db_path
    conn = _get_conn()
    # 레거시 테이블명 마이그레이션 (supervisor_state → teleclaw_state)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "supervisor_state" in tables and "teleclaw_state" not in tables:
        conn.execute("ALTER TABLE supervisor_state RENAME TO teleclaw_state")
        conn.commit()
    conn.executescript(_SCHEMA)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    """스레드별 커넥션 반환."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


# --- 세션 상태 ---

def set_session(name: str, **kwargs):
    """세션 상태 upsert."""
    conn = _get_conn()
    kwargs["updated_at"] = time.time()
    # upsert
    existing = conn.execute("SELECT name FROM sessions WHERE name=?", (name,)).fetchone()
    if existing:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        conn.execute(f"UPDATE sessions SET {sets} WHERE name=?", (*kwargs.values(), name))
    else:
        kwargs["name"] = name
        cols = ", ".join(kwargs.keys())
        phs = ", ".join("?" for _ in kwargs)
        conn.execute(f"INSERT INTO sessions ({cols}) VALUES ({phs})", tuple(kwargs.values()))
    conn.commit()


def get_session(name: str) -> dict:
    """세션 상태 조회. 없으면 빈 dict."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE name=?", (name,)).fetchone()
    return dict(row) if row else {}


def get_all_sessions() -> dict:
    """전체 세션 상태. {name: {status, session_id, ...}}"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM sessions").fetchall()
    return {r["name"]: dict(r) for r in rows}


def delete_session(name: str):
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE name=?", (name,))
    conn.commit()


# --- 명령 큐 (flag 파일 대체) ---

def push_command(target: str, command: str, args: str = ""):
    """명령 큐에 추가."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO commands (target, command, args, created_at) VALUES (?, ?, ?, ?)",
        (target, command, args, time.time()))
    conn.commit()


def pop_command(target: str) -> dict:
    """target에 대한 미처리 명령 1개 가져오기 (FIFO). 없으면 빈 dict."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM commands WHERE target=? AND processed=0 ORDER BY id LIMIT 1",
        (target,)).fetchone()
    if not row:
        return {}
    conn.execute("UPDATE commands SET processed=1 WHERE id=?", (row["id"],))
    conn.commit()
    return dict(row)


def pop_commands(target: str) -> list:
    """target에 대한 미처리 명령 전부 가져오기."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM commands WHERE target=? AND processed=0 ORDER BY id",
        (target,)).fetchall()
    if rows:
        ids = [r["id"] for r in rows]
        conn.execute(f"UPDATE commands SET processed=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
    return [dict(r) for r in rows]


def has_pending_command(target: str, command: str = "") -> bool:
    """미처리 명령이 있는지 확인."""
    conn = _get_conn()
    if command:
        row = conn.execute(
            "SELECT 1 FROM commands WHERE target=? AND command=? AND processed=0 LIMIT 1",
            (target, command)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM commands WHERE target=? AND processed=0 LIMIT 1",
            (target,)).fetchone()
    return row is not None


# --- relay 설정 ---

def set_relay(bot_id: str, chat_id: str, enabled: bool = True):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO relay_config (bot_id, chat_id, enabled) VALUES (?, ?, ?)",
        (bot_id, chat_id, 1 if enabled else 0))
    conn.commit()


def is_relay_enabled(bot_id: str, chat_id: str) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT enabled FROM relay_config WHERE bot_id=? AND chat_id=?",
        (bot_id, chat_id)).fetchone()
    return bool(row and row["enabled"])


# --- 폴링 offset ---

def set_offset(bot_id: str, offset: int):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO poll_offsets (bot_id, offset_val, updated_at) VALUES (?, ?, ?)",
        (bot_id, offset, time.time()))
    conn.commit()


def get_offset(bot_id: str) -> int:
    conn = _get_conn()
    row = conn.execute("SELECT offset_val FROM poll_offsets WHERE bot_id=?", (bot_id,)).fetchone()
    return row["offset_val"] if row else 0


# --- TeleClaw 전역 상태 ---

def set_state(key: str, value: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO teleclaw_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, time.time()))
    conn.commit()


def get_state(key: str, default: str = "") -> str:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM teleclaw_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# --- 일시정지 상태 ---

def is_paused(name: str) -> bool:
    """세션이 일시정지 상태인지."""
    s = get_session(name)
    return s.get("status") == "paused"


def set_paused(name: str, paused: bool = True):
    if paused:
        set_session(name, status="paused")
    else:
        s = get_session(name)
        if s.get("status") == "paused":
            set_session(name, status="idle")


# --- 정리 ---

def cleanup_old_commands(max_age_hours: int = 24):
    """오래된 처리 완료 명령 삭제."""
    conn = _get_conn()
    cutoff = time.time() - max_age_hours * 3600
    conn.execute("DELETE FROM commands WHERE processed=1 AND created_at<?", (cutoff,))
    conn.commit()
