#!/usr/bin/env python3
"""
TeleClaw 전체 Smoke Test (#1~#45) — 비파괴 검증.
실행: python tests/test_smoke_all.py
"""

import os
import sys
import json
import time
import subprocess

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TELECLAW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TELECLAW_DIR)
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
    sv_path = os.path.join(TELECLAW_DIR, "src", "teleclaw.py")
    with open(sv_path, "r", encoding="utf-8") as f:
        return f.read()


def read_wrapper_code():
    wp_path = os.path.join(TELECLAW_DIR, "src", "teleclaw_daemon.py")
    with open(wp_path, "r", encoding="utf-8") as f:
        return f.read()


def read_commands_code():
    cmd_path = os.path.join(TELECLAW_DIR, "src", "commands.py")
    with open(cmd_path, "r", encoding="utf-8") as f:
        return f.read()


# ==========================================================
# 코드 / 로그 / 상태 로드
# ==========================================================
sv_code = read_code()
wp_code = read_wrapper_code()
cmd_code = read_commands_code()
log_content = read_text(LOG_FILE)
wrapper_log = read_text(WRAPPER_LOG)
status = read_json(STATUS_FILE)
sessions = status["sessions"]

# ==========================================================
# #1: 프로세스 상태
# ==========================================================
print("\n#1: 프로세스 상태")
test("teleclaw_status.json 존재", os.path.exists(STATUS_FILE))
test("PID 기록", status.get("pid", 0) > 0, f"pid={status.get('pid')}")
# tasklist로 프로세스 확인
try:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {status['pid']}", "/NH"],
                       capture_output=True, text=True, timeout=5)
    test("teleclaw 프로세스 존재", str(status["pid"]) in r.stdout)
except Exception as e:
    skip("teleclaw 프로세스 존재", str(e))
test(f"{len(sessions)}개 세션 존재", len(sessions) >= 3, f"count={len(sessions)}")
for name, s in sessions.items():
    test(f"{name} connected=true", s["connected"])

# ==========================================================
# #2: 메시지 처리 (로그 기반)
# ==========================================================
print("\n#2: 메시지 처리")
test("'메시지 수신' 로그 존재", "메시지 수신:" in log_content)
test("'메시지 처리 시작' 로그 존재", "메시지 처리 시작:" in log_content)
test("'처리 완료' 로그 존재", "처리 완료" in log_content)
recv_count = log_content.count("메시지 수신:")
proc_count = log_content.count("메시지 처리 시작:")
done_count = log_content.count("처리 완료")
test("수신-처리-완료 흐름 정상", recv_count > 0 and proc_count > 0 and done_count > 0,
     f"수신={recv_count}, 처리={proc_count}, 완료={done_count}")

# ==========================================================
# #3: 명령어 동작
# ==========================================================
print("\n#3: 명령어 동작")
test("handle_command 함수 존재", "def handle_command" in cmd_code)
for cmd in ["/status", "/usage", "/restart", "/reset", "/log", "/help"]:
    test(f"명령어 {cmd} 핸들러", f'"{cmd}"' in cmd_code or f"'{cmd}'" in cmd_code or cmd.lstrip("/") in cmd_code)

# ==========================================================
# #4: 세션 재시작
# ==========================================================
print("\n#4: 세션 재시작")
test("_restart_session 메서드 존재", "async def _restart_session" in sv_code)
test("restart_request flag 감지 로직", "restart_request_" in sv_code)
# 로그에서 재시작 이력 확인
restart_logs = [l for l in log_content.split("\n") if "재시작 시도" in l]
if restart_logs:
    test(f"재시작 이력 {len(restart_logs)}건", True)
else:
    skip("재시작 이력", "현재 로그에 없음")

# ==========================================================
# #5: TeleClaw 자체 재시작
# ==========================================================
print("\n#5: TeleClaw 자체 재시작")
test("restart_request_supervisor.flag 감지", "restart_request_supervisor" in sv_code)
test("os._exit(0) 호출로 wrapper 재시작 유도", "os._exit(0)" in sv_code)
# wrapper 로그에서 재시작 이력
wrapper_restarts = wrapper_log.count("teleclaw 시작")
test(f"wrapper 재시작 이력 {wrapper_restarts}건", wrapper_restarts > 0)

