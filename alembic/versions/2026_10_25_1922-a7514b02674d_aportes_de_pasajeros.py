"""aportes de pasajeros: catálogo de líneas, aportantes anónimos y puntos GPS de grabaciones

Revision ID: a7514b02674d
Revises: 15930bfdd2d6
Create Date: 2026-10-25 19:22:14.481589

"""
from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7514b02674d"
down_revision: str | Sequence[str] | None = "15930bfdd2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

line_source = postgresql.ENUM("official", "admin", "contributed", name="line_source", create_type=False)
line_status = postgresql.ENUM("active", "pending_review", "rejected", name="line_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    line_source.create(bind, checkfirst=True)
    line_status.create(bind, checkfirst=True)
    op.execute("CREATE SEQUENCE IF NOT EXISTS contributed_line_seq")

    op.create_table(
        "contributors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "lines",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("commune", sa.String(length=60), nullable=True),
        sa.Column("source", line_source, nullable=False),
        sa.Column("status", line_status, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["contributors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index("ix_lines_status", "lines", ["status"], unique=False)

    # Catálogo inicial: las líneas de los trazados importados (comuna = la más frecuente de sus
    # tramos) y, como "admin", las de vehículos o grabaciones que no tienen trazado oficial.
    # `source::text`: en una base nueva todas las migraciones van en una transacción y PostgreSQL
    # no deja usar el valor 'recorded' (agregado al ENUM en la migración anterior) antes del commit.
    op.execute("""
        INSERT INTO lines (code, commune, source, status)
        SELECT line_code, mode() WITHIN GROUP (ORDER BY commune), 'official', 'active'
        FROM routes WHERE source::text <> 'recorded' GROUP BY line_code
    """)
    op.execute("""
        INSERT INTO lines (code, commune, source, status)
        SELECT line_code, max(commune), 'admin', 'active' FROM (
            SELECT line_code, NULL::varchar AS commune FROM vehicles
            UNION ALL SELECT line_code, NULL FROM route_recordings
            UNION ALL SELECT line_code, commune FROM routes WHERE source::text = 'recorded'
        ) AS other GROUP BY line_code
        ON CONFLICT (code) DO NOTHING
    """)

    op.create_geospatial_table(
        "recording_points",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "geom",
            Geometry(geometry_type="POINT", srid=4326, dimension=2, spatial_index=False,
                     from_text="ST_GeomFromEWKT", name="geometry", nullable=False),
            nullable=False,
        ),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["recording_id"], ["route_recordings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recording_id", "recorded_at", name="uq_recording_point_time"),
    )

    # Una grabación ahora la hace un conductor O un pasajero; el pasajero puede no saber el sentido.
    op.add_column("route_recordings", sa.Column("contributor_id", sa.Uuid(), nullable=True))
    op.add_column("route_recordings", sa.Column("comment", sa.String(length=300), nullable=True))
    op.alter_column("route_recordings", "vehicle_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(
        "route_recordings", "direction",
        existing_type=postgresql.ENUM("ida", "regreso", name="route_direction", create_type=False),
        nullable=True,
    )
    op.create_foreign_key(
        "route_recordings_contributor_id_fkey", "route_recordings", "contributors",
        ["contributor_id"], ["id"], ondelete="CASCADE",
    )
    # Autogenerate no detecta CHECK constraints: se agrega a mano.
    op.create_check_constraint(
        "ck_recording_one_author", "route_recordings", "(vehicle_id IS NULL) <> (contributor_id IS NULL)"
    )
    op.create_index(
        "ux_recording_active_per_contributor", "route_recordings", ["contributor_id"], unique=True,
        postgresql_where=sa.text("status = 'recording'"),
    )


def downgrade() -> None:
    # Las grabaciones de pasajeros (y las que quedaron sin sentido) no caben en el esquema anterior.
    op.execute("DELETE FROM route_recordings WHERE contributor_id IS NOT NULL OR direction IS NULL")
    op.drop_index("ux_recording_active_per_contributor", table_name="route_recordings",
                  postgresql_where=sa.text("status = 'recording'"))
    op.drop_constraint("ck_recording_one_author", "route_recordings", type_="check")
    op.drop_constraint("route_recordings_contributor_id_fkey", "route_recordings", type_="foreignkey")
    op.alter_column(
        "route_recordings", "direction",
        existing_type=postgresql.ENUM("ida", "regreso", name="route_direction", create_type=False),
        nullable=False,
    )
    op.alter_column("route_recordings", "vehicle_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("route_recordings", "comment")
    op.drop_column("route_recordings", "contributor_id")
    op.drop_geospatial_table("recording_points")
    op.drop_index("ix_lines_status", table_name="lines")
    op.drop_table("lines")
    op.drop_table("contributors")
    op.execute("DROP SEQUENCE IF EXISTS contributed_line_seq")
    bind = op.get_bind()
    line_status.drop(bind, checkfirst=True)
    line_source.drop(bind, checkfirst=True)
