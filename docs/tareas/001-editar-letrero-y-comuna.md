# 001 · Editar el letrero y la comuna de cualquier línea desde /admin

Estado: lista

## Por qué

El registro oficial no trae lo que dice el letrero de cada colectivo (por eso las líneas se ven
como "Por Av. Santa Rosa, Tomé y Fresia"). Hoy el administrador solo puede escribir el letrero al
**aprobar una línea propuesta** por un pasajero. Necesita poder corregir el letrero (`name`) y la
comuna (`commune`) de **cualquier** línea: oficiales y ya aprobadas.

## Qué hacer

### 1. Servidor: `PATCH /api/v1/admin/lines/{code}`

Archivo: `app/api/v1/endpoints/contributions.py` (en el `admin_router` que ya existe, prefijo
`/admin/lines`).

- Cuerpo (JSON), esquema nuevo `LineEdit` en `app/schemas/line.py`:
  - `name: str | None` (máx. 120) — lo que dice el letrero.
  - `commune: str | None` (máx. 60).
- Solo se cambian los campos **que vienen en el cuerpo** (usa `body.model_fields_set`): si el
  cuerpo trae solo `name`, la comuna no se toca.
- Un texto vacío o solo espacios significa "borrar" (queda `None`). Quitar espacios al inicio y al
  final.
- Línea que no existe → 404 con el mensaje `"Línea no encontrada."`.
- Responde la línea actualizada con `LineRead` (como `approve_line`).
- **No** cambies `description`: la llena un script al arrancar el servidor y sobrescribiría lo que
  escriba el administrador.

### 2. Panel: editar desde "Líneas propuestas"

Archivo: `app/admin_ui/index.html`.

- En `selectLine(l)`, cuando la línea **no** está pendiente (`l.status !== "pending_review"`),
  muestra dos campos ("Letrero / nombre" y "Comuna") con los valores actuales y un botón
  "Guardar cambios" que llama a `PATCH /admin/lines/{code}` con ambos campos. Al guardar: cerrar
  el panel y recargar la lista; si falla, mostrar el error en el panel (como hacen los otros
  modos).
- Las líneas oficiales no aparecen en las pestañas de esa lista (solo las propuestas). Agrega
  arriba de la lista, solo en la pestaña "Aprobadas", un ítem **"Buscar línea por código"** que
  pida el código (`prompt`) y abra su panel. Para obtener la línea usa el endpoint público
  `GET /api/v1/lines/{code}`. Ojo: como la lista usa `/admin/...`, esa ruta pública se llama con
  `api("/lines/" + encodeURIComponent(codigo))`. Si no existe, mostrar el error en la lista.

### 3. README

Agrega la fila del endpoint nuevo en la tabla de `README.md`, junto a las otras de
`/api/v1/admin/lines`.

### 4. Test

Archivo nuevo `tests/test_line_edit.py`, sin base de datos (como `tests/test_line_info_csv.py`),
que pruebe el esquema `LineEdit`:

- `LineEdit(name="  Metro Bellavista  ")` → `name == "Metro Bellavista"`.
- `LineEdit(name="")` → `name is None` y `"name" in model_fields_set`.
- `LineEdit(commune="x" * 61)` → error de validación.
- `LineEdit()` → `model_fields_set` vacío.

(La limpieza de espacios y el vacío → `None` van en un `field_validator` del esquema, para que el
test la cubra.)

## No tocar

- Ningún otro endpoint, modelo ni migración (no hacen falta columnas nuevas).
- `scripts/describe_lines.py`.

## Cómo se comprueba

- `.venv/Scripts/python.exe -m pytest -q` → todo pasa (hoy son 65 tests, más los nuevos).
- Claude lo prueba contra la API local: editar una línea oficial y una aprobada, borrar la comuna
  con un texto vacío, código inexistente → 404, sin clave de admin → 403/422.

## Resultado (lo completa Copilot)

### Resultado

- Archivos cambiados:
  - `app/schemas/line.py`: se agregó `LineEdit` con limpieza de espacios y borrado de textos vacíos.
  - `app/api/v1/endpoints/contributions.py`: se agregó `PATCH /api/v1/admin/lines/{code}`.
  - `app/admin_ui/index.html`: se agregó la edición de letrero y comuna y la búsqueda por código en Líneas aprobadas.
  - `README.md`: se agregó el endpoint nuevo a la tabla de API.
  - `tests/test_line_edit.py`: se agregaron las cuatro comprobaciones solicitadas.
  - `docs/tareas/001-editar-letrero-y-comuna.md`: se completó esta sección.
- Comandos corridos:
  - `.venv/Scripts/python.exe -m pytest -q tests/test_line_edit.py`
  - `.venv/Scripts/python.exe -m pytest -q`
- Salida:
  - Test específico: `4 passed`.
  - Suite completa: `69 passed, 1 warning` (único aviso: deprecación de Starlette/FastAPI en `testclient`).
- Dudas: ninguna.

### Resultado 2

- Archivos cambiados:
  - `app/schemas/line.py`: `limpiar_texto` devuelve sin modificar cualquier valor que no sea texto, para que Pydantic responda 422.
  - `app/api/v1/endpoints/contributions.py`: se agregó el docstring de `edit_line` y se dividió el import largo del esquema.
  - `tests/test_line_edit.py`: se agregó la comprobación de que `{"name": 123}` produce `ValidationError`.
- Comandos corridos:
  - `.venv/Scripts/python.exe -m pytest -q`
- Salida:
  - Suite completa: `70 passed, 1 warning` (único aviso: deprecación de Starlette/FastAPI en `testclient`).
- Dudas: ninguna.

## Revisión (la completa Claude)

### Revisión 1 — cambios pedidos

Bien hecho en general. Probado contra la API local: editar una línea oficial (233035) y una
aprobada (P00017), borrar la comuna con texto vacío sin tocar el letrero, 404 con código
inexistente, 403 sin clave de admin, y en el panel "Buscar línea por código" abre la línea con sus
campos. Tests: 69 pasan.

Falta corregir:

1. **Error 500 con un valor que no es texto.** `PATCH /api/v1/admin/lines/233035` con
   `{"name": 123}` responde **500** (en el validador, `value.strip()` falla con `AttributeError`).
   Tiene que responder **422** como cualquier dato inválido. En `limpiar_texto` (de
   `app/schemas/line.py`), si `value` no es `str`, devuélvelo tal cual para que Pydantic lo
   rechace. Agrega en `tests/test_line_edit.py`:
   `LineEdit.model_validate({"name": 123})` → `ValidationError`.
2. **Docstring del endpoint.** `edit_line` no tiene docstring; agrega uno corto en español, como
   los otros endpoints del archivo (por ejemplo: corrige el letrero y la comuna de cualquier
   línea; solo cambia los campos que vienen en el cuerpo).
3. **Línea larga.** El `from app.schemas.line import ...` de `contributions.py` quedó de 121
   caracteres: pártelo en varias líneas entre paréntesis.

Después: `.venv/Scripts/python.exe -m pytest -q` y agrega abajo un "Resultado 2".

### Revisión 2 — aprobada

Los tres puntos quedaron bien. Tests: 70 pasan. Contra la API local: `{"name": 123}` y
`{"commune": ["x"]}` → 422; texto con espacios se guarda limpio; texto vacío borra; cuerpo vacío no
cambia nada. Subido a `main` (Render lo despliega cuando pasan los tests de GitHub).
