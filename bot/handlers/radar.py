"""Radar: парсит источники, фильтрует через LLM, шлёт карточки.

Команды:
- /radar_now — запустить полный прогон по всем источникам.
- /radar_now pubmed — только PubMed.
- /radar_now rss — только RSS.

Inline callbacks:
- rd_app:<parsed_item_id> — ✅ В работу
- rd_trash:<parsed_item_id> — ❌ Мусор
"""

import asyncio
from typing import Final

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from loguru import logger

from decimal import Decimal

from ai_engine.client import summarize_radar_batch
from ai_engine.schemas import RadarCard
from bot.formatters import render_radar_card
from bot.keyboards import CB_APPROVE_PREFIX, CB_TRASH_PREFIX, radar_card_kb
from database.client import session_scope
from database.models import ParsedItem, RunLog
from database.repos import approved as approved_repo
from database.repos import dedup as dedup_repo
from database.repos import feedback as feedback_repo
from database.repos import runs as runs_repo
from integrations.github import commit_approved
from parsers.base import RawItem
from parsers.pubmed import PubMedParser
from parsers.rss import make_default_rss_parsers

router = Router(name="radar")

# Не персистентный кэш карточек: parsed_item.id -> RadarCard.
# При рестарте бота карточки теряются — user перезапускает /radar_now.
_pending_cards: Final[dict[int, RadarCard]] = {}

LLM_BATCH_SIZE = 10


async def _gather_raw_items(scope: str) -> list[RawItem]:
    parsers_to_run: list = []
    if scope in ("all", "pubmed"):
        parsers_to_run.append(PubMedParser())
    if scope in ("all", "rss"):
        parsers_to_run.extend(make_default_rss_parsers())

    if not parsers_to_run:
        return []

    results = await asyncio.gather(*(p.fetch() for p in parsers_to_run), return_exceptions=True)
    items: list[RawItem] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("parser failed: {}", r)
            continue
        items.extend(r)
    return items


async def _process_and_send(message: Message, scope: str) -> None:
    user_id = message.from_user.id if message.from_user else 0
    job_name = f"radar:{scope}"
    progress = await message.answer(f"⏳ Запускаю <code>{job_name}</code>...")

    async with session_scope() as session:
        run = await runs_repo.start(session, job_name=job_name)
        run_id = run.id

    total = 0
    new_count = 0
    sent_count = 0
    cost = None
    error: str | None = None

    try:
        raw_items = await _gather_raw_items(scope)
        total = len(raw_items)
        await progress.edit_text(f"⏳ Получено <b>{total}</b> сырых items, дедуп...")

        async with session_scope() as session:
            new_items = await dedup_repo.filter_new(session, raw_items)
        new_count = len(new_items)

        if not new_items:
            await progress.edit_text(
                f"✅ <code>{job_name}</code>\nВсего: {total}, новых: 0 (всё уже видели)"
            )
            async with session_scope() as session:
                run = await session.get(RunLog, run_id)
                await runs_repo.finish(
                    session, run, status="ok", items_total=total, items_kept=0, items_sent=0
                )
            return

        await progress.edit_text(
            f"⏳ Новых: <b>{new_count}</b>. Отправляю в LLM батчами по {LLM_BATCH_SIZE}..."
        )

        all_cards: list[RadarCard] = []
        total_cost = Decimal(0)
        for i in range(0, len(new_items), LLM_BATCH_SIZE):
            batch = new_items[i : i + LLM_BATCH_SIZE]
            cards, batch_cost = await summarize_radar_batch(batch)
            all_cards.extend(cards)
            total_cost += batch_cost
        cost = total_cost

        # Insert parsed_items, получаем mapping content_hash → id
        async with session_scope() as session:
            id_map = await dedup_repo.bulk_insert(session, new_items)

        # Сопоставляем карточки с parsed_item.id по external_id
        ext_to_hash = {it.external_id: it.content_hash for it in new_items}

        for card in all_cards:
            if card.is_trash:
                continue
            content_hash = ext_to_hash.get(card.external_id)
            if not content_hash:
                logger.warning(
                    "LLM card external_id not in input batch", external_id=card.external_id
                )
                continue
            parsed_id = id_map.get(content_hash)
            if not parsed_id:
                logger.warning("no parsed_item.id for hash", hash=content_hash)
                continue

            _pending_cards[parsed_id] = card
            await message.answer(
                render_radar_card(card),
                reply_markup=radar_card_kb(parsed_id),
                disable_web_page_preview=False,
            )
            sent_count += 1
            await asyncio.sleep(0.3)  # лёгкий троттлинг по TG

        await progress.edit_text(
            f"✅ <code>{job_name}</code>\n"
            f"Всего: {total}, новых: {new_count}, отправлено: {sent_count}, "
            f"стоимость: ${cost or 0:.4f}"
        )

        async with session_scope() as session:
            run = await session.get(RunLog, run_id)
            await runs_repo.finish(
                session,
                run,
                status="ok",
                items_total=total,
                items_kept=new_count,
                items_sent=sent_count,
                cost_usd=cost,
            )

    except Exception as e:
        logger.exception("radar pipeline error")
        error = str(e)
        await progress.edit_text(f"❌ <code>{job_name}</code> упал: <code>{error[:300]}</code>")
        async with session_scope() as session:
            run = await session.get(RunLog, run_id)
            await runs_repo.finish(
                session,
                run,
                status="error",
                items_total=total,
                items_kept=new_count,
                items_sent=sent_count,
                cost_usd=cost,
                error=error,
            )


