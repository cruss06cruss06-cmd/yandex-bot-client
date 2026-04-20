"""Клиент Bot API Яндекс.Мессенджера: long polling, сообщения, кнопки, сессия по пользователю. Роутеры, F, FSM, Message/CallbackQuery — по аналогии с aiogram."""

import asyncio
import contextvars
import json
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

if TYPE_CHECKING:
    from .router import Router

import aiohttp
from loguru import logger

from .fsm import get_state
from .keyboard import Keyboard
from .middleware import Middleware
from .types import CallbackQuery, Message
import os


BASE_URL = "https://botapi.messenger.yandex.net/bot/v1"

# Кто сейчас обрабатывается — чтобы reply() и Bot.current() работали без глобального bot.
_current_login: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_login", default=None
)
_current_bot: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "current_bot", default=None
)


class Bot:
    """Клиент к Bot API: long polling, сообщения, кнопки, сессия по login. Обработчики — текст, кнопки по cmd, callback, default."""

    def __init__(self, api_key: str, *, log: Optional[Any] = None) -> None:
        """api_key — OAuth-токен из настроек бота в Мессенджере. log — свой логгер или по умолчанию loguru."""
        self.api_key = api_key
        self._log = log if log is not None else logger
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_update_id = 0
        self._running = False

        self._handlers: List[Dict[str, Any]] = []
        self._button_handlers: List[Dict[str, Any]] = []
        self._callback_handlers: List[Dict[str, Any]] = []
        self._default_handlers: List[Dict[str, Any]] = []
        self._middlewares: List[Middleware] = []

        self._user_states: Dict[str, dict] = {}
        self._fsm_states: Dict[str, str] = {}  # FSM по login; отдельно от state(login), чтобы не пересекаться с твоими ключами
        self._pending_tasks: Set[asyncio.Task] = set()

    @staticmethod
    def current() -> Optional["Bot"]:
        """Бот, который сейчас обрабатывает обновление. Только из хендлера — иначе None (из другой задачи контекста нет)."""
        return _current_bot.get()

    def state(self, login: str) -> dict:
        """Словарь данных пользователя по login (свой для каждого). Туда — выбранные значения, email и т.п. FSM лежит отдельно в get_state/set_state."""
        if login not in self._user_states:
            self._user_states[login] = {}
        return self._user_states[login]

    def message_handler(
        self,
        text: Optional[str] = None,
        *,
        filters: Optional[Callable[[Dict], bool]] = None,
        state: Optional[str] = None,
    ) -> Callable:
        """Вешает обработчик на текст. text — команда вроде "/start" или None на любое. filters — доп. проверка по update. state — только в этом FSM-состоянии. Вернуть False — передать дальше по цепочке или в default."""

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
    ) -> Callable:
        """Обработчик нажатия кнопки по cmd. action — как в кнопке, без слэша (cmd="/yes" → "yes"). state — опционально."""

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
    ) -> Callable:
        """Обработчик для кнопок без cmd или с произвольным payload (hash и т.д.). Вызывается, если button_handler по cmd не нашёлся. filters — (update, payload) -> bool."""

        def decorator(f: Callable) -> Callable:
            self._callback_handlers.append({
                "filter": filters if filters is not None else (lambda u, p: True),
                "func": f,
            })
            return f

        if func is not None:
            return decorator(func)
        return decorator

    def default_handler(
        self,
        func: Optional[Callable] = None,
        *,
        state: Optional[str] = None,
    ) -> Callable:
        """Вызывается для текста, когда ни один message_handler не подошёл. state — при желании ограничить по FSM."""

        def decorator(f: Callable) -> Callable:
            self._default_handlers.append({"state": state, "func": f})
            return f

        if func is not None:
            return decorator(func)
        return decorator

    def include_router(self, router: "Router") -> "Bot":
        """Добавляет обработчики роутера в конец. Порядок: свои хендлеры, потом роутеры по порядку include_router. Срабатывает первый подходящий — порядок важен."""
        from .router import Router as RouterCls
        if isinstance(router, RouterCls):
            router._merge_into(self)
        return self

    def middleware(self, mw: Middleware) -> Middleware:
        """Добавляет middleware в цепочку. Сигнатура: async (handler, event, data) -> await handler(event, data). Вызов — по порядку регистрации."""
        self._middlewares.append(mw)
        return mw

    async def _run_middleware_chain(
        self,
        event: Any,
        data: Dict[str, Any],
        final_handler: Callable,
    ) -> Any:
        """Гоняет event и data по цепочке middlewares, в конце — final_handler(event, data)."""
        async def run(i: int, e: Any, d: Dict[str, Any]) -> Any:
            if i >= len(self._middlewares):
                return await final_handler(e, d)

            async def next_h(e2: Any, d2: Dict[str, Any]) -> Any:
                return await run(i + 1, e2, d2)
            return await self._middlewares[i](next_h, e, d)
        return await run(0, event, data)

    def _keyboard_for_api(self, keyboard: Optional[List[List[Dict]]]) -> Optional[List[Dict]]:
        """Клавиатура в формат API — плоский список кнопок."""
        if not keyboard:
            return None
        flat = []
        for row in keyboard:
            for btn in row:
                b = {"text": btn.get("text", "")}
                cd = btn.get("callback_data") or btn.get("callbackData")
                if cd is not None:
                    b["callback_data"] = (
                        cd if isinstance(cd, dict) else json.loads(cd) if isinstance(cd, str) else cd
                    )
                # Здесь должен быть код для url!
                if "url" in btn:
                    b["url"] = btn["url"]
                flat.append(b)
        return flat

    async def send_message(
        self,
        login: str,
        text: str,
        keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """Шлёт текст пользователю по login. keyboard — результат Keyboard().build(), можно не передавать. Возвращает message_id или None."""
        if not self._session:
            return None
        payload: Dict[str, Any] = {"text": text, "login": login}
        if keyboard is not None:
            payload["inline_keyboard"] = self._keyboard_for_api(keyboard)
        try:
            async with self._session.post(f"{BASE_URL}/messages/sendText", json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    self._log.error("send_message {}: {}", resp.status, body)
                    return None
                data = json.loads(body) if body else {}
                return data.get("message_id")
        except Exception as e:
            self._log.exception("send_message: {}", e)
            return None

    async def reply(
        self,
        text: str,
        keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """Шлёт сообщение тому, кто написал/нажал. Только из хендлера — из create_task контекста нет, вернёт None и warning."""
        login = _current_login.get()
        if not login:
            self._log.warning("reply() вызван вне контекста обновления")
            return None
        return await self.send_message(login, text, keyboard)

    async def upload_file(self, file_path: str) -> Optional[Dict]:
        """
        Загружает файл на сервер и возвращает информацию о файле.

        Args:
            file_path: путь к файлу на диске

        Returns:
            словарь с информацией о файле (file_id и др.) или None
        """
        if not self._session:
            return None

        url = f"{BASE_URL}/messages/sendFile"

        try:
            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData()
                form_data.add_field('file', f, filename=os.path.basename(file_path))

                async with self._session.post(url, data=form_data) as resp:
                    if resp.status != 200:
                        self._log.error(f"upload_file error: {resp.status}")
                        return None
                    return await resp.json()
        except Exception as e:
            self._log.exception(f"upload_file: {e}")
            return None

    async def send_file(
            self,
            login: str,
            file_id: str,
            caption: Optional[str] = None,
            keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """
        Отправляет файл пользователю по file_id.

        Args:
            login: логин получателя
            file_id: идентификатор загруженного файла
            caption: подпись к файлу
            keyboard: клавиатура

        Returns:
            message_id или None
        """
        if not self._session:
            return None

        payload: Dict[str, Any] = {
            "login": login,
            "file_id": file_id,
        }

        if caption:
            payload["text"] = caption

        if keyboard is not None:
            payload["inline_keyboard"] = self._keyboard_for_api(keyboard)

        try:
            async with self._session.post(f"{BASE_URL}/messages/sendFile", json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    self._log.error("send_file {}: {}", resp.status, body)
                    return None
                data = json.loads(body) if body else {}
                return data.get("message_id")
        except Exception as e:
            self._log.exception("send_file: {}", e)
            return None

    async def send_photo(
            self,
            login: str,
            photo: str,  # путь к файлу или file_id
            caption: Optional[str] = None,
            keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """
        Отправляет фото пользователю.

        Args:
            login: логин получателя
            photo: путь к файлу или file_id
            caption: подпись
            keyboard: клавиатура
        """
        if os.path.isfile(photo):  # если это файл на диске
            file_info = await self.upload_file(photo)
            if not file_info:
                return None
            file_id = file_info.get("file_id") or file_info.get("id")
        else:  # это уже file_id
            file_id = photo

        return await self.send_file(login, file_id, caption, keyboard)

    async def send_document(
            self,
            login: str,
            document: str,  # путь к файлу или file_id
            caption: Optional[str] = None,
            keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """Отправляет документ пользователю."""
        return await self.send_photo(login, document, caption, keyboard)

    async def reply_photo(
            self,
            photo: str,
            caption: Optional[str] = None,
            keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """Отправляет фото текущему пользователю."""
        login = _current_login.get()
        if not login:
            self._log.warning("reply_photo() вызван вне контекста обновления")
            return None
        return await self.send_photo(login, photo, caption, keyboard)

    async def reply_document(
            self,
            document: str,
            caption: Optional[str] = None,
            keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[int]:
        """Отправляет документ текущему пользователю."""
        login = _current_login.get()
        if not login:
            self._log.warning("reply_document() вызван вне контекста обновления")
            return None
        return await self.send_document(login, document, caption, keyboard)

    async def send_document_bytes(
            self,
            login: str,
            document_data: bytes,
            filename: str,
            thread_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Отправляет документ из байтов.

        Args:
            login: логин получателя
            document_data: байтовые данные документа
            filename: имя файла (например, "report.xlsx", "document.pdf")
            thread_id: идентификатор треда (опционально)

        Returns:
            message_id или None
        """
        if not self._session:
            return None

        url = f"{BASE_URL}/messages/sendFile/"  # Endpoint для документов

        safe_filename = filename.replace(' ', '_').replace('(', '').replace(')', '')

        form_data = aiohttp.FormData()
        form_data.add_field('login', login)
        form_data.add_field('document', document_data, filename=safe_filename)

        if thread_id:
            form_data.add_field('thread_id', str(thread_id))

        try:
            async with self._session.post(url, data=form_data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self._log.error(f"send_document_bytes error {resp.status}: {body}")
                    return None
                data = await resp.json()
                return data.get("message_id")
        except Exception as e:
            self._log.exception("send_document_bytes: {}", e)
            return None

    async def reply_document_bytes(
            self,
            document_data: bytes,
            filename: str,
            thread_id: Optional[int] = None,
    ) -> Optional[int]:
        """Отправляет документ текущему пользователю."""
        login = _current_login.get()
        if not login:
            self._log.warning("reply_document_bytes() вызван вне контекста обновления")
            return None
        return await self.send_document_bytes(login, document_data, filename, thread_id)

    async def send_image_bytes(
            self,
            login: str,
            image_data: bytes,
            thread_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Отправляет изображение из байтов.

        Args:
            login: логин получателя
            image_data: байтовые данные изображения
            thread_id: идентификатор треда (опционально)
        """
        if not self._session:
            return None

        url = f"{BASE_URL}/messages/sendImage/"  # ВАЖНО: правильный endpoint

        form_data = aiohttp.FormData()
        form_data.add_field('login', login)
        form_data.add_field('image', image_data, filename='image.jpg')

        if thread_id:
            form_data.add_field('thread_id', str(thread_id))

        try:
            async with self._session.post(url, data=form_data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self._log.error(f"send_image_bytes error {resp.status}: {body}")
                    return None
                data = await resp.json()
                return data.get("message_id")
        except Exception as e:
            self._log.exception("send_image_bytes: {}", e)
            return None

    async def reply_image_bytes(
            self,
            image_data: bytes,
            thread_id: Optional[int] = None,
    ) -> Optional[int]:
        """Отправляет изображение текущему пользователю."""
        login = _current_login.get()
        if not login:
            self._log.warning("reply_image_bytes() вызван вне контекста обновления")
            return None
        return await self.send_image_bytes(login, image_data, thread_id)

    def current_login(self) -> Optional[str]:
        """Логин того, чьё обновление сейчас в работе. Удобно для bot.state(bot.current_login()). Вне хендлера — None."""
        return _current_login.get()

    def _parse_update(self, update: Dict) -> Optional[tuple]:
        """Достаёт из update login, text и payload (если кнопка). Единая точка входа под API — меняешь только тут."""
        try:
            user = update.get("from") if isinstance(update.get("from"), dict) else {}
            login = user.get("login") if user else None
            if not login or not isinstance(login, str):
                return None
            text = (update.get("text") or "").strip() if isinstance(update.get("text"), (str, type(None))) else ""
            raw = update.get("callbackData") or update.get("callback_data") or update.get("payload")
            if raw is None:
                return (login, text, None)
            if isinstance(raw, dict):
                return (login, text, raw)
            if isinstance(raw, str):
                try:
                    return (login, text, json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        except (AttributeError, TypeError, KeyError) as e:
            self._log.warning("parse_update: invalid structure: {}", e)
            return None

    async def _get_updates(self) -> List[Dict]:
        """Забирает новые обновления. При ошибке сети — [], в лог warning, цикл не падает."""
        if not self._session:
            return []
        url = f"{BASE_URL}/messages/getUpdates?offset={self._last_update_id + 1}&limit=10"
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with self._session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                updates = data.get("updates", [])
                if updates:
                    self._last_update_id = updates[-1]["update_id"]
                return updates
        except (aiohttp.ClientError, OSError, ConnectionError, asyncio.TimeoutError) as e:
            self._log.warning("get_updates (сеть): {} — повтор через паузу", e)
            return []
        except Exception as e:
            self._log.warning("get_updates: {} — повтор через паузу", e)
            return []

    async def _process_update(self, update: Dict) -> None:
        """Один update: кнопка → _handle_callback, иначе — подбор message/default handler."""
        parsed = self._parse_update(update)
        if not parsed:
            return
        login, text, payload = parsed

        if payload is not None:
            await self._handle_callback(update, login, payload)
            return

        token_login = _current_login.set(login)
        token_bot = _current_bot.set(self)
        try:
            current_state = get_state(self, login)
            handled = False
            event = Message(update)
            data: Dict[str, Any] = {}
            for h in self._handlers:
                if h["state"] is not None and h["state"] != current_state:
                    continue
                if h["text"] is not None and h["text"] != text:
                    continue
                if h.get("filter") is not None and not h["filter"](update):
                    continue
                try:
                    if self._middlewares:
                        async def _final(e: Any, d: Dict[str, Any], func: Callable = h["func"]) -> Any:
                            return await func(e, **d)
                        result = await self._run_middleware_chain(event, dict(data), _final)
                    else:
                        result = await h["func"](event)
                    if result is not False:
                        handled = True
                        break
                except Exception as e:
                    self._log.exception("handler: {}", e)
            if not handled:
                logger.info(f"Пришёл хэндлке: {handled}")
                for h in self._default_handlers:
                    if h["state"] is not None and h["state"] != current_state:
                        continue
                    try:
                        if self._middlewares:
                            async def _final_def(e: Any, d: Dict[str, Any], func: Callable = h["func"]) -> Any:
                                return await func(e, **d)
                            await self._run_middleware_chain(event, dict(data), _final_def)
                        else:
                            await h["func"](event)
                        handled = True
                        break
                    except Exception as e:
                        self._log.exception("default_handler: {}", e)
                if not handled:
                    await self.reply("Не понимаю. Введите /start или /menu.")
                    logger.info(f"Непонятный текст: {event}")
        finally:
            _current_login.reset(token_login)
            _current_bot.reset(token_bot)

    async def _handle_callback(self, update: Dict, login: str, payload: Dict) -> None:
        """Кнопка: сначала button_handler по cmd, если нет — callback_handler по фильтру."""
        token_login = _current_login.set(login)
        token_bot = _current_bot.set(self)
        try:
            current_state = get_state(self, login)
            cmd = payload.get("cmd") or payload.get("action")
            cb_event = CallbackQuery(update, payload)
            cb_data: Dict[str, Any] = {}
            # logger.info(f"Пришёл payload: {payload}")
            # logger.info(f"Пришёл cmd: {cmd}")
            # logger.info(f"Пришёл current_state: {current_state}")
            # logger.info(f"Пришёл cb_event: {cb_event}")
            # logger.info(f"Пришёл cb_data: {cb_data}")
            if cmd:
                # logger.info(f"Пришёл cmd: {cmd}")
                action = (cmd.lstrip("/") if isinstance(cmd, str) else str(cmd))
                for h in self._button_handlers:
                    # mlogger.info(f"Пришёл хэндлке в cmd: {h}")
                    if h["action"] != action:
                        continue
                    if h["state"] is not None and h["state"] != current_state:
                        continue
                    try:
                        if self._middlewares:
                            async def _final_btn(e: Any, d: Dict[str, Any], func: Callable = h["func"]) -> Any:
                                return await func(e, **d)
                            await self._run_middleware_chain(cb_event, dict(cb_data), _final_btn)
                        else:
                            await h["func"](cb_event)
                        return
                    except Exception as e:
                        self._log.exception("button handler: {}", e)
                        await self.reply("Ошибка при обработке действия.")
                        return
            for h in self._callback_handlers:
                logger.info(f"Пришёл хэндлке: {h}")
                if not h["filter"](update, payload):
                    logger.info(f"Пришёл хэндлке: {h}")
                    continue
                try:
                    if self._middlewares:
                        async def _final_cb(e: Any, d: Dict[str, Any], func: Callable = h["func"]) -> Any:
                            return await func(e, **d)
                        await self._run_middleware_chain(cb_event, dict(cb_data), _final_cb)
                    else:
                        await h["func"](cb_event)
                    return
                except Exception as e:
                    self._log.exception("callback_handler: {}", e)
            await self.reply("Неизвестное действие.")
        finally:
            _current_login.reset(token_login)
            _current_bot.reset(token_bot)

    def _task_done_callback(self, task: asyncio.Task) -> None:
        """Снимает задачу с учёта, при исключении — логирует."""
        self._pending_tasks.discard(task)
        try:
            exc = task.exception()
            if exc is not None:
                self._log.exception("update task: {}", exc)
        except asyncio.CancelledError:
            pass

    async def run(self) -> None:
        """Long polling до остановки. Каждое обновление — отдельная задача (до 128 параллельно). Остановка — Ctrl+C или stop(); перед выходом ждёт активные задачи до 10 с."""
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"OAuth {self.api_key}",
                # "Content-Type": "application/json",  # НЕ НАДО!
            }
        )
        self._running = True
        semaphore = asyncio.Semaphore(128)
        self._log.info("Bot started")
        try:
            while self._running:
                try:
                    updates = await self._get_updates()
                    for u in updates:
                        async def process_one(update: Dict) -> None:
                            async with semaphore:
                                await self._process_update(update)
                        task = asyncio.create_task(process_one(u))
                        self._pending_tasks.add(task)
                        task.add_done_callback(self._task_done_callback)
                except asyncio.CancelledError:
                    break
                except (OSError, ConnectionError, asyncio.TimeoutError) as e:
                    self._log.warning("Сеть: {} — пауза 15 с", e)
                    await asyncio.sleep(15)
                except Exception as e:
                    self._log.exception("process_updates: {}", e)
                    await asyncio.sleep(5)
                else:
                    # были обновления — мало ждём, быстрее подхватим следующие; пусто — дольше, чтобы не долбить API
                    await asyncio.sleep(0.2 if updates else 1)
        finally:
            if self._pending_tasks:
                done, pending = await asyncio.wait(
                    self._pending_tasks, timeout=10.0, return_when=asyncio.ALL_COMPLETED
                )
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            await self._session.close()
            self._session = None
            self._running = False
            self._log.info("Bot stopped")

    def stop(self) -> None:
        """Останавливает цикл — run() выйдет на следующей итерации."""
        self._running = False
