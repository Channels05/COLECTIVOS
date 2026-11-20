from pydantic import BaseModel


class MyContributions(BaseModel):
    """Resumen de lo que aportó un pasajero y cuánto sirvió a la comunidad."""

    sightings: int
    sighting_lines: int
    recordings_approved: int
    recordings_pending: int
    lines_approved: int
    reports_sent: int
    reports_applied: int
