"""텔레그램 API 함수 + 텍스트 유틸리티"""

import re
import httpx

from .config import CHAT_ID, PROJECTS
from .logging_utils import log

# --- 봇 명령어 메뉴 (Telegram의 "/" 자동완성) ---

# (command, en, ko) — 이름은 소문자/숫자/_ 만 허용, 별칭(/s, /u 등)은 메뉴에서 제외
BOT_COMMANDS = [
    ("status", "Session status (OK / DEAD / STUCK)", "세션 상태"),
    ("usage", "Claude usage (5h / 7d limits)", "API 사용량"),
    ("sys", "System info (CPU / memory / processes)", "시스템 (CPU/RAM)"),
    ("log", "Recent logs", "최근 로그"),
    ("mode", "Output mode: minimal or normal", "출력 모드"),
    ("esc", "Interrupt the current task", "현재 작업 중단"),
    ("pause", "Pause the session", "세션 일시정지"),
    ("restart", "Restart the session (keeps context)", "세션 재시작 (컨텍스트 유지)"),
    ("reset", "Reset the session (clears context)", "세션 리셋 (컨텍스트 초기화)"),
    ("ask", "Quick question in a separate session", "별도 세션에 빠른 질문"),
    ("compact", "Compress context", "컨텍스트 압축"),
    ("context", "Context usage details", "컨텍스트 사용량"),
    ("cost", "Session cost and duration", "세션 비용/시간"),
    ("help", "Command list", "명령어 목록"),
]


def register_bot_commands(bot_token: str, lang: str = "en") -> bool:
    """봇 명령어 목록을 Telegram에 등록 (setMyCommands). 실패해도 예외 없이 False."""
    idx = 2 if lang == "ko" else 1
    commands = [{"command": c[0], "description": c[idx]} for c in BOT_COMMANDS]
    url = f"https://api.telegram.org/bot{bot_token}/setMyCommands"
    try:
        r = httpx.post(url, json={"commands": commands}, timeout=15)
        ok = bool(r.json().get("ok"))
        if not ok:
            log(f"setMyCommands 실패: {r.text[:200]}")
        return ok
    except Exception as e:
        log(f"setMyCommands 실패: {e}")
        return False


# --- 텔레그램 API ---

def send_telegram(text: str, bot_token: str, bot_name: str = "", use_html: bool = False, notify: bool = False) -> int:
    """메시지 전송 후 message_id 반환 (실패 시 0). notify=True면 알림 소리 활성화."""
    if bot_name:
        prefix = f"[{bot_name}] "
        text = prefix + text
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    last_msg_id = 0
    chunks = _split_message(text)
    for chunk in chunks:
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk[:4096],
            "disable_web_page_preview": True,
            "disable_notification": not notify,
        }
        if use_html:
            payload["parse_mode"] = "HTML"
        try:
            r = httpx.post(url, json=payload, timeout=15)
            data = r.json()
            if not data.get("ok") and use_html:
                payload.pop("parse_mode", None)
                r = httpx.post(url, json=payload, timeout=15)
                data = r.json()
            last_msg_id = data.get("result", {}).get("message_id", 0)
        except Exception as e:
            log(f"텔레그램 전송 실패: {e}")
    return last_msg_id


def edit_telegram(text: str, message_id: int, bot_token: str, bot_name: str = "") -> bool:
    """기존 메시지 수정. "message not modified" 무시."""
    if bot_name and not text.startswith(f"[{bot_name}]"):
        text = f"[{bot_name}] {text}"
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    try:
        r = httpx.post(url, json={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }, timeout=15)
        if r.status_code == 200:
            return True
        if r.status_code == 400 and "not modified" in r.text:
            return True
        log(f"edit 실패 ({r.status_code}): {r.text[:100]}")
        return False
    except Exception as e:
        log(f"edit 예외: {e}")
        return False


def send_ack(bot_token: str, msg_id: int, bot_name: str = ""):
    prefix = f"[{bot_name}] " if bot_name else ""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        httpx.post(url, json={
            "chat_id": CHAT_ID,
            "text": f"{prefix}\u2705",
            "reply_to_message_id": msg_id,
        }, timeout=5)
    except Exception:
        pass


# --- 파일 전송 (이미지/문서) ---

def _build_multipart(chat_id: str, field: str, file_path: str, caption: str = "") -> tuple[bytes, str]:
    """multipart/form-data body 구성. Returns: (body_bytes, boundary)"""
    import mimetypes
    boundary = "----TeleClawBoundary"
    body = b""
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode()
    if caption:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode()
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    filename = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    with open(file_path, "rb") as f:
        body += f.read()
    body += f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


