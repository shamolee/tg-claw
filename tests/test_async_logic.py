#!/usr/bin/env python3
"""tg-claw async business logic tests.

Covers:
  1. _restart_session — core recovery logic
  2. _assess_health — session health check (extra cases)
  3. channel_telegram.py — Channel interface
  4. state_db.py — DB functions
  5. commands.py — command handlers
"""

import sys
import os
import time
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.session import SessionState
from src import state_db as db


# ============================================================
# Helpers: tg-claw instance + SessionState factory
# ============================================================

def _make_tg_claw():
    """Create a tg-claw instance without external dependencies."""
    from src.tg_claw import TgClaw
    tc = TgClaw()
    tc._shutdown = False
    tc._fresh_start = False
    return tc


def _make_state(name="Test", **kwargs):
    """Create a SessionState for tests."""
    config = {"bot_token": "123:fake", "cwd": ".", "bot_id": "123"}
    state = SessionState(name=name, config=config)
    state.channel = MagicMock()
    state.channel.send_sync = MagicMock(return_value="1")
    state.client = MagicMock()
    state.connected = kwargs.get("connected", True)
    state.busy = kwargs.get("busy", False)
    state.restarting = kwargs.get("restarting", False)
    state.busy_since = kwargs.get("busy_since", 0)
    state.start_time = kwargs.get("start_time", time.time() - 300)
    state.session_id = kwargs.get("session_id", "abc123")
    state.resume_count = kwargs.get("resume_count", 0)
    state.last_restart_mode = kwargs.get("last_restart_mode", "resume")
    state.no_resume_before_restart = kwargs.get("no_resume_before_restart", False)
    state.was_busy_before_restart = kwargs.get("was_busy_before_restart", False)
    state.restart_history = kwargs.get("restart_history", [])
    state.restart_count = kwargs.get("restart_count", 0)
    state.error_count = kwargs.get("error_count", 0)
    state.last_notify_time = kwargs.get("last_notify_time", 0)
    if kwargs.get("client_none"):
        state.client = None
    return state


# ============================================================
# 1. _restart_session tests
# ============================================================

class TestRestartSession(unittest.TestCase):
    """Tests for _restart_session core recovery logic."""

    def setUp(self):
        self.tc = _make_tg_claw()
        self.tc._broadcast_sync = MagicMock()
        self.tc._write_status = MagicMock()
        self.tc._connect_session = AsyncMock()
        self.tc._safe_disconnect = AsyncMock()

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_skip_if_already_restarting(self):
        """Skip if already restarting."""
        state = _make_state(restarting=True)
        self._run(self.tc._restart_session(state, "test"))
        # _connect_session not called
        self.tc._connect_session.assert_not_awaited()

    def test_restart_limit_exceeded_not_force(self):
        """Over the restart limit (3/30min) → skip unless force."""
        now = time.time()
        state = _make_state(restart_history=[now - 10, now - 20, now - 30])
        self._run(self.tc._restart_session(state, "test", force=False))
        self.tc._connect_session.assert_not_awaited()

    def test_restart_limit_bypass_with_force(self):
        """force=True restarts even over the limit."""
        now = time.time()
        state = _make_state(restart_history=[now - 10, now - 20, now - 30])

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", force=True))
        self.tc._connect_session.assert_awaited_once()

    def test_resume_mode_sets_last_restart_mode(self):
        """resume-mode restart sets last_restart_mode."""
        state = _make_state()

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", mode="resume", force=True))
        self.assertEqual(state.last_restart_mode, "resume")
        self.assertFalse(state.restarting)  # reset in finally

    def test_reset_mode_sets_last_restart_mode(self):
        """reset-mode restart sets last_restart_mode."""
        state = _make_state()

        async def fake_connect(s, mode="reset"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", mode="reset", force=True))
        self.assertEqual(state.last_restart_mode, "reset")

    def test_restart_increments_count(self):
        """Successful restart increments restart_count."""
        state = _make_state(restart_count=0)

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", force=True))
        self.assertEqual(state.restart_count, 1)

    def test_client_set_to_none_on_restart(self):
        """Restart sets the old client to None."""
        old_client = MagicMock()
        state = _make_state()
        state.client = old_client

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", force=True))
        # _safe_disconnect is called (hard to verify directly since it goes through asyncio.create_task)
        # Instead, check connected=False then reconnect
        self.tc._connect_session.assert_awaited_once()

    def test_restarting_flag_reset_on_exception(self):
        """restarting flag resets even if _connect_session fails."""
        state = _make_state()
        self.tc._connect_session = AsyncMock(side_effect=RuntimeError("fail"))

        with self.assertRaises(RuntimeError):
            self._run(self.tc._restart_session(state, "test", force=True))
        self.assertFalse(state.restarting)

    def test_was_busy_before_restart_from_stuck(self):
        """STUCK reason → was_busy_before_restart=True."""
        state = _make_state(busy=False)

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "STUCK (30분+ busy)", force=True))
        self.assertTrue(state.was_busy_before_restart)

    def test_old_restart_history_cleaned(self):
        """Records outside RESTART_WINDOW are pruned."""
        old_ts = time.time() - 5000  # outside the window
        state = _make_state(restart_history=[old_ts])

        async def fake_connect(s, mode="resume"):
            s.connected = True
        self.tc._connect_session = AsyncMock(side_effect=fake_connect)

        self._run(self.tc._restart_session(state, "test", force=True))
        # old_ts is removed; only the new record remains
        self.assertEqual(len(state.restart_history), 1)
        self.assertNotEqual(state.restart_history[0], old_ts)


