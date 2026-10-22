"""Project settings, paths and constants, loaded from config.yaml"""

import os
import sys

# --- Load config.yaml ---

_TG_CLAW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# config.yaml lookup: TG_CLAW_CONFIG env var → package parent dir → home dir
# (TELECLAW_CONFIG and ~/.teleclaw are still accepted from before the rename)
_CONFIG_PATH = ""
_env_config = os.environ.get("TG_CLAW_CONFIG") or os.environ.get("TELECLAW_CONFIG", "")
for _candidate in [
    _env_config,
    os.path.join(_TG_CLAW_DIR, "config.yaml"),
    os.path.join(os.path.expanduser("~"), ".tg-claw", "config.yaml"),
    os.path.join(os.path.expanduser("~"), ".teleclaw", "config.yaml"),
]:
    if _candidate and os.path.exists(_candidate):
        _CONFIG_PATH = _candidate
        break
if not _CONFIG_PATH:
    _CONFIG_PATH = os.path.join(_TG_CLAW_DIR, "config.yaml")  # fallback (used for the "not found" warning)


def _scalar(val: str) -> str:
    """Fallback-parser value: unquote "x"/'x' and drop a trailing # comment."""
    val = val.strip()
    if val[:1] in ('"', "'"):
        end = val.find(val[0], 1)
        return val[1:end] if end > 0 else val[1:]
    return val.split(" #", 1)[0].strip()


def _load_yaml(path: str) -> dict:
    """Load config.yaml. Uses PyYAML if available, otherwise a simple fallback parser."""
    if not os.path.exists(path):
        print(f"[config] config.yaml not found: {path}", file=sys.stderr)
        print(f"[config] Run: cp config.example.yaml config.yaml", file=sys.stderr)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Prefer PyYAML
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except ImportError:
        pass
    # Fallback: simple parser (up to 2 levels of nesting)
    result = {}
    current_section = None
    current_item = None
    for line in content.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = _scalar(val)
            if val:
                result[key.strip()] = val
            else:
                result[key.strip()] = {}
                current_section = key.strip()
                current_item = None
        elif indent == 2 and current_section and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = _scalar(val)
            if val:
                if current_item and isinstance(result[current_section].get(current_item), dict):
                    result[current_section][current_item][key.strip()] = val
                else:
                    result[current_section][key.strip()] = val
            else:
                current_item = key.strip()
                result[current_section][current_item] = {}
        elif indent == 4 and current_section and current_item and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = _scalar(val)
            result[current_section][current_item][key.strip()] = val
    return result


_cfg = _load_yaml(_CONFIG_PATH)

# --- Project settings ---

CHAT_ID = _cfg.get("chat_id", "")
if not CHAT_ID:
    print("[config] WARNING: chat_id is empty — no users will be allowed", file=sys.stderr)
LANG = _cfg.get("lang", "en")

# Notification icons (customizable)
_icons = _cfg.get("icons", {})
ICON_THINKING = _icons.get("thinking", "💭...") if isinstance(_icons, dict) else "💭..."
ICON_DONE = _icons.get("done", "✓") if isinstance(_icons, dict) else "✓"

# Output level: minimal / normal / verbose
OUTPUT_LEVEL = _cfg.get("output_level", "normal")

# Allowed user IDs (if empty, only CHAT_ID is allowed)
_allowed_raw = _cfg.get("allowed_users", "")
ALLOWED_USERS = set(uid.strip() for uid in _allowed_raw.split(",") if uid.strip()) if _allowed_raw else set()
if CHAT_ID:
    ALLOWED_USERS.add(CHAT_ID)

PROJECTS = {}
for name, info in _cfg.get("projects", {}).items():
    if isinstance(info, dict) and "bot_token" in info:
        PROJECTS[name] = {
            "cwd": info.get("cwd", ""),
            "bot_token": info["bot_token"],
            "bot_id": info.get("bot_id", info["bot_token"].split(":")[0]),
            "mcp_json": os.path.join(info.get("cwd", ""), ".mcp.json"),
        }

# --- Paths ---

# Directories are relative to config.yaml (supports pip installs)
TG_CLAW_DIR = os.path.dirname(_CONFIG_PATH) if _CONFIG_PATH else _TG_CLAW_DIR

# All runtime files live under storage_dir (relative paths are resolved against config.yaml)
STORAGE_DIR = os.path.join(TG_CLAW_DIR, os.path.expanduser(_cfg.get("storage_dir", "storage")))
LOGS_DIR = os.path.join(STORAGE_DIR, "logs")        # logs only
LOCKS_DIR = os.path.join(STORAGE_DIR, "locks")      # single-instance locks
STATE_DIR = os.path.join(STORAGE_DIR, "state")      # state that must survive restarts
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")  # images/files received via Telegram

LOG_FILE = os.path.join(LOGS_DIR, "tg_claw.log")
WRAPPER_LOG_FILE = os.path.join(LOGS_DIR, "wrapper.log")
STDERR_LOG_FILE = os.path.join(LOGS_DIR, "tg_claw_stderr.log")
LOCK_FILE = os.path.join(LOCKS_DIR, "tg_claw.lock")
WRAPPER_LOCK_FILE = os.path.join(LOCKS_DIR, "wrapper.lock")
STATUS_FILE = os.path.join(STATE_DIR, "tg_claw_status.json")
SESSION_IDS_FILE = os.path.join(STATE_DIR, "session_ids.json")
DB_FILE = os.path.join(STATE_DIR, "tg_claw.db")
CLAUDE_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")

# Upload retention in days. 0 = never delete
UPLOAD_RETENTION_DAYS = int(_cfg.get("upload_retention_days", 7))

# Log retention
LOG_RETENTION_DAYS = int(_cfg.get("log_retention_days", 7))          # daily tg_claw_<date>.log archives; 0 = keep forever
LOG_MAX_LINES = int(_cfg.get("log_max_lines", 500))                  # tg_claw.log keeps this many recent lines
WRAPPER_LOG_MAX_LINES = int(_cfg.get("wrapper_log_max_lines", 200))  # wrapper.log keeps this many recent lines
TASK_LOG_MAX_MB = int(_cfg.get("task_log_max_mb", 5))                # task_out.log rotation (Windows launcher); 0 = never

# Windows scheduled task name (service.py, win-scripts/*.ps1)
SERVICE_NAME = _cfg.get("service_name", "tg-claw")

# --- Constants ---

HEALTH_CHECK_INTERVAL = 120  # 2 min
STUCK_THRESHOLD = 1800  # 30 min
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW = 1800  # 30 min
AUTO_RESUME_ENABLED = True

AUTO_RESUME_MODE = "resume"
# Message keys (resources/lang) of the prompt sent to Claude after a restart, per mode
AUTO_RESUME_PROMPTS = {
    "resume": "auto_resume_prompt_resume",
    "check": "auto_resume_prompt_check",
    "none": None,
}
