"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parsed_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "parsed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_parsed_items_source_extid"),
        sa.UniqueConstraint("content_hash", name="uq_parsed_items_content_hash"),
    )
    op.create_index(
        "ix_parsed_items_source_date", "parsed_items", ["source", "parsed_at"], unique=False
    )

    op.create_table(
        "run_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(128), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("items_total", sa.Integer(), nullable=True),
        sa.Column("items_kept", sa.Integer(), nullable=True),
        sa.Column("items_sent", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_logs_job_started", "run_logs", ["job_name", "started_at"])

    op.create_table(
        "approved_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parsed_item_id", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("transformation_type", sa.String(32), nullable=True),
        sa.Column("insight_tags", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("github_commit", sa.String(64), nullable=True),
        sa.Column("github_path", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parsed_item_id"], ["parsed_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "feedback_trash",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parsed_item_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rejected_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "rejected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parsed_item_id"], ["parsed_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("feedback_trash")
    op.drop_table("approved_items")
    op.drop_index("ix_run_logs_job_started", table_name="run_logs")
    op.drop_table("run_logs")
    op.drop_index("ix_parsed_items_source_date", table_name="parsed_items")
    op.drop_table("parsed_items")
