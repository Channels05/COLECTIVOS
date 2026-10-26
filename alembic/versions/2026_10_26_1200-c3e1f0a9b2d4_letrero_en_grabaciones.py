"""letrero en grabaciones de pasajeros

Revision ID: c3e1f0a9b2d4
Revises: a7514b02674d
Create Date: 2026-10-26 12:00:00

El registro oficial no trae lo que dice el letrero del auto. El pasajero que graba lo anota y el
administrador lo pasa al catálogo (lines.name) al aprobar.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e1f0a9b2d4"
down_revision: str | None = "a7514b02674d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("route_recordings", sa.Column("sign_text", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("route_recordings", "sign_text")
