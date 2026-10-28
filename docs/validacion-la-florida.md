# Validación de los trazados de La Florida

Generado por `python -m scripts.validate_lines "La Florida"`: trazados del GORE (abril de 2023) contra la red vial de OpenStreetMap (Santiago.osm.pbf). 38 líneas, 76 sentidos: **3 para revisar** y 73 sin observaciones.

Criterios: calce vial bajo 95% (puntos a más de 15 m de una calle), más de 200 m seguidos fuera de las calles, saltos de más de 150 m entre tramos, o falta un sentido.

## Para revisar

| Línea | Sentido | Largo | Calce vial | Observaciones |
|---|---|---|---|---|
| 231006 | regreso | 10.1 km | 85% | calce vial 85%; 600 m seguidos fuera de las calles |
| 234021 | regreso | 13.6 km | 89% | calce vial 89% |
| 234021 | ida | 14.8 km | 91% | calce vial 91% |

## Sin observaciones

232001, 233004, 233006, 233007, 233008, 233009, 233010, 233013, 233015, 233018, 233019, 233021, 233022, 233023, 233025, 233027, 233028, 233029, 233030, 233031, 233032, 233033, 233034, 233035, 233036, 234009, 234019, 234022, 234028, 234058, 235011, 235026, 235054, 235091, 239001, 239020
