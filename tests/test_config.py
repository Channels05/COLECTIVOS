"""La URL de la base se acepta tal como la entregan los proveedores (Render, Neon)."""

from app.core.config import Settings


def url(raw: str) -> str:
    return Settings(database_url=raw).database_url


def test_render_url_uses_asyncpg():
    assert url("postgres://u:p@dpg-x.virginia-postgres.render.com/colectivos") == (
        "postgresql+asyncpg://u:p@dpg-x.virginia-postgres.render.com/colectivos"
    )


def test_neon_url_as_copied_from_the_console():
    raw = (
        "postgresql://neondb_owner:secreto@ep-cool-name-a1b2c3-pooler.us-east-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert url(raw) == (
        "postgresql+asyncpg://neondb_owner:secreto@ep-cool-name-a1b2c3.us-east-1.aws.neon.tech/neondb"
        "?ssl=require"
    )


def test_local_url_is_unchanged():
    local = "postgresql+asyncpg://colectivos:colectivos@localhost:5432/colectivos"
    assert url(local) == local
