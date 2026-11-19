# Instrucciones para Copilot — backend de Colectivos

API de una app de taxis colectivos de Santiago (Chile): los conductores comparten su ubicación y
cupos, los pasajeros ven los autos cercanos, graban recorridos y planifican viajes.

## Cómo trabajamos

- Las tareas vienen escritas en `docs/tareas/NNN-nombre.md`. Haz **solo** lo que pide la tarea, en
  los archivos que indica. Si algo no está claro o crees que hay que tocar otra cosa, dilo en vez
  de inventarlo.
- **No hagas commit, push ni merge.** Otra persona (Claude) revisa el cambio antes de subirlo.
- Al terminar, escribe al final de la tarea, en la sección "Resultado", qué archivos cambiaste,
  qué comandos corriste y su salida (tests), y cualquier duda.
- Si la tarea ya tiene una "Revisión", agrega un "Resultado N" nuevo como **última sección del
  archivo** (después de la última Revisión). Nunca escribas dentro de una Revisión.

## Stack

- Python 3.12+, FastAPI, SQLAlchemy 2 async (asyncpg), PostgreSQL + PostGIS, Redis, Alembic.
- Entorno virtual en `.venv`: en Windows, `.venv/Scripts/python.exe -m pytest -q`.
- Base y Redis locales con `docker compose up -d db redis`.

## Dónde va cada cosa

- Endpoints: `app/api/v1/endpoints/<tema>.py`, con routers `public_router`, `contributor_router`
  (`/contributions`, pasajero anónimo) o `admin_router` (`/admin`, con `Depends(require_admin)`).
  Todo router nuevo se registra en `app/api/v1/router.py`.
- Lógica de negocio que se reutiliza: `app/services/`. Modelos: `app/models/` (y exportarlos en
  `app/models/__init__.py`). Esquemas Pydantic v2: `app/schemas/` o junto al endpoint si son solo
  de ese archivo.
- Panel de administración: un solo archivo, `app/admin_ui/index.html`, JavaScript sin
  frameworks. Usa los ayudantes que ya existen: `api(path, options)` (pone la clave de admin) y
  `el(tag, props, ...children)`. Copia el estilo de los modos que ya hay (lista a la izquierda,
  panel con el detalle).
- Cambios de esquema: migración de Alembic en `alembic/versions/` con el formato de nombre
  `AAAA_MM_DD_HHMM-<revisión>_<descripción>.py`. `alembic check` tiene que quedar sin cambios.
- Cada endpoint nuevo se agrega a la tabla de endpoints de `README.md`.

## Estilo

- **Todo en español**: comentarios, docstrings, mensajes de error (`HTTPException`) y textos del
  panel. Español de Chile, claro y sin tecnicismos para el usuario final.
- Comentarios que explican el **porqué**, no lo obvio. Sigue la densidad de comentarios del
  archivo que estás tocando.
- Líneas de hasta 120 caracteres. Tipos en todas las funciones.
- Errores de negocio: `HTTPException` con el código correcto (404 no existe, 409 ya revisado o
  duplicado, 422 datos inválidos, 429 demasiadas solicitudes).

## Reglas

- Nunca escribas claves, contraseñas ni el contenido de `.env` en el código ni en los mensajes.
- No agregues dependencias nuevas a `requirements.txt` sin que la tarea lo pida.
- Datos de pasajeros: anónimos. No guardes IP ni nada que identifique a una persona.
- Antes de terminar: `.venv/Scripts/python.exe -m pytest -q` sin fallas y, si tocaste modelos,
  `.venv/Scripts/python.exe -m alembic check`.
