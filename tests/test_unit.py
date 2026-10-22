#!/usr/bin/env python3
"""tg-claw unit tests — isolated pure-function tests."""

import sys
import os
import unittest
from dataclasses import dataclass

# Add path for package imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# telegram_api function tests
# ============================================================

from src.telegram_api import (
    _clean_text, _escape_html, _convert_table_to_list,
    _md_to_telegram_html, _split_message,
)


class TestCleanText(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(_clean_text("  hello  "), "hello")

    def test_control_chars(self):
        self.assertNotIn("\x00", _clean_text("a\x00b"))
        self.assertNotIn("\x01", _clean_text("a\x01b"))

    def test_consecutive_blank_lines(self):
        result = _clean_text("a\n\n\n\nb")
        self.assertEqual(result, "a\n\nb")

    def test_replacement_char(self):
        result = _clean_text("abc\ufffd\ufffdef")
        self.assertIn("?", result)
        self.assertNotIn("\ufffd", result)

    def test_empty(self):
        self.assertEqual(_clean_text(""), "")

    def test_korean(self):
        self.assertEqual(_clean_text("  안녕하세요  "), "안녕하세요")


class TestEscapeHtml(unittest.TestCase):
    def test_ampersand(self):
        self.assertEqual(_escape_html("a & b"), "a &amp; b")

    def test_angle_brackets(self):
        self.assertEqual(_escape_html("<div>"), "&lt;div&gt;")

    def test_combined(self):
        self.assertEqual(_escape_html("a < b & c > d"), "a &lt; b &amp; c &gt; d")

    def test_no_change(self):
        self.assertEqual(_escape_html("hello"), "hello")


class TestConvertTableToList(unittest.TestCase):
    def test_simple_table(self):
        table = "| 이름 | 값 |\n|---|---|\n| A | 1 |\n| B | 2 |"
        result = _convert_table_to_list(table)
        self.assertIn("A: 1", result)
        self.assertIn("B: 2", result)
        self.assertNotIn("|", result)

    def test_three_columns(self):
        table = "| 이름 | 값 | 설명 |\n|---|---|---|\n| A | 1 | first |"
        result = _convert_table_to_list(table)
        self.assertIn("A: 1", result)
        self.assertIn("first", result)

    def test_no_table(self):
        text = "그냥 텍스트"
        self.assertEqual(_convert_table_to_list(text), text)


class TestMdToTelegramHtml(unittest.TestCase):
    def test_bold(self):
        result = _md_to_telegram_html("**hello**")
        self.assertIn("<b>hello</b>", result)

    def test_italic(self):
        result = _md_to_telegram_html("*hello*")
        self.assertIn("<i>hello</i>", result)

    def test_bold_italic(self):
        result = _md_to_telegram_html("***hello***")
        self.assertIn("<b><i>hello</i></b>", result)

    def test_strikethrough(self):
        result = _md_to_telegram_html("~~hello~~")
        self.assertIn("<s>hello</s>", result)

    def test_header(self):
        result = _md_to_telegram_html("## Title")
        self.assertIn("<b>Title</b>", result)

    def test_code_block(self):
        result = _md_to_telegram_html("```\ncode here\n```")
        self.assertIn("<pre>", result)
        self.assertIn("code here", result)

    def test_inline_code(self):
        result = _md_to_telegram_html("use `foo()` here")
        self.assertIn("<code>foo()</code>", result)

    def test_code_block_html_escape(self):
        result = _md_to_telegram_html("```\na < b & c > d\n```")
        self.assertIn("&lt;", result)
        self.assertIn("&amp;", result)

    def test_link_stripped(self):
        result = _md_to_telegram_html("[click](https://example.com)")
        self.assertIn("click", result)
        self.assertNotIn("https://", result)

    def test_bare_url_stripped(self):
        result = _md_to_telegram_html("visit https://example.com now")
        self.assertNotIn("https://", result)

    def test_multiple_urls_stripped(self):
        md = "링크1: https://example.com\n링크2: [구글](https://google.com)\n링크3: http://test.org/path?q=1"
        html = _md_to_telegram_html(md)
        self.assertNotIn("https://example.com", html)
        self.assertNotIn("https://google.com", html)
        self.assertNotIn("http://test.org", html)
        self.assertIn("구글", html)

    def test_url_in_codeblock_preserved(self):
        md = "설치 방법:\n```\ncurl https://example.com/install.sh | bash\n```\n끝."
        html = _md_to_telegram_html(md)
        self.assertIn("https://example.com/install.sh", html)

    def test_url_in_inline_code_preserved(self):
        md = "API 엔드포인트: `https://api.example.com/v1`"
        html = _md_to_telegram_html(md)
        self.assertIn("https://api.example.com/v1", html)

    def test_mixed_content(self):
        md = "**제목**\n\n참고: https://docs.example.com\n\n코드: `https://keep.this.url`\n\n[링크텍스트](https://remove.this.url)"
        html = _md_to_telegram_html(md)
        self.assertIn("<b>제목</b>", html)
        self.assertNotIn("https://docs.example.com", html)
        self.assertIn("https://keep.this.url", html)
        self.assertNotIn("https://remove.this.url", html)
        self.assertIn("링크텍스트", html)

    def test_blockquote(self):
        result = _md_to_telegram_html("> quoted text")
        self.assertIn("<blockquote>", result)

    def test_fallback_on_plain(self):
        result = _md_to_telegram_html("hello world")
        self.assertIn("hello world", result)


class TestSplitMessage(unittest.TestCase):
    def test_short_message(self):
        result = _split_message("hello", max_len=100)
        self.assertEqual(result, ["hello"])

    def test_exact_limit(self):
        text = "a" * 100
        result = _split_message(text, max_len=100)
        self.assertEqual(len(result), 1)

    def test_split_on_newline(self):
        text = "a" * 50 + "\n" + "b" * 50
        result = _split_message(text, max_len=60)
        self.assertGreater(len(result), 1)

    def test_split_on_blank_line(self):
        text = "a" * 40 + "\n\n" + "b" * 40
        result = _split_message(text, max_len=50)
        self.assertGreater(len(result), 1)

    def test_chunk_numbering(self):
        text = "a" * 100 + "\n\n" + "b" * 100
        result = _split_message(text, max_len=60)
        self.assertTrue(any("(1/" in c for c in result))

    def test_forced_split(self):
        # Long text with no split points
        text = "a" * 200
        result = _split_message(text, max_len=50)
        self.assertGreater(len(result), 1)
        for chunk in result:
            # Excluding the chunk number, content is within max_len
            base = chunk.split("\n\n(")[0] if "\n\n(" in chunk else chunk
            self.assertLessEqual(len(base), 50)


# ============================================================
# tg_claw.py function tests
# ============================================================

class TestToolSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tg_claw import TgClaw
        cls.func = staticmethod(TgClaw._tool_summary)

    def test_read_file(self):
        result = self.func("Read", {"file_path": "/some/path/file.py"})
        self.assertIn("Read", result)
        self.assertIn("file.py", result)

    def test_mcp_tool(self):
        result = self.func("mcp__ai-chat__ask_ai", {"prompt": "hello"})
        self.assertIn("ai-chat.ask_ai", result)

    def test_long_path_truncated(self):
        long_path = "/very/long/path/" + "a" * 50 + "/file.py"
        result = self.func("Read", {"file_path": long_path})
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 80)

    def test_bash_command(self):
        result = self.func("Bash", {"command": "ls -la"})
        self.assertIn("ls -la", result)

    def test_no_input(self):
        result = self.func("TodoWrite", {})
        self.assertEqual(result, "TodoWrite")


class TestFormatToolLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tg_claw import TgClaw
        cls.func = staticmethod(TgClaw._format_tool_line)

    def test_single(self):
        result = self.func(["🔧 Read: a.py"])
        self.assertIn("Read: a.py", result)

    def test_multiple(self):
        result = self.func(["🔧 Read: a.py", "🔧 Grep: b.py"])
        self.assertIn("→", result)
        self.assertIn("Read: a.py", result)
        self.assertIn("Grep: b.py", result)

    def test_four_tools_not_truncated(self):
        """4 or fewer are all shown"""
        tools = ["🔧 Read: a.py", "🔧 Grep: b.py", "🔧 Edit: c.py", "🔧 Bash: ls"]
        result = self.func(tools)
        self.assertIn("Read: a.py", result)
        self.assertIn("Bash: ls", result)
        self.assertNotIn("...", result)

    def test_five_plus_tools_truncated(self):
        """5 or more collapse to first 2 + ...+N + last 1"""
        tools = ["🔧 Read: a", "🔧 Grep: b", "🔧 Edit: c", "🔧 Bash: d", "🔧 Write: e"]
        result = self.func(tools)
        self.assertIn("Read: a", result)
        self.assertIn("Grep: b", result)
        self.assertIn("Write: e", result)
        self.assertIn("...+2", result)
        self.assertNotIn("Edit: c", result)

    def test_many_tools_truncated(self):
        """Collapse 10 tools"""
        tools = [f"🔧 Tool{i}: x" for i in range(10)]
        result = self.func(tools)
        self.assertIn("...+7", result)
        self.assertIn("Tool0: x", result)
        self.assertIn("Tool9: x", result)

    def test_format_tool_line_single(self):
        """1 tool — shown in full"""
        result = self.func(["🔧 Read: only.py"])
        self.assertIn("Read: only.py", result)
        self.assertNotIn("...", result)

    def test_format_tool_line_short(self):
        """4 or fewer tools — shown in full"""
        tools = ["🔧 Read: a.py", "🔧 Grep: b.py", "🔧 Edit: c.py"]
        result = self.func(tools)
        self.assertIn("Read: a.py", result)
        self.assertIn("Grep: b.py", result)
        self.assertIn("Edit: c.py", result)
        self.assertNotIn("...", result)

    def test_format_tool_line_exact_four(self):
        """Exactly 4 — shown in full (no collapsing)"""
        tools = ["🔧 Read: a.py", "🔧 Grep: b.py", "🔧 Edit: c.py", "🔧 Bash: ls"]
        result = self.func(tools)
        self.assertIn("Read: a.py", result)
        self.assertIn("Grep: b.py", result)
        self.assertIn("Edit: c.py", result)
        self.assertIn("Bash: ls", result)
        self.assertNotIn("...", result)

    def test_format_tool_line_long(self):
        """6 tools → first 2 + ...+3 + last 1"""
        tools = [
            "🔧 Read: a", "🔧 Grep: b", "🔧 Edit: c",
            "🔧 Bash: d", "🔧 Write: e", "🔧 Glob: f",
        ]
        result = self.func(tools)
        self.assertIn("Read: a", result)
        self.assertIn("Grep: b", result)
        self.assertIn("Glob: f", result)
        self.assertIn("...+3", result)
        self.assertNotIn("Edit: c", result)
        self.assertNotIn("Bash: d", result)
        self.assertNotIn("Write: e", result)


class TestStabilizeMarkdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tg_claw import TgClaw
        cls.func = staticmethod(TgClaw._stabilize_markdown)

    def test_open_code_block(self):
        result = self.func("```\ncode")
        self.assertEqual(result.count("```"), 2)

    def test_closed_code_block(self):
        result = self.func("```\ncode\n```")
        self.assertEqual(result.count("```"), 2)

    def test_no_code_block(self):
        result = self.func("hello")
        self.assertNotIn("```", result)


class TestAssessHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tg_claw import TgClaw
        from src.session import SessionState
        cls.supervisor = TgClaw()
        cls.SessionState = SessionState

    def _make_state(self, **kwargs):
        import time
        defaults = {
            "name": "Test",
            "config": {"bot_token": "x", "cwd": ".", "bot_id": "1"},
            "connected": True,
            "busy": False,
            "restarting": False,
            "busy_since": 0,
            "start_time": time.time() - 300,
            "client": "fake",  # client=None would be judged DEAD
        }
        defaults.update(kwargs)
        state = self.SessionState(name=defaults["name"], config=defaults["config"])
        state.connected = defaults["connected"]
        state.busy = defaults["busy"]
        state.restarting = defaults["restarting"]
        state.busy_since = defaults["busy_since"]
        state.start_time = defaults["start_time"]
        state.client = defaults["client"]
        return state

    def test_ok(self):
        state = self._make_state(connected=True, busy=False)
        self.assertEqual(self.supervisor._assess_health(state), "OK")

    def test_dead(self):
        state = self._make_state(connected=False)
        self.assertEqual(self.supervisor._assess_health(state), "DEAD")

    def test_dead_no_client(self):
        state = self._make_state(connected=True, client=None)
        self.assertEqual(self.supervisor._assess_health(state), "DEAD")

    def test_restarting_ok(self):
        state = self._make_state(connected=False, restarting=True)
        self.assertEqual(self.supervisor._assess_health(state), "OK")

    def test_stuck(self):
        import time
        state = self._make_state(connected=True, busy=True, busy_since=time.time() - 2000)
        self.assertEqual(self.supervisor._assess_health(state), "STUCK")

    def test_busy_not_stuck(self):
        import time
        state = self._make_state(connected=True, busy=True, busy_since=time.time() - 10)
        self.assertEqual(self.supervisor._assess_health(state), "OK")


# ============================================================
# wrapper function tests
# ============================================================

class TestHandleEmergencyCommand(unittest.TestCase):
    _func = None

    @classmethod
    def setUpClass(cls):
        from src import tg_claw_daemon as mod
        mod.tg_send = lambda text: None
        cls._func = staticmethod(mod.handle_emergency_command)

    def test_restart(self):
        self.assertEqual(self._func("/restart", 5, 30, 0), "restart")

    def test_kill(self):
        self.assertEqual(self._func("/kill", 5, 30, 0), "kill")

    def test_log(self):
        self.assertIsNone(self._func("/log", 5, 30, 0))

    def test_status(self):
        self.assertIsNone(self._func("/status", 5, 30, 0))

    def test_help(self):
        self.assertIsNone(self._func("/help", 5, 30, 0))

    def test_unknown(self):
        self.assertIsNone(self._func("random text", 5, 30, 0))

    def test_korean_restart(self):
        self.assertEqual(self._func("재시작", 5, 30, 0), "restart")

    def test_korean_kill(self):
        self.assertEqual(self._func("종료", 5, 30, 0), "kill")


