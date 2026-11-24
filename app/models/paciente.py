"""Modelo ORM ``Paciente``."""

from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.cuenta_corriente import CuentaCorriente
    from app.models.historia_clinica import HistoriaClinica
    from app.models.obra_social import ObraSocial
    from app.models.presupuesto import Presupuesto
    from app.models.turno import Turno


class Paciente(Base):
    """Persona que reserva turnos y recibe notificaciones por WhatsApp.

    Datos requeridos: ``nombre`` y ``telefono`` (el teléfono es la vía de aviso
    por WhatsApp). El resto de la información es **opcional**:

    * ``email``: correo de contacto.
    * ``dni``: documento del paciente, **único** mediante el índice
      ``uq_paciente_dni``. Al ser opcional, las filas sin documento quedan en
      ``NULL`` y un índice único admite tantos ``NULL`` como haga falta (ni
      SQLite ni PostgreSQL consideran duplicados dos ``NULL``); sí se rechaza
      repetir un documento informado.
    * ``obra_social_id`` / ``numero_afiliado``: cobertura médica del paciente.
      La obra social es una **clave foránea** al catálogo ``obras_sociales``
      (``ondelete="SET NULL"``: si algún día se borra físicamente una cobertura,
      el paciente queda sin obra social en lugar de perder la fila), mientras
      que el número de afiliado sigue siendo texto libre.
    * **Datos filiatorios** de la Historia Clínica General de papel
      (``Historia-Clinica-GeneralNUEVA.pdf``): ``fecha_nacimiento`` (la edad se
      calcula, nunca se guarda), ``estado_civil``, ``nacionalidad``,
      ``domicilio``, ``profesion``, ``titular`` de la obra social,
      ``lugar_trabajo`` y ``jerarquia``. Son la **única fuente de verdad**: la
      ficha médica (Módulo 4) los muestra, los carga y los sincroniza acá al
      guardar, de modo que la secretaría no vuelve a tipear los mismos datos.
    * ``categoria`` / ``categoria_observaciones``: calificación administrativa
      del paciente. ``categoria`` guarda el **código** de una fila del catálogo
      administrable ``categorias_paciente`` (por defecto ``SIN_CATEGORIZAR``;
      las categorías históricas son ``EXCELENTE`` y ``MALO``) y
      ``categoria_observaciones`` el motivo que la justifica. Una categoría con
      ``alerta`` (``MALO`` en el catálogo inicial) dispara un aviso con sus
      observaciones antes de asignarle un turno en el panel y en la agenda
      móvil. El catálogo se valida en el servicio: acá se guarda el código para
      no atar la fila a un ``id`` que la secretaría podría recrear.
    """

    __tablename__ = "pacientes"
    __table_args__ = (
        # Índice único *nombrado* (en lugar de una UniqueConstraint) para poder
        # replicarlo sobre bases existentes con ``CREATE UNIQUE INDEX`` sin
        # reconstruir la tabla (ver scripts/migrar_pacientes_datos.py).
        Index("uq_paciente_dni", "dni", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    dni: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    obra_social_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("obras_sociales.id", ondelete="SET NULL"), nullable=True, index=True
    )
    numero_afiliado: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Datos filiatorios de la Historia Clínica General. Todos opcionales: el
    # paciente puede completarlos desde el enlace de la ficha médica y la
    # secretaría desde el formulario de pacientes (los dos escriben acá).
    fecha_nacimiento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    estado_civil: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    nacionalidad: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    domicilio: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    profesion: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    titular: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    lugar_trabajo: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    jerarquia: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Categoría administrativa con la que la secretaría califica al paciente.
    # Guarda el código del catálogo ``categorias_paciente`` (texto, no un enum
    # fijo): la secretaría puede crear categorías nuevas desde el panel y las
    # que tienen ``alerta`` exigen revisar las observaciones antes de asignar un
    # turno. Las fichas históricas quedan en ``SIN_CATEGORIZAR`` gracias al
    # default del modelo y de la migración.
    categoria: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="SIN_CATEGORIZAR",
        server_default="SIN_CATEGORIZAR",
    )
    categoria_observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relaciones
    turnos: Mapped[List["Turno"]] = relationship(
        back_populates="paciente",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # ``lazy="joined"``: el nombre de la obra social viaja en la misma consulta,
    # de modo que la respuesta de la API puede exponerlo sin depender de un
    # *lazy load* posterior al cierre de la sesión.
    obra_social: Mapped[Optional["ObraSocial"]] = relationship(
        back_populates="pacientes",
        lazy="joined",
    )
    # Ficha clínica del paciente (Módulo 2): una sola por paciente. Se crea sola
    # cuando el odontólogo carga la primera evolución (ver
    # app.services.historia_clinica_service) y cuelga de acá su línea de tiempo.
    historia_clinica: Mapped[Optional["HistoriaClinica"]] = relationship(
        back_populates="paciente",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Cuenta corriente del paciente (Módulo 5). Es **una sola** por paciente y se
    # abre sola en el primer movimiento financiero (ver
    # app.services.finanzas_service). Sin ``cascade``: la contabilidad es un
    # registro transaccional con valor de comprobante y no se borra junto con el
    # paciente (la FK es ``ondelete="RESTRICT"``).
    cuenta_corriente: Mapped[Optional["CuentaCorriente"]] = relationship(
        back_populates="paciente",
        uselist=False,
        passive_deletes=True,
    )
    # Presupuestos entregados al paciente (documentos comerciales). La relación
    # inversa permite consultarlos y los borra junto con el paciente.
    presupuestos: Mapped[List["Presupuesto"]] = relationship(
        back_populates="paciente",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Paciente id={self.id} nombre={self.nombre!r} "
            f"telefono={self.telefono!r} dni={self.dni!r}>"
        )