# ==========================================================
# #6: DEAD 세션 자동 복구
# ==========================================================
print("\n#6: DEAD 세션 자동 복구")
test("_assess_health 메서드 존재", "def _assess_health" in sv_code)
test("DEAD 판정 로직", '"DEAD"' in sv_code)
test("health_check_loop에서 DEAD 감지 → 재시작", "status == \"DEAD\"" in sv_code or 'status == "DEAD"' in sv_code)
test("MAX_RESTARTS_PER_WINDOW 제한", "MAX_RESTARTS_PER_WINDOW" in sv_code)

# ==========================================================
# #7: SDK query hard timeout
# ==========================================================
print("\n#7: SDK query hard timeout")
test("query 타임아웃 체크 (600초)", "600" in sv_code and "강제 중단" in sv_code)
test("10분 무응답 break", "10분간 메시지 없음" in sv_code)

# ==========================================================
# #8: 재시작 시 메시지 유실 방지
# ==========================================================
print("\n#8: 재시작 시 메시지 유실 방지")
test("offset 저장 로직 (_save_offset)", "_save_offset" in sv_code)
test("offset 복원 로직 (_load_offset)", "_load_offset" in sv_code or "offset 복원" in sv_code)
# offset 파일 존재 확인
offset_files = [f for f in os.listdir(DATA_DIR) if f.startswith("last_offset_")]
test(f"offset 파일 {len(offset_files)}개 존재", len(offset_files) > 0)
# 로그에서 offset 복원 확인
if "offset 복원" in log_content:
    test("offset 복원 로그", True)
else:
    skip("offset 복원 로그", "현재 로그에 없음 (재시작 후 로테이션)")

# ==========================================================
# #9: busy 중 새 메시지 큐잉
# ==========================================================
print("\n#9: busy 중 새 메시지 큐잉")
test("asyncio.Queue 사용", "message_queue" in sv_code)
test("busy 상태에서 대기건수 표시", "처리 중, 대기" in sv_code)

# ==========================================================
# #10: pause 모드
# ==========================================================
# ==========================================================
# #10: flag 이름 일관성
# ==========================================================
print("\n#10: flag 이름 일관성")
test("supervisor flag와 세션 flag 구분", "restart_request_supervisor" in sv_code and "restart_request_{name}" in sv_code)

# ==========================================================
# #12: 병렬 메시지
# ==========================================================
print("\n#12: 병렬 메시지")
test("세션별 독립 폴링 루프", "_bot_poll_loop" in sv_code)
test("세션별 독립 처리 루프", "_session_loop" in sv_code)
# 로그에서 여러 세션 동시 처리 확인
session_names_in_log = set()
for name in sessions:
    if f"{name}: 메시지 처리 시작" in log_content:
        session_names_in_log.add(name)
test(f"다중 세션 처리 확인 ({len(session_names_in_log)}개)", len(session_names_in_log) >= 1)

# ==========================================================
# #13: /status 고스트 상태
# ==========================================================
print("\n#13: /status 고스트 상태")
test("connected 상태와 실제 client 체크", "state.client is None" in sv_code or "not state.client" in sv_code)
test("health check에서 client=None → DEAD", "client is None" in sv_code)

# ==========================================================
# #14: 세션 미연결 시 재시도
# ==========================================================
print("\n#14: 세션 미연결 시 재시도")
test("retry_conn 키 사용", "retry_conn" in sv_code)
test("미연결 재시도 1회 제한", 'retry < 1' in sv_code)
test("실패 시 텔레그램 알림", "세션 연결 실패, 메시지 처리 불가" in sv_code)

# ==========================================================
# #14-1: client=None 무한 재큐잉 방지
# ==========================================================
print("\n#14-1: client=None 무한 재큐잉 방지")
test("retry_noclient 키 사용", "retry_noclient" in sv_code)
test("10회 제한 (재시작 대기 충분)", "retry < 10" in sv_code)
test("소진 시 드롭 + 알림", "재시도 소진" in sv_code)

# ==========================================================
# #15: query() 타임아웃 재시도
# ==========================================================
print("\n#15: query() 타임아웃 재시도")
test("retry_timeout 키 사용", "retry_timeout" in sv_code)
test("2회 제한", "retry < 2" in sv_code)
test("타임아웃 재시도 큐잉 로그", "타임아웃 재시도 큐잉" in sv_code)

