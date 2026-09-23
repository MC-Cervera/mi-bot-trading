"""Creación del motor SQLite y de sesiones."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from bot.db.modelos import Base


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
