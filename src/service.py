"""System service install/uninstall: systemd (Linux) / Task Scheduler (Windows)"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import LOG_FILE, SERVICE_NAME
from .messages import msg


def _get_python():
    return sys.executable


def _get_wrapper_path():
    return str(Path(__file__).resolve().parent / "tg_claw_daemon.py")


def _get_service_dir():
    return Path(__file__).resolve().parent.parent


# --- Linux (systemd) ---

def _systemd_install():
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_file = service_dir / "tg-claw.service"

    python = _get_python()
    wrapper = _get_wrapper_path()
    work_dir = str(_get_service_dir())

    content = f"""[Unit]
Description=tg-claw - Telegram remote control for Claude Code
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart="{python}" "{wrapper}"
WorkingDirectory={work_dir}
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tg-claw

[Install]
WantedBy=default.target
"""
    service_file.write_text(content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "tg-claw"], check=True)
    subprocess.run(["systemctl", "--user", "start", "tg-claw"], check=True)
    # loginctl enable-linger for auto-start without login
    user = os.environ.get("USER", "")
    if user:
        subprocess.run(["loginctl", "enable-linger", user], capture_output=True)
    print(f"Installed: {service_file}")
    print("Commands:")
    print("  systemctl --user status tg-claw")
    print("  journalctl --user -u tg-claw -f")
    print("  systemctl --user stop tg-claw")


def _systemd_uninstall():
    subprocess.run(["systemctl", "--user", "stop", "tg-claw"], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", "tg-claw"], capture_output=True)
    service_file = Path.home() / ".config" / "systemd" / "user" / "tg-claw.service"
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("Uninstalled: tg-claw.service")


def _systemd_status():
    subprocess.run(["systemctl", "--user", "status", "tg-claw"])


def _systemd_logs(n: int = 50):
    subprocess.run(["journalctl", "--user", "-u", "tg-claw", "-n", str(n), "--no-pager"])


# --- Windows (Task Scheduler) ---

def _schtasks_install():
    python = _get_python()
    wrapper = _get_wrapper_path()
    work_dir = str(_get_service_dir())
    task_name = SERVICE_NAME

    # Remove the existing task
    subprocess.run(
        ["schtasks", "/delete", "/tn", task_name, "/f"],
        capture_output=True)

    # Start automatically at logon
    subprocess.run([
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", f'"{python}" "{wrapper}"',
        "/sc", "onlogon",
        "/rl", "highest",
        "/f",
    ], check=True)
    print(f"Installed: Task Scheduler '{task_name}'")
    print("Commands:")
    print(f"  schtasks /query /tn {task_name}")
    print(f"  schtasks /run /tn {task_name}")
    print(f"  schtasks /end /tn {task_name}")

    # Start right away
    subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True)


def _schtasks_uninstall():
    task_name = SERVICE_NAME
    subprocess.run(["schtasks", "/end", "/tn", task_name], capture_output=True)
    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)
    print(f"Uninstalled: Task Scheduler '{task_name}'")


def _schtasks_status():
    subprocess.run(["schtasks", "/query", "/tn", SERVICE_NAME, "/v", "/fo", "list"])


def _schtasks_logs(n: int = 50):
    log_file = Path(LOG_FILE)
    if not log_file.exists():
        print("No log file")
        return
    lines = log_file.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        print(line)


# --- Common interface ---

def install():
    """Register the system service and start it."""
    if sys.platform == "win32":
        _schtasks_install()
    else:
        _systemd_install()


def uninstall():
    """Unregister the system service."""
    if sys.platform == "win32":
        _schtasks_uninstall()
    else:
        _systemd_uninstall()


def status():
    """Show service status."""
    if sys.platform == "win32":
        _schtasks_status()
    else:
        _systemd_status()


def logs(n: int = 50):
    """Show service logs."""
    if sys.platform == "win32":
        _schtasks_logs(n)
    else:
        _systemd_logs(n)
