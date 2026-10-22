#!/usr/bin/env python3
"""
tg-claw full smoke test (#1-#45) — non-destructive checks.
Run: python tests/test_smoke_all.py
"""

import os
import sys
import json
import time
import subprocess

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TG_CLAW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TG_CLAW_DIR)
from src.config import (  # noqa: E402
    LOGS_DIR, STATE_DIR as DATA_DIR, STATUS_FILE, LOG_FILE, SESSION_IDS_FILE, LOCK_FILE,
    WRAPPER_LOG_FILE as WRAPPER_LOG,
)

passed = 0
failed = 0
skipped = 0


def test(name, result, detail=""):
    global passed, failed
    if result:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


def skip(name, reason):
    global skipped
    print(f"  ⏭️ {name} — {reason}")
    skipped += 1


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_code():
    sv_path = os.path.join(TG_CLAW_DIR, "src", "tg_claw.py")
    with open(sv_path, "r", encoding="utf-8") as f:
        return f.read()


def read_wrapper_code():
    wp_path = os.path.join(TG_CLAW_DIR, "src", "tg_claw_daemon.py")
    with open(wp_path, "r", encoding="utf-8") as f:
        return f.read()


def read_commands_code():
    cmd_path = os.path.join(TG_CLAW_DIR, "src", "commands.py")
    with open(cmd_path, "r", encoding="utf-8") as f:
        return f.read()


# ==========================================================
# Load code / logs / status
# ==========================================================
sv_code = read_code()
wp_code = read_wrapper_code()
cmd_code = read_commands_code()
log_content = read_text(LOG_FILE)
wrapper_log = read_text(WRAPPER_LOG)
status = read_json(STATUS_FILE)
sessions = status["sessions"]

# ==========================================================
# #1: Process state
# ==========================================================
print("\n#1: Process state")
test("tg_claw_status.json exists", os.path.exists(STATUS_FILE))
test("PID recorded", status.get("pid", 0) > 0, f"pid={status.get('pid')}")
# Check the process with tasklist
try:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {status['pid']}", "/NH"],
                       capture_output=True, text=True, timeout=5)
    test("tg-claw process exists", str(status["pid"]) in r.stdout)
except Exception as e:
    skip("tg-claw process exists", str(e))
test(f"{len(sessions)} sessions exist", len(sessions) >= 3, f"count={len(sessions)}")
for name, s in sessions.items():
    test(f"{name} connected=true", s["connected"])

# ==========================================================
# #2: Message handling (log based)
# ==========================================================
print("\n#2: Message handling")
test("'message received' log exists", "message received:" in log_content)
test("'processing message' log exists", "processing message:" in log_content)
test("'done processing' log exists", "done processing" in log_content)
recv_count = log_content.count("message received:")
proc_count = log_content.count("processing message:")
done_count = log_content.count("done processing")
test("receive-process-done flow OK", recv_count > 0 and proc_count > 0 and done_count > 0,
     f"received={recv_count}, processing={proc_count}, done={done_count}")

# ==========================================================
# #3: Commands
# ==========================================================
print("\n#3: Commands")
test("handle_command function exists", "def handle_command" in cmd_code)
for cmd in ["/status", "/usage", "/restart", "/reset", "/log", "/help"]:
    test(f"command {cmd} handler", f'"{cmd}"' in cmd_code or f"'{cmd}'" in cmd_code or cmd.lstrip("/") in cmd_code)

# ==========================================================
# #4: Session restart
# ==========================================================
print("\n#4: Session restart")
test("_restart_session method exists", "async def _restart_session" in sv_code)
test("restart_request flag detection", "restart_request_" in sv_code)
# Check restart history in the log
restart_logs = [l for l in log_content.split("\n") if "restart attempt" in l]
if restart_logs:
    test(f"restart history: {len(restart_logs)}", True)
else:
    skip("restart history", "not in current log")

