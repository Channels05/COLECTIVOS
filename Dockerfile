# Imagen de la API para desplegar en la nube (Render, Railway, Fly o un VPS).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Shapely, pyproj, asyncpg y duckdb traen wheels: no hace falta compilar nada.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini docker-entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY data/gore/colectivos_rm.geojson ./data/gore/colectivos_rm.geojson

# Por si el script se editó en Windows (CRLF rompe el shebang).
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh \
    && useradd --create-home app
USER app

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
