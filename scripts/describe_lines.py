"""Describe las líneas oficiales con las calles por donde pasan (data/gore/line_streets.json).

    python -m scripts.describe_lines

Lo corre el contenedor al arrancar, después de cargar los trazados. El archivo lo genera
`build_line_streets` a partir de OpenStreetMap. Solo toca descripciones vacías o generadas por
este mismo script ("Por ..."): si un administrador escribió otra, se respeta.
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import bindparam, or_, update

from app.db.session import SessionLocal, engine
from app.models import Line, LineSource

STREETS = Path(__file__).resolve().parent.parent / "data" / "gore" / "line_streets.json"


async def main() -> None:
    if not STREETS.exists():
        print(f"No está {STREETS.name}: las líneas quedan sin descripción.")
        return
    rows = [
        {"line_code": code, "new_description": info["description"][:300]}
        for code, info in json.loads(STREETS.read_text(encoding="utf-8")).items()
    ]
    stmt = (
        update(Line)
        .where(
            Line.code == bindparam("line_code"),
            Line.source == LineSource.OFFICIAL,
            or_(Line.description.is_(None), Line.description.like("Por %")),
        )
        .values(description=bindparam("new_description"))
        .execution_options(synchronize_session=False)
    )
    async with SessionLocal() as session, session.begin():
        # executemany: un UPDATE por línea en un solo viaje a la base.
        conn = await session.connection()
        await conn.execute(stmt, rows)
    await engine.dispose()
    print(f"Descripciones de {len(rows)} líneas aplicadas.")


if __name__ == "__main__":
    asyncio.run(main())