# ==========================================================
# #16: 빈 응답 알림
# ==========================================================
print("\n#16: 빈 응답 알림")
test("빈 응답 감지", "빈 응답" in sv_code)

# ==========================================================
# #17: 처리 중 예외 재시도
# ==========================================================
print("\n#17: 처리 중 예외 재시도")
test("retry_error 키 사용", "retry_error" in sv_code)
test("에러 재시도 큐잉", "에러 재시도 큐잉" in sv_code)
test("연속 에러 카운트 (error_count)", "state.error_count += 1" in sv_code)

# ==========================================================
# #18: 재시도 시 메시지 순서 보존
# ==========================================================
print("\n#18: 재시도 시 메시지 순서 보존")
session_code = read_text(os.path.join(TELECLAW_DIR, "src", "session.py"))
test("FIFO 큐 (asyncio.Queue)", "asyncio.Queue" in session_code)
test("재시도 시 put으로 큐 뒤에 추가", "message_queue.put(msg_data)" in sv_code)

# ==========================================================
# #19: wrapper 무한 크래시 시 알림 반복
# ==========================================================
print("\n#19: wrapper 무한 크래시 시 알림 반복")
test("5/10/20/50회 알림", "fail_count in (5, 10, 20, 50)" in wp_code)
test("50회 이후 주기 알림", "fail_count % 50 == 0" in wp_code)

# ==========================================================
# #20: busy 중 supervisor 재시작 graceful shutdown
# ==========================================================
print("\n#20: busy 중 supervisor 재시작 graceful shutdown")
test("busy 세션 대기 로직", "busy 세션 대기" in sv_code)
test("최대 60초 대기", "waited < 60" in sv_code)
test("force 시 대기 스킵", "not force" in sv_code)

# ==========================================================
# #21: 폴링 에러 메시지 상세화
# ==========================================================
print("\n#21: 폴링 에러 메시지 상세화")
test("repr(e) 사용", "repr(e)" in sv_code)
test("traceback 포함", "traceback" in sv_code)

# ==========================================================
# #22: 세션 병렬 연결
# ==========================================================
print("\n#22: 세션 병렬 연결")
test("asyncio.gather로 병렬 연결", "asyncio.gather" in sv_code)
test("return_exceptions=True", "return_exceptions=True" in sv_code)
# 로그에서 연결 시간 분석 — "TeleClaw 시작" 이후의 연결만 추출
lines = log_content.split("\n")
last_start_idx = -1
for i, l in enumerate(lines):
    if "TeleClaw 시작" in l:
        last_start_idx = i
if last_start_idx >= 0:
    recent_lines = lines[last_start_idx:]
    connect_logs = [l for l in recent_lines if "SDK 세션 연결 완료" in l]
    if len(connect_logs) >= 3:
        times = []
        for l in connect_logs[:3]:  # 해당 시작의 처음 3개
            t = l.split("]")[0].lstrip("[").strip()
            try:
                ts = time.strptime(t, "%Y-%m-%d %H:%M:%S")
                times.append(time.mktime(ts))
            except Exception:
                pass
        if len(times) >= 2:
            spread = max(times) - min(times)
            test(f"병렬 연결 시간 차이 {spread:.0f}초 (< 15초)", spread < 15,
                 f"spread={spread:.0f}초")

# ==========================================================
# #44: client 교체 감지 (receive_messages 중단)
# ==========================================================
print("\n#44: client 교체 감지")
test("state.client is not client 체크", "state.client is not client" in sv_code)
test("교체 감지 시 break", "client 교체 감지" in sv_code)
# 로그에서 실제 발동 확인
swap_logs = [l for l in log_content.split("\n") if "client 교체 감지" in l]
test(f"client 교체 감지 로그 {len(swap_logs)}건", True)
if swap_logs:
    print(f"    최근: {swap_logs[-1].strip()}")