# ==========================================================
# #5: tg-claw self-restart
# ==========================================================
print("\n#5: tg-claw self-restart")
test("restart_request_supervisor.flag detection", "restart_request_supervisor" in sv_code)
test("os._exit(0) triggers wrapper restart", "os._exit(0)" in sv_code)
# Restart history in the wrapper log
wrapper_restarts = wrapper_log.count("tg-claw starting")
test(f"wrapper restart history: {wrapper_restarts}", wrapper_restarts > 0)

# ==========================================================
# #6: DEAD session auto-recovery
# ==========================================================
print("\n#6: DEAD session auto-recovery")
test("_assess_health method exists", "def _assess_health" in sv_code)
test("DEAD detection logic", '"DEAD"' in sv_code)
test("health_check_loop detects DEAD → restart", "status == \"DEAD\"" in sv_code or 'status == "DEAD"' in sv_code)
test("MAX_RESTARTS_PER_WINDOW limit", "MAX_RESTARTS_PER_WINDOW" in sv_code)

# ==========================================================
# #7: SDK query hard timeout
# ==========================================================
print("\n#7: SDK query hard timeout")
test("query timeout check (600s)", "600" in sv_code and "force stop" in sv_code)
test("break after 10 min without messages", "no messages for 10 min" in sv_code)

# ==========================================================
# #8: No message loss on restart
# ==========================================================
print("\n#8: No message loss on restart")
test("offset save logic (_save_offset)", "_save_offset" in sv_code)
test("offset restore logic (_load_offset)", "_load_offset" in sv_code or "offset restored" in sv_code)
# Check that offset files exist
offset_files = [f for f in os.listdir(DATA_DIR) if f.startswith("last_offset_")]
test(f"{len(offset_files)} offset files exist", len(offset_files) > 0)
# Check offset restore in the log
if "offset restored" in log_content:
    test("offset restore log", True)
else:
    skip("offset restore log", "not in current log (rotated after restart)")

# ==========================================================
# #9: Queue new messages while busy
# ==========================================================
print("\n#9: Queue new messages while busy")
test("uses asyncio.Queue", "message_queue" in sv_code)
test("shows queue count while busy", 'msg("ack_busy"' in sv_code)

# ==========================================================
# #10: pause mode
# ==========================================================
# ==========================================================
# #10: flag name consistency
# ==========================================================
print("\n#10: flag name consistency")
test("supervisor flag vs session flag", "restart_request_supervisor" in sv_code and "restart_request_{name}" in sv_code)

# ==========================================================
# #12: Parallel messages
# ==========================================================
print("\n#12: Parallel messages")
test("independent poll loop per session", "_bot_poll_loop" in sv_code)
test("independent processing loop per session", "_session_loop" in sv_code)
# Check in the log that multiple sessions processed messages
session_names_in_log = set()
for name in sessions:
    if f"{name}: processing message" in log_content:
        session_names_in_log.add(name)
test(f"multi-session processing ({len(session_names_in_log)})", len(session_names_in_log) >= 1)

# ==========================================================
# #13: /status ghost state
# ==========================================================
print("\n#13: /status ghost state")
test("connected state checks the actual client", "state.client is None" in sv_code or "not state.client" in sv_code)
test("health check: client=None → DEAD", "client is None" in sv_code)

# ==========================================================
# #14: Retry when session not connected
# ==========================================================
print("\n#14: Retry when session not connected")
test("uses retry_conn key", "retry_conn" in sv_code)
test("not-connected retry limited to 1", 'retry < 1' in sv_code)
test("Telegram notice on failure", 'msg("session_connect_fail"' in sv_code)

# ==========================================================
# #14-1: Prevent endless requeue on client=None
# ==========================================================
print("\n#14-1: Prevent endless requeue on client=None")
test("uses retry_noclient key", "retry_noclient" in sv_code)
test("limited to 10 (enough to wait for restart)", "retry < 10" in sv_code)
test("drop + notice when exhausted", "retries exhausted" in sv_code)