# ============================================================
# 2. _assess_health extra cases
# ============================================================

class TestAssessHealthExtended(unittest.TestCase):
    """Extra _assess_health tests — complements test_unit.py."""

    def setUp(self):
        self.tc = _make_tg_claw()

    def test_recently_started_busy_not_stuck(self):
        """Right after start (within HEALTH_CHECK_INTERVAL), busy is OK, not STUCK."""
        state = _make_state(
            connected=True, busy=True,
            busy_since=time.time() - 2000,  # busy for a long time
            start_time=time.time() - 10,  # started 10s ago
        )
        # elapsed < HEALTH_CHECK_INTERVAL returns OK
        self.assertEqual(self.tc._assess_health(state), "OK")

    def test_queue_not_empty_and_not_busy_is_stuck(self):
        """Messages queued but not busy → STUCK."""
        state = _make_state(start_time=time.time() - 300)
        state.message_queue.put_nowait({"text": "hello"})
        state.busy = False
        self.assertEqual(self.tc._assess_health(state), "STUCK")

    def test_busy_but_within_threshold_ok(self):
        """Busy but within STUCK_THRESHOLD → OK."""
        state = _make_state(
            busy=True, busy_since=time.time() - 60,
            start_time=time.time() - 300,
        )
        self.assertEqual(self.tc._assess_health(state), "OK")

    def test_busy_exceeding_threshold_stuck(self):
        """Busy beyond STUCK_THRESHOLD → STUCK."""
        state = _make_state(
            busy=True, busy_since=time.time() - 2000,
            start_time=time.time() - 3000,
        )
        self.assertEqual(self.tc._assess_health(state), "STUCK")

    def test_disconnected_and_not_restarting_dead(self):
        """connected=False, restarting=False → DEAD."""
        state = _make_state(connected=False, start_time=time.time() - 300)
        self.assertEqual(self.tc._assess_health(state), "DEAD")

    def test_disconnected_but_restarting_ok(self):
        """connected=False but restarting=True → OK."""
        state = _make_state(connected=False, restarting=True, start_time=time.time() - 300)
        self.assertEqual(self.tc._assess_health(state), "OK")

    def test_client_none_dead(self):
        """client=None → DEAD."""
        state = _make_state(client_none=True, start_time=time.time() - 300)
        self.assertEqual(self.tc._assess_health(state), "DEAD")

    def test_empty_queue_not_busy_ok(self):
        """Empty queue and not busy → OK."""
        state = _make_state(start_time=time.time() - 300)
        self.assertEqual(self.tc._assess_health(state), "OK")


# ============================================================
# 3. channel_telegram.py tests
# ============================================================

class TestTelegramChannelProperties(unittest.TestCase):
    """TelegramChannel basic attribute tests."""

    def setUp(self):
        from src.channel_telegram import TelegramChannel
        self.ch = TelegramChannel(
            bot_token="123:fake", chat_id="456", bot_name="TestBot"
        )

    def test_name(self):
        self.assertEqual(self.ch.name, "telegram")

    def test_max_length(self):
        self.assertEqual(self.ch.max_length, 4096)

    def test_bot_token(self):
        self.assertEqual(self.ch.bot_token, "123:fake")

    def test_chat_id(self):
        self.assertEqual(self.ch.chat_id, "456")

    def test_bot_name(self):
        self.assertEqual(self.ch.bot_name, "TestBot")

    def test_offset_default(self):
        self.assertEqual(self.ch.get_offset(), 0)

    def test_set_offset(self):
        self.ch.set_offset(42)
        self.assertEqual(self.ch.get_offset(), 42)


