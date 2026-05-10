from sqlalchemy.ext.asyncio import AsyncSession

from database.models import FeedbackTrash


async def create_trash(
    session: AsyncSession,
    *,
    parsed_item_id: int,
    rejected_by: int,
    reason: str | None = None,
) -> FeedbackTrash:
    row = FeedbackTrash(
        parsed_item_id=parsed_item_id,
        rejected_by=rejected_by,
        reason=reason,
    )
    session.add(row)
    await session.flush()
    return row
