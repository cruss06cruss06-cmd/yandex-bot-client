"""FSM по пользователю: состояния в bot._fsm_states, отдельно от bot.state(login), чтобы не пересекаться с твоими ключами."""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .client import Bot


class State:
    """Наследуй и задавай атрибуты-строки (main = \"main\" и т.д.). Используй в message_handler(..., state=...) и в set_state/get_state."""

    pass


def get_state(bot: "Bot", login: str) -> Optional[str]:
    """Текущее FSM-состояние пользователя. None, если не задано."""
    return getattr(bot, "_fsm_states", {}).get(login)


def set_state(bot: "Bot", login: str, state: Optional[str]) -> None:
    """Ставит FSM-состояние. state=None — сброс."""
    storage = getattr(bot, "_fsm_states", None)
    if storage is None:
        return
    if state is None:
        storage.pop(login, None)
    else:
        storage[login] = state


def clear_state(bot: "Bot", login: str) -> None:
    """Сброс FSM для пользователя (то же что set_state(bot, login, None))."""
    set_state(bot, login, None)


class FSMContext:
    """Удобная обёртка: работа с FSM для текущего пользователя (берёт current_login). Только из хендлера."""

    def __init__(self, bot: "Bot") -> None:
        self._bot = bot

    def get_state(self) -> Optional[str]:
        """Текущее состояние текущего пользователя."""
        login = self._bot.current_login()
        if not login:
            return None
        return get_state(self._bot, login)

    def set_state(self, state: Optional[str]) -> None:
        """Ставит состояние текущему пользователю."""
        login = self._bot.current_login()
        if login:
            set_state(self._bot, login, state)

    def clear_state(self) -> None:
        """Сбрасывает состояние текущему пользователю."""
        login = self._bot.current_login()
        if login:
            clear_state(self._bot, login)
