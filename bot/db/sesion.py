"""Creación del motor SQLite y de sesiones."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from bot.db.modelos import Base

# SQLite limita cuántos valores caben en una consulta: 32 766 en el SQLite que trae Python en Windows
# (999 en versiones muy antiguas). Las inserciones masivas se parten en bloques que quepan con holgura.
MAX_VARIABLES_SQLITE = 990  # seguro incluso con SQLite antiguos (límite 999)


def filas_por_bloque(columnas: int) -> int:
    return max(1, min(1000, MAX_VARIABLES_SQLITE // max(columnas, 1)))


def crear_motor(ruta_db: Path | str) -> Engine:
    """`ruta_db` puede ser una ruta de archivo o ':memory:' (pruebas)."""
    if str(ruta_db) != ":memory:":
        Path(ruta_db).parent.mkdir(parents=True, exist_ok=True)
    motor = create_engine(f"sqlite:///{ruta_db}", future=True)

    @event.listens_for(motor, "connect")
    def _pragmas(conexion, _):
        cur = conexion.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # permite leer desde el panel mientras el bot escribe
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(motor)
    return motor


def crear_sesion(motor: Engine) -> Session:
    return sessionmaker(bind=motor, expire_on_commit=False)()
