"""Modelo ORM ``ArancelObraSocial``: qué cubre cada cobertura por práctica.

Es la tabla que hace posible la **facturación mixta**: para un par
(obra social, prestación) guarda cuánto paga la cobertura (``monto_cubre_os``) y
cuánto le queda al paciente como **coseguro** a cobrar en el mostrador
(``monto_coseguro_paciente``). Al cerrar una visita, el servicio de facturación
resuelve cada práctica contra esta configuración y arma el movimiento de caja
con los dos importes.

Notas de diseño:

* Índice único nombrado ``uq_arancel_os_prestacion`` sobre
  ``(obra_social_id, prestacion_id)``: una cobertura no puede tener dos aranceles
  para la misma práctica (la segunda fila confundiría el cálculo). Se usa un
  índice nombrado —y no una ``UniqueConstraint``— para poder replicarlo en bases
  existentes con ``CREATE UNIQUE INDEX``.
* ``ondelete="CASCADE"`` en ambas claves foráneas: el arancel es *configuración*
  de un par; si se borra la cobertura o la práctica, la fila no tiene sentido.
* Los importes son ``Float`` y se redondean a dos decimales al facturar
  (convención del Módulo 3).
"""

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.obra_social import ObraSocial
    from app.models.prestacion import Prestacion


class ArancelObraSocial(Base):
    """Cobertura y coseguro de una práctica para una obra social concreta."""

    __tablename__ = "aranceles_obra_social"
    __table_args__ = (
        Index(
            "uq_arancel_os_prestacion",
            "obra_social_id",
            "prestacion_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    obra_social_id: Mapped[int] = mapped_column(
        ForeignKey("obras_sociales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prestacion_id: Mapped[int] = mapped_column(
        ForeignKey("prestaciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    monto_cubre_os: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    monto_coseguro_paciente: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    # Relaciones
    # ``lazy="joined"``: el nombre de la cobertura y la práctica viajan en la
    # misma consulta, así la API los expone sin depender de un *lazy load*
    # (mismo criterio que ``Paciente.obra_social``).
    obra_social: Mapped["ObraSocial"] = relationship(lazy="joined")
    prestacion: Mapped["Prestacion"] = relationship(
        back_populates="aranceles", lazy="joined"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<ArancelObraSocial id={self.id} obra_social_id={self.obra_social_id} "
            f"prestacion_id={self.prestacion_id} cubre_os={self.monto_cubre_os} "
            f"coseguro={self.monto_coseguro_paciente}>"
        )
