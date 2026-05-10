"""Insight: pull-режим. /insight <reddit_url> → анализ ЦА.

Поток:
1. Парсим тред (Reddit .json).
2. Формируем текст для LLM.
3. Claude → InsightReport (Pydantic).
4. Рендерим: header → pain_points (по одному сообщению на каждую) → tail с кнопкой save.
5. Кнопка save → коммит в wellness-content/insights/.
"""

import secrets
from dataclasses import dataclass
from typing import Final

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from loguru import logger

from ai_engine.client import analyze_insight
from ai_engine.schemas import InsightReport
from bot.formatters import (
    render_insight_header,
    render_insight_tail,
    render_pain_point,
)
from bot.keyboards import (
    CB_INSIGHT_SAVE_PREFIX,
    CB_INSIGHT_SKIP_PREFIX,
    insight_save_kb,
)
from integrations.github import commit_insight
from parsers.reddit import (
    fetch_thread,
    format_thread_for_llm,
    normalize_reddit_url,
)

router = Router(name="insight")


@dataclass
class _PendingInsight:
    report: InsightReport
    source_url: str
    source_label: str
    slug: str


_pending: Final[dict[str, _PendingInsight]] = {}


@router.message(Command("insight"))
async def cmd_insight(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            "Использование: <code>/insight &lt;reddit_url&gt;</code>\n\n"
            "Пример:\n"
            "<code>/insight https://www.reddit.com/r/solotravel/comments/abc123/</code>\n\n"
            "Бот вытащит топ-комментарии, прогонит через Claude и вернёт структурированный "
            "разбор ЦА: боли, желания, триггеры, AEO-конспект.",
            parse_mode="HTML",
        )
        return

    if not normalize_reddit_url(arg):
        await message.answer(
            "Не похоже на Reddit URL. Жду что-то вроде "
            "<code>https://reddit.com/r/&lt;sub&gt;/comments/&lt;id&gt;/</code>"
        )
        return

    progress = await message.answer("⏳ Тяну тред с Reddit...")

    try:
        thread = await fetch_thread(arg, top_n=80, max_depth=2)
    except Exception as e:
        logger.exception("reddit fetch error")
        await progress.edit_text(f"❌ Reddit упал: <code>{str(e)[:200]}</code>")
        return

    if thread is None:
        await progress.edit_text(
            "❌ Не смог распарсить тред. Проверь что URL рабочий и сабреддит публичный."
        )
        return

    if not thread.comments and not thread.op_body:
        await progress.edit_text("⚠️ Тред пустой (ни OP body, ни комментов).")
        return

    await progress.edit_text(
        f"⏳ Получено: <b>{len(thread.comments)}</b> комментов из r/{thread.subreddit}.\n"
        f"Шлю в Claude..."
    )

    thread_text = format_thread_for_llm(thread)

    try:
        report, cost = await analyze_insight(thread_text)
    except Exception as e:
        logger.exception("insight LLM error")
        await progress.edit_text(f"❌ LLM упал: <code>{str(e)[:200]}</code>")
        return

    if report is None:
        await progress.edit_text("❌ LLM не сумел вернуть валидный отчёт. См. логи.")
        return

    source_label = f"r/{thread.subreddit}: {thread.title[:80]}"
    token = secrets.token_urlsafe(8)
    _pending[token] = _PendingInsight(
        report=report,
        source_url=thread.url,
        source_label=source_label,
        slug=thread.slug,
    )

    await progress.edit_text(
        f"✅ Готово (стоимость: ${cost:.4f}). Отчёт ниже."
    )

    # Header
    await message.answer(
        render_insight_header(report, source_label, thread.url),
        disable_web_page_preview=True,
    )

    # Каждая боль — отдельным сообщением (читаемее)
    for i, pp in enumerate(report.pain_points, 1):
        await message.answer(render_pain_point(pp, i), disable_web_page_preview=True)

    # Tail с кнопкой save
    await message.answer(
        render_insight_tail(report),
        reply_markup=insight_save_kb(token),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith(CB_INSIGHT_SAVE_PREFIX))
async def cb_insight_save(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    token = query.data[len(CB_INSIGHT_SAVE_PREFIX) :]
    pending = _pending.pop(token, None)
    if pending is None:
        await query.answer("Отчёт устарел (рестарт?). Запусти /insight ещё раз.", show_alert=True)
        return

    try:
        commit = await commit_insight(
            pending.report,
            source_url=pending.source_url,
            source_label=pending.source_label,
            slug=pending.slug,
        )
    except Exception as e:
        logger.exception("insight commit failed")
        await query.answer(f"GitHub упал: {str(e)[:100]}", show_alert=True)
        _pending[token] = pending  # вернуть для ретрая
        return

    await query.message.edit_text(
        f"📄 <b>Сохранено</b>: <a href=\"{commit.html_url}\">{commit.path}</a>",
        disable_web_page_preview=True,
    )
    await query.answer("Сохранено")


@router.callback_query(F.data.startswith(CB_INSIGHT_SKIP_PREFIX))
async def cb_insight_skip(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    token = query.data[len(CB_INSIGHT_SKIP_PREFIX) :]
    _pending.pop(token, None)
    await query.message.edit_text("✖️ <i>Пропущено, не сохраняем.</i>")
    await query.answer()