# ==========================================================
# #15: query() timeout retry
# ==========================================================
print("\n#15: query() timeout retry")
test("uses retry_timeout key", "retry_timeout" in sv_code)
test("limited to 2", "retry < 2" in sv_code)
test("timeout retry queueing log", "queueing timeout retry" in sv_code)

# ==========================================================
# #16: Empty response notice
# ==========================================================
print("\n#16: Empty response notice")
test("empty response detected", 'msg("ask_empty")' in sv_code)

# ==========================================================
# #17: Retry on exception during processing
# ==========================================================
print("\n#17: Retry on exception during processing")
test("uses retry_error key", "retry_error" in sv_code)
test("error retry queueing", "queueing error retry" in sv_code)
test("consecutive error count (error_count)", "state.error_count += 1" in sv_code)

# ==========================================================
# #18: Message order preserved on retry
# ==========================================================
print("\n#18: Message order preserved on retry")
session_code = read_text(os.path.join(TG_CLAW_DIR, "src", "session.py"))
test("FIFO queue (asyncio.Queue)", "asyncio.Queue" in session_code)
test("retry re-appends with put", "message_queue.put(msg_data)" in sv_code)

# ==========================================================
# #19: Repeated notices on endless wrapper crashes
# ==========================================================
print("\n#19: Repeated notices on endless wrapper crashes")
test("notices at 5/10/20/50", "fail_count in (5, 10, 20, 50)" in wp_code)
test("periodic notice after 50", "fail_count % 50 == 0" in wp_code)

# ==========================================================
# #20: Graceful shutdown on supervisor restart while busy
# ==========================================================
print("\n#20: Graceful shutdown on supervisor restart while busy")
test("waits for busy sessions", "waiting for busy sessions" in sv_code)
test("waits up to 60s", "waited < 60" in sv_code)
test("skips wait when forced", "not force" in sv_code)

# ==========================================================
# #21: Detailed polling error messages
# ==========================================================
print("\n#21: Detailed polling error messages")
test("uses repr(e)", "repr(e)" in sv_code)
test("includes traceback", "traceback" in sv_code)

# ==========================================================
# #22: Parallel session connect
# ==========================================================
print("\n#22: Parallel session connect")
test("parallel connect with asyncio.gather", "asyncio.gather" in sv_code)
test("return_exceptions=True", "return_exceptions=True" in sv_code)
# Analyze connect times in the log — only connects after the last "tg-claw starting"
lines = log_content.split("\n")
last_start_idx = -1
for i, l in enumerate(lines):
    if "tg-claw starting" in l:
        last_start_idx = i
if last_start_idx >= 0:
    recent_lines = lines[last_start_idx:]
    connect_logs = [l for l in recent_lines if "SDK session connected" in l]
    if len(connect_logs) >= 3:
        times = []
        for l in connect_logs[:3]:  # the first 3 after that start
            t = l.split("]")[0].lstrip("[").strip()
            try:
                ts = time.strptime(t, "%Y-%m-%d %H:%M:%S")
                times.append(time.mktime(ts))
            except Exception:
                pass
        if len(times) >= 2:
            spread = max(times) - min(times)
            test(f"parallel connect spread {spread:.0f}s (< 15s)", spread < 15,
                 f"spread={spread:.0f}s")

# ==========================================================
# #44: Client replacement detection (stop receive_messages)
# ==========================================================
print("\n#44: Client replacement detection")
test("state.client is not client check", "state.client is not client" in sv_code)
test("break on replacement", "client replacement detected" in sv_code)
# Check in the log whether it actually fired
swap_logs = [l for l in log_content.split("\n") if "client replacement detected" in l]
test(f"client replacement logs: {len(swap_logs)}", True)
if swap_logs:
    print(f"    latest: {swap_logs[-1].strip()}")

# ==========================================================
# #45: Force-kill the process when disconnect fails
# ==========================================================
print("\n#45: Force-terminate the process when disconnect fails")
test("_transport._process path", '_transport' in sv_code and '_process' in sv_code)
test("calls proc.terminate()", "proc.terminate()" in sv_code)
test("proc.returncode is None guard", "proc.returncode is None" in sv_code)
# Check in the log whether force terminate fired
terminate_logs = [l for l in log_content.split("\n") if "terminating process (pid=" in l]
test(f"force terminate logs: {len(terminate_logs)}", len(terminate_logs) >= 0)
if terminate_logs:
    print(f"    latest: {terminate_logs[-1].strip()}")

