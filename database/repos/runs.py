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


async def get_cost_summary(session: AsyncSession) -> dict[str, Decimal]:
    """Возвращает {'today': X, 'month': Y, 'total': Z} в USD."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)

    today_q = await session.execute(
        select(func.coalesce(func.sum(RunLog.cost_usd), 0)).where(
            RunLog.started_at >= today_start
        )
    )
    month_q = await session.execute(
        select(func.coalesce(func.sum(RunLog.cost_usd), 0)).where(
            RunLog.started_at >= month_start
        )
    )
    total_q = await session.execute(select(func.coalesce(func.sum(RunLog.cost_usd), 0)))

    return {
        "today": Decimal(today_q.scalar() or 0),
        "month": Decimal(month_q.scalar() or 0),
        "total": Decimal(total_q.scalar() or 0),
    }
