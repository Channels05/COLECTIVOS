# Importar todos los modelos aquí para que Base.metadata los conozca (create_all / Alembic).
from app.models.contributor import Contributor
from app.models.line import Line, LineSource, LineStatus, contributed_line_seq
from app.models.location import LocationUpdate
from app.models.recording import RecordingPoint, RecordingStatus, RouteRecording
from app.models.route import Route, RouteDirection, RouteKind, RouteSource
from app.models.vehicle import Vehicle

__all__ = [
    "Contributor",
    "Line",
    "LineSource",
    "LineStatus",
    "LocationUpdate",
    "RecordingPoint",
    "RecordingStatus",
    "Route",
    "RouteDirection",
    "RouteKind",
    "RouteRecording",
    "RouteSource",
    "Vehicle",
    "contributed_line_seq",
]
