"""Telegram channel — Telegram implementation of the Channel interface.

Wraps the existing telegram_api.py functions in the Channel interface.
Calls telegram_api functions internally to stay backward compatible.
"""

import httpx

from .channel import Channel
from . import telegram_api as tg


class TelegramChannel(Channel):
    """Channel backed by a Telegram bot."""

    def __init__(self, bot_token: str, chat_id: str, bot_name: str = "", ahttp: httpx.AsyncClient = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._bot_name = bot_name
        self._ahttp = ahttp
        self._offset = 0

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def max_length(self) -> int:
        return 4096

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def chat_id(self) -> str:
        return self._chat_id

    @property
    def bot_name(self) -> str:
        return self._bot_name

    def set_ahttp(self, ahttp: httpx.AsyncClient):
        self._ahttp = ahttp

    # --- Receiving ---

    async def poll(self, timeout: int = 25) -> list:
        if not self._ahttp:
            return []
        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        params = {"offset": self._offset, "timeout": timeout, "allowed_updates": ["message", "edited_message"]}
        try:
            resp = await self._ahttp.get(url, params=params, timeout=timeout + 10)
            data = resp.json()
        except Exception:
            return []

        if not data.get("ok"):
            return []

        messages = []
        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            is_edited = "edited_message" in update
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            text = msg.get("text", "") or msg.get("caption", "")
            files = []
            # Image
            if msg.get("photo"):
                files.append({"type": "photo", "file_id": msg["photo"][-1]["file_id"]})
            # Document
            if msg.get("document"):
                files.append({"type": "document", "file_id": msg["document"]["file_id"],
                              "name": msg["document"].get("file_name", "")})
            raw = dict(msg)
            raw["_is_edited"] = is_edited
            messages.append({
                "id": str(msg.get("message_id", "")),
                "text": text,
                "from_id": str(msg.get("chat", {}).get("id", "")),
                "reply_to": str(msg.get("reply_to_message", {}).get("message_id", "")) if msg.get("reply_to_message") else "",
                "files": files,
                "date": msg.get("date", 0),
                "_raw": raw,  # for access to Telegram-only fields
            })
        return messages

    # --- Sending ---

    async def send(self, text: str, reply_to: str = "", use_markup: bool = False) -> str:
        if not self._ahttp:
            return ""
        mid = await tg.async_send_telegram(
            self._ahttp, text, self._bot_token, self._bot_name,
            use_html=use_markup, reply_to=int(reply_to) if reply_to else 0)
        return str(mid)

    async def edit(self, message_id: str, text: str, use_markup: bool = False) -> bool:
        if not self._ahttp:
            return False
        return await tg.async_edit_telegram(
            self._ahttp, text, int(message_id), self._bot_token, self._bot_name,
            use_html=use_markup)

    async def delete(self, message_id: str) -> bool:
        if not self._ahttp:
            return False
        url = f"https://api.telegram.org/bot{self._bot_token}/deleteMessage"
        try:
            resp = await self._ahttp.post(url, json={"chat_id": self._chat_id, "message_id": int(message_id)})
            return resp.json().get("ok", False)
        except Exception:
            return False

    async def react(self, message_id: str, emoji: str = "\U0001f440") -> bool:
        if not self._ahttp:
            return False
        try:
            await tg.async_react(self._ahttp, self._bot_token, int(message_id), emoji)
            return True
        except Exception:
            return False

    # --- Sending (sync) ---

    def send_sync(self, text: str, use_markup: bool = False, notify: bool = False) -> str:
        mid = tg.send_telegram(text, self._bot_token, self._bot_name,
                               use_html=use_markup, notify=notify)
        return str(mid)

    # --- File sending ---

    async def send_photo(self, file_path: str, caption: str = "") -> str:
        if not self._ahttp:
            return ""
        mid = await tg.async_send_photo(self._ahttp, self._bot_token, file_path, caption)
        return str(mid)

    async def send_file(self, file_path: str, caption: str = "") -> str:
        if not self._ahttp:
            return ""
        mid = await tg.async_send_file(self._ahttp, self._bot_token, file_path, caption)
        return str(mid)

    def send_photo_sync(self, file_path: str, caption: str = "") -> str:
        mid = tg.send_photo_sync(self._bot_token, file_path, caption)
        return str(mid)

    def send_file_sync(self, file_path: str, caption: str = "") -> str:
        mid = tg.send_file_sync(self._bot_token, file_path, caption)
        return str(mid)

    # --- File download ---

    async def download_file(self, file_ref: str) -> bytes:
        if not self._ahttp:
            return b""
        # getFile → download
        url = f"https://api.telegram.org/bot{self._bot_token}/getFile"
        try:
            resp = await self._ahttp.get(url, params={"file_id": file_ref}, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                return b""
            file_path = data["result"]["file_path"]
            dl_url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"
            dl_resp = await self._ahttp.get(dl_url, timeout=30)
            return dl_resp.content
        except Exception:
            return b""

    # --- Formatting ---

    def format(self, markdown_text: str) -> str:
        return tg._md_to_telegram_html(markdown_text)

    def split(self, text: str) -> list:
        return tg._split_message(text)

    # --- Broadcast ---

    def broadcast_sync(self, text: str):
        tg._notify_all(text)

    async def broadcast(self, text: str):
        if self._ahttp:
            await tg.async_notify_all(self._ahttp, text)

    # --- Offset management ---

    def get_offset(self) -> int:
        return self._offset

    def set_offset(self, offset: int):
        self._offset = offset