def send_photo_sync(bot_token: str, file_path: str, caption: str = "") -> int:
    """동기 이미지 전송. Returns: message_id (0 on failure)"""
    body, boundary = _build_multipart(CHAT_ID, "photo", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        r = httpx.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"사진 전송 실패: {e}")
        return 0


def send_file_sync(bot_token: str, file_path: str, caption: str = "") -> int:
    """동기 파일 전송. Returns: message_id (0 on failure)"""
    body, boundary = _build_multipart(CHAT_ID, "document", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        r = httpx.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"파일 전송 실패: {e}")
        return 0


async def async_send_photo(ahttp: httpx.AsyncClient, bot_token: str, file_path: str, caption: str = "") -> int:
    """비동기 이미지 전송. Returns: message_id"""
    body, boundary = _build_multipart(CHAT_ID, "photo", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        r = await ahttp.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"사진 전송 실패: {e}")
        return 0


async def async_send_file(ahttp: httpx.AsyncClient, bot_token: str, file_path: str, caption: str = "") -> int:
    """비동기 파일 전송. Returns: message_id"""
    body, boundary = _build_multipart(CHAT_ID, "document", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        r = await ahttp.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"파일 전송 실패: {e}")
        return 0


# --- 텍스트 정리 ---

_re_blank_lines = re.compile(r"\n{3,}")
_re_control_chars = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_re_md_link = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_re_bare_url = re.compile(r"https?://\S+")


def _strip_urls(text: str) -> str:
    """마크다운 링크 → 텍스트만, bare URL → 삭제"""
    text = _re_md_link.sub(r"\1", text)
    return _re_bare_url.sub("", text)


def _clean_text(text: str) -> str:
    """제어 문자 제거 + 깨진 문자 복구 + URL 제거 + 빈 줄 정리"""
    text = _re_control_chars.sub("", text)
    try:
        raw = text.encode("utf-8", errors="surrogateescape")
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    text = re.sub(r"\ufffd+", "?", text)
    text = _strip_urls(text)
    text = _re_blank_lines.sub("\n\n", text)
    return text.strip()


# --- 마크다운 → 텔레그램 HTML 변환 ---

def _escape_html(text: str) -> str:
    """HTML 특수문자 이스케이프"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_table_to_list(text: str) -> str:
    """마크다운 테이블 → 리스트 형태 변환 (텔레그램 <table> 미지원)"""
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            headers = [h.strip() for h in line.split("|")[1:-1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                cols = [c.strip() for c in lines[i].split("|")[1:-1]]
                if len(cols) >= 2:
                    entry = f"\u25aa\ufe0f {cols[0]}: {cols[1]}"
                    if len(cols) > 2 and cols[2]:
                        entry += f" \u2014 {cols[2]}"
                    result.append(entry)
                i += 1
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _protect_code_blocks(text: str) -> tuple[str, list, list]:
    """코드블록/인라인코드를 플레이스홀더로 치환. (text, code_blocks, inline_codes) 반환."""
    code_blocks = []
    def _save_code(m):
        code_blocks.append(m.group(1))
        return f"__CODEBLOCK_{len(code_blocks) - 1}__"
    text = re.sub(r"```\w*\n(.*?)```", _save_code, text, flags=re.DOTALL)
    text = re.sub(r"```(.*?)```", _save_code, text, flags=re.DOTALL)

    inline_codes = []
    def _save_inline(m):
        inline_codes.append(m.group(1))
        return f"__INLINE_{len(inline_codes) - 1}__"
    text = re.sub(r"`([^`]+)`", _save_inline, text)

    return text, code_blocks, inline_codes


def _restore_code_blocks(text: str, code_blocks: list, inline_codes: list) -> str:
    """플레이스홀더를 HTML 태그로 복원."""
    for i, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_{i}__", f"<code>{_escape_html(code)}</code>")
    for i, code in enumerate(code_blocks):
        text = text.replace(f"__CODEBLOCK_{i}__", f"<pre>{_escape_html(code)}</pre>")
    return text


def _convert_markdown_formatting(text: str) -> str:
    """마크다운 서식 → 텔레그램 HTML 태그. 우선순위: ***>**>*"""
    text = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


def _merge_blockquotes(text: str) -> str:
    """연속 인용줄(> ...)을 하나의 <blockquote>로 병합."""
    lines = text.split("\n")
    result = []
    in_quote = False
    for line in lines:
        if line.startswith("&gt;"):
            content = re.sub(r"^&gt;\s?", "", line)
            if not in_quote:
                result.append(f"<blockquote>{content}")
                in_quote = True
            else:
                result.append(content)
        else:
            if in_quote:
                result[-1] += "</blockquote>"
                in_quote = False
            result.append(line)
    if in_quote:
        result[-1] += "</blockquote>"
    return "\n".join(result)


def _md_to_telegram_html(text: str) -> str:
    """GitHub Flavored Markdown → Telegram HTML 변환. 실패 시 plain text 반환.

    파이프라인:
      테이블→리스트 → 코드블록 보호 → HTML이스케이프 → 서식변환
      → 인용병합 → URL제거 → 도구라인강조 → 빈줄정리 → 코드블록 복원
    """
    try:
        text = _convert_table_to_list(text)
        text, code_blocks, inline_codes = _protect_code_blocks(text)
        text = _escape_html(text)
        text = _convert_markdown_formatting(text)
        text = _merge_blockquotes(text)
        text = _strip_urls(text)
        text = re.sub(r"^(\u2500 \U0001f527 .+)$", r"<code>\1</code>", text, flags=re.MULTILINE)
        text = _re_blank_lines.sub("\n\n", text)
        text = _restore_code_blocks(text, code_blocks, inline_codes)
        return text
    except Exception as e:
        log(f"HTML 변환 실패: {e}")
        return _escape_html(text)


# --- 메시지 분할 ---

def _split_message(text: str, max_len: int = 4000) -> list:
    """의미 단위로 메시지 분할 (빈줄 > 줄바꿈 > 공백 우선)"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, max_len)
        if cut < max_len // 2:
            cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = text.rfind(" ", 0, max_len)
        if cut < 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    if len(chunks) > 1:
        chunks = [f"{c}\n\n({i + 1}/{len(chunks)})" for i, c in enumerate(chunks)]
    return chunks