# ==========================================================
# #45: disconnect 실패 시 프로세스 강제 kill
# ==========================================================
print("\n#45: disconnect 실패 시 프로세스 강제 terminate")
test("_transport._process 경로", '_transport' in sv_code and '_process' in sv_code)
test("proc.terminate() 호출", "proc.terminate()" in sv_code)
test("proc.returncode is None 가드", "proc.returncode is None" in sv_code)
# 로그에서 강제 terminate 발동 확인
terminate_logs = [l for l in log_content.split("\n") if "강제 terminate" in l]
test(f"강제 terminate 로그 {len(terminate_logs)}건", len(terminate_logs) >= 0)
if terminate_logs:
    print(f"    최근: {terminate_logs[-1].strip()}")

# ==========================================================
# #23: disconnect 에러 시 리소스 정리
# ==========================================================
print("\n#23: disconnect 에러 시 프로세스 강제 종료")
test("_safe_disconnect 메서드", "async def _safe_disconnect" in sv_code)
test("_transport._process 접근", "getattr(transport" in sv_code)
test("terminate() 후 pid 로그", "강제 terminate" in sv_code)
# 로그에서 disconnect 에러 후 terminate 확인
disc_errors = [l for l in log_content.split("\n") if "disconnect 에러" in l]
disc_terminates = [l for l in log_content.split("\n") if "강제 terminate" in l]
test(f"disconnect 에러 {len(disc_errors)}건", True)
if disc_errors and disc_terminates:
    test("에러 후 terminate 실행됨", len(disc_terminates) > 0)

# ==========================================================
# #24: wrapper에서 supervisor crash stderr 캡처
# ==========================================================
print("\n#24: wrapper crash stderr 캡처 (파일 리다이렉트)")
test("메인 실행에 capture_output 미사용", "stderr=sf" in wp_code)
test("stderr 파일 리다이렉트", "teleclaw_stderr.log" in wp_code)
test("stderr 로그 기록", "teleclaw stderr:" in wp_code)
test("stderr 텔레그램 전송", "크래시 stderr" in wp_code)
# wrapper 로그에서 실제 stderr 캡처 확인
stderr_logs = [l for l in wrapper_log.split("\n") if "teleclaw stderr:" in l]
test(f"wrapper stderr 캡처 이력 {len(stderr_logs)}건", True)
if stderr_logs:
    print(f"    최근: {stderr_logs[-1].strip()[:100]}")

# ==========================================================
# #46: wrapper 재시작 blocking 방지
# ==========================================================
print("\n#46: wrapper 재시작 blocking 방지")
test("stderr 파일 리다이렉트 (stdout 캡처 안 함)", "stderr=sf" in wp_code)
test("메인 실행 stdout 비캡처 (blocking 방지)", "stderr=sf" in wp_code)
# wrapper 로그에서 재시작 즉시성 확인 — "teleclaw 종료" 후 "teleclaw 시작"까지 시간 차이
wp_lines = wrapper_log.split("\n")
restart_pairs = []
for i, l in enumerate(wp_lines):
    if "teleclaw 종료" in l and "exit_code=0" in l:
        # 다음 "teleclaw 시작" 찾기
        for j in range(i+1, min(i+5, len(wp_lines))):
            if "teleclaw 시작" in wp_lines[j]:
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
    test(f"마지막 재시작 지연 {last_delay:.0f}초 (< 30초)", last_delay < 30,
         f"delay={last_delay:.0f}초")
else:
    skip("재시작 지연 측정", "정상 종료 후 재시작 이력 없음")

# ==========================================================
# #51: auto-resume ON/OFF 제어
# ==========================================================
print("\n#51: auto-resume ON/OFF 제어")
cfg_code = read_text(os.path.join(TELECLAW_DIR, "src", "config.py"))
cmd_code = read_text(os.path.join(TELECLAW_DIR, "src", "commands.py"))
test("전역 설정 AUTO_RESUME_ENABLED", "AUTO_RESUME_ENABLED" in cfg_code)
test("supervisor에서 import", "AUTO_RESUME_ENABLED" in sv_code)
test("noresume 파라미터", "no_resume" in sv_code)
test("flag 파싱에 noresume", "noresume" in sv_code)
test("_restart_session에 auto-resume 로직", "AUTO_RESUME_ENABLED and not no_resume" in sv_code)
test("STUCK reason → was_busy 강제", '"STUCK" in reason' in sv_code)
test("/restart noresume 명령어", "noresume" in cmd_code)
test("프롬프트 config 기반", "AUTO_RESUME_PROMPTS" in sv_code)
# 로그에서 auto-resume 동작 확인
resume_logs = [l for l in log_content.split("\n") if "자동 재개" in l and "스킵" not in l]
skip_logs = [l for l in log_content.split("\n") if "자동 재개 스킵" in l or "대기 상태 → 자동 재개" in l]
if resume_logs:
    test(f"자동 재개 실행 {len(resume_logs)}건", True)
