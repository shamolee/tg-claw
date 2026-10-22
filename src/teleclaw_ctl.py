#!/usr/bin/env python3
"""TeleClaw CLI — command tool usable from both Telegram and the console.

Usage:
  python -m src.teleclaw_ctl s              # status
  python -m src.teleclaw_ctl r [name]       # restart
  python -m src.teleclaw_ctl x [name]       # reset
  python -m src.teleclaw_ctl p <name>       # pause
  python -m src.teleclaw_ctl l [N]          # logs
  python -m src.teleclaw_ctl u              # usage
  python -m src.teleclaw_ctl h              # help
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .messages import msg
from .config import PROJECTS
from . import config as _config
from .storage import ensure_storage

STATUS_FILE = Path(_config.STATUS_FILE)
LOG_FILE = Path(_config.LOG_FILE)
SESSION_IDS_FILE = Path(_config.SESSION_IDS_FILE)
from . import state_db as db

SESSION_NAMES = list(PROJECTS.keys())
CWD_MAP = {}
for name, info in PROJECTS.items():
    cwd = info["cwd"]
    CWD_MAP[cwd] = name
    CWD_MAP[cwd.replace("/", "\\")] = name


def _guess_session():
    cwd = os.getcwd().replace("\\", "/").rstrip("/")
    for path, name in CWD_MAP.items():
        if cwd.replace("\\", "/").rstrip("/") == path.replace("\\", "/").rstrip("/"):
            return name
    return None


def _resolve_name(arg):
    if not arg:
        name = _guess_session()
        if not name:
            print(msg("tcctl_specify_session", names=", ".join(SESSION_NAMES)))
            return None
        return name
    # case-insensitive match
    for n in SESSION_NAMES + ["teleclaw"]:
        if n.lower() == arg.lower():
            return n
    print(msg("tcctl_session_not_found", name=arg, available=", ".join(SESSION_NAMES) + ", teleclaw"))
    return None


def _get_all_processes():
    """List claude.exe plus all teleclaw-related python processes.

    Returns:
        {"sessions": {name: {pid, mem_mb}},
         "manual": [{pid, mem_mb}],
         "infra": [{pid, mem_mb, label}]}
    """
    import re as _re
    # Find claude + teleclaw-related processes (cross-platform)
    try:
        import psutil
        lines = []
        for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
            try:
                info = proc.info
                pname = (info["name"] or "").lower()
                cmdline = " ".join(info["cmdline"] or [])
                mem = info["memory_info"].rss if info["memory_info"] else 0
                if "claude" in pname or ("python" in pname and "teleclaw" in cmdline):
                    lines.append(f"{info['pid']}|{info['name']}|{mem}|{cmdline[:200]}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        # Without psutil, fall back to PowerShell (Windows only)
        if sys.platform == "win32":
            ps_cmd = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -eq 'claude.exe' -or "
                "($_.Name -eq 'python.exe' -and $_.CommandLine -match 'teleclaw') } | "
                "ForEach-Object { "
                "$mem = (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).WorkingSet64; "
                "Write-Output ('{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.Name, $mem, "
                "$_.CommandLine.Substring(0, [math]::Min(200, $_.CommandLine.Length))) }"
            )
            try:
                result = subprocess.run(
                    ["powershell", "-c", ps_cmd],
                    capture_output=True, text=True, timeout=15
                )
                lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            except Exception:
                return {"sessions": {}, "manual": [], "infra": []}
        else:
            print(msg("tcctl_need_psutil"))
            return {"sessions": {}, "manual": [], "infra": []}

    # Map session ID → name from session_ids.json
    sid_to_name = {}
    if SESSION_IDS_FILE.exists():
        try:
            ids = json.loads(SESSION_IDS_FILE.read_text(encoding="utf-8"))
            for name, info in ids.items():
                sid = info.get("session_id", "")
                if sid:
                    sid_to_name[sid] = name
        except Exception:
            pass

    sessions = {}
    manual = []
    infra = []

    for line in lines:
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        pid = int(parts[0])
        proc_name = parts[1]
        mem_mb = round(int(parts[2] or 0) / 1024 / 1024)
        cmdline = parts[3]

        # python teleclaw processes
        if "python" in proc_name.lower():
            if "teleclaw_daemon" in cmdline or "teleclaw-wrapper" in cmdline:
                infra.append({"pid": pid, "mem_mb": mem_mb, "label": "daemon"})
            elif "teleclaw" in cmdline:
                infra.append({"pid": pid, "mem_mb": mem_mb, "label": "teleclaw"})
            continue

        # claude.exe — map to sessions
        matched_name = None
        if "--resume" in cmdline:
            for sid, sname in sid_to_name.items():
                if sid in cmdline:
                    matched_name = sname
                    break
        if not matched_name and "TELEGRAM_BOT_NAME" in cmdline:
            m = _re.search(r'TELEGRAM_BOT_NAME[\\"]* *[,:] *[\\"]*([\w]+)', cmdline)
            if m:
                matched_name = m.group(1)

        entry = {"pid": pid, "mem_mb": mem_mb}
        if matched_name:
            sessions[matched_name] = entry
        else:
            manual.append(entry)

    return {"sessions": sessions, "manual": manual, "infra": infra}


def cmd_sys():
    """System CPU and memory status."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        cores = psutil.cpu_count()
        mem = psutil.virtual_memory()
        print(msg("sys_cpu", pct=cpu, cores=cores))
        print(msg("sys_mem", used=mem.used / (1024**3), total=mem.total / (1024**3), pct=mem.percent))
    except ImportError:
        print(msg("tcctl_need_psutil"))
    except Exception as e:
        print(msg("tcctl_error", name="sys", error=str(e)))


