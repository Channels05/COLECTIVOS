"""esquema inicial: vehicles, routes, location_updates (+ PostGIS y enums)

Revision ID: 7832332f9d01
Revises:
Create Date: 2026-10-24 23:37:16.277536

"""
from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7832332f9d01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enums declarados una vez: se crean/borran explícitamente (create_type=False en las columnas),
# porque drop_table no elimina los tipos ENUM de PostgreSQL.
route_source = postgresql.ENUM("osm", "gore_rm", name="route_source", create_type=False)
route_direction = postgresql.ENUM("ida", "regreso", name="route_direction", create_type=False)
route_kind = postgresql.ENUM(
    "trazado", "via_alternativa", "bucle", "nocturno", name="route_kind", create_type=False
)
ENUMS = (route_source, route_direction, route_kind)


def upgrade() -> None:
    # Las columnas geometry y los índices GIST necesitan PostGIS.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_geospatial_table(
        "routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", route_source, nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("direction", route_direction, nullable=False),
        sa.Column("kind", route_kind, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("commune", sa.String(length=60), nullable=False),
        sa.Column(
            "geom",
            Geometry(geometry_type="MULTILINESTRING", srid=4326, dimension=2, spatial_index=False,
                     from_text="ST_GeomFromEWKT", name="geometry", nullable=False),
            nullable=False,
        ),
        sa.Column("source_attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_routes_source"),
    )
    op.create_geospatial_index("idx_routes_geom", "routes", ["geom"], unique=False, postgresql_using="gist", postgresql_ops={})
    op.create_index(op.f("ix_routes_line_code"), "routes", ["line_code"], unique=False)

    op.create_table(
        "vehicles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plate", sa.String(length=6), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("max_capacity", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("device_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("max_capacity BETWEEN 1 AND 6", name="ck_vehicles_capacity"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plate"),
    )
    op.create_index(op.f("ix_vehicles_line_code"), "vehicles", ["line_code"], unique=False)

    op.create_geospatial_table(
        "location_updates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "geom",
            Geometry(geometry_type="POINT", srid=4326, dimension=2, spatial_index=False,
                     from_text="ST_GeomFromEWKT", name="geometry", nullable=False),
            nullable=False,
        ),
        sa.Column("available_seats", sa.SmallInteger(), nullable=False),
        sa.Column("speed_kmh", sa.Float(), nullable=True),
        sa.Column("heading", sa.Float(), nullable=True),
        sa.Column("on_route", sa.Boolean(), nullable=True),
        sa.Column("distance_to_route_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_location_recorded_brin", "location_updates", ["recorded_at"], unique=False, postgresql_using="brin")
    op.create_index("ux_location_vehicle_time", "location_updates", ["vehicle_id", "recorded_at"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_location_vehicle_time", table_name="location_updates")
    op.drop_index("ix_location_recorded_brin", table_name="location_updates", postgresql_using="brin")
    op.drop_geospatial_table("location_updates")
    op.drop_index(op.f("ix_vehicles_line_code"), table_name="vehicles")
    op.drop_table("vehicles")
    op.drop_index(op.f("ix_routes_line_code"), table_name="routes")
    op.drop_geospatial_index("idx_routes_geom", table_name="routes", postgresql_using="gist", column_name="geom")
    op.drop_geospatial_table("routes")

    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
    # La extensión PostGIS no se elimina: puede estar en uso por otros objetos de la base.
