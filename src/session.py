"""SessionState dataclass"""

import asyncio
from dataclasses import dataclass, field

from claude_code_sdk import ClaudeSDKClient


@dataclass
class SessionState:
    name: str
    config: dict
    client: ClaudeSDKClient | None = None
    channel: "Channel | None" = None  # src.channel.Channel
    connected: bool = False
    busy: bool = False
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    error_count: int = 0
    start_time: float = 0.0
    query_count: int = 0
    restart_count: int = 0
    restart_history: list = field(default_factory=list)
    last_notify_time: float = 0.0
    restarting: bool = False
    busy_since: float = 0.0
    session_id: str | None = None
    resume_count: int = 0  # consecutive auto-resumes (reset on normal completion)
    last_restart_mode: str = ""  # last restart mode (resume/reset/crash)
    was_busy_before_restart: bool = False  # whether the session was busy before the restart
    no_resume_before_restart: bool = False  # prevents an auto-resume loop on TeleClaw/flag restarts
