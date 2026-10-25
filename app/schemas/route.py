from typing import Any, Literal

from pydantic import BaseModel


class RouteFeature(BaseModel):
    """Feature GeoJSON (RFC 7946) lista para dibujar con react-native-maps.

    properties: id, line_code, direction (ida|regreso), kind (trazado|via_alternativa|bucle|nocturno),
    name, source_date (fecha de corte del dato oficial, o null).
    """

    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class RouteFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[RouteFeature]
