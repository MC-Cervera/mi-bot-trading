"""Respaldo diario de la base de datos (copia consistente con la API de backup de SQLite)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

MANTENER = 14


def respaldar(ruta_db: Path, carpeta: Path | None = None, ahora: pd.Timestamp | None = None) -> Path:
    carpeta = carpeta or ruta_db.parent / "respaldos"
    carpeta.mkdir(parents=True, exist_ok=True)
    ahora = ahora or pd.Timestamp.now(tz="UTC")
    destino = carpeta / f"{ruta_db.stem}_{ahora:%Y%m%d_%H%M}.db"
    with sqlite3.connect(ruta_db) as origen, sqlite3.connect(destino) as copia:
        origen.backup(copia)
    for viejo in sorted(carpeta.glob(f"{ruta_db.stem}_*.db"))[:-MANTENER]:
        viejo.unlink()
    return destino
