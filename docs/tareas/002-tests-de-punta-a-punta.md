# 002 · Tests de punta a punta con base de datos real

Estado: pendiente

## Por qué

Hoy los tests (`tests/`) no tocan la base de datos: prueban funciones sueltas o usan un Redis
falso. Las funciones de las últimas semanas (tarifas, avisos, reportes, inscripción de conductores,
"Seguir mi viaje", editar líneas) se probaron a mano contra la API local, con scripts que ya no
existen. Queremos que esas pruebas corran **solas en GitHub antes de cada despliegue** (Render solo
publica si pasan), para que un cambio futuro no rompa algo sin que nadie lo note.

## Cómo tienen que funcionar

### Carpeta y preparación — `tests/integracion/conftest.py`

- Todos los tests nuevos van en `tests/integracion/` (con `__init__.py`).
- **Si no hay base de datos disponible, se saltan** (no fallan): en el `conftest.py`, intenta
  conectarte a `get_settings().database_url` al cargar la carpeta; si falla, usa
  `pytest.skip(..., allow_module_level=True)` o márcalos como saltados. Así
  `pytest -q` sigue pasando en un PC sin Docker.
- Fixture **`client`** (una por test):
  - `app.state.redis = FakeAsyncRedis(decode_responses=True)` (nuevo en cada test, así los límites
    de solicitudes por IP empiezan de cero) y
    `app.state.route_validator = RouteValidator(buffer_m=40)` vacío. Mira cómo lo hace
    `tests/test_api_tracking.py`.
  - **Importante:** `TestClient` usado sin `with` corre cada request en un event loop distinto, y
    el pool de conexiones de `app/db/session.py` se rompe entre loops. Reemplaza la dependencia
    `get_db` (de `app.db.session`) con `app.dependency_overrides`, usando un engine de pruebas con
    `poolclass=NullPool` (`sqlalchemy.pool.NullPool`) y `async_sessionmaker(..., expire_on_commit=False)`.
  - No uses `with TestClient(app)`: correría el `lifespan`, que se conecta a un Redis real.
  - Al final, `app.dependency_overrides.clear()`.
- Fixture **`admin`**: los headers `{"X-Admin-Key": get_settings().admin_api_key}`.
- Fixture **`aportante`**: crea un pasajero anónimo con `POST /api/v1/contributors` y devuelve los
  headers `X-Contributor-Id` / `X-Contributor-Token`.
- Fixture **`linea`**: inserta directamente en la base una línea oficial activa (modelo `Line`,
  `source=LineSource.OFFICIAL`, `status=LineStatus.ACTIVE`, una `description`) con un código al
  azar, por ejemplo `"T" + 6 dígitos`, y devuelve el código. **La base de CI parte vacía**: ningún
  test puede depender de líneas como `233035`.
- Patentes al azar con el formato válido (4 letras de `BCDFGHJKLPRSTVWXYZ` + 2 dígitos).
- Ningún test llama a internet: en búsquedas de lugares, reemplaza `app.services.places._nominatim`
  con `monkeypatch` por una función `async` que devuelva `[]`.

### Qué probar (un archivo por tema)

**`test_ficha_y_tarifas.py`** (`app/api/v1/endpoints/line_info.py`)
- `GET /api/v1/lines/{code}/details` → 200 con `live_vehicles == 0`; código inexistente → 404.
- Informar tarifa (`POST /api/v1/contributions/line-info`): cuerpo solo con `line_code` → 422;
  hora `"25:00"` → 422; tarifa y horario completos → 201 `pending`.
- Admin: aparece en `GET /api/v1/admin/line-info` con `current`; aplicar corrigiendo
  `fare_night` → `applied`; aplicar de nuevo → 409; la ficha muestra los valores (con la nocturna
  corregida). Otro aporte descartado no cambia la línea.
- `POST /api/v1/admin/line-info/import` con un CSV de 2 filas buenas (dos líneas creadas por el
  test) y 3 malas (línea inexistente, tarifa 50000, hora 25:00) → `updated == 2` y 3 errores; una
  celda vacía no borra lo que la línea ya tenía.

**`test_avisos.py`**
- Aviso de una línea (código en minúsculas: se guarda en mayúsculas) y aviso general → 201;
  línea inexistente → 404.
