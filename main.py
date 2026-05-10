import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web
from loguru import logger

from bot.handlers import main_router
from bot.middlewares import WhitelistMiddleware
from config import settings
from database.client import engine, ping
from observability.logging import setup_logging
from observability.sentry import setup_sentry


async def healthcheck(request: web.Request) -> web.Response:
    try:
        ok = await ping()
    except Exception as e:
        logger.error("healthcheck db ping failed: {}", e)
        return web.json_response({"status": "degraded", "db": False}, status=503)
    return web.json_response({"status": "ok", "db": ok})


async def start_healthcheck_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", healthcheck)
    app.router.add_get("/", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logger.info("healthcheck server started", port=settings.port)
    return runner


async def main() -> None:
    setup_logging()
    setup_sentry()

    logger.info("starting welness bot", env=settings.env, allowed_users=settings.allowed_users)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware())
    dp.include_router(main_router)

    runner = await start_healthcheck_server()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("polling started")
        await dp.start_polling(bot, handle_signals=True)
    finally:
        logger.info("shutting down")
        await runner.cleanup()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("interrupted")
