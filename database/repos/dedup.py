"""Дедуп: фильтр сырых items по content_hash + bulk insert."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ParsedItem
from parsers.base import RawItem


async def filter_new(session: AsyncSession, items: list[RawItem]) -> list[RawItem]:
    if not items:
        return []
    hashes = [it.content_hash for it in items]
    result = await session.execute(
        select(ParsedItem.content_hash).where(ParsedItem.content_hash.in_(hashes))
    )
    seen = {row[0] for row in result.all()}
    return [it for it in items if it.content_hash not in seen]


async def bulk_insert(session: AsyncSession, items: list[RawItem]) -> dict[str, int]:
    """Вставляет items, возвращает мэппинг content_hash -> parsed_item.id.

    ON CONFLICT DO NOTHING на (source, external_id) и content_hash, чтобы не падать
    при гонках. После insert делает SELECT чтобы получить id (включая уже существующие).
    """
    if not items:
        return {}
    rows = [
        {
            "source": it.source,
            "external_id": it.external_id,
            "content_hash": it.content_hash,
            "title": it.title[:1000] if it.title else None,
            "url": it.url,
        }
        for it in items
    ]
    stmt = pg_insert(ParsedItem).values(rows).on_conflict_do_nothing()
    await session.execute(stmt)
    await session.flush()

    hashes = [it.content_hash for it in items]
    result = await session.execute(
        select(ParsedItem.content_hash, ParsedItem.id).where(
            ParsedItem.content_hash.in_(hashes)
        )
    )
    return {h: pid for h, pid in result.all()}
