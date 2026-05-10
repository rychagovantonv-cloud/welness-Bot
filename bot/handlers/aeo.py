"""AEO handler: /aeo <query> — параллельно опрашивает Claude (+Gemini) и
выдаёт структурированный мета-анализ для Answer Engine Optimization.
"""

import secrets
from dataclasses import dataclass
from html import escape
from typing import Final

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from loguru import logger

from ai_engine.aeo import run_aeo
from ai_engine.schemas import AeoAnalysis, AeoModelResponse
from bot.budget import get_budget_line
from bot.keyboards import (
    CB_AEO_SAVE_PREFIX,
    CB_AEO_SKIP_PREFIX,
    aeo_save_kb,
)
from config import settings
from database.client import session_scope
from database.models import RunLog
from database.repos import runs as runs_repo
from integrations.github import commit_aeo

router = Router(name="aeo")


@dataclass
class _PendingAeo:
    query: str
    responses: list[AeoModelResponse]
    analysis: AeoAnalysis


_pending: Final[dict[str, _PendingAeo]] = {}


def _slugify_query(q: str) -> str:
    import re
    s = q.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:60] or "query"


def _render_analysis_main(analysis: AeoAnalysis, models: list[str]) -> str:
    common = "\n".join(f"• {escape(t)}" for t in analysis.common_themes[:8])
    gaps = "\n".join(f"• {escape(g)}" for g in analysis.content_gaps[:6])
    return (
        f"🎯 <b>AEO-анализ</b>  ·  моделей: {len(models)}\n\n"
        f"<b>Доминирующий нарратив:</b>\n{escape(analysis.dominant_narrative)}\n\n"
        f"<b>Общие темы:</b>\n{common}\n\n"
        f"<b>Контентные пробелы:</b>\n{gaps}"
    )


def _render_analysis_keywords(analysis: AeoAnalysis) -> str:
    keywords = "  ·  ".join(f"<code>{escape(k)}</code>" for k in analysis.recommended_keywords[:15])
    unique_lines = []
    for model, items in analysis.unique_angles.items():
        if not items:
            continue
        unique_lines.append(f"<b>{escape(model)}:</b>")
        for it in items[:5]:
            unique_lines.append(f"  • {escape(it)}")
    unique_text = "\n".join(unique_lines) if unique_lines else "<i>(нет существенных)</i>"

    return (
        f"<b>🔑 Рекомендованные ключи:</b>\n{keywords}\n\n"
        f"<b>Уникальные углы по моделям:</b>\n{unique_text}\n\n"
        f"<b>📌 Что делать:</b>\n{escape(analysis.summary)}"
    )


@router.message(Command("aeo"))
async def cmd_aeo(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Использование: <code>/aeo &lt;запрос&gt;</code>\n\n"
            "Бот пошлёт ваш запрос параллельно в Claude и Gemini "
            "(если задан <code>GEMINI_API_KEY</code>), сравнит ответы и выдаст:\n"
            "• какой нарратив у AI сейчас доминирует,\n"
            "• какие темы общие, какие уникальны для каждой модели,\n"
            "• где контентные пробелы (куда вклиниться),\n"
            "• какие ключевые фразы AI повторяет.\n\n"
            "Примеры:\n"
            "<code>/aeo where to retreat after burnout in Spain</code>\n"
            "<code>/aeo solo travel after divorce midlife</code>\n"
            "<code>/aeo psilocybin retreat for grief</code>",
            parse_mode="HTML",
        )
        return

    has_gemini = bool(settings.gemini_api_key)
    progress_text = "⏳ Шлю запрос в Claude"
    if has_gemini:
        progress_text += " + Gemini"
    progress_text += "..."
    progress = await message.answer(progress_text)

    async with session_scope() as session:
        run = await runs_repo.start(session, job_name="aeo")
        run_id = run.id

    try:
        responses, analysis, cost = await run_aeo(query)
    except Exception as e:
        logger.exception("aeo failed")
        await progress.edit_text(f"❌ AEO упал: <code>{str(e)[:200]}</code>")
        async with session_scope() as session:
            run = await session.get(RunLog, run_id)
            await runs_repo.finish(session, run, status="error", error=str(e))
        return

    if not responses:
        async with session_scope() as session:
            run = await session.get(RunLog, run_id)
            await runs_repo.finish(
                session, run, status="error", cost_usd=cost,
                error="all models failed",
            )
        await progress.edit_text("❌ Все модели отвалились. См. логи.")
        return

    if analysis is None:
        async with session_scope() as session:
            run = await session.get(RunLog, run_id)
            await runs_repo.finish(
                session, run, status="partial", cost_usd=cost,
                items_total=len(responses), error="meta-analyzer failed",
            )
        await progress.edit_text(
            "⚠️ Получил ответы моделей но мета-анализатор не сработал. "
            f"Стоимость: ${cost:.4f}. Попробуй другой запрос."
        )
        return

    async with session_scope() as session:
        run = await session.get(RunLog, run_id)
        await runs_repo.finish(
            session, run, status="ok", cost_usd=cost,
            items_total=len(responses), items_sent=len(responses),
        )

    token = secrets.token_urlsafe(8)
    _pending[token] = _PendingAeo(query=query, responses=responses, analysis=analysis)

    models_str = ", ".join(r.model for r in responses)
    if not has_gemini:
        models_str += "  ⚠️ <i>Gemini не настроен — только Claude</i>"

    budget_line = await get_budget_line(cost)
    await progress.edit_text(
        f"✅ AEO готово (модели: {models_str})\n{budget_line}"
    )

    # Сначала сырые ответы моделей (краткие, по одному сообщению на модель)
    for r in responses:
        text = r.raw_text
        if len(text) > 3500:
            text = text[:3500] + "\n\n<i>...обрезано, полная версия в .md если сохранить</i>"
        await message.answer(
            f"<b>🤖 {escape(r.model)}:</b>\n\n{escape(text)}",
            disable_web_page_preview=True,
        )

    # Мета-анализ — два сообщения чтобы не вылезти за лимит
    await message.answer(
        _render_analysis_main(analysis, [r.model for r in responses]),
        disable_web_page_preview=True,
    )
    await message.answer(
        _render_analysis_keywords(analysis),
        reply_markup=aeo_save_kb(token),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith(CB_AEO_SAVE_PREFIX))
async def cb_aeo_save(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    token = query.data[len(CB_AEO_SAVE_PREFIX) :]
    pending = _pending.pop(token, None)
    if pending is None:
        await query.answer("Отчёт устарел. Запусти /aeo ещё раз.", show_alert=True)
        return

    try:
        commit = await commit_aeo(
            pending.query,
            pending.responses,
            pending.analysis,
            slug=_slugify_query(pending.query),
        )
    except Exception as e:
        logger.exception("aeo commit failed")
        await query.answer(f"GitHub упал: {str(e)[:100]}", show_alert=True)
        _pending[token] = pending
        return

    await query.message.edit_text(
        f"📄 <b>AEO сохранён</b>: <a href=\"{commit.html_url}\">{commit.path}</a>",
        disable_web_page_preview=True,
    )
    await query.answer("Сохранено")


@router.callback_query(F.data.startswith(CB_AEO_SKIP_PREFIX))
async def cb_aeo_skip(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    token = query.data[len(CB_AEO_SKIP_PREFIX) :]
    _pending.pop(token, None)
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
