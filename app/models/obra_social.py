"""Modelo ORM ``ObraSocial``: coberturas médicas asignables a los pacientes.

La tabla es el catálogo que alimenta el desplegable *Obra social* del formulario
de pacientes (``app/static/index.html``): en lugar de escribir el nombre a mano,
la secretaría elige una cobertura registrada.

Notas de diseño:

* ``activa`` implementa el **borrado lógico** del catálogo: la baja
  (``DELETE /api/v1/obras-sociales/{id}``) marca ``activa = False`` y la
  cobertura deja de ofrecerse en los desplegables, pero el registro se conserva
  porque los pacientes que la tenían siguen apuntándole (su historia clínica no
  se altera).
* ``nombre`` es único mediante el índice nombrado ``uq_obra_social_nombre`` (y
  no una ``UniqueConstraint``) para poder replicarlo en bases existentes con
  ``CREATE UNIQUE INDEX`` sin reconstruir la tabla, igual que
  ``uq_paciente_dni`` (ver ``scripts/migrar_obra_social.py``).
* ``codigo`` es opcional: sirve para identificar la cobertura ante el
  financiador (p. ej. un código interno del consultorio).
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Index, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.paciente import Paciente


class ObraSocial(Base):
    """Cobertura médica (obra social o prepaga) del consultorio."""

    __tablename__ = "obras_sociales"
    __table_args__ = (
        Index("uq_obra_social_nombre", "nombre", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    codigo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    activa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # Relaciones
    pacientes: Mapped[List["Paciente"]] = relationship(
        back_populates="obra_social",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<ObraSocial id={self.id} nombre={self.nombre!r} "
            f"codigo={self.codigo!r} activa={self.activa}>"
        )
