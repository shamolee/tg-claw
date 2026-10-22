#!/usr/bin/env python3
"""CLI for sending text/images/files to Telegram.

Usage:
    python -m src.telegram_helper text  <message>
    python -m src.telegram_helper photo <path> [caption]
    python -m src.telegram_helper file  <path> [caption]
"""
import os
import sys

from .config import PROJECTS, CHAT_ID
from . import telegram_api as tg


def _match_project():
    """Find the bot_token in PROJECTS matching the current cwd."""
    cwd = os.getcwd().replace("\\", "/").rstrip("/")
    for name, cfg in PROJECTS.items():
        proj_cwd = cfg.get("cwd", "").replace("\\", "/").rstrip("/")
        if proj_cwd and (cwd == proj_cwd or cwd.startswith(proj_cwd + "/")):
            return cfg["bot_token"], name
    if PROJECTS:
        first = next(iter(PROJECTS.values()))
        return first["bot_token"], next(iter(PROJECTS.keys()))
    return None, None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: telegram_helper <text|photo|file> <message|path> [caption]")
        sys.exit(1)

    kind = sys.argv[1]
    arg = sys.argv[2]
    caption = sys.argv[3] if len(sys.argv) > 3 else ""

    if kind not in ("text", "photo", "file"):
        print("First argument must be 'text', 'photo', or 'file'")
        sys.exit(1)

    bot_token, proj_name = _match_project()
    if not bot_token:
        print("No telegram config found")
        sys.exit(1)

    if kind == "text":
        mid = tg.send_telegram(arg, bot_token)
        if mid:
            print("Sent")
        else:
            print("Send failed", file=sys.stderr)
            sys.exit(1)
    else:
        if not os.path.exists(arg):
            print(f"File not found: {arg}")
            sys.exit(1)
        if kind == "photo":
            mid = tg.send_photo_sync(bot_token, arg, caption)
        else:
            mid = tg.send_file_sync(bot_token, arg, caption)
        if mid:
            print(f"Sent: {kind} {os.path.basename(arg)}")
        else:
            print("Send failed", file=sys.stderr)
            sys.exit(1)
