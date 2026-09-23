"""BOTÓN DE EMERGENCIA: cierra todas las posiciones de todas las carteras y detiene el bot.

Uso:  python scripts/emergencia.py          (pide confirmación)
      python scripts/emergencia.py --si     (sin confirmación)
El bot no volverá a arrancar hasta ejecutar scripts/reactivar.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.emergencia import activar_emergencia  # noqa: E402


def main() -> int:
    if "--si" not in sys.argv and input("¿Cerrar TODO y detener el bot? Escribe SI: ").strip().upper() != "SI":
        print("Cancelado.")
        return 1
    r = activar_emergencia("Botón de emergencia (terminal)")
    if r["error"]:
        print(f"[ERROR] {r['error']}")
    for cartera, quedan in r["pendientes"].items():
        print(f"{cartera}: detenida. Posiciones sin cerrar: {quedan or 'ninguna'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
