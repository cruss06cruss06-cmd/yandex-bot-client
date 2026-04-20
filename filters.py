"""Фильтры в стиле aiogram: F.text == \"/start\", F.callback_data.has(\"key\"), композиция через & | ~, StateFilter по FSM."""

from typing import Any, Callable, Dict, List, Optional, Union

from .client import Bot
from .fsm import get_state


def _text_eq(value: str) -> Callable[[Dict], bool]:
    """Текст сообщения (после strip) равен value."""

    def _check(update: Dict) -> bool:
        text = (update.get("text") or "").strip()
        return text == value

    return _check


def _callback_has(key: str) -> Callable[[Dict, Dict], bool]:
    """В payload кнопки есть ключ key."""

    def _check(update: Dict, payload: Dict) -> bool:
        return key in (payload or {})

    return _check


def _callback_eq(key: str, value: Any) -> Callable[[Dict, Dict], bool]:
    """payload[key] == value."""

    def _check(update: Dict, payload: Dict) -> bool:
        return (payload or {}).get(key) == value

    return _check


class Filter:
    """Обёртка над (update) -> bool: можно комбинировать через & | ~. Вызывается как функция: filter(update)."""

    def __init__(self, func: Callable[[Dict], bool]) -> None:
        self._func = func

    def __call__(self, update: Dict) -> bool:
        return self._func(update)

    def __and__(self, other: Union["Filter", Callable[[Dict], bool]]) -> "Filter":
        if isinstance(other, Filter):
            other = other._func
        return Filter(lambda u: self._func(u) and other(u))

    def __or__(self, other: Union["Filter", Callable[[Dict], bool]]) -> "Filter":
        if isinstance(other, Filter):
            other = other._func
        return Filter(lambda u: self._func(u) or other(u))

    def __invert__(self) -> "Filter":
        return Filter(lambda u: not self._func(u))


def StateFilter(
    state_or_states: Union[str, List[str], tuple],
) -> Filter:
    """Фильтр по текущему FSM-состоянию. state_or_states — одна строка или список допустимых. Берёт Bot.current() и login из update."""
    if isinstance(state_or_states, str):
        allowed = {state_or_states}
    else:
        allowed = set(state_or_states)

    def _check(update: Dict) -> bool:
        bot = Bot.current()
        if not bot:
            return False
        user = update.get("from") if isinstance(update.get("from"), dict) else {}
        login = user.get("login") if user else None
        if not login:
            return False
        return get_state(bot, login) in allowed

    return Filter(_check)


class _TextFilter:
    """F.text == \"...\" — возвращает Filter, можно комбинировать с & | ~."""

    def __eq__(self, value: object) -> Filter:
        return Filter(_text_eq(str(value)))


class _CallbackDataFilter:
    """F.callback_data.has(\"key\") и F.callback_data[\"key\"] == value."""

    def has(self, key: str) -> Callable[[Dict, Dict], bool]:
        return _callback_has(key)

    def __getitem__(self, key: str) -> "_CallbackDataKey":
        return _CallbackDataKey(key)


class _CallbackDataKey:
    def __init__(self, key: str) -> None:
        self._key = key

    def __eq__(self, value: object) -> Callable[[Dict, Dict], bool]:
        return _callback_eq(self._key, value)


class F:
    """F.text == \"/start\" — точный текст. F.callback_data.has(\"cmd\") — есть ключ в payload. F.callback_data[\"hash\"] == \"abc\" — значение по ключу."""

    text: _TextFilter = _TextFilter()
    callback_data: _CallbackDataFilter = _CallbackDataFilter()


def and_f(
    *filters: Callable[..., bool],
) -> Callable[..., bool]:
    """Склеивает фильтры через AND. Для message — один арг (update), для callback — (update, payload)."""

    def _combined(*args: Any, **kwargs: Any) -> bool:
        return all(f(*args, **kwargs) for f in filters)

    return _combined


def or_f(
    *filters: Callable[..., bool],
) -> Callable[..., bool]:
    """Склеивает фильтры через OR."""

    def _combined(*args: Any, **kwargs: Any) -> bool:
        return any(f(*args, **kwargs) for f in filters)

    return _combined
