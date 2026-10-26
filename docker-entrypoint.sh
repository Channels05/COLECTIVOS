#!/bin/sh
# Arranque del contenedor: migraciones, trazados iniciales (solo si la base está vacía) y la API.
set -e
alembic upgrade head
python -m scripts.seed_routes
python -m scripts.describe_lines
# --proxy-headers: detrás del proxy del proveedor, request.client es la IP real del teléfono
# (la usa el límite de registros por IP).
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
