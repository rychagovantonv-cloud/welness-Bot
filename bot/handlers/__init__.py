from aiogram import Router

from bot.handlers.common import router as common_router
from bot.handlers.radar import router as radar_router

main_router = Router()
main_router.include_router(common_router)
main_router.include_router(radar_router)

__all__ = ["main_router"]
