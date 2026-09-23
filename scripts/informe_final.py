"""Informe comparativo del paper trading: técnico solo vs. técnico + Claude vs. comprar y mantener.

Uso:  python scripts/informe_final.py      → reportes/informe_comparativo_FECHA.md
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.arranque import construir  # noqa: E402
from bot.config import RAIZ  # noqa: E402
from bot.informe_final import generar  # noqa: E402


def main() -> int:
    config, s, _ = construir(con_mercado=False)
    texto = generar(s, config)
    ruta = RAIZ / "reportes" / f"informe_comparativo_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M}.md"
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"Guardado en {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
