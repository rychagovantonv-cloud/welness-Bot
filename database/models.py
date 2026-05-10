from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ParsedItem(Base):
    """Дедуп: одна строка на каждый спарсенный объект (статья / тред)."""

    __tablename__ = "parsed_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_parsed_items_source_extid"),
        UniqueConstraint("content_hash", name="uq_parsed_items_content_hash"),
        Index("ix_parsed_items_source_date", "source", "parsed_at"),
    )


class RunLog(Base):
    """Лог одного запуска scheduler-job (Radar) или ad-hoc команды (Insight)."""

    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))
    items_total: Mapped[int | None] = mapped_column(Integer)
    items_kept: Mapped[int | None] = mapped_column(Integer)
    items_sent: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_run_logs_job_started", "job_name", "started_at"),)


class ApprovedItem(Base):
    """Одобренная пользователем карточка → закоммичена в content-репо."""

    __tablename__ = "approved_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parsed_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parsed_items.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    transformation_type: Mapped[str | None] = mapped_column(String(32))
    insight_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    github_commit: Mapped[str | None] = mapped_column(String(64))
    github_path: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeedbackTrash(Base):
    """Карточки, отмеченные как мусор — для будущего улучшения фильтра."""

    __tablename__ = "feedback_trash"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parsed_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parsed_items.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    rejected_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rejected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
