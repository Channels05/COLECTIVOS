# 002 · Tests de punta a punta con base de datos real

Estado: lista

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

- Archivos cambiados:
  - `tests/integracion/__init__.py`: inicialización del paquete de integración.
  - `tests/integracion/conftest.py`: detección de base disponible, `TestClient` sin lifespan, motor con
    `NullPool`, dependencias de base y Redis, fixtures `admin`, `aportante`, `linea` y `linea_factory`.
  - `tests/integracion/test_ficha_y_tarifas.py`: ficha, aportes de tarifas y horarios, aplicación,
    descarte e importación CSV.
  - `tests/integracion/test_avisos.py`: avisos de línea y generales, vigencia, borrado y acceso admin.
  - `tests/integracion/test_reportes_y_busquedas.py`: reportes, resolución y descarte, búsquedas
    fallidas sin internet y registro de lugares/viajes sin resultado.
  - `tests/integracion/test_inscripcion_conductores.py`: solicitud, normalización de patente,
    aprobación, rechazo, token del vehículo y ubicación.
  - `tests/integracion/test_seguir_mi_viaje.py`: vehículo activo, enlace compartido, página pública
    y comportamiento al perder transmisión.
  - `tests/integracion/test_editar_linea.py`: edición, borrado de comuna y validaciones del endpoint
    administrativo.
  - `.github/workflows/tests.yml`: migraciones movidas antes del paso "Tests".
- Comandos corridos:
  - `docker compose up -d db redis`
  - `.venv/Scripts/python.exe -m alembic upgrade head`
  - `.venv/Scripts/python.exe -m pytest -q tests/integracion`
  - `.venv/Scripts/python.exe -m pytest -q`
  - `docker compose stop db`
  - `.venv/Scripts/python.exe -m pytest -q tests/integracion`
  - `docker compose start db`
  - `.venv/Scripts/python.exe -m pytest -q`
- Salida:
  - Integración con base local: `7 passed, 1 warning`.
  - Suite completa con base local: `77 passed, 1 warning`.
  - Integración con PostgreSQL apagado: `7 skipped, 1 warning`.
  - Suite completa final después de encender PostgreSQL: `77 passed, 1 warning`.
  - Aviso existente: deprecación de Starlette/FastAPI sobre `TestClient`.
- Dudas: ninguna.

## Revisión (la completa Claude)

### Revisión 1 — cambios pedidos

Muy bien hecho. Cubre todos los casos de la tarea y el workflow quedó en el orden correcto.
Comprobado:

- Con la base local: 77 pasan (7 nuevos). Sin la base: los nuevos se saltan.
- Los tests **sí detectan errores**: rompí a mano la aplicación en cuatro lugares (aplicar una tarifa
  dos veces sin 409, el token de la inscripción que no sirve para el vehículo, el enlace compartido
  que no guarda la última posición, avisos de una línea que aparecen en todas) y en los cuatro casos
  falló un test. Después la dejé como estaba.

Falta:

1. **Protección para no escribir en producción.** Los tests crean líneas, vehículos y aportes en la
   base que diga `DATABASE_URL`. Si algún día esa variable apunta a la base real (Neon), la
   ensuciarían. En `conftest.py`, salta todos los tests de integración (con un motivo claro) si el
   host de `settings.database_url` no es `localhost` ni `127.0.0.1`. Puedes leerlo con
   `sqlalchemy.engine.make_url(settings.database_url).host`.
2. **Celda vacía del CSV.** Falta probar lo que pedía la tarea: que una celda vacía **no borre un
   valor que la línea ya tenía**. Agrega en `test_ficha_y_tarifas.py` una segunda importación con
   una fila válida para `linea_b` que traiga solo la tarifa de día (por ejemplo `linea_b,950,,,`) y
   comprueba que `fare_night` sigue en 1100 y `first_departure` en `"06:00"`.
3. **Clave de Redis escrita a mano.** En `test_seguir_mi_viaje.py` usa
   `app.services.tracking.state_key(vehicle["id"])` en vez de `f"vehicle:{...}:state"`, así el test
   no se rompe si cambia el formato de la clave. Mueve también el `import asyncio` al principio del
   archivo.
4. **Función repetida.** `patente_al_azar` está copiada en dos archivos: déjala una sola vez en
   `conftest.py` (como fixture, o como función que los tests importen desde
   `tests.integracion.conftest`).

Después: `.venv/Scripts/python.exe -m pytest -q` (con la base encendida) y agrega abajo un
"Resultado 2".

### Resultado 2

