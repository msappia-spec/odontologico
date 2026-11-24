"""Modelo ORM ``CategoriaPaciente``: catálogo administrable de categorías.

La secretaría administra desde el panel las categorías con las que califica a un
paciente (por ejemplo «Sin categorizar», «Excelente» o «Malo»). Cada fila define:

* ``codigo``: identificador interno que se guarda en ``pacientes.categoria``. Es
  único y no distingue mayúsculas de minúsculas (se normaliza en mayúsculas).
* ``nombre``: texto que ve la secretaría en el desplegable y en las insignias.
* ``alerta``: si es ``true``, el panel pide observaciones y muestra un aviso
  antes de asignarle un turno al paciente (comportamiento histórico de ``MALO``).
* ``activa``: **borrado lógico**. Una categoría dada de baja no se ofrece en el
  formulario, pero se conserva para no dejar huérfanos a los pacientes que ya la
  tenían.
* ``orden``: orden de aparición en el desplegable (empate: por nombre).

La tabla la crea ``create_all`` y ``init_db`` siembra las tres categorías
históricas cuando está vacía (ver ``app.services.categoria_paciente_service``).
Las bases anteriores a este catálogo conservan sus códigos en
``pacientes.categoria``: los tres históricos siguen siendo válidos.
"""

from sqlalchemy import Boolean, Index, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class CategoriaPaciente(Base):
    """Categoría administrativa configurable del paciente."""

    __tablename__ = "categorias_paciente"
    __table_args__ = (
        # Índice único *nombrado* (en lugar de una UniqueConstraint) para poder
        # replicarlo en una base existente sin reconstruir la tabla y para
        # traducir la carrera de dos altas simultáneas a un 409.
        Index("uq_categoria_paciente_codigo", "codigo", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    codigo: Mapped[str] = mapped_column(String(30), nullable=False)
    nombre: Mapped[str] = mapped_column(String(60), nullable=False)
    # ``MALO`` históricamente avisaba antes de dar un turno; ahora cualquier
    # categoría puede activar ese comportamiento desde el panel.
    alerta: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    activa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    orden: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<CategoriaPaciente id={self.id} codigo={self.codigo!r} "
            f"nombre={self.nombre!r} alerta={self.alerta} activa={self.activa}>"
        )