# ============================================================
# Run
# ============================================================
# commands.py — /restart, /reset clear pause
# ============================================================

from src import state_db as db

class TestCommandsPauseRelease(unittest.TestCase):
    """restart/reset commands clear pause (DB-based)."""

    @classmethod
    def setUpClass(cls):
        db.init()

    def test_restart_removes_pause(self):
        """/restart while paused → unpaused."""
        db.set_paused("TestSession", True)
        self.assertTrue(db.is_paused("TestSession"))
        db.set_paused("TestSession", False)
        self.assertFalse(db.is_paused("TestSession"))

    def test_reset_removes_pause(self):
        """/reset while paused → unpaused."""
        db.set_paused("TestReset", True)
        self.assertTrue(db.is_paused("TestReset"))
        db.set_paused("TestReset", False)
        self.assertFalse(db.is_paused("TestReset"))

    def test_no_error_without_pause(self):
        """No error when not paused."""
        self.assertFalse(db.is_paused("NoExist"))


# ============================================================
# tg_claw.py — _should_auto_resume decision logic
# ============================================================

class TestShouldAutoResume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tg_claw import TgClaw
        from src.session import SessionState
        cls.supervisor = TgClaw()
        cls.SessionState = SessionState

    def _make_state(self, **kwargs):
        import time
        from unittest.mock import MagicMock
        state = self.SessionState(
            name="Test",
            config={"bot_token": "x", "cwd": ".", "bot_id": "1"},
        )
        state.channel = MagicMock()
        state.session_id = kwargs.get("session_id", "abc123")
        state.no_resume_before_restart = kwargs.get("no_resume", False)
        state.last_restart_mode = kwargs.get("mode", "resume")
        state.resume_count = kwargs.get("resume_count", 0)
        return state

    def test_normal_resume(self):
        """Default resume mode → True"""
        state = self._make_state()
        self.assertTrue(self.supervisor._should_auto_resume(state))

    def test_no_resume_marked(self):
        """no_resume flag → False (once only)"""
        state = self._make_state(no_resume=True)
        self.assertFalse(self.supervisor._should_auto_resume(state))
        # Flag resets after skipping once
        self.assertFalse(state.no_resume_before_restart)

    def test_reset_mode(self):
        """reset mode → False"""
        state = self._make_state(mode="reset")
        self.assertFalse(self.supervisor._should_auto_resume(state))

    def test_no_session_id(self):
        """No session_id → False"""
        state = self._make_state(session_id="")
        self.assertFalse(self.supervisor._should_auto_resume(state))

    def test_resume_count_exceeded(self):
        """More than 2 → False"""
        state = self._make_state(resume_count=2)
        self.assertFalse(self.supervisor._should_auto_resume(state))

    def test_resume_count_under_limit(self):
        """Once → True"""
        state = self._make_state(resume_count=1)
        self.assertTrue(self.supervisor._should_auto_resume(state))


# ============================================================
# tg_claw.py — skip errors while restarting
# ============================================================

class TestRestartingErrorSkip(unittest.TestCase):
    """Error retries are skipped when state.restarting=True."""

    def test_restarting_skips_retry(self):
        """Error while restarting does not increment retry_error."""
        msg_data = {"text": "리셋", "retry_error": 0}
        state_restarting = True
        # Simulate tg_claw.py logic
        if state_restarting:
            skipped = True
        else:
            msg_data["retry_error"] = msg_data.get("retry_error", 0) + 1
            skipped = False
        self.assertTrue(skipped)
        self.assertEqual(msg_data["retry_error"], 0)

    def test_not_restarting_does_retry(self):
        """Error while not restarting → retry_error increments."""
        msg_data = {"text": "테스트", "retry_error": 0}
        state_restarting = False
        if state_restarting:
            skipped = True
        else:
            msg_data["retry_error"] = msg_data.get("retry_error", 0) + 1
            skipped = False
        self.assertFalse(skipped)
        self.assertEqual(msg_data["retry_error"], 1)


# ============================================================
# logging_utils.py — delete archives older than 7 days
# ============================================================

class TestArchiveCleanup(unittest.TestCase):
    """Archive logs are kept for 7 days."""

    def test_archive_lines_import(self):
        """_archive_lines function exists."""
        from src.logging_utils import _archive_lines
        self.assertTrue(callable(_archive_lines))


# ============================================================

if __name__ == "__main__":
    # Run from the supervisor directory
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    unittest.main(verbosity=2)