- Archivos cambiados:
  - `tests/integracion/conftest.py`: protege la ejecución remota comprobando el host de
    `DATABASE_URL`; solo permite `localhost` y `127.0.0.1`. Se agregó también la generación
    compartida de patentes y la fixture `patente_factory`.
  - `tests/integracion/test_ficha_y_tarifas.py`: se agregó una segunda importación CSV con la
    tarifa de día sola y se comprobó que la tarifa nocturna y la primera salida no se borran.
  - `tests/integracion/test_seguir_mi_viaje.py`: usa `tracking.state_key(...)` para borrar el
    estado Redis y el `import asyncio` quedó al principio del archivo.
  - `tests/integracion/test_inscripcion_conductores.py`: dejó de repetir la función de patente
    aleatoria y usa la fixture compartida.
- Comandos corridos:
  - `docker compose start db`
  - `.venv/Scripts/python.exe -m pytest -q`
  - `$env:DATABASE_URL='postgresql://colectivos:colectivos@ejemplo.neon.tech/colectivos'`
    seguida de `.venv/Scripts/python.exe -m pytest -q tests/integracion`
- Salida:
  - Suite completa con la base local: `77 passed, 1 warning`.
  - Protección remota simulada: `7 skipped, 1 warning`, con motivo de seguridad por host remoto.
  - Aviso existente: deprecación de Starlette/FastAPI sobre `TestClient`.
- Dudas: ninguna.

### Revisión 2 — un cambio más

Los cuatro puntos quedaron bien (con la base local: 77 pasan; la protección de host funciona). Pero
encontré un error que venía de la primera versión y se me pasó en la revisión 1:

**Sin base de datos se saltan los 77 tests, no solo los 7 de integración.** Probado con la corrida
completa (`pytest -q`, no solo `tests/integracion`): con `DATABASE_URL` remota → `77 skipped`; con la
base apagada → `77 skipped`. El hook `pytest_collection_modifyitems` de
`tests/integracion/conftest.py` recibe **todos** los tests de la sesión, no solo los de esa carpeta,
así que marca como saltados también los tests unitarios (`tests/test_*.py`). Un error en ellos
pasaría inadvertido en un PC sin Docker.

Arreglo: borra el hook `pytest_collection_modifyitems` y en su lugar agrega en ese mismo
`conftest.py` una fixture `autouse=True` (que pytest aplica solo a los tests de esa carpeta) que
llame a `pytest.skip(DATABASE_SKIP_REASON)` cuando `DATABASE_AVAILABLE` sea `False`.

Cómo comprobarlo (los tres con la corrida **completa** `pytest -q`):
- Base local encendida → `77 passed`.
- `DATABASE_URL` remota → `70 passed, 7 skipped`.
- Base apagada (`docker compose stop db`) → `70 passed, 7 skipped`. Al terminar,
  `docker compose start db`.

Agrega abajo un "Resultado 3".

### Resultado 3

- Archivos cambiados:
  - `tests/integracion/conftest.py`: se eliminó `pytest_collection_modifyitems` y se agregó la
    fixture `require_database`, con `autouse=True`, que aplica el salto solo dentro de la carpeta
    `tests/integracion`.
- Comandos corridos:
  - `docker compose start db`
  - `.venv/Scripts/python.exe -m pytest -q`
  - `$env:DATABASE_URL='postgresql://colectivos:colectivos@ejemplo.neon.tech/colectivos'`
    seguida de `.venv/Scripts/python.exe -m pytest -q`
  - `docker compose stop db`
  - `.venv/Scripts/python.exe -m pytest -q`
  - `docker compose start db`
- Salida:
  - Base local encendida: `77 passed, 1 warning`.
  - Base remota simulada: `70 passed, 7 skipped, 1 warning`.
  - Base local apagada: `70 passed, 7 skipped, 1 warning`.
  - PostgreSQL volvió a quedar encendido al terminar.
- Dudas: ninguna.

### Revisión 3 — aprobada

El cambio quedó bien. Comprobado con la corrida completa (`pytest -q`): base local → `77 passed`;
`DATABASE_URL` remota → `70 passed, 7 skipped` (motivo: host remoto); base apagada →
`70 passed, 7 skipped`. Las cuatro fallas puestas a propósito en la aplicación (aplicar una tarifa
dos veces, token de la inscripción, última posición del enlace compartido, avisos por línea) hacen
fallar los tests. (Nota: el "Resultado 3" quedó escrito en medio de la Revisión 2; lo moví a su
lugar. La próxima vez, agrégalo siempre al final del archivo.)
