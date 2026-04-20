"""Message и CallbackQuery — обёртки над update. В message_handler приходит Message, в button/callback_handler — CallbackQuery."""

from typing import Any, Dict, Optional


class User:
    """Пользователь из update["from"]. Поля: id, login, display_name, robot. Могут отсутствовать в ответе API."""

    __slots__ = ("id", "login", "display_name", "robot", "_raw")

    def __init__(self, data: Optional[Dict] = None) -> None:
        data = data or {}
        self.id: Optional[str] = data.get("id")
        self.login: Optional[str] = data.get("login")
        self.display_name: Optional[str] = data.get("display_name")
        self.robot: bool = bool(data.get("robot"))
        self._raw: Dict = data

    def __repr__(self) -> str:
        return f"User(login={self.login!r})"


class Message:
    """Текстовое сообщение. Атрибуты: text, message_id, from_user, chat, update_id, timestamp. raw — исходный update, если нужны поля вне типа."""

    __slots__ = ("text", "message_id", "from_user", "chat", "update_id", "timestamp", "raw")

    def __init__(self, update: Dict[str, Any]) -> None:
        self.raw: Dict[str, Any] = update
        self.text: str = (update.get("text") or "").strip()
        self.message_id: Optional[int] = update.get("message_id")
        self.from_user: User = User(update.get("from"))
        self.chat: Optional[Dict] = update.get("chat")
        self.update_id: Optional[int] = update.get("update_id")
        self.timestamp: Optional[int] = update.get("timestamp")

    def __repr__(self) -> str:
        return f"Message(text={self.text[:20]!r}...)" if len(self.text) > 20 else f"Message(text={self.text!r})"


class CallbackQuery:
    """Нажатие кнопки. from_user, payload (callback_data), data — то же что payload, message_id, update_id. raw_update / raw_payload — сырые dict."""

    __slots__ = ("from_user", "payload", "data", "message_id", "update_id", "raw_update", "raw_payload")

    def __init__(self, update: Dict[str, Any], payload: Dict[str, Any]) -> None:
        self.raw_update: Dict[str, Any] = update
        self.raw_payload: Dict[str, Any] = payload
        self.from_user: User = User(update.get("from"))
        self.payload: Dict[str, Any] = payload
        self.data: Dict[str, Any] = payload  # alias, как в aiogram
        self.message_id: Optional[int] = update.get("message_id")
        self.update_id: Optional[int] = update.get("update_id")

    def __repr__(self) -> str:
        return f"CallbackQuery(payload={self.payload!r})"
