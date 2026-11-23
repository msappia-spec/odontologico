"""Modelos ORM del módulo de presupuestos odontológicos.

Un presupuesto es la cotización que la secretaría entrega antes de comenzar un
tratamiento. Reproduce el documento de papel del consultorio:

* **cabecera**: paciente, fecha, dirección y forma de pago;
* **detalle de los trabajos**: cada práctica con su código, su descripción
  (que puede ocupar varias líneas) y su precio; si corresponde, también la
  entrega que el paciente abona al aceptar el presupuesto.

Decisiones de diseño:

* Es un registro **comercial**, no clínico: se puede corregir mientras el
  paciente no lo haya aceptado. Por eso el ``PUT`` reemplaza la cabecera y los
  renglones completos, igual que la ficha médica.
* Los renglones viven en ``presupuesto_items`` con un ``orden`` explícito para
  conservar exactamente la secuencia en que se cargaron y se imprimen.
* ``codigo`` guarda una **copia** del código de nomenclador de la prestación.
  No es una clave foránea: el presupuesto conserva el código con el que se
  cotizó aunque después se modifique o se dé de baja la práctica del catálogo.
* ``direccion`` es una **foto** del domicilio que se imprime. Si queda vacía, el
  servicio usa el domicilio actual del paciente; así un presupuesto viejo no
  cambia si después se actualiza la ficha del paciente.
* ``precio`` y ``entrega`` son ``Float`` (misma convención del Módulo 3) y se
  redondean a dos decimales en la capa de servicio. ``entrega`` es opcional:
  ``None`` significa que ese trabajo no tiene un anticipo informado.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.paciente import Paciente

#: Longitud máxima del código de prestación copiado al presupuesto.
MAX_CODIGO_PRESUPUESTO = 30

#: Longitud máxima del detalle de un trabajo del presupuesto. Es más amplia que
#: la descripción del catálogo porque el renglón admite varios detalles.
MAX_DETALLE_PRESUPUESTO = 1000

#: Tope de renglones de un presupuesto (el papel admite muchos menos).
MAX_ITEMS_PRESUPUESTO = 50

#: Tope de un importe para frenar errores de tipeo enormes.
MAX_IMPORTE_PRESUPUESTO = 99_999_999.0

#: Forma de pago que aparece por defecto en el documento.
FORMA_PAGO_POR_DEFECTO = "Contado / transferencia"


class Presupuesto(Base):
    """Cabecera de un presupuesto y sus renglones de trabajo."""

    __tablename__ = "presupuestos"
    __table_args__ = (
        # Consulta habitual del panel: los presupuestos de un paciente, del más
        # nuevo al más viejo.
        Index("ix_presupuesto_paciente_fecha", "paciente_id", "fecha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    paciente_id: Mapped[int] = mapped_column(
        ForeignKey("pacientes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Fecha del presupuesto. El PDF muestra sólo el mes y el año.
    fecha: Mapped[date] = mapped_column(
        Date, nullable=False, default=date.today, server_default=func.current_date()
    )
    #: Forma de pago general del documento (contado, transferencia, etc.).
    forma_pago: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default=FORMA_PAGO_POR_DEFECTO,
        server_default=FORMA_PAGO_POR_DEFECTO,
    )
    #: Dirección que se imprime en la cabecera. ``None`` = usar la del paciente.
    direccion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    creado_en: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    # Relaciones
    items: Mapped[List["PresupuestoItem"]] = relationship(
        back_populates="presupuesto",
        cascade="all, delete-orphan",
        order_by="(PresupuestoItem.orden, PresupuestoItem.id)",
    )
    # ``lazy="joined"``: el nombre del paciente viaja en la misma consulta y la
    # API puede exponerlo sin un lazy load posterior.
    paciente: Mapped["Paciente"] = relationship(
        back_populates="presupuestos",
        lazy="joined",
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Presupuesto id={self.id} paciente_id={self.paciente_id} "
            f"fecha={self.fecha} items={len(self.items)}>"
        )


class PresupuestoItem(Base):
    """Renglón «Detalle de los trabajos» de un presupuesto."""

    __tablename__ = "presupuesto_items"
    __table_args__ = (
        Index("ix_presupuesto_item_presupuesto", "presupuesto_id", "orden"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    presupuesto_id: Mapped[int] = mapped_column(
        ForeignKey("presupuestos.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Posición dentro del documento (0 = primer renglón).
    orden: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Código de nomenclador de la prestación (se copia como texto para que el
    #: presupuesto conserva el código aunque el catálogo cambie después).
    codigo: Mapped[Optional[str]] = mapped_column(
        String(MAX_CODIGO_PRESUPUESTO), nullable=True
    )
    #: Texto que se imprime en la columna «Detalle de los trabajos». Admite
    #: saltos de línea para agregar aclaraciones debajo de la prestación.
    detalle: Mapped[str] = mapped_column(Text, nullable=False)
    #: Precio del trabajo en pesos.
    precio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Anticipo/entrega informado para ese trabajo (opcional).
    entrega: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    presupuesto: Mapped["Presupuesto"] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<PresupuestoItem id={self.id} orden={self.orden} "
            f"codigo={self.codigo!r} precio={self.precio} "
            f"detalle={self.detalle!r}>"
        )
