"""
TeleClaw — Telegram bot built on the Claude Code SDK
Receive Telegram message → SDK query → response → send to Telegram.
Integrates health checks, restarts, state management and a watchdog.
"""

import os
import sys
import json
import time
import asyncio
import signal
import threading
import httpx
from pathlib import Path

# Avoid Windows cp949 encoding issues — force stdout/stderr to UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from claude_code_sdk import (
    ClaudeSDKClient, ClaudeCodeOptions,
    SystemMessage, AssistantMessage, UserMessage, ResultMessage,
)
from claude_code_sdk.types import StreamEvent

# monkey-patch: ignore unknown message types (rate_limit_event, etc.)
import claude_code_sdk._internal.message_parser as _mp
_original_parse = _mp.parse_message
def _patched_parse(data):
    try:
        return _original_parse(data)
    except Exception:
        return None
_mp.parse_message = _patched_parse

from .config import (
    PROJECTS, CHAT_ID, ALLOWED_USERS, TELECLAW_DIR, LOG_FILE,
    STATUS_FILE, SESSION_IDS_FILE, STATE_DIR, UPLOADS_DIR,
    HEALTH_CHECK_INTERVAL, STUCK_THRESHOLD,
    MAX_RESTARTS_PER_WINDOW, RESTART_WINDOW,
    AUTO_RESUME_ENABLED, AUTO_RESUME_MODE, AUTO_RESUME_PROMPTS,
    ICON_THINKING, ICON_DONE, OUTPUT_LEVEL, LANG,
)
from .logging_utils import log, _find_existing_teleclaw, _write_lock, _release_lock
from .storage import ensure_storage, prune_uploads
from .channel_telegram import TelegramChannel
from .session import SessionState
from .commands import handle_command, _get_usage
from .messages import msg
from .telegram_api import register_bot_commands
from . import state_db as db


