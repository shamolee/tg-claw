"""TeleClaw 명령어 핸들러"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .config import (
    LOGS_DIR, LOG_FILE,
)
from .logging_utils import log
from .messages import msg
from . import state_db as db


def _spawn(teleclaw, coro):
    """Schedule a coroutine from either the event loop or a worker thread.

    Command handlers run via asyncio.to_thread, where asyncio.create_task
    has no running loop, so hand the coroutine to TeleClaw's main loop.
    """
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        return asyncio.run_coroutine_threadsafe(coro, teleclaw._loop)


async def _do_interrupt(state, name: str, channel):
    """세션의 현재 작업을 중단한다."""
    try:
        await state.client.interrupt()
        log(f"{name}: interrupt 완료")
        channel.send_sync(msg("interrupted", name=name))
    except Exception as e:
        log(f"{name}: interrupt 실패: {e}")
        channel.send_sync(msg("interrupt_fail", name=name, error=e))


def _find_session_by_token(sessions: dict, bot_token: str) -> str | None:
    """bot_token에 해당하는 세션 이름을 반환한다. 없으면 None."""
    for name, state in sessions.items():
        if state.config["bot_token"] == bot_token:
            return name
    return None


def _get_usage(http_client) -> str:
    """Anthropic OAuth Usage API로 토큰 사용량 조회 (60초 캐시).

    /usage 명령 수신 시 호출. http_client는 httpx.Client 인스턴스.
    """
    import os
    cache_path = Path(os.environ.get("TEMP", "/tmp")) / "claude-sv-usage.json"
    # 캐시 확인
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if time.time() - cached.get("_ts", 0) < 60:
                return cached["_text"]
    except Exception:
        pass

    # OAuth 토큰 읽기
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        creds = json.loads(cred_path.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
    except Exception as e:
        return msg("usage_fail_cred", error=e)

    # API 호출
    try:
        r = http_client.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return msg("usage_fail_http", code=r.status_code)
        data = r.json()
    except Exception as e:
        return msg("usage_fail", error=e)

    from .usage_fmt import usage_bar as _bar, reset_str as _reset_str

    # API가 키는 주되 값을 null로 줄 수 있음 (예: 구독 유형에 따라) -> `or {}` 필요
    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    sonnet = data.get("seven_day_sonnet") or {}
    opus = data.get("seven_day_opus") or {}

    lines = [msg("usage_header")]
    lines.append("")

    if five.get("utilization") is not None:
        pct = five["utilization"]
        lines.append(f"5h   {_bar(pct)} {_reset_str(five)}")
    if seven.get("utilization") is not None:
        pct = seven["utilization"]
        lines.append(f"7d   {_bar(pct)} {_reset_str(seven)}")
    if sonnet and sonnet.get("utilization") is not None:
        pct = sonnet["utilization"]
        lines.append(f"Son  {_bar(pct)} {_reset_str(sonnet)}")
    if opus and opus.get("utilization") is not None:
        pct = opus["utilization"]
        lines.append(f"Opus {_bar(pct)} {_reset_str(opus)}")

    if len(lines) == 2:  # header + blank only: API returned no usage buckets
        lines.append("(no usage data returned by the API)")

    text = "\n".join(lines)
    # 캐시 저장
    try:
        cache_path.write_text(json.dumps({"_ts": time.time(), "_text": text}), encoding="utf-8")
    except Exception:
        pass
    return text


def handle_command(teleclaw, text: str, bot_token: str, channel) -> bool:
    """텔레그램 메시지가 /로 시작할 때 호출. 처리했으면 True 반환.

    지원 명령: /status, /esc, /pause, /restart, /reset, /log, /usage, /sys, /ask, /help
    """
    text = text.strip()
    if not text.startswith("/"):
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/stop", "/shutdown", "/kill"):
        channel.send_sync(msg("shutdown_not_allowed"))
        return True

    if cmd in ("/status", "/s"):
        now = time.time()
        uptime = int(now - teleclaw._start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        lines = [msg("status_header", h=h, m=m)]
        for name, state in teleclaw.sessions.items():
            status = teleclaw._assess_health(state)
            lines.append(
                f"  {name}: {status} | Q={state.query_count} E={state.error_count} R={state.restart_count}"
            )
        channel.send_sync("\n".join(lines))
        return True

    if cmd in ("/restart", "/r"):
        no_resume = False
        if arg:
            parts = arg.split()
            name = parts[0]
            no_resume = "noresume" in parts[1:]
        else:
            name = _find_session_by_token(teleclaw.sessions, bot_token)
        if name and name.lower() == "teleclaw":
            channel.send_sync(msg("sv_restart_requested"))
            db.push_command("teleclaw", "restart", "force")
            return True
        if name and name in teleclaw.sessions:
            db.set_paused(name, False)
            _spawn(teleclaw, teleclaw._restart_session(teleclaw.sessions[name], "/restart 명령", mode="resume", force=True, no_resume=no_resume))
            tag = " (noresume)" if no_resume else ""
            channel.send_sync(msg("restart_requested", name=name, tag=tag))
        elif name:
            channel.send_sync(msg("session_not_found", name=name, available=", ".join(teleclaw.sessions) + ", teleclaw"))
        return True


    if cmd in ("/pause", "/p"):
        if arg:
            name = arg
        else:
            name = _find_session_by_token(teleclaw.sessions, bot_token)
        if name and name in teleclaw.sessions:
            if db.is_paused(name):
                channel.send_sync(msg("already_paused", name=name))
                return True
            db.set_paused(name, True)
            # 세션 disconnect
            state = teleclaw.sessions[name]
            old_client = state.client
            state.client = None
            if old_client:
                _spawn(teleclaw, teleclaw._safe_disconnect(old_client, name))
            state.connected = False
            state.busy = False
            log(f"{name}: 일시정지됨 (/pause 명령)")
            channel.send_sync(msg("paused", name=name))
        elif name:
            channel.send_sync(msg("session_not_found", name=name, available=", ".join(teleclaw.sessions)))
        return True

    if cmd in ("/esc", "/interrupt"):
        if arg:
            name = arg
        else:
            name = _find_session_by_token(teleclaw.sessions, bot_token)
        if name and name in teleclaw.sessions:
            state = teleclaw.sessions[name]
            if state.connected and state.client:
                _spawn(teleclaw, _do_interrupt(state, name, channel))
            else:
                channel.send_sync(msg("session_not_connected", name=name))
        elif name:
            channel.send_sync(msg("session_not_found", name=name, available=", ".join(teleclaw.sessions)))
        return True

    if cmd == "/reset":
        if arg:
            name = arg
        else:
            name = _find_session_by_token(teleclaw.sessions, bot_token)
        if name and name in teleclaw.sessions:
            db.set_paused(name, False)
            _spawn(teleclaw, teleclaw._restart_session(teleclaw.sessions[name], "/reset 명령 (컨텍스트 초기화)", mode="reset", force=True))
            channel.send_sync(msg("reset_requested", name=name))
        elif name:
            channel.send_sync(msg("session_not_found", name=name, available=", ".join(teleclaw.sessions)))
        return True

    if cmd in ("/log", "/l"):
        n = int(arg) if arg and arg.isdigit() else 20
        n = min(n, 50)  # 텔레그램 메시지 길이 제한
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = lines[-n:] if len(lines) >= n else lines
            text = msg("log_header", n=len(tail)) + "\n" + "".join(tail)
            if len(text) > 4000:
                text = text[-4000:]
            channel.send_sync(text)
        except Exception as e:
            channel.send_sync(msg("log_read_fail", error=e))
        return True

    if cmd in ("/usage", "/u"):
        usage_text = _get_usage(teleclaw._http)
        channel.send_sync(usage_text)
        return True

    if cmd == "/ctx":
        # 각 세션의 마지막 usage 로그에서 컨텍스트 사용량 추정
        lines = [msg("ctx_header")]
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                log_lines = f.readlines()
        except Exception:
            log_lines = []
        for name, state in teleclaw.sessions.items():
            usage_data = None
            for line in reversed(log_lines):
                if f"{name}: [usage]" in line:
                    try:
                        idx = line.index("{")
                        usage_data = json.loads(line[idx:].strip().replace("'", '"'))
                    except Exception:
                        pass
                    break
            if usage_data:
                inp = usage_data.get("input_tokens", 0)
                cache_read = usage_data.get("cache_read_input_tokens", 0)
                cache_create = usage_data.get("cache_creation_input_tokens", 0)
                out = usage_data.get("output_tokens", 0)
                lines.append(
                    f"  {name}: in={inp:,} cache_r={cache_read:,} cache_w={cache_create:,} out={out:,}"
                )
            else:
                lines.append(msg("ctx_no_data", name=name))
        lines.append(msg("ctx_note"))
        channel.send_sync("\n".join(lines))
        return True

    if cmd == "/sys":
        proc_limit = int(arg) if arg and arg.isdigit() else 10
        proc_limit = min(proc_limit, 30)
        lines = [msg("sys_header")]
        try:
            import psutil
            # CPU
            cpu_percent = psutil.cpu_percent(interval=0.5)
            cpu_count = psutil.cpu_count()
            lines.append("\n" + msg("sys_cpu", pct=cpu_percent, cores=cpu_count))
            # Memory
            mem = psutil.virtual_memory()
            used_gb = mem.used / (1024**3)
            total_gb = mem.total / (1024**3)
            lines.append(msg("sys_mem", used=used_gb, total=total_gb, pct=mem.percent))
            # Disk
            disk = psutil.disk_usage("/" if sys.platform != "win32" else os.environ.get("SystemDrive", "C:/"))
            disk_used = disk.used / (1024**3)
            disk_total = disk.total / (1024**3)
            lines.append(msg("sys_disk", used=disk_used, total=disk_total, pct=disk.percent))
            # Claude processes
            lines.append(msg("sys_procs_header", limit=proc_limit))
            claude_procs = []
            for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "cmdline"]):
                try:
                    pname = proc.info["name"] or ""
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    mem_mb = (proc.info["memory_info"].rss / (1024**2)) if proc.info["memory_info"] else 0
                    if "claude" in pname.lower():
                        claude_procs.append((pname, proc.info["pid"], mem_mb, proc.info["cpu_percent"] or 0))
                    elif "node" in pname.lower() and "mcp" in cmdline.lower():
                        claude_procs.append((pname, proc.info["pid"], mem_mb, proc.info["cpu_percent"] or 0))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if claude_procs:
                for pname, pid, mem_mb, cpu_p in sorted(claude_procs, key=lambda x: -x[2])[:proc_limit]:
                    lines.append(f"  {pname}(PID:{pid}) {mem_mb:.0f}MB CPU:{cpu_p:.0f}%")
            else:
                lines.append(msg("sys_no_procs"))
            # TeleClaw self
            sv_proc = psutil.Process()
            sv_mem = sv_proc.memory_info().rss / (1024**2)
            lines.append(msg("sys_teleclaw", pid=sv_proc.pid, mem=sv_mem))
        except ImportError:
            lines.append(msg("sys_no_psutil"))
        except Exception as e:
            lines.append(msg("error_generic", error=e))
        channel.send_sync("\n".join(lines))
        return True

    if cmd == "/ask":
        if not arg:
            channel.send_sync(msg("ask_usage"))
            return True
        _spawn(teleclaw, teleclaw._handle_ask(arg, bot_token))
        return True

    if cmd in ("/help", "/h"):
        names = ", ".join(teleclaw.sessions.keys())
        channel.send_sync(msg("help_text", names=names))
        return True

    if cmd == "/mode":
        valid = ("minimal", "normal")
        if arg and arg in valid:
            teleclaw.output_level = arg
            channel.send_sync(msg("mode_set", mode=arg))
        else:
            channel.send_sync(msg("mode_current", mode=teleclaw.output_level, options="/".join(valid)))
        return True

    return False
