"""Reactiva el bot tras una emergencia o una parada por caída máxima. Hazlo solo después de revisar qué pasó.

Uso:  python scripts/reactivar.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.arranque import ARCHIVO_DETENER, construir  # noqa: E402


def main() -> int:
    _, _, ciclo = construir(con_mercado=False)
    for nombre, g in ciclo.carteras.items():
        r = g.registro()
        print(f"{nombre}: {r.estado} — {r.motivo}")
    if input("¿Revisaste el motivo y quieres reactivar? Escribe REACTIVAR: ").strip() != "REACTIVAR":
        print("Cancelado.")
        return 1
    ahora = pd.Timestamp.now(tz="UTC")
    for g in ciclo.carteras.values():
        g.reactivar(ahora, quien=getpass.getuser())
    ARCHIVO_DETENER.unlink(missing_ok=True)
    print("Reactivado. Arranca de nuevo con: python scripts/bot.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
