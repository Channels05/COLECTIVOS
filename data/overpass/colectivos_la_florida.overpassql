/*
  Pegar en https://overpass-turbo.eu  ->  Ejecutar  ->  Exportar  ->  GeoJSON
  Guardar como data/overpass/colectivos_la_florida.geojson

  (o sin pasar por la web: python -m scripts.fetch_overpass_routes <esta_query> <salida.geojson>)

  El etiquetado estándar de OSM para colectivos es route=share_taxi.
  Se busca la comuna dentro de la Región Metropolitana para no confundirla
  con otras "La Florida" del país.

  ATENCIÓN (oct-2026): hoy esta consulta devuelve 0 rutas: OSM no tiene recorridos de
  colectivos mapeados en Santiago (solo buses RED y Metro). Los trazados hay que
  conseguirlos de la fuente oficial o mapearlos en OSM. Para desarrollo usar
  ejemplo_bus_e13.overpassql.
*/
[out:json][timeout:90];

area["name"="Región Metropolitana de Santiago"]["admin_level"="4"]->.rm;
rel(area.rm)["boundary"="administrative"]["admin_level"="8"]["name"="La Florida"];
map_to_area->.comuna;

(
  relation["type"="route"]["route"="share_taxi"](area.comuna);
);
out geom;
