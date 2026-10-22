"""Cross-platform process utilities, replacing tasklist/taskkill/powershell."""

import os
import signal
import sys


def is_pid_alive(pid: int) -> bool:
    """Check whether a PID is running. Cross-platform."""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    # Without psutil, fall back to OS-specific checks
    if sys.platform == "win32":
        import subprocess
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5)
        return str(pid) in r.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def kill_pid(pid: int):
    """Force-kill a PID. Cross-platform."""
    try:
        import psutil
        p = psutil.Process(pid)
        p.kill()
        return
    except ImportError:
        pass
    except Exception:
        pass
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=5)
    else:
        os.kill(pid, signal.SIGKILL)


def find_processes(name_pattern: str):
    """Find processes by name pattern. Returns [(pid, name, cmdline), ...]."""
    try:
        import psutil
        results = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = p.info
                pname = info["name"] or ""
                cmdline = " ".join(info["cmdline"] or [])
                if name_pattern.lower() in pname.lower() or name_pattern.lower() in cmdline.lower():
                    results.append((info["pid"], pname, cmdline))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return results
    except ImportError:
        return []
