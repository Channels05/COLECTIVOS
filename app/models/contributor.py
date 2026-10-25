import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Contributor(Base):
    """Pasajero que aporta recorridos o líneas nuevas. Anónimo: solo un id y un token por teléfono.

    No guarda nombre, correo ni teléfono. El token sirve para retomar sus grabaciones y para que
    un administrador pueda bloquear a quien envíe basura (`is_blocked`).
    """

    __tablename__ = "contributors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
