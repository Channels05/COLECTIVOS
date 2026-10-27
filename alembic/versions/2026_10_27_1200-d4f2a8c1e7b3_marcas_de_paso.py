"""marcas de paso de colectivos

Revision ID: d4f2a8c1e7b3
Revises: c3e1f0a9b2d4
Create Date: 2026-10-27 12:00:00

El pasajero marca "pasó un colectivo" de una línea donde espera; con las marcas de un lugar se
estima cada cuánto pasa cada línea.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa

from alembic import op

revision: str = "d4f2a8c1e7b3"
down_revision: str | None = "c3e1f0a9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sightings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("contributor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contributor_id"], ["contributors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["line_code"], ["lines.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sightings_line_seen", "sightings", ["line_code", "seen_at"])
    op.create_index("ix_sightings_contributor_id", "sightings", ["contributor_id"])
    op.create_index("idx_sightings_geom", "sightings", ["geom"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("idx_sightings_geom", table_name="sightings", postgresql_using="gist")
    op.drop_index("ix_sightings_contributor_id", table_name="sightings")
    op.drop_index("ix_sightings_line_seen", table_name="sightings")
    op.drop_table("sightings")
