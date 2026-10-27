from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClientError(Base):
    """Un error de JavaScript que la app reportó (se ven agrupados en /admin → Errores).

    Anónimo: no guarda quién lo tuvo, solo la versión de la app y el modelo de teléfono.
    """

    __tablename__ = "client_errors"
    __table_args__ = (Index("ix_client_errors_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Cuándo ocurrió en el teléfono (puede llegar después, si la app se cerró por el error).
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(String(500))
    stack: Mapped[str | None] = mapped_column(Text)
    fatal: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(40))  # global, pantalla, promesa…
    screen: Mapped[str | None] = mapped_column(String(100))
    app_version: Mapped[str | None] = mapped_column(String(20))
    update_id: Mapped[str | None] = mapped_column(String(64))  # actualización por aire en uso
    platform: Mapped[str | None] = mapped_column(String(20))
    os_version: Mapped[str | None] = mapped_column(String(20))
    device: Mapped[str | None] = mapped_column(String(80))
