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
from datetime import datetime, timezone
from html import escape
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
    CB_INSIGHT_RUN_PREFIX,
    CB_INSIGHT_SAVE_PREFIX,
    CB_INSIGHT_SKIP_PREFIX,
    insight_run_kb,
    insight_save_kb,
)
from integrations.github import commit_insight
from parsers.reddit import (
    fetch_thread,
    format_thread_for_llm,
    normalize_reddit_url,
    search_threads,
)

router = Router(name="insight")


@dataclass
class _PendingInsight:
    report: InsightReport
    source_url: str
    source_label: str
    slug: str


_pending: Final[dict[str, _PendingInsight]] = {}
_pending_search: Final[dict[str, str]] = {}  # token -> reddit URL для cb_insight_run


async def _run_insight_pipeline(message: Message, url: str) -> None:
    """Полный pipeline: fetch -> LLM -> render. Дёргается из /insight и из cb."""
    progress = await message.answer("⏳ Тяну тред с Reddit...")

    try:
        thread = await fetch_thread(url, top_n=80, max_depth=2)
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

    await progress.edit_text(f"✅ Готово (стоимость: ${cost:.4f}). Отчёт ниже.")

    await message.answer(
        render_insight_header(report, source_label, thread.url),
        disable_web_page_preview=True,
    )
    for i, pp in enumerate(report.pain_points, 1):
        await message.answer(render_pain_point(pp, i), disable_web_page_preview=True)
    await message.answer(
        render_insight_tail(report),
        reply_markup=insight_save_kb(token),
        disable_web_page_preview=True,
    )


@router.message(Command("insight", "razbor"))
async def cmd_insight(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            "Использование: <code>/insight &lt;reddit_url&gt;</code>\n\n"
            "Бот вытащит топ-комментарии, прогонит через Claude и вернёт "
            "структурированный разбор ЦА.\n\n"
            "Если хочешь чтобы бот САМ нашёл треды по теме — используй "
            "<code>/insight_find &lt;тема&gt;</code>.",
            parse_mode="HTML",
        )
        return

    if not normalize_reddit_url(arg):
        await message.answer(
            "Не похоже на Reddit URL. Жду что-то вроде "
            "<code>https://reddit.com/r/&lt;sub&gt;/comments/&lt;id&gt;/</code>"
        )
        return

    await _run_insight_pipeline(message, arg)


@router.message(Command("insight_find", "poisk"))
async def cmd_insight_find(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Использование: <code>/insight_find &lt;тема&gt;</code>\n\n"
            "Примеры:\n"
            "<code>/insight_find midlife crisis solo travel</code>\n"
            "<code>/insight_find burnout meditation retreat</code>\n"
            "<code>/insight_find divorce solo travel meaning</code>\n\n"
            "Бот пройдёт по 18 курированным сабреддитам Reflective Traveler и "
            "вернёт топ тредов с >20 комментов за последний месяц. "
            "На каждом будет кнопка 🔍 Разобрать.",
            parse_mode="HTML",
        )
        return

    progress = await message.answer(f"⏳ Ищу треды по <i>{escape(query)}</i>...")

    try:
        hits = await search_threads(query, time_range="month", limit=10, min_comments=20)
    except Exception as e:
        logger.exception("reddit search failed")
        await progress.edit_text(f"❌ Поиск упал: <code>{str(e)[:200]}</code>")
        return

    if not hits:
        await progress.edit_text(
            "🔍 Ничего не нашёл с >20 комментариев за месяц по этому запросу.\n"
            "Попробуй другую формулировку или расширь — "
            "<code>/insight_find</code> ищет в курированных сабреддитах."
        )
        return

    top = hits[:10]
    await progress.edit_text(
        f"🔍 Найдено <b>{len(top)}</b> релевантных тредов. Жми 🔍 Разобрать на интересном:"
    )

    for hit in top:
        token = secrets.token_urlsafe(8)
        _pending_search[token] = hit.url
        age_days = (datetime.now(timezone.utc).timestamp() - hit.created_utc) / 86400
        text = (
            f"<b>{escape(hit.title[:200])}</b>\n"
            f"r/{escape(hit.subreddit)}  ·  {hit.num_comments} комм  ·  "
            f"{hit.score} score  ·  {int(age_days)}д назад\n"
            f"<a href=\"{escape(hit.url)}\">тред на reddit</a>"
        )
        await message.answer(
            text, reply_markup=insight_run_kb(token), disable_web_page_preview=True
        )


@router.callback_query(F.data.startswith(CB_INSIGHT_RUN_PREFIX))
async def cb_insight_run(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    token = query.data[len(CB_INSIGHT_RUN_PREFIX) :]
    url = _pending_search.pop(token, None)
    if url is None:
        await query.answer("Ссылка устарела. Перезапусти /insight_find.", show_alert=True)
        return
    await query.answer("Запускаю анализ...")
    # Убираем кнопку у исходного сообщения чтобы не нажали повторно
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _run_insight_pipeline(query.message, url)


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
