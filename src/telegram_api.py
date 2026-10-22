"""Telegram API functions + text utilities"""

import re
import httpx

from .config import CHAT_ID, PROJECTS
from .logging_utils import log
from .messages import msg_for

# --- Bot command menu (Telegram "/" autocomplete) ---

# Command names; descriptions come from cmd_<name> in resources/lang.
# Names allow only lowercase/digits/_; aliases (/s, /u, etc.) are left out of the menu
BOT_COMMANDS = [
    "status", "usage", "sys", "log", "mode", "esc", "pause",
    "restart", "reset", "ask", "compact", "context", "cost", "help",
]


def register_bot_commands(bot_token: str, lang: str = "en") -> bool:
    """Register the bot command list with Telegram (setMyCommands). Returns False on failure, never raises."""
    commands = [{"command": c, "description": msg_for(lang, f"cmd_{c}")} for c in BOT_COMMANDS]
    url = f"https://api.telegram.org/bot{bot_token}/setMyCommands"
    try:
        r = httpx.post(url, json={"commands": commands}, timeout=15)
        ok = bool(r.json().get("ok"))
        if not ok:
            log(f"setMyCommands failed: {r.text[:200]}")
        return ok
    except Exception as e:
        log(f"setMyCommands failed: {e}")
        return False


# --- Telegram API ---

def send_telegram(text: str, bot_token: str, bot_name: str = "", use_html: bool = False, notify: bool = False) -> int:
    """Send a message and return its message_id (0 on failure). notify=True enables the notification sound."""
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
            log(f"Telegram send failed: {e}")
    return last_msg_id


def edit_telegram(text: str, message_id: int, bot_token: str, bot_name: str = "") -> bool:
    """Edit an existing message. Ignores "message not modified"."""
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
        log(f"edit failed ({r.status_code}): {r.text[:100]}")
        return False
    except Exception as e:
        log(f"edit exception: {e}")
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


# --- File sending (images/documents) ---

def _build_multipart(chat_id: str, field: str, file_path: str, caption: str = "") -> tuple[bytes, str]:
    """Build a multipart/form-data body. Returns: (body_bytes, boundary)"""
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
    """Send an image (sync). Returns: message_id (0 on failure)"""
    body, boundary = _build_multipart(CHAT_ID, "photo", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        r = httpx.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"Photo send failed: {e}")
        return 0


def send_file_sync(bot_token: str, file_path: str, caption: str = "") -> int:
    """Send a file (sync). Returns: message_id (0 on failure)"""
    body, boundary = _build_multipart(CHAT_ID, "document", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        r = httpx.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"File send failed: {e}")
        return 0


async def async_send_photo(ahttp: httpx.AsyncClient, bot_token: str, file_path: str, caption: str = "") -> int:
    """Send an image (async). Returns: message_id"""
    body, boundary = _build_multipart(CHAT_ID, "photo", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        r = await ahttp.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"Photo send failed: {e}")
        return 0


async def async_send_file(ahttp: httpx.AsyncClient, bot_token: str, file_path: str, caption: str = "") -> int:
    """Send a file (async). Returns: message_id"""
    body, boundary = _build_multipart(CHAT_ID, "document", file_path, caption)
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        r = await ahttp.post(url, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=30)
        return r.json().get("result", {}).get("message_id", 0)
    except Exception as e:
        log(f"File send failed: {e}")
        return 0


# --- Text cleanup ---

_re_blank_lines = re.compile(r"\n{3,}")
_re_control_chars = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_re_md_link = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_re_bare_url = re.compile(r"https?://\S+")


def _strip_urls(text: str) -> str:
    """Markdown links → text only, bare URLs → removed"""
    text = _re_md_link.sub(r"\1", text)
    return _re_bare_url.sub("", text)


def _clean_text(text: str) -> str:
    """Strip control chars + repair broken chars + remove URLs + tidy blank lines"""
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


# --- Markdown → Telegram HTML conversion ---

def _escape_html(text: str) -> str:
    """Escape HTML special characters"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_table_to_list(text: str) -> str:
    """Markdown table → list form (Telegram has no <table> support)"""
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
    """Replace code blocks/inline code with placeholders. Returns (text, code_blocks, inline_codes)."""
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
    """Restore placeholders as HTML tags."""
    for i, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_{i}__", f"<code>{_escape_html(code)}</code>")
    for i, code in enumerate(code_blocks):
        text = text.replace(f"__CODEBLOCK_{i}__", f"<pre>{_escape_html(code)}</pre>")
    return text


def _convert_markdown_formatting(text: str) -> str:
    """Markdown formatting → Telegram HTML tags. Precedence: ***>**>*"""
    text = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


def _merge_blockquotes(text: str) -> str:
    """Merge consecutive quote lines (> ...) into a single <blockquote>."""
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
    """GitHub Flavored Markdown → Telegram HTML. Returns plain text on failure.

    Pipeline:
      tables→lists → protect code blocks → HTML-escape → formatting
      → merge quotes → remove URLs → highlight tool lines → tidy blank lines → restore code blocks
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
        log(f"HTML conversion failed: {e}")
        return _escape_html(text)


# --- Message splitting ---

def _split_message(text: str, max_len: int = 4000) -> list:
    """Split a message at natural boundaries (blank line > newline > space)"""
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


# --- Async Telegram API (for session loops) ---

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
        log(f"Telegram send failed: {e}")
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
        log(f"edit failed ({r.status_code}): {r.text[:100]}")
        return False
    except Exception as e:
        log(f"edit exception: {e}")
        return False


async def async_react(ahttp: httpx.AsyncClient, bot_token: str, msg_id: int, emoji: str = "\U0001f440"):
    """Add a reaction to the user's message (silent read receipt)."""
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
    """Global notice — broadcast to all bots (sync)"""
    sent = set()
    for name, config in PROJECTS.items():
        token = config.get("bot_token", "")
        if token and token not in sent:
            send_telegram(text, token)
            sent.add(token)


async def async_notify_all(ahttp, text: str):
    """Global notice — broadcast to all bots (async)"""
    sent = set()
    for name, config in PROJECTS.items():
        token = config.get("bot_token", "")
        if token and token not in sent:
            await async_send_telegram(ahttp, text, token)
            sent.add(token)