def cmd_ps():
    """List related processes and their state."""
    all_procs = _get_all_processes()
    tc_data = {}

    # TeleClaw status
    if STATUS_FILE.exists():
        tc_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        ts = tc_data.get("ts", 0)
        saved_uptime = tc_data.get("uptime", 0)
        if ts and saved_uptime:
            uptime = int(saved_uptime + (time.time() - ts))
        else:
            uptime = 0
        h, m = uptime // 3600, (uptime % 3600) // 60
        sv_pid = tc_data.get("pid", "?")
        print(msg("tcctl_sv_running", pid=sv_pid, h=h, m=m))
    else:
        print(msg("tcctl_sv_not_running"))

    # Infrastructure processes (wrapper, teleclaw python)
    for p in all_procs["infra"]:
        print(f"  {p['label']}: PID={p['pid']} {p['mem_mb']}MB")

    total_mb = sum(p["mem_mb"] for p in all_procs["infra"])

    # Session processes
    for name, sess in tc_data.get("sessions", {}).items():
        status = sess.get("status", "?")
        if db.is_paused(name):
            status = "PAUSED"
        q = sess.get("query_count", 0)
        e = sess.get("error_count", 0)
        r = sess.get("restart_count", 0)
        pi = all_procs["sessions"].get(name, {})
        pid_str = f"PID={pi['pid']}" if pi.get("pid") else "PID=?"
        mem_mb = pi.get("mem_mb", 0)
        mem_str = f"{mem_mb}MB" if mem_mb else "?MB"
        total_mb += mem_mb
        print(f"  {name}: {status} | {pid_str} {mem_str} | Q={q} E={e} R={r}")

    # Manual/unmapped sessions
    for i, p in enumerate(all_procs["manual"]):
        total_mb += p["mem_mb"]
        label = msg("tcctl_manual_n", n=i + 1) if len(all_procs["manual"]) > 1 else msg("tcctl_manual")
        print(f"  ({label}): PID={p['pid']} {p['mem_mb']}MB")

    print(msg("tcctl_total", mem=total_mb))


def cmd_restart(arg, mode="resume"):
    name = _resolve_name(arg)
    if not name:
        return
    parts = ["force"]
    if mode != "resume":
        parts.append(mode)
    db.push_command(name.lower() if name.lower() == "teleclaw" else name, "restart", ",".join(parts))
    if name.lower() == "teleclaw":
        print(msg("tcctl_restart_sv"))
    else:
        mode_str = f" ({mode})" if mode != "resume" else ""
        print(msg("tcctl_restart_session", name=name, mode=mode_str))



def cmd_pause(arg):
    """Pause a session — mark it in the DB + kill its process. TeleClaw won't restart it."""
    name = _resolve_name(arg)
    if not name or name.lower() == "teleclaw":
        print(msg("tcctl_specify_session_no_sv"))
        return
    db.set_paused(name, True)
    # Kill the process
    all_procs = _get_all_processes()
    pi = all_procs["sessions"].get(name)
    if pi and pi.get("pid"):
        try:
            from .process_utils import kill_pid
            kill_pid(pi["pid"])
            print(msg("tcctl_paused", name=name, pid=pi['pid']))
        except Exception:
            print(msg("tcctl_paused", name=name, pid="?"))
    else:
        print(msg("tcctl_paused_no_proc", name=name))


