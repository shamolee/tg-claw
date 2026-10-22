"""tg-claw package for Telegram"""

import asyncio

from .config import PROJECTS, CHAT_ID, TG_CLAW_DIR, LOGS_DIR
from .logging_utils import log, _find_existing_tg_claw, _write_lock, _release_lock
from .channel import Channel
from .channel_telegram import TelegramChannel
from .session import SessionState
from .tg_claw import TgClaw, main as _async_main


def main():
    """CLI entry point with subcommands.

    tg-claw            → run tg-claw
    tg-claw install    → register the system service
    tg-claw uninstall  → unregister the system service
    tg-claw status     → service status
    tg-claw logs [N]   → service logs
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
    "TgClaw",
    "SessionState",
    "main",
    "log",
    "Channel",
    "TelegramChannel",
    "PROJECTS",
]
