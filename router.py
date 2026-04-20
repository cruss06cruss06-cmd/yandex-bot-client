"""Роутер — группа хендлеров, подключается к боту через include_router. Удобно разнести логику по модулям (меню, оплаты и т.д.)."""

from typing import Any, Callable, Dict, List, Optional


def _default_callback_filter(update: Dict, payload: Dict) -> bool:
    return True


class Router:
    """Куча обработчиков с тем же API, что у Bot. При include_router бот копирует их в себя — роутер на бота не ссылается."""

    def __init__(self) -> None:
        pass
        self._handlers: List[Dict[str, Any]] = []
        self._button_handlers: List[Dict[str, Any]] = []
        self._callback_handlers: List[Dict[str, Any]] = []
        self._default_handlers: List[Dict[str, Any]] = []

    def message_handler(
        self,
        text: Optional[str] = None,
        *,
        filters: Optional[Callable[[Dict], bool]] = None,
        state: Optional[str] = None,
    ) -> Callable[[Callable], Callable]:
        """Как Bot.message_handler: text, filters, state."""

        def decorator(func: Callable) -> Callable:
            self._handlers.append({
                "text": text,
                "filter": filters,
                "state": state,
                "func": func,
            })
            return func

        return decorator

    def button_handler(
        self,
        action: str,
        *,
        state: Optional[str] = None,
    ) -> Callable[[Callable], Callable]:
        """Как Bot.button_handler: action без слэша, опционально state."""

        def decorator(func: Callable) -> Callable:
            self._button_handlers.append({
                "action": action,
                "state": state,
                "func": func,
            })
            return func

        return decorator

    def callback_handler(
        self,
        func: Optional[Callable] = None,
        *,
        filters: Optional[Callable[[Dict, Dict], bool]] = None,
    ) -> Callable[[Callable], Callable]:
        """Как Bot.callback_handler: кнопки без cmd или с произвольным payload. filters — (update, payload) -> bool."""

        def decorator(f: Callable) -> Callable:
            self._callback_handlers.append({
                "filter": filters or _default_callback_filter,
                "func": f,
            })
            return f

        if func is not None:
            return decorator(func)
        return decorator

    def default_handler(
        self,
        *,
        state: Optional[str] = None,
    ) -> Callable[[Callable], Callable]:
        """Как Bot.default_handler: вызывается, когда ни один message_handler не подошёл."""

        def decorator(func: Callable) -> Callable:
            self._default_handlers.append({
                "state": state,
                "func": func,
            })
            return func

        return decorator

    def _merge_into(self, bot: Any) -> None:
        """Копирует все хендлеры роутера в бота (вызывает include_router)."""
        bot._handlers.extend(self._handlers)
        bot._button_handlers.extend(self._button_handlers)
        bot._callback_handlers.extend(self._callback_handlers)
        bot._default_handlers.extend(self._default_handlers)
