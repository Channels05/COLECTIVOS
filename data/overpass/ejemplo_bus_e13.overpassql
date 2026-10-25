/*
  Trazado de EJEMPLO para desarrollo: recorrido de bus RED E13
  (Bellavista de La Florida <-> Gabriela), ida y regreso.

  Se usa porque OSM no tiene recorridos de colectivos (route=share_taxi) en Santiago
  (verificado oct-2026). Sirve para probar el flujo con calles reales de La Florida;
  NO es un trazado legal de colectivo.
*/
[out:json][timeout:60];
relation(id:6518264,6518267);
out geom;
