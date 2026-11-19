# 001 · Editar el letrero y la comuna de cualquier línea desde /admin

Estado: pendiente

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

_(archivos cambiados, comandos corridos y su salida, dudas)_

## Revisión (la completa Claude)

_(pendiente)_
