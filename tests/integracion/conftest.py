import asyncio
import random
import string
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import Line, LineSource, LineStatus
from app.services.route_validator import RouteValidator


settings = get_settings()
DATABASE_AVAILABLE = True
DATABASE_SKIP_REASON = ""
database_host = make_url(settings.database_url).host
if database_host not in {"localhost", "127.0.0.1"}:
    DATABASE_AVAILABLE = False
    DATABASE_SKIP_REASON = (
        f"Los tests de integración no pueden escribir fuera de localhost (DATABASE_URL apunta a {database_host})."
    )
else:
    ping_engine = create_async_engine(settings.database_url, poolclass=NullPool)

    async def _ping_database() -> None:
        async with ping_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    try:
        asyncio.run(_ping_database())
    except Exception as exc:
        DATABASE_AVAILABLE = False
        DATABASE_SKIP_REASON = f"No hay base de datos disponible: {exc}"
    finally:
        asyncio.run(ping_engine.dispose())


@pytest.fixture(autouse=True)
def require_database() -> None:
    if not DATABASE_AVAILABLE:
        pytest.skip(DATABASE_SKIP_REASON)


def _codigo_linea() -> str:
    return "T" + "".join(random.choice(string.digits) for _ in range(6))


def generar_patente() -> str:
    letras = "BCDFGHJKLPRSTVWXYZ"
    letras_patente = "".join(random.choice(letras) for _ in range(4))
    numeros_patente = "".join(random.choice("0123456789") for _ in range(2))
    return letras_patente + numeros_patente


@pytest.fixture
def patente_factory():
    return generar_patente


@pytest.fixture
def patente(patente_factory) -> str:
    return patente_factory()


@pytest.fixture
def client() -> AsyncIterator[TestClient]:
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    test_sessions = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with test_sessions() as session:
            yield session

    app.state.redis = FakeAsyncRedis(decode_responses=True)
    app.state.route_validator = RouteValidator(buffer_m=40)
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    asyncio.run(test_engine.dispose())


@pytest.fixture
def admin() -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_api_key}


@pytest.fixture
def aportante(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/contributors")
    assert response.status_code == 201
    body = response.json()
    return {
        "X-Contributor-Id": str(body["id"]),
        "X-Contributor-Token": body["token"],
    }


@pytest.fixture
def linea(client: TestClient) -> str:
    code = _codigo_linea()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def insertar() -> None:
        async with sessionmaker() as session:
            session.add(Line(
                code=code,
                name=f"Línea {code}",
                description="Descripción de prueba",
                commune="La Florida",
                source=LineSource.OFFICIAL,
                status=LineStatus.ACTIVE,
            ))
            await session.commit()

    asyncio.run(insertar())
    asyncio.run(engine.dispose())
    return code


@pytest.fixture
def linea_factory(client: TestClient):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    def crear(code: str | None = None, description: str = "Descripción de prueba") -> str:
        code = code or _codigo_linea()

        async def insertar() -> None:
            async with sessionmaker() as session:
                session.add(Line(
                    code=code,
                    name=f"Línea {code}",
                    description=description,
                    commune="La Florida",
                    source=LineSource.OFFICIAL,
                    status=LineStatus.ACTIVE,
                ))
                await session.commit()

        asyncio.run(insertar())
        return code

    yield crear
    asyncio.run(engine.dispose())
