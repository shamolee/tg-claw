#!/usr/bin/env python3
"""
TeleClaw 래퍼 — teleclaw 패키지를 감싸서 자동 재시작 + 데스루프 방지.

- 30초 미만 생존 = 코드 에러로 판정
- 지수 백오프: 3초 → 30초 → 5분 → 30분(최대)
- 텔레그램 알림: 비정상 종료 시 에러 로그 전송
- 백오프 대기 중 텔레그램 비상 명령어 수신
"""

import os
import sys
import time
import json
import subprocess
import urllib.request

# Windows cp949 인코딩 문제 방지
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PYTHON = sys.executable
from .config import (
    PROJECTS, CHAT_ID, ALLOWED_USERS, _TELECLAW_DIR,
    WRAPPER_LOG_FILE as LOG_FILE, LOG_FILE as SV_LOG_FILE,
    WRAPPER_LOCK_FILE as LOCK_FILE, STDERR_LOG_FILE,
)
from .storage import ensure_storage
from .messages import msg
BOT_TOKENS = [p["bot_token"] for p in PROJECTS.values()]
BOT_TOKEN = BOT_TOKENS[0] if BOT_TOKENS else ""

MIN_ALIVE_SEC = 30
BACKOFF_BASE = 3
BACKOFF_MAX = 1800  # 30분
EXIT_ALREADY_RUNNING = 42


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 200:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-200:])
    except Exception:
        pass