# --- 비동기 텔레그램 API (세션 루프용) ---

async def async_send_telegram(ahttp: httpx.AsyncClient, text: str, bot_token: str, bot_name: str = "", use_html: bool = False, reply_to: int = 0) -> int:
    text = _clean_text(text)
    if bot_name:
        text = f"[{bot_name}] {text}"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text[:4096],
            "disable_web_page_preview": True,
            "disable_notification": True,
        }
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to}
        if use_html:
            payload["parse_mode"] = "HTML"
        r = await ahttp.post(url, json=payload, timeout=15)
        data = r.json()
        if not data.get("ok") and use_html:
            payload.pop("parse_mode", None)
            r = await ahttp.post(url, json=payload, timeout=15)
            data = r.json()
        return data.get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"텔레그램 전송 실패: {e}")
        return 0


async def async_edit_telegram(ahttp: httpx.AsyncClient, text: str, message_id: int, bot_token: str, bot_name: str = "", use_html: bool = False) -> bool:
    text = _clean_text(text)
    if bot_name and not text.startswith(f"[{bot_name}]"):
        text = f"[{bot_name}] {text}"
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    try:
        payload = {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }
        if use_html:
            payload["parse_mode"] = "HTML"
        r = await ahttp.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            return True
        if r.status_code == 400 and "not modified" in r.text:
            return True
        if r.status_code == 400 and use_html:
            payload.pop("parse_mode", None)
            r = await ahttp.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return True
        log(f"edit 실패 ({r.status_code}): {r.text[:100]}")
        return False
    except Exception as e:
        log(f"edit 예외: {e}")
        return False


async def async_react(ahttp: httpx.AsyncClient, bot_token: str, msg_id: int, emoji: str = "\U0001f440"):
    """사용자 메시지에 리액션 추가 (알림 없이 수신 확인)."""
    url = f"https://api.telegram.org/bot{bot_token}/setMessageReaction"
    try:
        await ahttp.post(url, json={
            "chat_id": CHAT_ID,
            "message_id": msg_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        }, timeout=5)
    except Exception:
        pass


def _notify_all(text: str):
    """전체 알림 — 모든 봇에 broadcast (동기)"""
    sent = set()
    for name, config in PROJECTS.items():
        token = config.get("bot_token", "")
        if token and token not in sent:
            send_telegram(text, token)
            sent.add(token)


async def async_notify_all(ahttp, text: str):
    """전체 알림 — 모든 봇에 broadcast (비동기)"""
    sent = set()
    for name, config in PROJECTS.items():
        token = config.get("bot_token", "")
        if token and token not in sent:
            await async_send_telegram(ahttp, text, token)
            sent.add(token)