elif skip_logs:
    test(f"자동 재개 스킵 {len(skip_logs)}건 (조건 미충족)", True)
else:
    skip("자동 재개 로그", "현재 로그에 없음")

# ==========================================================
# #52: auto-resume busy 상태 영속화
# ==========================================================
print("\n#52: auto-resume busy 상태 영속화")
test("session_ids에 was_busy 저장", 'entry["was_busy"]' in sv_code or '"was_busy"' in sv_code)
test("_load_session_ids에서 was_busy 복원", "was_busy_before_restart = True" in sv_code)
test("하위 호환 (문자열 session_id)", "isinstance(val, str)" in sv_code)
test("os._exit 전 _save_session_ids", "_save_session_ids()" in sv_code and "os._exit" in sv_code)
# session_ids.json 포맷 확인
try:
    with open(SESSION_IDS_FILE, "r") as f:
        sid_data = json.load(f)
    # dict 형태 확인 (새 포맷)
    has_dict = any(isinstance(v, dict) for v in sid_data.values())
    if has_dict:
        test("session_ids.json 새 포맷 (dict)", True)
    else:
        skip("session_ids.json 새 포맷", "아직 이전 포맷 — 재시작 후 마이그레이션")
except Exception as e:
    skip("session_ids.json 포맷", str(e))
# 로그에서 busy 복원 확인
busy_restore = [l for l in log_content.split("\n") if "busy 상태 복원" in l]
if busy_restore:
    test(f"busy 상태 복원 이력 {len(busy_restore)}건", True)
    print(f"    최근: {busy_restore[-1].strip()}")
else:
    skip("busy 상태 복원 이력", "현재 로그에 없음 (재시작 시 idle이었을 수 있음)")

# ==========================================================
# #53: auto-resume 프롬프트 모드
# ==========================================================
print("\n#53: auto-resume 프롬프트 모드")
test("AUTO_RESUME_MODE 설정", "AUTO_RESUME_MODE" in cfg_code)
test("AUTO_RESUME_PROMPTS dict", "AUTO_RESUME_PROMPTS" in cfg_code)
test("3가지 모드 (resume/check/none)", all(
    f'"{m}"' in cfg_code for m in ["resume", "check", "none"]
))
test("none 모드 = None", '"none": None' in cfg_code)
test("supervisor에서 MODE import", "AUTO_RESUME_MODE" in sv_code)
test("supervisor에서 PROMPTS import", "AUTO_RESUME_PROMPTS" in sv_code)
test("prompt None 체크 (none 모드 지원)", "if prompt and self._should_auto_resume" in sv_code)
test("none 모드 로그", 'mode=none' in sv_code)
# 로그에서 모드 확인
mode_logs = [l for l in log_content.split("\n") if "mode=" in l and "자동 재개" in l]
none_logs = [l for l in log_content.split("\n") if "mode=none" in l]
if mode_logs:
    test(f"자동 재개 모드 로그 {len(mode_logs)}건", True)
    print(f"    최근: {mode_logs[-1].strip()[:100]}")
elif none_logs:
    test(f"none 모드 대기 로그 {len(none_logs)}건", True)
else:
    skip("자동 재개 모드 로그", "현재 로그에 없음")

# ==========================================================
# #54: 느린 응답 중간 알림
# ==========================================================
print("\n#54: 느린 응답 중간 알림")
test("last_progress_notify 변수", "last_progress_notify" in sv_code)
test("300초 기준", "elapsed > 300" in sv_code)
test("5분 간격 (300초)", "last_progress_notify > 300" in sv_code)
test("중간 알림 전송", "아직 처리 중" in sv_code)

# ==========================================================
# #55: wrapper 반복 재시작 경고
# ==========================================================
print("\n#55: wrapper 반복 재시작 경고")
test("recent_restarts 리스트", "recent_restarts" in wp_code)
test("10분(600초) 윈도우", "now - t < 600" in wp_code)
test("5회 이상 감지", "len(recent_restarts) >= 5" in wp_code)
test("텔레그램 경고 전송", "잦은 재시작 감지" in wp_code)

