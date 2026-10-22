"""Abstract channel interface — common API for messaging platforms (Telegram, Discord, ...).

Usage:
    channel = TelegramChannel(bot_token, chat_id)
    msg_id = await channel.send("Hello")
    await channel.edit(msg_id, "Hello, updated")
"""

from abc import ABC, abstractmethod


class Channel(ABC):
    """Abstract interface for receiving/sending messages."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name (e.g. "telegram", "discord")"""

    @property
    @abstractmethod
    def max_length(self) -> int:
        """Max message length (Telegram: 4096, Discord: 2000)"""

    # --- Receiving ---

    @abstractmethod
    async def poll(self, timeout: int = 25) -> list:
        """Poll for new messages.
        Returns: [{"id": str, "text": str, "from_id": str, "reply_to": str|None, "files": [...]}]
        """

    # --- Sending ---

    @abstractmethod
    async def send(self, text: str, reply_to: str = "", use_markup: bool = False) -> str:
        """Send a message. Returns: message_id (str)"""

    @abstractmethod
    async def edit(self, message_id: str, text: str, use_markup: bool = False) -> bool:
        """Edit a message."""

    @abstractmethod
    async def delete(self, message_id: str) -> bool:
        """Delete a message."""

    @abstractmethod
    async def react(self, message_id: str, emoji: str = "") -> bool:
        """Add a reaction/emoji."""

    # --- Sending (sync, for command replies) ---

    @abstractmethod
    def send_sync(self, text: str, use_markup: bool = False, notify: bool = False) -> str:
        """Send a message (sync). Used by commands.py etc."""

    # --- File sending ---

    @abstractmethod
    async def send_photo(self, file_path: str, caption: str = "") -> str:
        """Send an image. Returns: message_id (str)"""

    @abstractmethod
    async def send_file(self, file_path: str, caption: str = "") -> str:
        """Send a file. Returns: message_id (str)"""

    def send_photo_sync(self, file_path: str, caption: str = "") -> str:
        """Send an image (sync). For CLI/hooks."""
        return ""

    def send_file_sync(self, file_path: str, caption: str = "") -> str:
        """Send a file (sync). For CLI/hooks."""
        return ""

    # --- File download ---

    async def download_file(self, file_ref: str) -> bytes:
        """Download a file. file_ref is platform-specific (Telegram: file_id)."""
        return b""

    # --- Formatting ---

    def format(self, markdown_text: str) -> str:
        """Markdown → platform-native format.
        Default: returned unchanged. Telegram converts to HTML."""
        return markdown_text

    def split(self, text: str) -> list:
        """Split a message to fit platform limits.
        Default: simple split at max_length."""
        if len(text) <= self.max_length:
            return [text]
        chunks = []
        while text:
            if len(text) <= self.max_length:
                chunks.append(text)
                break
            # Split on newlines
            cut = text.rfind("\n", 0, self.max_length)
            if cut < self.max_length // 2:
                cut = self.max_length
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        return chunks

    # --- Broadcast (notify multiple bots) ---

    def broadcast_sync(self, text: str):
        """Sync notice to all channels. Default: send_sync."""
        self.send_sync(text)

    async def broadcast(self, text: str):
        """Async notice to all channels. Default: send."""
        await self.send(text)