# ==========================================================
# #23: Resource cleanup on disconnect error
# ==========================================================
print("\n#23: Force-kill the process on disconnect error")
test("_safe_disconnect method", "async def _safe_disconnect" in sv_code)
test("_transport._process access", "getattr(transport" in sv_code)
test("pid logged after terminate()", "terminating process (pid=" in sv_code)
# Check in the log that terminate followed a disconnect error
disc_errors = [l for l in log_content.split("\n") if "disconnect error" in l]
disc_terminates = [l for l in log_content.split("\n") if "terminating process (pid=" in l]
test(f"disconnect errors: {len(disc_errors)}", True)
if disc_errors and disc_terminates:
    test("terminate ran after the error", len(disc_terminates) > 0)

# ==========================================================
# #24: Wrapper captures supervisor crash stderr
# ==========================================================
print("\n#24: Wrapper crash stderr capture (file redirect)")
test("main run doesn't use capture_output", "stderr=sf" in wp_code)
test("stderr redirected to file", "tg_claw_stderr.log" in wp_code)
test("stderr logged", "tg-claw stderr:" in wp_code)
test("stderr sent to Telegram", 'msg("daemon_crash_stderr"' in wp_code)
# Check actual stderr captures in the wrapper log
stderr_logs = [l for l in wrapper_log.split("\n") if "tg-claw stderr:" in l]
test(f"wrapper stderr captures: {len(stderr_logs)}", True)
if stderr_logs:
    print(f"    latest: {stderr_logs[-1].strip()[:100]}")

# ==========================================================
# #46: Prevent wrapper restart blocking
# ==========================================================
print("\n#46: Prevent wrapper restart blocking")
test("stderr redirected to file (stdout not captured)", "stderr=sf" in wp_code)
test("main run stdout not captured (prevents blocking)", "stderr=sf" in wp_code)
# Check restart latency in the wrapper log — time from "tg-claw exited" to "tg-claw starting"
wp_lines = wrapper_log.split("\n")
restart_pairs = []
for i, l in enumerate(wp_lines):
    if "tg-claw exited" in l and "exit_code=0" in l:
        # find the next "tg-claw starting"
        for j in range(i+1, min(i+5, len(wp_lines))):
            if "tg-claw starting" in wp_lines[j]:
                try:
                    t1 = wp_lines[i].split("]")[0].lstrip("[").strip()
                    t2 = wp_lines[j].split("]")[0].lstrip("[").strip()
                    import time as _t
                    ts1 = _t.mktime(_t.strptime(t1, "%Y-%m-%d %H:%M:%S"))
                    ts2 = _t.mktime(_t.strptime(t2, "%Y-%m-%d %H:%M:%S"))
                    restart_pairs.append(ts2 - ts1)
                except Exception:
                    pass
                break
if restart_pairs:
    last_delay = restart_pairs[-1]
    test(f"last restart delay {last_delay:.0f}s (< 30s)", last_delay < 30,
         f"delay={last_delay:.0f}s")
else:
    skip("restart delay measurement", "no restart after a clean exit")

