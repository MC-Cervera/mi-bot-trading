"""Descarga/actualiza el histórico de velas de todos los pares configurados.

Uso:  python scripts/descargar_historico.py [--dias 730] [--pares BTC/USDT ETH/USDT]
No necesita claves: usa solo datos públicos del exchange.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_config  # noqa: E402
from bot.datos.exchange import crear_cliente_datos, validar_pares  # noqa: E402
from bot.datos.historico import actualizar_historico  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402

log = logging.getLogger("descargar_historico")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, help="días hacia atrás si el par no tiene histórico")
    ap.add_argument("--pares", nargs="*", help="subconjunto de pares (por defecto todos los de config.yaml)")
    args = ap.parse_args()

    config = cargar_config()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))
    cliente = crear_cliente_datos(config)

    pares = args.pares or config.pares
    validos, invalidos = validar_pares(cliente, pares, config.exchange.tipo_mercado)
    for p in invalidos:
        log.error("%s no está disponible en %s (%s); se omite", p, config.exchange.nombre, config.exchange.tipo_mercado)

    total = 0
    for par in validos:
        r = actualizar_historico(
            sesion, cliente, config.exchange.nombre, par, config.temporalidad,
            config.exchange.tipo_mercado, args.dias or config.historico.dias,
        )
        total += r.nuevas
        print(f"{par:<10} +{r.nuevas:>6} velas   huecos: {len(r.huecos)}")
    print(f"\nTotal: {total} velas nuevas en {len(validos)} pares. Omitidos: {invalidos or 'ninguno'}")
    return 1 if invalidos else 0


if __name__ == "__main__":
    sys.exit(main())