def tg_send(text: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": CHAT_ID,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"텔레그램 알림 실패: {e}")


def tg_get_updates(offset: int, timeout: int = 5, bot_token: str = "") -> tuple[list, int]:
    """텔레그램 메시지 폴링. (messages, new_offset) 반환."""
    if not bot_token:
        bot_token = BOT_TOKEN
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        data = json.dumps({
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout + 10)
        result = json.loads(resp.read()).get("result", [])
        messages = []
        new_offset = offset
        for u in result:
            new_offset = u["update_id"] + 1
            msg = u.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id not in ALLOWED_USERS:
                continue
            text = msg.get("text", "")
            if text:
                messages.append(text)
        return messages, new_offset
    except Exception:
        return [], offset


def tg_flush(offset: int, bot_token: str = "") -> int:
    """기존 pending 메시지를 모두 소비하고 최신 offset 반환."""
    msgs, new_offset = tg_get_updates(offset, timeout=0, bot_token=bot_token)
    while msgs:
        msgs, new_offset = tg_get_updates(new_offset, timeout=0, bot_token=bot_token)
    return new_offset


def handle_emergency_command(text: str, fail_count: int, wait: int, start_time: float) -> str | None:
    """비상 명령어 처리. 반환값: 'restart' = 즉시 재시작, 'kill' = 래퍼 종료, None = 계속 대기."""
    cmd = text.strip().lower()

    if cmd in ("/log", "/logs", "로그"):
        lines = []
        for path, label in [(LOG_FILE, "wrapper"), (SV_LOG_FILE, "teleclaw")]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tail = f.readlines()[-10:]
                lines.append(f"📋 {label}:")
                lines.extend(l.rstrip() for l in tail)
            except Exception:
                lines.append(f"📋 {label}: 읽기 실패")
            lines.append("")
        tg_send("\n".join(lines))
        return None

    if cmd in ("/status", "상태"):
        uptime = int(time.time() - start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        tg_send(msg("daemon_emergency_status", h=h, m=m, fails=fail_count, wait=wait))
        return None

    if cmd in ("/restart", "재시작", "/force"):
        tg_send(msg("daemon_restarting"))
        return "restart"

    if cmd in ("/kill", "종료"):
        tg_send(msg("daemon_killed"))
        return "kill"

    if cmd in ("/help", "도움"):
        tg_send(msg("daemon_help"))
        return None

    # /ask — Claude CLI로 임시 질문
    if text.strip().lower().startswith("/ask "):
        question = text.strip()[5:].strip()
        if not question:
            tg_send(msg("daemon_ask_usage"))
            return None
        tg_send(msg("daemon_ask_processing"))
        try:
            r = subprocess.run(
                ["claude", "-p", question, "--output-format", "text"],
                capture_output=True, text=True, timeout=120,
                cwd=os.path.dirname(__file__),
            )
            answer = r.stdout.strip()
            if answer:
                # 4096자 제한
                if len(answer) > 3900:
                    answer = answer[:3900] + "\n... (잘림)"
                tg_send(msg("daemon_ask_response", answer=answer))
            elif r.stderr.strip():
                tg_send(msg("daemon_ask_error", error=r.stderr[:1000]))
            else:
                tg_send(msg("daemon_ask_empty"))
        except subprocess.TimeoutExpired:
            tg_send(msg("daemon_ask_timeout"))
        except Exception as e:
            tg_send(msg("daemon_ask_fail", error=e))
        return None

    return None


_poll_offsets = {token: 0 for token in BOT_TOKENS}  # 봇별 폴링 offset

def wait_with_polling(wait_sec: int, fail_count: int, start_time: float) -> str | None:
    """백오프 대기 중 텔레그램 폴링 (3개 봇 순차). 'restart'/'kill' 반환 시 즉시 탈출."""
    global _poll_offsets
    deadline = time.time() + wait_sec
    log(f"비상 폴링 시작 (대기 {wait_sec}초, {len(BOT_TOKENS)}개 봇)")

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        if remaining <= 0:
            break
        poll_timeout = min(remaining, 3)  # 봇당 3초 (3개 × 3초 ≈ 9초/라운드)
        for token in BOT_TOKENS:
            messages, _poll_offsets[token] = tg_get_updates(_poll_offsets[token], timeout=poll_timeout, bot_token=token)
            for text in messages:
                log(f"비상 명령 수신: {text}")
                result = handle_emergency_command(text, fail_count, wait_sec, start_time)
                if result:
                    return result
            if time.time() >= deadline:
                break

    log("비상 폴링 종료")
    return None


def _is_pid_alive(pid: int) -> bool:
    from .process_utils import is_pid_alive
    return is_pid_alive(pid)


def _acquire_lock() -> bool:
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            old_pid = data.get("pid", 0)
            if _is_pid_alive(old_pid):
                return False
        except Exception:
            pass
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "started": time.time()}, f)
        return True
    except (FileExistsError, OSError):
        return False


def _release_lock():
    # 다른 래퍼의 lock은 지우지 않음 (lock 획득 실패 후 종료 시에도 호출됨)
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            if json.load(f).get("pid") != os.getpid():
                return
        os.remove(LOCK_FILE)
    except Exception:
        pass


def _bind_children_to_self():
    """Windows: put this process in a kill-on-close job so children die with it."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    if not ok or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        log(f"job object 설정 실패: {ctypes.get_last_error()}")
        return
    global _JOB_HANDLE
    _JOB_HANDLE = job  # keep open for the process lifetime


_JOB_HANDLE = None


def main():
    migrated = ensure_storage()
    if not _acquire_lock():
        print(msg("daemon_already_running"))
        sys.exit(0)
    for note in migrated:
        log(f"storage 마이그레이션: {note}")

    _bind_children_to_self()
    fail_count = 0
    notified = False
    start_time = time.time()
    recent_restarts = []  # 정상 종료 포함 전체 재시작 시각 기록

    # 시작 시 기존 메시지 flush (모든 봇)
    for token in BOT_TOKENS:
        _poll_offsets[token] = tg_flush(0, bot_token=token)

    log("래퍼 시작")

    while True:
        log(f"teleclaw 시작 (fail_count={fail_count})")
        sv_start = time.time()

        # stderr만 파일로 캡처 (capture_output은 자식 프로세스 blocking 유발)
        # stdout은 콘솔일 때만 그대로 보여줌. 서비스로 실행 시 teleclaw.log와 중복이라 버림
        with open(STDERR_LOG_FILE, "w", encoding="utf-8") as sf:
            proc = subprocess.run(
                [PYTHON, "-m", "src"],
                cwd=_TELECLAW_DIR,
                stdout=None if sys.stdout.isatty() else subprocess.DEVNULL,
                stderr=sf,
            )

        elapsed = time.time() - sv_start
        exit_code = proc.returncode
        log(f"teleclaw 종료 (exit_code={exit_code}, 생존={elapsed:.0f}초)")

        # 짧은 시간 반복 재시작 경고 (10분 내 5회 이상)
        now = time.time()
        recent_restarts.append(now)
        recent_restarts = [t for t in recent_restarts if now - t < 600]
        if len(recent_restarts) >= 5:
            tg_send(msg("daemon_frequent_restart", count=len(recent_restarts), elapsed=elapsed, code=exit_code))
            log(f"잦은 재시작 경고: {len(recent_restarts)}회/10분")

        # 비정상 종료 시 stderr 기록
        if exit_code != 0:
            try:
                with open(STDERR_LOG_FILE, "r", encoding="utf-8", errors="replace") as sf:
                    stderr_content = sf.read().strip()
                if stderr_content:
                    stderr_tail = stderr_content[-500:]
                    log(f"teleclaw stderr: {stderr_tail}")
                    if elapsed < MIN_ALIVE_SEC:
                        tg_send(msg("daemon_crash_stderr", stderr=stderr_tail[:1000]))
            except Exception:
                pass

        # 기존 인스턴스 실행 중 → 60초 폴링 대기
        if exit_code == EXIT_ALREADY_RUNNING:
            log("기존 TeleClaw 실행 중, 60초 대기")
            result = wait_with_polling(60, fail_count, start_time)
            if result == "kill":
                return
            continue

        if elapsed >= MIN_ALIVE_SEC:
            fail_count = 0
            notified = False
            wait = BACKOFF_BASE
        else:
            fail_count += 1
            wait = min(BACKOFF_BASE * (2 ** (fail_count - 1)), BACKOFF_MAX)

            # 첫 실패 + 5/10/20/50회마다 반복 알림
            should_notify = (
                not notified
                or fail_count in (5, 10, 20, 50)
                or (fail_count > 50 and fail_count % 50 == 0)
            )
            if should_notify:
                tg_send(msg("daemon_crash", elapsed=elapsed, code=exit_code, fails=fail_count, wait=wait))
                notified = True
                log("텔레그램 알림 전송")

            log(f"빠른 종료 감지 (연속 {fail_count}회), {wait}초 대기")

        # 대기 중 텔레그램 폴링 (비상 명령 수신)
        result = wait_with_polling(wait, fail_count, start_time)
        if result == "kill":
            return
        if result == "restart":
            fail_count = 0
            notified = False


if __name__ == "__main__":
    try:
        main()
    finally:
        _release_lock()
