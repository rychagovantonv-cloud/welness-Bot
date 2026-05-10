from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ApprovedItem


async def create(
    session: AsyncSession,
    *,
    parsed_item_id: int,
    summary: str,
    transformation_type: str | None,
    insight_tags: list[str] | None,
    approved_by: int,
    github_commit: str | None = None,
    github_path: str | None = None,
) -> ApprovedItem:
    row = ApprovedItem(
        parsed_item_id=parsed_item_id,
        summary=summary,
        transformation_type=transformation_type,
        insight_tags=insight_tags,
        approved_by=approved_by,
        github_commit=github_commit,
        github_path=github_path,
    )
    session.add(row)
    await session.flush()
    return row
