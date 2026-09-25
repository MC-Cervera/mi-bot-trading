"""Descarga/actualiza el histórico de velas de todos los pares configurados.

Uso:  python scripts/descargar_historico.py [--dias 730] [--pares BTC/USDT ETH/USDT] [--spot]
No necesita claves: usa solo datos públicos. Guarda por tramos: si se corta, vuelve a ejecutarlo y continúa.
--spot  usa los precios del mercado spot (casi idénticos a los de futuros) si los futuros no responden en tu región.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.config import cargar_config  # noqa: E402
from bot.datos.exchange import crear_cliente_datos, diagnosticar, validar_pares  # noqa: E402
from bot.datos.historico import actualizar_historico, ultimo_ts  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402

log = logging.getLogger("descargar_historico")


def conectar(config, tipo):
    cliente = crear_cliente_datos(config, tipo)
    validos, invalidos = validar_pares(cliente, config.pares, tipo)
    return cliente, validos, invalidos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, help="días hacia atrás si el par no tiene histórico")
    ap.add_argument("--pares", nargs="*", help="subconjunto de pares (por defecto todos los de config.yaml)")
    ap.add_argument("--spot", action="store_true", help="usar precios del mercado spot")
    args = ap.parse_args()

    config = cargar_config()
    if args.pares:
        config.pares = args.pares
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    ruta_db = config.rutas.absoluta(config.rutas.base_datos)
    sesion = crear_sesion(crear_motor(ruta_db))
    print(f"Base de datos: {ruta_db}")

    tipo = "spot" if args.spot else config.exchange.mercado_datos
    try:
        cliente, validos, invalidos = conectar(config, tipo)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] No se pudo conectar con los datos de Binance ({tipo}):\n   {diagnosticar(e)}")
        if tipo == "spot":
            return 1
        print("Intentando con los precios del mercado spot...")
        try:
            tipo = "spot"
            cliente, validos, invalidos = conectar(config, tipo)
        except Exception as e2:  # noqa: BLE001
            print(f"[ERROR] Tampoco con spot:\n   {diagnosticar(e2)}")
            print(f"Detalle completo en {config.rutas.absoluta(config.rutas.logs)}")
            log.exception("Fallo de conexión")
            return 1
        print("[AVISO] Se usarán precios de spot (difieren muy poco de los de futuros).\n"
              "        Pon `datos_spot: true` en la sección exchange de config/config.yaml para que el bot use\n"
              "        también spot en vivo.")
    for p in invalidos:
        print(f"[AVISO] {p} no está disponible en Binance ({tipo}); se omite")

    def progreso(par, hasta, ahora):
        print(f"\r  {par:<10} hasta {pd.Timestamp(hasta, unit='ms'):%Y-%m-%d}", end="", flush=True)

    total, fallidos = 0, []
    for par in validos:
        try:
            r = actualizar_historico(sesion, cliente, config.exchange.nombre, par, config.temporalidad, tipo,
                                     args.dias or config.historico.dias, progreso=progreso)
            total += r.nuevas
            print(f"\r{par:<10} +{r.nuevas:>6} velas nuevas   huecos: {len(r.huecos)}          ")
        except Exception as e:  # noqa: BLE001 - un par con problemas no detiene a los demás
            guardadas = ultimo_ts(sesion, config.exchange.nombre, par, config.temporalidad)
            print(f"\r{par:<10} [ERROR] {diagnosticar(e)}"
                  + (f"\n           (lo descargado hasta {pd.Timestamp(guardadas, unit='ms'):%Y-%m-%d} quedó guardado)"
                     if guardadas else ""))
            log.exception("Error descargando %s", par)
            fallidos.append(par)
    print(f"\nTotal: {total} velas nuevas en {len(validos) - len(fallidos)} de {len(validos)} pares.")
    if fallidos:
        print(f"Pares con error: {fallidos}. Vuelve a ejecutar el comando: continuará donde se quedó.")
    return 1 if fallidos or not validos else 0


if __name__ == "__main__":
    sys.exit(main())
