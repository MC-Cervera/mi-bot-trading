"""Arranca el bot en segundo plano si no está ya en marcha (lo usa INICIAR.bat).

Uso:  python scripts/arrancar_bot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import proceso  # noqa: E402
from bot.arranque import ARCHIVO_DETENER  # noqa: E402


def main() -> int:
    if ARCHIVO_DETENER.exists():
        print("El bot está DETENIDO por el botón de emergencia: no se inicia. Revisa qué pasó y reactívalo con\n"
              "  python scripts/reactivar.py")
        return 3
    e = proceso.estado()
    if e.en_marcha:
        print(f"El bot ya estaba en marcha (PID {e.pid}).")
        return 0
    pid = proceso.iniciar()
    print(f"Bot iniciado en segundo plano (PID {pid}). Sigue funcionando aunque cierres el panel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