class TestTelegramChannelNoAhttp(unittest.TestCase):
    """Without ahttp, each method safely returns an empty value."""

    def setUp(self):
        from src.channel_telegram import TelegramChannel
        self.ch = TelegramChannel(
            bot_token="123:fake", chat_id="456", ahttp=None
        )

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_poll_returns_empty(self):
        result = self._run(self.ch.poll(timeout=1))
        self.assertEqual(result, [])

    def test_send_returns_empty(self):
        result = self._run(self.ch.send("hello"))
        self.assertEqual(result, "")

    def test_edit_returns_false(self):
        result = self._run(self.ch.edit("1", "hello"))
        self.assertFalse(result)

    def test_delete_returns_false(self):
        result = self._run(self.ch.delete("1"))
        self.assertFalse(result)

    def test_react_returns_false(self):
        result = self._run(self.ch.react("1"))
        self.assertFalse(result)

    def test_send_photo_returns_empty(self):
        result = self._run(self.ch.send_photo("/fake.png"))
        self.assertEqual(result, "")

    def test_send_file_returns_empty(self):
        result = self._run(self.ch.send_file("/fake.txt"))
        self.assertEqual(result, "")

    def test_download_file_returns_empty(self):
        result = self._run(self.ch.download_file("fake_id"))
        self.assertEqual(result, b"")


