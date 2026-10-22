"""TeleClaw package for Telegram"""

import asyncio

from .config import PROJECTS, CHAT_ID, TELECLAW_DIR, LOGS_DIR
from .logging_utils import log, _find_existing_teleclaw, _write_lock, _release_lock
from .channel import Channel
from .channel_telegram import TelegramChannel
from .session import SessionState
from .teleclaw import TeleClaw, main as _async_main


def main():
    """CLI entry point with subcommands.

    teleclaw            → run TeleClaw
    teleclaw install    → register the system service
    teleclaw uninstall  → unregister the system service
    teleclaw status     → service status
    teleclaw logs [N]   → service logs
    """
    import sys
    args = sys.argv[1:]
    if args:
        cmd = args[0].lower()
        from . import service
        if cmd == "install":
            service.install()
            return
        if cmd == "uninstall":
            service.uninstall()
            return
        if cmd == "status":
            service.status()
            return
        if cmd == "logs":
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
            service.logs(n)
            return
    asyncio.run(_async_main())


__all__ = [
    "TeleClaw",
    "SessionState",
    "main",
    "log",
    "Channel",
    "TelegramChannel",
    "PROJECTS",
]
