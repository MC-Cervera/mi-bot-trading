"""Registro detallado a archivo rotativo (UTF-8, compatible con Windows) y a consola."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class FormatoConsola(logging.Formatter):
    """En pantalla solo el mensaje; el detalle técnico (traceback) va únicamente al archivo de log."""

    def format(self, record: logging.LogRecord) -> str:
        copia = logging.makeLogRecord(record.__dict__)
        copia.exc_info = None
        copia.exc_text = None
        copia.stack_info = None
        return super().format(copia)


def configurar_logging(ruta_log: Path, nivel: int = logging.INFO) -> None:
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG)
    for h in list(raiz.handlers):
        raiz.removeHandler(h)

    archivo = RotatingFileHandler(ruta_log, maxBytes=10_000_000, backupCount=10, encoding="utf-8")
    archivo.setLevel(logging.DEBUG)
    archivo.setFormatter(logging.Formatter(FORMATO))

    consola = logging.StreamHandler()
    consola.setLevel(nivel)
    consola.setFormatter(FormatoConsola("%(levelname)s | %(message)s"))

    raiz.addHandler(archivo)
    raiz.addHandler(consola)
    # ccxt y urllib3 son muy verbosos en DEBUG
    for ruidoso in ("ccxt", "urllib3"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