- La ficha de esa línea trae ambos avisos; la de otra línea solo el general; `GET /api/v1/notices`
  trae los dos. Borrar → 204. Sin clave de admin → 403 o 422.

**`test_reportes_y_busquedas.py`** (`app/api/v1/endpoints/feedback.py`)
- Reporte con `lat`/`lon` → 201 `pending`; `kind` inválido → 422; línea inexistente → 404.
- En `GET /api/v1/admin/route-reports` aparece con su `lat`. Resolver → `applied`; resolver de
  nuevo → 409; otro se descarta → `discarded`.
- Lugar sin resultado (`GET /api/v1/places/search?q=...`, con `_nominatim` reemplazado) → `[]`, y
  aparece en `GET /api/v1/admin/search-misses?days=1` en `places`.
- Viaje sin resultado entre dos puntos de la cordillera sin líneas ni Metro (por ejemplo
  `-33.30,-70.40` → `-33.33,-70.43`) → `options` y `combos` vacíos, y aparece en `trips` con el
  destino redondeado.

**`test_inscripcion_conductores.py`** (`app/api/v1/endpoints/driver_applications.py`)
- Pedir inscripción con la patente en minúsculas y con guion → 201, patente normalizada, `pending`
  y un `token` de al menos 32 caracteres. Patente inválida (`"XX"`) → 422.
- `GET /api/v1/driver-applications/{id}` con `X-Application-Token` → `pending`; con otro token →
  404. Aparece en `GET /api/v1/admin/driver-applications`.
- Aprobar con `max_capacity: 3` → `approved` y `vehicle_id`; aprobar de nuevo → 409.
- El token de la solicitud sirve como `X-Device-Token` del vehículo: `GET /api/v1/vehicles/{id}`
  → 200 con `max_capacity == 3`, y puede enviar su ubicación (`POST .../location` → 200 o 202).
- Pedir de nuevo con la misma patente → 409. Otra solicitud rechazada con nota: el estado
  devuelve `rejected` y la nota.

**`test_seguir_mi_viaje.py`** (`app/api/v1/endpoints/shares.py`)
- Crear un vehículo (`POST /api/v1/vehicles` como admin), enviar una ubicación, y
  `POST /api/v1/shares` → la `url` contiene `/seguir/`.
- `GET /api/v1/shares/{id}` → `active`, la patente y los cupos enviados. `GET /seguir/{id}` → 200
  con el texto "Seguir el viaje".
- Borrar del Redis falso el estado del auto (clave de `app.services.tracking.state_key(id)`) →
  `active == false` y sigue trayendo la última posición.
- Enlace inventado → 404; compartir un vehículo que no transmite → 404.

**`test_editar_linea.py`** (tarea 001)
- `PATCH /api/v1/admin/lines/{code}`: cambia el letrero; un texto vacío borra la comuna sin tocar
  el letrero; `{"name": 123}` → 422; código inexistente → 404.

### GitHub Actions — `.github/workflows/tests.yml`

- Mueve el paso **"Migraciones desde cero"** (`alembic upgrade head`) para que corra **antes** de
  "Tests": los tests nuevos necesitan las tablas. `alembic check` sigue al final.

## No tocar

- El código de la aplicación (`app/`): esta tarea solo agrega tests y cambia el workflow. Si un
  test descubre un error en la aplicación, **no lo arregles**: déjalo marcado con
  `@pytest.mark.xfail(reason="...")` y explícalo en "Resultado".
- Los tests que ya existen en `tests/`.

## Cómo se comprueba

- Con la base local encendida (`docker compose up -d db redis`) y migrada
  (`.venv/Scripts/python.exe -m alembic upgrade head`): `.venv/Scripts/python.exe -m pytest -q` →
  todo pasa, incluidos los nuevos.
- Con la base apagada (`docker compose stop db`): `pytest -q` → los nuevos aparecen como
  **saltados**, no como fallas. Vuelve a encenderla al terminar (`docker compose start db`).
- Claude revisa que cada caso de arriba esté cubierto y que los tests de verdad fallen si se rompe
  la aplicación (por ejemplo, cambiando a mano un código de respuesta y viendo que el test falla).

## Resultado (lo completa Copilot)

_(archivos cambiados, comandos corridos y su salida, dudas)_

## Revisión (la completa Claude)

_(pendiente)_