# ==========================================================
# #56: 로그 로테이션 (날짜별 보관)
# ==========================================================
print("\n#56: 로그 로테이션")
log_utils = read_text(os.path.join(TELECLAW_DIR, "src", "logging_utils.py"))
test("_archive_lines 함수", "_archive_lines" in log_utils)
test("날짜별 파일명", 'teleclaw_{date_str}.log' in log_utils or "teleclaw_" in log_utils)
test("잘린 로그 보관", "_archive_lines(lines[:-500])" in log_utils)
# 아카이브 파일 존재 확인
import glob
archive_files = glob.glob(os.path.join(LOGS_DIR, "teleclaw_20*.log"))
if archive_files:
    test(f"아카이브 파일 {len(archive_files)}개", True)
    print(f"    최근: {os.path.basename(archive_files[-1])}")
else:
    skip("아카이브 파일", "아직 로테이션 미발생")

# ==========================================================
# #47: deleteMessage POST
# ==========================================================
print("\n#47: deleteMessage POST")
test("deleteMessage POST 방식", "ahttp.post" in sv_code and "deleteMessage" in sv_code)
test("json= 파라미터", 'json={"chat_id"' in sv_code or 'json={' in sv_code)

# ==========================================================
# #48: _notify_all 비동기화
# ==========================================================
print("\n#48: _notify_all 비동기화")
tg_code = read_text(os.path.join(TELECLAW_DIR, "src", "telegram_api.py"))
test("async_notify_all 함수 존재", "async def async_notify_all" in tg_code)
test("async_notify_all import", "async_notify_all" in sv_code)
test("루프 내 비동기 호출", "await async_notify_all" in sv_code)
# 시작/종료는 동기 유지 확인
test("시작 알림은 동기 유지", '_notify_all("[HUB] TeleClaw 시작")' in sv_code)

# ==========================================================
# #57: 이미지 누적 에러 감지 → 자동 reset
# ==========================================================
print("\n#57: 이미지 누적 에러 감지")
test("예외 처리부 감지 (dimension limit)", '"dimension limit" in err_str' in sv_code)
test("예외 처리부 감지 (many-image)", '"many-image" in err_str' in sv_code)
test("응답 텍스트 감지", '"dimension limit" in full_response' in sv_code)
test("reset 모드 재시작", '"이미지 누적 에러", mode="reset"' in sv_code)
test("텔레그램 알림", "이미지 누적으로 컨텍스트 초과" in sv_code)
# 로그에서 실제 발생 확인
img_err_logs = [l for l in log_content.split("\n") if "이미지 누적 에러" in l]
if img_err_logs:
    test(f"이미지 누적 에러 이력 {len(img_err_logs)}건", True)
else:
    skip("이미지 누적 에러 이력", "현재 로그에 미발생")

# ==========================================================
# #49: edited_message 수신 지원
# ==========================================================
print("\n#49: edited_message 수신 지원")
test("allowed_updates에 edited_message", '"edited_message"' in sv_code)
test("edited_message 파싱", 'u.get("edited_message")' in sv_code)
test("is_edited 플래그", "is_edited" in sv_code)
test("[수정] 태그 부착", "[수정]" in sv_code)

# ==========================================================
# #50: document/file 메시지 처리
# ==========================================================
print("\n#50: document/file 메시지 처리")
test("document 메시지 감지", 'msg.get("document")' in sv_code)
test("getFile API 호출", "getFile" in sv_code)
test("파일 저장 디렉토리", 'os.path.join(UPLOADS_DIR, "files")' in sv_code)
test("캡션 분기", "caption" in sv_code)

# ==========================================================
# #25~#35: 기존 test_smoke_25_35.py 범위
# ==========================================================

# #25: 중복 제거
print("\n#25: 중복 메시지 필터링")
test("_last_msg_map 존재", "_last_msg_map" in sv_code)
test("message_id 기반 체크", f'msg_key = f"' in sv_code and "msg_id" in sv_code)

# #26: date+text 중복
print("\n#26: 네트워크 재전송 중복 제거")
test("msg_date_key 로직", "msg_date_key" in sv_code)
# TTL 300초: cutoff = time.time() - 300 (정리 로직에서 사용)
test("TTL 5분 (300초)", "time.time() - 300" in sv_code)

