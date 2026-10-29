"""solicitudes de conductores

Revision ID: f6b4c0e3a2d7
Revises: e5a3b9d2f1c6
Create Date: 2026-10-28 21:00:00

El conductor pide su inscripción desde la app; el administrador la aprueba en /admin y el
vehículo queda creado con el token que el teléfono ya tiene.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6b4c0e3a2d7"
down_revision: str | None = "e5a3b9d2f1c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status_enum = postgresql.ENUM("pending", "approved", "rejected", name="application_status", create_type=False)


def upgrade() -> None:
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "driver_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plate", sa.String(length=6), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("max_capacity", sa.SmallInteger(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("vehicle_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_applications_status", "driver_applications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_driver_applications_status", table_name="driver_applications")
    op.drop_table("driver_applications")
    status_enum.drop(op.get_bind(), checkfirst=True)
