"""Middleware — цепочка до хендлера. async (handler, event, data) -> await handler(event, data). event — Message или CallbackQuery, data можно дополнять. Регистрация: bot.middleware(mw), вызов по порядку."""

from typing import Any, Awaitable, Callable, Dict, Union

from .types import CallbackQuery, Message

Handler = Callable[..., Awaitable[Any]]
Middleware = Callable[
    [Handler, Union[Message, CallbackQuery], Dict[str, Any]],
    Awaitable[Any],
]


def noop_middleware(handler: Handler, event: Union[Message, CallbackQuery], data: Dict[str, Any]) -> Awaitable[Any]:
    """Просто прокидывает в следующий в цепочке."""
    return handler(event, data)