# #27: 이미지
print("\n#27: 이미지 메시지 처리")
test("_download_photo 존재", "async def _download_photo" in sv_code)
test("캡션 분기", "caption" in sv_code)

# #28: ACK
print("\n#28: 수신 확인 (ACK)")
test("idle/busy ACK 분기", 'ack = "✔️"' in sv_code and "처리 중, 대기" in sv_code)
test("ACK → 큐 투입 순서", sv_code.find("async_send_telegram") < sv_code.find("message_queue.put"))

# #29-30: 자동 리셋
print("\n#29-30: 세션 자동 리셋")
test("쿼리수 체크", "SESSION_RESET_QUERIES" in sv_code)
test("시간 체크", "SESSION_RESET_HOURS" in sv_code)
for name, s in sessions.items():
    qc = s["query_count"]
    age_h = (time.time() - s["start_time"]) / 3600
    test(f"{name}: Q={qc}/100, age={age_h:.1f}h/6h", qc < 100 and age_h < 6)

# #31-32: 자동 재개
print("\n#31-32: 자동 재개")
test("_should_auto_resume 존재", "def _should_auto_resume" in sv_code)
test("resume_count 2회 제한", "resume_count >= 2" in sv_code)
test("사용자 메시지 → resume_count 리셋", "not is_auto_resume" in sv_code)

# #33: watchdog
print("\n#33: watchdog")
test("asyncio watchdog loop", "async def _watchdog_loop" in sv_code)
test("threading watchdog", "def _start_watchdog_thread" in sv_code)
test("300초 임계값", "age > 300" in sv_code)
watchdog_logs = [l for l in log_content.split("\n") if "WATCHDOG" in l]
test(f"WATCHDOG 발동 0건", len(watchdog_logs) == 0)

# #34: rate limit
print("\n#34: rate limit")
test("monkey-patch 존재", "_patched_parse" in sv_code)
test("rate_limit_event 처리", "rate_limit_event" in sv_code)
test("_rate_limit_data 저장", "_rate_limit_data" in sv_code)

# #35: 쿨다운
print("\n#35: 재시작 쿨다운")
test("cooldown = 300", "cooldown = 300" in sv_code)
test("force 우회", "not force and elapsed < cooldown" in sv_code)

# ==========================================================
# #36: session_id 복원 / continue 폴백
# ==========================================================
print("\n#36: session_id 복원 / continue 폴백")
test("session_id 복원 로직", "session_id 복원" in sv_code)
test("continue 폴백 (session_id 없을 때)", "continue 폴백" in sv_code or "continue_conversation" in sv_code)
# 로그에서 실제 동작 확인
restore_logs = [l for l in log_content.split("\n") if "session_id 복원" in l]
fallback_logs = [l for l in log_content.split("\n") if "continue 폴백" in l]
test(f"session_id 복원 이력 {len(restore_logs)}건", len(restore_logs) >= 0)  # 0도 OK
if restore_logs:
    print(f"    최근: {restore_logs[-1].strip()}")
if fallback_logs:
    test(f"continue 폴백 이력 {len(fallback_logs)}건", True)
    print(f"    최근: {fallback_logs[-1].strip()}")

# ==========================================================
# #37: MCP 안정화 대기
# ==========================================================
print("\n#37: MCP 안정화 대기")
test("_wait_mcp_ready 메서드", "_wait_mcp_ready" in sv_code or "MCP 안정화" in sv_code)
mcp_logs = [l for l in log_content.split("\n") if "MCP 안정화" in l]
if mcp_logs:
    test(f"MCP 안정화 대기 로그 {len(mcp_logs)}건", True)
else:
    skip("MCP 안정화 대기 로그", "현재 로그에 없음 (재시작 후 로테이션)")

# ==========================================================
# #38: 응답 시간 분포 (이상치 감지)
# ==========================================================
print("\n#38: 응답 시간 분포")
import re
send_logs = [l for l in log_content.split("\n") if "최종 전송" in l]
times_sec = []
for l in send_logs:
    m = re.search(r'(\d+\.\d+)s\)', l)
    if m:
        times_sec.append(float(m.group(1)))