def cmd_log(arg):
    n = int(arg) if arg and arg.isdigit() else 20
    n = min(n, 50)
    if not LOG_FILE.exists():
        print(msg("tcctl_no_log"))
        return
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        print(line)


def cmd_usage():
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        creds = json.loads(cred_path.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
    except Exception as e:
        print(msg("tcctl_cred_fail", error=str(e)))
        return
    try:
        import httpx
        r = httpx.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(msg("tcctl_usage_fail_http", code=r.status_code))
            return
        data = r.json()
    except Exception as e:
        print(msg("tcctl_usage_fail", error=str(e)))
        return

    from datetime import datetime, timezone

    from .usage_fmt import usage_bar as _bar, reset_str as _reset_str

    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    if five.get("utilization") is not None:
        print(f"5h   {_bar(five['utilization'])} {_reset_str(five)}")
    if seven.get("utilization") is not None:
        print(f"7d   {_bar(seven['utilization'])} {_reset_str(seven)}")


# Derived from each cwd in config.py (D:/workspace/foo → D--workspace-foo)
PROJECT_DIRS = {
    name: info["cwd"].replace(":", "").replace("/", "-").replace("\\", "-")
    for name, info in PROJECTS.items()
}
SESSIONS_BASE = Path.home() / ".claude" / "projects"


def cmd_ctx():
    if not SESSION_IDS_FILE.exists():
        print(msg("tcctl_no_session_ids"))
        return
    ids = json.loads(SESSION_IDS_FILE.read_text(encoding="utf-8"))
    for name in SESSION_NAMES:
        info = ids.get(name)
        if not info:
            print(msg("tcctl_no_session", name=name))
            continue
        sid = info.get("session_id", "")
        proj_dir = PROJECT_DIRS.get(name)
        if not proj_dir or not sid:
            print(msg("tcctl_no_mapping", name=name))
            continue
        jsonl = SESSIONS_BASE / proj_dir / f"{sid}.jsonl"
        if not jsonl.exists():
            print(msg("tcctl_no_transcript", name=name))
            continue
        try:
            with open(jsonl, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 100000))
                tail = f.read().decode("utf-8", errors="ignore")
            last_usage = None
            last_model = ""
            for line in tail.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if entry.get("type") == "assistant":
                    emsg = entry.get("message", {})
                    u = emsg.get("usage")
                    if u:
                        last_usage = u
                    m = emsg.get("model", "")
                    if m:
                        last_model = m
            if not last_usage:
                print(msg("tcctl_no_usage", name=name))
                continue
            inp = last_usage.get("input_tokens", 0)
            cache_create = last_usage.get("cache_creation_input_tokens", 0)
            cache_read = last_usage.get("cache_read_input_tokens", 0)
            total = inp + cache_create + cache_read
            # Context size per model
            CTX_SIZES = {"opus": 1000000, "1m": 1000000, "sonnet": 200000, "haiku": 200000}
            ctx_size = next((v for k, v in CTX_SIZES.items() if k in last_model), 200000)
            pct = total / ctx_size * 100
            bar_filled = round(pct / 5)
            bar = "|" * bar_filled + "." * (20 - bar_filled)
            print(f"  {name}: {bar} {pct:.0f}% ({total:,}/{ctx_size:,})")
        except Exception as e:
            print(msg("tcctl_error", name=name, error=str(e)))


def cmd_help():
    print(msg("tcctl_help"))
    print()
    print(f"  {', '.join(SESSION_NAMES)}, teleclaw")


def main():
    ensure_storage()  # so db.init() doesn't create an empty DB first and block migration
    db.init()

    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1].lower().lstrip("/")
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    commands = {
        "sys": cmd_sys, "system": cmd_sys,
        "ps": cmd_ps, "s": cmd_ps, "status": cmd_ps,
        "r": lambda: cmd_restart(arg), "restart": lambda: cmd_restart(arg),
        "tc": lambda: cmd_restart("teleclaw"), "teleclaw": lambda: cmd_restart("teleclaw"),
        "reset": lambda: cmd_restart(arg, "reset"),
        "p": lambda: cmd_pause(arg), "pause": lambda: cmd_pause(arg),
        "c": cmd_ctx, "ctx": cmd_ctx,
        "l": lambda: cmd_log(arg), "log": lambda: cmd_log(arg),
        "u": cmd_usage, "usage": cmd_usage,
        "h": cmd_help, "help": cmd_help,
    }

    fn = commands.get(cmd)
    if fn:
        fn()
    else:
        print(msg("tcctl_unknown_cmd", cmd=cmd))
        cmd_help()


if __name__ == "__main__":
    main()
