"""Entorno de migraciones (async, asyncpg).

La URL sale de `app.core.config` (variable DATABASE_URL / .env), igual que la app: no hay
credenciales en alembic.ini.

    alembic upgrade head                              # aplicar migraciones
    alembic revision --autogenerate -m "descripción"  # nueva migración desde los modelos
    alembic check                                     # falla si modelos y BD difieren
"""

import asyncio
from logging.config import fileConfig

from geoalchemy2 import alembic_helpers
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  (registra los modelos en Base.metadata)
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tablas que pertenecen a extensiones (postgis: spatial_ref_sys; la imagen postgis/postgis
# además instala postgis_tiger_geocoder y topology, con ~60 tablas visibles en el search_path).
# No son nuestras: autogenerate jamás debe proponer borrarlas. Se completa al conectarse.
extension_tables: set[str] = {"spatial_ref_sys"}

_EXTENSION_TABLES_SQL = text(
    """
    SELECT c.relname FROM pg_class c
    JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'e'
    WHERE c.relkind IN ('r', 'p', 'v', 'm')
    """
)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    table_name = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    if table_name in extension_tables:
        return False
    # Además, GeoAlchemy2 oculta los índices GIST que crea solo junto a cada columna espacial.
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


CONFIGURE_OPTS = dict(
    target_metadata=target_metadata,
    include_object=include_object,
    compare_type=True,
    # Renderiza Geometry(...) correctamente y usa las operaciones espaciales de GeoAlchemy2.
    process_revision_directives=alembic_helpers.writer,
    render_item=alembic_helpers.render_item,
)


def run_migrations_offline() -> None:
    """Modo offline (`alembic upgrade head --sql`): genera el SQL sin conectarse."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    extension_tables.update(connection.execute(_EXTENSION_TABLES_SQL).scalars())
    # La consulta abrió una transacción implícita (autobegin). Si sigue abierta, Alembic la
    # trata como externa, no hace commit y la migración se revierte en silencio al cerrar.
    connection.commit()
    context.configure(connection=connection, **CONFIGURE_OPTS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