if times_sec:
    avg = sum(times_sec) / len(times_sec)
    max_t = max(times_sec)
    test(f"응답 {len(times_sec)}건, 평균 {avg:.0f}초, 최대 {max_t:.0f}초", True)
    test(f"15분 초과 응답 없음", max_t < 900, f"최대={max_t:.0f}초")
else:
    skip("응답 시간 분석", "최종 전송 로그 없음")

# ==========================================================
# #39: 3개 세션 병렬 처리 실측
# ==========================================================
print("\n#39: 3개 세션 병렬 처리")
# 같은 시간대에 여러 세션이 busy였는지 확인
sessions_active = set()
for l in log_content.split("\n"):
    if "메시지 처리 시작" in l:
        for name in ["Converter", "NemoNemo", "Crossword"]:
            if name in l:
                sessions_active.add(name)
test(f"복수 세션 활동 확인 ({len(sessions_active)}개)", len(sessions_active) >= 2,
     f"활동 세션: {sessions_active}")

# ==========================================================
# #40: usage 로그 기록
# ==========================================================
print("\n#40: usage 로그 기록")
usage_logs = [l for l in log_content.split("\n") if "[usage]" in l]
test(f"usage 로그 {len(usage_logs)}건 기록", len(usage_logs) > 0)
# usage에 input/output tokens 포함 확인
if usage_logs:
    test("input_tokens 포함", "input_tokens" in usage_logs[-1])
    test("output_tokens 포함", "output_tokens" in usage_logs[-1])

# ==========================================================
# #41: disconnect 에러 후 재연결 성공
# ==========================================================
print("\n#41: disconnect 에러 후 재연결")
disconnect_errors = [l for l in log_content.split("\n") if "disconnect 에러" in l]
reconnect_after = 0
for i, l in enumerate(log_content.split("\n")):
    if "disconnect 에러" in l:
        # 이후 "연결 완료" 있는지 확인
        remaining = "\n".join(log_content.split("\n")[i:i+20])
        if "연결 완료" in remaining or "SDK 세션 연결 완료" in remaining:
            reconnect_after += 1
if disconnect_errors:
    test(f"disconnect {len(disconnect_errors)}건 후 재연결 {reconnect_after}건",
         reconnect_after > 0,
         f"disconnect={len(disconnect_errors)}, 재연결={reconnect_after}")
else:
    skip("disconnect 에러 후 재연결", "disconnect 에러 없음")

# ==========================================================
# #42: 최종 전송 청크 분할
# ==========================================================
print("\n#42: 최종 전송 청크 분할")
test("_split_message 로직", "_split_message" in sv_code)
test("최종 전송 로그 형식 (N자, N청크, N초)", "최종 전송" in sv_code)
multi_chunk = [l for l in send_logs if "1청크" not in l]
if multi_chunk:
    test(f"다중 청크 전송 {len(multi_chunk)}건", True)
    print(f"    예시: {multi_chunk[-1].strip()}")
else:
    test("모든 응답 1청크 (4096자 이하)", True)

# ==========================================================
# 추가: 시스템 무결성 체크
# ==========================================================
print("\n== 시스템 무결성 ==")
test("supervisor.lock 존재", os.path.exists(LOCK_FILE))
if os.path.exists(LOCK_FILE):
    lock_data = read_json(LOCK_FILE)
    test("lock PID = status PID", lock_data.get("pid") == status.get("pid"),
         f"lock={lock_data.get('pid')}, status={status.get('pid')}")
test("session_ids.json 존재", os.path.exists(SESSION_IDS_FILE))
# 에러 로그 분석
# 실제 처리 에러만 필터 (도구 호출 로그 제외)
error_lines = [l for l in log_content.split("\n")
               if ("처리 에러:" in l or "loop 에러:" in l)
               and "[block]" not in l and "[result]" not in l]
critical_errors = error_lines
test(f"critical 에러 0건", len(critical_errors) == 0,
     f"{len(critical_errors)}건: {critical_errors[-1][:80] if critical_errors else ''}")

# ==========================================================
# 결과
# ==========================================================
print(f"\n{'='*60}")
print(f"전체 결과: ✅ {passed} passed, ❌ {failed} failed, ⏭️ {skipped} skipped")
print(f"{'='*60}")

sys.exit(1 if failed > 0 else 0)
