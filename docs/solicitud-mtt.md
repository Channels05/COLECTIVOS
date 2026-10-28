# Solicitud al MTT por Ley de Transparencia (Ley 20.285)

Los trazados que usa la app son del geoportal del GORE RM (abril de 2023). El registro vigente de
los servicios de taxi colectivo lo lleva el Ministerio de Transportes (SEREMITT RM) y no está
publicado como datos abiertos, así que hay que pedirlo. Es gratis y el plazo legal de respuesta es
de 20 días hábiles (prorrogable por 10).

## Cómo enviarla

1. Entrar a https://www.portaltransparencia.cl → "Solicitar información" (con ClaveÚnica o correo).
2. Organismo: **Ministerio de Transportes y Telecomunicaciones** (Subsecretaría de Transportes).
3. Pegar el texto de abajo y elegir **entrega por correo electrónico, en formato digital**.

## Texto de la solicitud

> Junto con saludar, de acuerdo con la Ley N° 20.285 sobre Acceso a la Información Pública,
> solicito la siguiente información respecto de los servicios de taxi colectivo urbano de la
> Región Metropolitana inscritos en el Registro Nacional de Servicios de Transporte Público de
> Pasajeros:
>
> 1. Listado de servicios vigentes a la fecha, con su código o folio de inscripción, nombre o
>    denominación del servicio (texto del letrero), responsable del servicio y comunas que recorre.
> 2. El trazado autorizado de cada servicio (ida y regreso, y sus variantes o vías alternativas
>    si las hubiere), idealmente en formato geográfico digital (shapefile, KML o GeoJSON); en
>    subsidio, la descripción de calles del recorrido tal como figura en la resolución que lo
>    autoriza.
> 3. Horarios de funcionamiento autorizados de cada servicio, incluidos los servicios nocturnos.
> 4. Fecha de la última modificación del trazado de cada servicio.
>
> Si parte de la información no existe en formato digital, solicito que se entregue la que exista
> en ese formato y se indique cuál no. La información será usada para un proyecto sin fines de
> lucro que muestra a los pasajeros los recorridos de los colectivos.
>
> Agradezco de antemano.

## Cuando llegue la respuesta

- Si viene en shapefile/KML: convertir a GeoJSON y cargarla con `scripts/import_gore_routes.py`
  (mismo formato `nombre` = `<código>_I` / `<código>_R`) o con un importador nuevo si los campos
  cambian. Los recorridos grabados por pasajeros se mantienen.
- El letrero de cada servicio sirve para completar `lines.name` (hoy lo aportan los pasajeros).
- Volver a correr `python -m scripts.validate_lines` y `python -m scripts.build_line_streets`.