class TeleClaw:
    def __init__(self):
        self.sessions: dict[str, SessionState] = {}
        self._shutdown = False
        self._http = httpx.Client(timeout=35)
        self._ahttp: httpx.AsyncClient | None = None
        self._update_ids: dict[str, int] = {}
        self._watchdog_ts = time.time()
        self._start_time = time.time()
        self._last_msg_map: dict[str, float] = {}  # for duplicate message removal
        self._ask_client: ClaudeSDKClient | None = None
        self._ask_busy = False
        self._fresh_start = True  # right after the TeleClaw process starts (vs. a single session restart)
        self.output_level = OUTPUT_LEVEL  # minimal / normal (switchable at runtime via /mode)

    async def start(self):
        log("TeleClaw starting")
        db.init()
        # Initialize sessions
        for name, config in PROJECTS.items():
            state = SessionState(name=name, config=config)
            channel = TelegramChannel(
                bot_token=config["bot_token"],
                chat_id=CHAT_ID,
                bot_name=name,
            )
            state.channel = channel
            self.sessions[name] = state

        self._load_session_ids()

        # Create the AsyncClient first (needed for polling)
        self._ahttp = httpx.AsyncClient(timeout=35)
        self._loop = asyncio.get_running_loop()  # command handlers run in a thread

        # Set ahttp on each channel
        for state in self.sessions.values():
            state.channel.set_ahttp(self._ahttp)

        # Startup notification (after channels are initialized)
        self._broadcast_sync(msg("sv_start"))

        # Register the "/" autocomplete menu (once per bot token; startup continues on failure)
        for token in {s.config.get("bot_token", "") for s in self.sessions.values()} - {""}:
            await asyncio.to_thread(register_bot_commands, token, LANG)

        # Start polling + utility loops right away (messages can be received before connecting)
        tasks = []
        for name, state in self.sessions.items():
            tasks.append(asyncio.create_task(self._bot_poll_loop(state)))
        tasks.append(asyncio.create_task(self._restart_flag_loop()))
        tasks.append(asyncio.create_task(self._health_check_loop()))
        tasks.append(asyncio.create_task(self._watchdog_loop()))

        # Connect sessions in parallel (minimizes downtime, skips paused sessions)
        async def _connect_and_init(state):
            if db.is_paused(state.name):
                log(f"{state.name}: PAUSED — skipping connect")
                return
            await self._connect_session(state)
            if state.connected:
                await self._wait_mcp_ready(state, timeout=5)
                state.channel.send_sync(msg("sv_ready"), notify=True)
                log(f"{state.name}: starting session loop immediately")

        await asyncio.gather(
            *[_connect_and_init(s) for s in self.sessions.values()],
            return_exceptions=True,
        )

        # Start all session loops (regardless of connection — unconnected ones auto-reconnect)
        for name, state in self.sessions.items():
            tasks.append(asyncio.create_task(self._session_loop(state)))

        self._write_status()

        connected = [n for n, s in self.sessions.items() if s.connected]
        elapsed = int(time.time() - self._start_time)
        await self._broadcast(msg("sv_init_done", elapsed=elapsed, names=', '.join(connected)))
        log("All loops started")

        # No auto-resume when TeleClaw itself starts
        # (sessions may still be unstable, and was_busy_before_restart is not set)
        self._fresh_start = False
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                log(f"task[{i}] exited with error: {r}")
                await self._broadcast(msg("sv_task_error", i=i, error=r))


    async def _safe_disconnect(self, client, name: str):
        """Terminate the process directly. client.disconnect() is not called because the anyio cancel scope conflict causes 100% CPU."""
        try:
            transport = getattr(client, "_transport", None)
            proc = getattr(transport, "_process", None) if transport else None
            if proc and proc.returncode is None:
                proc.terminate()
                log(f"{name}: terminating process (pid={proc.pid})")
            else:
                log(f"{name}: process already exited")
        except Exception as kill_err:
                log(f"{name}: failed to terminate process: {kill_err}")

    async def _connect_session(self, state: SessionState, mode: str = "resume"):
        try:
            mcp_servers = {}
            mcp_json_path = state.config.get("mcp_json")
            if mcp_json_path and os.path.exists(mcp_json_path):
                with open(mcp_json_path, "r", encoding="utf-8") as f:
                    mcp_data = json.load(f)
                mcp_servers = mcp_data.get("mcpServers", {})
                # Exclude only the polling telegram MCP (keep the sender)
                exclude = {"telegram", "telegram-crossword", "telegram-nemonemo"}
                mcp_servers = {
                    k: v for k, v in mcp_servers.items()
                    if k not in exclude and not k.startswith("telegram_")
                }

            options = ClaudeCodeOptions(
                permission_mode="bypassPermissions",
                cwd=state.config["cwd"],
                mcp_servers=mcp_servers,
                max_turns=50,
            )

            # reset mode: clear context (new conversation)
            if mode == "reset":
                state.session_id = None
                log(f"{state.name}: reset mode (new conversation)")
            else:
                # resume/new both keep the existing context
                if state.session_id:
                    options.resume = state.session_id
                    log(f"{state.name}: {mode} mode (session_id={state.session_id[:16]}...)")
                else:
                    options.continue_conversation = True
                    log(f"{state.name}: continue fallback (no session_id)")


            state.client = ClaudeSDKClient(options)
            await asyncio.wait_for(state.client.connect(None), timeout=120)
            state.connected = True
            state.error_count = 0
            state.start_time = time.time()
            state.query_count = 0
            state.last_restart_mode = mode
            log(f"{state.name}: SDK session connected (mode={mode})")
            state.channel.send_sync(msg("sv_connected", name=state.name), notify=True)
        except Exception as e:
            if mode != "reset":
                log(f"{state.name}: {mode} failed ({e}), retrying in reset mode")
                state.session_id = None
                await self._connect_session(state, mode="reset")
                return
            log(f"{state.name}: SDK connect failed: {e}")
            state.connected = False
            state.error_count += 1

    async def _wait_mcp_ready(self, state: SessionState, timeout: int = 5):
        """Wait for MCP servers to be ready. At least 3s, at most timeout seconds."""
        for i in range(timeout):
            await asyncio.sleep(1)
            if not state.connected or not state.client:
                break
        log(f"{state.name}: MCP stabilization wait done ({min(timeout, i+1)}s)")

    async def _ensure_ask_client(self) -> bool:
        """Create/reuse the SDK client dedicated to ask. True on success."""
        if self._ask_client is not None:
            return True
        try:
            options = ClaudeCodeOptions(
                permission_mode="bypassPermissions",
                cwd=TELECLAW_DIR,
                max_turns=5,
            )
            self._ask_client = ClaudeSDKClient(options)
            await asyncio.wait_for(self._ask_client.connect(None), timeout=60)
            log("ask session connected")
            return True
        except Exception as e:
            log(f"ask session connect failed: {e}")
            self._ask_client = None
            return False

    def _broadcast_sync(self, text: str):
        """Notify all sessions synchronously."""
        sent = set()
        for state in self.sessions.values():
            token = state.config.get("bot_token", "")
            if token and token not in sent:
                state.channel.send_sync(text)
                sent.add(token)

    async def _broadcast(self, text: str):
        """Notify all sessions asynchronously."""
        sent = set()
        for state in self.sessions.values():
            token = state.config.get("bot_token", "")
            if token and token not in sent:
                await state.channel.send(text)
                sent.add(token)

    def _channel_by_token(self, bot_token: str):
        """Return the channel for the given bot_token."""
        for state in self.sessions.values():
            if state.config["bot_token"] == bot_token:
                return state.channel
        return None

    async def _handle_ask(self, question: str, bot_token: str):
        """Handle the ask command asynchronously."""
        ch = self._channel_by_token(bot_token)
        if self._ask_busy:
            ch.send_sync(msg("ask_busy"))
            return
        self._ask_busy = True
        try:
            if not await self._ensure_ask_client():
                ch.send_sync(msg("ask_connect_fail"))
                return
            ch.send_sync(msg("ask_processing"))
            await self._ask_client.query(question)
            answer_parts = []
            async for ask_msg in self._ask_client.receive_messages():
                if ask_msg is None:
                    continue
                if isinstance(ask_msg, AssistantMessage):
                    for block in ask_msg.content:
                        if hasattr(block, "text") and block.text.strip():
                            answer_parts.append(block.text.strip())
                elif isinstance(ask_msg, ResultMessage):
                    break
            answer = "\n".join(answer_parts) if answer_parts else msg("ask_empty")
            if len(answer) > 3900:
                answer = answer[:3900] + msg("truncated")
            ch.send_sync(msg("ask_response", answer=answer))
        except Exception as e:
            log(f"ask handling failed: {e}")
            ch.send_sync(msg("ask_error", error=e))
            # Reset the session
            self._ask_client = None
        finally:
            self._ask_busy = False

    async def _restart_session(self, state: SessionState, reason: str, mode: str = "resume", force: bool = False, no_resume: bool = False):
        if state.restarting:
            log(f"{state.name}: restart already in progress, skipping (reason: {reason})")
            return

        now = time.time()
        state.restart_history = [ts for ts in state.restart_history if now - ts < RESTART_WINDOW]
        if not force and len(state.restart_history) >= MAX_RESTARTS_PER_WINDOW:
            oldest = min(state.restart_history) if state.restart_history else now
            wait_remaining = int(RESTART_WINDOW - (now - oldest))
            if wait_remaining > 0:
                limit_msg = msg("restart_limit", name=state.name, max=MAX_RESTARTS_PER_WINDOW, window=RESTART_WINDOW//60, reason=reason, remaining=wait_remaining)
                log(limit_msg)
                if now - state.last_notify_time > 300:
                    self._broadcast_sync(limit_msg)
                    state.last_notify_time = now
                return

        state.restarting = True
        try:
            log(f"{state.name}: restart attempt (reason: {reason})")
            state.channel.send_sync(msg("restart_reason", name=state.name, reason=reason))

            # Record pre-restart state (STUCK forces busy)
            state.was_busy_before_restart = state.busy or "STUCK" in reason
            state.last_restart_mode = mode

            # Drop the client reference after disconnect
            old_client = state.client
            state.client = None
            if old_client:
                asyncio.create_task(self._safe_disconnect(old_client, state.name))
            state.connected = False
            state.busy = False

            await asyncio.sleep(3)

            # reconnect
            await self._connect_session(state, mode=mode)
            state.restart_history.append(now)
            state.restart_count += 1
            self._write_status()
            if state.connected:
                state.channel.send_sync(msg("restart_done", name=state.name), notify=True)

                # auto-resume: only on individual session restarts
                # skipped for reset(new) or initial TeleClaw start (handled in check mode in start())
                if mode != "new" and not self._fresh_start and AUTO_RESUME_ENABLED and not no_resume and state.message_queue.empty():
                    # individual session restart → use the configured mode (resume/check)
                    effective_mode = AUTO_RESUME_MODE
                    prompt_key = AUTO_RESUME_PROMPTS.get(effective_mode)
                    prompt = msg(prompt_key) if prompt_key else None
                    if prompt and self._should_auto_resume(state):
                        state.resume_count += 1
                        log(f"{state.name}: auto-resume ({state.resume_count}/2, mode={effective_mode}) — leaving the decision to the AI")
                        await state.message_queue.put({
                            "text": prompt,
                            "msg_id": 0,
                            "auto_resume": True,
                            "retry_count": 0,
                        })
                    elif effective_mode == "none" and self._should_auto_resume(state):
                        log(f"{state.name}: auto-resume mode=none → waiting without a prompt")
        finally:
            state.restarting = False

    async def _restart_flag_loop(self):
        while not self._shutdown:
            try:
                await asyncio.sleep(1)

                # Check for a teleclaw self-restart (DB)
                sv_cmd = db.pop_command("teleclaw")
                if sv_cmd:
                    mode = "resume"
                    force = False
                    args = sv_cmd.get("args", "")
                    for t in [x.strip() for x in args.split(",") if x.strip()]:
                        if t == "force": force = True
                        elif t in ("resume", "reset"): mode = t
                    cooldown = 300  # 5 min
                    elapsed = time.time() - self._start_time
                    if not force and elapsed < cooldown:
                        log(f"ignoring teleclaw self-restart flag (cooldown: {int(cooldown - elapsed)}s left)")
                    else:
                        # mark busy sessions no_resume (prevents auto-resume loops)
                        self._save_session_ids(no_resume_if_busy=True)
                        # if any session is busy, wait for it to finish (up to 60s)
                        busy_sessions = [n for n, s in self.sessions.items() if s.busy]
                        if busy_sessions and not force:
                            log(f"teleclaw self-restart flag detected — waiting for busy sessions: {', '.join(busy_sessions)}")
                            waited = 0
                            while waited < 60:
                                await asyncio.sleep(2)
                                waited += 2
                                busy_sessions = [n for n, s in self.sessions.items() if s.busy]
                                if not busy_sessions:
                                    break
                            if busy_sessions:
                                log(f"graceful wait exceeded 60s, forcing exit (busy: {', '.join(busy_sessions)})")
                        log(f"teleclaw self-restart flag detected (mode={mode}, force={force}) → exiting process")
                        await self._broadcast(msg("sv_self_restart", mode=mode))
                        self._shutdown = True
                        os._exit(0)  # the wrapper restarts it automatically

                for name, state in self.sessions.items():
                    cmd = db.pop_command(name)
                    if not cmd:
                        continue
                    mode = "resume"
                    force = False
                    no_resume = False
                    command = cmd.get("command", "")
                    if command == "pause":
                        db.set_paused(name, True)
                        log(f"{name}: pause command (DB)")
                        continue
                    if command == "wakeup":
                        db.set_paused(name, False)
                        log(f"{name}: wakeup command (DB)")
                        continue
                    args = cmd.get("args", "")
                    tokens = [t.strip() for t in args.split(",") if t.strip()]
                    for t in tokens:
                        if t == "force": force = True
                        elif t == "noresume": no_resume = True
                        elif t in ("new", "resume", "reset"): mode = t
                    log(f"{name}: {command} command (DB, mode={mode})")
                    # a restart request automatically clears pause
                    db.set_paused(name, False)
                    state.no_resume_before_restart = False
                    log(f"{name}: running restart (mode={mode}, force={force}, noresume={no_resume})")
                    await self._restart_session(state, msg("reason_cmd_request", mode=mode), mode=mode, force=force, no_resume=no_resume)
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log(f"restart_flag_loop error: {e}")
                await asyncio.sleep(5)

    def _assess_health(self, state: SessionState) -> str:
        if state.restarting:
            return "OK"
        if not state.connected or state.client is None:
            return "DEAD"
        elapsed = time.time() - state.start_time
        if elapsed < HEALTH_CHECK_INTERVAL:
            return "OK"
        if state.busy and state.busy_since > 0:
            busy_duration = time.time() - state.busy_since
            if busy_duration > STUCK_THRESHOLD:
                return "STUCK"
        # messages are queued but the session hasn't been busy for 5+ minutes
        if not state.busy and state.message_queue.qsize() > 0:
            return "STUCK"
        return "OK"

    async def _health_check_loop(self):
        # 2-minute grace period after startup
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)
        last_prune = 0.0
        while not self._shutdown:
            try:
                if time.time() - last_prune > 3600:  # prune uploads once an hour
                    last_prune = time.time()
                    removed = prune_uploads()
                    if removed:
                        log(f"deleted {removed} old uploads")
                for name, state in self.sessions.items():
                    if self._shutdown:
                        return
                    if db.is_paused(name):
                        continue
                    status = self._assess_health(state)
                    if status == "DEAD":
                        await self._restart_session(state, "DEAD")
                    elif status == "STUCK":
                        await self._restart_session(state, msg("reason_stuck"))
                self._write_status()
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log(f"health_check_loop error: {e}")
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    def _save_offset(self, bot_id: str, offset: int):
        """Save the polling offset to a file atomically (write→rename)."""
        path = os.path.join(STATE_DIR, f"last_offset_{bot_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump({"offset": offset, "ts": time.time()}, f)
            os.replace(tmp_path, path)
        except Exception:
            pass

    def _load_offset(self, bot_id: str) -> int | None:
        """Restore the saved offset. None if missing."""
        path = os.path.join(STATE_DIR, f"last_offset_{bot_id}.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return data.get("offset")
        except Exception:
            return None

    def _save_session_ids(self, no_resume_if_busy=False):
        """Save session_id + busy state to a file (restored after restart).
        no_resume_if_busy=True: on a TeleClaw self-restart, mark busy sessions no_resume."""
        data = {}
        for name, state in self.sessions.items():
            entry = {}
            if state.session_id:
                entry["session_id"] = state.session_id
            entry["was_busy"] = state.busy
            if no_resume_if_busy and state.busy:
                entry["no_resume"] = True
            data[name] = entry
        try:
            with open(SESSION_IDS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_session_ids(self):
        """Restore the saved session_id + busy state."""
        try:
            with open(SESSION_IDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, val in data.items():
                if name not in self.sessions:
                    continue
                state = self.sessions[name]
                # backward compat: a plain string is just the session_id
                if isinstance(val, str):
                    if val:
                        state.session_id = val
                        log(f"{name}: session_id restored ({val[:16]}...)")
                elif isinstance(val, dict):
                    sid = val.get("session_id", "")
                    if sid:
                        state.session_id = sid
                        log(f"{name}: session_id restored ({sid[:16]}...)")
                    if val.get("was_busy"):
                        state.was_busy_before_restart = True
                        log(f"{name}: pre-restart busy state restored")
                    if val.get("no_resume"):
                        state.no_resume_before_restart = True
                        log(f"{name}: no_resume mark restored (prevents auto-resume loop)")
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            log(f"session_id restore failed: {e}")

    def _write_status(self):
        now = time.time()
        data = {
            "pid": os.getpid(),
            "uptime": int(now - self._start_time),
            "ts": now,
            "sessions": {},
        }
        for name, state in self.sessions.items():
            data["sessions"][name] = {
                "connected": state.connected,
                "busy": state.busy,
                "status": self._assess_health(state),
                "session_id": state.session_id[:16] if state.session_id else None,
                "restart_count": state.restart_count,
                "query_count": state.query_count,
                "error_count": state.error_count,
                "start_time": state.start_time,
            }
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def _watchdog_loop(self):
        while not self._shutdown:
            await asyncio.sleep(30)
            self._watchdog_ts = time.time()

    def _start_watchdog_thread(self):
        def _watchdog():
            while not self._shutdown:
                time.sleep(30)
                age = time.time() - self._watchdog_ts
                if age > 300:
                    log(f"WATCHDOG: asyncio loop unresponsive for {int(age)}s, forcing exit")
                    os._exit(1)
        t = threading.Thread(target=_watchdog, daemon=True)
        t.start()

    def _handle_command(self, text: str, bot_token: str) -> bool:
        """Handle a TeleClaw command. Returns True if handled."""
        ch = self._channel_by_token(bot_token)
        return handle_command(self, text, bot_token, ch)

    def _find_session_by_token(self, bot_token: str) -> str | None:
        for name, state in self.sessions.items():
            if state.config["bot_token"] == bot_token:
                return name
        return None

    def _get_usage(self) -> str:
        return _get_usage(self._http)

    async def _download_photo(self, msg: dict, bot_token: str, name: str) -> str:
        """Download a Telegram image and return its local path."""
        photos = msg.get("photo", [])
        if not photos:
            return ""
        # Pick the largest resolution
        photo = photos[-1]
        file_id = photo.get("file_id", "")
        if not file_id:
            return ""
        try:
            # Look up the file path via the getFile API
            url = f"https://api.telegram.org/bot{bot_token}/getFile"
            r = await self._ahttp.post(url, json={"file_id": file_id}, timeout=10)
            data = r.json()
            if not data.get("ok"):
                log(f"{name}: getFile failed: {data}")
                return ""
            file_path = data["result"]["file_path"]
            # Download
            download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            r = await self._ahttp.get(download_url, timeout=30)
            # Save locally
            save_dir = os.path.join(UPLOADS_DIR, "images")
            os.makedirs(save_dir, exist_ok=True)
            ext = os.path.splitext(file_path)[1] or ".jpg"
            save_path = os.path.join(save_dir, f"{name}_{int(time.time())}{ext}")
            with open(save_path, "wb") as f:
                f.write(r.content)
            log(f"{name}: image downloaded: {save_path}")
            return save_path
        except Exception as e:
            log(f"{name}: image download failed: {e}")
            return ""

    async def _download_photo_via_channel(self, ch, file_id: str, name: str) -> str:
        """Download an image via channel.download_file() and return its local path."""
        try:
            data = await ch.download_file(file_id)
            if not data:
                return ""
            save_dir = os.path.join(UPLOADS_DIR, "images")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"{name}_{int(time.time())}.jpg")
            with open(save_path, "wb") as f:
                f.write(data)
            log(f"{name}: image downloaded: {save_path}")
            return save_path
        except Exception as e:
            log(f"{name}: image download failed: {e}")
            return ""

    async def _download_doc_via_channel(self, ch, file_id: str, file_name: str, name: str) -> str:
        """Download a document via channel.download_file() and return its local path."""
        try:
            data = await ch.download_file(file_id)
            if not data:
                return ""
            save_dir = os.path.join(UPLOADS_DIR, "files")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, file_name)
            with open(save_path, "wb") as f:
                f.write(data)
            log(f"{name}: file downloaded: {save_path}")
            return save_path
        except Exception as e:
            log(f"{name}: file download failed: {e}")
            return ""

    @staticmethod
    def _tool_summary(tool_name: str, tool_input: dict) -> str:
        """Summarize a tool call as a short name"""
        # Shorten MCP tool names (mcp__ai-chat__ask → ai-chat.ask)
        short = tool_name
        if short.startswith("mcp__"):
            parts = short[5:].split("__", 1)
            short = ".".join(parts) if len(parts) > 1 else parts[0]
        path = (tool_input.get("file_path") or tool_input.get("path")
                or tool_input.get("pattern") or tool_input.get("command", "")[:60])
        if path:
            # long paths: file name only
            if len(path) > 40:
                path = "..." + path[-35:]
            return f"{short}: {path}"
        return short

    @staticmethod
    def _format_tool_line(tool_lines: list) -> str:
        """Format the tool call list as one compact line. Abbreviated when more than 4."""
        names = [t.replace("\U0001f527 ", "") for t in tool_lines]
        if len(names) <= 4:
            return "\u2500 \U0001f527 " + " \u2192 ".join(names)
        # more than 4: first 2 + last 1 + ellipsis marker
        shown = names[:2] + [f"...+{len(names) - 3}"] + names[-1:]
        return "\u2500 \U0001f527 " + " \u2192 ".join(shown)

    @staticmethod
    def _stabilize_markdown(text: str) -> str:
        """Temporarily close unclosed code blocks before editing to avoid broken markdown."""
        if text.count("```") % 2 == 1:
            text += "\n```"
        return text

    def _should_auto_resume(self, state: SessionState) -> bool:
        """Decide whether to auto-resume."""
        # no_resume mark (session was busy during a TeleClaw/flag restart → prevents loops)
        if state.no_resume_before_restart:
            log(f"{state.name}: no_resume mark → skipping auto-resume (prevents loop)")
            state.no_resume_before_restart = False
            return False
        # no resume in reset mode
        if state.last_restart_mode == "reset":
            log(f"{state.name}: reset mode → skipping auto-resume")
            return False
        # no session_id means context is lost → cannot resume
        if not state.session_id:
            log(f"{state.name}: no session_id (context lost) → skipping auto-resume")
            return False
        # resume_count exceeded
        if state.resume_count >= 2:
            log(f"{state.name}: auto-resume exceeded {state.resume_count} times → stopping")
            state.channel.send_sync(msg("auto_resume_fail", name=state.name))
            state.resume_count = 0
            return False
        return True

    async def _session_loop(self, state: SessionState):
        # auto-resume is only handled on individual session restarts (_restart_session)
        # on initial TeleClaw start, just wait
        log(f"{state.name}: session loop started — waiting")
        _idle_count = 0

        while not self._shutdown:
            try:
                msg_data = await asyncio.wait_for(
                    state.message_queue.get(), timeout=60
                )
                _idle_count = 0
            except (asyncio.TimeoutError, asyncio.CancelledError):
                _idle_count += 1
                if _idle_count % 5 == 0:  # heartbeat every 5 min
                    qsize = state.message_queue.qsize()
                    log(f"{state.name}: session loop waiting ({_idle_count}min, queue={qsize}, connected={state.connected})")
                continue
            except BaseException:
                continue

            if not state.client:
                retry = msg_data.get("retry_noclient", 0)
                if retry < 10:
                    msg_data["retry_noclient"] = retry + 1
                    await state.message_queue.put(msg_data)
                    if retry % 3 == 0:  # log every 3rd try (avoid spam)
                        log(f"{state.name}: no client, requeueing ({retry+1}/10, waiting 2s)")
                    await asyncio.sleep(2)
                else:
                    log(f"{state.name}: no client, retries exhausted (10 tries/20s) → dropping message")
                    state.channel.send_sync(msg("session_init_fail", text=msg_data['text'][:200]))
                continue

            if not state.connected:
                await self._restart_session(state, msg("reason_not_connected"))
                if not state.connected or not state.client:
                    retry = msg_data.get("retry_conn", 0)
                    if retry < 1:
                        msg_data["retry_conn"] = retry + 1
                        await state.message_queue.put(msg_data)
                        log(f"{state.name}: session not connected, queueing retry ({retry+1}/1)")
                        await asyncio.sleep(2)
                    else:
                        state.channel.send_sync(msg("session_connect_fail", text=msg_data['text'][:200]))
                    continue

            was_queued = msg_data.get("retry_count", 0) > 0 or msg_data.get("queued_while_busy", False)
            state.busy = True
            state.busy_since = time.time()
            text = msg_data["text"]
            is_auto_resume = msg_data.get("auto_resume", False)
            msg_id = msg_data.get("msg_id", 0)

            # user message → reset resume_count
            if not is_auto_resume and state.resume_count > 0:
                state.resume_count = 0

            # send a separator when handling a queued message (avoids confusion with the previous reply)
            if was_queued and not is_auto_resume:
                await state.channel.send(msg("pending_message", text=text[:100]))

            log(f"{state.name}: processing message: {text[:50]}")
            # the processing-started notice is replaced by the receipt ack (✔️)

            try:
                client = state.client
                if not client:
                    continue
                # buffer drain: remove leftover messages from the previous turn before query() (prevents N-turn lag)
                # NOTE: accesses SDK private attributes (_query, _message_receive) directly.
                # May break on SDK updates; re-check after upgrading.
                drain_count = 0
                drain_types = []
                try:
                    import anyio
                    if hasattr(client, '_query') and client._query:
                        stream = client._query._message_receive
                        while True:
                            try:
                                stale = stream.receive_nowait()
                                drain_count += 1
                                msg_type = stale.get("type", "?")
                                # summarize the content (first 50 chars for text blocks)
                                preview = ""
                                if msg_type == "assistant":
                                    content = stale.get("message", {}).get("content", [])
                                    for b in content if isinstance(content, list) else []:
                                        if isinstance(b, dict) and b.get("type") == "text":
                                            preview = b.get("text", "")[:50]
                                            break
                                        elif isinstance(b, dict) and b.get("type") == "tool_use":
                                            preview = f"tool:{b.get('name', '?')}"
                                            break
                                elif msg_type == "result":
                                    preview = f"session={stale.get('session_id', '?')[:16]}"
                                drain_types.append(f"{msg_type}({preview})" if preview else msg_type)
                            except anyio.WouldBlock:
                                break
                            except anyio.ClosedResourceError:
                                break
                except Exception as e:
                    log(f"{state.name}: buffer drain error: {e}")
                if drain_count:
                    log(f"{state.name}: buffer drain removed {drain_count} items: {drain_types[:10]}")

                try:
                    await asyncio.wait_for(client.query(text), timeout=10)
                except asyncio.TimeoutError:
                    retry = msg_data.get("retry_timeout", 0)
                    log(f"{state.name}: query() init timeout (10s), retry={retry}")
                    await self._restart_session(state, msg("reason_query_timeout"), mode="resume", force=True)
                    if retry < 2:
                        msg_data["retry_timeout"] = retry + 1
                        await state.message_queue.put(msg_data)
                        log(f"{state.name}: queueing timeout retry ({retry+1}/2)")
                        await asyncio.sleep((retry + 1) * 2)
                    else:
                        state.channel.send_sync(msg("timeout_exhausted", text=msg_data['text'][:200]))
                    state.busy = False
                    continue

                live_msg_id = ""  # current editMessage target (str, channel interface)
                live_lines = []  # text accumulated in the current message
                tool_lines = []  # temporary tool call buffer (cleared when text arrives)
                last_tool_name = ""  # last tool name (to identify the ToolResult)
                msg_count = 0
                last_edit = 0.0
                edit_interval = 1.0  # adaptive edit interval (seconds)
                query_start = time.time()  # for detecting fast replies
                consecutive_edit_fails = 0  # consecutive edit failures
                last_progress_notify = 0  # time of the last progress notice
                bot_token = state.config["bot_token"]
                ch = state.channel

                async for sdk_msg in client.receive_messages():
                    if sdk_msg is None:
                        continue
                    # if the client was replaced (restart), stop the current loop
                    if state.client is not client:
                        log(f"{state.name}: client replacement detected → stopping receive_messages")
                        break
                    # message receive timeout check (10 min without a message → force stop)
                    if time.time() - state.busy_since > 600 and msg_count == 0:
                        log(f"{state.name}: no messages for 10 min → force stop")
                        break
                    # progress notice for slow replies (every 2 min)
                    elapsed = time.time() - state.busy_since
                    if elapsed > 120 and time.time() - last_progress_notify > 120:
                        mins = int(elapsed / 60)
                        await ch.send(msg("still_processing", mins=mins, tools=msg_count))
                        last_progress_notify = time.time()
                    msg_count += 1

                    if isinstance(sdk_msg, AssistantMessage):
                        _lvl = self.output_level
                        for block in sdk_msg.content:
                            block_type = type(block).__name__
                            if hasattr(block, "text") and block.text.strip():
                                # TextBlock — when a text reply arrives, flush the tool summary
                                if tool_lines:
                                    live_lines.append(self._format_tool_line(tool_lines))
                                    tool_lines = []
                                live_lines.append(block.text)
                                log(f"{state.name}: [block] TextBlock ({len(block.text)} chars)")
                            elif block_type == "ToolUseBlock":
                                tool_name = getattr(block, "name", "tool")
                                tool_input = getattr(block, "input", {})
                                last_tool_name = tool_name
                                if _lvl != "minimal":
                                    summary = self._tool_summary(tool_name, tool_input)
                                    tool_lines.append(f"\U0001f527 {summary}")
                                log(f"{state.name}: [block] ToolUse: {self._tool_summary(tool_name, tool_input)}")
                            elif block_type == "ThinkingBlock":
                                thinking_text = getattr(block, "thinking", "") or ""
                                log(f"{state.name}: [block] Thinking ({len(thinking_text)} chars)")
                                continue
                            else:
                                log(f"{state.name}: [block] {block_type} (skip)")
                                continue

                            # live send/edit
                            now = time.time()
                            display = list(live_lines)
                            if tool_lines:
                                display.append(self._format_tool_line(tool_lines))
                            content = "\n".join(display)

                            # start a new message after a 10s+ gap or past 2000 chars
                            need_new_msg = (
                                live_msg_id and (
                                    now - last_edit >= 10.0 or
                                    len(content) > 2000
                                )
                            )
                            if need_new_msg and len(live_lines) > 1:
                                # finish the current message and start a new one (only with 2+ lines)
                                prev = "\n".join(live_lines[:-1])
                                await ch.edit(live_msg_id, prev)
                                new_start = live_lines[-1]
                                live_msg_id = await ch.send(new_start)
                                live_lines = [new_start]
                                tool_lines = []
                                last_edit = now
                            elif not live_msg_id:
                                # first message: buffer for 3s — fast replies go out in the final send without edits
                                elapsed_since_start = now - query_start
                                if elapsed_since_start >= 3.0:
                                    live_msg_id = await ch.send(content)
                                    last_edit = now
                                # hold sending under 3s (sent all at once in the final send)
                            elif now - last_edit >= edit_interval:
                                # headroom under the 4096-char limit (prefix + margin)
                                max_len = 4096 - len(f"[{state.name}] ") - 50
                                stable_content = ch.format(content)
                                if len(content) > max_len:
                                    prev = "\n".join(live_lines[:-1]) if len(live_lines) > 1 else "\n".join(live_lines)
                                    await ch.edit(live_msg_id, ch.format(prev), use_markup=True)
                                    new_start = live_lines[-1] if live_lines else ""
                                    live_msg_id = await ch.send(new_start)
                                    live_lines = [new_start] if new_start else []
                                    tool_lines = []
                                else:
                                    ok = await ch.edit(live_msg_id, stable_content, use_markup=True)
                                    if not ok:
                                        consecutive_edit_fails += 1
                                        if consecutive_edit_fails >= 3:
                                            # rate limit: increase the interval
                                            edit_interval = min(edit_interval * 2, 5.0)
                                            consecutive_edit_fails = 0
                                        live_msg_id = await ch.send(stable_content, use_markup=True)
                                    else:
                                        consecutive_edit_fails = 0
                                        # on success, gradually restore the interval
                                        if edit_interval > 1.0:
                                            edit_interval = max(edit_interval - 0.5, 1.0)
                                last_edit = now

                    elif isinstance(sdk_msg, UserMessage):
                        # ToolResult handling — ai-chat results in full, others summarized
                        result_text = ""
                        for block in sdk_msg.content:
                            bt = type(block).__name__
                            if hasattr(block, "text") and block.text:
                                result_text = block.text.strip()
                                log(f"{state.name}: [user-block] {bt} ({len(result_text)} chars)")
                                break
                            elif hasattr(block, "content") and isinstance(block.content, str):
                                result_text = block.content.strip()
                                log(f"{state.name}: [user-block] {bt} ({len(result_text)} chars)")
                                break
                            else:
                                log(f"{state.name}: [user-block] {bt} (skip)")
                        if result_text:
                            is_ai_chat = last_tool_name.startswith("mcp__ai_chat__") or last_tool_name.startswith("mcp__ai-chat__")
                            if is_ai_chat:
                                # ai-chat results go into a separate new message
                                if live_msg_id:
                                    # finish the previous message with tool_lines even if live_lines is empty
                                    prev_content = "\n".join(live_lines) if live_lines else ""
                                    if tool_lines:
                                        tl = self._format_tool_line(tool_lines)
                                        prev_content = f"{prev_content}\n{tl}" if prev_content else tl
                                    if prev_content:
                                        await ch.edit(live_msg_id, prev_content)
                                    tool_lines = []
                                    live_lines = []
                                    live_msg_id = ""
                                # JSON {"result":"..."} parsing
                                display_text = result_text
                                try:
                                    parsed = json.loads(result_text)
                                    if isinstance(parsed, dict) and "result" in parsed:
                                        display_text = parsed["result"]
                                except (json.JSONDecodeError, TypeError):
                                    pass
                                # send ai-chat results immediately as a separate message (shown before ✅)
                                mcp_display = f"\U0001f4ac {display_text}"
                                for mcp_chunk in ch.split(mcp_display):
                                    await ch.send(mcp_chunk)
                                log(f"{state.name}: [result] ai-chat ({len(result_text)} chars) → sent immediately")
                            elif last_tool_name in ("Edit", "Write", "NotebookEdit", "Read", "Grep", "Glob"):
                                # code/file results show only their length
                                if tool_lines:
                                    tool_lines[-1] += msg("tool_result_chars", n=len(result_text))
                                log(f"{state.name}: [result] {last_tool_name} ({len(result_text)} chars)")
                            elif len(result_text) <= 500:
                                if tool_lines:
                                    live_lines.append(self._format_tool_line(tool_lines))
                                    tool_lines = []
                                live_lines.append(result_text)
                                log(f"{state.name}: [result] short ({len(result_text)} chars)")
                            else:
                                if tool_lines:
                                    tool_lines[-1] += msg("tool_result_chars", n=len(result_text))
                                log(f"{state.name}: [result] long ({len(result_text)} chars)")

                            # live update
                            now = time.time()
                            display = list(live_lines)
                            if tool_lines:
                                display.append(self._format_tool_line(tool_lines))
                            content = "\n".join(display)
                            if not live_msg_id:
                                live_msg_id = await ch.send(ch.format(content), use_markup=True)
                                last_edit = now
                            elif now - last_edit >= edit_interval:
                                stable_content = ch.format(content)
                                ok = await ch.edit(live_msg_id, stable_content, use_markup=True)
                                if not ok:
                                    consecutive_edit_fails += 1
                                    if consecutive_edit_fails >= 3:
                                        edit_interval = min(edit_interval * 2, 5.0)
                                        consecutive_edit_fails = 0
                                    live_msg_id = await ch.send(stable_content, use_markup=True)
                                else:
                                    consecutive_edit_fails = 0
                                    if edit_interval > 1.0:
                                        edit_interval = max(edit_interval - 0.5, 1.0)
                                last_edit = now

                    elif isinstance(sdk_msg, ResultMessage):
                        if hasattr(sdk_msg, "session_id") and sdk_msg.session_id:
                            state.session_id = sdk_msg.session_id
                            self._save_session_ids()
                        if sdk_msg.usage:
                            log(f"{state.name}: [usage] {sdk_msg.usage}")
                        # if live streaming was empty, fall back to ResultMessage.result
                        if not live_lines and sdk_msg.result:
                            result_text = sdk_msg.result.strip()
                            if result_text:
                                live_lines.append(result_text)
                                log(f"{state.name}: [result-fallback] {len(result_text)} chars")
                        if hasattr(sdk_msg, "total_cost_usd") and sdk_msg.total_cost_usd:
                            log(f"{state.name}: [cost] ${sdk_msg.total_cost_usd:.4f}")
                        break

                    elif isinstance(sdk_msg, SystemMessage):
                        subtype = getattr(sdk_msg, "subtype", "?")
                        log(f"{state.name}: [system] subtype={subtype}")

                    elif isinstance(sdk_msg, StreamEvent):
                        event = getattr(sdk_msg, "event", "?")
                        log(f"{state.name}: [stream] event={event}")

                # final update (with HTML conversion)
                if tool_lines:
                    live_lines.append(self._format_tool_line(tool_lines))
                if live_lines:
                    content = "\n".join(live_lines)
                    # split the plain text, then convert each chunk to HTML (avoids cutting tags)
                    chunks = [ch.format(c) for c in ch.split(content)]
                    elapsed = time.time() - query_start
                    # fast reply (within 3s): send as a new message instead of editMessage
                    # → prevents "lagging replies" caused by editMessage delays
                    if live_msg_id and elapsed >= 3.0:
                        ok = await ch.edit(live_msg_id, chunks[0], use_markup=True)
                        if not ok:
                            await ch.edit(live_msg_id, ch.split(content)[0])
                        for chunk in chunks[1:]:
                            await ch.send(chunk, use_markup=True)
                    else:
                        # fast reply or no live_msg_id: send as a new message
                        if live_msg_id:
                            # delete the existing live message (avoid duplicates)
                            await ch.delete(live_msg_id)
                        for chunk in chunks:
                            await ch.send(chunk, use_markup=True)
                    log(f"{state.name}: final send ({len(content)} chars, {len(chunks)} chunks, {elapsed:.1f}s)")
                else:
                    log(f"{state.name}: empty reply")
                    await ch.send(msg("empty_response"))

                log(f"{state.name}: done processing")

                # completion notice (new message, triggers a notification)
                asyncio.create_task(ch.send(ICON_DONE))

                # commit the offset after processing (unprocessed messages are re-received after a restart)
                processed_update_id = msg_data.get("update_id", 0)
                if processed_update_id:
                    bot_id = state.config["bot_id"]
                    self._update_ids[bot_id] = max(
                        self._update_ids.get(bot_id, 0), processed_update_id
                    )

                state.error_count = 0
                state.query_count += 1
                # completed normally → reset resume_count
                if state.resume_count > 0:
                    log(f"{state.name}: completed normally → resume_count reset ({state.resume_count} → 0)")
                    state.resume_count = 0

                # auto-reset removed — Claude auto-compact manages context
                # use /reset manually if needed

            except BaseException as e:
                # includes CancelledError — catch everything so the loop doesn't die
                err_name = type(e).__name__
                err_str = str(e)
                log(f"{state.name}: processing error ({err_name}): {e}")
                state.error_count += 1

                # accumulated image error → automatic reset (resume can't fix it)
                if "dimension limit" in err_str or "many-image" in err_str:
                    log(f"{state.name}: accumulated image error detected → restarting in reset mode")
                    state.channel.send_sync(msg("image_overflow"))
                    await self._restart_session(state, msg("reason_image_overflow"), mode="reset", force=True)
                    continue

                if state.restarting:
                    # process exit caused by a reset/restart — intended, so no retry
                    log(f"{state.name}: ignoring error during restart ({err_name})")
                elif isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                    # cancel/interrupt: move on to the next message without retrying
                    log(f"{state.name}: {err_name} — loop kept alive, waiting for the next message")
                else:
                    retry = msg_data.get("retry_error", 0)
                    if retry < 1:
                        msg_data["retry_error"] = retry + 1
                        await state.message_queue.put(msg_data)
                        log(f"{state.name}: queueing error retry ({retry+1}/1)")
                        await asyncio.sleep(2)
                    else:
                        state.channel.send_sync(msg("process_fail", error=e, text=msg_data['text'][:200]))
                if state.error_count >= 3:
                    await self._restart_session(state, msg("reason_consecutive_errors", count=state.error_count))
            finally:
                state.busy = False
                state.busy_since = 0.0

    async def _bot_poll_loop(self, state: SessionState):
        """Independent polling task per bot — each bot long-polls in parallel"""
        name = state.name
        bot_token = state.config["bot_token"]
        bot_id = state.config["bot_id"]
        ch = state.channel

        # restore offset (use the saved one if present, otherwise flush)
        saved_offset = self._load_offset(bot_id)
        if saved_offset is not None:
            self._update_ids[bot_id] = saved_offset
            ch.set_offset(saved_offset + 1)
            log(f"{name}: offset restored = {saved_offset}")
        else:
            # flush: consume all pending messages to initialize the offset
            try:
                flush_msgs = await ch.poll(timeout=0)
                # poll updates the offset internally
                last_offset = ch.get_offset() - 1 if ch.get_offset() > 0 else 0
                self._update_ids[bot_id] = last_offset
                log(f"{name}: offset initialized = {last_offset} (flushed {len(flush_msgs)})")
            except Exception as e:
                log(f"{name}: offset init failed: {e}")
                self._update_ids[bot_id] = 0

        error_count = 0
        _poll_count = 0
        while not self._shutdown:
            _poll_count += 1
            if _poll_count % 20 == 0:  # every ~10 min (25s long poll × 20)
                log(f"{name}: polling loop healthy (cycle={_poll_count}, errors={error_count})")
            try:
                messages = await ch.poll(timeout=25)
                for m in messages:
                    raw = m.get("_raw", {})
                    msg_id_str = m["id"]
                    msg_id = int(msg_id_str) if msg_id_str else 0
                    from_id = m.get("from_id", "")
                    if not ALLOWED_USERS or (from_id and str(from_id) not in ALLOWED_USERS):
                        log(f"{name}: ignoring message from unauthorized user (from_id={from_id})")
                        continue
                    msg_date = m.get("date", 0)
                    if msg_date < self._start_time:
                        log(f"{name}: skipping old message (date={msg_date})")
                        continue
                    text = m.get("text", "")
                    is_edited = raw.get("_is_edited", False) if raw else False
                    files = m.get("files", [])

                    # handle image messages
                    if not text and files:
                        for f_info in files:
                            if f_info.get("type") == "photo":
                                photo_path = await self._download_photo_via_channel(ch, f_info["file_id"], name)
                                if photo_path:
                                    caption = raw.get("caption", "") if raw else ""
                                    text = msg("prompt_check_image", path=photo_path)
                                    if caption:
                                        text = msg("prompt_image_caption", caption=caption, path=photo_path)
                                break
                            elif f_info.get("type") == "document":
                                file_id = f_info.get("file_id", "")
                                file_name = f_info.get("name", "unknown")
                                caption = raw.get("caption", "") if raw else ""
                                if file_id:
                                    doc_path = await self._download_doc_via_channel(ch, file_id, file_name, name)
                                    if doc_path:
                                        text = msg("prompt_check_file", path=doc_path)
                                        if caption:
                                            text = msg("prompt_file_caption", caption=caption, path=doc_path)
                                break

                    if not text:
                        continue

                    if await asyncio.to_thread(self._handle_command, text, bot_token):
                        log(f"{name}: handling command: {text}")
                        continue

                    sender = raw.get("from", {}).get("first_name", "") if raw else ""
                    edit_tag = msg("edited_tag") if is_edited else ""
                    full_text = f"{sender}{edit_tag}: {text}"

                    # drop duplicate messages (by message_id)
                    msg_key = f"{name}_{msg_id}"
                    if msg_key in self._last_msg_map:
                        continue
                    # drop network-retransmit duplicates (same date + same text)
                    msg_date_key = f"{name}_d{msg_date}_{text}"
                    if msg_date_key in self._last_msg_map:
                        log(f"{name}: skipping duplicate with same date+text (date={msg_date}): {text[:30]}")
                        continue
                    self._last_msg_map[msg_key] = time.time()
                    self._last_msg_map[msg_date_key] = time.time()
                    # prune old entries (when over 100)
                    if len(self._last_msg_map) > 100:
                        cutoff = time.time() - 300
                        self._last_msg_map = {k: v for k, v in self._last_msg_map.items() if v > cutoff}

                    # paused: restart/reset commands pass, everything else is rejected
                    if db.is_paused(name):
                        text_lower = text.strip().lower()
                        if text_lower in ("restart", "reset", "재시작", "리셋", "/restart", "/reset"):
                            db.set_paused(name, False)
                            mode = "reset" if "reset" in text_lower or "리셋" in text_lower else "resume"
                            db.push_command(name, "restart", f"force,{mode}" if mode != "resume" else "force")
                            await ch.send(msg("pause_unpause_restart", name=name), reply_to=msg_id_str)
                            self._save_offset(bot_id, ch.get_offset() - 1)
                            log(f"{name}: PAUSED → resumed (Telegram command: {text_lower})")
                            continue
                        await ch.send(msg("paused_hint", name=name), reply_to=msg_id_str)
                        self._save_offset(bot_id, ch.get_offset() - 1)
                        log(f"{name}: PAUSED — message rejected: {text[:50]}")
                        continue

                    # receipt ack
                    if state.busy:
                        qsize = state.message_queue.qsize() + 1
                        await ch.send(msg("ack_busy", icon=ICON_THINKING, qsize=qsize))
                    else:
                        await ch.send(ICON_THINKING)

                    # track update_id (channel.poll manages the offset, so current offset - 1)
                    update_id = ch.get_offset() - 1

                    await state.message_queue.put({
                        "text": full_text,
                        "msg_id": msg_id,
                        "update_id": update_id,
                        "retry_count": 0,
                        "queued_while_busy": state.busy,
                    })
                    # update the offset + save to file right after enqueueing
                    self._update_ids[bot_id] = update_id
                    self._save_offset(bot_id, update_id)
                    log(f"{name}: message received: {text[:50]}")
                if error_count > 0:
                    error_count = 0
            except Exception as e:
                error_count += 1
                if error_count % 10 == 1:
                    import traceback
                    tb = traceback.format_exc()
                    log(f"{name}: polling error #{error_count}: {repr(e)}\n{tb}")
                await asyncio.sleep(min(2 ** min(error_count, 5), 30))

    async def shutdown(self):
        self._shutdown = True
        self._broadcast_sync(msg("sv_shutting_down"))
        if self._ahttp:
            await self._ahttp.aclose()
        # clean up the ask client
        if self._ask_client:
            await self._safe_disconnect(self._ask_client, "ask")
            self._ask_client = None
        # disconnect session clients
        for name, state in self.sessions.items():
            if state.client:
                await self._safe_disconnect(state.client, name)
            state.client = None
            state.connected = False
        self._write_status()
        _release_lock()
        log("TeleClaw stopped")


async def main():
    for note in ensure_storage():
        log(f"storage migration: {note}")

    existing_pid = _find_existing_teleclaw()
    if existing_pid:
        log(f"TeleClaw already running (PID={existing_pid}), exiting")
        print(f"TeleClaw is already running (PID={existing_pid}).")
        sys.exit(42)  # the wrapper recognizes this code as 'already running'

    _write_lock()
    hub = TeleClaw()
    hub._start_watchdog_thread()

    def on_signal(sig, frame):
        hub._shutdown = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        await hub.start()
    except KeyboardInterrupt:
        pass
    finally:
        await hub.shutdown()