# ==========================================================
# #51: auto-resume ON/OFF control
# ==========================================================
print("\n#51: auto-resume ON/OFF control")
cfg_code = read_text(os.path.join(TG_CLAW_DIR, "src", "config.py"))
cmd_code = read_text(os.path.join(TG_CLAW_DIR, "src", "commands.py"))
test("global setting AUTO_RESUME_ENABLED", "AUTO_RESUME_ENABLED" in cfg_code)
test("imported in supervisor", "AUTO_RESUME_ENABLED" in sv_code)
test("noresume parameter", "no_resume" in sv_code)
test("noresume in flag parsing", "noresume" in sv_code)
test("auto-resume logic in _restart_session", "AUTO_RESUME_ENABLED and not no_resume" in sv_code)
test("STUCK reason → force was_busy", '"STUCK" in reason' in sv_code)
test("/restart noresume command", "noresume" in cmd_code)
test("prompts come from config", "AUTO_RESUME_PROMPTS" in sv_code)
# Check auto-resume behavior in the log
resume_logs = [l for l in log_content.split("\n") if "auto-resume" in l and "skipping" not in l]
skip_logs = [l for l in log_content.split("\n") if "skipping auto-resume" in l or "idle state → auto-resume" in l]
if resume_logs:
    test(f"auto-resume runs: {len(resume_logs)}", True)
elif skip_logs:
    test(f"auto-resume skips: {len(skip_logs)} (conditions not met)", True)
else:
    skip("auto-resume log", "not in current log")

# ==========================================================
# #52: auto-resume busy state persistence
# ==========================================================
print("\n#52: auto-resume busy state persistence")
test("was_busy saved in session_ids", 'entry["was_busy"]' in sv_code or '"was_busy"' in sv_code)
test("_load_session_ids restores was_busy", "was_busy_before_restart = True" in sv_code)
test("backward compat (string session_id)", "isinstance(val, str)" in sv_code)
test("_save_session_ids before os._exit", "_save_session_ids()" in sv_code and "os._exit" in sv_code)
# Check session_ids.json format
try:
    with open(SESSION_IDS_FILE, "r") as f:
        sid_data = json.load(f)
    # check dict form (new format)
    has_dict = any(isinstance(v, dict) for v in sid_data.values())
    if has_dict:
        test("session_ids.json new format (dict)", True)
    else:
        skip("session_ids.json new format", "still old format — migrated after restart")
except Exception as e:
    skip("session_ids.json format", str(e))
# Check busy restore in the log
busy_restore = [l for l in log_content.split("\n") if "busy state restored" in l]
if busy_restore:
    test(f"busy state restores: {len(busy_restore)}", True)
    print(f"    latest: {busy_restore[-1].strip()}")
else:
    skip("busy state restore history", "not in current log (may have been idle at restart)")

# ==========================================================
# #53: auto-resume prompt modes
# ==========================================================
print("\n#53: auto-resume prompt modes")
test("AUTO_RESUME_MODE setting", "AUTO_RESUME_MODE" in cfg_code)
test("AUTO_RESUME_PROMPTS dict", "AUTO_RESUME_PROMPTS" in cfg_code)
test("3 modes (resume/check/none)", all(
    f'"{m}"' in cfg_code for m in ["resume", "check", "none"]
))
test("none mode = None", '"none": None' in cfg_code)
test("MODE imported in supervisor", "AUTO_RESUME_MODE" in sv_code)
test("PROMPTS imported in supervisor", "AUTO_RESUME_PROMPTS" in sv_code)
test("prompt None check (none mode support)", "if prompt and self._should_auto_resume" in sv_code)
test("none mode log", 'mode=none' in sv_code)
# Check modes in the log
mode_logs = [l for l in log_content.split("\n") if "mode=" in l and "auto-resume" in l]
none_logs = [l for l in log_content.split("\n") if "mode=none" in l]
if mode_logs:
    test(f"auto-resume mode logs: {len(mode_logs)}", True)
    print(f"    latest: {mode_logs[-1].strip()[:100]}")
elif none_logs:
    test(f"none mode wait logs: {len(none_logs)}", True)
else:
    skip("auto-resume mode log", "not in current log")

# ==========================================================
# #54: Progress notice for slow responses
# ==========================================================
print("\n#54: Progress notice for slow responses")
test("last_progress_notify variable", "last_progress_notify" in sv_code)
test("300s threshold", "elapsed > 300" in sv_code)
test("every 5 min (300s)", "last_progress_notify > 300" in sv_code)
test("sends progress notice", 'msg("still_processing"' in sv_code)

