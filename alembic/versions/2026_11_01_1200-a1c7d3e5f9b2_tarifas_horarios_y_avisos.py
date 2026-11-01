"""tarifas, horarios y avisos de las líneas

Revision ID: a1c7d3e5f9b2
Revises: f6b4c0e3a2d7
Create Date: 2026-11-01 12:00:00

Los pasajeros informan la tarifa y el horario de una línea (se aplican al aprobarlos en /admin) y
el administrador publica avisos por línea o para todas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c7d3e5f9b2"
down_revision: str | None = "f6b4c0e3a2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

report_status = postgresql.ENUM("pending", "applied", "discarded", name="report_status", create_type=False)


def upgrade() -> None:
    op.add_column("lines", sa.Column("fare_day", sa.Integer(), nullable=True))
    op.add_column("lines", sa.Column("fare_night", sa.Integer(), nullable=True))
    op.add_column("lines", sa.Column("first_departure", sa.String(length=5), nullable=True))
    op.add_column("lines", sa.Column("last_departure", sa.String(length=5), nullable=True))
    op.add_column("lines", sa.Column("info_updated_at", sa.DateTime(timezone=True), nullable=True))

    report_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "line_info_reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("contributor_id", sa.Uuid(), nullable=False),
        sa.Column("fare_day", sa.Integer(), nullable=True),
        sa.Column("fare_night", sa.Integer(), nullable=True),
        sa.Column("first_departure", sa.String(length=5), nullable=True),
        sa.Column("last_departure", sa.String(length=5), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("status", report_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["line_code"], ["lines.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contributor_id"], ["contributors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_line_info_reports_status", "line_info_reports", ["status"])
    op.create_index("ix_line_info_reports_contributor_id", "line_info_reports", ["contributor_id"])

    op.create_table(
        "line_notices",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=True),
        sa.Column("message", sa.String(length=280), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["line_code"], ["lines.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_line_notices_line_code", "line_notices", ["line_code"])


def downgrade() -> None:
    op.drop_index("ix_line_notices_line_code", table_name="line_notices")
    op.drop_table("line_notices")
    op.drop_index("ix_line_info_reports_contributor_id", table_name="line_info_reports")
    op.drop_index("ix_line_info_reports_status", table_name="line_info_reports")
    op.drop_table("line_info_reports")
    report_status.drop(op.get_bind(), checkfirst=True)
    for column in ("info_updated_at", "last_departure", "first_departure", "fare_night", "fare_day"):
        op.drop_column("lines", column)
