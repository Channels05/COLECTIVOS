"""Analítica del histórico con DuckDB, sin cargar trabajo sobre la base transaccional.

    python -m analytics.daily_report 2026-10-24

1. Extrae un día de `location_updates` desde PostGIS a Parquet (particionado por fecha).
2. Corre las métricas sobre el Parquet: DuckDB escanea millones de filas en segundos.
"""

import argparse
from datetime import date
from pathlib import Path

import duckdb

from app.core.config import get_settings

LAKE_DIR = Path("data/lake/location_updates")


def _libpq_dsn() -> str:
    # El scanner de DuckDB usa libpq: quitamos el driver de SQLAlchemy.
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def export_day(con: duckdb.DuckDBPyConnection, day: date) -> Path:
    out = LAKE_DIR / f"date={day.isoformat()}" / "part-0.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    # postgres_query empuja el SQL a PostGIS: así extraemos lon/lat con ST_X/ST_Y,
    # ya que el tipo geometry no se mapea directo a DuckDB.
    pg_sql = f"""
        SELECT l.vehicle_id::text, v.line_code, l.recorded_at, ST_X(l.geom) AS lon, ST_Y(l.geom) AS lat,
               l.available_seats, v.max_capacity, l.speed_kmh, l.on_route, l.distance_to_route_m
        FROM location_updates l JOIN vehicles v ON v.id = l.vehicle_id
        -- Día calendario de Santiago, no de UTC: a las 22:00 en Chile ya es "mañana" en UTC.
        WHERE l.recorded_at >= ('{day.isoformat()}'::date)::timestamp AT TIME ZONE 'America/Santiago'
          AND l.recorded_at <  ('{day.isoformat()}'::date + 1)::timestamp AT TIME ZONE 'America/Santiago'
    """
    con.execute(f"COPY (SELECT * FROM postgres_query('pg', $${pg_sql}$$)) TO '{out.as_posix()}' (FORMAT parquet)")
    return out


REPORT_SQL = """
SELECT
    line_code,
    hour(recorded_at AT TIME ZONE 'America/Santiago')         AS hora,
    count(DISTINCT vehicle_id)                                AS vehiculos,
    round(avg(1 - available_seats / max_capacity) * 100, 1)   AS ocupacion_pct,
    -- "kilómetros vacíos": pings con el auto sin pasajeros
    round(avg((available_seats = max_capacity)::int) * 100, 1) AS pings_vacio_pct,
    round(avg((NOT on_route)::int) * 100, 2)                  AS fuera_de_ruta_pct
FROM read_parquet('{path}', hive_partitioning = true)
GROUP BY ALL
ORDER BY line_code, hora
"""


def main(day: date) -> None:
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{_libpq_dsn()}' AS pg (TYPE postgres, READ_ONLY)")
    parquet = export_day(con, day)
    con.sql(REPORT_SQL.format(path=parquet.as_posix())).show(max_rows=200)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("day", type=date.fromisoformat)
    main(parser.parse_args().day)
