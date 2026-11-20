# Colectivos API — MVP piloto La Florida

## Arquitectura

```
App conductor ──POST c/5s──► FastAPI ──(Shapely en memoria: ¿en ruta?)──► Redis
                                                                          ├─ GEO set  vehicles:geo     ◄── GET /nearby (app pasajero)
                                                                          ├─ HASH     vehicle:{id}:state (TTL 30 s = "activo")
                                                                          └─ STREAM   stream:locations
                                                                                  │
                                              workers/location_persister ◄────────┘ (lotes de 1000)
                                                         │
                                                         ▼
                                           PostgreSQL + PostGIS (vehicles, routes, location_updates)
                                                         │
                                                         ▼
                                    analytics/ (DuckDB → Parquet: ocupación, km vacíos, desvíos)
```

El camino caliente (POST del conductor) **no escribe en PostgreSQL**: un solo pipeline a Redis.

## Estructura

```
backend/
├── app/
│   ├── main.py                  # FastAPI + lifespan (Redis, caché de trazados)
│   ├── core/                    # config (pydantic-settings), seguridad (tokens de dispositivo)
│   ├── db/                      # Base declarativa, engine/sesión async
│   ├── models/                  # SQLAlchemy: Vehicle, Route, LocationUpdate
│   ├── schemas/                 # Pydantic: contratos de entrada/salida de la API
│   ├── services/                # lógica de dominio, sin FastAPI
│   │   ├── route_validator.py   #   Shapely en memoria + alternativa PostGIS
│   │   └── tracking.py          #   estado en tiempo real sobre Redis
│   ├── api/
│   │   ├── deps.py              # inyección: DB, Redis, auth vehículo/admin
│   │   └── v1/endpoints/        # driver, passenger, vehicles (admin), routes
│   └── workers/                 # procesos aparte: Redis Stream → PostGIS
├── alembic/                     # migraciones de esquema (env.py async + versions/)
├── scripts/                     # descarga/importación de trazados, simulador de conductor
├── analytics/                   # DuckDB
├── data/gore/                   # trazados oficiales GORE RM (toda la región y recorte La Florida)
├── data/overpass/               # queries Overpass QL + GeoJSON exportados
└── tests/
```

Regla de dependencias: `api → services → models/schemas`. Los servicios no importan FastAPI,
así se reutilizan desde workers, scripts y tests.

## Levantar en local

```bash
cp .env.example .env        # si 5432/6379 están ocupados, cambiar *_HOST_PORT y las URLs
docker compose up -d --wait
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
alembic upgrade head        # crea PostGIS, enums y tablas

# Trazados oficiales de colectivos (GORE RM) de toda la región, con su comuna principal
python -m scripts.fetch_gore_routes --output data/gore/colectivos_rm.geojson
python -m scripts.import_gore_routes data/gore/colectivos_rm.geojson
# (o solo una comuna: --commune "La Florida" --output data/gore/colectivos_la_florida.geojson)

uvicorn app.main:app --host 0.0.0.0 --port 8000   # docs en http://localhost:8000/docs
python -m app.workers.location_persister          # en otra terminal
pytest
```

### Datos de trazados

