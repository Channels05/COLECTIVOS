"""grabaciones de recorridos: tabla route_recordings + fuente de rutas "recorded"

Revision ID: 15930bfdd2d6
Revises: 7832332f9d01
Create Date: 2026-10-25 14:42:08.420300

"""
from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "15930bfdd2d6"
down_revision: str | Sequence[str] | None = "7832332f9d01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# route_direction ya existe (migración inicial): se reutiliza sin crearla.
route_direction = postgresql.ENUM("ida", "regreso", name="route_direction", create_type=False)
recording_status = postgresql.ENUM(
    "recording", "pending_review", "approved", "rejected", "cancelled", name="recording_status", create_type=False
)


def upgrade() -> None:
    # Autogenerate no detecta valores nuevos de un ENUM: se agrega a mano.
    op.execute("ALTER TYPE route_source ADD VALUE IF NOT EXISTS 'recorded'")
    recording_status.create(op.get_bind(), checkfirst=True)

    op.create_geospatial_table(
        "route_recordings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("direction", route_direction, nullable=False),
        sa.Column("status", recording_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw_geom",
            Geometry(geometry_type="LINESTRING", srid=4326, dimension=2, spatial_index=False,
                     from_text="ST_GeomFromEWKT", name="geometry"),
            nullable=True,
        ),
        sa.Column(
            "proposed_geom",
            Geometry(geometry_type="LINESTRING", srid=4326, dimension=2, spatial_index=False,
                     from_text="ST_GeomFromEWKT", name="geometry"),
            nullable=True,
        ),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recording_status", "route_recordings", ["status"], unique=False)
    op.create_index(op.f("ix_route_recordings_line_code"), "route_recordings", ["line_code"], unique=False)
    op.create_index(
        "ux_recording_active_per_vehicle", "route_recordings", ["vehicle_id"], unique=True,
        postgresql_where=sa.text("status = 'recording'"),
    )


def downgrade() -> None:
    op.drop_index("ux_recording_active_per_vehicle", table_name="route_recordings",
                  postgresql_where=sa.text("status = 'recording'"))
    op.drop_index(op.f("ix_route_recordings_line_code"), table_name="route_recordings")
    op.drop_index("ix_recording_status", table_name="route_recordings")
    op.drop_geospatial_table("route_recordings")
    recording_status.drop(op.get_bind(), checkfirst=True)

    # PostgreSQL no permite quitar un valor de un ENUM: se recrea el tipo sin 'recorded'.
    # Las rutas grabadas se eliminan (dependían de esta funcionalidad).
    op.execute("DELETE FROM routes WHERE source = 'recorded'")
    op.execute("ALTER TYPE route_source RENAME TO route_source_old")
    op.execute("CREATE TYPE route_source AS ENUM ('osm', 'gore_rm')")
    op.execute("ALTER TABLE routes ALTER COLUMN source TYPE route_source USING source::text::route_source")
    op.execute("DROP TYPE route_source_old")