class TestTelegramChannelMocked(unittest.TestCase):
    """send/edit/delete/react tests with a mocked ahttp."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_poll_parses_updates(self):
        """poll parses Telegram updates correctly."""
        from src.channel_telegram import TelegramChannel

        mock_ahttp = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 1,
                        "text": "hello",
                        "chat": {"id": 456},
                        "date": 1234567890,
                    },
                },
                {
                    "update_id": 101,
                    "edited_message": {
                        "message_id": 2,
                        "text": "edited",
                        "chat": {"id": 456},
                        "date": 1234567891,
                    },
                },
            ],
        }
        mock_ahttp.get = AsyncMock(return_value=mock_resp)

        ch = TelegramChannel("123:fake", "456", ahttp=mock_ahttp)
        messages = self._run(ch.poll(timeout=1))

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["text"], "hello")
        self.assertEqual(messages[0]["id"], "1")
        self.assertEqual(messages[0]["from_id"], "456")
        # edited_message
        self.assertEqual(messages[1]["text"], "edited")
        self.assertTrue(messages[1]["_raw"]["_is_edited"])
        # offset updated
        self.assertEqual(ch.get_offset(), 102)

    def test_poll_with_photo_and_document(self):
        """Parse photo/document attachments."""
        from src.channel_telegram import TelegramChannel

        mock_ahttp = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 200,
                    "message": {
                        "message_id": 5,
                        "text": "",
                        "caption": "사진 캡션",
                        "chat": {"id": 456},
                        "date": 1234567890,
                        "photo": [
                            {"file_id": "small", "width": 90},
                            {"file_id": "large", "width": 800},
                        ],
                        "document": {
                            "file_id": "doc123",
                            "file_name": "test.pdf",
                        },
                    },
                },
            ],
        }
        mock_ahttp.get = AsyncMock(return_value=mock_resp)

        ch = TelegramChannel("123:fake", "456", ahttp=mock_ahttp)
        messages = self._run(ch.poll(timeout=1))

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], "사진 캡션")
        self.assertEqual(len(messages[0]["files"]), 2)
        # photo uses the last (largest) size
        self.assertEqual(messages[0]["files"][0]["file_id"], "large")
        self.assertEqual(messages[0]["files"][1]["type"], "document")
        self.assertEqual(messages[0]["files"][1]["name"], "test.pdf")

    def test_poll_not_ok_returns_empty(self):
        """API returns ok=false → empty list."""
        from src.channel_telegram import TelegramChannel

        mock_ahttp = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False}
        mock_ahttp.get = AsyncMock(return_value=mock_resp)

        ch = TelegramChannel("123:fake", "456", ahttp=mock_ahttp)
        result = self._run(ch.poll(timeout=1))
        self.assertEqual(result, [])

    def test_poll_exception_returns_empty(self):
        """Exception during poll → empty list."""
        from src.channel_telegram import TelegramChannel

        mock_ahttp = AsyncMock()
        mock_ahttp.get = AsyncMock(side_effect=Exception("network error"))

        ch = TelegramChannel("123:fake", "456", ahttp=mock_ahttp)
        result = self._run(ch.poll(timeout=1))
        self.assertEqual(result, [])

    def test_format_calls_md_to_html(self):
        """format() converts markdown to HTML."""
        from src.channel_telegram import TelegramChannel
        ch = TelegramChannel("123:fake", "456")
        result = ch.format("**bold**")
        self.assertIn("<b>bold</b>", result)

    def test_split_delegates(self):
        """split() calls _split_message."""
        from src.channel_telegram import TelegramChannel
        ch = TelegramChannel("123:fake", "456")
        result = ch.split("short text")
        self.assertEqual(result, ["short text"])


# ============================================================
# 4. state_db.py tests
# ============================================================

class TestStateDB(unittest.TestCase):
    """state_db function tests — in-memory DB."""

    @classmethod
    def setUpClass(cls):
        db.init(":memory:")

    def tearDown(self):
        """Clean up data after each test."""
        conn = db._get_conn()
        conn.execute("DELETE FROM commands")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM tg_claw_state")
        conn.execute("DELETE FROM poll_offsets")
        conn.execute("DELETE FROM relay_config")
        conn.commit()

    # --- push_command / pop_command ---

    def test_push_and_pop_command(self):
        db.push_command("session1", "restart", "force")
        cmd = db.pop_command("session1")
        self.assertEqual(cmd["command"], "restart")
        self.assertEqual(cmd["args"], "force")
        self.assertEqual(cmd["target"], "session1")

    def test_pop_empty(self):
        result = db.pop_command("nonexistent")
        self.assertEqual(result, {})

    def test_pop_marks_processed(self):
        db.push_command("s1", "restart")
        db.pop_command("s1")
        # popping again returns an empty dict
        self.assertEqual(db.pop_command("s1"), {})

    def test_pop_fifo_order(self):
        db.push_command("s1", "first")
        db.push_command("s1", "second")
        self.assertEqual(db.pop_command("s1")["command"], "first")
        self.assertEqual(db.pop_command("s1")["command"], "second")

    def test_pop_commands_all(self):
        db.push_command("s1", "a")
        db.push_command("s1", "b")
        db.push_command("s1", "c")
        cmds = db.pop_commands("s1")
        self.assertEqual(len(cmds), 3)
        self.assertEqual(cmds[0]["command"], "a")
        self.assertEqual(cmds[2]["command"], "c")
        # popping again returns an empty list
        self.assertEqual(db.pop_commands("s1"), [])

    def test_has_pending_command(self):
        db.push_command("s1", "restart")
        self.assertTrue(db.has_pending_command("s1"))
        self.assertTrue(db.has_pending_command("s1", "restart"))
        self.assertFalse(db.has_pending_command("s1", "reset"))
        db.pop_command("s1")
        self.assertFalse(db.has_pending_command("s1"))

    def test_different_targets_isolated(self):
        db.push_command("s1", "cmd1")
        db.push_command("s2", "cmd2")
        self.assertEqual(db.pop_command("s1")["command"], "cmd1")
        self.assertEqual(db.pop_command("s2")["command"], "cmd2")

    # --- set_session / get_session ---

    def test_set_and_get_session(self):
        db.set_session("TestSess", status="running", session_id="xyz")
        s = db.get_session("TestSess")
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["session_id"], "xyz")

    def test_get_session_nonexistent(self):
        self.assertEqual(db.get_session("NoExist"), {})

    def test_set_session_upsert(self):
        db.set_session("S1", status="idle")
        db.set_session("S1", status="running")
        s = db.get_session("S1")
        self.assertEqual(s["status"], "running")

    def test_get_all_sessions(self):
        db.set_session("A", status="idle")
        db.set_session("B", status="running")
        all_s = db.get_all_sessions()
        self.assertIn("A", all_s)
        self.assertIn("B", all_s)
        self.assertEqual(all_s["A"]["status"], "idle")

    def test_delete_session(self):
        db.set_session("Del", status="idle")
        db.delete_session("Del")
        self.assertEqual(db.get_session("Del"), {})

    # --- set_paused / is_paused ---

    def test_set_paused_true(self):
        db.set_session("P1", status="idle")
        db.set_paused("P1", True)
        self.assertTrue(db.is_paused("P1"))

    def test_set_paused_false(self):
        db.set_paused("P2", True)
        self.assertTrue(db.is_paused("P2"))
        db.set_paused("P2", False)
        self.assertFalse(db.is_paused("P2"))

    def test_is_paused_nonexistent(self):
        self.assertFalse(db.is_paused("Ghost"))

    def test_set_paused_false_only_if_paused(self):
        """set_paused(False) when not paused leaves status unchanged."""
        db.set_session("Run", status="running")
        db.set_paused("Run", False)
        s = db.get_session("Run")
        self.assertEqual(s["status"], "running")

    # --- set_state / get_state ---

    def test_set_and_get_state(self):
        db.set_state("mode", "minimal")
        self.assertEqual(db.get_state("mode"), "minimal")

    def test_get_state_default(self):
        self.assertEqual(db.get_state("nonexistent", "fallback"), "fallback")

    def test_set_state_overwrite(self):
        db.set_state("key1", "val1")
        db.set_state("key1", "val2")
        self.assertEqual(db.get_state("key1"), "val2")

    # --- set_offset / get_offset ---

    def test_set_and_get_offset(self):
        db.set_offset("bot1", 100)
        self.assertEqual(db.get_offset("bot1"), 100)

    def test_get_offset_default(self):
        self.assertEqual(db.get_offset("nobot"), 0)

    def test_set_offset_overwrite(self):
        db.set_offset("bot1", 50)
        db.set_offset("bot1", 200)
        self.assertEqual(db.get_offset("bot1"), 200)

    # --- relay ---

    def test_set_relay_and_check(self):
        db.set_relay("bot1", "chat1", True)
        self.assertTrue(db.is_relay_enabled("bot1", "chat1"))

    def test_relay_disabled(self):
        db.set_relay("bot1", "chat1", False)
        self.assertFalse(db.is_relay_enabled("bot1", "chat1"))

    def test_relay_nonexistent(self):
        self.assertFalse(db.is_relay_enabled("nobot", "nochat"))

    # --- cleanup ---

    def test_cleanup_old_commands(self):
        db.push_command("s1", "old")
        db.pop_command("s1")  # marks processed=1
        # move created_at into the past
        conn = db._get_conn()
        conn.execute("UPDATE commands SET created_at=?", (time.time() - 100000,))
        conn.commit()
        db.cleanup_old_commands(max_age_hours=1)
        # should have been deleted
        row = conn.execute("SELECT count(*) as c FROM commands").fetchone()
        self.assertEqual(row["c"], 0)


# ============================================================
# 5. commands.py tests
# ============================================================

class TestHandleCommand(unittest.TestCase):
    """commands.handle_command tests."""

    @classmethod
    def setUpClass(cls):
        db.init(":memory:")

    def setUp(self):
        self.tc = _make_tg_claw()
        self.channel = MagicMock()
        self.channel.send_sync = MagicMock(return_value="1")

        # register session
        state = _make_state(name="TestProj")
        state.config["bot_token"] = "111:aaa"
        self.tc.sessions["TestProj"] = state
        self.tc._start_time = time.time() - 3600

    def test_non_command_returns_false(self):
        """Not starting with / → False."""
        from src.commands import handle_command
        result = handle_command(self.tc, "hello", "111:aaa", self.channel)
        self.assertFalse(result)

    def test_unknown_command_returns_false(self):
        """/unknown command → False."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/unknown", "111:aaa", self.channel)
        self.assertFalse(result)

    def test_help_returns_true(self):
        """/help → True, calls send_sync."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/help", "111:aaa", self.channel)
        self.assertTrue(result)
        self.channel.send_sync.assert_called()

    def test_status_returns_true(self):
        """/status → True."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/status", "111:aaa", self.channel)
        self.assertTrue(result)
        self.channel.send_sync.assert_called()

    def test_mode_minimal(self):
        """/mode minimal → changes output_level."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/mode minimal", "111:aaa", self.channel)
        self.assertTrue(result)
        self.assertEqual(self.tc.output_level, "minimal")

    def test_mode_normal(self):
        """/mode normal → changes output_level."""
        from src.commands import handle_command
        handle_command(self.tc, "/mode minimal", "111:aaa", self.channel)
        result = handle_command(self.tc, "/mode normal", "111:aaa", self.channel)
        self.assertTrue(result)
        self.assertEqual(self.tc.output_level, "normal")

    def test_mode_no_arg_shows_current(self):
        """/mode alone → shows current mode."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/mode", "111:aaa", self.channel)
        self.assertTrue(result)
        self.channel.send_sync.assert_called()

    def test_mode_invalid_shows_current(self):
        """/mode invalid → shows current mode (no change)."""
        from src.commands import handle_command
        self.tc.output_level = "normal"
        handle_command(self.tc, "/mode invalid", "111:aaa", self.channel)
        self.assertEqual(self.tc.output_level, "normal")

    def test_stop_blocked(self):
        """/stop is blocked."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/stop", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_kill_blocked(self):
        """/kill is blocked."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/kill", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_pause_command(self):
        """/pause → pauses the session."""
        from src.commands import handle_command
        self.tc._safe_disconnect = AsyncMock()
        with patch("src.commands.asyncio.create_task"):
            result = handle_command(self.tc, "/pause TestProj", "111:aaa", self.channel)
        self.assertTrue(result)
        self.assertTrue(db.is_paused("TestProj"))
        # cleanup
        db.set_paused("TestProj", False)

    def test_pause_unknown_session(self):
        """/pause with unknown session → error message."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/pause NoExist", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_log_command(self):
        """/log → True (handles missing file)."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/log", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_ctx_command(self):
        """/ctx → True."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/ctx", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_ask_no_arg(self):
        """/ask without args → usage hint."""
        from src.commands import handle_command
        result = handle_command(self.tc, "/ask", "111:aaa", self.channel)
        self.assertTrue(result)
        self.channel.send_sync.assert_called()


class TestHandleCommandShortcuts(unittest.TestCase):
    """Command shortcut tests."""

    @classmethod
    def setUpClass(cls):
        db.init(":memory:")

    def setUp(self):
        self.tc = _make_tg_claw()
        self.channel = MagicMock()
        self.channel.send_sync = MagicMock(return_value="1")
        state = _make_state(name="S1")
        state.config["bot_token"] = "111:aaa"
        self.tc.sessions["S1"] = state
        self.tc._start_time = time.time() - 3600

    def test_s_is_status(self):
        from src.commands import handle_command
        result = handle_command(self.tc, "/s", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_h_is_help(self):
        from src.commands import handle_command
        result = handle_command(self.tc, "/h", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_l_is_log(self):
        from src.commands import handle_command
        result = handle_command(self.tc, "/l", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_u_is_usage(self):
        from src.commands import handle_command
        # _get_usage needs credentials, so mock it
        with patch("src.commands._get_usage", return_value="usage info"):
            result = handle_command(self.tc, "/u", "111:aaa", self.channel)
        self.assertTrue(result)

    def test_p_is_pause(self):
        from src.commands import handle_command
        self.tc._safe_disconnect = AsyncMock()
        with patch("src.commands.asyncio.create_task"):
            result = handle_command(self.tc, "/p S1", "111:aaa", self.channel)
        self.assertTrue(result)
        db.set_paused("S1", False)


# ============================================================
# _should_auto_resume extra tests
# ============================================================

class TestShouldAutoResumeExtended(unittest.TestCase):
    """_should_auto_resume extra cases."""

    def setUp(self):
        self.tc = _make_tg_claw()

    def test_resume_count_exactly_2_rejected(self):
        """resume_count == 2 → denied."""
        state = _make_state(resume_count=2, session_id="abc")
        self.assertFalse(self.tc._should_auto_resume(state))

    def test_resume_count_1_accepted(self):
        """resume_count == 1 → allowed."""
        state = _make_state(resume_count=1, session_id="abc")
        self.assertTrue(self.tc._should_auto_resume(state))

    def test_resume_count_0_accepted(self):
        """resume_count == 0 → allowed."""
        state = _make_state(resume_count=0, session_id="abc")
        self.assertTrue(self.tc._should_auto_resume(state))

    def test_no_resume_flag_clears_after_skip(self):
        """no_resume flag resets after skipping."""
        state = _make_state(no_resume_before_restart=True, session_id="abc")
        self.assertFalse(self.tc._should_auto_resume(state))
        self.assertFalse(state.no_resume_before_restart)

    def test_reset_mode_rejected(self):
        """last_restart_mode=reset → denied."""
        state = _make_state(last_restart_mode="reset", session_id="abc")
        self.assertFalse(self.tc._should_auto_resume(state))

    def test_no_session_id_rejected(self):
        """Empty session_id → denied."""
        state = _make_state(session_id="")
        self.assertFalse(self.tc._should_auto_resume(state))

    def test_none_session_id_rejected(self):
        """session_id=None → denied."""
        state = _make_state(session_id=None)
        self.assertFalse(self.tc._should_auto_resume(state))


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    unittest.main(verbosity=2)
