"""BOTÓN DE EMERGENCIA: cierra todas las posiciones de todas las carteras y detiene el bot.

Uso:  python scripts/emergencia.py          (pide confirmación)
      python scripts/emergencia.py --si     (sin confirmación)
El bot no volverá a arrancar hasta ejecutar scripts/reactivar.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.arranque import ARCHIVO_DETENER, construir  # noqa: E402


def main() -> int:
    if "--si" not in sys.argv and input("¿Cerrar TODO y detener el bot? Escribe SI: ").strip().upper() != "SI":
        print("Cancelado.")
        return 1
    ARCHIVO_DETENER.parent.mkdir(exist_ok=True)
    ARCHIVO_DETENER.write_text(f"Emergencia activada {pd.Timestamp.now(tz='UTC')}\n", encoding="utf-8")
    _, _, ciclo = construir()
    ahora = pd.Timestamp.now(tz="UTC")
    try:
        precios = ciclo.mercado.precios()
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] No se pudieron obtener precios ({e}). El bot queda detenido; cierra manualmente si hace falta.")
        precios = {}
    for nombre, g in ciclo.carteras.items():
        g.emergencia(precios, ahora)
        quedan = g.abiertas()
        print(f"{nombre}: detenida. Posiciones sin cerrar: {[o.par for o in quedan] or 'ninguna'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
