# 003 · Resumen de "Tus aportes" para el pasajero

Estado: lista

## Por qué

Mientras no haya conductores transmitiendo, la app vive de lo que aportan los pasajeros: marcas de
"pasó un colectivo", recorridos grabados, líneas propuestas y reportes de tarifa o recorrido. Para
motivarlos, el inicio de la app va a mostrar cuánto ha aportado cada uno y cuánto sirvió (eso es la
tarea 002 de `mobile/`). Esta tarea hace el endpoint del servidor que da esas cifras.

## Qué hacer

### 1. Esquema — archivo nuevo `backend/app/schemas/my_contributions.py`

```python
class MyContributions(BaseModel):
    sightings: int            # marcas "pasó un colectivo"
    sighting_lines: int       # líneas distintas en que marcó
    recordings_approved: int  # recorridos grabados y aprobados
    recordings_pending: int   # recorridos esperando revisión (status pending_review)
    lines_approved: int       # líneas que propuso y quedaron activas
    reports_sent: int         # reportes de tarifa/horario + reportes de recorrido enviados
    reports_applied: int      # de esos, los que el administrador aplicó (status applied)
```

Con un docstring corto en español.

### 2. Servicio — archivo nuevo `backend/app/services/my_contributions.py`

Una función `async def summary(db: AsyncSession, contributor_id: uuid.UUID) -> dict` que calcule
las siete cifras con consultas `select(func.count())` (y `func.count(func.distinct(...))` para
`sighting_lines`). Sigue el modelo de `profile()` en `backend/app/services/moderation.py`.

- Tablas: `Sighting`, `RouteRecording` (`RecordingStatus.APPROVED` / `PENDING_REVIEW`), `Line`
  (`Line.created_by == contributor_id` y `LineStatus.ACTIVE`), `LineInfoReport` y `RouteReport`
  (ambos con `ReportStatus.APPLIED` para `reports_applied`).
- Todas las cifras deben ser `int` (usa `or 0` donde `scalar` pueda devolver `None`).

### 3. Endpoint — `backend/app/api/v1/endpoints/contributions.py`

En el `contributor_router` que ya existe (prefijo `/contributions`):

```python
@contributor_router.get("/me", response_model=MyContributions)
async def my_contributions(contributor: AuthenticatedContributor, db: DbSession) -> dict:
    """"Tus aportes": cuánto aportó este pasajero y cuánto sirvió."""
```

Queda en `GET /api/v1/contributions/me` con los encabezados `X-Contributor-Id` y
`X-Contributor-Token` (los mismos de los otros endpoints del aportante). Sin credenciales válidas →
el error que ya da `AuthenticatedContributor`.

### 4. README — `backend/README.md`

Agrega una fila en la tabla de la API, junto a las de `/api/v1/contributions/recordings`:
`| GET | /api/v1/contributions/me (aportante) | pasajero: "Tus aportes" (cuánto aportó y cuánto sirvió) |`
(con el mismo formato de las filas vecinas).

### 5. Test de integración — archivo nuevo `backend/tests/integracion/test_tus_aportes.py`

Con las fixtures de `backend/tests/integracion/conftest.py` (`client`, `admin`, `aportante`,
`linea`). Mira `backend/tests/integracion/test_ficha_y_tarifas.py` como modelo.

1. Aportante nuevo → `GET /api/v1/contributions/me` responde 200 con las siete cifras en `0`.
2. Envía dos reportes de tarifa (`POST /api/v1/contributions/line-info`) para `linea`, aplica uno
   con `POST /api/v1/admin/line-info/{id}/apply` → `reports_sent == 2` y `reports_applied == 1`.
3. Envía un reporte de recorrido (`POST /api/v1/contributions/route-reports`, `kind: "other"`) →
   `reports_sent == 3`.
4. Sin encabezados de aportante → la respuesta **no** es 200.

## No tocar

- Modelos y migraciones (no hacen falta columnas nuevas).
- `backend/app/services/moderation.py` y el endpoint de administrador de aportantes.
- El endpoint `GET /api/v1/contributions/recordings` (la app ya lo usa para otra cosa).

## Cómo se comprueba

Con Docker encendido (`docker compose start db` en `backend/`):

- `.venv/Scripts/python.exe -m pytest -q` (la corrida **completa**) → todo pasa (hoy son 77, más
  los nuevos).
- Claude lo prueba contra la API local con un aportante real.

## Resultado (lo completa Copilot)

- Archivos cambiados:
  - `app/schemas/my_contributions.py`: se agregó el esquema `MyContributions` con las siete cifras.
  - `app/services/my_contributions.py`: se agregó el servicio que cuenta marcas, grabaciones,
    líneas y reportes del aportante.
  - `app/api/v1/endpoints/contributions.py`: se agregó `GET /api/v1/contributions/me`.
  - `README.md`: se agregó la fila del endpoint en la tabla de API.
  - `tests/integracion/test_tus_aportes.py`: se agregaron las comprobaciones de cifras iniciales,
    reportes aplicados, reporte de recorrido y credenciales ausentes.
- Comandos corridos:
  - `docker compose start db`
  - `.venv/Scripts/python.exe -m pytest -q`
- Salida:
  - Suite completa: `78 passed, 1 warning` (único aviso: deprecación de Starlette/FastAPI sobre
    `TestClient`).
- Dudas: ninguna.

## Revisión (la completa Claude)

### Revisión 1 — aprobada

Hecho como pedía la tarea, en una sola ronda. Suite completa: `78 passed`. Probado además contra la
base local con marcas reales sobre el trazado de dos líneas (233035 y 233025): `sightings == 2`,
`sighting_lines == 2`; con un token inválido → 401. Detalle menor que no hace falta corregir: la
fila del README quedó entre las dos filas de grabaciones.