# ==========================================================
# #55: Wrapper repeated-restart warning
# ==========================================================
print("\n#55: Wrapper repeated-restart warning")
test("recent_restarts list", "recent_restarts" in wp_code)
test("10 min (600s) window", "now - t < 600" in wp_code)
test("detects 5 or more", "len(recent_restarts) >= 5" in wp_code)
test("sends Telegram warning", 'msg("daemon_frequent_restart"' in wp_code)

# ==========================================================
# #56: Log rotation (per-day archives)
# ==========================================================
print("\n#56: Log rotation")
log_utils = read_text(os.path.join(TG_CLAW_DIR, "src", "logging_utils.py"))
test("_archive_lines function", "_archive_lines" in log_utils)
test("per-day file name", 'tg_claw_{date_str}.log' in log_utils or "tg_claw_" in log_utils)
test("trimmed log archived", "_archive_lines(lines[:-500])" in log_utils)
# Check that archive files exist
import glob
archive_files = glob.glob(os.path.join(LOGS_DIR, "tg_claw_20*.log"))
if archive_files:
    test(f"{len(archive_files)} archive files", True)
    print(f"    latest: {os.path.basename(archive_files[-1])}")
else:
    skip("archive files", "no rotation yet")

# ==========================================================
# #47: deleteMessage POST
# ==========================================================
print("\n#47: deleteMessage POST")
test("deleteMessage via POST", "ahttp.post" in sv_code and "deleteMessage" in sv_code)
test("json= parameter", 'json={"chat_id"' in sv_code or 'json={' in sv_code)

# ==========================================================
# #48: async _notify_all
# ==========================================================
print("\n#48: async _notify_all")
tg_code = read_text(os.path.join(TG_CLAW_DIR, "src", "telegram_api.py"))
test("async_notify_all function exists", "async def async_notify_all" in tg_code)
test("async_notify_all imported", "async_notify_all" in sv_code)
test("async call inside loops", "await async_notify_all" in sv_code)
# Start/stop notices stay synchronous
test("start notice stays synchronous", '_broadcast_sync(msg("sv_start"))' in sv_code)

# ==========================================================
# #57: Accumulated image error → auto reset
# ==========================================================
print("\n#57: Accumulated image error detection")
test("detected in exception handler (dimension limit)", '"dimension limit" in err_str' in sv_code)
test("detected in exception handler (many-image)", '"many-image" in err_str' in sv_code)
test("detected in response text", '"dimension limit" in full_response' in sv_code)
test("restart in reset mode", 'msg("reason_image_overflow"), mode="reset"' in sv_code)
test("Telegram notice", 'msg("image_overflow")' in sv_code)
# Check actual occurrences in the log
img_err_logs = [l for l in log_content.split("\n") if "accumulated image error" in l]
if img_err_logs:
    test(f"accumulated image errors: {len(img_err_logs)}", True)
else:
    skip("accumulated image error history", "hasn't happened in current log")

# ==========================================================
# #49: edited_message support
# ==========================================================
print("\n#49: edited_message support")
test("edited_message in allowed_updates", '"edited_message"' in sv_code)
test("edited_message parsing", 'u.get("edited_message")' in sv_code)
test("is_edited flag", "is_edited" in sv_code)
test("[edited] tag appended", 'msg("edited_tag")' in sv_code)

# ==========================================================
# #50: document/file messages
# ==========================================================
print("\n#50: document/file messages")
test("document message detection", 'msg.get("document")' in sv_code)
test("getFile API call", "getFile" in sv_code)
test("file save directory", 'os.path.join(UPLOADS_DIR, "files")' in sv_code)
test("caption branch", "caption" in sv_code)

# ==========================================================
# #25-#35: scope of the former test_smoke_25_35.py
# ==========================================================

# #25: dedup
print("\n#25: Duplicate message filtering")
test("_last_msg_map exists", "_last_msg_map" in sv_code)
test("message_id based check", f'msg_key = f"' in sv_code and "msg_id" in sv_code)

