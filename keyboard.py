"""Сборка inline-клавиатур. API ждёт кнопки с text и callback_data (dict; cmd уходит в button_handler)."""

from typing import Any, Dict, List, Optional


class Keyboard:
    """Ряды кнопок через .row(...). В конце — .build() и передать в send_message(..., keyboard=...)."""

    def __init__(self) -> None:
        self._rows: List[List[Dict[str, Any]]] = []

    @staticmethod
    def button(
        text: str,
        cmd: Optional[str] = None,
        *,
        callback_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Одна кнопка. cmd — команда при нажатии (в button_handler попадёт без слэша). callback_data — свой dict; если есть cmd, он туда добавится. Только свои поля без cmd — обрабатывай в callback_handler. url — кнопка-ссылка."""
        data = callback_data.copy() if callback_data else {}
        if cmd:
            data["cmd"] = cmd if cmd.startswith("/") else f"/{cmd}"
        btn: Dict[str, Any] = {"text": text, "callback_data": data}
        if url:
            btn["url"] = url
        return btn

    def row(self, *buttons: Dict[str, Any]) -> "Keyboard":
        """Добавляет ряд кнопок. Можно несколько кнопок в один ряд. Возвращает self для цепочки."""
        self._rows.append(list(buttons))
        return self

    def build(self) -> List[List[Dict[str, Any]]]:
        """Готовый формат для send_message(..., keyboard=...)."""
        return self._rows

    @staticmethod
    def from_rows(rows: List[List[Dict[str, Any]]]) -> List[List[Dict[str, Any]]]:
        """Собрать из готовых рядов (список списков кнопок)."""
        return rows