**Fuente principal: geoportal GORE RM**, capa
[`MOV_rutas_taxis_colectivos`](https://geo.gobiernosantiago.cl/portal/apps/sites/#/geoportal-gore-rm/datasets/596ce3f0a30d464a93785d0f6367d138)
Importados: **1.522 tramos, 410 líneas** (322 urbanas y 88 rurales) en **47 comunas**; solo
se omite `236038 _`, que no trae sentido. En La Florida: 61 líneas que pasan por la comuna
(39 la tienen como comuna principal).

| Campo GORE | Se guarda como |
|---|---|
| `nombre` urbano `233010_I` / `_R` | `line_code` = `233010`, `direction` = ida / regreso |
| `nombre` rural `20061_ITINERARIO_3_R` | `line_code` = `20061` (sus 1–9 itinerarios forman un solo corredor) |
| `recorrido` | `kind`: `trazado`, `via_alternativa`, `bucle`, `nocturno` (rurales: vacío → `trazado`) |
| `tipo` `…URBANO 200423` | `source_date` = 2023-04-20 (fecha de corte del registro) |
| — (calculado con límites OSM) | `commune` = comuna donde recorre más metros |

- **Desactualización**: el corte es del **20-04-2023** (capa editada por última vez el 09-07-2024).
  La API expone `source_date` para que la app pueda advertirlo. Antes del piloto, validar las
  líneas de la comuna piloto contra el registro vigente del MTT.
- **Calidad**: mediana de 100 % del trazado a ≤ 15 m de una calle OSM (p10: 96 %). Revisar a
  mano las de menor calce: 232017 (73–80 %), 235091, 234021.
- **Validación de desvíos**: trazado + vías alternativas + bucles cuentan como "en ruta"; los
  trazados nocturnos solo entre `NIGHT_HOURS` (por defecto 22–6, **supuesto a confirmar** con
  la resolución de cada línea).
- **Código de línea**: es el código del registro (ej. `233010`); puede no coincidir con el número
  del letrero del auto. Hay que mapearlo al registrar vehículos.
- **Letrero y calles**: el registro no trae el texto del letrero. Cada línea se describe por sus
  calles principales ("Por Av. Vicuña Mackenna, Av. Colombia y …"), calculadas con OpenStreetMap
  por `scripts/build_line_streets.py` (348 de 408 líneas; las rurales fuera del Gran Santiago
  quedan sin descripción). El resultado, `data/gore/line_streets.json`, va en git y
  `scripts/describe_lines.py` lo aplica al arrancar. El letrero lo anotan los pasajeros al grabar
  y el administrador lo pasa al catálogo al aprobar. Para recalcular las calles:
  ```bash
  pip install osmium
  curl -o data/osm/Santiago.osm.pbf https://download.bbbike.org/osm/bbbike/Santiago/Santiago.osm.pbf
  python -m scripts.build_line_streets
  ```
- **Licencia**: la capa no declara licencia; confirmar con el GORE antes de redistribuir.

**OSM** (`fetch_overpass_routes`) queda como fuente secundaria: hoy no hay colectivos
(`route=share_taxi`) mapeados en Santiago (verificado oct-2026). `ejemplo_bus_e13.overpassql`
trae un recorrido de bus real, útil solo para pruebas.

### Simular una flota (demo en toda la RM)

```bash
# 2 líneas por comuna (+6 en la comuna piloto), incluidas las rurales: ~92 autos en 46 comunas
python -m scripts.simulate_fleet --api http://localhost:8000 --per-commune 2 --pilot-lines 6 --include-rural
```

Registra un auto por línea solo la primera vez (credenciales en `.demo/flota_demo.json`, fuera de
git) y en las siguientes ejecuciones los reutiliza. Un ping rechazado se informa y el auto sigue.

### Simular un conductor

```bash
# 1. registrar un auto (guarda id y device_token de la respuesta)
curl -X POST localhost:8000/api/v1/vehicles -H "X-Admin-Key: <ADMIN_API_KEY>" \
     -H "Content-Type: application/json" -d '{"plate":"BCDF12","line_code":"233021","max_capacity":4}'
# 2. recorrer el trazado (ping cada 5 s; --detour-at 10 fuerza un desvío de 150 m)
python -m scripts.simulate_driver --api http://localhost:8000 --vehicle-id <id> --token <device_token>
```

Analítica del día (hora de Santiago): `python -m analytics.daily_report 2026-10-24`.

## Desplegar en la nube (Render + Neon, plan gratis)

- **API y Redis en Render** (`render.yaml`, Blueprint): no vencen; la API se duerme tras 15 min
  sin tráfico (la primera petición tarda ~1 min) y Redis no persiste a disco.
- **Base de datos en [Neon](https://neon.com)**: Postgres con PostGIS, gratis y sin vencimiento
  (1 GB; la compute se apaga sola tras 5 min sin uso). La base gratis de Render vence a los 30
  días, por eso no se usa.

Al arrancar, el contenedor (`docker-entrypoint.sh`) aplica las migraciones (crean PostGIS) y, si la
base está vacía, importa los 410 trazados del GORE (`scripts/seed_routes.py`) y describe las líneas
por sus calles (`scripts/describe_lines.py`): una base nueva queda lista sola. El persistidor de
ubicaciones corre dentro de la API (`RUN_WORKER_IN_PROCESS=true`) porque los workers aparte no
tienen plan gratis, y borra el historial de posiciones de más de `LOCATION_RETENTION_DAYS` días
(30 por defecto) para no llenar la base.

1. Subir esta carpeta (`backend/`) a un repositorio **privado** de GitHub (`.env` y `.demo/` quedan fuera por `.gitignore`).
2. En Neon: crear un proyecto en **AWS US East 1 (N. Virginia)**, la misma zona que Render (otra
   región hace lenta cada consulta). Copiar la URL de conexión (Dashboard → Connect).
3. En [render.com](https://render.com): New → Blueprint → elegir el repositorio → Apply. Pide
   `DATABASE_URL`: pegar la de Neon tal cual (la app quita `channel_binding`, cambia `sslmode` por
   `ssl` y usa el endpoint directo en vez del `-pooler`, que no funciona con asyncpg).
4. Cada push a `main` corre los tests en GitHub Actions (`.github/workflows/tests.yml`: tests,
   migraciones desde cero sobre PostGIS y `alembic check`). Render despliega solo si pasan
   (`autoDeployTrigger: checksPass`); si fallan, sigue en línea la versión anterior.
5. La URL queda como `https://colectivos-api.onrender.com` (o con un sufijo si el nombre está
   tomado). La clave de `/admin` está en el servicio → Environment → `ADMIN_API_KEY`.

Probar la imagen en local contra una base vacía:

```bash
docker build -t colectivos-api .
docker run -p 8020:8000 -e DATABASE_URL=postgres://colectivos:colectivos@host.docker.internal:5433/otra_base \
  -e REDIS_URL=redis://host.docker.internal:6379/3 -e ADMIN_API_KEY=prueba -e RUN_WORKER_IN_PROCESS=true colectivos-api
```

## Migraciones (Alembic)

El esquema se versiona con Alembic; la URL sale de `DATABASE_URL`, igual que la app.

```bash
alembic upgrade head                                  # aplicar
alembic revision --autogenerate -m "agrega X"         # tras cambiar un modelo; REVISAR el archivo
alembic check                                         # falla si modelos y BD difieren (útil en CI)
alembic downgrade -1                                  # revertir la última
```

Al revisar una migración autogenerada:
- **Enums**: autogenerate no detecta valores nuevos de un `ENUM`; agregarlos a mano con
  `op.execute("ALTER TYPE route_kind ADD VALUE '...'")`. `drop_table` tampoco borra el tipo:
  la migración inicial lo hace explícitamente en `downgrade`.
- **PostGIS**: `env.py` usa los helpers de GeoAlchemy2 (columnas `geometry` e índices GIST) y
  excluye las tablas de extensiones (`spatial_ref_sys`, y las ~40 de `postgis_tiger_geocoder`
  y `postgis_topology` que trae la imagen Docker) para que nunca se propongan borrarlas.
- Una base creada antes de Alembic (con el antiguo `init_db`) se marca con `alembic stamp head`
  después de comprobar que el esquema coincide.

## Endpoints

| Método | Ruta | Quién |
|---|---|---|
| POST | `/api/v1/vehicles` (header `X-Admin-Key`) | admin: registra auto, devuelve `device_token` una vez |
| GET | `/api/v1/vehicles` (admin) | admin: vehículos registrados (sin tokens) |
| POST | `/api/v1/vehicles/{id}/token` (admin) | admin: token nuevo (QR nuevo); el anterior deja de servir |
| POST | `/api/v1/vehicles/{id}/location` (header `X-Device-Token`) | app conductor, cada 5 s |
| GET | `/api/v1/vehicles/nearby?lat=&lon=&radius_m=2000&line_code=&min_seats=&user_lat=&user_lon=` | app pasajero (con `user_lat/lon`, cada auto trae `route_distance_m`: metros por el recorrido de su línea hasta el pasajero) |
| GET | `/api/v1/routes/{line_code}` | app pasajero: trazado en GeoJSON (`source`: `gore_rm` / `osm` / `recorded`) |
| POST | `/api/v1/routes/reload` (header `X-Admin-Key`) | admin: refresca caché de trazados |
| POST | `/api/v1/vehicles/{id}/recordings` `{direction}` (device token) | conductor: empieza a grabar ida/regreso |
| GET | `/api/v1/vehicles/{id}/recordings/active` (device token) | conductor: retomar grabación en curso |
| POST | `/api/v1/vehicles/{id}/recordings/{rid}/finish` · `/cancel` (device token) | conductor: terminar (propone trazado) o cancelar |
| GET | `/api/v1/admin/recordings?status_filter=` · `/{rid}` (admin) | admin: bandeja y detalle con GeoJSON |
| POST | `/api/v1/admin/recordings/{rid}/approve` · `/reject` `{note, direction?, line_name?, replace_previous?}` (admin) | admin: aprobar (suma el tramo nuevo; `line_name` guarda el letrero) o rechazar |
| GET | `/api/v1/admin/stats?days=28` (admin) | admin: panel "Datos" — pasajeros activos, marcas y grabaciones por línea y por zona (celdas de ~1 km) |
| POST | `/api/v1/client-errors` `{errors: [...]}` | app: reporta sus errores de JavaScript (anónimos; máx. 60 por IP y hora; se guardan 90 días) |
| GET | `/api/v1/admin/errors?days=30` (admin) | admin: panel "Errores" — errores de la app agrupados por mensaje, con versiones, teléfonos y detalle |
| GET | `/api/v1/admin/fleet` (admin) | admin: modo "Flota" — todos los autos transmitiendo, con cupos, velocidad y estado de ruta (se refresca cada 5 s) |
| GET | `/api/v1/admin/occupancy?days=30` (admin) | admin: modo "Ocupación" — qué tan llenos van los autos de cada línea por hora del día |
| POST | `/api/v1/driver-applications` `{plate, line_code, max_capacity, note?}` | app conductor: pedir la inscripción; responde una sola vez el `token` que será el del vehículo |
| GET | `/api/v1/driver-applications/{id}` (`X-Application-Token`) | app conductor: estado de su solicitud; aprobada trae `vehicle_id` |
| GET · POST | `/api/v1/admin/driver-applications` · `/{id}/approve` · `/{id}/reject` (admin) | admin: solicitudes pendientes en "Vehículos"; aprobar crea el vehículo con el token del teléfono |
| GET | `/api/v1/trips/plan?from_lat=&from_lon=&to_lat=&to_lon=` | app pasajero: ¿qué colectivo me lleva? — líneas que pasan cerca de ambos puntos; `combos`: colectivo + Metro (o al revés, o solo Metro) si es más rápido, y dos colectivos con transbordo si ninguno lleva directo (red en `data/transporte/metro.json`, de `scripts/build_metro`) |
| GET | `/api/v1/lines/{code}/details` | app pasajero: ficha de la línea — tarifa, horario, calles en orden por sentido, autos en vivo y avisos |
| GET | `/api/v1/notices` | app: avisos vigentes (de una línea o para todas) |
| POST | `/api/v1/contributions/line-info` `{line_code, fare_day?, fare_night?, first_departure?, last_departure?, note?}` | app pasajero: informar tarifa u horario (queda pendiente; máx. 10 al día) |
| GET · POST | `/api/v1/admin/line-info` · `/{id}/apply` · `/{id}/discard` (admin) | admin: modo "Tarifas y horarios" — aplicar a la línea lo informado (se puede corregir) |
| GET · POST · DELETE | `/api/v1/admin/notices` · `/{id}` (admin) | admin: modo "Avisos" — publicar avisos con vigencia en días |
| POST | `/api/v1/admin/line-info/import` `{csv}` (admin) | admin: cargar tarifas y horarios de muchas líneas (línea; día; noche; primero; último) |
| POST | `/api/v1/contributions/route-reports` `{line_code, kind, message?, lat?, lon?}` | app pasajero: avisar que una línea cambió (`route_changed`, `not_running`, `sign`, `other`) |
| GET · POST | `/api/v1/admin/route-reports` · `/{id}/resolve` · `/{id}/discard` (admin) | admin: modo "Reportes" — con el recorrido y dónde estaba el pasajero |
| GET | `/api/v1/admin/search-misses?days=30` (admin) | admin: modo "Sin resultado" — lugares no encontrados y viajes sin colectivo (anónimos, ~100 m) |
| POST · GET | `/api/v1/shares` `{vehicle_id}` · `/shares/{id}` | app pasajero: "Seguir mi viaje" — enlace de 3 h a `/seguir/{id}` con la posición del colectivo |
| GET | `/api/v1/places/search?q=` | app pasajero: buscar un lugar por nombre (Nominatim, con caché de un día) |
| GET | `/api/v1/admin/contributors/{id}` (admin) | admin: lo que aportó un pasajero anónimo (marcas, grabaciones por estado, líneas) |
| POST | `/api/v1/admin/contributors/{id}/block` `{purge?}` · `/unblock` (admin) | admin: bloquearlo (la API le responde 403); con `purge` rechaza sus pendientes, desactiva sus tramos aprobados, borra sus marcas y rechaza sus líneas sin revisar |
| WS | `/api/v1/vehicles/nearby/live` | app pasajero: cercanos en vivo; manda los parámetros de `GET /vehicles/nearby` y recibe cada cambio (mín. cada 10 s) |
| POST | `/api/v1/contributors` | app pasajero: registro anónimo (id + token), máx. 10 por IP y hora |
| GET | `/api/v1/lines?q=&lat=&lon=&radius_m=300` | app pasajero: buscar líneas activas por código, letrero o calle (con lat/lon: las que pasan cerca) |
| GET | `/api/v1/lines/{code}` | app pasajero: una línea (letrero y calles) para la tarjeta del colectivo |
| GET | `/api/v1/frequencies?lat=&lon=&line_code=` | app pasajero: cada cuánto pasa cada línea cerca, según las marcas de pasajeros |
| POST | `/api/v1/contributions/sightings` `{line_code, lat, lon, accuracy_m?}` (aportante) | pasajero: "pasó un colectivo"; responde la frecuencia ya contando la marca |
| POST | `/api/v1/contributions/lines` `{name, description, commune}` (aportante) | pasajero: proponer una línea que falta (código `P00001`…) |
| POST | `/api/v1/contributions/recordings` `{line_code, direction?, comment?, sign_text?}` (aportante) | pasajero: empezar a grabar su viaje |
| POST | `/api/v1/contributions/recordings/{rid}/points` `{points: [...]}` (aportante) | pasajero: lote de puntos GPS (idempotente) |
| GET | `/api/v1/contributions/recordings` · `/active` (aportante) | pasajero: mis aportes / retomar |
| GET | `/api/v1/contributions/me` (aportante) | pasajero: "Tus aportes" (cuánto aportó y cuánto sirvió) |
| POST | `/api/v1/contributions/recordings/{rid}/finish` · `/cancel` (aportante) | pasajero: terminar o descartar |
| GET | `/api/v1/admin/lines?status_filter=` (admin) | admin: líneas propuestas por pasajeros |
| POST | `/api/v1/admin/lines/{code}/approve` · `/reject` (admin) | admin: aprobar (puede corregir letrero y comuna) o rechazar |
| PATCH | `/api/v1/admin/lines/{code}` `{name?, commune?}` (admin) | admin: editar el letrero y la comuna de cualquier línea |
| GET | `/admin` | página web: recorridos grabados, líneas propuestas y vehículos (registrar y entregar el QR del conductor) |

## Grabación de recorridos (líneas sin trazado o recorridos que cambiaron)

1. **Conductor** (app, con el turno iniciado): "Grabar ida" / "Grabar regreso" → maneja su
   recorrido → "Terminar". La grabación es una *ventana de tiempo* sobre los pings que ya envía:
   no hay un canal de datos aparte (`location_updates` entre `started_at` y `finished_at`).
2. **Servidor** (`services/route_inference.py`) arma el trazado propuesto: descarta picos de GPS
   (imposibles respecto a ambos vecinos), puntos con el auto detenido, suaviza (promedio móvil
   de 3–5 puntos) y simplifica (Douglas-Peucker 8 m). Guarda métricas y advertencias (pocos
   puntos, recorrido corto, tramos sin señal). Con pings simulados densos y ruido de 5 m: largo
   a 2 % del real y desviación máxima de 7 m respecto a la calle.
3. **Administrador** en `http://<servidor>/admin`: ve los puntos crudos, el trazado propuesto, el
   oficial (si existe) y lo ya grabado sobre el mapa, y aprueba o rechaza. Al aprobar, el tramo
   **se suma** a lo grabado de esa línea y sentido: solo se agrega como `Route`
   (`source="recorded"`) la parte que aún no estaba cubierta (a más de 25 m de lo aprobado; restos
   de menos de 50 m se descartan). Así varias grabaciones parciales arman el recorrido completo.
   Con `replace_previous` (casilla "Reemplazar" en el panel) se desactiva lo grabado antes, para
   corregir un trazado malo. Se recarga la validación de desvíos de inmediato.

**Ajuste a las calles** (`services/map_matching.py`): al terminar la grabación, en segundo plano,
el trazado se pega a la red vial con OSRM (datos de OpenStreetMap). Usa el servidor de
demostración (`MAP_MATCHING_URL`, uso no comercial, 1 consulta por segundo y ~10 puntos por
consulta): se muestrea un punto cada ≥ 50 m y se ajusta por tramos (≈ 15 s). Si algún tramo
falla, el largo cambia más de 25 % o se aleja más de 60 m, queda el trazado suavizado. En una
prueba con ruido GPS de 8 m, la distancia media al trazado oficial bajó de 8 a 3 m. `/admin`
muestra si se ajustó y por qué no; el administrador igual valida visualmente.

### Aportes de pasajeros

El pasajero también puede grabar el recorrido del colectivo en que va ("Grabar mi viaje" en la app):

- **Identidad anónima** (`contributors`): la app pide un id + token la primera vez (header
  `X-Contributor-Id` / `X-Contributor-Token`). No se guardan datos personales; `is_blocked`
  permite bloquear a quien envíe basura.
- **Catálogo de líneas** (`lines`): se llenó en la migración con las líneas de los trazados
  importados (`official`) y las de vehículos/grabaciones (`admin`). Registrar un vehículo o
  importar trazados agrega la línea si falta. Si el colectivo del pasajero no aparece, lo
  propone (`contributed`, código `P00001`…, estado `pending_review`; máx. 3 pendientes por
  pasajero) y puede grabarlo de inmediato.
- **Puntos**: el pasajero no envía pings de vehículo; la app sube lotes a `recording_points`
  cada ~20 s (reenviar un lote no duplica). Se ignoran puntos fuera del área de servicio o de
  la ventana de la grabación, y al armar el trazado se descartan los de precisión > 50 m.
- **Revisión**: misma bandeja de `/admin`, marcada "Aporte de pasajero". El pasajero puede no
  saber si va de ida o de regreso: el administrador elige el sentido al aprobar. Aprobar un
  recorrido de una línea propuesta aprueba también la línea; rechazar una línea rechaza sus
  grabaciones pendientes.

Probado de punta a punta (31 verificaciones): búsqueda por ubicación, línea propuesta, puntos en
lotes con un fix malo, aprobación con sentido, línea visible con su trazado, rechazo y freno anti-spam.

### Frecuencia de paso ("pasó un colectivo")

Mientras espera, el pasajero marca cada colectivo de su línea que pasa (`sightings`: línea, lugar
y hora del servidor). Con las marcas a menos de 400 m de un lugar (últimas 4 semanas) se estima
cada cuánto pasa cada línea (`app/services/frequency.py`):

1. Marcas a menos de 90 s entre sí son el mismo auto (dos personas, o un doble toque).
2. Se mide el tiempo entre pasadas seguidas del mismo día; los huecos de más de 45 min no cuentan
   (nadie estaba mirando).
3. La cifra es la mediana: primero con la misma franja (día hábil o fin de semana, ±1 h de la hora
   actual) y, si faltan datos, con todas las horas. Se necesitan al menos 3 intervalos.

Frenos: una marca por línea por minuto y 60 por hora por pasajero; precisión GPS de hasta 150 m.
Es una estimación: si los pasajeros no marcan todos los autos, el intervalo sale más largo.