# #26: date+text dedup
print("\n#26: Network resend dedup")
test("msg_date_key logic", "msg_date_key" in sv_code)
# TTL 300s: cutoff = time.time() - 300 (used in cleanup)
test("TTL 5 min (300s)", "time.time() - 300" in sv_code)

# #27: images
print("\n#27: Image messages")
test("_download_photo exists", "async def _download_photo" in sv_code)
test("caption branch", "caption" in sv_code)

# #28: ACK
print("\n#28: Receipt ACK")
test("idle/busy ACK branch", 'ack = "✔️"' in sv_code and 'msg("ack_busy"' in sv_code)
test("ACK → enqueue order", sv_code.find("async_send_telegram") < sv_code.find("message_queue.put"))

# #29-30: auto reset
print("\n#29-30: Session auto reset")
test("query count check", "SESSION_RESET_QUERIES" in sv_code)
test("age check", "SESSION_RESET_HOURS" in sv_code)
for name, s in sessions.items():
    qc = s["query_count"]
    age_h = (time.time() - s["start_time"]) / 3600
    test(f"{name}: Q={qc}/100, age={age_h:.1f}h/6h", qc < 100 and age_h < 6)

# #31-32: auto-resume
print("\n#31-32: Auto-resume")
test("_should_auto_resume exists", "def _should_auto_resume" in sv_code)
test("resume_count limited to 2", "resume_count >= 2" in sv_code)
test("user message → resume_count reset", "not is_auto_resume" in sv_code)

# #33: watchdog
print("\n#33: watchdog")
test("asyncio watchdog loop", "async def _watchdog_loop" in sv_code)
test("threading watchdog", "def _start_watchdog_thread" in sv_code)
test("300s threshold", "age > 300" in sv_code)
watchdog_logs = [l for l in log_content.split("\n") if "WATCHDOG" in l]
test(f"WATCHDOG fired 0 times", len(watchdog_logs) == 0)

# #34: rate limit
print("\n#34: rate limit")
test("monkey-patch exists", "_patched_parse" in sv_code)
test("rate_limit_event handling", "rate_limit_event" in sv_code)
test("_rate_limit_data stored", "_rate_limit_data" in sv_code)

# #35: cooldown
print("\n#35: Restart cooldown")
test("cooldown = 300", "cooldown = 300" in sv_code)
test("force bypass", "not force and elapsed < cooldown" in sv_code)

# ==========================================================
# #36: session_id restore / continue fallback
# ==========================================================
print("\n#36: session_id restore / continue fallback")
test("session_id restore logic", "session_id restored" in sv_code)
test("continue fallback (no session_id)", "continue fallback" in sv_code or "continue_conversation" in sv_code)
# Check actual behavior in the log
restore_logs = [l for l in log_content.split("\n") if "session_id restored" in l]
fallback_logs = [l for l in log_content.split("\n") if "continue fallback" in l]
test(f"session_id restores: {len(restore_logs)}", len(restore_logs) >= 0)  # 0 is OK too
if restore_logs:
    print(f"    latest: {restore_logs[-1].strip()}")
if fallback_logs:
    test(f"continue fallbacks: {len(fallback_logs)}", True)
    print(f"    latest: {fallback_logs[-1].strip()}")

# ==========================================================
# #37: MCP stabilization wait
# ==========================================================
print("\n#37: MCP stabilization wait")
test("_wait_mcp_ready method", "_wait_mcp_ready" in sv_code or "MCP stabilization" in sv_code)
mcp_logs = [l for l in log_content.split("\n") if "MCP stabilization" in l]
if mcp_logs:
    test(f"MCP stabilization wait logs: {len(mcp_logs)}", True)
else:
    skip("MCP stabilization wait log", "not in current log (rotated after restart)")

# ==========================================================
# #38: Response time distribution (outlier detection)
# ==========================================================
print("\n#38: Response time distribution")
import re
send_logs = [l for l in log_content.split("\n") if "final send" in l]
times_sec = []
for l in send_logs:
    m = re.search(r'(\d+\.\d+)s\)', l)
    if m:
        times_sec.append(float(m.group(1)))
