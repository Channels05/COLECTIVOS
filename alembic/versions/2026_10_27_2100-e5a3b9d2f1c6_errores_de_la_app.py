"""errores de la app

Revision ID: e5a3b9d2f1c6
Revises: d4f2a8c1e7b3
Create Date: 2026-10-27 21:00:00

La app reporta sus errores de JavaScript (anónimos) para verlos en /admin → Errores.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a3b9d2f1c6"
down_revision: str | None = "d4f2a8c1e7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_errors",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("stack", sa.Text(), nullable=True),
        sa.Column("fatal", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.Column("screen", sa.String(length=100), nullable=True),
        sa.Column("app_version", sa.String(length=20), nullable=True),
        sa.Column("update_id", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=20), nullable=True),
        sa.Column("os_version", sa.String(length=20), nullable=True),
        sa.Column("device", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_errors_created_at", "client_errors", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_client_errors_created_at", table_name="client_errors")
    op.drop_table("client_errors")
