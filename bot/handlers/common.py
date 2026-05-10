from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from loguru import logger
from sqlalchemy import select

from bot.budget import format_budget_line
from database.client import session_scope
from database.models import RunLog
from database.repos import runs as runs_repo

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
        "<b>📊 Общие:</b>\n"
        "/start — приветствие\n"
        "/help — этот список\n"
        "/status — последние запуски\n\n"
        "<b>📡 Радар (новости + наука):</b>\n"
        "/radar [all|science|travel|wellness|magazines|rss] — запуск\n"
        "/musor [N] — последние N помеченных мусором (default 20)\n"
        "/istoria [N] — последние N спарсенных (default 30)\n\n"
        "<b>🔍 Разбор ЦА:</b>\n"
        "/razbor &lt;url&gt; — анализ Reddit-треда или YouTube-видео\n"
        "/poisk &lt;тема&gt; — бот САМ найдёт треды по теме\n\n"
        "<b>🎯 AEO:</b>\n"
        "/aeo &lt;запрос&gt; — что AI говорит про вашу нишу + контентные пробелы\n\n"
        "<i>Старые имена тоже работают: /radar_now, /radar_trash, "
        "/radar_seen, /insight, /insight_find.</i>",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    from config import settings as _settings

    async with session_scope() as session:
        result = await session.execute(
            select(RunLog).order_by(RunLog.started_at.desc()).limit(8)
        )
        runs = result.scalars().all()
        spent = await runs_repo.get_spent_since(session, _settings.anthropic_balance_as_of)

    budget_line = format_budget_line(None, spent)

    if not runs:
        msg = "📊 Запусков ещё не было."
        if budget_line:
            msg += f"\n\n{budget_line}"
        await message.answer(msg, parse_mode="HTML")
        return

    lines = ["📊 <b>Последние запуски:</b>\n"]
    for r in runs:
        when = r.started_at.strftime("%m-%d %H:%M")
        status_icon = {"ok": "✅", "error": "❌", "partial": "⚠️"}.get(r.status or "", "▫️")
        cost_str = f"${r.cost_usd:.4f}" if r.cost_usd else "—"
        lines.append(
            f"{status_icon} <code>{r.job_name}</code> {when}  ·  "
            f"{r.items_total or 0}/{r.items_kept or 0}/{r.items_sent or 0}  ·  {cost_str}"
        )
    if budget_line:
        lines.append("")
        lines.append(budget_line)
    await message.answer("\n".join(lines), parse_mode="HTML")
