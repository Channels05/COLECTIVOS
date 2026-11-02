"""reportes de recorrido y búsquedas sin resultado

Revision ID: b2d8e4f6a1c3
Revises: a1c7d3e5f9b2
Create Date: 2026-11-02 12:00:00

Los pasajeros avisan cuando una línea cambió ("ya no pasa por aquí", "el letrero dice otra
cosa") y se guardan, anónimas, las búsquedas que no encontraron nada.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2d8e4f6a1c3"
down_revision: str | None = "a1c7d3e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

kind_enum = postgresql.ENUM("route_changed", "not_running", "sign", "other", name="route_report_kind", create_type=False)
status_enum = postgresql.ENUM("pending", "applied", "discarded", name="report_status", create_type=False)


def _point() -> geoalchemy2.types.Geometry:
    return geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326, spatial_index=False)


def upgrade() -> None:
    kind_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "route_reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("contributor_id", sa.Uuid(), nullable=False),
        sa.Column("kind", kind_enum, nullable=False),
        sa.Column("message", sa.String(length=300), nullable=True),
        sa.Column("geom", _point(), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["line_code"], ["lines.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contributor_id"], ["contributors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_route_reports_status", "route_reports", ["status"])
    op.create_index("ix_route_reports_contributor_id", "route_reports", ["contributor_id"])

    op.create_table(
        "search_misses",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("query", sa.String(length=80), nullable=True),
        sa.Column("origin", _point(), nullable=True),
        sa.Column("destination", _point(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_misses_created", "search_misses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_search_misses_created", table_name="search_misses")
    op.drop_table("search_misses")
    op.drop_index("ix_route_reports_contributor_id", table_name="route_reports")
    op.drop_index("ix_route_reports_status", table_name="route_reports")
    op.drop_table("route_reports")
    kind_enum.drop(op.get_bind(), checkfirst=True)
