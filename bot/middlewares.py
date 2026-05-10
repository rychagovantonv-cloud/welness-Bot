from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from loguru import logger

from config import settings


class WhitelistMiddleware(BaseMiddleware):
    """Пропускает только сообщения от user_id из ALLOWED_USERS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = self._extract_user_id(event)
        if user_id is None:
            return None

        if user_id not in settings.allowed_users:
            logger.warning("blocked unauthorized user", user_id=user_id)
            return None

        return await handler(event, data)

    @staticmethod
    def _extract_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                return event.message.from_user.id
            if event.callback_query and event.callback_query.from_user:
                return event.callback_query.from_user.id
            if event.edited_message and event.edited_message.from_user:
                return event.edited_message.from_user.id
        return None
