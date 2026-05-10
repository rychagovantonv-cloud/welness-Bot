from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RunLog


async def start(session: AsyncSession, job_name: str) -> RunLog:
    run = RunLog(job_name=job_name)
    session.add(run)
    await session.flush()
    return run


async def finish(
    session: AsyncSession,
    run: RunLog,
    *,
    status: str,
    items_total: int = 0,
    items_kept: int = 0,
    items_sent: int = 0,
    cost_usd: Decimal | None = None,
    error: str | None = None,
) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.status = status
    run.items_total = items_total
    run.items_kept = items_kept
    run.items_sent = items_sent
    run.cost_usd = cost_usd
    run.error = error[:2000] if error else None
    await session.flush()


async def get_spent_since(session: AsyncSession, as_of: datetime | None) -> Decimal:
    """Сумма cost_usd с заданного момента (или со старта если as_of=None)."""
    stmt = select(func.coalesce(func.sum(RunLog.cost_usd), 0))
    if as_of is not None:
        stmt = stmt.where(RunLog.started_at >= as_of)
    result = await session.execute(stmt)
    return Decimal(result.scalar() or 0)