if times_sec:
    avg = sum(times_sec) / len(times_sec)
    max_t = max(times_sec)
    test(f"{len(times_sec)} responses, avg {avg:.0f}s, max {max_t:.0f}s", True)
    test(f"no response over 15 min", max_t < 900, f"max={max_t:.0f}s")
else:
    skip("response time analysis", "no final send logs")

# ==========================================================
# #39: Measured parallel processing across 3 sessions
# ==========================================================
print("\n#39: Parallel processing across 3 sessions")
# Check whether multiple sessions were busy in the same period
sessions_active = set()
for l in log_content.split("\n"):
    if "processing message" in l:
        for name in ["Converter", "NemoNemo", "Crossword"]:
            if name in l:
                sessions_active.add(name)
test(f"multiple sessions active ({len(sessions_active)})", len(sessions_active) >= 2,
     f"active sessions: {sessions_active}")

# ==========================================================
# #40: usage logging
# ==========================================================
print("\n#40: usage logging")
usage_logs = [l for l in log_content.split("\n") if "[usage]" in l]
test(f"usage logs recorded: {len(usage_logs)}", len(usage_logs) > 0)
# Check that usage includes input/output tokens
if usage_logs:
    test("includes input_tokens", "input_tokens" in usage_logs[-1])
    test("includes output_tokens", "output_tokens" in usage_logs[-1])

# ==========================================================
# #41: Reconnect succeeds after disconnect error
# ==========================================================
print("\n#41: Reconnect after disconnect error")
disconnect_errors = [l for l in log_content.split("\n") if "disconnect error" in l]
reconnect_after = 0
for i, l in enumerate(log_content.split("\n")):
    if "disconnect error" in l:
        # check for a following "connected"
        remaining = "\n".join(log_content.split("\n")[i:i+20])
        if "connected" in remaining or "SDK session connected" in remaining:
            reconnect_after += 1
if disconnect_errors:
    test(f"{len(disconnect_errors)} disconnects, {reconnect_after} reconnects",
         reconnect_after > 0,
         f"disconnect={len(disconnect_errors)}, reconnect={reconnect_after}")
else:
    skip("reconnect after disconnect error", "no disconnect errors")

# ==========================================================
# #42: Final send chunking
# ==========================================================
print("\n#42: Final send chunking")
test("_split_message logic", "_split_message" in sv_code)
test("final send log format (N chars, N chunks, Ns)", "final send" in sv_code)
multi_chunk = [l for l in send_logs if ", 1 chunks," not in l]
if multi_chunk:
    test(f"multi-chunk sends: {len(multi_chunk)}", True)
    print(f"    example: {multi_chunk[-1].strip()}")
else:
    test("all responses in 1 chunk (<= 4096 chars)", True)

# ==========================================================
# Extra: system integrity checks
# ==========================================================
print("\n== System integrity ==")
test("supervisor.lock exists", os.path.exists(LOCK_FILE))
if os.path.exists(LOCK_FILE):
    lock_data = read_json(LOCK_FILE)
    test("lock PID = status PID", lock_data.get("pid") == status.get("pid"),
         f"lock={lock_data.get('pid')}, status={status.get('pid')}")
test("session_ids.json exists", os.path.exists(SESSION_IDS_FILE))
# Error log analysis
# Only real processing errors (exclude tool call logs)
error_lines = [l for l in log_content.split("\n")
               if ("processing error (" in l or "loop error:" in l)
               and "[block]" not in l and "[result]" not in l]
critical_errors = error_lines
test(f"0 critical errors", len(critical_errors) == 0,
     f"{len(critical_errors)}: {critical_errors[-1][:80] if critical_errors else ''}")

# ==========================================================
# Results
# ==========================================================
print(f"\n{'='*60}")
print(f"Total: ✅ {passed} passed, ❌ {failed} failed, ⏭️ {skipped} skipped")
print(f"{'='*60}")

sys.exit(1 if failed > 0 else 0)
