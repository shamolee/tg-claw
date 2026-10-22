"""프로젝트 설정, 경로, 상수 — config.yaml에서 로드"""

import os
import sys

# --- config.yaml 로드 ---

_TELECLAW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# config.yaml 탐색: 환경변수 TELECLAW_CONFIG → 패키지 상위 → 홈 디렉토리
_CONFIG_PATH = ""
_env_config = os.environ.get("TELECLAW_CONFIG", "")
for _candidate in [
    _env_config,
    os.path.join(_TELECLAW_DIR, "config.yaml"),
    os.path.join(os.path.expanduser("~"), ".teleclaw", "config.yaml"),
]:
    if _candidate and os.path.exists(_candidate):
        _CONFIG_PATH = _candidate
        break
if not _CONFIG_PATH:
    _CONFIG_PATH = os.path.join(_TELECLAW_DIR, "config.yaml")  # 폴백 (파일 없음 경고용)


def _load_yaml(path: str) -> dict:
    """config.yaml 로드. PyYAML 우선, 없으면 간단 파서 폴백."""
    if not os.path.exists(path):
        print(f"[config] config.yaml not found: {path}", file=sys.stderr)
        print(f"[config] Run: cp config.example.yaml config.yaml", file=sys.stderr)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # PyYAML 우선
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except ImportError:
        pass
    # 폴백: 간단 파서 (중첩 2단계까지)
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
            val = val.strip().strip('"').strip("'")
            if val:
                result[key.strip()] = val
            else:
                result[key.strip()] = {}
                current_section = key.strip()
                current_item = None
        elif indent == 2 and current_section and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip().strip('"').strip("'")
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
            val = val.strip().strip('"').strip("'")
            result[current_section][current_item][key.strip()] = val
    return result


_cfg = _load_yaml(_CONFIG_PATH)

# --- 프로젝트 설정 ---

CHAT_ID = _cfg.get("chat_id", "")
if not CHAT_ID:
    print("[config] WARNING: chat_id is empty — no users will be allowed", file=sys.stderr)
LANG = _cfg.get("lang", "en")

# 알림 아이콘 (커스터마이즈 가능)
_icons = _cfg.get("icons", {})
ICON_THINKING = _icons.get("thinking", "💭...") if isinstance(_icons, dict) else "💭..."
ICON_DONE = _icons.get("done", "✓") if isinstance(_icons, dict) else "✓"

# 출력 레벨: minimal / normal / verbose
OUTPUT_LEVEL = _cfg.get("output_level", "normal")

# 허용된 사용자 ID 목록 (비어있으면 CHAT_ID만 허용)
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

# --- 경로 ---

# config.yaml 위치 기준으로 디렉토리 설정 (pip install 환경 대응)
TELECLAW_DIR = os.path.dirname(_CONFIG_PATH) if _CONFIG_PATH else _TELECLAW_DIR

# 런타임 파일은 모두 storage_dir 아래 (상대 경로는 config.yaml 위치 기준)
STORAGE_DIR = os.path.join(TELECLAW_DIR, os.path.expanduser(_cfg.get("storage_dir", "storage")))
LOGS_DIR = os.path.join(STORAGE_DIR, "logs")        # 로그만
LOCKS_DIR = os.path.join(STORAGE_DIR, "locks")      # 단일 인스턴스 lock
STATE_DIR = os.path.join(STORAGE_DIR, "state")      # 재시작 후에도 유지해야 하는 상태
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")  # 텔레그램으로 받은 이미지/파일

LOG_FILE = os.path.join(LOGS_DIR, "teleclaw.log")
WRAPPER_LOG_FILE = os.path.join(LOGS_DIR, "wrapper.log")
STDERR_LOG_FILE = os.path.join(LOGS_DIR, "teleclaw_stderr.log")
LOCK_FILE = os.path.join(LOCKS_DIR, "teleclaw.lock")
WRAPPER_LOCK_FILE = os.path.join(LOCKS_DIR, "wrapper.lock")
STATUS_FILE = os.path.join(STATE_DIR, "teleclaw_status.json")
SESSION_IDS_FILE = os.path.join(STATE_DIR, "session_ids.json")
DB_FILE = os.path.join(STATE_DIR, "teleClaw.db")
CLAUDE_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")

# 업로드 보관 기간 (일). 0이면 삭제하지 않음
UPLOAD_RETENTION_DAYS = int(_cfg.get("upload_retention_days", 7))

# Windows 예약 작업 이름 (service.py, win-scripts/*.ps1)
SERVICE_NAME = _cfg.get("service_name", "TeleClaw")

# --- 상수 ---

HEALTH_CHECK_INTERVAL = 120  # 2분
STUCK_THRESHOLD = 1800  # 30분
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW = 1800  # 30분
AUTO_RESUME_ENABLED = True

AUTO_RESUME_MODE = "resume"
AUTO_RESUME_PROMPTS = {
    "resume": "[시스템 재시작됨] 직전에 수행하던 작업이 완료되지 않았다면 이어서 진행해줘. 완료되었다면 대기해줘.",
    "check": "[시스템 재시작됨] 직전에 수행하던 작업이 무엇이었는지 간단히 알려줘. 이어서 진행하지는 마.",
    "none": None,
}