@router.message(Command("radar_now"))
async def cmd_radar_now(message: Message, command: CommandObject) -> None:
    arg = (command.args or "all").strip().lower()
    if arg not in ("all", "pubmed", "rss"):
        await message.answer(
            "Использование: <code>/radar_now [all|pubmed|rss]</code>\n"
            "Без аргумента = all."
        )
        return
    await _process_and_send(message, arg)


@router.callback_query(F.data.startswith(CB_APPROVE_PREFIX))
async def cb_approve(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    parsed_id = int(query.data[len(CB_APPROVE_PREFIX) :])
    card = _pending_cards.pop(parsed_id, None)
    if card is None:
        await query.answer("Карточка устарела (рестарт?). Запусти /radar_now ещё раз.", show_alert=True)
        return

    user_id = query.from_user.id

    async with session_scope() as session:
        parsed_item = await session.get(ParsedItem, parsed_id)
        original_title = parsed_item.title if parsed_item and parsed_item.title else card.title

    try:
        commit = await commit_approved(
            card, original_title=original_title, approved_by=user_id
        )
    except Exception as e:
        logger.exception("github commit failed")
        await query.answer(f"GitHub упал: {str(e)[:100]}", show_alert=True)
        # вернуть карточку в кэш чтобы можно было ретраить
        _pending_cards[parsed_id] = card
        return

    async with session_scope() as session:
        await approved_repo.create(
            session,
            parsed_item_id=parsed_id,
            summary=card.summary,
            transformation_type=card.transformation_type,
            insight_tags=None,
            approved_by=user_id,
            github_commit=commit.sha,
            github_path=commit.path,
        )

    await query.message.edit_text(
        f"✅ <b>Сохранено</b>: <a href=\"{commit.html_url}\">{commit.path}</a>",
        disable_web_page_preview=True,
    )
    await query.answer("В работу")


@router.callback_query(F.data.startswith(CB_TRASH_PREFIX))
async def cb_trash(query: CallbackQuery) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    parsed_id = int(query.data[len(CB_TRASH_PREFIX) :])
    _pending_cards.pop(parsed_id, None)

    user_id = query.from_user.id
    async with session_scope() as session:
        await feedback_repo.create_trash(
            session, parsed_item_id=parsed_id, rejected_by=user_id
        )

    await query.message.edit_text("❌ <i>Помечено как мусор</i>")
    await query.answer("Мусор")
