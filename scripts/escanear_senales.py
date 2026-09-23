"""Recorre el histórico guardado y muestra cuántas señales genera la estrategia en cada par.

Uso:  python scripts/escanear_senales.py [--pares BTC/USDT ...] [--guardar] [--ultimas 5]
Solo describe las señales; su rentabilidad se mide en la Fase 3 (backtesting).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_config  # noqa: E402
from bot.datos.historico import cargar_velas  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402
from bot.senales import CORTO, LARGO, ParametrosSenal, extraer_candidatas, generar_senales, guardar_senales  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pares", nargs="*")
    ap.add_argument("--guardar", action="store_true", help="guarda los cruces en la tabla senales")
    ap.add_argument("--ultimas", type=int, default=0, help="explica las N últimas señales de cada par")
    args = ap.parse_args()

    config = cargar_config()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))
    p = ParametrosSenal.desde_config(config)

    print(f"{'Par':<10}{'Velas':>7}{'Cruces':>8}{'Largos':>8}{'Cortos':>8}  Descartes: volumen / RSI / horario")
    for par in args.pares or config.pares:
        velas = cargar_velas(sesion, config.exchange.nombre, par, config.temporalidad)
        if velas.empty:
            print(f"{par:<10} sin histórico (ejecuta scripts/descargar_historico.py)")
            continue
        df = generar_senales(velas, p, config.temporalidad)
        cruces = df[df["cruce"] != 0]
        desc = cruces[cruces["senal"] == 0]
        print(
            f"{par:<10}{len(df):>7}{len(cruces):>8}{(df['senal'] == LARGO).sum():>8}{(df['senal'] == CORTO).sum():>8}  "
            f"{(~desc['conf_volumen']).sum()} / {(~desc['conf_rsi'].astype(bool)).sum()} / {(~desc['en_horario']).sum()}"
        )
        if args.guardar:
            guardar_senales(sesion, df, config.exchange.nombre, par, p, config.temporalidad)
        for c in extraer_candidatas(df, par, p, config.temporalidad)[-args.ultimas:] if args.ultimas else []:
            print("   · " + c.explicar())
    return 0


if __name__ == "__main__":
    sys.exit(main())
