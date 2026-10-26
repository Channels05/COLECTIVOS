from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Colectivos API"
    database_url: str = "postgresql+asyncpg://colectivos:colectivos@localhost:5432/colectivos"
    redis_url: str = "redis://localhost:6379/0"
    admin_api_key: str = "dev-admin-key"
    # En la nube con un solo servicio (p. ej. el plan gratis de Render, sin workers aparte), el
    # persistidor de ubicaciones corre dentro del proceso de la API en vez de como proceso propio.
    run_worker_in_process: bool = False
    # Historial de posiciones (location_updates): lo más viejo se borra para no llenar la base
    # (el plan gratis de Neon tiene 1 GB; cada auto transmitiendo todo el día suma ~1,5 MB).
    # 0 = no borrar nunca.
    location_retention_days: int = 30

    @field_validator("database_url")
    @classmethod
    def use_asyncpg(cls, url: str) -> str:
        """Acepta la URL tal como la entregan los proveedores (Render, Neon).

        - "postgres://" / "postgresql://" -> driver asyncpg.
        - asyncpg usa `ssl` y no `sslmode`, y no conoce `channel_binding` (Neon lo agrega).
        - El endpoint "-pooler" de Neon es PgBouncer en modo transacción, que rompe los
          prepared statements de asyncpg: se usa el endpoint directo (mismo host sin "-pooler").
        """
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url.removeprefix(prefix)
        parts = urlsplit(url)
        query = [
            ("ssl" if key == "sslmode" else key, value)
            for key, value in parse_qsl(parts.query)
            if key != "channel_binding"
        ]
        netloc = parts.netloc.replace("-pooler.", ".")
        return urlunsplit(parts._replace(netloc=netloc, query=urlencode(query)))

    # --- Tiempo real ---
    vehicle_ttl_s: int = 30  # sin ping en 30 s => el colectivo deja de aparecer como activo
    max_live_age_s: int = 30  # pings más antiguos (enviados tras perder señal) solo van al histórico
    max_clock_skew_s: int = 30  # tolerancia para timestamps "del futuro" del reloj del teléfono

    # Área de servicio (min_lon, min_lat, max_lon, max_lat): Región Metropolitana completa, que es
    # donde hay trazados oficiales (incluye comunas rurales como Melipilla, Colina o Paine).
    # Descarta basura típica de GPS como (0, 0) antes de tocar Redis.
    service_bbox: tuple[float, float, float, float] = (-71.75, -34.30, -69.75, -32.90)

    # --- Validación de ruta ---
    route_buffer_m: float = 40.0  # tolerancia = error GPS (~10-20 m) + ancho de calzada
    off_route_strikes: int = 3  # pings consecutivos fuera (≈15 s) antes de alertar
    metric_crs: str = "EPSG:32719"  # WGS84 / UTM 19S: distancias en metros para Santiago
    # Horario en que valen los "trazados nocturnos" [inicio, fin), hora de Santiago.
    # SUPUESTO: confirmar el horario con la resolución del MTT para cada línea.
    night_hours: tuple[int, int] = (22, 6)


@lru_cache
def get_settings() -> Settings:
    return Settings()
