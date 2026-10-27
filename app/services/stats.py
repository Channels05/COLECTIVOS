"""Panel de datos de /admin: cuánto aportan los pasajeros, en qué líneas y en qué zonas.

Cuenta las marcas "pasó un colectivo" (`sightings`) y las grabaciones de recorridos de los
últimos `days` días. Las zonas son celdas de ~1 km (0,01°) donde hubo marcas o empezaron
grabaciones; su nombre es la comuna más común de las líneas que aparecen ahí.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ZONE_CELL_DEG = 0.01  # ≈ 1,1 km de latitud en Santiago
MAX_LINES = 60
MAX_ZONES = 300

_TOTALS = text("""
    SELECT
        (SELECT count(*) FROM contributors) AS contributors,
        (SELECT count(DISTINCT c) FROM (
            SELECT contributor_id AS c FROM sightings WHERE seen_at > now() - make_interval(days => :days)
            UNION ALL
            SELECT contributor_id FROM route_recordings
            WHERE contributor_id IS NOT NULL AND started_at > now() - make_interval(days => :days)
        ) active) AS active_contributors,
        (SELECT count(*) FROM sightings WHERE seen_at > now() - make_interval(days => :days)) AS sightings,
        (SELECT count(*) FROM sightings WHERE seen_at > now() - interval '7 days') AS sightings_7d,
        (SELECT count(*) FROM lines WHERE status::text = 'active') AS lines_active,
        (SELECT count(*) FROM lines WHERE status::text = 'pending_review') AS lines_pending,
        (SELECT count(DISTINCT line_code) FROM routes WHERE source::text = 'recorded' AND is_active) AS lines_recorded,
        (SELECT coalesce(sum(ST_Length(geom::geography)), 0) FROM routes
         WHERE source::text = 'recorded' AND is_active) AS recorded_m
""")

_RECORDINGS_BY_STATUS = text("""
    SELECT status::text, count(*) FROM route_recordings
    WHERE started_at > now() - make_interval(days => :days)
    GROUP BY status
""")

# Una fila por línea con actividad en el período o con recorrido grabado; la más activa primero.
_LINES = text("""
    WITH s AS (
        SELECT line_code, count(*) AS sightings, max(seen_at) AS last_seen
        FROM sightings WHERE seen_at > now() - make_interval(days => :days) GROUP BY line_code
    ), r AS (
        SELECT line_code,
               count(*) FILTER (WHERE status::text = 'approved') AS approved,
               count(*) FILTER (WHERE status::text = 'pending_review') AS pending,
               max(started_at) AS last_recording
        FROM route_recordings WHERE started_at > now() - make_interval(days => :days) GROUP BY line_code
    ), g AS (
        SELECT line_code, sum(ST_Length(geom::geography)) AS recorded_m
        FROM routes WHERE source::text = 'recorded' AND is_active GROUP BY line_code
    ), codes AS (
        SELECT line_code FROM s UNION SELECT line_code FROM r UNION SELECT line_code FROM g
    )
    SELECT c.line_code, l.name, l.description, l.commune,
           coalesce(s.sightings, 0) AS sightings,
           coalesce(r.approved, 0) AS recordings_approved,
           coalesce(r.pending, 0) AS recordings_pending,
           coalesce(g.recorded_m, 0) AS recorded_m,
           greatest(s.last_seen, r.last_recording) AS last_activity
    FROM codes c
    LEFT JOIN lines l ON l.code = c.line_code
    LEFT JOIN s ON s.line_code = c.line_code
    LEFT JOIN r ON r.line_code = c.line_code
    LEFT JOIN g ON g.line_code = c.line_code
    ORDER BY coalesce(s.sightings, 0) + 5 * (coalesce(r.approved, 0) + coalesce(r.pending, 0)) DESC,
             last_activity DESC NULLS LAST
    LIMIT :max_lines
""")

# Celdas con marcas o con inicios de grabación (el primer punto del recorrido crudo).
_ZONES = text("""
    WITH events AS (
        SELECT geom, line_code, 'sighting' AS kind FROM sightings
        WHERE seen_at > now() - make_interval(days => :days)
        UNION ALL
        SELECT ST_StartPoint(raw_geom), line_code, 'recording' FROM route_recordings
        WHERE raw_geom IS NOT NULL AND started_at > now() - make_interval(days => :days)
    )
    SELECT floor(ST_Y(e.geom) / :cell) AS cy, floor(ST_X(e.geom) / :cell) AS cx,
           count(*) FILTER (WHERE kind = 'sighting') AS sightings,
           count(*) FILTER (WHERE kind = 'recording') AS recordings,
           count(DISTINCT e.line_code) AS lines,
           mode() WITHIN GROUP (ORDER BY l.commune) AS commune
    FROM events e LEFT JOIN lines l ON l.code = e.line_code
    GROUP BY cy, cx
    ORDER BY count(*) DESC
    LIMIT :max_zones
""")


async def summary(db: AsyncSession, days: int) -> dict:
    totals = (await db.execute(_TOTALS, {"days": days})).mappings().one()
    by_status = {status: n for status, n in await db.execute(_RECORDINGS_BY_STATUS, {"days": days})}
    lines = (await db.execute(_LINES, {"days": days, "max_lines": MAX_LINES})).mappings().all()
    zones = await db.execute(_ZONES, {"days": days, "cell": ZONE_CELL_DEG, "max_zones": MAX_ZONES})
    return {
        "days": days,
        "totals": {
            "contributors": totals["contributors"],
            "active_contributors": totals["active_contributors"],
            "sightings": totals["sightings"],
            "sightings_7d": totals["sightings_7d"],
            "recordings": by_status,
            "lines_active": totals["lines_active"],
            "lines_pending": totals["lines_pending"],
            "lines_with_recorded_route": totals["lines_recorded"],
            "recorded_km": round(totals["recorded_m"] / 1000, 1),
        },
        "lines": [
            {**{k: row[k] for k in ("line_code", "name", "description", "commune", "sightings",
                                    "recordings_approved", "recordings_pending", "last_activity")},
             "recorded_km": round(row["recorded_m"] / 1000, 1)}
            for row in lines
        ],
        "zones": [
            {
                # Centro de la celda.
                "lat": round((cy + 0.5) * ZONE_CELL_DEG, 5),
                "lon": round((cx + 0.5) * ZONE_CELL_DEG, 5),
                "sightings": sightings,
                "recordings": recordings,
                "lines": n_lines,
                "commune": commune,
            }
            for cy, cx, sightings, recordings, n_lines, commune in zones
        ],
    }
