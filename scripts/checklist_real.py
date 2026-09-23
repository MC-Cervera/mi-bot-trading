"""Checklist antes de operar con dinero real (comprueba lo automático y lista lo manual).

Uso:  python scripts/checklist_real.py [--sin-pruebas]
Aunque todo salga OK, el modo real sigue bloqueado en el código hasta tu autorización explícita.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.arranque import construir  # noqa: E402
from bot.checklist import FALLA, evaluar, texto  # noqa: E402
from bot.config import cargar_secretos  # noqa: E402


def main() -> int:
    config, s, _ = construir(con_mercado=False)
    puntos = evaluar(s, config, cargar_secretos(), correr_pruebas="--sin-pruebas" not in sys.argv)
    print(texto(puntos))
    return 1 if any(p.estado == FALLA for p in puntos) else 0


if __name__ == "__main__":
    sys.exit(main())
