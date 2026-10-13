"""Configuración centralizada de logging para toda la aplicación."""

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configurar_logging(level: int = logging.INFO) -> None:
    """Configura el logger raíz una única vez, con salida a stdout."""
    root = logging.getLogger()
    if root.handlers:
        # Ya configurado (por ejemplo por uvicorn): sólo ajustamos el nivel.
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
