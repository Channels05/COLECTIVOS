"""Ficha de una línea: tarifa, horario, calles en orden, autos en vivo y avisos.

Los pasajeros informan la tarifa y el horario (quedan pendientes) y el administrador los aplica en
/admin, donde también publica avisos para una línea o para todas.
"""

import csv
import io
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from app.api.deps import AuthenticatedContributor, DbSession, RedisClient, require_admin
from app.models import Line, LineInfoReport, LineNotice, LineStatus, ReportStatus
from app.services import line_streets, tracking

public_router = APIRouter(tags=["pasajero"])
contributor_router = APIRouter(prefix="/contributions", tags=["pasajero"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_REPORTS_PER_DAY = 10
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
Fare = Field(default=None, ge=200, le=10_000)
Time = Field(default=None, pattern=TIME_PATTERN)


class NoticeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_code: str | None  # None = para todas las líneas
    message: str
    starts_at: datetime
    ends_at: datetime | None


class LineDetails(BaseModel):
    code: str
    name: str | None
    description: str | None
    commune: str | None
    fare_day: int | None
    fare_night: int | None
    first_departure: str | None
    last_departure: str | None
    info_updated_at: datetime | None
    streets: dict[str, list[str]]  # {"ida": [...], "regreso": [...]}, en el orden del trazado
    live_vehicles: int  # autos de la línea transmitiendo ahora
    live_with_seats: int
    notices: list[NoticeRead]


class LineInfoIn(BaseModel):
    line_code: str = Field(min_length=1, max_length=20)
    fare_day: int | None = Fare
    fare_night: int | None = Fare
    first_departure: str | None = Time
    last_departure: str | None = Time
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def at_least_one(self) -> "LineInfoIn":
        if not any((self.fare_day, self.fare_night, self.first_departure, self.last_departure, (self.note or "").strip())):
            raise ValueError("Informa al menos la tarifa, el horario o una nota.")
        return self


class LineInfoReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_code: str
    fare_day: int | None
    fare_night: int | None
    first_departure: str | None
    last_departure: str | None
    note: str | None
    status: ReportStatus
    created_at: datetime


class LineInfoReportAdmin(LineInfoReportRead):
    contributor_id: str
    # Lo que la línea tiene ahora, para comparar antes de aplicar.
    current: dict[str, int | str | None]


class LineInfoApply(BaseModel):
    """Al aplicar, el administrador puede corregir lo informado (lo que mande reemplaza al aporte)."""

    fare_day: int | None = Fare
    fare_night: int | None = Fare
    first_departure: str | None = Time
    last_departure: str | None = Time


class NoticeIn(BaseModel):
    line_code: str | None = Field(default=None, max_length=20)
    message: str = Field(min_length=3, max_length=280)
    days: int | None = Field(default=None, ge=1, le=365)  # vigencia; None = hasta que se borre


INFO_FIELDS = ("fare_day", "fare_night", "first_departure", "last_departure")


class CsvImport(BaseModel):
    csv: str = Field(min_length=1, max_length=200_000)


class CsvImportResult(BaseModel):
    updated: int
    errors: list[str]


def _active_notices(line_code: str | None = None):
    now = datetime.now(UTC)
    query = select(LineNotice).where(LineNotice.starts_at <= now, or_(LineNotice.ends_at.is_(None), LineNotice.ends_at > now))
    if line_code is not None:
        query = query.where(or_(LineNotice.line_code.is_(None), LineNotice.line_code == line_code))
    return query.order_by(LineNotice.created_at.desc())


# --- Público ------------------------------------------------------------------------------------


@public_router.get("/lines/{code}/details", response_model=LineDetails)
async def line_details(code: str, db: DbSession, redis: RedisClient) -> dict:
    """La ficha de la línea en la app: tarifa, horario, por qué calles va, si hay autos y avisos."""
    line = await db.get(Line, code)
    if line is None or line.status == LineStatus.REJECTED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea no encontrada.")
    live = [v for v in await tracking.fleet(redis) if v["line_code"] == code]
    notices = list(await db.scalars(_active_notices(code)))
    return {
        **{f: getattr(line, f) for f in ("code", "name", "description", "commune", *INFO_FIELDS, "info_updated_at")},
        "streets": line_streets.sequence(code),
        "live_vehicles": len(live),
        "live_with_seats": sum(1 for v in live if v["available_seats"] > 0),
        "notices": notices,
    }


@public_router.get("/notices", response_model=list[NoticeRead])
async def notices(db: DbSession) -> list[LineNotice]:
    """Avisos vigentes (de todas las líneas y generales): la app los muestra en el inicio y el mapa."""
    return list(await db.scalars(_active_notices().limit(50)))


# --- Pasajero aportante -------------------------------------------------------------------------


@contributor_router.post("/line-info", response_model=LineInfoReportRead, status_code=status.HTTP_201_CREATED)
async def report_line_info(body: LineInfoIn, contributor: AuthenticatedContributor, db: DbSession) -> LineInfoReport:
    """"¿Cuánto cobra? ¿Hasta qué hora pasa?": queda pendiente hasta que lo revise el administrador."""
    line = await db.get(Line, body.line_code)
    if line is None or line.status == LineStatus.REJECTED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea no encontrada.")
    recent = await db.scalar(
        select(func.count()).select_from(LineInfoReport).where(
            LineInfoReport.contributor_id == contributor.id,
            LineInfoReport.created_at > datetime.now(UTC) - timedelta(days=1),
        )
    )
    if recent >= MAX_REPORTS_PER_DAY:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Ya enviaste muchos datos hoy. Gracias, sigue mañana.")
    report = LineInfoReport(
        line_code=body.line_code, contributor_id=contributor.id, note=(body.note or "").strip() or None,
        **{f: getattr(body, f) for f in INFO_FIELDS},
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


# --- Administrador ------------------------------------------------------------------------------


@admin_router.get("/line-info", response_model=list[LineInfoReportAdmin])
async def list_reports(db: DbSession, status_filter: ReportStatus = ReportStatus.PENDING) -> list[dict]:
    rows = await db.execute(
        select(LineInfoReport, Line).join(Line, Line.code == LineInfoReport.line_code)
        .where(LineInfoReport.status == status_filter).order_by(LineInfoReport.created_at.desc()).limit(200)
    )
    return [
        {
            **LineInfoReportRead.model_validate(report).model_dump(),
            "contributor_id": str(report.contributor_id),
            "current": {f: getattr(line, f) for f in INFO_FIELDS},
        }
        for report, line in rows
    ]


async def _pending_report(db: DbSession, report_id: int) -> LineInfoReport:
    report = await db.get(LineInfoReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aporte no encontrado.")
    if report.status != ReportStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese aporte ya fue revisado.")
    return report


@admin_router.post("/line-info/{report_id}/apply", response_model=LineInfoReportRead)
async def apply_report(report_id: int, body: LineInfoApply, db: DbSession) -> LineInfoReport:
    """Copia a la línea los datos informados (o los corregidos por el administrador)."""
    report = await _pending_report(db, report_id)
    line = await db.get(Line, report.line_code)
    now = datetime.now(UTC)
    for field in INFO_FIELDS:
        value = getattr(body, field) or getattr(report, field)
        if value is not None:
            setattr(line, field, value)
    line.info_updated_at = now
    report.status = ReportStatus.APPLIED
    report.reviewed_at = now
    await db.commit()
    await db.refresh(report)
    return report


@admin_router.post("/line-info/{report_id}/discard", response_model=LineInfoReportRead)
async def discard_report(report_id: int, db: DbSession) -> LineInfoReport:
    report = await _pending_report(db, report_id)
    report.status = ReportStatus.DISCARDED
    report.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(report)
    return report


def _parse_row(row: list[str]) -> tuple[str, dict]:
    """línea, tarifa día, tarifa noche, primer auto, último auto (vacío = no cambiar)."""
    cells = [c.strip() for c in row] + [""] * 5
    code = cells[0].upper()
    if not code:
        raise ValueError("falta el código de la línea")
    values: dict = {}
    for field, cell in zip(("fare_day", "fare_night"), cells[1:3], strict=True):
        if cell:
            fare = int(re.sub(r"\D", "", cell) or "0")
            if not 200 <= fare <= 10_000:
                raise ValueError(f"tarifa fuera de rango: {cell}")
            values[field] = fare
    for field, cell in zip(("first_departure", "last_departure"), cells[3:5], strict=True):
        if cell:
            cell = cell.replace(".", ":").zfill(5)
            if not re.fullmatch(TIME_PATTERN, cell):
                raise ValueError(f"hora inválida: {cell} (usa 06:00)")
            values[field] = cell
    if not values:
        raise ValueError("no trae tarifa ni horario")
    return code, values


@admin_router.post("/line-info/import", response_model=CsvImportResult)
async def import_line_info(body: CsvImport, db: DbSession) -> dict:
    """Carga de golpe tarifas y horarios: una fila por línea (con o sin encabezado; separado por
    coma o punto y coma). Las celdas vacías no cambian lo que la línea ya tiene."""
    sample = body.csv[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    updated, errors, now = 0, [], datetime.now(UTC)
    for number, row in enumerate(csv.reader(io.StringIO(body.csv), delimiter=delimiter), start=1):
        if not any(c.strip() for c in row):
            continue
        if number == 1 and not any(ch.isdigit() for ch in "".join(row[1:])):
            continue  # encabezado
        try:
            code, values = _parse_row(row)
        except ValueError as exc:
            errors.append(f"Fila {number}: {exc}")
            continue
        line = await db.get(Line, code)
        if line is None:
            errors.append(f"Fila {number}: no existe la línea {code}")
            continue
        for field, value in values.items():
            setattr(line, field, value)
        line.info_updated_at = now
        updated += 1
    await db.commit()
    return {"updated": updated, "errors": errors[:50]}


@admin_router.get("/notices", response_model=list[NoticeRead])
async def admin_notices(db: DbSession) -> list[LineNotice]:
    return list(await db.scalars(_active_notices().limit(200)))


@admin_router.post("/notices", response_model=NoticeRead, status_code=status.HTTP_201_CREATED)
async def create_notice(body: NoticeIn, db: DbSession) -> LineNotice:
    line_code = (body.line_code or "").strip().upper() or None
    if line_code and await db.get(Line, line_code) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe la línea {line_code}.")
    notice = LineNotice(
        line_code=line_code, message=body.message.strip(),
        ends_at=datetime.now(UTC) + timedelta(days=body.days) if body.days else None,
    )
    db.add(notice)
    await db.commit()
    await db.refresh(notice)
    return notice


@admin_router.delete("/notices/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notice(notice_id: int, db: DbSession) -> None:
    notice = await db.get(LineNotice, notice_id)
    if notice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado.")
    await db.delete(notice)
    await db.commit()
