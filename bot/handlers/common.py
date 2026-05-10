from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from loguru import logger
from sqlalchemy import select

from database.client import session_scope
from database.models import RunLog

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    logger.info("start command", user_id=user.id if user else None)
    await message.answer(
        "👋 Welness Bot живой.\n\n"
        "Доступные команды:\n"
        "/help — список команд\n"
        "/status — последние запуски\n\n"
        "Phase 0: каркас. Парсеры и AI-pipeline появятся в следующей фазе."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Команды</b>\n\n"
        "<b>Общие:</b>\n"
        "/start — приветствие\n"
        "/help — этот список\n"
        "/status — статус последних запусков\n\n"
        "<b>Radar (новости + наука):</b>\n"
        "/radar_now [all|science|travel|wellness|magazines|rss] — запуск\n"
        "/radar_trash [N] — последние N помеченных мусором (default 20)\n"
        "/radar_seen [N] — последние N спарсенных (для дедупа, default 30)\n\n"
        "<b>Insight (анализ ЦА):</b>\n"
        "/insight &lt;reddit_url&gt; — разбор конкретного треда\n"
        "/insight_find &lt;тема&gt; — бот САМ ищет треды по теме",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with session_scope() as session:
        result = await session.execute(
            select(RunLog).order_by(RunLog.started_at.desc()).limit(5)
        )
        runs = result.scalars().all()

    if not runs:
        await message.answer("📊 Запусков ещё не было.")
        return

    lines = ["📊 <b>Последние запуски:</b>\n"]
    for r in runs:
        when = r.started_at.strftime("%m-%d %H:%M")
        status_icon = {"ok": "✅", "error": "❌", "partial": "⚠️"}.get(r.status or "", "▫️")
        lines.append(
            f"{status_icon} <code>{r.job_name}</code> — {when} "
            f"(всего: {r.items_total or 0}, отфильтровано: {r.items_kept or 0})"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
