from aiogram import Router

from bot.handlers.common import router as common_router

main_router = Router()
main_router.include_router(common_router)

__all__ = ["main_router"]
